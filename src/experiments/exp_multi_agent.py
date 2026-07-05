"""Multi-agent communication experiment runner.

Compares single-agent (mode="api", mode="rule") vs multi-agent (mode="multi")
game performance across N games × M runs, measuring latency, memory usage,
and decision quality.

Usage::

    python -m src.experiments.exp_multi_agent \\
        --games 5 --runs 3 --max-steps 50 \\
        --modes api,rule,multi \\
        --output /tmp/exp_results.json \\
        --report /tmp/exp_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.agent.hybrid_agent import HybridAgent
from src.tools.game_catalog import GameEntry, generate_json, scan_games

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Metrics for a single game × mode × run trial."""

    game_id: str
    mode: str
    run: int
    steps: int = 0
    win: bool = False
    completed: bool = False
    reason: str = ""
    elapsed_s: float = 0.0
    avg_latency_ms: float = 0.0
    memory_hits: dict[str, int] = field(default_factory=lambda: {
        "episodic_queries": 0,
        "semantic_queries": 0,
        "rule_matches": 0,
        "verdict_recommendations": 0,
    })

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentReport:
    """Aggregated experiment results."""

    config: dict[str, Any] = field(default_factory=dict)
    summary: list[dict[str, Any]] = field(default_factory=list)
    details: list[RunResult] = field(default_factory=list)
    elapsed_total_s: float = 0.0


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


async def run_trial(
    game: GameEntry,
    mode: str,
    max_steps: int,
    run_index: int,
    memory_config: dict[str, Any] | None = None,
    api_client: Any = None,
    vlm_engine: Any = None,
) -> RunResult:
    """Run a single game × mode trial and return metrics.

    Parameters
    ----------
    game:
        Game catalog entry with path to HTML file.
    mode:
        Game-playing mode (``"api"``, ``"rule"``, ``"multi"``, etc.).
    max_steps:
        Maximum observation/action cycles.
    run_index:
        Trial number (0-based) for identifying this run.
    memory_config:
        Optional dict with ``db_path`` key for persistent memory.
    api_client:
        OpenCodeGoClient instance (required for API-based modes).
    vlm_engine:
        GameAgentInference instance (required for VLM modes).

    Returns
    -------
    RunResult with collected metrics.
    """
    game_id = game.game_id
    game_path = game.path

    logger.info("Run %d | %s | mode=%s | steps=%d", run_index + 1, game_id, mode, max_steps)

    agent = HybridAgent(
        mode=mode,
        game_id=game_id,
        api_client=api_client,
        vlm_engine=vlm_engine,
        memory_config=memory_config,
    )

    t0 = time.perf_counter()
    try:
        result = await agent.run_game(game_path, max_steps=max_steps, headed=False)
    except Exception as exc:
        logger.warning("Run %d crashed: %s", run_index + 1, exc)
        result = {"completed": False, "win": False, "steps": 0, "reason": str(exc)}

    elapsed = time.perf_counter() - t0
    steps = result.get("steps", 0)
    avg_latency = (elapsed / max(steps, 1)) * 1000

    # Collect memory statistics from agent context
    memory_hits = _extract_memory_hits(agent)

    return RunResult(
        game_id=game_id,
        mode=mode,
        run=run_index + 1,
        steps=steps,
        win=result.get("win", False),
        completed=result.get("completed", False),
        reason=result.get("reason", ""),
        elapsed_s=round(elapsed, 2),
        avg_latency_ms=round(avg_latency, 1),
        memory_hits=memory_hits,
    )


def _extract_memory_hits(agent: HybridAgent) -> dict[str, int]:
    """Extract memory usage counts from agent context metadata."""
    hits: dict[str, int] = {
        "episodic_queries": 0,
        "semantic_queries": 0,
        "rule_matches": 0,
        "verdict_recommendations": 0,
    }
    try:
        ctx = getattr(agent, "_ctx", None)
        if ctx is not None:
            meta = ctx.metadata
            hits["episodic_queries"] = len(meta.get("previous_sessions", []))
            hits["semantic_queries"] = len(meta.get("relevant_knowledge", []))
            hits["verdict_recommendations"] = len(ctx.errors)
            if meta.get("matched_rule"):
                hits["rule_matches"] = 1
    except Exception:
        pass
    return hits


