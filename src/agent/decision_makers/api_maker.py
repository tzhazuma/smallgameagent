"""Decision maker that delegates to the text LLM (DeepSeek)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.registry import BaseDecisionMaker, DecisionRegistry

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.llm_agent import LLMAgent


@DecisionRegistry.register("api")
class APIDecisionMaker(BaseDecisionMaker):
    """Decision maker that uses the text LLM to decide actions.

    Delegates to ``LLMAgent._think_text()`` with the current probe state.
    Falls back to a ``wait`` action when no LLM agent is available.

    Parameters
    ----------
    llm_agent:
        Optional :class:`LLMAgent` instance.
    """

    def __init__(self, llm_agent: LLMAgent | None = None, **kwargs: Any) -> None:
        self._llm_agent = llm_agent

    async def decide(self, ctx: AgentContext) -> dict[str, Any]:
        if self._llm_agent is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_api_client"}
        return await self._llm_agent._think_text(ctx.probe_state, ctx=ctx)
