"""Hierarchical decision maker — registers the ``hierarchical`` mode.

Wires the three-layer HierarchicalPlanner into the DecisionRegistry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.agent.hierarchical_planner import HierarchicalPlanner
from src.agent.registry import BaseDecisionMaker, DecisionRegistry

if TYPE_CHECKING:
    from src.agent.context import AgentContext


@DecisionRegistry.register("hierarchical")
class HierarchicalDecisionMaker(BaseDecisionMaker):
    """Three-layer hierarchical decision maker.

    L0 = rule engine (every step), L1 = local VLM (every 5 steps / stuck),
    L2 = cloud API (every 15 steps / phase change).
    """

    def __init__(
        self,
        rule_engine: Any = None,
        api_client: Any = None,
        lmstudio_client: Any = None,
        rule_params: Any = None,
        strategy_memory: Any = None,
        **kwargs: Any,
    ) -> None:
        # If no lmstudio_client provided, try to create one only when L1 is enabled
        l1_interval = kwargs.get("l1_interval", 5)
        if lmstudio_client is None and l1_interval > 0:
            try:
                from src.agent.lmstudio_client import LMStudioClient
                lmstudio_client = LMStudioClient()
            except Exception:
                pass

        self._planner = HierarchicalPlanner(
            rule_engine=rule_engine,
            api_client=api_client,
            lmstudio_client=lmstudio_client,
            l1_interval=l1_interval,
            l2_interval=kwargs.get("l2_interval", 15),
            stuck_threshold=kwargs.get("stuck_threshold", 3),
            rule_params=rule_params,
            strategy_memory=strategy_memory,
        )

    async def decide(self, ctx: "AgentContext") -> dict[str, Any]:
        # Run the synchronous planner in a thread executor so that blocking
        # cloud API calls do not freeze the Playwright event loop.
        loop = asyncio.get_running_loop()
        action = await loop.run_in_executor(None, self._planner.step, ctx)
        ctx.metadata["hierarchical_stats"] = self._planner.stats()
        return action
