"""Explicit message bus for multi-agent communication.

The bus decouples agent roles (Observer, StateMapper, DecisionAnalyst,
Verifier, Critic, MemoryCurator) by letting them publish and subscribe to
structured messages instead of calling each other directly through the
shared :class:`AgentContext`.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass


class MessageType(Enum):
    """Message types exchanged between agent roles."""

    OBSERVE = "observe"
    PERCEIVE = "perceive"
    DECIDE = "decide"
    VERIFY = "verify"
    CRITIC = "critic"
    MEMORY = "memory"
    NEGOTIATE = "negotiate"
    RULE_UPDATE = "rule_update"


@dataclass
class Message:
    """A single message on the bus.

    Parameters
    ----------
    sender:
        Role name that published the message.
    recipient:
        Target role name, or ``None`` for broadcast.
    type:
        :class:`MessageType` tag.
    payload:
        Arbitrary JSON-safe dict carried by the message.
    step:
        Game step number the message belongs to.
    ts:
        Monotonic timestamp; auto-filled when omitted.
    """

    sender: str
    recipient: str | None
    type: MessageType
    payload: dict[str, Any]
    step: int
    ts: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "type": self.type.value,
            "payload": self.payload,
            "step": self.step,
            "ts": self.ts,
        }


class AgentBus:
    """In-process publish/subscribe bus for agent roles.

    Subscribers register a handler for one or more :class:`MessageType`s.
    Published messages are stored in an append-only history and dispatched
    synchronously to matching handlers.  The bus is intentionally lightweight
    so it can be inspected and logged.
    """

    def __init__(self) -> None:
        self._subscribers: dict[MessageType, list[Callable[[Message], Any]]] = defaultdict(list)
        self._history: list[Message] = []

    def subscribe(
        self,
        type_: MessageType | list[MessageType],
        handler: Callable[[Message], Any],
    ) -> None:
        """Register *handler* for one or more message types."""
        types = [type_] if isinstance(type_, MessageType) else type_
        for t in types:
            self._subscribers[t].append(handler)

    def publish(self, msg: Message) -> None:
        """Store *msg* and dispatch it to subscribers matching its type."""
        self._history.append(msg)
        for handler in self._subscribers.get(msg.type, []):
            handler(msg)

    def publish_async_safe(self, msg: Message) -> None:
        """Same as :meth:`publish` but named to make callers aware of sync dispatch.

        Async callers should await any async handlers themselves; this method
        only invokes synchronous callbacks.
        """
        self.publish(msg)

    def history(self, step: int | None = None, type_: MessageType | None = None) -> list[Message]:
        """Return filtered message history."""
        out = self._history
        if step is not None:
            out = [m for m in out if m.step == step]
        if type_ is not None:
            out = [m for m in out if m.type == type_]
        return out

    def last(self, type_: MessageType, step: int | None = None) -> Message | None:
        """Return the most recent message of *type_* (optionally for *step*)."""
        msgs = self.history(step=step, type_=type_)
        return msgs[-1] if msgs else None

    def stats(self) -> dict[str, Any]:
        """Return bus traffic summary."""
        counts: dict[str, int] = defaultdict(int)
        for m in self._history:
            counts[m.type.value] += 1
        return {"total_messages": len(self._history), "by_type": dict(counts)}
