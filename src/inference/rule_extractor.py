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

_RULE_EXTRACT_PROMPT = """Analyze this game screenshot and state. Output a JSON action for your next move. Use this format:
{
  "action": "move" / "tap" / "wait",
  "params": {
    "dx": float (for move, -1 to 1),
    "dy": float (for move, -1 to 1),
    "duration_ms": int
  },
  "reason": "brief explanation"
}
Output ONLY valid JSON, no other text."""


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
        # VLM predict() returns {"action": ..., "params": ..., "reason": ...}
        # Convert directly to a Rule without parsing nested JSON
        vlm_action = result.get("action")
        if vlm_action:
            rule = GameRule(
                name="vlm_action",
                priority=5,
                condition="default",
                action_template={
                    "action": vlm_action,
                    "params": result.get("params", {"duration_ms": 500}),
                },
            )
            return RuleSet(
                game_id="extracted",
                driver_type="follow-guide-audited",
                rules=[rule],
                source="vlm",
                metadata={"reason": result.get("reason", "")},
            )
        # Fallback: try parsing JSON from reason field
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
        # Text analysis is currently unused but kept for side-effect-free API shape.
        _ = text_api_fn(probe_state, [])

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
    # Handle action-based format (new): {"action": "move", "params": {...}}
    action = parsed.get("action")
    if action:
        atype = action
        aparams = parsed.get("params", {"duration_ms": 500})
        rules = [GameRule(
            name="vlm_suggested_action",
            priority=5,
            condition="default",
            action_template={"action": atype, "params": aparams},
        )]
        return RuleSet(
            game_id="extracted",
            driver_type="follow-guide-audited",
            rules=rules,
            source=source,
            metadata={"reason": parsed.get("reason", "")},
        )

    # Legacy format: {"rules": [...], "game_mechanics": ...}
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
