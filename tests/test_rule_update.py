"""Tests for src.agent.rule_update."""

from __future__ import annotations

from unittest import mock

import pytest

from src.agent.rule_update import (
    RuleParameters,
    RuleUpdateApplier,
    RuleUpdateRequest,
    RuleUpdateTrigger,
    parse_update_response,
    update_prompt,
)


class TestRuleParameters:
    def test_defaults(self) -> None:
        p = RuleParameters({"upgrade_threshold": 100})
        assert p.get("upgrade_threshold") == 100
        assert p.get("missing", "x") == "x"

    def test_update(self) -> None:
        p = RuleParameters()
        p.update({"a": 1, "b": 2})
        assert p.to_dict() == {"a": 1, "b": 2}


class TestRuleUpdateRequest:
    def test_round_trip(self) -> None:
        req = RuleUpdateRequest(
            update_type="param",
            target="rules.upgrade_threshold",
            reason="save before upgrade",
            payload={"upgrade_threshold": 500},
            confidence=0.85,
        )
        data = req.to_dict()
        restored = RuleUpdateRequest.from_dict(data)
        assert restored.update_type == "param"
        assert restored.target == "rules.upgrade_threshold"
        assert restored.payload == {"upgrade_threshold": 500}
        assert restored.confidence == pytest.approx(0.85)


class TestRuleUpdateTrigger:
    def test_low_composite_trigger(self) -> None:
        trigger = RuleUpdateTrigger(composite_threshold=0.2, composite_window=3)
        ctx = mock.Mock()
        ctx.step_number = 5
        ctx.working_memory = {"last_composite": 0.1}
        # First two checks below window
        assert trigger.check(ctx) is None
        ctx.step_number = 6
        assert trigger.check(ctx) is None
        ctx.step_number = 7
        reason = trigger.check(ctx)
        assert reason is not None
        assert "low_composite" in reason

    def test_stall_trigger(self) -> None:
        trigger = RuleUpdateTrigger(stall_threshold=3)
        ctx = mock.Mock()
        ctx.step_number = 1
        ctx.working_memory = {"stall_streak": 1}
        assert trigger.check(ctx) is None
        ctx.step_number = 2
        ctx.working_memory = {"stall_streak": 2}
        assert trigger.check(ctx) is None
        ctx.step_number = 3
        ctx.working_memory = {"stall_streak": 3}
        reason = trigger.check(ctx)
        assert reason is not None
        assert "stall" in reason

    def test_no_trigger_when_healthy(self) -> None:
        trigger = RuleUpdateTrigger(composite_threshold=0.2, composite_window=3)
        ctx = mock.Mock()
        ctx.step_number = 1
        ctx.working_memory = {"last_composite": 0.3, "stall_streak": 0}
        assert trigger.check(ctx) is None


class TestRuleUpdateApplier:
    def test_apply_param(self) -> None:
        params = RuleParameters()
        applier = RuleUpdateApplier(params)
        req = RuleUpdateRequest(
            update_type="param",
            target="upgrade_threshold",
            reason="save money",
            payload={"upgrade_threshold": 500},
            confidence=0.9,
        )
        assert applier.apply(req) is True
        assert params.get("upgrade_threshold") == 500

    def test_low_confidence_skipped(self) -> None:
        params = RuleParameters()
        applier = RuleUpdateApplier(params)
        req = RuleUpdateRequest(
            update_type="param",
            target="x",
            reason="x",
            payload={"x": 1},
            confidence=0.3,
        )
        assert applier.apply(req) is False
        assert params.get("x") is None

    def test_apply_memory_entry(self) -> None:
        memory = mock.Mock()
        params = RuleParameters()
        applier = RuleUpdateApplier(params, memory)
        req = RuleUpdateRequest(
            update_type="memory_entry",
            target="00461",
            reason="save then upgrade works",
            payload={
                "game_id": "00461",
                "phase_id": "early",
                "pattern": {"action": "wait_for_money"},
                "success": True,
                "notes": "good",
            },
            confidence=0.8,
        )
        assert applier.apply(req) is True
        memory.record.assert_called_once()

    def test_apply_phase_contract(self) -> None:
        params = RuleParameters()
        applier = RuleUpdateApplier(params)
        req = RuleUpdateRequest(
            update_type="phase_contract",
            target="early",
            reason="define early game",
            payload={"precondition": "money<100"},
            confidence=0.8,
        )
        assert applier.apply(req) is True
        assert params.get("phase_contract:early") == {"precondition": "money<100"}


class TestParseUpdateResponse:
    def test_parse_json(self) -> None:
        text = '{"update_type": "param", "target": "x", "reason": "r", "payload": {"a": 1}, "confidence": 0.8}'
        req = parse_update_response(text)
        assert req is not None
        assert req.update_type == "param"
        assert req.payload == {"a": 1}

    def test_parse_markdown_fenced(self) -> None:
        text = '```json\n{"update_type": "param", "target": "x", "reason": "r", "payload": {"a": 1}, "confidence": 0.8}\n```'
        req = parse_update_response(text)
        assert req is not None
        assert req.confidence == pytest.approx(0.8)

    def test_parse_invalid_returns_none(self) -> None:
        assert parse_update_response("not json") is None


class TestUpdatePrompt:
    def test_prompt_structure(self) -> None:
        messages = update_prompt("stall", {"money": 100}, {"upgrade_threshold": 200})
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "stall" in messages[1]["content"]
