"""Observer role — raw game state and screenshot observation for the 6-agent pipeline.

From the PPT's 6-agent design: the Observer captures raw observations
(probe state + screenshot) without interpretation, feeding downstream
roles (StateMapper, Verifier) with clean, timestamped data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext


class Observer(BaseAgentRole):
    """Captures raw game observations for the multi-agent pipeline.

    The Observer is the first role in the PPT's 6-agent workflow loop.
    It reads probe state and screenshot from the AgentContext and
    timestamps them for downstream processing.
    """

    role_name = "Observer"
    capabilities = ["raw-observation", "state-capture", "screenshot-capture"]

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Capture current probe state and screenshot metadata."""
        import time

        return {
            "timestamp": time.monotonic(),
            "probe_state_ready": bool(ctx.probe_state.get("ready", False)),
            "screenshot_available": ctx.screenshot is not None,
            "step_number": ctx.step_number,
        }

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """No reasoning — Observer is a pure observation role."""
        obs = await self.observe(ctx)
        return {"observation": obs, "status": "observed"}

    async def act(self, ctx: AgentContext) -> None:
        """Store observation timestamp in metadata for downstream roles."""
        obs = await self.observe(ctx)
        ctx.metadata["last_observation"] = obs
