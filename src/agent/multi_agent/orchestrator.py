"""Multi-agent orchestrator that coordinates roles over an explicit message bus.

The orchestrator wires together Observer, StateMapper, DecisionAnalyst,
Verifier, Critic and MemoryCurator.  Each role publishes structured messages
to the shared :class:`AgentBus`, making agent-to-agent communication observable
and debuggable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.multi_agent.bus import AgentBus, Message, MessageType

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.roles.base import BaseAgentRole


class MultiAgentOrchestrator:
    """Run one or more critic/decision rounds per game step.

    Parameters
    ----------
    bus:
        Shared :class:`AgentBus` for role communication.
    observer:
        Captures raw observations.
    state_mapper:
        Extracts visual structure and queries semantic memory.
    decision_analyst:
        Produces the concrete action.
    verifier:
        Validates the action and can trigger a re-decide.
    critic:
        Optional role that diagnoses verifier-flagged decisions.
    memory_curator:
        Optional role that persists cross-session knowledge.
    strategy_memory:
        Optional light-weight strategy memory.
    max_rounds:
        Maximum number of decide/verify/critic rounds per step.
    """

    def __init__(
        self,
        bus: AgentBus,
        observer: "BaseAgentRole",
        state_mapper: "BaseAgentRole",
        decision_analyst: "BaseAgentRole",
        verifier: "BaseAgentRole",
        critic: "BaseAgentRole | None" = None,
        memory_curator: "BaseAgentRole | None" = None,
        strategy_memory: Any = None,
        max_rounds: int = 2,
    ) -> None:
        self.bus = bus
        self.observer = observer
        self.state_mapper = state_mapper
        self.decision_analyst = decision_analyst
        self.verifier = verifier
        self.critic = critic
        self.memory_curator = memory_curator
        self.strategy_memory = strategy_memory
        self.max_rounds = max(max_rounds, 1)

    async def step(self, ctx: "AgentContext") -> dict[str, Any]:
        """Execute one full multi-agent step and return the final action."""
        step_num = ctx.step_number

        # --- Observer ---
        self.bus.publish(
            Message("orchestrator", None, MessageType.OBSERVE, {"status": "start"}, step_num)
        )
        obs = await self.observer.observe(ctx)
        await self.observer.act(ctx)
        self.bus.publish(
            Message("Observer", None, MessageType.OBSERVE, {"observation": obs}, step_num)
        )

        # --- StateMapper ---
        self.bus.publish(
            Message("orchestrator", None, MessageType.PERCEIVE, {"status": "start"}, step_num)
        )
        perception = await self.state_mapper.reason(ctx)
        await self.state_mapper.act(ctx)
        self.bus.publish(
            Message("StateMapper", None, MessageType.PERCEIVE, {"perception": perception}, step_num)
        )

        # --- Decision / Verify loop ---
        final_action: dict[str, Any] | None = None
        for round_idx in range(self.max_rounds):
            self.bus.publish(
                Message("orchestrator", None, MessageType.DECIDE, {"round": round_idx}, step_num)
            )
            await self.decision_analyst.reason(ctx)
            await self.decision_analyst.act(ctx)
            final_action = ctx.final_action
            self.bus.publish(
                Message(
                    "DecisionAnalyst",
                    None,
                    MessageType.DECIDE,
                    {"action": final_action, "round": round_idx},
                    step_num,
                )
            )

            self.bus.publish(
                Message("orchestrator", None, MessageType.VERIFY, {"round": round_idx}, step_num)
            )
            verdict = await self.verifier.reason(ctx)
            await self.verifier.act(ctx)
            ctx.metadata["verifier_verdict"] = verdict
            self.bus.publish(
                Message("Verifier", None, MessageType.VERIFY, {"verdict": verdict, "round": round_idx}, step_num)
            )

            if not self._should_redecide(verdict):
                break

            if round_idx == 0 and self.critic is not None:
                self.bus.publish(
                    Message("orchestrator", None, MessageType.CRITIC, {"round": round_idx}, step_num)
                )
                feedback = await self.critic.reason(ctx)
                await self.critic.act(ctx)
                self.bus.publish(
                    Message("Critic", None, MessageType.CRITIC, {"feedback": feedback}, step_num)
                )
        else:
            # Loop exhausted without verifier approval.
            final_action = ctx.final_action or {
                "action": "wait",
                "params": {"duration_ms": 500},
                "reason": "orchestrator_fallback",
            }

        # --- Memory ---
        await self._update_memory(ctx, final_action)
        self.bus.publish(
            Message("orchestrator", None, MessageType.MEMORY, {"status": "updated"}, step_num)
        )

        return final_action or {"action": "wait", "params": {"duration_ms": 500}, "reason": "empty"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_redecide(self, verdict: Any) -> bool:
        """Return True if the orchestrator should run another decision round."""
        if verdict is None:
            return False
        if isinstance(verdict, dict):
            recommendation = verdict.get("recommendation")
            stuck = verdict.get("stuck")
            effective = verdict.get("action_effective", True)
        else:
            recommendation = getattr(verdict, "recommendation", None)
            stuck = getattr(verdict, "stuck", False)
            effective = getattr(verdict, "action_effective", True)
        if recommendation in {"escape_rotate", "reobserve"}:
            return True
        return bool(stuck) or not bool(effective)

    async def _update_memory(self, ctx: "AgentContext", action: dict[str, Any]) -> None:
        """Persist step outcome to available memory stores."""
        if self.memory_curator is not None:
            try:
                await self.memory_curator.act(ctx)
            except Exception:
                pass
        if self.strategy_memory is not None:
            try:
                game_id = ctx.metadata.get("game_id", "unknown")
                phase = self.strategy_memory.phase_id(ctx.probe_state)
                success = not ctx.probe_state.get("done") or ctx.probe_state.get("win", False)
                self.strategy_memory.record(
                    game_id, phase, {"action": action.get("action"), "params": action.get("params")}, success
                )
            except Exception:
                pass
