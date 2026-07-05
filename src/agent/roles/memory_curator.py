"""MemoryCurator role — cross-session memory orchestration.

The MemoryCurator observes session lifecycle events (start/end) and
orchestrates episodic, semantic, and procedural memory components to
provide cross-session context and persistent knowledge extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.memory import EpisodicMemory, ProceduralMemory, SemanticMemory


class MemoryCurator(BaseAgentRole):
    """Agent role that manages cross-session memory lifecycle.

    At session start, it loads relevant past sessions, semantic knowledge,
    and procedural rules. At session end, it persists the outcome and
    extracts new knowledge.

    Parameters
    ----------
    episodic_memory:
        Optional EpisodicMemory instance for session/step persistence.
    semantic_memory:
        Optional SemanticMemory instance for vector-searchable knowledge.
    procedural_memory:
        Optional ProceduralMemory instance for rule matching.
    """

    role_name = "MemoryCurator"
    capabilities = [
        "cross-session-memory",
        "knowledge-extraction",
        "memory-lifecycle-management",
    ]

    def __init__(
        self,
        episodic_memory: EpisodicMemory | None = None,
        semantic_memory: SemanticMemory | None = None,
        procedural_memory: ProceduralMemory | None = None,
    ) -> None:
        self._episodic = episodic_memory
        self._semantic = semantic_memory
        self._procedural = procedural_memory

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Read session phase and ID from context metadata.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard).

        Returns
        -------
        dict
            Observation dict with ``phase`` and ``session_id`` keys.
        """
        phase = ctx.metadata.get("session_phase", "mid")
        session_id = ctx.metadata.get("session_id")
        return {"phase": phase, "session_id": session_id}

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """Reason over session lifecycle and orchestrate memory operations.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard).

        Returns
        -------
        dict
            Reasoning output keyed by phase:
            - ``"start"``: loads previous sessions, relevant knowledge, matched rule.
            - ``"end"``: persists session, generates summary, extracts knowledge.
            - ``"mid"``: no-op.
        """
        obs = await self.observe(ctx)
        phase = obs["phase"]
        result: dict[str, Any] = {"phase": phase}

        if phase == "start" and self._episodic:
            try:
                game_id = ctx.metadata.get("game_id", "unknown")
                sessions = await self._episodic.find_similar(game_id, top_k=3)
                result["previous_sessions"] = sessions
            except Exception:
                result["previous_sessions"] = []

            if self._semantic:
                try:
                    knowledge = await self._semantic.query(
                        "game strategy", game_id=game_id, top_k=3
                    )
                    result["relevant_knowledge"] = knowledge
                except Exception:
                    result["relevant_knowledge"] = []

            if self._procedural:
                try:
                    matched = self._procedural.match(
                        ctx, getattr(ctx, "working_memory", None)
                    )
                    result["matched_rule"] = matched
                except Exception:
                    result["matched_rule"] = None

        elif phase == "end" and self._episodic:
            session_id = obs["session_id"]
            if session_id:
                try:
                    await self._episodic.end_session(
                        session_id,
                        ctx.metadata.get("result", "timeout"),
                        ctx.metadata.get("score", 0.0),
                    )
                except Exception:
                    pass

                try:
                    summary = getattr(
                        self._episodic,
                        "summarize_session",
                        lambda sid: f"Session {sid}",
                    )(session_id)
                    result["session_summary"] = summary
                except Exception:
                    result["session_summary"] = f"Session {session_id}"

                if self._semantic and result.get("session_summary"):
                    try:
                        count = await self._semantic.extract_from_session(
                            result["session_summary"],
                            ctx.metadata.get("game_id", ""),
                        )
                        result["knowledge_extracted"] = count
                    except Exception:
                        result["knowledge_extracted"] = 0

        return result

    async def act(self, ctx: AgentContext) -> None:
        """Commit memory reasoning results back to the agent context.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard) — metadata is updated in place.
        """
        reasoning = await self.reason(ctx)
        phase = reasoning.get("phase", "mid")

        if phase == "start":
            ctx.metadata["previous_sessions"] = reasoning.get(
                "previous_sessions", []
            )
            ctx.metadata["relevant_knowledge"] = reasoning.get(
                "relevant_knowledge", []
            )
            if reasoning.get("matched_rule"):
                ctx.metadata["matched_rule"] = reasoning["matched_rule"]

        elif phase == "end":
            ctx.metadata["session_summary"] = reasoning.get(
                "session_summary", ""
            )
            ctx.metadata["knowledge_extracted"] = reasoning.get(
                "knowledge_extracted", 0
            )
