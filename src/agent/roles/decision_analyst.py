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
        strategy_memory: Any = None,
    ) -> None:
        self._rule_engine = rule_engine
        self._procedural = procedural_memory
        self._llm_agent = llm_agent
        self._strategy_memory = strategy_memory

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

        Precedence: procedural memory → strategy memory → rule engine → API LLM → fallback wait.
        A diversity guard prevents strategy_memory from trapping the agent in a
        same-action loop within a single session (e.g. repeated move on
        SSD_00483P01): when memory recommends repeating a move and the rule
        engine suggests a different action type, the rule engine wins.
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

        # 2. StrategyMemory readback — use high-success-rate patterns, but exclude
        #    entries recorded in the current run to avoid online self-reinforcement.
        if action is None and self._strategy_memory is not None:
            try:
                game_id = ctx.metadata.get("game_id", "unknown")
                phase = self._strategy_memory.phase_id(ctx.probe_state)
                patterns = self._strategy_memory.lookup(
                    game_id, phase, top_k=1, min_attempts=2,
                    exclude_session_id=ctx.metadata.get("run_id"),
                )
                if patterns:
                    pat = patterns[0]
                    attempts = pat.get("attempts", 1)
                    successes = pat.get("successes", 0)
                    rate = successes / max(1, attempts)
                    if rate >= 0.6:
                        stored = pat.get("pattern", {})
                        action = {
                            "action": stored.get("action", "wait"),
                            "params": stored.get("params", {"duration_ms": 500}),
                            "reason": f"strategy_memory:{rate:.2f}",
                        }
                        source = "strategy_memory"
                        ctx.metadata.setdefault("memory_hits", 0)
                        ctx.metadata["memory_hits"] += 1
            except Exception:
                pass

        # 2b. Diversity guard: break same-action loops if memory somehow still
        #     recommends repeating move while the rule engine wants something else.
        if action is not None and self._rule_engine is not None and ctx.working_memory is not None:
            try:
                recent = ctx.working_memory.recent_actions(3)
                recent_types = [r.action.get("action") for r in recent if getattr(r, "action", None)]
                memory_type = action.get("action")
                if (
                    memory_type == "move"
                    and len(recent_types) >= 2
                    and all(t == "move" for t in recent_types)
                ):
                    rule_action = self._rule_engine.step(ctx.probe_state, ctx.visual_struct)
                    if rule_action and rule_action.get("action") not in ("move", None):
                        action = rule_action
                        source = "rule_engine_diversity_override"
            except Exception:
                pass

        # 3. Rule engine (local, zero-latency)
        if action is None and self._rule_engine is not None:
            try:
                action = self._rule_engine.step(ctx.probe_state, ctx.visual_struct)
                source = "rule_engine"
            except Exception:
                pass

        # 4. API LLM (DeepSeek, high-latency)
        if action is None and self._llm_agent is not None:
            try:
                action = await self._llm_agent._think_text(ctx.probe_state, ctx=ctx)
                source = "api_llm"
            except Exception:
                pass

        # 5. Fallback
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
