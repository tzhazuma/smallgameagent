"""Tests for RuleEngine obstacle learning/avoidance and world-model integration.

Covers the P1 additions to ``src/engine/rules.py``:

- obstacle point recording from expected-vs-actual displacement mismatches,
- potential-field repulsion steering (biased but never 180° flipped),
- world-model staleness triggering a local target re-plan,
- stuck-escape direction scoring away from obstacle-dense regions,
- HybridAgent wiring of the VersionedWorldModel.

All tests are pure logic — no browser, no network.
"""

from __future__ import annotations

import math

import pytest

from src.agent.hybrid_agent import HybridAgent
from src.agent.world_model import VersionedWorldModel
from src.engine.rules import (
    BLOCK_MIN_EXPECTED,
    OBSTACLE_LOOKAHEAD,
    RuleEngine,
)

GAME_ID = "SSD_00461P01"


def _state(
    x: float,
    z: float,
    candidates: list[tuple[str, float, float]] | None = None,
) -> dict:
    """Build a minimal probe-like state with a player position and candidates."""
    cands = [
        {"path": f"root/{name}", "name": name, "worldPosition": {"x": cx, "z": cz}}
        for name, cx, cz in (candidates if candidates is not None else [("UnlockItem_3", 10.0, 0.0)])
    ]
    return {
        "ready": True,
        "player": {"worldPosition": {"x": x, "z": z}},
        "guide_or_target_candidates": cands,
        "keyNumbers": {},
        "keyFlags": {},
    }


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine(GAME_ID)


# ------------------------------------------------------------ obstacle learning


def test_blocked_moves_record_obstacle_point(engine: RuleEngine) -> None:
    """Two consecutive blocked moves in the same direction record one obstacle."""
    state = _state(0.0, 0.0)
    a1 = engine.step(state)
    assert a1["action"] == "move"
    assert engine._learned_obstacles == []

    # Player did not move at all: actual displacement 0 << expected.
    engine.step(state)
    assert engine._learned_obstacles == []  # first blocked move only arms the streak

    engine.step(state)
    assert len(engine._learned_obstacles) == 1
    obs = engine._learned_obstacles[0]
    # Obstacle is recorded ahead of the blocked direction (+X toward the target).
    assert obs["x"] == pytest.approx(OBSTACLE_LOOKAHEAD, abs=0.2)
    assert obs["z"] == pytest.approx(0.0, abs=0.2)
    assert obs["count"] == 1
    assert obs["step"] == engine.step_count


def test_successful_move_does_not_record_obstacle(engine: RuleEngine) -> None:
    state = _state(0.0, 0.0)
    engine.step(state)
    # Move freely toward the target: actual displacement matches expectation.
    state2 = _state(5.0, 0.0)
    engine.step(state2)
    state3 = _state(9.0, 0.0)
    engine.step(state3)
    assert engine._learned_obstacles == []
    assert engine._block_dir_streak == 0


def test_single_blocked_move_then_recovery_learns_nothing(engine: RuleEngine) -> None:
    engine.step(_state(0.0, 0.0))
    engine.step(_state(0.0, 0.0))  # blocked once — streak armed
    assert engine._block_dir_streak == 1
    engine.step(_state(5.0, 0.0))  # recovered — streak resets, no obstacle
    assert engine._learned_obstacles == []
    assert engine._block_dir_streak == 0


def test_expected_below_noise_floor_is_ignored(engine: RuleEngine) -> None:
    """Moves whose expected displacement is below the noise floor never learn."""
    # White-box: drive the verification path directly with an expected
    # displacement below BLOCK_MIN_EXPECTED — the pulse table never produces
    # one this small via engine.step().
    for _ in range(4):
        engine._prev_move = {
            "pos": (0.0, 0.0),
            "dir": (1.0, 0.0),
            "expected": BLOCK_MIN_EXPECTED / 2,
            "step": engine.step_count,
        }
        engine._learn_from_last_move((0.0, 0.0))  # zero actual displacement
    assert engine._learned_obstacles == []
    assert engine._block_dir_streak == 0
    assert engine._speed_est > 0
    assert BLOCK_MIN_EXPECTED > 0


def test_repeated_blocking_merges_into_one_obstacle(engine: RuleEngine) -> None:
    state = _state(0.0, 0.0)
    for _ in range(6):
        engine.step(state)  # stuck_escape kicks in at streak >= 5, still blocked
    assert len(engine._learned_obstacles) >= 1
    # All blocked intents cluster near the player — merges keep the list small.
    assert len(engine._learned_obstacles) <= 3
    total_conf = sum(o["count"] for o in engine._learned_obstacles)
    assert total_conf >= 2


# ---------------------------------------------------------- repulsion steering


def test_repulsion_biases_direction_without_flipping(engine: RuleEngine) -> None:
    """An obstacle offset from the target line deflects but never reverses."""
    engine._learned_obstacles.append(
        {"x": 1.0, "z": 0.5, "step": 0, "count": 1, "dir": (1.0, 0.0)}
    )
    # Player at origin, target straight down +X at 5m.
    sx, sz = engine._steer_around_obstacles(0.0, 0.0, 5.0, 0.0, 5.0)
    # Magnitude preserved (pulse timing unaffected).
    assert math.hypot(sx, sz) == pytest.approx(5.0, abs=1e-6)
    # Deflected away from the obstacle (which sits at +Z side).
    assert sz < 0
    # Not flipped 180°: still a positive component along the target direction.
    assert sx > 0


def test_repulsion_ignores_far_obstacles(engine: RuleEngine) -> None:
    engine._learned_obstacles.append(
        {"x": 100.0, "z": 0.0, "step": 0, "count": 3, "dir": (1.0, 0.0)}
    )
    sx, sz = engine._steer_around_obstacles(0.0, 0.0, 5.0, 0.0, 5.0)
    assert (sx, sz) == (5.0, 0.0)


