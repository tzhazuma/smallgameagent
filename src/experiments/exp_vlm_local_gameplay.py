#!/usr/bin/env python3
"""Experiment C: Local VLM online decision loop.

Starts a local llama-server (gemma-4-E4B), runs the agent in vlm-local mode
for 20 steps on SSD_00461P01, and compares with rule mode.

Requires the llama-server binary and GGUF models to be present.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.hybrid_agent import HybridAgent
from src.experiments.game_env import score_trajectory

ROOT = Path(__file__).resolve().parent.parent.parent
GAME_PATH = ROOT / "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html"
MAX_STEPS = 20

LLAMA_SERVER = (
    Path.home()
    / ".lmstudio/extensions/backends/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.17.0/llama-server"
)
GGUF = Path.home() / ".lmstudio/models/unsloth/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q4_K_M.gguf"
MMPROJ = Path.home() / ".lmstudio/models/unsloth/gemma-4-E4B-it-GGUF/mmproj-F16.gguf"
PORT = 1234


def _wait_for_server(port: int, timeout: float = 240.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2.0) as r:
                data = json.loads(r.read())
                if data.get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


async def run_mode(mode: str, name: str) -> dict:
    print(f"  [{name}] mode={mode} starting...", flush=True)
    t0 = time.time()
    try:
        agent = HybridAgent(
            mode=mode,
            game_id="SSD_00461P01",
            config={"max_steps": MAX_STEPS, "probe_timeout_ms": 18_000},
        )
        result = await agent.run_game(GAME_PATH, max_steps=MAX_STEPS, headed=False)
    except Exception as exc:
        result = {"completed": False, "win": False, "steps": 0,
                  "reason": str(exc)[:200], "step_log": []}
    elapsed = time.time() - t0
    score = score_trajectory(
        step_log=result.get("step_log", []),
        result=result,
        candidate_transitions=result.get("candidate_transitions", []),
        world_model_stats=result.get("world_model_stats"),
    )
    out = {
        "name": name,
        "mode": mode,
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "details": score.details,
    }
    print(f"  [{name}] composite={out['composite']:.3f} steps={out['steps']} "
          f"elapsed={out['elapsed_s']}s", flush=True)
    return out


async def main() -> None:
    # Start llama-server
    if not LLAMA_SERVER.exists():
        print(f"llama-server not found at {LLAMA_SERVER}", file=sys.stderr)
        return
    if not GGUF.exists():
        print(f"GGUF not found at {GGUF}", file=sys.stderr)
        return

    log_path = Path("/tmp/llama-server-vlm-local.log")
    cmd = [
        str(LLAMA_SERVER),
        "-m", str(GGUF),
        "--mmproj", str(MMPROJ),
        "--host", "127.0.0.1",
        "--port", str(PORT),
        "-ngl", "999",
        "-c", "4096",
        "-n", "512",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "--flash-attn", "on",
    ]
    print("[server] starting gemma-4-E4B for vlm-local...", flush=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

    try:
        if not _wait_for_server(PORT, timeout=240.0):
            print("[server] failed to start", file=sys.stderr)
            return

        results = []
        # Run vlm-local
        results.append(await run_mode("vlm-local", "vlm_local_gemma"))
        # Run rule as baseline
        results.append(await run_mode("rule", "rule_baseline"))

        out_path = ROOT / "experiment_vlm_local_gameplay.json"
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved {len(results)} results to {out_path}")
        for r in results:
            print(f"  {r['name']:25s} composite={r['composite']:.3f} "
                  f"steps={r['steps']} elapsed={r['elapsed_s']}s")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log_file.close()


if __name__ == "__main__":
    asyncio.run(main())
