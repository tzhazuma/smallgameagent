#!/usr/bin/env python3
"""Local VLM structured-extraction benchmark on RTX 5060.

Starts a llama.cpp server for each GGUF/mmproj pair, runs the same
visual-structure extraction prompt over a small set of game frames, and
records latency / throughput / parse success.  Designed for the RTX 5060
Laptop (8 GB) with 4-bit model weights and q4_0 KV-cache quantisation.

Usage::

    python src/experiments/exp_local_vlm_matrix.py \
        --frames _frame_0.png _frame_1.png _frame_2.png
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent

LLAMA_SERVER = (
    Path.home()
    / ".lmstudio/extensions/backends/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.17.0/llama-server"
)

MODELS = {
    "Qwen3.5-4B-Q4KM": {
        "gguf": Path.home() / ".lmstudio/models/unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf",
        "mmproj": Path.home() / ".lmstudio/models/unsloth/Qwen3.5-4B-GGUF/mmproj-F16.gguf",
        "extra_args": ["--image-min-tokens", "1024"],
    },
    "Qwen3.5-9B-Q4KM": {
        "gguf": Path.home() / ".lmstudio/models/unsloth/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf",
        "mmproj": Path.home() / ".lmstudio/models/unsloth/Qwen3.5-9B-GGUF/mmproj-F16.gguf",
        "extra_args": ["--image-min-tokens", "1024"],
    },
    "gemma-4-E4B-it-Q4KM": {
        "gguf": Path.home() / ".lmstudio/models/unsloth/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q4_K_M.gguf",
        "mmproj": Path.home() / ".lmstudio/models/unsloth/gemma-4-E4B-it-GGUF/mmproj-F16.gguf",
        "extra_args": [],
    },
}

STRUCT_PROMPT = (
    "You are a game state visual analyser. Given a screenshot of a mobile game frame, "
    "extract structured information about what you see. Return ONLY valid JSON with these fields:\n\n"
    '{"has_arrow": true/false, "arrow_screen_x": float or null, "arrow_screen_y": float or null, '
    '"arrow_world_dx": float or null, "arrow_world_dz": float or null, "has_target": true/false, '
    '"target_screen_x": float or null, "target_screen_y": float or null, "has_obstacle": true/false, '
    '"obstacle_screen_x": float or null, "obstacle_screen_y": float or null, '
    '"is_end_screen": true/false, "end_screen_type": "win"/"lose"/null, "ui_buttons": ["text", ...], '
    '"player_screen_x": float or null, "player_screen_y": float or null, '
    '"has_guide_indicator": true/false, "guide_direction": "up"/"down"/"left"/"right"/null, '
    '"extra_notes": "string"}'
)

SYSTEM_PROMPT = (
    "You are a helpful vision assistant. Always follow the user's JSON schema exactly. "
    "Output only the requested JSON object, with no markdown fences."
)


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text or ""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _wait_for_server(port: int, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                data = json.loads(r.read())
                # llama-server >=2.22 reports model_loaded; 2.17 only returns status.
                if data.get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _start_server(model_name: str, port: int) -> subprocess.Popen:
    cfg = MODELS[model_name]
    for p in (cfg["gguf"], cfg["mmproj"], LLAMA_SERVER):
        if not p.exists():
            raise FileNotFoundError(f"Missing path for {model_name}: {p}")
    log_path = Path(f"/tmp/llama-server-{model_name.replace('/', '_')}.log")
    cmd = [
        str(LLAMA_SERVER),
        "-m", str(cfg["gguf"]),
        "--mmproj", str(cfg["mmproj"]),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-ngl", "999",
        "-c", "4096",
        "-n", "512",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "--flash-attn", "on",
        *cfg["extra_args"],
    ]
    print(f"[server] starting {model_name} on port {port}", flush=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._log_file = log_file  # type: ignore[attr-defined]
    return proc


def _stop_server(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    finally:
        getattr(proc, "_log_file", None) and proc._log_file.close()  # type: ignore[attr-defined]


def _request(port: int, image_path: Path) -> dict[str, Any]:
    b64 = _encode_image(image_path)
    payload = {
        "model": "local-vlm",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": STRUCT_PROMPT},
                ],
            },
        ],
        "max_tokens": 512,
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180.0) as r:
        resp = json.loads(r.read())
    elapsed = time.perf_counter() - t0

    choice = resp["choices"][0]["message"]
    content = choice.get("content") or ""
    parsed = _extract_json(content)
    usage = resp.get("usage", {})
    timings = resp.get("timings", {})
    return {
        "elapsed_s": round(elapsed, 3),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "prompt_ms": timings.get("prompt_ms"),
        "predicted_ms": timings.get("predicted_ms"),
        "prompt_per_second": timings.get("prompt_per_second"),
        "predicted_per_second": timings.get("predicted_per_second"),
        "parsed": parsed is not None,
        "raw_preview": content[:300],
        "extracted": parsed,
    }


def _benchmark_model(model_name: str, frames: list[Path], port: int = 1234) -> dict[str, Any]:
    proc = _start_server(model_name, port)
    try:
        if not _wait_for_server(port, timeout=240.0):
            return {"model": model_name, "error": "server failed to start"}
        print(f"[bench] {model_name} ready; running {len(frames)} frames", flush=True)
        frame_results = []
        for fp in frames:
            try:
                frame_results.append({"frame": str(fp.name), "result": _request(port, fp)})
            except Exception as exc:
                frame_results.append({"frame": str(fp.name), "error": str(exc)})
        ok = [r for r in frame_results if "error" not in r and r["result"].get("parsed")]
        latencies = [r["result"]["elapsed_s"] for r in ok]
        speeds = [r["result"].get("predicted_per_second") for r in ok if r["result"].get("predicted_per_second")]
        return {
            "model": model_name,
            "frames": frame_results,
            "summary": {
                "n_frames": len(frames),
                "n_success": len(ok),
                "mean_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
                "max_latency_s": round(max(latencies), 3) if latencies else None,
                "mean_gen_tok_s": round(sum(speeds) / len(speeds), 1) if speeds else None,
            },
        }
    finally:
        _stop_server(proc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local VLM structured-extraction benchmark")
    parser.add_argument(
        "--frames",
        nargs="+",
        default=[str(ROOT / "_frame_0.png"), str(ROOT / "_frame_1.png"), str(ROOT / "_frame_2.png")],
        help="PNG frames to benchmark",
    )
    parser.add_argument("--models", nargs="+", default=list(MODELS.keys()), help="Models to run")
    parser.add_argument("--start-port", type=int, default=1234, help="First server port")
    args = parser.parse_args()

    frames = [Path(f) for f in args.frames]
    missing = [f for f in frames if not f.exists()]
    if missing:
        print(f"Missing frames: {missing}", file=sys.stderr)
        return 1

    results = []
    for idx, model_name in enumerate(args.models):
        if model_name not in MODELS:
            print(f"Unknown model {model_name}; skipping", file=sys.stderr)
            continue
        port = args.start_port + idx
        try:
            results.append(_benchmark_model(model_name, frames, port=port))
        except Exception as exc:
            results.append({"model": model_name, "error": str(exc)})

    out_path = ROOT / "experiment_local_vlm_matrix.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved results to {out_path}")
    for r in results:
        summary = r.get("summary")
        if summary:
            print(
                f"  {r['model']}: success={summary['n_success']}/{summary['n_frames']} "
                f"mean_latency={summary['mean_latency_s']}s "
                f"mean_gen_tok_s={summary['mean_gen_tok_s']}"
            )
        else:
            print(f"  {r['model']}: error={r.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
