"""Unit tests for src/agent/probe_budget.py."""

from __future__ import annotations

import pytest

from src.agent.probe_budget import (
    DEFAULT_TRIGGER_LEVELS,
    AdaptiveLogger,
    EscalationTrigger,
    ProbeBudgetConfig,
    ProbeBudgetManager,
    ProbeLevel,
    TriggerContext,
    evaluate_triggers,
)

L0, L1, L2 = ProbeLevel.L0_STATE, ProbeLevel.L1_COMPONENTS, ProbeLevel.L2_SCREENSHOT


def obs(numbers=None, flags=None, candidates=None, phase=None):
    return {
        "keyNumbers": numbers or {},
        "keyFlags": flags or {},
        "candidates": candidates or [],
        "phase": phase,
    }


# ---------------------------------------------------------------------------
# Trigger evaluation: each of the five triggers, hit and boundary miss.
# ---------------------------------------------------------------------------


class TestEvaluateTriggers:
    def test_phase_change_candidates(self):
        ctx = TriggerContext(prev_obs=obs(candidates=["a"]), curr_obs=obs(candidates=["a", "b"]))
        assert EscalationTrigger.PHASE_CHANGE in evaluate_triggers(ctx)

    def test_phase_change_phase_flag(self):
        ctx = TriggerContext(prev_obs=obs(phase="build"), curr_obs=obs(phase="combat"))
        assert EscalationTrigger.PHASE_CHANGE in evaluate_triggers(ctx)

    def test_phase_change_not_fired_when_identical(self):
        ctx = TriggerContext(
            prev_obs=obs(candidates=["a"], phase="x"), curr_obs=obs(candidates=["a"], phase="x")
        )
        assert EscalationTrigger.PHASE_CHANGE not in evaluate_triggers(ctx)

    def test_low_confidence_boundary(self):
        low = evaluate_triggers(TriggerContext(confidence=0.39))
        high = evaluate_triggers(TriggerContext(confidence=0.41))
        at = evaluate_triggers(TriggerContext(confidence=0.4))
        assert EscalationTrigger.LOW_CONFIDENCE in low
        assert EscalationTrigger.LOW_CONFIDENCE not in high
        assert EscalationTrigger.LOW_CONFIDENCE not in at  # strictly below

    def test_action_no_effect_boundary(self):
        hit = evaluate_triggers(TriggerContext(consecutive_no_effect=2))
        miss = evaluate_triggers(TriggerContext(consecutive_no_effect=1))
        assert EscalationTrigger.ACTION_NO_EFFECT in hit
        assert EscalationTrigger.ACTION_NO_EFFECT not in miss

    def test_collision_hint_boundary(self):
        hit = evaluate_triggers(
            TriggerContext(expected_displacement=100.0, actual_displacement=29.0)
        )
        miss = evaluate_triggers(
            TriggerContext(expected_displacement=100.0, actual_displacement=31.0)
        )
        assert EscalationTrigger.COLLISION_HINT in hit
        assert EscalationTrigger.COLLISION_HINT not in miss

    def test_collision_hint_not_fired_without_expected(self):
        ctx = TriggerContext(expected_displacement=0.0, actual_displacement=0.0)
        assert EscalationTrigger.COLLISION_HINT not in evaluate_triggers(ctx)

    def test_semantic_flip(self):
        ctx = TriggerContext(
            prev_obs=obs(flags={"autoFishing": False}),
            curr_obs=obs(flags={"autoFishing": True}),
        )
        assert EscalationTrigger.SEMANTIC_FLIP in evaluate_triggers(ctx)

    def test_semantic_flip_ignores_non_bool_and_missing_keys(self):
        ctx = TriggerContext(
            prev_obs=obs(flags={"n": 1}),
            curr_obs=obs(flags={"n": 2, "new": True}),
        )
        assert EscalationTrigger.SEMANTIC_FLIP not in evaluate_triggers(ctx)

    def test_empty_context_fires_nothing(self):
        assert evaluate_triggers(TriggerContext()) == set()


# ---------------------------------------------------------------------------
# ProbeBudgetManager: mapping, cooldown, budget cap, decorative-node scenario.
# ---------------------------------------------------------------------------


