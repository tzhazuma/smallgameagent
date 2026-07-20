#!/usr/bin/env python3
"""Merge multiple VLM training-data directories into one.

Usage::

    .venv/bin/python scripts/merge_vlm_datasets.py \
        vlm-training-data-processed-runs \
        vlm-training-data-representative \
        vlm-training-data-A-full \
        --output vlm-training-data-merged

For each task the script concatenates ``train.jsonl``, ``val.jsonl`` and
``smoke.jsonl`` from all input directories, removes exact-duplicate lines,
and writes a fresh ``dataset-manifest.json`` with per-split counts.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("merge_vlm_datasets")

SPLITS = ("train", "val", "smoke")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "dataset-manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def discover_tasks(input_dirs: list[Path]) -> set[str]:
    tasks: set[str] = set()
    for d in input_dirs:
        if not d.is_dir():
            continue
        for task_dir in (d / "tasks").iterdir():
            if task_dir.is_dir():
                tasks.add(task_dir.name)
    return tasks


def merge_split(task_name: str, split: str, input_dirs: list[Path], output_dir: Path) -> int:
    seen: set[str] = set()
    out_task_dir = output_dir / "tasks" / task_name
    out_task_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_task_dir / f"{split}.jsonl"

    with open(out_path, "w", encoding="utf-8") as out_f:
        for d in input_dirs:
            src = d / "tasks" / task_name / f"{split}.jsonl"
            if not src.is_file():
                continue
            with open(src, encoding="utf-8") as in_f:
                for line in in_f:
                    line = line.rstrip("\n")
                    if not line or line in seen:
                        continue
                    seen.add(line)
                    out_f.write(line + "\n")
    return len(seen)


def merge_datasets(input_dirs: list[Path], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = sorted(discover_tasks(input_dirs))
    logger.info("Discovered tasks: %s", tasks)

    manifest: dict[str, Any] = {
        "source": "merged",
        "input_dirs": [str(d) for d in input_dirs],
        "tasks": {},
        "total_samples": 0,
    }

    total = 0
    for task in tasks:
        task_entry: dict[str, Any] = {"splits": {}}
        task_total = 0
        for split in SPLITS:
            count = merge_split(task, split, input_dirs, output_dir)
            task_entry["splits"][split] = count
            task_total += count
        task_entry["total"] = task_total
        manifest["tasks"][task] = task_entry
        total += task_total
        logger.info("Task %s: %d samples", task, task_total)

    manifest["total_samples"] = total
    (output_dir / "dataset-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Merged dataset written to %s (total_samples=%d)", output_dir, total)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge VLM training-data directories")
    parser.add_argument("input_dirs", nargs="+", type=Path, help="Input dataset directories")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    merge_datasets(args.input_dirs, args.output)


if __name__ == "__main__":
    main()
