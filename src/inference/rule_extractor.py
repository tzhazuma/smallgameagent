"""VLM/API-based rule extractor for Modes 4 & 5.

Uses the fine-tuned VLM or API models (DeepSeek + Mimo) to analyse the
game state and screenshot, then outputs structured gameplay rules that
the ``RuleEngine`` can execute.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.engine.rules import GameRule, RuleSet

logger = logging.getLogger(__name__)

_RULE_EXTRACT_PROMPT = """You are a game strategy analyst. Based on the game screenshot and state below, identify the core gameplay rules. Return ONLY valid JSON:

{
  "game_mechanics": "follow_guide" / "collect_targets" / "avoid_obstacles" / "complete_level" / "unknown",
  "target_type": "arrow" / "backend" / "visual" / "none",
  "obstacle_handling": "rotate_around" / "reroute" / "none",
  "completion_condition": "reach_target" / "button_click" / "none",
  "movement_pattern": "direct" / "waypoint" / "grid",
  "visual_cues": ["cyan_arrow", "ui_button", "end_screen", ...],
  "rules": [
    {
      "name": "rule_name",
      "priority": 0-10,
      "condition": "when to apply this rule",
      "action_type": "move" / "tap" / "wait",
      "action_params": {"dx": 0, "dy": 0, "duration_ms": 320}
    }
  ],
  "extra_notes": ""
}"""


def extract_rules_from_vlm(
    vlm_predict_fn,
    screenshot: Any,
    probe_state: dict[str, Any] | None = None,
) -> RuleSet:
    """Extract gameplay rules from the VLM.

    Parameters
    ----------
    vlm_predict_fn:
        ``GameAgentInference.predict()`` or similar.
    screenshot:
        PIL Image of the current game frame.
    probe_state:
        Current game state from the probe.

    Returns
    -------
    ``RuleSet`` containing extracted rules.
    """
    state_payload = {
        "_extract_rules": True,
        "_prompt": _RULE_EXTRACT_PROMPT,
        "probe": probe_state or {},
    }

    try:
        result = vlm_predict_fn(screenshot, state_payload)
        raw = result.get("reason", "")
        parsed = _parse_rule_json(raw)
        if parsed:
            return _to_ruleset(parsed, source="vlm")
    except Exception as exc:
        logger.warning("VLM rule extraction failed: %s", exc)

    return RuleSet(game_id="unknown", driver_type="follow-guide-audited", source="vlm_fallback")


def extract_rules_from_api(
    text_api_fn,
    vision_api_fn,
    screenshot: Any,
    probe_state: dict[str, Any],
) -> RuleSet:
    """Extract gameplay rules from API models (DeepSeek + Mimo).

    Parameters
    ----------
    text_api_fn:
        ``LLMAgent._think_text()`` or similar.
    vision_api_fn:
        ``LLMAgent._think_vision()`` or similar.
    screenshot:
        PIL Image or screenshot path.
    probe_state:
        Current game state.

    Returns
    -------
    ``RuleSet`` containing extracted rules.
    """
    try:
        # Get text analysis
        text_result = text_api_fn(probe_state, [])

        # Get vision analysis
        vision_result = vision_api_fn(screenshot) if vision_api_fn else {}

        # Synthesize rules from both
        rules: list[GameRule] = []

        # Arrow detection rule
        if isinstance(vision_result, dict) and vision_result.get("has_arrow"):
            direction = vision_result.get("arrow_direction", "none")
            rules.append(GameRule(
                name=f"follow_arrow_{direction}",
                priority=8,
                condition=f"visible arrow pointing {direction}",
                action_template={"action": "move", "params": {"duration_ms": 320}},
            ))

        # End screen detection rule
        if isinstance(vision_result, dict) and vision_result.get("is_end_screen"):
            rules.append(GameRule(
                name="end_screen_detected",
                priority=10,
                condition="end screen UI visible",
                action_template={"action": "wait", "params": {"duration_ms": 1000}},
            ))

        return RuleSet(
            game_id=probe_state.get("_game_id", "unknown"),
            driver_type=probe_state.get("_driver_type", "follow-guide-audited"),
            rules=rules,
            source="api",
        )
    except Exception as exc:
        logger.warning("API rule extraction failed: %s", exc)

    return RuleSet(game_id="unknown", driver_type="follow-guide-audited", source="api_fallback")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_rule_json(text: str) -> dict[str, Any] | None:
    import re
    if not text or not text.strip():
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _to_ruleset(parsed: dict[str, Any], source: str) -> RuleSet:
    rules_raw = parsed.get("rules", [])
    rules = []
    for r in rules_raw:
        rules.append(GameRule(
            name=r.get("name", "unnamed"),
            priority=r.get("priority", 0),
            condition=r.get("condition", ""),
            action_template={
                "action": r.get("action_type", "wait"),
                "params": r.get("action_params", {"duration_ms": 500}),
            },
        ))

    return RuleSet(
        game_id="extracted",
        driver_type=parsed.get("game_mechanics", "follow-guide-audited"),
        rules=rules,
        source=source,
        metadata={
            "target_type": parsed.get("target_type"),
            "obstacle_handling": parsed.get("obstacle_handling"),
            "completion_condition": parsed.get("completion_condition"),
            "visual_cues": parsed.get("visual_cues", []),
        },
    )
