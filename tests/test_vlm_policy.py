"""Unit tests for the adaptive VLM call policy (src/agent/vlm_policy.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent.vlm_policy import ESCALATION_TASKS, HIGH_ACCEPTANCE_TASKS, VlmCallPolicy


def test_progressing_skips_vlm() -> None:
    policy = VlmCallPolicy()
    for step in range(1, 8):
        policy.observe_step(step, gameplay_advanced=(step % 2 == 1))
    # 4 advances in window >= min_progress_steps(3) -> skip.
    assert policy.should_call_vlm(10) is False


def test_stall_triggers_vlm() -> None:
    policy = VlmCallPolicy(stall_threshold=4)
    for step in range(1, 5):
        policy.observe_step(step, gameplay_advanced=False)
    assert policy.should_call_vlm(5) is True


def test_low_probe_info_triggers_vlm() -> None:
    policy = VlmCallPolicy()
    policy.observe_step(1, True)
    policy.observe_step(2, False)
    assert policy.should_call_vlm(3, probe_info_sufficiency=0.3) is True


def test_cap_limits_calls() -> None:
    policy = VlmCallPolicy(max_calls_per_run=2, cooldown_steps=0, stall_threshold=1)
    for step in range(20):
        policy.observe_step(step, gameplay_advanced=False)
        if policy.should_call_vlm(step + 1):
            policy.record_call(step + 1)
    assert policy._calls_this_run == 2


def test_cooldown_blocks_back_to_back() -> None:
    policy = VlmCallPolicy(cooldown_steps=8, stall_threshold=1)
    policy.observe_step(1, False)
    assert policy.should_call_vlm(2) is True
    policy.record_call(2)
    policy.observe_step(3, False)
    assert policy.should_call_vlm(4) is False  # within cooldown


def test_high_acceptance_tasks_first() -> None:
    policy = VlmCallPolicy()
    tasks = policy.tasks_for_call()
    assert tasks == list(HIGH_ACCEPTANCE_TASKS)
    # After 3 calls, escalation tasks appear.
    policy._calls_this_run = 3
    escalated = policy.tasks_for_call()
    assert any(t in ESCALATION_TASKS for t in escalated)


def test_reset_run() -> None:
    policy = VlmCallPolicy()
    policy.observe_step(1, False)
    policy.record_call(2)
    assert policy._calls_this_run == 1
    policy.reset_run()
    assert policy._calls_this_run == 0
    assert policy._last_call_step == -100
    assert policy._recent_gameplay == []
    assert policy._escalated is False
