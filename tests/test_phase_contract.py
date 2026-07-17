"""Tests for src/agent/phase_contract.py — temporal-consistency mechanisms."""

from __future__ import annotations

import pytest

from src.agent.phase_contract import (
    CLOCK_STEP,
    ConsistencyViolation,
    FailureAction,
    FailureContext,
    FieldConsistencyChecker,
    PhaseContract,
    PhaseEvent,
    PhaseGate,
    PhaseStatus,
    PrefixViolation,
    TimestampedValue,
    align_tolerance,
    classify_failure,
    compute_prefix_hash,
    evaluate_predicate,
    verify_prefix,
)

# ---------------------------------------------------------------------------
# Cross-field consistency: the temperature case from real runs
# ---------------------------------------------------------------------------


class TestFieldConsistency:
    def test_temperature_case_131_of_190(self) -> None:
        """190 frames where 131 have decision.temp vs delta.temp deviation >0.05."""
        checker = FieldConsistencyChecker()
        checker.register_group(
            "temp", ["decision.temp", "delta.temp"], tolerance=0.05, unit="ratio"
        )
        for i in range(190):
            if i < 131:
                obs = {"decision": {"temp": 0.73}, "delta": {"temp": 0.36}}
            else:
                obs = {"decision": {"temp": 0.50}, "delta": {"temp": 0.52}}
            checker.check_frame(obs, step=i)
        assert checker.violation_count == 131
        v = checker.violations[0]
        assert v.group == "temp"
        assert v.deviation == pytest.approx(0.37)
        assert v.values == {"decision.temp": 0.73, "delta.temp": 0.36}

    def test_tolerance_boundary_not_violated(self) -> None:
        checker = FieldConsistencyChecker()
        checker.register_group("temp", ["a.t", "b.t"], tolerance=0.05)
        checker.check_frame({"a": {"t": 1.0}, "b": {"t": 0.96}}, step=0)
        assert checker.violation_count == 0
        # just above the tolerance -> violation
        checker.check_frame({"a": {"t": 1.0}, "b": {"t": 0.5}}, step=1)
        assert checker.violation_count == 1

    def test_bool_group_flip(self) -> None:
        checker = FieldConsistencyChecker()
        checker.register_group("auto", ["a.f", "b.f"], tolerance=0)
        checker.check_frame({"a": {"f": True}, "b": {"f": False}}, step=3)
        assert checker.violation_count == 1
        assert checker.violations[0].deviation == 1.0

    def test_missing_paths_skipped(self) -> None:
        checker = FieldConsistencyChecker()
        checker.register_group("temp", ["a.t", "b.t"], tolerance=0.05)
        checker.check_frame({"a": {"t": 1.0}}, step=0)  # only one path resolvable
        assert checker.violation_count == 0


# ---------------------------------------------------------------------------
# Three-layer time alignment
# ---------------------------------------------------------------------------


class TestTimestampedValue:
    def test_adjacent_frame_plunge_stale_and_align(self) -> None:
        """73% -> 36% between adjacent frames: values at different event times
        must not align within a sub-step tolerance."""
        before = TimestampedValue(
            value=0.73, event_time=10, observed_at=10, source="obs", unit="ratio"
        )
        after = TimestampedValue(
            value=0.36, event_time=11, observed_at=11, source="obs", unit="ratio"
        )
        assert not before.aligns_with(after, tolerance=0.5)
        assert after.aligns_with(before, tolerance=1.0)
        # staleness judged on event_time
        assert after.is_stale(max_age=5, now=20)
        assert not after.is_stale(max_age=5, now=15)

    def test_unit_mismatch_not_comparable(self) -> None:
        a = TimestampedValue(value=1, event_time=0, observed_at=0, unit="ratio")
        b = TimestampedValue(value=1, event_time=0, observed_at=0, unit="count")
        assert not align_tolerance(a, b, tolerance=10)

    def test_clock_mismatch_raises(self) -> None:
        a = TimestampedValue(value=1, event_time=0, observed_at=0, clock=CLOCK_STEP)
        b = TimestampedValue(
            value=1, event_time=0.0, observed_at=0.0, clock="monotonic"
        )
        with pytest.raises(ValueError, match="clock mismatch"):
            align_tolerance(a, b, tolerance=1)

    def test_step_clock_requires_int(self) -> None:
        with pytest.raises(TypeError):
            TimestampedValue(value=1, event_time=0.5, observed_at=0, clock=CLOCK_STEP)

    def test_roundtrip(self) -> None:
        v = TimestampedValue(
            value=0.73, event_time=1, observed_at=2, settled_at=4, source="probe", unit="ratio"
        )
        assert TimestampedValue.from_dict(v.to_dict()) == v


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------


