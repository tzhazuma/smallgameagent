"""VLM-based structured visual state extractor for Mode 3.

Takes a screenshot and optional probe state, uses the fine-tuned VLM to
extract structured visual information (arrows, targets, obstacles, UI state),
then returns a normalised dict that can be merged into the probe state for
downstream decision-making (API text model or rule engine).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt for structured visual extraction
# ---------------------------------------------------------------------------

_STRUCT_EXTRACT_PROMPT = """You are a game state visual analyser. Given a screenshot of a mobile game frame, extract structured information about what you see. Return ONLY valid JSON with these fields:

{
  "has_arrow": true/false,
  "arrow_screen_x": float or null (x pixel coordinate of arrow center),
  "arrow_screen_y": float or null (y pixel coordinate),
  "arrow_world_dx": float or null (normalised direction x),
  "arrow_world_dz": float or null (normalised direction z),
  "has_target": true/false,
  "target_screen_x": float or null,
  "target_screen_y": float or null,
  "has_obstacle": true/false,
  "obstacle_screen_x": float or null,
  "obstacle_screen_y": float or null,
  "is_end_screen": true/false,
  "end_screen_type": "win"/"lose"/null,
  "ui_buttons": ["button_text", ...],
  "player_screen_x": float or null,
  "player_screen_y": float or null,
  "has_guide_indicator": true/false,
  "guide_direction": "up"/"down"/"left"/"right"/null,
  "extra_notes": "any other relevant observations"
}"""


def extract_visual_structure(
    vlm_predict_fn,
    screenshot: Any,
    probe_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use a VLM to extract structured visual information from a screenshot.

    Parameters
    ----------
    vlm_predict_fn:
        A callable ``f(screenshot, state_json)`` that returns a dict with
        at least ``action``, ``params``, and ``reason`` keys.  Typically
        ``GameAgentInference.predict()``.
    screenshot:
        PIL Image of the current game frame.
    probe_state:
        Optional probe state dict (used only to pass a dummy state to the
        VLM predict function).

    Returns
    -------
    dict with keys listed in ``_STRUCT_EXTRACT_PROMPT`` above.
    """
    # Build a minimal state containing the extraction instruction
    state_payload = {
        "_extract_mode": True,
        "_prompt": _STRUCT_EXTRACT_PROMPT,
        "probe": probe_state or {},
    }

    try:
        result = vlm_predict_fn(screenshot, state_payload)
        raw = result.get("reason", "")
        # Try to parse JSON from the reason field
        parsed = _parse_json_from_text(raw)
        if parsed:
            return _normalise_struct(parsed)
    except Exception as exc:
        logger.warning("VLM struct extraction failed: %s", exc)

    return _empty_struct()


def extract_visual_structure_from_api(
    api_vision_fn,
    screenshot: Any,
) -> dict[str, Any]:
    """Use an API vision model (Mimo-v2.5) to extract structured visual info.

    Parameters
    ----------
    api_vision_fn:
        A callable ``f(screenshot_path)`` that returns a vision analysis dict.
        Typically ``LLMAgent._think_vision()``.

    Returns
    -------
    dict with visual structure information.
    """
    try:
        result = api_vision_fn(screenshot)
        if isinstance(result, dict):
            return _normalise_struct_from_vision(result)
    except Exception as exc:
        logger.warning("API vision struct extraction failed: %s", exc)

    return _empty_struct()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text, tolerating markdown fences."""
    import re

    if not text or not text.strip():
        return None

    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)

    # Find first JSON object
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalise_struct(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise extracted struct to a canonical schema."""
    return {
        "has_arrow": bool(raw.get("has_arrow", False)),
        "arrow_screen_x": raw.get("arrow_screen_x"),
        "arrow_screen_y": raw.get("arrow_screen_y"),
        "arrow_world_dx": raw.get("arrow_world_dx"),
        "arrow_world_dz": raw.get("arrow_world_dz"),
        "has_target": bool(raw.get("has_target", False)),
        "target_screen_x": raw.get("target_screen_x"),
        "target_screen_y": raw.get("target_screen_y"),
        "has_obstacle": bool(raw.get("has_obstacle", False)),
        "obstacle_screen_x": raw.get("obstacle_screen_x"),
        "obstacle_screen_y": raw.get("obstacle_screen_y"),
        "is_end_screen": bool(raw.get("is_end_screen", False)),
        "end_screen_type": raw.get("end_screen_type"),
        "ui_buttons": raw.get("ui_buttons", []),
        "player_screen_x": raw.get("player_screen_x"),
        "player_screen_y": raw.get("player_screen_y"),
        "has_guide_indicator": bool(raw.get("has_guide_indicator", False)),
        "guide_direction": raw.get("guide_direction"),
        "extra_notes": raw.get("extra_notes", ""),
    }


def _normalise_struct_from_vision(vision_result: dict[str, Any]) -> dict[str, Any]:
    """Convert the existing Mimo vision output format to canonical struct."""
    return {
        "has_arrow": vision_result.get("has_arrow", False),
        "arrow_screen_x": None,
        "arrow_screen_y": None,
        "arrow_world_dx": None,
        "arrow_world_dz": None,
        "has_target": vision_result.get("has_target", False),
        "target_screen_x": None,
        "target_screen_y": None,
        "has_obstacle": vision_result.get("has_obstacle", False),
        "obstacle_screen_x": None,
        "obstacle_screen_y": None,
        "is_end_screen": vision_result.get("is_end_screen", False),
        "end_screen_type": "win" if vision_result.get("is_end_screen") else None,
        "ui_buttons": vision_result.get("ui_buttons", []),
        "player_screen_x": None,
        "player_screen_y": None,
        "has_guide_indicator": vision_result.get("has_arrow", False),
        "guide_direction": vision_result.get("arrow_direction"),
        "extra_notes": vision_result.get("extra_notes", ""),
    }


def _empty_struct() -> dict[str, Any]:
    """Return an empty (all-false) visual structure."""
    return {
        "has_arrow": False,
        "arrow_screen_x": None,
        "arrow_screen_y": None,
        "arrow_world_dx": None,
        "arrow_world_dz": None,
        "has_target": False,
        "target_screen_x": None,
        "target_screen_y": None,
        "has_obstacle": False,
        "obstacle_screen_x": None,
        "obstacle_screen_y": None,
        "is_end_screen": False,
        "end_screen_type": None,
        "ui_buttons": [],
        "player_screen_x": None,
        "player_screen_y": None,
        "has_guide_indicator": False,
        "guide_direction": None,
        "extra_notes": "",
    }
