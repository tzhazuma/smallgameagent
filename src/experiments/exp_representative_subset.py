#!/usr/bin/env python3
"""Representative subset experiment: 3 A-class + 3 B-class games.

Picks well-studied games and runs rule / multi-bus / multi-bus-memory /
hierarchical (A only) with a single seed.  Results go to
representative_results/batch_results.json and analysis.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Best-effort load .env so that PLAYWRIGHT_CHROMIUM_PATH and API keys are
# available when the script is launched without sourcing .env first.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_FILE.is_file():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key and val and key not in os.environ:
            os.environ[key] = val.strip('\"').strip("'")

from configs.game_profiles import get_driver_for_type, get_game_type  # noqa: E402
from src.experiments.analyze_batch import analyze  # noqa: E402
from src.experiments.batch_runner import BatchConfig, RunResult, run_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"

MAX_STEPS = 25
SEEDS = [42]

REPRESENTATIVE_GAMES = {
    # A-class (joystick)
    "SSD_00461P01": "塔防",
    "SSD_00483P01": "吸沙抽水",
    "SSD_00522P02": "地下炸矿",
    # B-class (tap-only)
    "SSD_00382P01": "低坑杀鲨鱼",
    "SSD_00594P02": "破石收水",
    "SSD_00742P01": "加油小镇",
}


def _resolve_html(game_id: str) -> str | None:
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


def build_configs() -> list[dict]:
    a_games: dict[str, str] = {}
    b_games: dict[str, str] = {}
    for gid, label in REPRESENTATIVE_GAMES.items():
        html = _resolve_html(gid)
        if not html:
            print(f"Warning: HTML not found for {gid}", file=sys.stderr)
            continue
        gtype = get_game_type(gid)
        print(f"  [{gtype}] {gid} ({label}) → {get_driver_for_type(gid)}")
        if gtype == "A":
            a_games[gid] = html
        else:
            b_games[gid] = html

    configs = []
    if a_games:
        # hierarchical requires a running LM Studio local VLM; excluded from
        # the default representative run to keep latency/cost bounded.
        configs.append({
            "name": "A_representative",
            "games": a_games,
            "modes": ["rule", "multi-bus", "multi-bus-memory"],
        })
    if b_games:
        configs.append({
            "name": "B_representative",
            "games": b_games,
            "modes": ["rule", "multi-bus-memory"],
        })
    return configs


async def main() -> None:
    configs = build_configs()
    if not configs:
        print("No games to run.", file=sys.stderr)
        return

    all_results = []
    for cfg in configs:
        print(f"\n{'='*60}")
        print(f"Group {cfg['name']}: {len(cfg['games'])} games × {len(cfg['modes'])} modes × {len(SEEDS)} seeds")
        print(f"{'='*60}", flush=True)

        group_results: list[RunResult] = []
        # Run each mode with its own isolated strategy memory so that
        # multi-bus and multi-bus-memory do not cross-pollinate within the
        # same batch.  This makes the per-mode numbers directly comparable.
        for mode in cfg["modes"]:
            memory_path = ROOT / "representative_results" / cfg["name"] / f"strategy_memory_{mode}.json"
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            batch_config = BatchConfig(
                games=cfg["games"],
                modes=[mode],
                seeds=SEEDS,
                max_steps=MAX_STEPS,
                headed=False,
                collect_dataset=True,
                output_dir=str(ROOT / "representative_results" / cfg["name"] / mode),
                memory_config={"strategy_memory_path": str(memory_path)},
                config_overrides={"probe_timeout_ms": 30_000},
            )
            results = await run_batch(batch_config)
            group_results.extend(results)
            all_results.extend(results)

        # Merge per-mode batch_results.json into the group summary expected
        # by the downstream analyze() helper.
        group_results_path = ROOT / "representative_results" / cfg["name"] / "batch_results.json"
        group_results_path.parent.mkdir(parents=True, exist_ok=True)
        group_results_path.write_text(
            json.dumps(
                [{"game_id": r.game_id, "mode": r.mode, "seed": r.seed,
                  "steps": r.steps, "composite": r.composite, "activity": r.activity,
                  "elapsed_s": r.elapsed_s, "details": r.details,
                  "trajectory_path": r.trajectory_path, "error": r.error}
                 for r in group_results],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        analyze(group_results_path, ROOT / "representative_results" / cfg["name"] / "analysis.md")

    merged_path = ROOT / "representative_results" / "batch_results_all.json"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    merged_path.write_text(
        json.dumps(
            [{"game_id": r.game_id, "mode": r.mode, "seed": r.seed,
              "steps": r.steps, "composite": r.composite, "activity": r.activity,
              "elapsed_s": r.elapsed_s, "details": r.details,
              "trajectory_path": r.trajectory_path, "error": r.error}
             for r in all_results],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    analyze(merged_path, ROOT / "representative_results" / "analysis_all.md")

    print(f"\n{'='*60}")
    print(f"TOTAL: {len(all_results)} runs completed")
    ok = [r for r in all_results if not r.error]
    print(f"  OK: {len(ok)}, Errors: {len(all_results) - len(ok)}")
    by_mode: dict[str, list[float]] = {}
    for r in ok:
        by_mode.setdefault(r.mode, []).append(r.composite)
    for mode in sorted(by_mode):
        comps = by_mode[mode]
        print(f"  {mode:20s} mean composite={sum(comps)/len(comps):.3f} (n={len(comps)})")
    print(f"Results: {merged_path}")


if __name__ == "__main__":
    asyncio.run(main())
