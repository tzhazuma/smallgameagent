"""Recovery ladder — ordered recovery steps per failure code.

Python port of gah recovery-ladder.mjs. Each failure code maps to an ordered
list of recovery steps; the ladder advances cursor when a step's attempts are
exhausted. The semantic objective fingerprint is protected: recovery may never
change the goal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RECOVERY_STEPS: dict[str, list[str]] = {
    "NO_PATH": ["REFRESH_LOCAL_MAP", "REPLAN", "PORTAL_CENTER", "INCREASE_CLEARANCE", "REVERSE", "EXIT_REENTER"],
    "OCCLUDED": ["REFRESH_LOCAL_MAP", "REPLAN", "INCREASE_CLEARANCE", "REVERSE", "EXIT_REENTER"],
    "OUT_OF_REACH": ["REFRESH_GEOMETRY", "REPLAN", "PORTAL_CENTER", "INCREASE_CLEARANCE", "REVERSE"],
    "STUCK": ["REFRESH_LOCAL_MAP", "REPLAN", "INCREASE_CLEARANCE", "REVERSE", "EXIT_REENTER"],
    "OSCILLATING": ["DROP_PASSED_WAYPOINTS", "REPLAN", "PORTAL_CENTER", "INCREASE_CLEARANCE", "REVERSE"],
    "WRONG_CONTROL_MAPPING": ["SETTLE", "RECALIBRATE", "DIRECTION_CORRECTION"],
    "TARGET_STALE": ["REFRESH_TARGET_CATALOG", "REVALIDATE_SAME_TARGET_IDENTITY"],
    "NO_RESOURCE": ["REFRESH_FRONTIER", "CHECK_COMBAT_CHAIN", "CHECK_CAPACITY", "SELECT_SAME_ROLE_SOURCE"],
    "CAPACITY_FULL": ["REFRESH_REQUIREMENT", "DELIVER_OR_UPGRADE"],
    "SEMANTIC_UNKNOWN": ["RETAIN_FAILURE_CAPSULE"],
    "WORLD_CHANGED": ["INVALIDATE_SCENE_CACHE", "REFRESH", "REVALIDATE", "REPLAN"],
    "TIMEOUT": ["REFRESH_TASK_PROGRESS", "REPLAN"],
}

DEFAULT_ATTEMPTS_PER_STEP = 2


@dataclass
class RecoveryLadder:
    failure_code: str
    objective_fingerprint: str | None = None
    maximum_attempts_per_step: int = DEFAULT_ATTEMPTS_PER_STEP
    _steps: list[str] = field(default_factory=list)
    _cursor: int = 0
    _attempts_for_step: int = 0
    _exhausted: bool = False

    def __post_init__(self) -> None:
        if not self._steps:
            self._steps = list(RECOVERY_STEPS.get(self.failure_code, ["REPLAN"]))

    def next(self, current_objective_fingerprint: str | None = None) -> dict:
        """Return the next recovery step, or {'exhausted': True} at the end."""
        if self._exhausted:
            return {"exhausted": True}
        if (
            current_objective_fingerprint is not None
            and self.objective_fingerprint is not None
            and current_objective_fingerprint != self.objective_fingerprint
        ):
            return {"rejected": "objective_fingerprint_mismatch"}
        if self._cursor >= len(self._steps):
            self._exhausted = True
            return {"exhausted": True, "wake_upper": True}
        step = self._steps[self._cursor]
        self._attempts_for_step += 1
        if self._attempts_for_step >= self.maximum_attempts_per_step:
            self._cursor += 1
            self._attempts_for_step = 0
        return {"step": step, "cursor": self._cursor}

    @property
    def exhausted(self) -> bool:
        return self._exhausted or self._cursor >= len(self._steps)

    @property
    def current_step(self) -> str | None:
        if self._cursor < len(self._steps):
            return self._steps[self._cursor]
        return None
