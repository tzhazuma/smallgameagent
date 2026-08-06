"""IntentGate — deterministic safety checks before any game input is submitted.

Python port of gah intent-gate.mjs. The gate rejects intents that violate
freshness, risk, control-map, target, or waypoint rules. Rejection happens
before input is submitted; the orchestrator never executes a rejected intent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAXIMUM_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class IntentVerdict:
    allowed: bool
    reason: str = ""
    rule: str = ""


@dataclass
class IntentGate:
    maximum_risk: str = "medium"
    max_high_risk_experiments: int = 3
    high_risk_count: int = 0

    def _risk_ok(self, risk: str) -> bool:
        return MAXIMUM_RISK_ORDER.get(risk, 99) <= MAXIMUM_RISK_ORDER.get(self.maximum_risk, 2)

    def evaluate(
        self,
        *,
        intent: dict,
        resolved_option: dict,
        current_base: dict,
        world: dict | None = None,
        monitor: dict | None = None,
        allow_stale: bool = False,
    ) -> IntentVerdict:
        base = intent.get("base") or {}
        option_name = resolved_option.get("option", intent.get("option"))
        risk = resolved_option.get("risk", "medium")
        requires_target = resolved_option.get("requires_target", False)
        target_id = (intent.get("parameters") or {}).get("target_id")

        # 1. Version freshness
        if not allow_stale:
            for key in ("game_id", "run_id", "state_version", "scene_epoch"):
                if base.get(key) != current_base.get(key):
                    return IntentVerdict(False, f"stale base {key}", "version_freshness")

        # 2. Risk limit
        if not self._risk_ok(risk):
            return IntentVerdict(False, f"risk {risk} > {self.maximum_risk}", "risk_limit")

        # 3. High-risk budget
        if risk == "high":
            if self.high_risk_count >= self.max_high_risk_experiments:
                return IntentVerdict(False, "high-risk budget exhausted", "high_risk_budget")
            self.high_risk_count += 1

        # 4. Critical monitor quarantine
        if monitor and monitor.get("critical") and option_name not in (
            "verify_completion", "observe_settle", "recover_reverse"
        ):
            return IntentVerdict(False, "critical monitor quarantine", "critical_monitor")

        # 5. Completion quarantine
        if world and world.get("completion", {}).get("suspected") and option_name not in (
            "verify_completion", "observe_settle"
        ):
            return IntentVerdict(False, "completion quarantine", "completion_quarantine")

        # 6. Control-map verification for movement options
        control_map_verified = bool(world and world.get("control_map_verified"))
        if option_name in ("approach_target", "explore_sector_sweep") and not control_map_verified:
            return IntentVerdict(False, "control map unverified", "control_map_unverified")

        # 7. Target existence / active / navigable
        if requires_target:
            targets = (world or {}).get("targets") or []
            target = next((t for t in targets if t.get("id") == target_id), None)
            if target_id and target is None:
                return IntentVerdict(False, f"target {target_id} does not exist", "target_exists")
            if target and target.get("active") is False:
                return IntentVerdict(False, f"target {target_id} inactive", "target_active")
            if target and target.get("navigable") is False:
                return IntentVerdict(False, f"target {target_id} not navigable", "target_navigable")

        # 8. Waypoint constraints
        waypoints = (intent.get("parameters") or {}).get("waypoints") or []
        if len(waypoints) > 16:
            return IntentVerdict(False, "too many waypoints", "waypoint_bounded")
        for wp in waypoints:
            if wp.get("distance", 0) > 25:
                return IntentVerdict(False, f"waypoint too far {wp.get('distance')}", "waypoint_bounded")

        # 9. Repeated no-effect target (blocked by default unless reentry authorized)
        if world and world.get("confirmed_no_effect_targets"):
            if target_id in world["confirmed_no_effect_targets"] and not intent.get("interaction_reentry"):
                return IntentVerdict(
                    False, f"target {target_id} confirmed no-effect", "repeated_semantic_no_effect"
                )

        return IntentVerdict(True, "ok")

    def record_execution(self, resolved_option: dict) -> None:
        """Called by the orchestrator after a successful execution."""
        # high-risk count already incremented in evaluate; keep for symmetry.
        return None
