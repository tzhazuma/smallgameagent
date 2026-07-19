#!/usr/bin/env python3
"""Multi-game generalisation experiment.

Picks a representative set of games from ``_extracted/games/`` (only
SSD_00461P01 has a hand-tuned profile; the rest run on the *uncalibrated*
generic fallback) and runs each through two modes via the batch runner:

  - rule              (L0 tap-guide; calibrated for 00461, generic otherwise)
  - multi-bus-memory  (bus + strategy memory readback)

The point is to demonstrate that the framework can load, drive and collect
trajectories from many different games, and to surface which games need a
per-game calibration to score well.  Per-step trajectories are written as
JSONL for later VLM fine-tuning / offline replay.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from configs.game_profiles import get_profile
from src.experiments.analyze_batch import analyze
from src.experiments.batch_runner import BatchConfig, run_batch

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"

# Representative games spanning different mechanics:
#   00461 tower-defense (calibrated), 00482 chop-tree/expand (collect),
#   00736 frog/fish/turtle (auto-fish capability flip -> consistency),
#   00342 build/merge, 00532 waterfall giant-log (collect).
SELECTED = [
    "SSD_00461P01",
    "SSD_00482P01",
    "SSD_00736P01",
    "SSD_00342P01",
    "SSD_00532P01",
]

MODES = ["rule", "multi-bus-memory"]
SEEDS = [7]
MAX_STEPS = 25


def _resolve_html(game_id: str) -> str | None:
    """Find the HTML file for *game_id* under ``_extracted/games/``."""
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


def build_games() -> dict[str, str]:
    games: dict[str, str] = {}
    for gid in SELECTED:
        html = _resolve_html(gid)
        if html is None:
            print(f"  [skip] {gid}: no HTML under {GAMES_DIR}")
            continue
        games[gid] = html
    return games


async def main() -> None:
    games = build_games()
    if not games:
        print("No games resolved; aborting.", file=sys.stderr)
        return

    print("Selected games (calibrated? / driver):")
    for gid in games:
        prof = get_profile(gid)
        if prof is None:
            print(f"  {gid}: GENERIC (uncalibrated) tap-guide")
        else:
            print(f"  {gid}: calibrated {prof.get('driver_type')}")

    out_dir = ROOT / "multi_game_results"
    config = BatchConfig(
        games=games,
        modes=MODES,
        seeds=SEEDS,
        max_steps=MAX_STEPS,
        headed=False,
        collect_dataset=False,
        output_dir=str(out_dir),
        memory_config={"strategy_memory_path": str(ROOT / "strategy_memory_multigame.json")},
        config_overrides={"probe_timeout_ms": 30_000},
    )

    results = await run_batch(config)

    results_path = out_dir / "batch_results.json"
    analysis_path = out_dir / "analysis.md"
    if results_path.exists():
        analyze(results_path, analysis_path)

    # Print a compact calibrated-vs-generic summary.
    print("\n=== Per-game summary ===")
    for r in results:
        prof = get_profile(r.game_id)
        cal = "cal" if prof is not None else "GEN"
        d = r.details
        status = f"composite={r.composite:.3f}" if not r.error else f"ERR:{r.error[:40]}"
        print(
            f"  [{cal}] {r.game_id} / {r.mode:18s} steps={r.steps:2d} "
            f"{status} act={r.activity:.2f} tap={d.get('tap_steps', 0)} "
            f"move={d.get('move_steps', 0)} stall={d.get('stall_steps', 0)} "
            f"({r.elapsed_s}s)"
        )


if __name__ == "__main__":
    asyncio.run(main())
