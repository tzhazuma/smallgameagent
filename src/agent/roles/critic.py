"""Critic role — provides structured feedback when a decision is challenged.

The Critic is invoked by the orchestrator when the Verifier recommends a
re-decide.  It analyses the current context and produces a short diagnosis
(e.g. ``"stuck"``, ``"wrong_target"``, ``"modal_panel"``) along with a
suggested correction direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext


@dataclass
class CriticFeedback:
    """Structured critic output."""

    diagnosis: str = "ok"
    correction: str = "none"
    confidence: float = 1.0


class Critic(BaseAgentRole):
    """Diagnoses questionable decisions and suggests corrections."""

    role_name = "Critic"
    capabilities = ["decision-diagnosis", "failure-analysis", "correction-suggestion"]

    async def observe(self, ctx: "AgentContext") -> dict[str, Any]:
        """Read the last action, verifier recommendation, and working memory."""
        wm = ctx.working_memory
        stuck = False
        if wm is not None and hasattr(wm, "detect_stuck"):
            player = ctx.probe_state.get("player") or {}
            pos = player.get("worldPosition") or player.get("screenPosition") or {}
            xy = (pos.get("x", 0.0), pos.get("y", 0.0) if "y" in pos else pos.get("z", 0.0))
            stuck = wm.detect_stuck(xy)
        return {
            "last_action": ctx.final_action,
            "verdict": ctx.metadata.get("verifier_verdict"),
            "stuck": stuck,
            "step": ctx.step_number,
        }

    async def reason(self, ctx: "AgentContext") -> dict[str, Any]:
        """Produce a diagnosis from the observation."""
        obs = await self.observe(ctx)
        verdict = obs.get("verdict") or {}
        recommendation = verdict.get("recommendation")

        if recommendation == "escape_rotate":
            feedback = CriticFeedback("stuck", "rotate_joystick", 0.8)
        elif recommendation == "reobserve":
            feedback = CriticFeedback("uncertain_state", "reobserve", 0.7)
        elif obs.get("stuck"):
            feedback = CriticFeedback("stuck", "escape_random", 0.7)
        elif ctx.probe_state.get("done"):
            feedback = CriticFeedback("terminal_state", "stop", 0.9)
        else:
            feedback = CriticFeedback("ok", "none", 0.9)
        return feedback.__dict__

    async def act(self, ctx: "AgentContext") -> None:
        """Write critic feedback to context metadata."""
        feedback = await self.reason(ctx)
        ctx.metadata["critic_feedback"] = feedback
