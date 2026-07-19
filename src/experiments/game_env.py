"""Verifiers-style game environment and rubrics for agent evaluation.

Inspired by the verifiers-v1 RL framework (https://www.primeintellect.ai/blog/verifiers-v1):
an *environment* produces trajectories, and *rubrics* score them on multiple
axes instead of a single win/loss bit. This module works in two modes:

- :func:`score_trajectory` — offline scoring of an existing experiment JSON
  (the ``step_log`` format produced by our instrumented runs).
- :class:`GameEnv` — online rollout: drives a ``HybridAgent`` run and scores
  the resulting trajectory.

Rubric axes (all in ``[0, 1]`` unless noted):

- ``completion`` — 1 when the run ends with ``win`` or ``completed``.
- ``progress_ratio`` — fraction of candidate-set transitions observed relative
  to the run's own furthest point (milestones reached / steps used, normalised
  per 100 steps).
- ``activity`` — 1 - stall ratio; stall = consecutive steps with near-zero
  world displacement while issuing move actions.
- ``consistency`` — 1 - normalised consistency violations (world-model stale
  events + capability flips + failCount flips observed).
- ``composite`` — weighted sum (completion 0.4, progress 0.3, activity 0.15,
  consistency 0.15).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Displacement below which a move step counts as stalled (world units).
STALL_DISPLACEMENT = 0.05
#: Weights for the composite score.
W_COMPLETION = 0.4
W_PROGRESS = 0.3
W_ACTIVITY = 0.15
W_CONSISTENCY = 0.15


@dataclass
class RubricScore:
    """One scored trajectory."""

    completion: float
    progress_ratio: float
    activity: float
    consistency: float
    composite: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "completion": round(self.completion, 4),
            "progress_ratio": round(self.progress_ratio, 4),
            "activity": round(self.activity, 4),
            "consistency": round(self.consistency, 4),
            "composite": round(self.composite, 4),
            "details": self.details,
        }


def _displacements(step_log: list[dict[str, Any]]) -> list[float]:
    """Per-step world displacement from consecutive player positions."""
    out: list[float] = []
    for prev, cur in zip(step_log, step_log[1:]):
        p, c = prev.get("player") or {}, cur.get("player") or {}
        if "x" not in p or "x" not in c:
            out.append(0.0)
            continue
        out.append(math.hypot(c.get("x", 0) - p.get("x", 0), c.get("z", 0) - p.get("z", 0)))
    return out


def score_trajectory(
    step_log: list[dict[str, Any]],
    result: dict[str, Any] | None = None,
    candidate_transitions: list[dict[str, Any]] | None = None,
    world_model_stats: dict[str, Any] | None = None,
) -> RubricScore:
    """Score a trajectory dict (experiment JSON shape) on all rubric axes.

    Parameters
    ----------
    step_log:
        List of per-step records with at least ``player`` (``x``/``z``),
        ``action``, ``reason`` and ``keyNumbers``.
    result:
        Run result summary (``completed`` / ``win`` / ``steps``).
    candidate_transitions:
        Milestone transitions (scene/candidate-set changes).
    world_model_stats:
        ``VersionedWorldModel.stats()`` when available.
    """
    result = result or {}
    n = len(step_log)
    if n == 0:
        return RubricScore(0, 0, 0, 0, 0, {"error": "empty trajectory"})

    completion = 1.0 if (result.get("completed") or result.get("win")) else 0.0

    transitions = len(candidate_transitions or [])
    # progress: milestones per 100 steps, capped at 1 (4+/100 steps is excellent)
    progress = min(1.0, transitions / max(1.0, n / 100.0) / 4.0)

    move_steps = [s for s in step_log if s.get("action") == "move"]
    tap_steps = [s for s in step_log if s.get("action") == "tap"]
    disps = _displacements(step_log)
    # A step where the agent issued a tap is intentional interaction, not a
    # stall — even though the player position doesn't change.
    active_actions = {i for i, s in enumerate(step_log) if s.get("action") in ("move", "tap")}
    stall = sum(
        1 for i, d in enumerate(disps)
        if d < STALL_DISPLACEMENT and (i + 1) not in active_actions
    )
    stall_ratio = stall / max(1, len(disps))
    activity = 1.0 - stall_ratio

    violations = 0
    if world_model_stats:
        violations += int(world_model_stats.get("stale_events", 0))
        violations += int(world_model_stats.get("capability_flips", 0))
    fail_flips = 0
    prev_fail = 0
    for s in step_log:
        fc = (s.get("keyNumbers") or {}).get("_failCount") or 0
        if isinstance(fc, (int, float)) and fc != prev_fail:
            fail_flips += 1
            prev_fail = fc
    violations += fail_flips
    # normalise: 10 violations per 100 steps -> consistency 0
    consistency = max(0.0, 1.0 - violations / max(1.0, n / 10.0))

    composite = (
        W_COMPLETION * completion
        + W_PROGRESS * progress
        + W_ACTIVITY * activity
        + W_CONSISTENCY * consistency
    )
    return RubricScore(
        completion=completion,
        progress_ratio=progress,
        activity=activity,
        consistency=consistency,
        composite=composite,
        details={
            "steps": n,
            "move_steps": len(move_steps),
            "tap_steps": len(tap_steps),
            "transitions": transitions,
            "stall_steps": stall,
            "fail_flips": fail_flips,
            "wm_violations": violations - fail_flips,
        },
    )


def score_experiment_json(path: str | Path) -> RubricScore:
    """Score an existing experiment JSON file (baseline / A-B format)."""
    data = json.loads(Path(path).read_text())
    return score_trajectory(
        step_log=data.get("step_log", []),
        result=data.get("result", {}),
        candidate_transitions=data.get("candidate_transitions", []),
        world_model_stats=(data.get("result") or {}).get("world_model_stats"),
    )


class GameEnv:
    """Online rollout environment wrapping a HybridAgent run.

    The agent decides; the env records and scores. Kept deliberately thin —
    the instrumentation scripts under ``/tmp`` already produce the trajectory
    format this class scores.
    """

    def __init__(self, game_id: str, html_path: str, mode: str = "rule") -> None:
        self.game_id = game_id
        self.html_path = html_path
        self.mode = mode

    async def rollout(self, max_steps: int = 300) -> dict[str, Any]:
        """Run one episode and return the scored trajectory."""
        from src.agent.hybrid_agent import HybridAgent

        agent = HybridAgent(mode=self.mode, game_id=self.game_id)
        result = await agent.run_game(self.html_path, max_steps=max_steps)
        score = score_trajectory(
            step_log=result.get("step_log", []) if isinstance(result, dict) else [],
            result=result if isinstance(result, dict) else {},
            world_model_stats=(result or {}).get("world_model_stats") if isinstance(result, dict) else None,
        )
        return {"result": result, "rubric": score.to_dict()}
