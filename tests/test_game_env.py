"""Tests for src/experiments/game_env.py — verifiers-style rubrics."""

from __future__ import annotations

import pytest

from src.experiments.game_env import score_experiment_json, score_trajectory


def _step(i: int, x: float, z: float, action: str = "move", fail: int = 0) -> dict:
    return {
        "i": i,
        "action": action,
        "reason": "follow_guide",
        "player": {"x": x, "z": z},
        "keyNumbers": {"_failCount": fail},
    }


class TestScoreTrajectory:
    def test_empty(self) -> None:
        assert score_trajectory([]).composite == 0

    def test_perfect_run(self) -> None:
        steps = [_step(i, float(i), 0.0) for i in range(50)]
        score = score_trajectory(
            steps,
            result={"completed": True, "win": True, "steps": 50},
            candidate_transitions=[{"step": 10}, {"step": 20}, {"step": 30}, {"step": 40}],
            world_model_stats={"stale_events": 0, "capability_flips": 0},
        )
        assert score.completion == 1.0
        assert score.progress_ratio == 1.0
        assert score.activity == pytest.approx(1.0)
        assert score.consistency == pytest.approx(1.0)
        assert score.composite == pytest.approx(1.0)

    def test_frozen_run_low_activity(self) -> None:
        # 100 steps: 10 moving, then frozen at (5, 5)
        steps = [_step(i, float(i), 0.0) for i in range(10)]
        steps += [_step(10 + j, 5.0, 5.0) for j in range(90)]
        score = score_trajectory(steps, result={"completed": False, "win": False, "steps": 100})
        assert score.completion == 0.0
        assert score.activity < 0.15
        assert score.details["stall_steps"] >= 88

    def test_fail_flips_hurt_consistency(self) -> None:
        steps = [_step(i, float(i % 3), 0.0, fail=i % 2) for i in range(100)]
        score = score_trajectory(steps, result={"steps": 100})
        # failCount toggles every step -> 99 flips -> consistency floored at 0
        assert score.consistency == 0.0
        assert score.details["fail_flips"] == 99

    def test_composite_weights(self) -> None:
        steps = [_step(i, float(i), 0.0) for i in range(20)]
        score = score_trajectory(steps, result={"completed": False, "win": False})
        # completion=0, progress=0, activity=1, consistency=1
        assert score.composite == pytest.approx(0.15 + 0.15)


class TestScoreExperimentJson:
    def test_baseline_scores(self) -> None:
        score = score_experiment_json("experiment_baseline_rule_00461.json")
        d = score.to_dict()
        assert d["details"]["steps"] == 300
        assert d["completion"] == 0.0
        assert 0.0 <= d["activity"] <= 1.0
        assert d["details"]["transitions"] == 8

    def test_p1_scores(self) -> None:
        score = score_experiment_json("experiment_p1_wm_00461.json")
        d = score.to_dict()
        assert d["details"]["steps"] == 300
        assert d["details"]["transitions"] == 7
        assert d["details"]["fail_flips"] == 1

    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            score_experiment_json("/nonexistent.json")
