#!/usr/bin/env python3
"""Diagnose why SSD_00483P01 multi-bus has activity=0.

Runs 00483 under several memory configurations to isolate the cause.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_FILE.is_file():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key and val and key not in os.environ:
            os.environ[key] = val.strip('"').strip("'")

from src.experiments.batch_runner import BatchConfig, run_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"
GAME_ID = "SSD_00483P01"


def _resolve_html(game_id: str) -> str | None:
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


def _empty_memory_path(name: str) -> str:
    path = ROOT / "diagnosis_00483_results" / f"strategy_memory_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"unknown": {}}', encoding="utf-8")
    return str(path)


async def main() -> None:
    html = _resolve_html(GAME_ID)
    if not html:
        print(f"HTML not found for {GAME_ID}", file=sys.stderr)
        sys.exit(1)

    output_dir = ROOT / "diagnosis_00483_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    configs: list[dict[str, Any]] = [
        {
            "name": "rule",
            "mode": "rule",
            "memory_path": _empty_memory_path("rule"),
        },
        {
            "name": "multi-bus_clean-memory",
            "mode": "multi-bus",
            "memory_path": _empty_memory_path("multi-bus_clean"),
        },
        {
            "name": "multi-bus-memory_clean-memory",
            "mode": "multi-bus-memory",
            "memory_path": _empty_memory_path("multi-bus-memory_clean"),
        },
        {
            "name": "multi-bus_existing-memory",
            "mode": "multi-bus",
            "memory_path": str(ROOT / "strategy_memory_A_representative.json"),
        },
    ]

    all_results = []
    for cfg in configs:
        print(f"\nRun: {cfg['name']} ({cfg['mode']})", flush=True)
        batch_config = BatchConfig(
            games={GAME_ID: html},
            modes=[cfg["mode"]],
            seeds=[42],
            max_steps=25,
            headed=False,
            collect_dataset=True,
            output_dir=str(output_dir / cfg["name"]),
            memory_config={"strategy_memory_path": cfg["memory_path"]},
            config_overrides={"probe_timeout_ms": 30_000},
        )
        results = await run_batch(batch_config)
        all_results.extend(results)

    summary = [
        {
            "name": cfg["name"],
            "mode": r.mode,
            "steps": r.steps,
            "composite": r.composite,
            "activity": r.activity,
            "elapsed_s": r.elapsed_s,
            "details": r.details,
            "trajectory_path": r.trajectory_path,
            "error": r.error,
        }
        for cfg, r in zip(configs, all_results)
    ]
    summary_path = output_dir / "diagnosis_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("Diagnosis summary")
    print("=" * 60)
    for item in summary:
        print(
            f"{item['name']:30s} composite={item['composite']:.3f} "
            f"activity={item['activity']:.3f} move={item['details'].get('move_steps', 0):2d} "
            f"tap={item['details'].get('tap_steps', 0):2d} stall={item['details'].get('stall_steps', 0):2d}"
        )
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