# ---------------------------------------------------------------------------
# Batch experiment execution
# ---------------------------------------------------------------------------


async def run_experiments(
    games: list[GameEntry],
    modes: list[str],
    runs_per_game: int,
    max_steps: int,
    memory_config: dict[str, Any] | None = None,
    api_client: Any = None,
    vlm_engine: Any = None,
    seed: int = 42,
) -> ExperimentReport:
    """Run a batch of experiments across games × modes × runs.

    Parameters
    ----------
    games:
        List of annotated game entries from the catalog.
    modes:
        List of mode strings to test (e.g. ``["api", "rule", "multi"]``).
    runs_per_game:
        Number of trials per game × mode combination.
    max_steps:
        Maximum steps per trial.
    memory_config:
        Optional memory configuration dict.
    api_client:
        Optional OpenCodeGoClient for API modes.
    vlm_engine:
        Optional GameAgentInference for VLM modes.
    seed:
        Random seed for reproducibility.

    Returns
    -------
    ExperimentReport with aggregated results.
    """
    random.seed(seed)
    random.shuffle(games)

    report = ExperimentReport(
        config={
            "n_games": len(games),
            "modes": modes,
            "runs_per_game": runs_per_game,
            "max_steps": max_steps,
            "seed": seed,
        },
    )

    t0 = time.perf_counter()
    total_trials = len(games) * len(modes) * runs_per_game
    trial_index = 0

    for game in games:
        for mode in modes:
            for run_idx in range(runs_per_game):
                trial_index += 1
                logger.info("[%d/%d] %s mode=%s run=%d", trial_index, total_trials, game.game_id, mode, run_idx + 1)

                result = await run_trial(
                    game=game,
                    mode=mode,
                    max_steps=max_steps,
                    run_index=run_idx,
                    memory_config=memory_config,
                    api_client=api_client,
                    vlm_engine=vlm_engine,
                )
                report.details.append(result)

    report.elapsed_total_s = round(time.perf_counter() - t0, 2)

    # Build summary aggregation
    report.summary = _build_summary(report.details, modes)
    return report


