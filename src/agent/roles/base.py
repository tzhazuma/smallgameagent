"""Base agent role abstraction, role card, and pipeline runner.

Provides the :class:`BaseAgentRole` abstract base class that all specialised
agent roles must implement, the :class:`RoleCard` introspection dataclass,
and the :func:`run_pipeline` helper that drives a list of roles through
their observe → reason → act lifecycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.context import AgentContext


# ---------------------------------------------------------------------------
# RoleCard — lightweight introspection descriptor
# ---------------------------------------------------------------------------


@dataclass
class RoleCard:
    """Describes a role's identity, capabilities, and I/O contract.

    Parameters
    ----------
    name:
        Human-readable role name (e.g. ``"explorer"``, ``"critic"``).
    capabilities:
        List of capability tags the role advertises.
    input_keys:
        Keys the role expects to find in the agent context.
    output_keys:
        Keys the role writes to the agent context.
    description:
        Free-text description of the role's purpose.
    """

    name: str
    capabilities: list[str] = field(default_factory=list)
    input_keys: list[str] = field(default_factory=list)
    output_keys: list[str] = field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# BaseAgentRole — abstract base class
# ---------------------------------------------------------------------------


class BaseAgentRole(ABC):
    """Abstract base for every specialised agent role.

    Subclasses must implement:

    - :attr:`role_name` (property)
    - :attr:`capabilities` (property)
    - :meth:`observe` — read + filter context data
    - :meth:`reason` — produce reasoning / decision dict
    - :meth:`act` — commit side effects (write to context, dispatch actions)

    A concrete :meth:`to_card` is provided that builds a :class:`RoleCard`
    from the role's properties.
    """

    # ── Abstract interface ────────────────────────────────────────────

    @property
    @abstractmethod
    def role_name(self) -> str:
        """Human-readable name for this role."""

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        """Capability tags this role provides."""

    @abstractmethod
    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        """Read from *ctx* and return a filtered observation dict.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard).

        Returns
        -------
        dict
            Role-specific observation data.
        """

    @abstractmethod
    async def reason(self, ctx: AgentContext) -> dict[str, Any]:
        """Reason over the current context and return a decision dict.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard).

        Returns
        -------
        dict
            Reasoning output (e.g. action plan, score, choice).
        """

    @abstractmethod
    async def act(self, ctx: AgentContext) -> None:
        """Commit side-effects back to *ctx* (write fields, dispatch actions).

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard).
        """

    # ── Concrete helpers ──────────────────────────────────────────────

    def to_card(self) -> RoleCard:
        """Build an introspection card from this role's properties.

        Returns
        -------
        RoleCard
            Card with ``name`` and ``capabilities`` populated.
        """
        return RoleCard(name=self.role_name, capabilities=list(self.capabilities))


# ---------------------------------------------------------------------------
# RolePipeline — sequential observe → reason → act
# ---------------------------------------------------------------------------


async def run_pipeline(ctx: AgentContext, roles: list[BaseAgentRole]) -> None:
    """Run observe → reason → act for each role sequentially.

    Parameters
    ----------
    ctx:
        Shared agent context passed to every role.
    roles:
        Ordered list of roles to execute.  An empty list is a no-op.

    .. note::

        Roles are executed **sequentially** in the order they appear in
        the list.  Future versions may support parallel execution stages.
    """
    for role in roles:
        await role.observe(ctx)
        await role.reason(ctx)
        await role.act(ctx)