class TestDecideLevel:
    def test_default_is_l0(self):
        mgr = ProbeBudgetManager()
        assert mgr.decide_level(0, TriggerContext()) is L0

    def test_trigger_level_mapping(self):
        mgr = ProbeBudgetManager()
        assert mgr.decide_level(0, TriggerContext(consecutive_no_effect=5)) is L1
        mgr = ProbeBudgetManager()
        assert mgr.decide_level(0, TriggerContext(confidence=0.1)) is L2

    def test_cooldown_holds_level_then_falls_back(self):
        cfg = ProbeBudgetConfig(cooldown_steps=3)
        mgr = ProbeBudgetManager(cfg)
        assert mgr.decide_level(0, TriggerContext(confidence=0.1)) is L2
        # Held for the next 3 steps even with no triggers.
        for step in (1, 2, 3):
            assert mgr.decide_level(step, TriggerContext()) is L2
        # Window over: back to L0.
        assert mgr.decide_level(4, TriggerContext()) is L0

    def test_cooldown_does_not_downgrade_new_lower_trigger(self):
        cfg = ProbeBudgetConfig(cooldown_steps=3)
        mgr = ProbeBudgetManager(cfg)
        assert mgr.decide_level(0, TriggerContext(confidence=0.1)) is L2
        # An L1 trigger during an active L2 hold must not lower the level.
        assert mgr.decide_level(1, TriggerContext(consecutive_no_effect=9)) is L2

    def test_accepts_duck_typed_mapping(self):
        mgr = ProbeBudgetManager()
        # Arbitrary world-model style dict, including unknown keys.
        ctx = {"confidence": 0.1, "unrelated_field": object(), "consecutive_no_effect": 0}
        assert mgr.decide_level(0, ctx) is L2
        assert mgr.decide_level(99, {"anything": 1}) is L0
        assert mgr.decide_level(100, None) is L0


class TestL2Budget:
    def test_cap_and_high_priority_preference(self):
        cfg = ProbeBudgetConfig(window_steps=100, l2_max_ratio=0.2, cooldown_steps=0)
        mgr = ProbeBudgetManager(cfg)
        # 15 low-priority L2 requests (LOW_CONFIDENCE).
        low_results = [mgr.decide_level(i, TriggerContext(confidence=0.1)) for i in range(15)]
        # 15 high-priority L2 requests (SEMANTIC_FLIP).
        flip = TriggerContext(prev_obs=obs(flags={"f": False}), curr_obs=obs(flags={"f": True}))
        high_results = [mgr.decide_level(15 + i, flip) for i in range(15)]

        stats = mgr.stats()
        # At most 20% of 100 steps may be L2.
        assert stats["level_counts"]["L2_SCREENSHOT"] <= 20
        # Every high-priority request was granted L2.
        assert all(r is L2 for r in high_results)
        # All 15 low-priority requests were granted at decision time (budget
        # was not yet full), but 10 were later evicted by high-priority
        # requests and retroactively counted as suppressed.
        assert all(r is L2 for r in low_results)
        assert stats["level_counts"]["L2_SCREENSHOT"] == 20
        assert stats["suppressed"] == 10

    def test_over_cap_low_priority_downgraded_to_l1(self):
        cfg = ProbeBudgetConfig(window_steps=10, l2_max_ratio=0.2, cooldown_steps=0)
        mgr = ProbeBudgetManager(cfg)  # cap = 2
        results = [mgr.decide_level(i, TriggerContext(confidence=0.1)) for i in range(5)]
        assert results[:2] == [L2, L2]
        assert results[2:] == [L1, L1, L1]
        assert mgr.stats()["suppressed"] == 3

    def test_budget_resets_next_window(self):
        cfg = ProbeBudgetConfig(window_steps=10, l2_max_ratio=0.2, cooldown_steps=0)
        mgr = ProbeBudgetManager(cfg)
        for i in range(10):
            mgr.decide_level(i, TriggerContext(confidence=0.1))
        assert mgr.decide_level(10, TriggerContext(confidence=0.1)) is L2


class TestDecorativeNodesScenario:
    def test_unchanged_obs_stay_l0_and_save_over_80_percent(self):
        mgr = ProbeBudgetManager()
        frame = obs(numbers={"gold": 100}, flags={"decor": True}, candidates=["c1"], phase="idle")
        for step in range(50):
            level = mgr.decide_level(
                step, TriggerContext(prev_obs=frame, curr_obs=frame, confidence=0.9)
            )
            assert level is L0
            mgr.record_observation(level, latency_ms=73.0, bytes_written=128)
        stats = mgr.stats()
        assert stats["level_counts"]["L0_STATE"] == 50
        assert stats["suppressed"] == 0
        assert stats["estimated_savings_ratio"] > 0.8
        assert stats["avg_observed_latency_ms"] == pytest.approx(73.0)
        assert stats["total_bytes_written"] == 50 * 128
        assert stats["trigger_hits"] == {t.name: 0 for t in EscalationTrigger}


