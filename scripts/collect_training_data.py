#!/usr/bin/env python3
"""Batch-collect gameplay trajectories and convert them to VLM training data.

Usage::

    .venv/bin/python scripts/collect_training_data.py \
        --games-dir _extracted/games \
        --output-dir collected-runs \
        --mode rule \
        --max-steps 25 \
        --seeds 42 123

Steps:

1. Discover all ``SSD_*P*.html`` games under ``--games-dir``.
2. Run ``HybridAgent(mode=...)`` for each game and each seed.
3. Write per-run trajectory JSONL under ``OUTPUT_DIR/trajectories/``.
4. Convert all trajectories to the 7-task VLM dataset format under
   ``OUTPUT_DIR/vlm-training-data/``.

The resulting JSONL samples can be merged with
``vlm-training-data-processed-runs/`` for QLoRA training.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.hybrid_agent import HybridAgent
from src.training.trajectory_converter import main as convert_main

logger = logging.getLogger("collect_training_data")


def discover_games(games_dir: Path) -> dict[str, Path]:
    """Return a mapping game_id → html_path."""
    games: dict[str, Path] = {}
    if not games_dir.is_dir():
        raise ValueError(f"Games directory not found: {games_dir}")
    for html_path in games_dir.rglob("SSD_*P*.html"):
        game_id = html_path.stem
        games[game_id] = html_path
    return dict(sorted(games.items()))


async def run_one(
    game_id: str,
    html_path: Path,
    mode: str,
    seed: int,
    max_steps: int,
    trajectories_dir: Path,
) -> dict[str, Any]:
    """Run a single game/seed and write its trajectory JSONL."""
    traj_path = trajectories_dir / f"{game_id}_{mode}_seed{seed}.jsonl"
    agent = HybridAgent(mode=mode, game_id=game_id)
    result = await agent.run_game(str(html_path), max_steps=max_steps, headed=False)

    # Persist per-step records if they were collected by DatasetWriter.
    dataset_path = Path(f"dataset_{game_id}_{mode}_{seed}.jsonl")
    if dataset_path.is_file():
        dataset_path.rename(traj_path)
    else:
        # Fallback: write a minimal trajectory from the result summary.
        record = {
            "game_id": game_id,
            "mode": mode,
            "seed": seed,
            "steps": result.get("steps", max_steps),
            "composite": result.get("composite"),
            "activity": result.get("activity"),
        }
        traj_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    logger.info("%s / %s / seed=%d → composite=%s", game_id, mode, seed, result.get("composite"))
    return result


async def run_collection(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    trajectories_dir = output_dir / "trajectories"
    trajectories_dir.mkdir(parents=True, exist_ok=True)

    games = discover_games(Path(args.games_dir))
    logger.info("Discovered %d games", len(games))

    tasks = []
    for game_id, html_path in games.items():
        for seed in args.seeds:
            tasks.append(
                run_one(game_id, html_path, args.mode, seed, args.max_steps, trajectories_dir)
            )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary_path = output_dir / "collection_summary.json"
    summary = {
        "mode": args.mode,
        "max_steps": args.max_steps,
        "seeds": args.seeds,
        "games": list(games.keys()),
        "results": [
            r if not isinstance(r, Exception) else {"error": str(r)}
            for r in results
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Summary written to %s", summary_path)

    # Convert trajectories to 7-task VLM dataset.
    vlm_data_dir = output_dir / "vlm-training-data"
    sys.argv = [
        "trajectory_converter.py",
        "--input-dir", str(trajectories_dir),
        "--output-dir", str(vlm_data_dir),
    ]
    convert_main()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect gameplay trajectories and VLM training data")
    parser.add_argument("--games-dir", type=str, required=True, help="Directory containing SSD_*P*.html games")
    parser.add_argument("--output-dir", type=str, default="collected-runs", help="Output directory")
    parser.add_argument("--mode", type=str, default="rule", help="Agent mode (rule, multi-bus, multi-bus-memory, ...)")
    parser.add_argument("--max-steps", type=int, default=25, help="Max steps per run")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42], help="Seeds to run")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    asyncio.run(run_collection(args))
