#!/usr/bin/env python3
"""Summarize representative subset results as a markdown table."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "representative_results" / "batch_results_all.json"


def main() -> None:
    if not RESULTS_PATH.exists():
        print(f"No results found at {RESULTS_PATH}")
        return

    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    rows: list[list[str]] = []
    for r in sorted(data, key=lambda x: (x["game_id"], x["mode"])):
        rows.append([
            r["game_id"],
            r["mode"],
            str(r["steps"]),
            f"{r['composite']:.3f}",
            f"{r['activity']:.3f}",
            str(r.get("details", {}).get("stall_steps", "—")),
            f"{r['elapsed_s']:.1f}s",
            r["error"][:30] if r.get("error") else "",
        ])

    headers = ["game_id", "mode", "steps", "composite", "activity", "stall", "wall", "error"]
    col_widths = [max(len(headers[i]), max((len(row[i]) for row in rows), default=0)) for i in range(len(headers))]

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"

    print(fmt(headers))
    print("|" + "|".join("-" * (w + 2) for w in col_widths) + "|")
    for row in rows:
        print(fmt(row))

    print(f"\nTotal runs: {len(data)}")
    ok = [r for r in data if not r.get("error")]
    print(f"OK: {len(ok)}, Errors: {len(data) - len(ok)}")
    by_mode: dict[str, list[float]] = {}
    for r in ok:
        by_mode.setdefault(r["mode"], []).append(r["composite"])
    for mode in sorted(by_mode):
        comps = by_mode[mode]
        print(f"  {mode:20s} mean composite={sum(comps)/len(comps):.3f} (n={len(comps)})")


if __name__ == "__main__":
    main()
