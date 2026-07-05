"""Multi-agent decision maker that runs the 3-role pipeline.

At session start: MemoryCurator loads cross-session experience.
Each game step: StateMapper (extract visual structure) → Decision (rule or API) → Verifier (validate).
At session end: MemoryCurator persists results and extracts knowledge.

This demonstrates API↔VLM communication: the StateMapper can use VLM
for visual perception, while the decision layer uses the API (DeepSeek)
for reasoning, coordinated through the shared AgentContext.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.registry import BaseDecisionMaker, DecisionRegistry

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.llm_agent import LLMAgent
    from src.agent.memory import EpisodicMemory, ProceduralMemory, SemanticMemory
    from src.engine.rules import RuleEngine


@DecisionRegistry.register("multi")
class MultiAgentDecisionMaker(BaseDecisionMaker):
    """3-role multi-agent pipeline for game playing.

    Parameters
    ----------
    llm_agent:
        LLMAgent instance for text-based reasoning (API).
    rule_engine:
        RuleEngine instance for rule-based decisions (local).
    episodic_memory:
        EpisodicMemory for session persistence.
    semantic_memory:
        SemanticMemory for knowledge retrieval.
    procedural_memory:
        ProceduralMemory for rule matching.
    """

    def __init__(
        self,
        llm_agent: LLMAgent | None = None,
        rule_engine: RuleEngine | None = None,
        episodic_memory: EpisodicMemory | None = None,
        semantic_memory: SemanticMemory | None = None,
        procedural_memory: ProceduralMemory | None = None,
        **kwargs: Any,
    ) -> None:
        self._llm_agent = llm_agent
        self._rule_engine = rule_engine
        self._episodic = episodic_memory
        self._semantic = semantic_memory
        self._procedural = procedural_memory
        self._session_started = False

    # ------------------------------------------------------------------
    # Main decision entry point
    # ------------------------------------------------------------------

    async def decide(self, ctx: AgentContext) -> dict[str, Any]:
        """Run one step of the multi-agent pipeline.

        1. MemoryCurator (session start only): load cross-session context
        2. StateMapper: extract visual structure from screenshot
        3. Decision: rule engine (local) or API LLM reasoning
        4. Verifier: validate action, detect stuck state

        Parameters
        ----------
        ctx:
            Shared agent context with probe_state, screenshot, metadata.

        Returns
        -------
        Action dict with ``action``, ``params``, ``reason`` keys.
        """
        # --- Session start: load memories once ---
        if not self._session_started and self._episodic is not None:
            await self._on_session_start(ctx)
            self._session_started = True

        # --- Step 1: StateMapper (VLM perception) ---
        await self._run_state_mapper(ctx)

        # --- Step 2: Decision (rule engine or API reasoning) ---
        decision = await self._run_decision(ctx)

        # --- Step 3: Verifier (action validation) ---
        await self._run_verifier(ctx)

        return decision

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _on_session_start(self, ctx: AgentContext) -> None:
        """Load cross-session experience via MemoryCurator."""
        ctx.metadata["session_phase"] = "start"
        from src.agent.roles.memory_curator import MemoryCurator

        curator = MemoryCurator(
            episodic_memory=self._episodic,
            semantic_memory=self._semantic,
            procedural_memory=self._procedural,
        )
        await curator.act(ctx)

    async def on_session_end(self, ctx: AgentContext) -> None:
        """Persist session results and extract knowledge."""
        ctx.metadata["session_phase"] = "end"
        from src.agent.roles.memory_curator import MemoryCurator

        curator = MemoryCurator(
            episodic_memory=self._episodic,
            semantic_memory=self._semantic,
            procedural_memory=self._procedural,
        )
        await curator.act(ctx)

    # ------------------------------------------------------------------
    # Per-step pipeline
    # ------------------------------------------------------------------

    async def _run_state_mapper(self, ctx: AgentContext) -> None:
        """Extract visual structure from screenshot (VLM or probe-based)."""
        from src.agent.roles.state_mapper import StateMapper

        mapper = StateMapper(semantic_memory=self._semantic)
        await mapper.act(ctx)

    async def _run_decision(self, ctx: AgentContext) -> dict[str, Any]:
        """Produce action using rule engine or text LLM.

        Precedence: rule engine → API LLM → procedural memory → fallback wait.
        """
        # 1. Try ProceduralMemory for matched rule
        if self._procedural is not None:
            try:
                matched = self._procedural.match(ctx, ctx.working_memory)
                if matched is not None:
                    return {
                        "action": matched.action_template.get("action", "wait"),
                        "params": matched.action_template.get("params", {"duration_ms": 500}),
                        "reason": f"procedural_rule:{matched.name}",
                    }
            except Exception:
                pass

        # 2. Try RuleEngine (local)
        if self._rule_engine is not None:
            try:
                return self._rule_engine.step(ctx.probe_state, ctx.visual_struct)
            except Exception:
                pass

        # 3. Try API LLM (DeepSeek via OpenCodeGo)
        if self._llm_agent is not None:
            try:
                return await self._llm_agent._think_text(ctx.probe_state, ctx=ctx)
            except Exception:
                pass

        # 4. Fallback
        return {
            "action": "wait",
            "params": {"duration_ms": 500},
            "reason": "multi_no_decision_engine",
        }

    async def _run_verifier(self, ctx: AgentContext) -> None:
        """Validate action effectiveness and detect stuck state."""
        from src.agent.roles.verifier import Verifier

        verifier = Verifier()
        await verifier.act(ctx)
