#!/usr/bin/env python3
"""Full-scale batch matrix experiment — ALL games × ALL modes × 2 seeds.

Reads auto-calibration results to classify games, then runs the appropriate
modes for each game type:

- A 类 (joystick): rule / multi-bus-memory / multi-bus / hierarchical
- B 类 (tap-only): rule / multi-bus-memory (tap-only driver)
- C 类 (probe fail): skipped

Produces full_matrix_results/batch_results.json + analysis.md + trajectory JSONL.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from configs.game_profiles import GAME_PROFILES, get_game_type, get_driver_for_type
from src.experiments.analyze_batch import analyze
from src.experiments.batch_runner import BatchConfig, run_batch

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"
CAL_JSON = ROOT / "configs" / "auto_calibrated_profiles.json"

MAX_STEPS = 25
SEEDS = [42, 123]


def _resolve_html(game_id: str) -> str | None:
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


def _all_game_ids() -> list[str]:
    """Return all game IDs from _extracted/games/."""
    ids = []
    for child in GAMES_DIR.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        parts = name.split("_")
        if len(parts) >= 2:
            gid = f"{parts[0]}_{parts[1]}"
        else:
            gid = name
        ids.append(gid)
    return sorted(ids)


def classify_games() -> dict[str, list[str]]:
    """Classify all games into A/B/C types."""
    types = {"A": [], "B": [], "C": []}
    for gid in _all_game_ids():
        gtype = get_game_type(gid)
        types[gtype].append(gid)
    return types


def build_configs() -> list[dict]:
    """Build BatchConfig objects for each game type group."""
    types = classify_games()
    print(f"Game classification: A={len(types['A'])}, B={len(types['B'])}, C={len(types['C'])}")
    for gtype in sorted(types):
        for gid in types[gtype]:
            driver = get_driver_for_type(gid)
            has_profile = gid in GAME_PROFILES
            print(f"  [{gtype}] {gid} → {driver}{' (tuned)' if has_profile else ' (auto)'}")

    configs = []

    # A 类: full matrix
    a_games = {}
    for gid in types["A"]:
        html = _resolve_html(gid)
        if html:
            a_games[gid] = html
    if a_games:
        configs.append({
            "name": "A_full",
            "games": a_games,
            "modes": ["rule", "multi-bus-memory", "multi-bus", "hierarchical"],
        })

    # B 类: tap-only, reduced modes
    b_games = {}
    for gid in types["B"]:
        html = _resolve_html(gid)
        if html:
            b_games[gid] = html
    if b_games:
        configs.append({
            "name": "B_tap",
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

        batch_config = BatchConfig(
            games=cfg["games"],
            modes=cfg["modes"],
            seeds=SEEDS,
            max_steps=MAX_STEPS,
            headed=False,
            collect_dataset=True,
            output_dir=str(ROOT / "full_matrix_results" / cfg["name"]),
            memory_config={"strategy_memory_path": str(ROOT / f"strategy_memory_{cfg['name']}.json")},
            config_overrides={"probe_timeout_ms": 30_000},
        )
        results = await run_batch(batch_config)
        all_results.extend(results)

        # Per-group analysis
        results_path = ROOT / "full_matrix_results" / cfg["name"] / "batch_results.json"
        analysis_path = ROOT / "full_matrix_results" / cfg["name"] / "analysis.md"
        if results_path.exists():
            analyze(results_path, analysis_path)

    # Merge all results
    merged_path = ROOT / "full_matrix_results" / "batch_results_all.json"
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
    analyze(merged_path, ROOT / "full_matrix_results" / "analysis_all.md")

    # Summary
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
