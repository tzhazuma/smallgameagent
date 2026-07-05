"""Tests for src.agent.context.AgentContext.

Coverage: default values, to_dict, from_dict, snapshot independence,
clear_step, and field mutation patterns.
"""

from __future__ import annotations

import json

import pytest

from src.agent.context import AgentContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def populated_ctx() -> AgentContext:
    """Return an AgentContext with all fields set to non-default values."""
    return AgentContext(
        probe_state={"game": "runner", "score": 42},
        screenshot=b"\x89PNG\r\n\x1a\n",
        visual_struct={"objects": ["player", "enemy"]},
        text_decision={"action": "move", "params": {"dx": 1.0}},
        vision_decision={"has_arrow": True, "direction": "right"},
        final_action={"type": "joystick", "x": 0.5, "y": 0.8},
        current_mode="hybrid",
        step_number=5,
        errors=["timeout on step 3"],
        metadata={"game_id": "SSD_00848P01"},
    )


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

class TestDefaults:
    """All fields should initialise to their default values."""

    def test_default_probe_state_is_empty_dict(self) -> None:
        ctx = AgentContext()
        assert ctx.probe_state == {}

    def test_default_screenshot_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.screenshot is None

    def test_default_visual_struct_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.visual_struct is None

    def test_default_text_decision_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.text_decision is None

    def test_default_vision_decision_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.vision_decision is None

    def test_default_final_action_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.final_action is None

    def test_default_extracted_rules_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.extracted_rules is None

    def test_default_working_memory_is_none(self) -> None:
        ctx = AgentContext()
        assert ctx.working_memory is None

    def test_default_current_mode_is_api_string(self) -> None:
        ctx = AgentContext()
        assert ctx.current_mode == "api"

    def test_default_step_number_is_zero(self) -> None:
        ctx = AgentContext()
        assert ctx.step_number == 0

    def test_default_errors_is_empty_list(self) -> None:
        ctx = AgentContext()
        assert ctx.errors == []

    def test_default_metadata_is_empty_dict(self) -> None:
        ctx = AgentContext()
        assert ctx.metadata == {}


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    """Serialisation via ``to_dict()``."""

    def test_to_dict_returns_plain_dict(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_is_json_serializable(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        # Should not raise.
        json.dumps(result)

    def test_to_dict_converts_bytes_to_str(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        assert isinstance(result["screenshot"], str)
        assert "PNG" in result["screenshot"]

    def test_to_dict_keeps_probe_state_intact(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        assert result["probe_state"] == {"game": "runner", "score": 42}

    def test_to_dict_includes_step_number(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        assert result["step_number"] == 5

    def test_to_dict_includes_errors(self, populated_ctx: AgentContext) -> None:
        result = populated_ctx.to_dict()
        assert "timeout on step 3" in result["errors"]


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------

class TestFromDict:
    """Deserialisation via ``from_dict()``."""

    def test_from_dict_reconstructs_scalar_fields(self, populated_ctx: AgentContext) -> None:
        data = populated_ctx.to_dict()
        restored = AgentContext.from_dict(data)
        assert restored.current_mode == "hybrid"
        assert restored.step_number == 5
        assert restored.probe_state == {"game": "runner", "score": 42}
        assert restored.errors == ["timeout on step 3"]
        assert restored.metadata == {"game_id": "SSD_00848P01"}

    def test_from_dict_round_trip_decision_fields(self, populated_ctx: AgentContext) -> None:
        data = populated_ctx.to_dict()
        restored = AgentContext.from_dict(data)
        assert restored.text_decision == {"action": "move", "params": {"dx": 1.0}}
        assert restored.vision_decision == {"has_arrow": True, "direction": "right"}
        assert restored.final_action == {"type": "joystick", "x": 0.5, "y": 0.8}
        assert restored.visual_struct == {"objects": ["player", "enemy"]}

    def test_from_dict_handles_empty_dict(self) -> None:
        """Passing ``{}`` should produce a fully default context."""
        restored = AgentContext.from_dict({})
        assert restored.probe_state == {}
        assert restored.screenshot is None
        assert restored.visual_struct is None
        assert restored.text_decision is None
        assert restored.vision_decision is None
        assert restored.final_action is None
        assert restored.current_mode == "api"
        assert restored.step_number == 0
        assert restored.errors == []
        assert restored.metadata == {}

    def test_from_dict_handles_partial_data(self) -> None:
        """Missing keys should fall back to safe defaults."""
        restored = AgentContext.from_dict({"current_mode": "vision", "step_number": 3})
        assert restored.current_mode == "vision"
        assert restored.step_number == 3
        assert restored.probe_state == {}
        assert restored.errors == []

    def test_from_dict_handles_none_values(self) -> None:
        restored = AgentContext.from_dict({
            "text_decision": None,
            "vision_decision": None,
            "final_action": None,
        })
        assert restored.text_decision is None
        assert restored.vision_decision is None
        assert restored.final_action is None


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    """Independent copy via ``snapshot()``."""

    def test_snapshot_is_independent_copy(self, populated_ctx: AgentContext) -> None:
        snap = populated_ctx.snapshot()
        populated_ctx.probe_state["new_key"] = "mutated"
        populated_ctx.step_number = 99
        assert "new_key" not in snap.probe_state
        assert snap.step_number == 5

    def test_snapshot_screenshot_shared_reference(self, populated_ctx: AgentContext) -> None:
        """bytes is immutable; sharing the reference is safe."""
        snap = populated_ctx.snapshot()
        assert snap.screenshot is populated_ctx.screenshot

    def test_snapshot_errors_list_is_independent(self, populated_ctx: AgentContext) -> None:
        snap = populated_ctx.snapshot()
        populated_ctx.errors.append("new error")
        assert len(snap.errors) == 1
        assert "timeout on step 3" in snap.errors

    def test_snapshot_metadata_is_independent(self, populated_ctx: AgentContext) -> None:
        snap = populated_ctx.snapshot()
        populated_ctx.metadata["extra"] = "value"
        assert "extra" not in snap.metadata


# ---------------------------------------------------------------------------
# clear_step
# ---------------------------------------------------------------------------

class TestClearStep:
    """Per-step field reset via ``clear_step()``."""

    def test_clear_step_resets_text_decision(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.text_decision is None

    def test_clear_step_resets_vision_decision(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.vision_decision is None

    def test_clear_step_resets_final_action(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.final_action is None

    def test_clear_step_preserves_probe_state(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.probe_state == {"game": "runner", "score": 42}

    def test_clear_step_preserves_screenshot(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.screenshot == b"\x89PNG\r\n\x1a\n"

    def test_clear_step_preserves_visual_struct(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.visual_struct == {"objects": ["player", "enemy"]}

    def test_clear_step_preserves_current_mode(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.current_mode == "hybrid"

    def test_clear_step_preserves_step_number(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.step_number == 5

    def test_clear_step_preserves_errors(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.errors == ["timeout on step 3"]

    def test_clear_step_preserves_metadata(self, populated_ctx: AgentContext) -> None:
        populated_ctx.clear_step()
        assert populated_ctx.metadata == {"game_id": "SSD_00848P01"}


# ---------------------------------------------------------------------------
# Field mutation patterns
# ---------------------------------------------------------------------------

class TestFieldMutation:
    """Everyday manipulation patterns."""

    def test_metadata_dict_access(self) -> None:
        ctx = AgentContext()
        ctx.metadata["game_id"] = "SSD_00848P01"
        ctx.metadata["started_at"] = "2026-07-05T12:00:00"
        assert ctx.metadata["game_id"] == "SSD_00848P01"
        assert ctx.metadata["started_at"] == "2026-07-05T12:00:00"
        assert len(ctx.metadata) == 2

    def test_errors_list_append_and_iterate(self) -> None:
        ctx = AgentContext()
        ctx.errors.append("probe timeout")
        ctx.errors.append("vision parse failure")
        ctx.errors.append("action rejected")
        collected = [e for e in ctx.errors]
        assert collected == ["probe timeout", "vision parse failure", "action rejected"]
        assert len(ctx.errors) == 3

    def test_step_number_increment_pattern(self) -> None:
        ctx = AgentContext()
        assert ctx.step_number == 0
        ctx.step_number += 1
        assert ctx.step_number == 1
        ctx.step_number += 1
        assert ctx.step_number == 2

    def test_probe_state_dict_manipulation(self) -> None:
        ctx = AgentContext()
        ctx.probe_state["score"] = 100
        ctx.probe_state["level"] = 3
        assert ctx.probe_state["score"] == 100
        assert len(ctx.probe_state) == 2
        del ctx.probe_state["score"]
        assert "score" not in ctx.probe_state

    def test_text_decision_assign_and_clear_cycle(self) -> None:
        ctx = AgentContext()
        assert ctx.text_decision is None
        ctx.text_decision = {"action": "tap", "params": {"x": 100, "y": 200}}
        assert ctx.text_decision is not None
        ctx.clear_step()
        assert ctx.text_decision is None