class TestPredicate:
    def test_ops(self) -> None:
        obs = {"keyFlags": {"hasNet": True}, "keyNumbers": {"gold": 120}}
        assert evaluate_predicate({"path": "keyFlags.hasNet", "op": "truthy"}, obs)
        assert evaluate_predicate({"path": "keyNumbers.gold", "op": "ge", "value": 100}, obs)
        assert not evaluate_predicate({"path": "keyNumbers.gold", "op": "lt", "value": 100}, obs)
        assert evaluate_predicate({"path": "keyNumbers.gold", "op": "ne", "value": 0}, obs)

    def test_missing_path_false(self) -> None:
        assert not evaluate_predicate({"path": "a.b.c", "op": "truthy"}, {})

    def test_unknown_op_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown predicate op"):
            evaluate_predicate({"path": "a", "op": "xor"}, {"a": 1})


# ---------------------------------------------------------------------------
# PhaseGate lifecycle
# ---------------------------------------------------------------------------


def _contract(**overrides) -> PhaseContract:
    base = {
        "phase_id": "gather",
        "preconditions": [{"path": "obs.temp", "op": "le", "value": 0.8}],
        "allowed_actions": ["move", "collect"],
        "success_predicate": [{"path": "obs.water", "op": "ge", "value": 5}],
        "timeout_steps": 10,
        "on_failure": "rollback",
    }
    base.update(overrides)
    return PhaseContract(**base)


class TestPhaseGate:
    def test_entry_rejected_lists_reasons(self) -> None:
        gate = PhaseGate()
        reasons = gate.enter(_contract(), {"obs": {"temp": 0.9}}, step=0)
        assert len(reasons) == 1
        assert gate.status == PhaseStatus.IDLE

    def test_full_flow_with_settle_recheck(self) -> None:
        gate = PhaseGate(settle_delay_steps=2)
        contract = _contract()
        assert gate.enter(contract, {"obs": {"temp": 0.5}}, step=0) == []
        assert gate.status == PhaseStatus.RUNNING

        # action whitelist
        assert gate.check_action_allowed("move")
        assert not gate.check_action_allowed("upgrade")
        assert gate.blocked_actions == 1

        # success first observed — still RUNNING (pending settle)
        assert gate.tick({"obs": {"temp": 0.5, "water": 5}}, step=1) == PhaseStatus.RUNNING
        assert gate.pending_success_step == 1
        # settle recheck fails (water dropped) — back to RUNNING, pending cleared
        assert gate.tick({"obs": {"temp": 0.5, "water": 4}}, step=2) == PhaseStatus.RUNNING
        assert gate.pending_success_step is None
        # success again, holds through the settle window
        assert gate.tick({"obs": {"temp": 0.5, "water": 6}}, step=3) == PhaseStatus.RUNNING
        assert gate.tick({"obs": {"temp": 0.5, "water": 6}}, step=5) == PhaseStatus.SUCCESS
        # terminal: further ticks are no-ops
        assert gate.tick({"obs": {"temp": 0.9}}, step=6) == PhaseStatus.SUCCESS
        # event trail records the migrations
        transitions = [(e.from_status, e.to_status) for e in gate.events]
        assert ("idle", "running") in transitions
        assert ("running", "success") in transitions

    def test_zero_settle_accepts_immediately(self) -> None:
        gate = PhaseGate(settle_delay_steps=0)
        gate.enter(_contract(), {"obs": {"temp": 0.5}}, step=0)
        assert gate.tick({"obs": {"temp": 0.5, "water": 9}}, step=1) == PhaseStatus.SUCCESS

    def test_guard_breach_violated(self) -> None:
        gate = PhaseGate()
        gate.enter(_contract(), {"obs": {"temp": 0.5}}, step=0)
        assert gate.tick({"obs": {"temp": 0.85}}, step=1) == PhaseStatus.VIOLATED
        assert "guard" in gate.events[-1].reason

    def test_timeout(self) -> None:
        gate = PhaseGate()
        gate.enter(_contract(), {"obs": {"temp": 0.5}}, step=0)
        assert gate.tick({"obs": {"temp": 0.5, "water": 1}}, step=10) == PhaseStatus.TIMEOUT

    def test_tick_before_enter_raises(self) -> None:
        with pytest.raises(RuntimeError):
            PhaseGate().tick({}, step=0)


