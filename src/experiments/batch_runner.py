"""Batch experiment runner — multi-game × multi-mode × multi-seed matrix.

Provides a reusable framework for running systematic experiments and
collecting per-step trajectory data for later analysis.

Usage::

    from src.experiments.batch_runner import BatchConfig, run_batch

    config = BatchConfig(
        games={"SSD_00461P01": "/path/to/game.html"},
        modes=["rule", "multi-bus-memory", "hierarchical"],
        seeds=[42, 123],
        max_steps=30,
    )
    results = asyncio.run(run_batch(config))
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.api_client import OpenCodeGoClient
from src.agent.hybrid_agent import HybridAgent
from src.experiments.game_env import score_trajectory


@dataclass
class BatchConfig:
    """Configuration for a batch experiment run."""

    games: dict[str, str] = field(default_factory=dict)
    """Mapping of game_id → HTML path."""

    modes: list[str] = field(default_factory=lambda: ["rule"])
    """Agent modes to test."""

    seeds: list[int] = field(default_factory=lambda: [42])
    """Random seeds (currently unused by the agent but recorded for reproducibility)."""

    max_steps: int = 30
    """Maximum steps per run."""

    headed: bool = False
    """Show browser window."""

    collect_dataset: bool = True
    """Write per-step trajectory JSONL files."""

    output_dir: str = "batch_results"
    """Directory for results and trajectories."""

    api_client: Any = None
    """Optional pre-created API client (shared across runs)."""

    memory_config: dict[str, Any] = field(default_factory=dict)
    """Memory config passed to HybridAgent."""

    config_overrides: dict[str, Any] = field(default_factory=dict)
    """Extra config passed to HybridAgent."""


@dataclass
class RunResult:
    """Result of a single experiment run."""

    game_id: str
    mode: str
    seed: int
    steps: int
    composite: float
    activity: float
    elapsed_s: float
    details: dict[str, Any]
    trajectory_path: str = ""
    error: str = ""


async def _run_single(
    game_id: str,
    html_path: str,
    mode: str,
    seed: int,
    config: BatchConfig,
    traj_dir: Path,
) -> RunResult:
    """Execute one (game, mode, seed) combination."""
    t0 = time.time()
    agent_config = {
        "max_steps": config.max_steps,
        "probe_timeout_ms": 18_000,
        "collect_dataset": config.collect_dataset,
        "dataset_output_dir": str(traj_dir),
    }
    agent_config.update(config.config_overrides)

    try:
        agent = HybridAgent(
            mode=mode,
            game_id=game_id,
            api_client=config.api_client,
            config=agent_config,
            memory_config=config.memory_config,
        )
        result = await agent.run_game(html_path, max_steps=config.max_steps, headed=config.headed)
    except Exception as exc:
        elapsed = time.time() - t0
        return RunResult(
            game_id=game_id, mode=mode, seed=seed,
            steps=0, composite=0.0, activity=0.0,
            elapsed_s=round(elapsed, 1), details={},
            error=str(exc)[:300],
        )

    elapsed = time.time() - t0
    score = score_trajectory(
        step_log=result.get("step_log", []),
        result=result,
        candidate_transitions=result.get("candidate_transitions", []),
        world_model_stats=result.get("world_model_stats"),
    )

    # Write trajectory JSONL
    traj_path = traj_dir / f"{game_id}_{mode}_seed{seed}.jsonl"
    if config.collect_dataset and result.get("step_log"):
        with open(traj_path, "w", encoding="utf-8") as f:
            for step_record in result["step_log"]:
                f.write(json.dumps(step_record, ensure_ascii=False, default=str) + "\n")

    return RunResult(
        game_id=game_id,
        mode=mode,
        seed=seed,
        steps=result.get("steps", 0),
        composite=score.composite,
        activity=score.activity,
        elapsed_s=round(elapsed, 1),
        details=score.details,
        trajectory_path=str(traj_path) if traj_path.exists() else "",
    )


async def run_batch(config: BatchConfig) -> list[RunResult]:
    """Run all (game × mode × seed) combinations sequentially.

    Returns a list of RunResult objects. Also writes ``batch_results.json``
    to ``config.output_dir``.
    """
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_dir = out_dir / "trajectories"
    traj_dir.mkdir(exist_ok=True)

    # Create shared API client if needed and not provided
    if config.api_client is None and any(m in ("api", "hierarchical", "api-rule", "api-memory") for m in config.modes):
        try:
            config.api_client = OpenCodeGoClient()
        except Exception:
            pass

    results: list[RunResult] = []
    total = len(config.games) * len(config.modes) * len(config.seeds)
    done = 0

    for game_id, html_path in config.games.items():
        if not Path(html_path).exists():
            print(f"  [skip] {game_id}: HTML not found at {html_path}")
            continue
        for mode in config.modes:
            for seed in config.seeds:
                done += 1
                print(f"  [{done}/{total}] {game_id} / {mode} / seed={seed}", flush=True)
                r = await _run_single(game_id, html_path, mode, seed, config, traj_dir)
                results.append(r)
                status = f"composite={r.composite:.3f}" if not r.error else f"ERROR:{r.error[:60]}"
                print(f"    → steps={r.steps} {status} ({r.elapsed_s}s)", flush=True)

    # Write summary
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
            "trajectory_path": r.trajectory_path,
            "error": r.error,
        }
        for r in results
    ]
    summary_path = out_dir / "batch_results.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nBatch complete: {len(results)} runs → {summary_path}")
    return results
