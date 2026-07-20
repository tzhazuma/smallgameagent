#!/usr/bin/env python3
"""Experiment: VLM visual pipeline vs pure-probe rule.

Compares three vision approaches on the same games:
  A) rule (pure probe, no vision)
  B) rule + PIL VisualAnalyzer (local cyan-arrow detection)
  C) vlm-local (gemma-4-E4B VLM vision)

Measures composite, activity, tap_count, and latency_per_step.
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
GAMES_DIR = ROOT / "_extracted" / "games"

GAMES = ["SSD_00461P01", "SSD_00736P01"]

CONFIGS = [
    ("rule", "probe_only"),
    ("rule", "pil_vision"),
    ("vlm-local", "vlm_gemma"),
]

MAX_STEPS = 25


def _resolve_html(game_id: str) -> str | None:
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


async def run_one(game_id: str, mode: str, name: str, use_visual: bool = False) -> dict:
    html = _resolve_html(game_id)
    if html is None:
        return {"game_id": game_id, "name": name, "error": "no_html"}
    print(f"  [{name}] {game_id} starting...", flush=True)
    t0 = time.time()
    try:
        agent = HybridAgent(
            mode=mode,
            game_id=game_id,
            config={"max_steps": MAX_STEPS, "probe_timeout_ms": 18_000},
        )
        result = await agent.run_game(html, max_steps=MAX_STEPS, headed=False)
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
    d = score.details
    out = {
        "game_id": game_id,
        "name": name,
        "mode": mode,
        "elapsed_s": round(elapsed, 1),
        "steps": result.get("steps", 0),
        "composite": score.composite,
        "activity": score.activity,
        "tap_steps": d.get("tap_steps", 0),
        "move_steps": d.get("move_steps", 0),
        "stall_steps": d.get("stall_steps", 0),
        "latency_per_step": round(elapsed / max(1, result.get("steps", 1)), 2),
    }
    print(f"  [{name}] composite={out['composite']:.3f} "
          f"tap={out['tap_steps']} stall={out['stall_steps']} "
          f"lat/step={out['latency_per_step']}s", flush=True)
    return out


async def main() -> None:
    results = []
    for game_id in GAMES:
        for mode, name in CONFIGS:
            r = await run_one(game_id, mode, name)
            results.append(r)

    out_path = ROOT / "experiment_vlm_pipeline.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        print(f"  {r['game_id']} / {r['name']:15s} composite={r['composite']:.3f} "
              f"activity={r['activity']:.3f} tap={r['tap_steps']} stall={r['stall_steps']}")


if __name__ == "__main__":
    asyncio.run(main())