class TestStats:
    def test_trigger_hit_distribution_and_shares(self):
        cfg = ProbeBudgetConfig(cooldown_steps=0)
        mgr = ProbeBudgetManager(cfg)
        mgr.decide_level(0, TriggerContext(confidence=0.1))  # LOW_CONFIDENCE -> L2
        mgr.decide_level(1, TriggerContext(consecutive_no_effect=3))  # ACTION_NO_EFFECT -> L1
        mgr.decide_level(2, TriggerContext())  # L0
        stats = mgr.stats()
        assert stats["decisions"] == 3
        assert stats["trigger_hits"]["LOW_CONFIDENCE"] == 1
        assert stats["trigger_hits"]["ACTION_NO_EFFECT"] == 1
        assert stats["level_share"]["L0_STATE"] == pytest.approx(1 / 3)
        assert stats["estimated_cost_ms"] == 400.0 + 150.0 + 73.0
        assert stats["baseline_all_l2_ms"] == 3 * 400.0

    def test_default_mapping_matches_spec(self):
        assert DEFAULT_TRIGGER_LEVELS[EscalationTrigger.COLLISION_HINT] is L1
        assert DEFAULT_TRIGGER_LEVELS[EscalationTrigger.ACTION_NO_EFFECT] is L1
        assert DEFAULT_TRIGGER_LEVELS[EscalationTrigger.PHASE_CHANGE] is L2
        assert DEFAULT_TRIGGER_LEVELS[EscalationTrigger.LOW_CONFIDENCE] is L2
        assert DEFAULT_TRIGGER_LEVELS[EscalationTrigger.SEMANTIC_FLIP] is L2


# ---------------------------------------------------------------------------
# AdaptiveLogger
# ---------------------------------------------------------------------------


class TestAdaptiveLogger:
    def test_debug_not_persisted_by_default(self):
        log = AdaptiveLogger()
        log.log(0, "DEBUG", "tick")
        log.log(1, "INFO", "info-msg")
        assert [r.level for r in log.persisted] == ["INFO"]
        assert log.stats()["buffered_count"] == 1

    def test_debug_persisted_inside_escalation_window(self):
        log = AdaptiveLogger()
        log.log(0, "INFO", "anchor")
        log.escalate(steps=2)
        log.log(1, "DEBUG", "in-window")
        log.log(2, "DEBUG", "still-in-window")
        persisted_levels = [r.level for r in log.persisted]
        assert persisted_levels == ["INFO", "DEBUG", "DEBUG"]
        # Window ended after step 2: DEBUG is buffered only again.
        log.log(3, "DEBUG", "after-window")
        assert [r.level for r in log.persisted].count("DEBUG") == 2
        assert log.stats()["buffered_count"] == 3
        assert log.stats()["escalation_windows"] == 1

    def test_trace_dump_always_persisted(self):
        log = AdaptiveLogger()
        log.log(0, "TRACE_DUMP", "dump", payload={"screenshot": "frame0.png"})
        assert len(log.persisted) == 1
        assert log.persisted[0].payload == {"screenshot": "frame0.png"}

    def test_ring_buffer_drops_oldest(self):
        log = AdaptiveLogger(capacity=3)
        for i in range(5):
            log.log(i, "DEBUG", f"m{i}")
        assert [r.message for r in log.buffer] == ["m2", "m3", "m4"]

    def test_stats_bytes_and_counts(self):
        log = AdaptiveLogger()
        log.log(0, "INFO", "abcd")  # 4 bytes
        log.log(1, "INFO", "ef", payload="xy")  # 2 + 2 bytes
        log.log(2, "DEBUG", "ignored")
        stats = log.stats()
        assert stats["persisted_count"] == 2
        assert stats["persisted_bytes"] == 4 + 4
        assert stats["buffered_count"] == 1
        assert stats["avg_bytes_per_record"] == pytest.approx(4.0)
        assert stats["escalation_windows"] == 0

    def test_unknown_level_rejected(self):
        log = AdaptiveLogger()
        with pytest.raises(ValueError, match="unknown log level"):
            log.log(0, "VERBOSE", "nope")
