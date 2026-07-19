#!/usr/bin/env python3
"""Experiment F: Hierarchical multi-agent architecture.

Compares three configurations on SSD_00461P01 (30 steps each):
  A) hierarchical  — L0 rule + L1 local VLM + L2 cloud API
  B) rule          — pure L0 (tap-guide)
  C) multi-bus-memory — current best multi-agent

Records composite, per-step latency, and L1/L2 call counts.
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
MAX_STEPS = 30


async def run_one(name: str, mode: str, api_client=None) -> dict:
    print(f"  [{name}] mode={mode} starting...", flush=True)
    t0 = time.time()
    try:
        agent = HybridAgent(
            mode=mode,
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
    h_stats = ctx_meta.get("hierarchical_stats") or {}
    out = {
        "name": name,
        "mode": mode,
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "details": score.details,
        "l0_calls": h_stats.get("l0_calls", 0),
        "l1_calls": h_stats.get("l1_calls", 0),
        "l2_calls": h_stats.get("l2_calls", 0),
        "latency_per_step": round(elapsed / max(1, result.get("steps", 1)), 2),
    }
    print(f"  [{name}] composite={out['composite']:.3f} "
          f"L0={out['l0_calls']} L1={out['l1_calls']} L2={out['l2_calls']} "
          f"lat/step={out['latency_per_step']}s", flush=True)
    return out


async def main() -> None:
    # Create cloud API client for hierarchical mode
    api_client = None
    try:
        api_client = OpenCodeGoClient(text_model="kimi-k2.7-code")
    except Exception as exc:
        print(f"  [warn] API client init failed: {exc}", flush=True)

    results = []
    results.append(await run_one("hierarchical", "hierarchical", api_client=api_client))
    results.append(await run_one("rule_baseline", "rule"))
    results.append(await run_one("multi_bus_memory", "multi-bus-memory",
                                  memory_config={"strategy_memory_path": str(ROOT / "strategy_memory_hier.json")}))

    out_path = ROOT / "experiment_hierarchical.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        print(f"  {r['name']:20s} composite={r['composite']:.3f} "
              f"lat/step={r['latency_per_step']}s "
              f"L1={r['l1_calls']} L2={r['l2_calls']}")


if __name__ == "__main__":
    asyncio.run(main())