def test_repulsion_weight_grows_with_confidence(engine: RuleEngine) -> None:
    low = {"x": 1.0, "z": 0.5, "step": 0, "count": 1, "dir": (1.0, 0.0)}
    high = {"x": 1.0, "z": 0.5, "step": 0, "count": 3, "dir": (1.0, 0.0)}
    engine._learned_obstacles = [low]
    _, sz_low = engine._steer_around_obstacles(0.0, 0.0, 5.0, 0.0, 5.0)
    engine._learned_obstacles = [high]
    _, sz_high = engine._steer_around_obstacles(0.0, 0.0, 5.0, 0.0, 5.0)
    assert sz_high < sz_low < 0


# --------------------------------------------------------------- stale replan


def test_scene_shift_marks_target_plan_stale_and_replans(engine: RuleEngine) -> None:
    wm = VersionedWorldModel()
    engine.world_model = wm

    # Baseline topology + first engine step registers the follow target.
    wm.write_observation({"guide_or_target_candidates": ["UnlockItem_3"]}, step=0)
    engine.step(_state(0.0, 0.0))
    assert engine._current_plan_id == "follow_target"
    plan = wm.get_plan("follow_target")
    assert plan.kind == "target"
    assert plan.depends_on == {"UnlockItem_3"}
    assert not wm.is_stale("follow_target")

    # Scene shift: the candidate set changes (as at baseline step 121).
    report = wm.write_observation(
        {"guide_or_target_candidates": ["UnlockItem_3", "UnlockItem_4"]}, step=1
    )
    assert report.bumped_epochs.get("scene") == 1
    assert "follow_target" in report.stale_plans
    assert wm.is_stale("follow_target")

    # Next engine step performs the local re-plan: motion state cleared,
    # target re-selected, plan re-registered fresh under the new epoch.
    engine.stuck_streak = 3
    engine._block_dir_streak = 1
    engine.step(_state(0.0, 0.0, candidates=[
        ("UnlockItem_3", 10.0, 0.0),
        ("UnlockItem_4", 10.0, 2.0),
    ]))
    assert engine.stale_replans == 1
    assert engine._block_dir_streak == 0
    assert engine._current_plan_id == "follow_target"
    assert not wm.is_stale("follow_target")
    fresh = wm.get_plan("follow_target")
    assert fresh.scene_epoch_at_creation == wm.scene_epoch
    assert wm.stats()["stale_events"] == 1


def test_stale_check_noop_without_world_model(engine: RuleEngine) -> None:
    engine._check_plan_stale()  # must not raise
    engine.step(_state(0.0, 0.0))
    assert engine._current_plan_id is None
    assert engine.stale_replans == 0


# ------------------------------------------------------------- escape scoring


def test_escape_direction_avoids_obstacle_cluster(engine: RuleEngine) -> None:
    # Dense cluster to the east of the player.
    engine._learned_obstacles = [
        {"x": 1.0, "z": 0.2, "step": 0, "count": 3, "dir": (1.0, 0.0)},
        {"x": 0.8, "z": -0.3, "step": 0, "count": 2, "dir": (1.0, 0.0)},
        {"x": 1.5, "z": 0.0, "step": 0, "count": 1, "dir": (1.0, 0.0)},
    ]
    ux, uz = engine._escape_direction(0.0, 0.0)
    assert math.hypot(ux, uz) == pytest.approx(1.0, abs=1e-6)
    # Best of the 8 candidate directions must point away from the cluster.
    assert ux < 0


def test_escape_direction_random_unit_vector_without_obstacles(engine: RuleEngine) -> None:
    ux, uz = engine._escape_direction(0.0, 0.0)
    assert math.hypot(ux, uz) == pytest.approx(1.0, abs=1e-6)


def test_stuck_escape_action_uses_scored_direction(engine: RuleEngine) -> None:
    # Obstacle east; walk the engine into the stuck state (5 frozen steps).
    engine._learned_obstacles = [
        {"x": 1.0, "z": 0.0, "step": 0, "count": 2, "dir": (1.0, 0.0)},
    ]
    state = _state(0.0, 0.0)
    action = None
    for _ in range(6):
        action = engine.step(state)
    assert action["reason"].startswith("stuck_escape")
    assert action["action"] == "move"
    # 00461 basis: stick +X → world +X. Escape must not push into the obstacle.
    assert action["params"]["dx"] < 0.5


# --------------------------------------------------------- HybridAgent wiring


def test_hybrid_agent_attaches_world_model_to_rule_engine() -> None:
    agent = HybridAgent(mode="rule", game_id=GAME_ID)
    assert agent._world_model is not None
    assert agent._rule_engine.world_model is agent._world_model


def test_hybrid_agent_wm_observe_normalises_candidates() -> None:
    agent = HybridAgent(mode="rule", game_id=GAME_ID)
    wm = agent._world_model

    class _Ctx:
        metadata: dict = {}

    state = _state(0.0, 0.0, candidates=[("UnlockItem_3", 10.0, 0.0)])
    agent._wm_observe(state, 0, _Ctx())
    assert wm.stats()["entity_count"] >= 1

    # Scene shift through the dict-shaped probe payload bumps the scene epoch.
    state2 = _state(0.0, 0.0, candidates=[("UnlockItem_4", 8.0, 1.0)])
    agent._wm_observe(state2, 1, _Ctx())
    assert wm.scene_epoch == 1
    assert _Ctx.metadata["wm_report"]["bumped_epochs"]["scene"] == 1
