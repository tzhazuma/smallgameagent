"""Multi-agent decision maker backed by an explicit message bus.

This is an evolution of ``multi_maker``: roles still perform the same
perception/reasoning/validation work, but they communicate through the
shared :class:`AgentBus`, making agent-to-agent communication observable
and extensible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.multi_agent import AgentBus, MultiAgentOrchestrator
from src.agent.registry import BaseDecisionMaker, DecisionRegistry
from src.agent.roles.critic import Critic
from src.agent.roles.decision_analyst import DecisionAnalyst
from src.agent.roles.memory_curator import MemoryCurator
from src.agent.roles.observer import Observer
from src.agent.roles.state_mapper import StateMapper
from src.agent.roles.verifier import Verifier

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.llm_agent import LLMAgent
    from src.engine.rules import RuleEngine


@DecisionRegistry.register("multi-bus")
class MultiAgentBusDecisionMaker(BaseDecisionMaker):
    """Bus-based multi-agent decision maker.

    Parameters
    ----------
    llm_agent:
        ``LLMAgent`` for API text reasoning.
    rule_engine:
        ``RuleEngine`` for local zero-latency decisions.
    api_client:
        ``OpenCodeGoClient`` for API vision calls.
    visual_analyzer:
        ``VisualAnalyzer`` for local visual fallback.
    episodic_memory:
        Optional cross-session episodic memory.
    semantic_memory:
        Optional vector-searchable semantic memory.
    procedural_memory:
        Optional learned procedural rules.
    strategy_memory:
        Optional light-weight strategy memory.
    """

    def __init__(
        self,
        llm_agent: "LLMAgent | None" = None,
        rule_engine: "RuleEngine | None" = None,
        api_client: Any | None = None,
        visual_analyzer: Any | None = None,
        episodic_memory: Any | None = None,
        semantic_memory: Any | None = None,
        procedural_memory: Any | None = None,
        strategy_memory: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self.bus = AgentBus()

        # Build roles
        observer = Observer()

        # StateMapper gets a synchronous local-VLM wrapper if a visual analyzer is provided.
        vlm_predict_fn = None
        if visual_analyzer is not None and hasattr(visual_analyzer, "analyze_pil"):
            def _vlm_predict(pil_image, _state_payload):
                return visual_analyzer.analyze_pil(pil_image)

            vlm_predict_fn = _vlm_predict

        state_mapper = StateMapper(
            vlm_predict_fn=vlm_predict_fn,
            semantic_memory=semantic_memory,
        )

        decision_analyst = DecisionAnalyst(
            rule_engine=rule_engine,
            procedural_memory=procedural_memory,
            llm_agent=llm_agent,
            strategy_memory=strategy_memory,
        )

        verifier = Verifier()
        critic = Critic()

        memory_curator = None
        if episodic_memory is not None:
            memory_curator = MemoryCurator(
                episodic_memory=episodic_memory,
                semantic_memory=semantic_memory,
                procedural_memory=procedural_memory,
            )

        self.orchestrator = MultiAgentOrchestrator(
            bus=self.bus,
            observer=observer,
            state_mapper=state_mapper,
            decision_analyst=decision_analyst,
            verifier=verifier,
            critic=critic,
            memory_curator=memory_curator,
            strategy_memory=strategy_memory,
            max_rounds=kwargs.get("max_rounds", 2),
        )

    async def decide(self, ctx: "AgentContext") -> dict[str, Any]:
        """Run one bus-coordinated multi-agent step."""
        ctx.metadata["_maker"] = self
        action = await self.orchestrator.step(ctx)
        ctx.metadata["bus_stats"] = self.bus.stats()
        return action

    async def on_session_end(self, ctx: "AgentContext") -> None:
        """Persist memory state at session end."""
        if self.orchestrator.memory_curator is not None:
            ctx.metadata["session_phase"] = "end"
            try:
                await self.orchestrator.memory_curator.act(ctx)
            except Exception:
                pass


@DecisionRegistry.register("multi-bus-memory")
class MultiAgentBusMemoryDecisionMaker(MultiAgentBusDecisionMaker):
    """Convenience alias for ``multi-bus`` when memory stores are present."""

    pass
