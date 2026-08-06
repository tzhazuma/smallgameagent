"""Phase strategy + StrategySpec state-machine interpreter.

Python port of gah phased-strategy + strategy-machine-runtime ideas:

- PhaseStrategy locks stage boundaries: a passed stage's policy may not be
  rewritten by later stages (solves temporal consistency).
- StrategySpec is a 1-16 state FSM authored by the L2 planner; the
  StrategyMachineRuntime interprets it deterministically (30+ predicates).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Predicates (mirrors gah STRATEGY_PREDICATES subset, extensible)
# ---------------------------------------------------------------------------

VALUELESS_PREDICATES = {
    "completion_suspected", "failure_active", "phase_changed_from_entry",
    "control_domain_changed_from_entry", "guide_changed_from_entry",
    "guide_absent", "guide_present", "objective_target_inactive",
    "objective_reached", "waypoint_reached", "target_relevant_progress",
    "always",
}
STRING_VALUE_PREDICATES = {"phase_is", "control_domain_is"}
NUMERIC_VALUE_PREDICATES = {
    "no_progress_at_least", "local_iterations_at_least",
    "cross_state_target_no_progress_at_least", "resource_counter_at_least",
    "resource_counter_at_most",
}
ALL_PREDICATES = VALUELESS_PREDICATES | STRING_VALUE_PREDICATES | NUMERIC_VALUE_PREDICATES


def _eval_predicate(predicate: str, key: Any, value: Any, ctx: dict) -> bool:
    """Evaluate one transition predicate against a context snapshot."""
    if predicate == "always":
        return True
    if predicate == "completion_suspected":
        return bool(ctx.get("completion_suspected"))
    if predicate == "failure_active":
        return bool(ctx.get("failure_active"))
    if predicate == "phase_is":
        return ctx.get("phase") == value
    if predicate == "phase_changed_from_entry":
        return ctx.get("phase") != ctx.get("entry_phase")
    if predicate == "control_domain_is":
        return ctx.get("control_domain") == value
    if predicate == "control_domain_changed_from_entry":
        return ctx.get("control_domain") != ctx.get("entry_control_domain")
    if predicate == "guide_changed_from_entry":
        return ctx.get("guide_id") != ctx.get("entry_guide_id")
    if predicate == "guide_absent":
        return not ctx.get("guide_id")
    if predicate == "guide_present":
        return bool(ctx.get("guide_id"))
    if predicate == "objective_target_inactive":
        return not bool(ctx.get("objective_active"))
    if predicate == "objective_reached":
        return bool(ctx.get("objective_reached"))
    if predicate == "waypoint_reached":
        return bool(ctx.get("waypoint_reached"))
    if predicate == "target_relevant_progress":
        return bool(ctx.get("target_relevant_progress"))
    if predicate == "no_progress_at_least":
        return int(ctx.get("no_progress_count", 0)) >= int(value or 1)
    if predicate == "local_iterations_at_least":
        return int(ctx.get("local_iterations", 0)) >= int(value or 1)
    if predicate == "cross_state_target_no_progress_at_least":
        return int(ctx.get("cross_state_no_progress", 0)) >= int(value or 1)
    if predicate == "resource_counter_at_least":
        return int(ctx.get("resources", {}).get(key, 0)) >= int(value or 1)
    if predicate == "resource_counter_at_most":
        return int(ctx.get("resources", {}).get(key, 0)) <= int(value or 0)
    if predicate == "resource_counter_increased_from_entry":
        return int(ctx.get("resources", {}).get(key, 0)) > int(ctx.get("entry_resources", {}).get(key, 0))
    return False


# ---------------------------------------------------------------------------
# StrategySpec
# ---------------------------------------------------------------------------


@dataclass
class StrategySpec:
    strategy_id: str
    base: dict
    entry_state: str
    states: list[dict]
    summary: str = ""
    global_replan_triggers: list[str] = field(default_factory=list)
    invariants: list[dict] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "schema_version": "agent_harness.strategy_spec.v1",
            "kind": "StrategySpec",
            "strategy_id": self.strategy_id,
            "base": self.base,
            "summary": self.summary,
            "entry_state": self.entry_state,
            "states": self.states,
            "global_replan_triggers": self.global_replan_triggers,
            "invariants": self.invariants,
            "evidence_refs": self.evidence_refs,
            "confidence": self.confidence,
        }


def parse_strategy_spec(data: dict) -> StrategySpec:
    return StrategySpec(
        strategy_id=data["strategy_id"],
        base=data.get("base", {}),
        entry_state=data["entry_state"],
        states=data["states"],
        summary=data.get("summary", ""),
        global_replan_triggers=data.get("global_replan_triggers", []),
        invariants=data.get("invariants", []),
        evidence_refs=data.get("evidence_refs", []),
        confidence=data.get("confidence", 0.5),
    )


def validate_strategy_spec(spec: StrategySpec) -> list[str]:
    """Return a list of contract violations (empty = valid)."""
    errors: list[str] = []
    ids = {s["state_id"] for s in spec.states}
    if spec.entry_state not in ids:
        errors.append(f"entry_state {spec.entry_state} not in states")
    for s in spec.states:
        for action in s.get("actions", []):
            if action.get("max_local_iterations") is not None and not (
                1 <= int(action["max_local_iterations"]) <= 20
            ):
                errors.append(f"{s['state_id']}.{action.get('action_id')}: max_local_iterations out of 1-20")
        for t in s.get("transitions", []):
            pred = t.get("predicate")
            if pred not in ALL_PREDICATES:
                errors.append(f"{s['state_id']}: unsupported predicate {pred}")
            nxt = t.get("next")
            if nxt not in ids and nxt not in ("REPLAN", "VERIFY_COMPLETION", "STOP"):
                errors.append(f"{s['state_id']}: bad transition target {nxt}")
    return errors


# ---------------------------------------------------------------------------
# StrategyMachineRuntime
# ---------------------------------------------------------------------------


@dataclass
class StrategyMachineRuntime:
    spec: StrategySpec
    _state_id: str = field(init=False)
    _local_iterations: dict = field(default_factory=dict)
    _no_progress_count: int = 0
    _entry_context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._state_id = self.spec.entry_state

    def state_id(self) -> str:
        return self._state_id

    def _current_state(self) -> dict:
        return next(s for s in self.spec.states if s["state_id"] == self._state_id)

    def next(self, ctx: dict) -> dict:
        """Return a decision: {kind: action, intent} | {kind: replan, reason}
        | {kind: verify} | {kind: stop}."""
        if not self._entry_context:
            self._entry_context = dict(ctx)

        # Global replan triggers
        for trigger in self.spec.global_replan_triggers:
            reason = self._check_global_trigger(trigger, ctx)
            if reason:
                return {"kind": "replan", "reason": reason}

        # Invariants
        for inv in self.spec.invariants:
            if _eval_predicate(inv.get("predicate"), inv.get("key"), inv.get("value"), ctx):
                return {"kind": "stop", "reason": f"invariant {inv.get('predicate')}"}

        # Transitions (ordered)
        state = self._current_state()
        for t in state.get("transitions", []):
            if _eval_predicate(t.get("predicate"), t.get("key"), t.get("value"), ctx):
                nxt = t.get("next")
                if nxt == "REPLAN":
                    return {"kind": "replan", "reason": f"transition {t.get('predicate')}"}
                if nxt == "VERIFY_COMPLETION":
                    return {"kind": "verify"}
                if nxt == "STOP":
                    return {"kind": "stop", "reason": f"transition {t.get('predicate')}"}
                if nxt in {s["state_id"] for s in self.spec.states}:
                    self._state_id = nxt
                    self._local_iterations[nxt] = 0
                    state = self._current_state()
                break

        # Materialize action from current state
        actions = state.get("actions", [])
        if not actions:
            return {"kind": "replan", "reason": "state has no actions"}
        action = actions[0]
        self._local_iterations[self._state_id] = self._local_iterations.get(self._state_id, 0) + 1
        intent = {
            "schema_version": "agent_harness.intent.v1",
            "kind": "Intent",
            "base": dict(self.spec.base),
            "option": action["option"],
            "parameters": dict(action.get("parameters", {})),
            "expected_effect": dict(action.get("expected_effect", {})),
            "preconditions": [],
            "abort_conditions": ["state_version_change"],
            "fallback": None,
            "confidence": self.spec.confidence,
            "evidence_refs": self.spec.evidence_refs,
        }
        return {"kind": "action", "intent": intent, "state_id": self._state_id}

    def _check_global_trigger(self, trigger: str, ctx: dict) -> str | None:
        if trigger == "phase_changed_from_entry" and ctx.get("phase") != self._entry_context.get("phase"):
            return "phase_changed_from_entry"
        if trigger == "control_domain_changed_from_entry" and ctx.get("control_domain") != self._entry_context.get("control_domain"):
            return "control_domain_changed_from_entry"
        if trigger == "guide_changed_from_entry" and ctx.get("guide_id") != self._entry_context.get("guide_id"):
            return "guide_changed_from_entry"
        if trigger == "completion_suspected" and ctx.get("completion_suspected"):
            return "completion_suspected"
        if trigger == "failure_active" and ctx.get("failure_active"):
            return "failure_active"
        if trigger == "repeated_no_progress" and int(ctx.get("no_progress_count", 0)) >= 4:
            return "repeated_no_progress"
        if trigger == "repeated_action_failure" and int(ctx.get("action_failure_count", 0)) >= 3:
            return "repeated_action_failure"
        if trigger == "objective_target_inactive" and not ctx.get("objective_active"):
            return "objective_target_inactive"
        return None


# ---------------------------------------------------------------------------
# PhaseStrategy (stage locking)
# ---------------------------------------------------------------------------


@dataclass
class PhaseStrategy:
    game_id: str
    strategy_id: str
    stages: list[dict] = field(default_factory=list)
    _passed_stage_ids: set = field(default_factory=set)

    def current_stage(self, phase: str) -> dict | None:
        """Find the active stage by phase name (or first unpassed stage)."""
        for stage in self.stages:
            if stage["stage_id"] in self._passed_stage_ids:
                continue
            if stage.get("phase") in (None, phase):
                return stage
        return None

    def mark_stage_passed(self, stage_id: str) -> None:
        self._passed_stage_ids.add(stage_id)

    def stage_locked(self, stage_id: str) -> bool:
        return stage_id in self._passed_stage_ids

    def locked_prefix_digest(self) -> str:
        """Hash of passed stages — used to forbid rewriting locked prefixes."""
        locked = [s for s in self.stages if s["stage_id"] in self._passed_stage_ids]
        raw = json.dumps(locked, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
