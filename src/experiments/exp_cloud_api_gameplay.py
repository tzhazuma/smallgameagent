#!/usr/bin/env python3
"""Experiment E: Cloud-API-in-the-loop gameplay on SSD_00461P01.

Now that the browser can initialise the Cocos scene, we run the ``api`` mode
(text LLM decisions) with two cloud models through OpenCodeGo and compare
them against the local rule (tap-guide) baseline:

  - kimi-k2.7-code  (fast text)
  - mimo-v2.5       (multimodal model used in text mode)
  - rule            (tap-guide baseline)

This is the first *gameplay* test of the cloud API mix (previously the Cocos
init failure limited cloud models to offline struct extraction).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.api_client import OpenCodeGoClient
from src.agent.hybrid_agent import HybridAgent
from src.experiments.game_env import score_trajectory

ROOT = Path(__file__).resolve().parent.parent.parent
GAME_PATH = ROOT / "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html"
MAX_STEPS = 15

CONFIGS = [
    {"name": "api_kimi_k27", "mode": "api", "text_model": "kimi-k2.7-code"},
    {"name": "api_mimo_v25", "mode": "api", "text_model": "mimo-v2.5"},
    {"name": "rule_baseline", "mode": "rule", "text_model": None},
]


async def run_one(cfg: dict) -> dict:
    name = cfg["name"]
    print(f"  [{name}] mode={cfg['mode']} model={cfg['text_model']} starting...",
          flush=True)
    t0 = time.time()
    api_client = None
    if cfg["text_model"]:
        try:
            api_client = OpenCodeGoClient(text_model=cfg["text_model"])
        except Exception as exc:
            return {"name": name, "mode": cfg["mode"], "steps": 0,
                    "composite": 0.0, "activity": 0.0, "elapsed_s": 0.0,
                    "details": {}, "error": f"client_init:{exc}"}
    try:
        agent = HybridAgent(
            mode=cfg["mode"],
            game_id="SSD_00461P01",
            api_client=api_client,
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
    ctx_meta = result.get("ctx_metadata") or {}
    out = {
        "name": name,
        "mode": cfg["mode"],
        "text_model": cfg["text_model"],
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "details": score.details,
        "reason": result.get("reason", ""),
        "decision_source_counts": _count_sources(ctx_meta),
    }
    print(f"  [{name}] composite={out['composite']:.3f} steps={out['steps']} "
          f"elapsed={out['elapsed_s']}s reason={out['reason'][:60]}", flush=True)
    return out


def _count_sources(ctx_meta: dict) -> dict:
    log = ctx_meta.get("decision_source_log")
    if not log:
        return {}
    from collections import Counter
    return dict(Counter(log).most_common(8))


async def main() -> None:
    results = []
    for cfg in CONFIGS:
        results.append(await run_one(cfg))

    out_path = ROOT / "experiment_cloud_api_gameplay.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        d = r["details"]
        print(f"  {r['name']:18s} composite={r['composite']:.3f} "
              f"move={d.get('move_steps', 0)} tap={d.get('tap_steps', 0)} "
              f"stall={d.get('stall_steps', 0)} elapsed={r['elapsed_s']}s")


if __name__ == "__main__":
    asyncio.run(main())
