"""DecisionAnalyst role — synthesizes observations into actionable decisions.

From the PPT's 6-agent design: the DecisionAnalyst consumes observations from
state mapping and produces a concrete action decision, bridging perception
(StateMapper) and execution (Verifier).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext


class DecisionAnalyst(BaseAgentRole):
    """Synthesizes observations into action decisions.

    This role sits between StateMapper and Verifier in the 6-agent pipeline.
    It evaluates the game state, visual structure, working memory, and
    procedural rules to produce a concrete action to execute.
    """

    role_name = "DecisionAnalyst"
    capabilities = ["action-synthesis", "rule-evaluation", "strategy-selection"]

    def __init__(
        self,
        rule_engine: Any = None,
        procedural_memory: Any = None,
        llm_agent: Any = None,
    ) -> None:
        self._rule_engine = rule_engine
        self._procedural = procedural_memory
        self._llm_agent = llm_agent

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Gather decision inputs from context."""
        return {
            "has_visual_struct": ctx.visual_struct is not None,
            "has_probe_state": bool(ctx.probe_state),
            "stuck": getattr(ctx.working_memory, 'is_stuck', False) if ctx.working_memory else False,
            "step_count": ctx.step_number,
        }

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """Evaluate available decision sources and select action.

        Precedence: procedural memory → rule engine → API LLM → fallback wait.
        """
        action: dict[str, Any] | None = None
        source = "none"

        # 1. ProceduralMemory rule match
        if self._procedural is not None:
            try:
                matched = self._procedural.match(ctx, ctx.working_memory)
                if matched is not None:
                    action = {
                        "action": matched.action_template.get("action", "wait"),
                        "params": matched.action_template.get("params", {"duration_ms": 500}),
                        "reason": f"procedural:{matched.name}",
                    }
                    source = "procedural_memory"
            except Exception:
                pass

        # 2. Rule engine (local, zero-latency)
        if action is None and self._rule_engine is not None:
            try:
                action = self._rule_engine.step(ctx.probe_state, ctx.visual_struct)
                source = "rule_engine"
            except Exception:
                pass

        # 3. API LLM (DeepSeek, high-latency)
        if action is None and self._llm_agent is not None:
            try:
                action = await self._llm_agent._think_text(ctx.probe_state, ctx=ctx)
                source = "api_llm"
            except Exception:
                pass

        # 4. Fallback
        if action is None:
            action = {
                "action": "wait",
                "params": {"duration_ms": 500},
                "reason": "decision_fallback",
            }
            source = "fallback"

        return {"action": action, "decision_source": source}

    async def act(self, ctx: AgentContext) -> None:
        """Write final action to context."""
        reasoning = await self.reason(ctx)
        ctx.final_action = reasoning["action"]
        ctx.metadata["decision_source"] = reasoning["decision_source"]
