"""VLM-local decision maker — uses a local llama.cpp / LM Studio VLM.

Registers the ``vlm-local`` mode: screenshot + probe state → local VLM →
JSON action.  Falls back to ``wait`` when the VLM is unreachable or the
response cannot be parsed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.agent.registry import BaseDecisionMaker, DecisionRegistry

if TYPE_CHECKING:
    from src.agent.context import AgentContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a game-playing agent. You receive a screenshot and a JSON game state. "
    "Output ONLY a JSON object with keys: action (move|tap|wait), params, reason. "
    "For move: params has dx (-1..1), dy (-1..1), duration_ms. "
    "For tap: params has x, y, duration_ms. "
    "For wait: params has duration_ms. "
    "No markdown fences, no extra text."
)


def _parse_action(text: str) -> dict[str, Any] | None:
    """Extract a JSON action from VLM output, tolerating fences."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    action = obj.get("action")
    if action not in ("move", "tap", "wait"):
        return None
    params = obj.get("params", {})
    if not isinstance(params, dict):
        params = {"duration_ms": 500}
    return {
        "action": action,
        "params": params,
        "reason": obj.get("reason", "vlm_local"),
    }


@DecisionRegistry.register("vlm-local")
class VLMLocalDecisionMaker(BaseDecisionMaker):
    """Decision maker that queries a local VLM via LMStudioClient.

    Parameters
    ----------
    lmstudio_client:
        An ``LMStudioClient`` instance.  When ``None`` a default one is
        created (connects to ``http://127.0.0.1:1234/v1``).
    """

    def __init__(self, lmstudio_client: Any = None, **kwargs: Any) -> None:
        if lmstudio_client is None:
            from src.agent.lmstudio_client import LMStudioClient
            lmstudio_client = LMStudioClient()
        self._client = lmstudio_client

    async def decide(self, ctx: "AgentContext") -> dict[str, Any]:
        """Send screenshot + state to local VLM and parse the action."""
        if ctx.screenshot is None:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "vlm_local_no_screenshot"}

        import base64
        b64 = base64.b64encode(ctx.screenshot).decode("ascii")
        state_snippet = json.dumps(
            {k: ctx.probe_state.get(k) for k in
             ("player", "keyNumbers", "keyFlags", "guide_or_target_candidates")
             if k in ctx.probe_state},
            default=str, ensure_ascii=False,
        )[:1500]

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": f"Game state:\n{state_snippet}\n\nNext action?"},
                ],
            },
        ]

        try:
            resp = self._client.chat_with_vision(messages, max_tokens=2048)
            text = self._client.extract_content(resp)
            action = _parse_action(text)
            if action:
                return action
            logger.warning("vlm-local: unparseable response: %s", text[:200])
        except Exception as exc:
            logger.warning("vlm-local: request failed: %s", exc)

        return {"action": "wait", "params": {"duration_ms": 500},
                "reason": "vlm_local_fallback"}
