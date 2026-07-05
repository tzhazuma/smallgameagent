"""SkillBuilder role — extracts reusable skills from gameplay experience.

From the PPT's 6-agent design: the SkillBuilder abstracts successful action
patterns into reusable skills, enabling cross-game transfer learning.

Each skill is a named, parameterized action template with preconditions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.roles.base import BaseAgentRole

if TYPE_CHECKING:
    from src.agent.context import AgentContext
    from src.agent.memory import ProceduralMemory


class SkillBuilder(BaseAgentRole):
    """Extracts and persists reusable skills from gameplay.

    Skills are action templates with preconditions that have proven
    successful across game sessions. The SkillBuilder monitors successful
    steps and creates procedural rules that can be reused.
    """

    role_name = "SkillBuilder"
    capabilities = ["skill-extraction", "pattern-learning", "cross-game-transfer"]

    def __init__(self, procedural_memory: ProceduralMemory | None = None) -> None:
        self._procedural = procedural_memory

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Check if the last action was effective."""
        verdict = ctx.metadata.get("verdict")
        action = ctx.final_action
        return {
            "action_effective": getattr(verdict, 'action_effective', False) if verdict else False,
            "action_type": action.get("action", "unknown") if action else "unknown",
            "stuck": ctx.metadata.get("stuck_streak", 0),
        }

    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """Determine if a new skill should be created.

        A skill is created when an action is effective and the agent
        is not stuck (i.e., the action produced progress).
        """
        obs = await self.observe(ctx)
        should_create = obs["action_effective"] and not obs["stuck"]
        return {
            "should_create_skill": should_create,
            "action_type": obs["action_type"],
        }

    async def act(self, ctx: AgentContext) -> None:
        """Create and persist a new procedural rule if warranted."""
        reasoning = await self.reason(ctx)
        if not reasoning["should_create_skill"] or self._procedural is None:
            return

        action = ctx.final_action
        if action is None:
            return

        from src.agent.memory import ProceduralRule

        game_id = ctx.metadata.get("game_id", "unknown")
        rule = ProceduralRule(
            name=f"skill_{game_id}_{ctx.step_number}",
            condition="step_count > 0",
            priority=4,
            action_template={
                "action": action.get("action", "wait"),
                "params": action.get("params", {"duration_ms": 500}),
            },
            source="learned",
            game_id=game_id,
        )
        self._procedural.learn(rule)
        ctx.metadata["skill_created"] = rule.name
