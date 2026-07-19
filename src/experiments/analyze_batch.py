#!/usr/bin/env python3
"""Analyze batch experiment results — generate comparison tables.

Reads ``batch_results.json`` and produces:
  - A Markdown comparison table (stdout + file)
  - Per-mode statistics (mean composite, mean activity, mean latency)
  - Per-game breakdown
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def analyze(results_path: str | Path, output_md: str | Path | None = None) -> str:
    """Analyze batch results and return a Markdown report string."""
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))

    # Group by mode
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for r in data:
        by_mode[r["mode"]].append(r)

    lines = ["# Batch Experiment Analysis\n"]

    # Summary table
    lines.append("## Per-Mode Summary\n")
    lines.append("| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |")
    lines.append("|---|---|---|---|---|---|")
    for mode in sorted(by_mode):
        runs = by_mode[mode]
        n = len(runs)
        composites = [r["composite"] for r in runs if not r.get("error")]
        activities = [r["activity"] for r in runs if not r.get("error")]
        latencies = [r["elapsed_s"] for r in runs if not r.get("error")]
        errors = sum(1 for r in runs if r.get("error"))
        mc = f"{sum(composites)/len(composites):.3f}" if composites else "N/A"
        ma = f"{sum(activities)/len(activities):.3f}" if activities else "N/A"
        ml = f"{sum(latencies)/len(latencies):.1f}" if latencies else "N/A"
        lines.append(f"| {mode} | {n} | {mc} | {ma} | {ml} | {errors} |")

    # Per-game breakdown
    lines.append("\n## Per-Game Breakdown\n")
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in data:
        by_game[r["game_id"]].append(r)

    for game_id in sorted(by_game):
        lines.append(f"### {game_id}\n")
        lines.append("| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(by_game[game_id], key=lambda x: (x["mode"], x["seed"])):
            d = r.get("details", {})
            lines.append(
                f"| {r['mode']} | {r['seed']} | {r['steps']} | "
                f"{r['composite']:.3f} | {r['activity']:.3f} | {r['elapsed_s']}s | "
                f"{d.get('tap_steps', 0)} | {d.get('move_steps', 0)} | {d.get('stall_steps', 0)} |"
            )

    # Trajectory data summary
    traj_runs = [r for r in data if r.get("trajectory_path")]
    if traj_runs:
        lines.append(f"\n## Trajectory Data\n\n{len(traj_runs)} runs produced trajectory JSONL files.\n")
        lines.append("| File | Steps |")
        lines.append("|---|---|")
        for r in traj_runs:
            p = Path(r["trajectory_path"])
            n_lines = sum(1 for _ in p.open()) if p.exists() else 0
            lines.append(f"| `{p.name}` | {n_lines} |")

    md = "\n".join(lines) + "\n"

    if output_md:
        Path(output_md).write_text(md, encoding="utf-8")
        print(f"Analysis written to {output_md}")

    return md


def main() -> None:
    results_path = sys.argv[1] if len(sys.argv) > 1 else "batch_results/batch_results.json"
    output_md = sys.argv[2] if len(sys.argv) > 2 else "batch_results/analysis.md"
    md = analyze(results_path, output_md)
    print(md)


if __name__ == "__main__":
    main()
