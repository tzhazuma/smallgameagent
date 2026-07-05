"""StateMapper role — visual state extraction + semantic memory lookup.

Extracts structured visual information from game screenshots using a VLM,
then cross-references the observed state against semantic memory to identify
known patterns, game phases, and relevant prior knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.memory import SemanticMemory


class StateMapper(BaseAgentRole):
    """Agent role that extracts visual structure and looks up semantic memory.

    Parameters
    ----------
    vlm_predict_fn:
        Optional callable ``f(screenshot, state_payload)`` used by
        :func:`src.inference.struct_extractor.extract_visual_structure`.
    semantic_memory:
        Optional :class:`SemanticMemory` instance for knowledge retrieval.
    """

    role_name = "StateMapper"
    capabilities = ["visual-state-extraction", "semantic-memory-lookup", "scene-element-detection"]

    def __init__(
        self,
        vlm_predict_fn: Any | None = None,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        self._vlm_predict_fn = vlm_predict_fn
        self._semantic_memory = semantic_memory

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Read probe state and screenshot availability from context.

        Parameters
        ----------
        ctx:
            Shared agent context.

        Returns
        -------
        dict
            Observation dict with ``probe_state``, ``screenshot`` (bool),
            and ``visual_struct`` placeholder (``None``).
        """
        return {
            "probe_state": ctx.probe_state,
            "screenshot": ctx.screenshot is not None,
            "visual_struct": None,
        }

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """Perform VLM visual extraction and semantic memory lookup.

        When a ``vlm_predict_fn`` is available and the context contains a
        screenshot, the method attempts to extract structured visual
        information.  Separately, when a ``semantic_memory`` instance is
        present, it queries for known game-phase patterns.

        Parameters
        ----------
        ctx:
            Shared agent context.

        Returns
        -------
        dict
            Reasoning result with keys ``visual_struct``, ``known_patterns``,
            and ``elements_detected``.
        """
        visual_struct: dict[str, Any] = {}

        if self._vlm_predict_fn is not None and ctx.screenshot is not None:
            try:
                from io import BytesIO

                from PIL import Image

                from src.inference.struct_extractor import extract_visual_structure

                pil = Image.open(BytesIO(ctx.screenshot)).convert("RGB")
                visual_struct = extract_visual_structure(
                    self._vlm_predict_fn, pil, ctx.probe_state
                )
            except Exception:
                pass

        known_patterns: list[dict[str, Any]] = []
        if self._semantic_memory is not None and ctx.probe_state:
            try:
                state_snippet = str(ctx.probe_state.get("keyNumbers", {}))[:200]
                known_patterns = await self._semantic_memory.query(
                    f"game phase from state: {state_snippet}",
                    game_id=ctx.metadata.get("game_id"),
                    top_k=3,
                )
            except Exception:
                pass

        return {
            "visual_struct": visual_struct,
            "known_patterns": known_patterns,
            "elements_detected": list(visual_struct.keys()) if visual_struct else [],
        }

    async def act(self, ctx: AgentContext) -> None:
        """Write extracted visual structure and relevant knowledge to context.

        Parameters
        ----------
        ctx:
            Shared agent context.  On success ``ctx.visual_struct`` is set
            and ``ctx.metadata["relevant_knowledge"]`` is populated.
        """
        reasoning = await self.reason(ctx)
        if reasoning.get("visual_struct"):
            ctx.visual_struct = reasoning["visual_struct"]
        if reasoning.get("known_patterns"):
            ctx.metadata["relevant_knowledge"] = reasoning["known_patterns"]
