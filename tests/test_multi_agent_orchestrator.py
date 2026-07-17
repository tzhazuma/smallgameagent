"""Tests for the multi-agent orchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.context import AgentContext
from src.agent.multi_agent import AgentBus, MessageType, MultiAgentOrchestrator
from src.agent.roles.base import BaseAgentRole


class _MockRole(BaseAgentRole):
    def __init__(self, name: str, action: dict[str, Any] | None = None) -> None:
        self._name = name
        self._action = action

    @property
    def role_name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> list[str]:
        return []

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        return {"role": self._name}

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        if self._action is not None and self._name == "DecisionAnalyst":
            return {"action": self._action}
        return {"status": "ok"}

    async def act(self, ctx: AgentContext) -> None:
        if self._name == "DecisionAnalyst" and self._action is not None:
            ctx.final_action = self._action


class _VerifierApprove(BaseAgentRole):
    role_name = "Verifier"
    capabilities = []

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        return {}

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        return {"action_effective": True, "stuck": False, "recommendation": None}

    async def act(self, ctx: AgentContext) -> None:
        pass


@pytest.mark.asyncio
async def test_orchestrator_runs_pipeline():
    bus = AgentBus()
    action = {"action": "move", "params": {"dx": 0.5}, "reason": "test"}
    orchestrator = MultiAgentOrchestrator(
        bus=bus,
        observer=_MockRole("Observer"),
        state_mapper=_MockRole("StateMapper"),
        decision_analyst=_MockRole("DecisionAnalyst", action=action),
        verifier=_VerifierApprove(),
    )

    ctx = AgentContext()
    ctx.probe_state = {"ready": True}
    result = await orchestrator.step(ctx)

    assert result == action
    assert ctx.final_action == action
    assert bus.stats()["total_messages"] >= 4
    assert bus.last(MessageType.DECIDE) is not None


@pytest.mark.asyncio
async def test_orchestrator_redecide_on_stuck():
    bus = AgentBus()
    first = {"action": "move", "params": {"dx": 0.0}, "reason": "first"}
    second = {"action": "tap", "params": {"x": 100}, "reason": "second"}

    class _VerifierStuckThenApprove(BaseAgentRole):
        role_name = "Verifier"
        capabilities = []
        calls = 0

        async def observe(self, ctx: AgentContext) -> dict[str, Any]:
            return {}

        async def reason(self, ctx: AgentContext) -> dict[str, Any]:
            _VerifierStuckThenApprove.calls += 1
            if _VerifierStuckThenApprove.calls == 1:
                return {"action_effective": False, "stuck": True, "recommendation": "escape_rotate"}
            return {"action_effective": True, "stuck": False, "recommendation": None}

        async def act(self, ctx: AgentContext) -> None:
            pass

    class _ChangingAnalyst(BaseAgentRole):
        role_name = "DecisionAnalyst"
        capabilities = []
        idx = 0

        async def observe(self, ctx: AgentContext) -> dict[str, Any]:
            return {}

        async def reason(self, ctx: AgentContext) -> dict[str, Any]:
            acts = [first, second]
            return {"action": acts[_ChangingAnalyst.idx]}

        async def act(self, ctx: AgentContext) -> None:
            acts = [first, second]
            ctx.final_action = acts[_ChangingAnalyst.idx]
            _ChangingAnalyst.idx += 1

    orchestrator = MultiAgentOrchestrator(
        bus=bus,
        observer=_MockRole("Observer"),
        state_mapper=_MockRole("StateMapper"),
        decision_analyst=_ChangingAnalyst(),
        verifier=_VerifierStuckThenApprove(),
        critic=_MockRole("Critic"),
        max_rounds=2,
    )

    ctx = AgentContext()
    ctx.probe_state = {"ready": True}
    result = await orchestrator.step(ctx)

    assert result == second
    assert bus.last(MessageType.CRITIC) is not None
    assert _ChangingAnalyst.idx == 2
