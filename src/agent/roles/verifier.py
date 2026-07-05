"""Verifier role — action validation, stuck detection, progress tracking.

Provides the :class:`Verdict` dataclass for structured action-evaluation
output and the :class:`Verifier` role that implements the observe → reason
→ act lifecycle from :class:`BaseAgentRole`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext


# ---------------------------------------------------------------------------
# Verdict — structured action-evaluation output
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """Structured outcome of a single action-evaluation cycle.

    Parameters
    ----------
    action_effective:
        Whether the last action produced measurable change (position /
        score).
    stuck:
        Whether the agent is in a stuck state according to working memory.
    progress_delta:
        Numeric change in score (first key from ``keyNumbers``).
    recommendation:
        Suggested next action when the verifier detects a problem —
        ``"escape_rotate"``, ``"reobserve"``, or ``None``.
    confidence:
        Confidence in the recommendation (0.0 – 1.0).
    """

    action_effective: bool = False
    stuck: bool = False
    progress_delta: float = 0.0
    recommendation: str | None = None
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Verifier — BaseAgentRole subclass
# ---------------------------------------------------------------------------


class Verifier(BaseAgentRole):
    """Evaluates action effectiveness, detects stuck states, and tracks progress.

    Compares the current probe state against the pre-action snapshot stored
    in ``ctx.metadata["prev_probe_state"]`` and produces a :class:`Verdict`.
    """

    # ── Role identity ──────────────────────────────────────────────────

    @property
    def role_name(self) -> str:
        """Human-readable name returned by ``to_card()``."""
        return "Verifier"

    @property
    def capabilities(self) -> list[str]:
        """Capability tags this role provides."""
        return [
            "action-validation",
            "stuck-detection",
            "progress-tracking",
        ]

    # ── Observe ────────────────────────────────────────────────────────

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Compare pre-action and post-action probe state.

        Parameters
        ----------
        ctx:
            Shared agent context containing ``probe_state`` and
            ``metadata["prev_probe_state"]``.

        Returns
        -------
        dict
            Keys ``position_changed`` (bool) and ``score_delta`` (float).
        """
        prev_state = ctx.metadata.get("prev_probe_state", {})
        curr_state = ctx.probe_state

        pos_changed = False
        score_delta = 0.0

        # --- Player position comparison ------------------------------------
        prev_player = prev_state.get("player", {}).get("worldPosition", {})
        curr_player = curr_state.get("player", {}).get("worldPosition", {})
        if prev_player and curr_player:
            pos_changed = (
                prev_player.get("x") != curr_player.get("x")
                or prev_player.get("z") != curr_player.get("z")
            )

        # --- Score / key-number delta --------------------------------------
        prev_keys = prev_state.get("keyNumbers", {})
        curr_keys = curr_state.get("keyNumbers", {})
        for k in curr_keys:
            score_delta = float(curr_keys.get(k, 0)) - float(
                prev_keys.get(k, 0)
            )
            break

        return {"position_changed": pos_changed, "score_delta": score_delta}

    # ── Reason ──────────────────────────────────────────────────────────

    async def reason(self, ctx: AgentContext) -> Verdict:
        """Evaluate action effectiveness and detect stuck conditions.

        Parameters
        ----------
        ctx:
            Shared agent context.

        Returns
        -------
        Verdict
            Structured evaluation with recommendation when applicable.
        """
        obs = await self.observe(ctx)
        wm = ctx.working_memory
        is_stuck = bool(wm.is_stuck) if wm is not None else False
        stuck_streak = int(wm.stuck_streak) if wm is not None else 0
        action_effective = obs["position_changed"] or obs["score_delta"] > 0

        recommendation: str | None = None
        confidence = 1.0

        if is_stuck or stuck_streak >= 3:
            recommendation = "escape_rotate"
            confidence = max(0.5, 1.0 - stuck_streak * 0.1)
        elif not action_effective:
            recommendation = "reobserve"
            confidence = 0.7

        return Verdict(
            action_effective=action_effective,
            stuck=is_stuck,
            progress_delta=obs["score_delta"],
            recommendation=recommendation,
            confidence=confidence,
        )

    # ── Act ─────────────────────────────────────────────────────────────

    async def act(self, ctx: AgentContext) -> None:
        """Write verdict to ``ctx.metadata`` and enqueue error on problem.

        Parameters
        ----------
        ctx:
            Shared agent context.  ``metadata["verdict"]`` is set to the
            :class:`Verdict` instance; ``errors`` is appended to when a
            recommendation exists.
        """
        verdict = await self.reason(ctx)
        ctx.metadata["verdict"] = verdict
        if verdict.recommendation:
            ctx.errors.append(
                f"Verifier: {verdict.recommendation} "
                f"(confidence: {verdict.confidence:.1%})"
            )
