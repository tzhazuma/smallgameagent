#!/usr/bin/env python3
"""Experiment A: StrategyMemory readback A/B test.

Phase 1 (write): multi-bus-memory runs 30 steps, writing to strategy memory.
Phase 2 (read):  multi-bus-memory runs 30 steps, reading back from memory.
Control:         multi-bus (no memory) runs 30 steps.

Records memory_hits and memory_overrides per run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.hybrid_agent import HybridAgent
from src.experiments.game_env import score_trajectory

ROOT = Path(__file__).resolve().parent.parent.parent
GAME_PATH = ROOT / "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html"
MEMORY_FILE = ROOT / "strategy_memory_expA.json"
MAX_STEPS = 30


async def run_one(mode: str, name: str, memory_path: str | None = None,
                  config_overrides: dict | None = None) -> dict:
    print(f"  [{name}] starting...", flush=True)
    t0 = time.time()
    memory_config = {}
    if memory_path:
        memory_config["strategy_memory_path"] = memory_path
    config = {"max_steps": MAX_STEPS, "probe_timeout_ms": 18_000}
    if config_overrides:
        config.update(config_overrides)
    try:
        agent = HybridAgent(mode=mode, game_id="SSD_00461P01",
                            config=config, memory_config=memory_config)
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
        "mode": mode,
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "details": score.details,
        "memory_hits": ctx_meta.get("memory_hits", 0),
        "decision_source_counts": _count_sources(ctx_meta),
    }
    print(f"  [{name}] composite={out['composite']:.3f} "
          f"memory_hits={out['memory_hits']} steps={out['steps']}", flush=True)
    return out


def _count_sources(ctx_meta: dict) -> dict:
    log = ctx_meta.get("decision_source_log")
    if not log:
        return {}
    from collections import Counter
    return dict(Counter(log).most_common(10))


async def main() -> None:
    # Clean memory file for fresh start
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()

    results = []

    # Phase 1: write memory
    results.append(await run_one(
        "multi-bus-memory", "phase1_write",
        memory_path=str(MEMORY_FILE),
    ))

    # Phase 2: read memory
    results.append(await run_one(
        "multi-bus-memory", "phase2_read",
        memory_path=str(MEMORY_FILE),
    ))

    # Control: no memory
    results.append(await run_one(
        "multi-bus", "control_no_memory",
    ))

    out_path = ROOT / "experiment_memory_readback.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        print(f"  {r['name']:25s} composite={r['composite']:.3f} "
              f"memory_hits={r['memory_hits']}")


if __name__ == "__main__":
    asyncio.run(main())
