#!/usr/bin/env python3
"""Continue the B_tap matrix for games that were interrupted.

Runs only the missing (game_id, mode, seed) combinations and writes them to a
separate output directory.  The caller is expected to merge the resulting
trajectories and batch_results.json into full_matrix_results/B_tap/.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.experiments.analyze_batch import analyze
from src.experiments.batch_runner import BatchConfig, run_batch

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"

MAX_STEPS = 25
SEEDS = [42, 123]
MISSING_GAMES = ["SSD_00733P01", "SSD_00742P01"]


def _resolve_html(game_id: str) -> str | None:
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


async def main() -> None:
    games = {}
    for gid in MISSING_GAMES:
        html = _resolve_html(gid)
        if html:
            games[gid] = html
        else:
            print(f"[warn] HTML not found for {gid}", file=sys.stderr)

    if not games:
        print("No missing games to run.", file=sys.stderr)
        return

    config = BatchConfig(
        games=games,
        modes=["rule", "multi-bus-memory"],
        seeds=SEEDS,
        max_steps=MAX_STEPS,
        headed=False,
        collect_dataset=True,
        output_dir=str(ROOT / "full_matrix_results" / "B_tap_continue"),
        memory_config={"strategy_memory_path": str(ROOT / "strategy_memory_B_tap_continue.json")},
        config_overrides={"probe_timeout_ms": 30_000},
    )

    results = await run_batch(config)

    results_path = ROOT / "full_matrix_results" / "B_tap_continue" / "batch_results.json"
    analysis_path = ROOT / "full_matrix_results" / "B_tap_continue" / "analysis.md"
    if results_path.exists():
        analyze(results_path, analysis_path)

    print(f"\nContinuation complete: {len(results)} runs")
    for r in results:
        status = "OK" if not r.error else f"ERROR:{r.error[:40]}"
        print(f"  {r.game_id} / {r.mode} / seed={r.seed}: composite={r.composite:.3f} ({status})")


if __name__ == "__main__":
    asyncio.run(main())