# ---------------------------------------------------------------------------
# Protected prefix
# ---------------------------------------------------------------------------


class TestPrefixProtection:
    STEPS = [{"op": "goto", "x": 1}, {"op": "collect"}, {"op": "return"}]

    def test_tampered_prefix_raises(self) -> None:
        h = compute_prefix_hash(self.STEPS, prefix_len=2)
        tampered = [dict(self.STEPS[0]), {"op": "collect", "extra": 1}, *self.STEPS[2:]]
        with pytest.raises(PrefixViolation) as exc:
            verify_prefix(tampered, h, prefix_len=2)
        assert exc.value.expected_hash == h
        assert exc.value.prefix_len == 2

    def test_append_beyond_prefix_ok(self) -> None:
        h = compute_prefix_hash(self.STEPS, prefix_len=2)
        extended = [*self.STEPS, {"op": "upgrade"}]
        assert verify_prefix(extended, h, prefix_len=2)

    def test_full_sequence_default(self) -> None:
        h = compute_prefix_hash(self.STEPS)
        assert verify_prefix(list(self.STEPS), h)
        with pytest.raises(PrefixViolation):
            verify_prefix([*self.STEPS, {"op": "extra"}], h)


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class TestFailureClassification:
    def test_no_side_effects_rollback(self) -> None:
        assert classify_failure(FailureContext()) == FailureAction.ROLLBACK

    def test_partial_side_effects_compensation(self) -> None:
        ctx = FailureContext(side_effects=["net_cast", "bait_consumed"])
        assert classify_failure(ctx) == FailureAction.COMPENSATION

    def test_world_advanced_stop_replan(self) -> None:
        ctx = FailureContext(side_effects=["upgrade_bought"], world_advanced=True)
        assert classify_failure(ctx) == FailureAction.STOP_REPLAN


# ---------------------------------------------------------------------------
# Serialization roundtrips
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_contract_roundtrip(self) -> None:
        c = _contract(protected_prefix_hash="abc123")
        assert PhaseContract.from_dict(c.to_dict()) == c

    def test_violation_roundtrip(self) -> None:
        v = ConsistencyViolation(
            group="temp", step=3, values={"a": 1.0, "b": 0.5},
            deviation=0.5, tolerance=0.05, unit="ratio",
        )
        assert ConsistencyViolation.from_dict(v.to_dict()) == v

    def test_event_roundtrip(self) -> None:
        e = PhaseEvent(phase_id="p", step=1, from_status="idle",
                       to_status="running", reason="entered phase")
        assert PhaseEvent.from_dict(e.to_dict()) == e

    def test_failure_context_roundtrip(self) -> None:
        ctx = FailureContext(side_effects=["x"], world_advanced=True)
        assert FailureContext.from_dict(ctx.to_dict()) == ctx

    def test_contract_invalid_on_failure(self) -> None:
        with pytest.raises(ValueError, match="on_failure"):
            PhaseContract(phase_id="p", on_failure="explode")
