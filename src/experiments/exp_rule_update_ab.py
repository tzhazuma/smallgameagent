#!/usr/bin/env python3
"""A/B benchmark for rule-update vs baseline modes.

Runs a small matrix: rule / multi-bus-memory / hierarchical (with rule-update
triggers) on a representative game and reports composite, activity, latency,
and rule-update events.

Usage::

    python src/experiments/exp_rule_update_ab.py --game SSD_00461P01 --html path/to/game.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.agent.api_client import MultiProviderClient
from src.experiments.batch_runner import BatchConfig, run_batch


async def main() -> int:
    parser = argparse.ArgumentParser(description="Rule-update A/B benchmark")
    parser.add_argument("--game", default="SSD_00461P01", help="Game id")
    parser.add_argument("--html", required=True, help="Path to game HTML")
    parser.add_argument("--max-steps", type=int, default=25, help="Steps per run")
    parser.add_argument("--output", default="experiment_rule_update_ab.json", help="Output JSON")
    parser.add_argument("--provider", default=None, help="Cloud provider for hierarchical mode")
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"HTML not found: {html_path}", file=__import__("sys").stderr)
        return 1

    api_client = None
    if args.provider:
        api_client = MultiProviderClient(provider=args.provider)
    else:
        try:
            api_client = MultiProviderClient()
        except Exception as exc:
            print(f"No cloud API available: {exc}; hierarchical mode will fall back to rule", file=__import__("sys").stderr)

    config = BatchConfig(
        games={args.game: str(html_path)},
        modes=["rule", "multi-bus-memory", "hierarchical"],
        seeds=[42],
        max_steps=args.max_steps,
        collect_dataset=True,
        output_dir="rule_update_ab_results",
        api_client=api_client,
        config_overrides={
            "l2_interval": 10,
            "l1_interval": 5,
            "stuck_threshold": 3,
        },
    )

    results = await run_batch(config)
    summary = [
        {
            "game_id": r.game_id,
            "mode": r.mode,
            "seed": r.seed,
            "steps": r.steps,
            "composite": r.composite,
            "activity": r.activity,
            "elapsed_s": r.elapsed_s,
            "details": r.details,
            "error": r.error,
        }
        for r in results
    ]
    out_path = Path(args.output)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved summary to {out_path}")
    for row in summary:
        print(
            f"  {row['mode']:20s} composite={row['composite']:.3f} "
            f"activity={row['activity']:.3f} steps={row['steps']} elapsed={row['elapsed_s']}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
