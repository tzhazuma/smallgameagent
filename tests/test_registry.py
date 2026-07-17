"""Tests for the pluggable DecisionRegistry and built-in decision makers.

No real browsers or API calls — all external dependencies are mocked.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from src.agent.context import AgentContext
from src.agent.decision_makers.api_maker import APIDecisionMaker
from src.agent.decision_makers.multi_maker import MultiAgentDecisionMaker
from src.agent.decision_makers.rule_maker import RuleDecisionMaker
from src.agent.hybrid_agent import HybridAgent
from src.agent.registry import BaseDecisionMaker, DecisionRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_response(action: dict) -> mock.MagicMock:
    """Build a mock chat response returning *action* as JSON."""
    resp = mock.MagicMock()
    resp.choices = [
        mock.MagicMock(message=mock.MagicMock(content=json.dumps(action))),
    ]
    return resp


# 1x1 white PNG — valid bytes for PIL to open without error.
# Generated with: PIL.Image.new('RGB', (1,1), 'white').save(buf, 'PNG')
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_mock_client(action: dict | None = None) -> mock.MagicMock:
    """Return a mock OpenCodeGoClient with chat returning the given action."""
    client = mock.MagicMock()
    action = action or {
        "action": "move",
        "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
        "reason": "mock move",
    }
    client.chat.return_value = _make_chat_response(action)
    return client


# ---------------------------------------------------------------------------
# DecisionRegistry unit tests
# ---------------------------------------------------------------------------


class TestDecisionRegistry:
    """Tests for the registry class itself."""

    def test_register_decorator_stores_class(self) -> None:
        """@register stores the class under the given name."""
        # Use a unique name to avoid collisions with other tests
        test_name = "_test_registry_register"

        @DecisionRegistry.register(test_name)
        class _TestMaker(BaseDecisionMaker):
            async def decide(self, ctx: AgentContext) -> dict:  # type: ignore[override]
                return {"action": "wait", "params": {"duration_ms": 100}, "reason": "test"}

        assert test_name in DecisionRegistry._registry
        assert DecisionRegistry._registry[test_name] is _TestMaker

    def test_create_returns_instance(self) -> None:
        """create() returns a fresh instance of the registered class."""
        maker = DecisionRegistry.create("api")
        assert isinstance(maker, BaseDecisionMaker)
        assert isinstance(maker, APIDecisionMaker)

    def test_create_raises_value_error_for_unknown_mode(self) -> None:
        """create() raises ValueError for unregistered mode names."""
        with pytest.raises(ValueError, match="Unknown decision maker: nonexistent_mode"):
            DecisionRegistry.create("nonexistent_mode")

    def test_list_modes_returns_sorted_names(self) -> None:
        """list_modes() returns alphabetically sorted mode names."""
        modes = DecisionRegistry.list_modes()
        assert isinstance(modes, list)
        assert modes == sorted(modes)
        # At minimum the 3 required modes should be present
        assert "api" in modes
        assert "rule" in modes
        assert "multi" in modes

    def test_register_twice_overwrites(self) -> None:
        """Registering the same name twice overwrites the previous entry."""
        test_name = "_test_registry_overwrite"

        @DecisionRegistry.register(test_name)
        class _FirstMaker(BaseDecisionMaker):
            async def decide(self, ctx: AgentContext) -> dict:  # type: ignore[override]
                return {"action": "wait", "params": {}, "reason": "first"}

        @DecisionRegistry.register(test_name)
        class _SecondMaker(BaseDecisionMaker):
            async def decide(self, ctx: AgentContext) -> dict:  # type: ignore[override]
                return {"action": "move", "params": {}, "reason": "second"}

        maker = DecisionRegistry.create(test_name)
        assert isinstance(maker, _SecondMaker)
        assert not isinstance(maker, _FirstMaker)


# ---------------------------------------------------------------------------
# Decision maker unit tests
# ---------------------------------------------------------------------------


class TestAPIDecisionMaker:
    """Tests for the API (text LLM) decision maker."""

    @pytest.mark.asyncio
    async def test_produces_valid_action_dict(self) -> None:
        """APIDecisionMaker returns valid action dict via _think_text."""
        llm_agent = mock.AsyncMock()
        llm_agent._think_text = mock.AsyncMock(return_value={
            "action": "move",
            "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
            "reason": "api decision",
        })

        maker = APIDecisionMaker(llm_agent=llm_agent)
        ctx = AgentContext(probe_state={"ready": True, "keyNumbers": {"score": 10}})
        result = await maker.decide(ctx)

        assert result["action"] == "move"
        assert result["params"]["dx"] == 0.5
        assert "reason" in result
        # Verify _think_text was called with the probe state
        llm_agent._think_text.assert_awaited_once_with(
            ctx.probe_state, ctx=ctx,
        )

    @pytest.mark.asyncio
    async def test_fallback_when_no_llm(self) -> None:
        """APIDecisionMaker returns wait when llm_agent is None."""
        maker = APIDecisionMaker(llm_agent=None)
        ctx = AgentContext()
        result = await maker.decide(ctx)

        assert result["action"] == "wait"
        assert "no_api_client" in result.get("reason", "")


class TestRuleDecisionMaker:
    """Tests for the rule engine decision maker."""

    @pytest.mark.asyncio
    async def test_produces_valid_action_dict(self) -> None:
        """RuleDecisionMaker returns valid action dict via rule_engine.step."""
        rule_engine = mock.MagicMock()
        rule_engine.step.return_value = {
            "action": "move",
            "params": {"dx": 0, "dy": 1, "duration_ms": 320},
            "reason": "rule engine",
        }

        maker = RuleDecisionMaker(rule_engine=rule_engine)
        ctx = AgentContext(probe_state={"ready": True})
        result = await maker.decide(ctx)

        assert result["action"] == "move"
        assert result["params"]["dy"] == 1
        # step should be called with state and visual=None
        rule_engine.step.assert_called_once_with(ctx.probe_state, None)

    @pytest.mark.asyncio
    async def test_fallback_when_no_engine(self) -> None:
        """RuleDecisionMaker returns wait when rule_engine is None."""
        maker = RuleDecisionMaker(rule_engine=None)
        ctx = AgentContext()
        result = await maker.decide(ctx)

        assert result["action"] == "wait"
        assert "no_rule_engine" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_with_visual_analyzer(self) -> None:
        """RuleDecisionMaker passes visual analysis when screenshot + analyzer available."""
        rule_engine = mock.MagicMock()
        rule_engine.step.return_value = {
            "action": "move", "params": {}, "reason": "with_visual",
        }
        visual_analyzer = mock.MagicMock()
        visual_analyzer.analyze_pil.return_value = {"stick": {"dx": 0.5, "dy": 0.0}}

        maker = RuleDecisionMaker(
            rule_engine=rule_engine,
            visual_analyzer=visual_analyzer,
        )
        ctx = AgentContext(
            probe_state={"ready": True},
            screenshot=MINI_PNG,
        )
        result = await maker.decide(ctx)

        assert result["action"] == "move"
        visual_analyzer.analyze_pil.assert_called_once()
        # step receives the visual result
        args, _kwargs = rule_engine.step.call_args
        assert args[1] == {"stick": {"dx": 0.5, "dy": 0.0}}  # visual is passed through


class TestMultiAgentDecisionMaker:
    """Tests for the multi-agent placeholder decision maker."""

    @pytest.mark.asyncio
    async def test_returns_wait_action(self) -> None:
        """MultiAgentDecisionMaker returns wait with placeholder reason."""
        maker = MultiAgentDecisionMaker()
        ctx = AgentContext()
        result = await maker.decide(ctx)

        assert result["action"] == "wait"
        assert "multi" in result.get("reason", "")


# ---------------------------------------------------------------------------
# Integration: HybridAgent + DecisionRegistry
# ---------------------------------------------------------------------------


class TestHybridAgentIntegration:
    """Tests that HybridAgent correctly uses DecisionRegistry for known modes."""

    @pytest.mark.asyncio
    async def test_mode_api_uses_registry(self) -> None:
        """HybridAgent with mode='api' dispatches via registry."""
        client = _make_mock_client({
            "action": "move",
            "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
            "reason": "api via registry",
        })
        agent = HybridAgent(mode="api", api_client=client)
        ctx = AgentContext(probe_state={"ready": True})
        decision = await agent._decide(ctx)

        assert decision["action"] == "move"
        assert decision["reason"] == "api via registry"

    @pytest.mark.asyncio
    async def test_mode_rule_uses_registry(self) -> None:
        """HybridAgent with mode='rule' dispatches via registry."""
        agent = HybridAgent(mode="rule", game_id="SSD_00848P01")
        ctx = AgentContext(probe_state={"ready": True, "keyNumbers": {"score": 0}})
        decision = await agent._decide(ctx)

        assert decision["action"] in ("move", "wait", "tap")
        assert "reason" in decision

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_through_to_legacy(self) -> None:
        """Unregistered mode falls through to legacy dispatch.

        The ``_legacy_decide`` returns ``unknown_mode`` because
        ``_decide_vlm*`` methods are not mocked in this test — verifying
        the fallback chain itself works.
        """
        agent = HybridAgent(mode="vlm-struct", api_client=_make_mock_client())
        ctx = AgentContext(probe_state={"ready": True})
        decision = await agent._decide(ctx)

        # Falls through to legacy: _decide_vlm_struct needs vlm_engine,
        # so returns "no_vlm_or_api" wait action
        assert decision["action"] == "wait"
