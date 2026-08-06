"""Strategy registry — candidate -> evaluated -> known-pass / superseded.

Python port of gah strategy-registry.mjs. Promotion requires fresh-start
passes, no navigation escape, no protected-prefix regression.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_FRESH_START_PASSES = 3


@dataclass
class StrategyRecord:
    policy_id: str
    game_id: str
    status: str  # candidate | evaluated | known-pass | superseded
    phases: list = field(default_factory=list)
    verified_options: list = field(default_factory=list)
    invariants: list = field(default_factory=list)
    evaluations: list = field(default_factory=list)  # per-run reports
    fresh_start_passes: int = 0
    superseded_by: str | None = None


class StrategyRegistry:
    def __init__(self, store_path: Path | None = None) -> None:
        self._records: dict[str, StrategyRecord] = {}
        self._store_path = store_path
        if store_path and store_path.exists():
            self._load()

    def _load(self) -> None:
        for line in self._store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            self._records[data["policy_id"]] = StrategyRecord(**data)

    def _save(self, record: StrategyRecord) -> None:
        if self._store_path:
            with open(self._store_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")

    def create_candidate(self, *, policy_id: str, game_id: str,
                         phases: list | None = None,
                         verified_options: list | None = None,
                         invariants: list | None = None) -> StrategyRecord:
        record = StrategyRecord(
            policy_id=policy_id, game_id=game_id, status="candidate",
            phases=phases or [], verified_options=verified_options or [],
            invariants=invariants or [],
        )
        self._records[policy_id] = record
        self._save(record)
        return record

    def record_evaluation(self, policy_id: str, report: dict) -> None:
        record = self._records.get(policy_id)
        if not record:
            return
        record.evaluations.append(report)
        if record.status == "candidate":
            record.status = "evaluated"
        if report.get("fresh_start") and report.get("settled_complete"):
            record.fresh_start_passes += 1
        self._save(record)

    def promotion_verdict(self, policy_id: str) -> dict:
        record = self._records.get(policy_id)
        if not record:
            return {"eligible": False, "reason": "unknown policy"}
        checks = {
            "fresh_start_passes": record.fresh_start_passes >= REQUIRED_FRESH_START_PASSES,
            "no_navigation_escape": all(
                not r.get("navigation_escapes", 0) for r in record.evaluations
            ),
            "no_protected_prefix_regression": all(
                not r.get("protected_prefix_regression", False) for r in record.evaluations
            ),
            "critical_monitor_within_limit": all(
                not r.get("critical_monitor_failures", 0) for r in record.evaluations
            ),
        }
        eligible = all(checks.values())
        return {"eligible": eligible, "checks": checks}

    def promote(self, policy_id: str) -> bool:
        verdict = self.promotion_verdict(policy_id)
        if not verdict["eligible"]:
            return False
        record = self._records[policy_id]
        # Demote current champion
        for other in self._records.values():
            if other.status == "known-pass" and other.policy_id != policy_id:
                other.status = "superseded"
                other.superseded_by = policy_id
        record.status = "known-pass"
        self._save(record)
        return True

    def champion(self, game_id: str) -> StrategyRecord | None:
        for record in self._records.values():
            if record.game_id == game_id and record.status == "known-pass":
                return record
        return None