def _build_summary(details: list[RunResult], modes: list[str]) -> list[dict[str, Any]]:
    """Aggregate trial results per game × mode."""
    from collections import defaultdict

    aggregator: dict[tuple[str, str], list[RunResult]] = defaultdict(list)
    for r in details:
        aggregator[(r.game_id, r.mode)].append(r)

    summary: list[dict[str, Any]] = []
    for (game_id, mode), runs in sorted(aggregator.items()):
        n = len(runs)
        summary.append({
            "game_id": game_id,
            "mode": mode,
            "runs": n,
            "avg_steps": round(sum(r.steps for r in runs) / n, 1),
            "win_rate": round(sum(1 for r in runs if r.win) / n, 2),
            "avg_elapsed_s": round(sum(r.elapsed_s for r in runs) / n, 2),
            "avg_latency_ms": round(sum(r.avg_latency_ms for r in runs) / n, 1),
            "memory_episodic": sum(r.memory_hits["episodic_queries"] for r in runs),
            "memory_semantic": sum(r.memory_hits["semantic_queries"] for r in runs),
            "memory_rules": sum(r.memory_hits["rule_matches"] for r in runs),
        })
    return summary


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_markdown(report: ExperimentReport) -> str:
    """Generate a markdown comparison table from experiment results.

    Parameters
    ----------
    report:
        Aggregated experiment report.

    Returns
    -------
    Formatted markdown string with comparison tables.
    """
    lines: list[str] = []
    lines.append("# Multi-Agent Communication Experiment Report")
    lines.append("")
    lines.append(f"**Config**: {report.config['n_games']} games × {len(report.config['modes'])} modes × {report.config['runs_per_game']} runs, max {report.config['max_steps']} steps/run, seed={report.config['seed']}")
    lines.append(f"**Total time**: {report.elapsed_total_s}s")
    lines.append("")

    # Per-mode summary
    lines.append("## Mode Comparison")
    lines.append("")
    lines.append("| Game | Mode | Avg Steps | Win Rate | Avg Time | Avg Latency | Mem Episodic | Mem Semantic | Mem Rules |")
    lines.append("|------|------|-----------|----------|----------|-------------|-------------|-------------|----------|")
    for row in report.summary:
        lines.append(
            f"| {row['game_id']} | {row['mode']} | "
            f"{row['avg_steps']} | {row['win_rate']:.0%} | "
            f"{row['avg_elapsed_s']}s | {row['avg_latency_ms']}ms | "
            f"{row['memory_episodic']} | {row['memory_semantic']} | {row['memory_rules']} |"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Game selection helpers
# ---------------------------------------------------------------------------


def select_diverse_games(games: list[GameEntry], n: int) -> list[GameEntry]:
    """Select N games with maximum diversity in controls + categories.

    Only annotated games (with ``has_annotations=True``) are considered.
    If fewer than N have annotations, all are returned.
    """
    annotated = [g for g in games if g.has_annotations]
    if len(annotated) <= n:
        return annotated

    # Sort by complexity (scene_elements + task_timeline) descending
    annotated.sort(key=lambda g: g.scene_elements_count + g.task_timeline_steps, reverse=True)
    return annotated[:n]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multi-agent communication experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--games", type=int, default=5, help="Number of games to test (default: 5)")
    parser.add_argument("--runs", type=int, default=3, help="Runs per game×mode combination (default: 3)")
    parser.add_argument("--max-steps", type=int, default=50, help="Max steps per run (default: 50)")
    parser.add_argument("--modes", type=str, default="api,rule,multi", help="Comma-separated modes (default: api,rule,multi)")
    parser.add_argument("--output", type=str, default="experiment_results.json", help="Output JSON path")
    parser.add_argument("--report", type=str, default="experiment_report.md", help="Output Markdown report path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--memory-db", type=str, default="experiment_memory.db", help="SQLite path for persistent memory")
    parser.add_argument("--api-key", type=str, default="", help="OpenCodeGo API key (or use OPENCODE_API_KEY env)")
    parser.add_argument("--vlm-server", type=str, default="", help="VLM inference server URL")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # Resolve games
    all_games = scan_games()
    annotated = [g for g in all_games if g.has_annotations]
    games = select_diverse_games(annotated, args.games)
    modes = [m.strip() for m in args.modes.split(",")]

    print(f"Games: {len(games)} selected ({len(annotated)} annotated available)")
    print(f"Modes: {modes}")
    print(f"Runs: {args.runs} per mode per game")
    print(f"Total trials: {len(games) * len(modes) * args.runs}")

    if args.dry_run:
        print("\nPlanned trials:")
        for g in games:
            for m in modes:
                print(f"  {g.game_id} mode={m} × {args.runs} runs")
        print("\nDry run complete — no trials executed.")
        return

    # Setup optional API client
    api_client = None
    if args.api_key:
        from src.agent.api_client import OpenCodeGoClient
        api_client = OpenCodeGoClient(api_key=args.api_key)

    # Memory config
    memory_config: dict[str, Any] | None = None
    if args.memory_db:
        memory_config = {"db_path": args.memory_db}

    print("\nRunning experiments...")
    report = await run_experiments(
        games=games,
        modes=modes,
        runs_per_game=args.runs,
        max_steps=args.max_steps,
        memory_config=memory_config,
        api_client=api_client,
        seed=args.seed,
    )

    # Save JSON
    output = {
        "config": report.config,
        "summary": report.summary,
        "details": [d.to_dict() for d in report.details],
        "elapsed_total_s": report.elapsed_total_s,
    }
    Path(args.output).write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"Results saved to: {args.output}")

    # Save Markdown report
    md = generate_markdown(report)
    Path(args.report).write_text(md, encoding="utf-8")
    print(f"Report saved to: {args.report}")

    print(f"\nDone. {len(report.details)} trials in {report.elapsed_total_s}s")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    asyncio.run(_main(argv))


if __name__ == "__main__":
    main()
