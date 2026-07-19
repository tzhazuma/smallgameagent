#!/usr/bin/env python3
"""Experiment D: Critic feedback loop A/B test.

Config A: multi-bus with max_rounds=1 (no Critic re-decide).
Config B: multi-bus with max_rounds=2 (default, Critic can intervene).

Records composite, bus_messages, critic_invocations per run.
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
MAX_STEPS = 30


async def run_one(name: str, max_rounds: int) -> dict:
    print(f"  [{name}] max_rounds={max_rounds} starting...", flush=True)
    t0 = time.time()
    try:
        agent = HybridAgent(
            mode="multi-bus",
            game_id="SSD_00461P01",
            config={"max_steps": MAX_STEPS, "probe_timeout_ms": 18_000,
                    "max_rounds": max_rounds},
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
    bus_stats = ctx_meta.get("bus_stats") or {}
    bus_by_type = bus_stats.get("by_type") or {}
    out = {
        "name": name,
        "max_rounds": max_rounds,
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "details": score.details,
        "bus_messages": bus_stats.get("total_messages", 0),
        "critic_invocations": bus_by_type.get("critic", 0),
        "verify_invocations": bus_by_type.get("verify", 0),
        "decision_source_counts": _count_sources(ctx_meta),
    }
    print(f"  [{name}] composite={out['composite']:.3f} "
          f"bus={out['bus_messages']} critic={out['critic_invocations']} "
          f"elapsed={out['elapsed_s']}s", flush=True)
    return out


def _count_sources(ctx_meta: dict) -> dict:
    log = ctx_meta.get("decision_source_log")
    if not log:
        return {}
    from collections import Counter
    return dict(Counter(log).most_common(10))


async def main() -> None:
    results = []
    results.append(await run_one("no_critic_r1", max_rounds=1))
    results.append(await run_one("with_critic_r2", max_rounds=2))

    out_path = ROOT / "experiment_critic_ab.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        print(f"  {r['name']:20s} composite={r['composite']:.3f} "
              f"critic={r['critic_invocations']} bus={r['bus_messages']}")


if __name__ == "__main__":
    asyncio.run(main())
