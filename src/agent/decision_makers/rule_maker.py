"""Decision maker that delegates to the rule engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.registry import BaseDecisionMaker, DecisionRegistry

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.visual_analyzer import VisualAnalyzer
    from src.engine.rules import RuleEngine


@DecisionRegistry.register("rule")
class RuleDecisionMaker(BaseDecisionMaker):
    """Decision maker that uses the rule engine (no LLM required).

    Optionally runs visual analysis on the current screenshot before
    delegating to the rule engine.

    Parameters
    ----------
    rule_engine:
        Optional :class:`RuleEngine` instance.
    visual_analyzer:
        Optional :class:`VisualAnalyzer` instance.
    """

    def __init__(
        self,
        rule_engine: RuleEngine | None = None,
        visual_analyzer: VisualAnalyzer | None = None,
        **kwargs: Any,
    ) -> None:
        self._rule_engine = rule_engine
        self._visual_analyzer = visual_analyzer

    async def decide(self, ctx: AgentContext) -> dict[str, Any]:
        if self._rule_engine is None:
            return {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_rule_engine"}

        visual: dict[str, Any] | None = None
        if ctx.screenshot is not None and self._visual_analyzer is not None:
            try:
                from io import BytesIO
                from PIL import Image

                pil = Image.open(BytesIO(ctx.screenshot)).convert("RGB")
                visual = self._visual_analyzer.analyze_pil(pil)
            except Exception:
                pass

        return self._rule_engine.step(ctx.probe_state, visual)
