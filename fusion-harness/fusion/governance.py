"""Multi-game validation governance (Python port, simplified).

Groups games into canary / frozen_unseen / stable_regression and schedules
validation runs with memory limits. Mirrors gah multi-game-validation.mjs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

VALIDATION_GROUPS = ("canary", "frozen_unseen", "stable_regression")
VARIANT_TYPES = (
    "stable_replay", "backend_only", "backend_codex",
    "backend_vlm_codex", "candidate_exploration", "vlm_only_shadow",
)


@dataclass
class ValidationJob:
    game_id: str
    group: str
    variant: str
    expected_policy_digest: str | None = None


class MultiGameValidator:
    def __init__(self, memory_mb_per_game: int = 900,
                 total_memory_mb: int = 8_000) -> None:
        self._jobs: list[ValidationJob] = []
        self._memory_mb_per_game = memory_mb_per_game
        self._total_memory_mb = total_memory_mb

    def add_game(self, game_id: str, group: str, variant: str,
                 expected_policy_digest: str | None = None) -> None:
        assert group in VALIDATION_GROUPS, f"bad group {group}"
        assert variant in VARIANT_TYPES, f"bad variant {variant}"
        self._jobs.append(ValidationJob(game_id, group, variant, expected_policy_digest))

    def schedule(self) -> list[dict]:
        """Deterministic first-fit schedule respecting memory limit."""
        # Priority: canary first, then frozen_unseen, then regression.
        ordered = sorted(
            self._jobs,
            key=lambda j: (
                VALIDATION_GROUPS.index(j.group),
                j.variant != "backend_vlm_codex",  # full pipeline earlier
            ),
        )
        batches: list[list[ValidationJob]] = []
        current: list[ValidationJob] = []
        used_mb = 0
        for job in ordered:
            if used_mb + self._memory_mb_per_game > self._total_memory_mb:
                batches.append(current)
                current = []
                used_mb = 0
            current.append(job)
            used_mb += self._memory_mb_per_game
        if current:
            batches.append(current)
        return [
            {"batch": i, "games": [j.game_id for j in batch]}
            for i, batch in enumerate(batches)
        ]

    def validate_report(self, run_report: dict, job: ValidationJob) -> dict:
        """Check a single run against governance invariants."""
        checks = {
            "game_identity": run_report.get("game_id") == job.game_id,
            "fresh_start": bool(run_report.get("fresh_start")),
            "settled_complete": bool(run_report.get("settled_complete")),
            "navigation_safe": not bool(run_report.get("navigation_escapes")),
        }
        if job.expected_policy_digest:
            checks["exact_policy"] = run_report.get("policy_digest") == job.expected_policy_digest
        return {
            "job": job.game_id,
            "passed": all(checks.values()),
            "checks": checks,
            "report": run_report,
        }
