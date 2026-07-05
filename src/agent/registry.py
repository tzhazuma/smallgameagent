"""Pluggable decision maker registry for the hybrid agent.

Provides :class:`BaseDecisionMaker` (ABC) and :class:`DecisionRegistry`
(pluggable registry with decorator-based registration).

Usage::

    @DecisionRegistry.register("api")
    class APIDecisionMaker(BaseDecisionMaker):
        ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.context import AgentContext


class BaseDecisionMaker(ABC):
    """Abstract base class for all decision makers.

    Subclasses must implement :meth:`decide`, which produces an action
    dict from the shared agent context.
    """

    @abstractmethod
    async def decide(self, ctx: AgentContext) -> dict[str, Any]:
        """Produce an action dict from the agent context.

        Parameters
        ----------
        ctx:
            Shared agent context (blackboard) with probe state, screenshot,
            working memory, and other per-step data.

        Returns
        -------
        dict
            Action dict with ``"action"``, ``"params"``, and ``"reason"``.
        """
        ...


class DecisionRegistry:
    """Pluggable registry for decision makers.

    Makers register themselves via the :meth:`register` decorator at import
    time.  At runtime the hybrid agent calls :meth:`create` to obtain a
    maker instance for the current mode.

    Example
    -------
    >>> @DecisionRegistry.register("my-mode")
    ... class MyMaker(BaseDecisionMaker):
    ...     async def decide(self, ctx): ...
    """

    _registry: dict[str, type[BaseDecisionMaker]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator: register a decision maker class under *name*.

        Parameters
        ----------
        name:
            Mode string that will be used to look up the maker at runtime.

        Returns
        -------
        Callable
            Decorator that stores the class and returns it unchanged.
        """

        def wrapper(maker_cls: type[BaseDecisionMaker]) -> type[BaseDecisionMaker]:
            cls._registry[name] = maker_cls
            return maker_cls

        return wrapper

    @classmethod
    def create(cls, name: str, **kwargs: Any) -> BaseDecisionMaker:
        """Create a decision maker instance by *name*.

        Parameters
        ----------
        name:
            Registered mode name.
        **kwargs:
            Keyword arguments forwarded to the maker's ``__init__``.

        Returns
        -------
        BaseDecisionMaker
            A fresh instance of the registered class.

        Raises
        ------
        ValueError
            If *name* is not registered.
        """
        maker_cls = cls._registry.get(name)
        if maker_cls is None:
            raise ValueError(f"Unknown decision maker: {name}")
        return maker_cls(**kwargs)

    @classmethod
    def list_modes(cls) -> list[str]:
        """Return sorted list of all registered mode names."""
        return sorted(cls._registry.keys())
