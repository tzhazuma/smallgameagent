"""Smoke tests for the fusion-harness core modules."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion import (  # noqa: E402
    FusionAgent, FusionConfig, IntentGate, OptionCatalog, RecoveryLadder,
    StrategyMachineRuntime, classify_execution_failure, parse_strategy_spec,
)


def test_option_compile_joystick() -> None:
    cat = OptionCatalog()
    resolved = cat.resolve({"option": "probe_joystick", "parameters": {"dx": 0.5, "dy": -0.3}})
    assert resolved["primitives"][0]["type"] == "move_pulse"
    with pytest.raises(ValueError):
        cat.resolve({"option": "probe_joystick", "parameters": {"dx": 0.1, "dy": 0.0}})


def test_intent_gate_freshness() -> None:
    gate = IntentGate()
    base = {"game_id": "g", "run_id": "r", "state_version": 1, "scene_epoch": 0}
    ok = gate.evaluate(
        intent={"option": "approach_target", "base": base, "parameters": {"target_id": "t1"}},
        resolved_option={"option": "approach_target", "risk": "medium", "requires_target": True},
        current_base=base,
        world={"targets": [{"id": "t1", "active": True, "navigable": True}], "control_map_verified": True},
    )
    assert ok.allowed
    stale = gate.evaluate(
        intent={"option": "approach_target", "base": {**base, "state_version": 9}, "parameters": {"target_id": "t1"}},
        resolved_option={"option": "approach_target", "risk": "medium", "requires_target": True},
        current_base=base,
        world={"control_map_verified": True},
    )
    assert not stale.allowed
    assert stale.rule == "version_freshness"


def test_failure_classification() -> None:
    f = classify_execution_failure(execution={"submitted_input": True})
    assert f is not None and f["code"] == "STUCK"
    assert classify_execution_failure(execution={"semantic_progress": True}) is None


def test_recovery_ladder() -> None:
    ladder = RecoveryLadder("STUCK")
    steps = [ladder.next()["step"] for _ in range(8)]
    assert steps[0] == "REFRESH_LOCAL_MAP"
    assert "REPLAN" in steps
    assert "REVERSE" in steps


def test_state_machine_transitions() -> None:
    spec = parse_strategy_spec({
        "strategy_id": "s1", "entry_state": "collect", "base": {"game_id": "g"},
        "states": [
            {"state_id": "collect",
             "actions": [{"option": "observe_settle", "parameters": {}, "expected_effect": {"state_settles": True}}],
             "transitions": [{"predicate": "objective_reached", "next": "dwell"}, {"predicate": "always", "next": "collect"}]},
            {"state_id": "dwell",
             "actions": [{"option": "dwell_at_target", "parameters": {"target_id": "t"}, "expected_effect": {}}],
             "transitions": [{"predicate": "always", "next": "REPLAN"}]},
        ],
        "global_replan_triggers": [], "invariants": [],
    })
    rt = StrategyMachineRuntime(spec)
    assert rt.next({"objective_reached": False})["kind"] == "action"
    assert rt.next({"objective_reached": True})["kind"] == "action"
    assert rt.state_id() == "dwell"


def test_fusion_agent_strategy_stable() -> None:
    def planner(_brief):
        return {
            "strategy_id": "s1", "entry_state": "collect", "base": {"game_id": "g1"},
            "states": [{
                "state_id": "collect",
                "actions": [{"option": "observe_settle", "parameters": {"duration_ms": 200},
                             "expected_effect": {"state_settles": True}}],
                "transitions": [{"predicate": "always", "next": "collect"}],
            }],
            "global_replan_triggers": [], "invariants": [],
        }

    agent = FusionAgent(FusionConfig(
        game_id="g1", max_steps=50, plan_strategy=planner,
        observe_world=lambda: {"progress_class": "none", "player": {"position": {"x": 0, "z": 0}},
                               "targets": [], "phase": "DISCOVER"},
    ))
    result = agent.run()
    assert result["terminal"] == "BUDGET_EXHAUSTED"
    assert result["planner_calls"] < 10  # strategy should be stable, not replan every step
