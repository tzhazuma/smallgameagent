"""Tests for the explicit multi-agent message bus."""

from __future__ import annotations


from src.agent.multi_agent.bus import AgentBus, Message, MessageType


def test_publish_and_history():
    bus = AgentBus()
    captured: list[Message] = []
    bus.subscribe(MessageType.OBSERVE, captured.append)

    msg = Message("Observer", None, MessageType.OBSERVE, {"foo": 1}, step=0)
    bus.publish(msg)

    assert len(captured) == 1
    assert captured[0].payload == {"foo": 1}
    assert bus.last(MessageType.OBSERVE) == msg


def test_subscribe_multiple_types():
    bus = AgentBus()
    captured: list[Message] = []
    bus.subscribe([MessageType.OBSERVE, MessageType.DECIDE], captured.append)

    bus.publish(Message("O", None, MessageType.OBSERVE, {}, step=0))
    bus.publish(Message("D", None, MessageType.DECIDE, {}, step=0))
    bus.publish(Message("V", None, MessageType.VERIFY, {}, step=0))

    assert len(captured) == 2


def test_history_filtering():
    bus = AgentBus()
    bus.publish(Message("O", None, MessageType.OBSERVE, {}, step=0))
    bus.publish(Message("O", None, MessageType.OBSERVE, {}, step=1))
    bus.publish(Message("D", None, MessageType.DECIDE, {}, step=1))

    assert len(bus.history(step=1)) == 2
    assert len(bus.history(type_=MessageType.OBSERVE)) == 2
    assert len(bus.history(step=1, type_=MessageType.DECIDE)) == 1


def test_message_to_dict():
    msg = Message("Observer", "Planner", MessageType.OBSERVE, {"x": 1}, step=5)
    d = msg.to_dict()
    assert d["sender"] == "Observer"
    assert d["recipient"] == "Planner"
    assert d["type"] == "observe"
    assert d["step"] == 5
    assert "ts" in d


def test_bus_stats():
    bus = AgentBus()
    for _ in range(3):
        bus.publish(Message("O", None, MessageType.OBSERVE, {}, step=0))
    bus.publish(Message("D", None, MessageType.DECIDE, {}, step=0))

    stats = bus.stats()
    assert stats["total_messages"] == 4
    assert stats["by_type"]["observe"] == 3
    assert stats["by_type"]["decide"] == 1
