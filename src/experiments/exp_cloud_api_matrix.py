#!/usr/bin/env python3
"""Cloud API matrix experiment.

Tries the OpenCodeGo endpoint for the multimodal / text models requested by
 the team:

- mimo-v2.5 (multimodal)
- kimi-k2.7-code (text)
- kimi-k2.6 (multimodal)

For each model we issue a minimal request (text-only or vision) and record
whether it succeeds, the error class, and latency.  The script is intentionally
small because the account currently reports insufficient balance.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.api_client import OpenCodeGoClient

ROOT = Path(__file__).resolve().parent.parent.parent
FRAME = ROOT / "_frame_0.png"

EXPERIMENTS = [
    {"model": "mimo-v2.5", "mode": "vision", "max_tokens": 256},
    {"model": "mimo-v2.5", "mode": "text", "max_tokens": 128},
    {"model": "kimi-k2.7-code", "mode": "text", "max_tokens": 128},
    {"model": "kimi-k2.6", "mode": "vision", "max_tokens": 256},
    {"model": "kimi-k2.6", "mode": "text", "max_tokens": 128},
]


def _run_one(client: OpenCodeGoClient, exp: dict) -> dict:
    model = exp["model"]
    mode = exp["mode"]
    max_tokens = exp["max_tokens"]
    t0 = time.perf_counter()
    try:
        if mode == "text":
            resp = client.chat(
                [{"role": "user", "content": "Say 'pong' and nothing else."}],
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            content = resp.choices[0].message.content
        else:
            if not FRAME.exists():
                raise FileNotFoundError(f"Frame not found: {FRAME}")
            image_url = OpenCodeGoClient.encode_image_base64(FRAME)
            resp = client.chat_with_vision(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url}},
                            {
                                "type": "text",
                                "text": "Describe this mobile game screenshot in one sentence.",
                            },
                        ],
                    }
                ],
                model=model,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
        elapsed = time.perf_counter() - t0
        return {
            "model": model,
            "mode": mode,
            "ok": True,
            "latency_s": round(elapsed, 3),
            "content_preview": (content or "")[:200],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "model": model,
            "mode": mode,
            "ok": False,
            "latency_s": round(elapsed, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:400],
        }


def main() -> int:
    try:
        client = OpenCodeGoClient()
    except Exception as exc:
        print(f"Failed to create OpenCodeGoClient: {exc}", file=sys.stderr)
        return 1

    results = [_run_one(client, exp) for exp in EXPERIMENTS]
    out_path = ROOT / "experiment_cloud_api_matrix.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} results to {out_path}")
    for r in results:
        status = "OK" if r["ok"] else f"FAIL:{r['error_type']}"
        print(f"  [{r['model']}/{r['mode']}] {status} ({r['latency_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
