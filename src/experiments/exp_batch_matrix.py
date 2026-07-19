#!/usr/bin/env python3
"""Batch matrix experiment — runs the batch_runner with a predefined config.

Default: 1 game (SSD_00461P01) × 4 modes × 2 seeds = 8 runs.
Produces batch_results.json + trajectory JSONL files + analysis.md.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.experiments.batch_runner import BatchConfig, run_batch
from src.experiments.analyze_batch import analyze

ROOT = Path(__file__).resolve().parent.parent.parent

GAMES = {
    "SSD_00461P01": str(ROOT / "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着^有埋点.html"),
}

MODES = ["rule", "multi-bus-memory", "hierarchical", "multi-bus"]

SEEDS = [42, 123]


async def main() -> None:
    config = BatchConfig(
        games=GAMES,
        modes=MODES,
        seeds=SEEDS,
        max_steps=30,
        headed=False,
        collect_dataset=False,
        output_dir=str(ROOT / "batch_results"),
        memory_config={"strategy_memory_path": str(ROOT / "strategy_memory_batch.json")},
    )

    results = await run_batch(config)

    # Generate analysis
    results_path = ROOT / "batch_results" / "batch_results.json"
    analysis_path = ROOT / "batch_results" / "analysis.md"
    if results_path.exists():
        analyze(results_path, analysis_path)

    print(f"\nTotal runs: {len(results)}")
    for r in results:
        status = f"composite={r.composite:.3f}" if not r.error else "ERROR"
        print(f"  {r.game_id} / {r.mode:20s} / seed={r.seed} → {status} ({r.elapsed_s}s)")


if __name__ == "__main__":
    asyncio.run(main())
