#!/usr/bin/env python3
"""Multi-agent / memory / local-cloud matrix experiment.

Runs a small matrix of agent configurations on 2--3 games and scores each
trajectory with the verifiers-style rubric from ``src.experiments.game_env``.

Configurations:
  - rule                    pure rule engine
  - multi                   existing implicit multi-agent pipeline
  - multi-bus               explicit message-bus multi-agent (no memory)
  - multi-bus-memory        bus + strategy memory
  - api (single LLM)        cloud text decision (requires credits)
  - api-memory              single LLM + working memory (requires credits)

Results are written to ``experiment_multi_agent_matrix.json``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.hybrid_agent import HybridAgent
from src.experiments.game_env import score_trajectory

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"
# Explicit, known-working game paths for the matrix.
GAMES = {
    "SSD_00461P01": ROOT / "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html",
}

CONFIGS = [
    {"mode": "rule", "name": "rule"},
    {"mode": "multi", "name": "multi"},
    {"mode": "multi-bus", "name": "multi-bus"},
    {"mode": "multi-bus-memory", "name": "multi-bus-memory", "memory": {"strategy_memory_path": "./strategy_memory_matrix.json"}},
]

MAX_STEPS = 30


def find_games() -> list[tuple[str, Path]]:
    games: list[tuple[str, Path]] = []
    for gid, html in GAMES.items():
        if html.exists():
            games.append((gid, html))
    return games


async def run_one(game_id: str, html_path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    print(f"  [{cfg['name']}] {game_id} starting...", flush=True)
    t0 = time.time()
    memory_config = cfg.get("memory", {})

    try:
        agent = HybridAgent(
            mode=cfg["mode"],
            game_id=game_id,
            config={"max_steps": MAX_STEPS, "probe_timeout_ms": 18_000},
            memory_config=memory_config,
        )
        result = await agent.run_game(html_path, max_steps=MAX_STEPS, headed=False)
    except Exception as exc:
        result = {
            "completed": False,
            "win": False,
            "steps": 0,
            "reason": str(exc)[:200],
            "step_log": [],
        }

    elapsed = time.time() - t0
    score = score_trajectory(
        step_log=result.get("step_log", []),
        result=result,
        candidate_transitions=result.get("candidate_transitions", []),
        world_model_stats=result.get("world_model_stats"),
    )
    out = {
        "game_id": game_id,
        "mode": cfg["name"],
        "elapsed_s": round(elapsed, 1),
        "completed": bool(result.get("completed") or result.get("win")),
        "win": bool(result.get("win")),
        "steps": result.get("steps", 0),
        "reason": result.get("reason", ""),
        "rubric": score.to_dict(),
    }
    if "error" in result:
        out["error"] = result["error"]

    # Extra diagnostics: bus traffic, decision sources, world-model churn.
    ctx_meta = result.get("ctx_metadata") or {}
    bus_stats = ctx_meta.get("bus_stats")
    if isinstance(bus_stats, dict):
        out["bus_messages"] = bus_stats.get("total_messages")
        out["bus_by_type"] = bus_stats.get("by_type")
    src_log = ctx_meta.get("decision_source_log")
    if isinstance(src_log, list):
        from collections import Counter

        out["decision_source_counts"] = dict(Counter(src_log).most_common(10))
    wm_stats = result.get("world_model_stats")
    if isinstance(wm_stats, dict):
        out["wm_observations"] = wm_stats.get("observations")
        out["wm_replans"] = wm_stats.get("stale_replans")

    print(
        f"  [{cfg['name']}] {game_id}: steps={out['steps']} "
        f"composite={out['rubric']['composite']:.3f} elapsed={out['elapsed_s']}s",
        flush=True,
    )
    return out


async def main() -> None:
    games = find_games()
    print(f"Found games: {[g[0] for g in games]}")

    results: list[dict[str, Any]] = []
    for game_id, html_path in games:
        print(f"\n=== {game_id} ===", flush=True)
        for cfg in CONFIGS:
            r = await run_one(game_id, html_path, cfg)
            results.append(r)

    out_path = ROOT / "experiment_multi_agent_matrix.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
