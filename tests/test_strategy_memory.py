"""Tests for the light-weight strategy memory."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.agent.strategy_memory import StrategyMemory


def test_record_and_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        mem = StrategyMemory(Path(tmp) / "strat.json")
        mem.record("g1", "phaseA", {"action": "move", "params": {"dx": 1}}, success=True, notes="ok")
        mem.record("g1", "phaseA", {"action": "move", "params": {"dx": 1}}, success=True)
        mem.record("g1", "phaseA", {"action": "tap"}, success=False)

        top = mem.lookup("g1", "phaseA", top_k=2)
        assert len(top) == 2
        assert top[0]["pattern"]["action"] == "move"
        assert top[0]["successes"] == 2
        assert top[0]["attempts"] == 2
        assert top[1]["pattern"]["action"] == "tap"


def test_phase_id_from_state():
    mem = StrategyMemory()
    assert mem.phase_id({"win": True, "keyFlags": {"f": 1}}) == "win_F:f"
    assert mem.phase_id({"done": True}) == "done"
    assert mem.phase_id({}) == "play"


def test_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "strat.json"
        mem = StrategyMemory(path)
        mem.record("g1", "p1", {"action": "wait"}, success=True)
        del mem

        mem2 = StrategyMemory(path)
        top = mem2.lookup("g1", "p1")
        assert len(top) == 1
        assert top[0]["pattern"]["action"] == "wait"


def test_lookup_empty():
    mem = StrategyMemory()
    assert mem.lookup("unknown", "phase") == []
