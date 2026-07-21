#!/usr/bin/env python3
"""Merge the B_tap continuation results back into full_matrix_results/B_tap/.

Usage::

    .venv/bin/python scripts/merge_B_tap_continue.py

This copies the continuation trajectories into ``B_tap/trajectories/`` and
appends the new ``batch_results.json`` entries to the existing ``B_tap`` summary,
then re-runs the analyzer.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.experiments.analyze_batch import analyze

ROOT = Path(__file__).resolve().parent.parent
B_TAP_DIR = ROOT / "full_matrix_results" / "B_tap"
CONTINUE_DIR = ROOT / "full_matrix_results" / "B_tap_continue"


def main() -> int:
    if not CONTINUE_DIR.exists():
        print(f"[skip] {CONTINUE_DIR} does not exist; nothing to merge.")
        return 0

    # Copy trajectories
    src_traj = CONTINUE_DIR / "trajectories"
    dst_traj = B_TAP_DIR / "trajectories"
    dst_traj.mkdir(parents=True, exist_ok=True)
    copied = 0
    if src_traj.exists():
        for f in src_traj.glob("*.jsonl"):
            dst = dst_traj / f.name
            shutil.copy2(f, dst)
            copied += 1
    print(f"Copied {copied} trajectory files into {dst_traj}")

    # Merge batch_results.json
    continue_results_path = CONTINUE_DIR / "batch_results.json"
    main_results_path = B_TAP_DIR / "batch_results.json"

    if not continue_results_path.exists():
        print(f"[warn] {continue_results_path} missing", file=sys.stderr)
        return 1

    new_entries = json.loads(continue_results_path.read_text(encoding="utf-8"))

    if main_results_path.exists():
        existing = json.loads(main_results_path.read_text(encoding="utf-8"))
    else:
        existing = []

    # Avoid duplicates by (game_id, mode, seed)
    seen = {(e["game_id"], e["mode"], e["seed"]) for e in existing}
    merged = list(existing)
    added = 0
    for e in new_entries:
        key = (e["game_id"], e["mode"], e["seed"])
        if key not in seen:
            merged.append(e)
            seen.add(key)
            added += 1

    main_results_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merged batch_results.json: {len(existing)} existing + {added} new = {len(merged)} total")

    # Rewrite analysis.md
    analysis_path = B_TAP_DIR / "analysis.md"
    analyze(main_results_path, analysis_path)
    print(f"Updated {analysis_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
