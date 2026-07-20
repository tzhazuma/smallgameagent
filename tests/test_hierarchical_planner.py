"""Tests for HierarchicalPlanner named-target resolution."""

import json
from unittest.mock import MagicMock

from src.agent.hierarchical_planner import HierarchicalPlanner


def _fake_api_client(response_text: str):
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = response_text
    client.chat.return_value = resp
    return client


def test_l2_named_target_plan_converted_to_tap():
    client = _fake_api_client(json.dumps({
        "plan": [
            {"action_hint": "tap", "target_name": "UnlockItem_1", "reason": "upgrade tower"},
        ],
        "reason": "focus on upgrades",
    }))
    planner = HierarchicalPlanner(api_client=client)
    planner._run_l2({
        "guide_or_target_candidates": [
            {"name": "UnlockItem_1", "path": "root/UnlockItem_1", "screenPosition": {"x": 360, "y": 780}},
        ],
    })
    action = planner._resolve_instruction(planner._l2_queue[0], {
        "guide_or_target_candidates": [
            {"name": "UnlockItem_1", "path": "root/UnlockItem_1", "screenPosition": {"x": 360, "y": 780}},
        ],
    })
    assert action is not None
    assert action["action"] == "tap"
    # Probe screenPosition is already in CSS viewport coords (assumed 375x812 here).
    assert action["params"]["x"] == 360.0
    assert action["params"]["y"] == 780.0


def test_l2_named_target_design_resolution_converted():
    planner = HierarchicalPlanner()
    state = {
        "guide_or_target_candidates": [
            {"name": "UnlockItem_2", "path": "root/UnlockItem_2", "screenPosition": {"x": 720, "y": 1560}},
        ],
    }
    action = planner._resolve_instruction(
        {"action_hint": "tap", "target_name": "UnlockItem_2"},
        state,
    )
    assert action is not None
    assert action["params"]["x"] == 375.0
    assert action["params"]["y"] == 0.0


def test_l2_target_not_found_returns_none():
    planner = HierarchicalPlanner()
    action = planner._resolve_instruction(
        {"action_hint": "tap", "target_name": "MissingTarget"},
        {"guide_or_target_candidates": []},
    )
    assert action is None


def test_l2_wait_instruction():
    planner = HierarchicalPlanner()
    action = planner._resolve_instruction({"action": "wait", "duration_ms": 300}, {})
    assert action["action"] == "wait"
    assert action["params"]["duration_ms"] == 300


def test_l2_legacy_coordinate_format():
    planner = HierarchicalPlanner()
    action = planner._resolve_instruction({"action": "tap", "x": 720, "y": 1560}, {})
    assert action["action"] == "tap"
    assert action["params"]["x"] == 375.0
    assert action["params"]["y"] == 0.0


def test_resolve_named_target_by_path_substring():
    planner = HierarchicalPlanner()
    state = {
        "guide_or_target_candidates": [
            {"name": "some_name", "path": "root/UnlockItem_3", "screenPosition": {"x": 100, "y": 200}},
        ],
    }
    cand = planner._resolve_named_target("unlockitem", state)
    assert cand is not None
    assert cand["path"] == "root/UnlockItem_3"
