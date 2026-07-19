"""Hierarchical multi-agent planner — three-layer decision architecture.

L0 (execution): Rule engine runs every step (~0 ms).
L1 (tactical):  Local VLM (gemma-4-E4B) runs every N steps or when stuck (~5 s).
L2 (strategic): Cloud API (kimi-k2.7-code) runs every M steps or on phase change (~3 s).

The idea: cloud API does long-range planning (macro-plan), local VLM does
short-range tactical corrections, and the rule engine executes at zero latency.
This amortises the expensive model calls while keeping per-step latency near zero.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.context import AgentContext

logger = logging.getLogger(__name__)

_L2_SYSTEM = (
    "You are a game strategy planner. Given the current game state JSON, "
    "output a JSON object with keys: macro_plan (string), sub_goals (list of strings), "
    "priority (string). Keep it concise. No markdown fences."
)

_L1_SYSTEM = (
    "You are a game tactical advisor. Given a screenshot and game state, "
    "decide if the current action should be overridden. Output JSON: "
    '{"override": null} to keep the rule engine action, or '
    '{"override": {"action": "move|tap|wait", "params": {...}, "reason": "..."}} '
    "to replace it. No markdown fences."
)


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


class HierarchicalPlanner:
    """Three-layer hierarchical decision planner.

    Parameters
    ----------
    rule_engine:
        The L0 rule engine for zero-latency execution.
    api_client:
        Cloud API client for L2 strategic planning.
    lmstudio_client:
        Local VLM client for L1 tactical corrections.
    l1_interval:
        How often (in steps) to invoke L1.
    l2_interval:
        How often (in steps) to invoke L2.
    stuck_threshold:
        Stuck streak that triggers an L1 call.
    """

    def __init__(
        self,
        rule_engine: Any = None,
        api_client: Any = None,
        lmstudio_client: Any = None,
        l1_interval: int = 5,
        l2_interval: int = 15,
        stuck_threshold: int = 3,
    ) -> None:
        self._rule_engine = rule_engine
        self._api_client = api_client
        self._lmstudio_client = lmstudio_client
        self._l1_interval = l1_interval
        self._l2_interval = l2_interval
        self._stuck_threshold = stuck_threshold

        # Cached plans
        self._macro_plan: dict[str, Any] | None = None
        self._tactical_override: dict[str, Any] | None = None
        self._last_phase: str = ""

        # Call counters for metrics
        self.l0_calls: int = 0
        self.l1_calls: int = 0
        self.l2_calls: int = 0

    def step(self, ctx: "AgentContext") -> dict[str, Any]:
        """Produce one action using the three-layer hierarchy."""
        step = ctx.step_number
        state = ctx.probe_state or {}
        stuck = getattr(ctx.working_memory, "stuck_streak", 0) if ctx.working_memory else 0

        # --- L2: Strategic planning (cloud API) ---
        phase = self._current_phase(state)
        need_l2 = (
            step % self._l2_interval == 0
            or phase != self._last_phase
        )
        if need_l2 and self._api_client is not None:
            self._last_phase = phase
            self._run_l2(state)

        # --- L1: Tactical correction (local VLM) ---
        need_l1 = (
            step % self._l1_interval == 0
            or stuck >= self._stuck_threshold
        )
        if need_l1 and self._lmstudio_client is not None and ctx.screenshot is not None:
            self._run_l1(ctx)
        elif not need_l1:
            self._tactical_override = None

        # --- L0: Rule engine execution ---
        self.l0_calls += 1
        if self._rule_engine is not None:
            action = self._rule_engine.step(state, ctx.visual_struct)
        else:
            action = {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_rule_engine"}

        # Apply tactical override if present
        if self._tactical_override is not None:
            action = self._tactical_override
            self._tactical_override = None  # one-shot

        # Inject macro-plan context into reason for logging
        if self._macro_plan:
            action = dict(action)
            action["reason"] = f"{action.get('reason', '')}|L2:{self._macro_plan.get('macro_plan', '')[:40]}"

        return action

    # ------------------------------------------------------------------
    # L2: Cloud API strategic planning
    # ------------------------------------------------------------------

    def _run_l2(self, state: dict[str, Any]) -> None:
        """Call cloud API for macro-plan."""
        self.l2_calls += 1
        state_snippet = json.dumps(
            {k: state.get(k) for k in
             ("player", "keyNumbers", "keyFlags", "guide_or_target_candidates")
             if k in state},
            default=str, ensure_ascii=False,
        )[:2000]
        try:
            resp = self._api_client.chat(
                [
                    {"role": "system", "content": _L2_SYSTEM},
                    {"role": "user", "content": f"Game state:\n{state_snippet}\n\nPlan?"},
                ],
                max_tokens=512,
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
            parsed = _parse_json(text)
            if parsed and "macro_plan" in parsed:
                self._macro_plan = parsed
                logger.info("L2 macro_plan: %s", parsed.get("macro_plan", "")[:80])
            else:
                logger.warning("L2 unparseable: %s", text[:120])
        except Exception as exc:
            logger.warning("L2 API call failed: %s", exc)

    # ------------------------------------------------------------------
    # L1: Local VLM tactical correction
    # ------------------------------------------------------------------

    def _run_l1(self, ctx: "AgentContext") -> None:
        """Call local VLM for tactical override."""
        self.l1_calls += 1
        import base64
        b64 = base64.b64encode(ctx.screenshot).decode("ascii")
        state_snippet = json.dumps(
            {k: ctx.probe_state.get(k) for k in
             ("player", "keyNumbers", "keyFlags")
             if k in ctx.probe_state},
            default=str, ensure_ascii=False,
        )[:1000]
        try:
            resp = self._lmstudio_client.chat_with_vision(
                [
                    {"role": "system", "content": _L1_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": f"State:\n{state_snippet}\n\nOverride?"},
                        ],
                    },
                ],
                max_tokens=512,
            )
            text = self._lmstudio_client.extract_content(resp)
            parsed = _parse_json(text)
            if parsed and parsed.get("override") is not None:
                ov = parsed["override"]
                if isinstance(ov, dict) and ov.get("action") in ("move", "tap", "wait"):
                    params = ov.get("params", {})
                    if not isinstance(params, dict):
                        params = {"duration_ms": 500}
                    self._tactical_override = {
                        "action": ov["action"],
                        "params": params,
                        "reason": f"L1:{ov.get('reason', '')[:60]}",
                    }
                    logger.info("L1 override: %s", ov.get("action"))
        except Exception as exc:
            logger.warning("L1 VLM call failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _current_phase(state: dict[str, Any]) -> str:
        """Derive a simple phase string from state."""
        if state.get("win"):
            return "win"
        if state.get("done"):
            return "done"
        numbers = state.get("keyNumbers") or {}
        first_key = next(iter(numbers), "")
        return f"play:{first_key}" if first_key else "play"

    def stats(self) -> dict[str, Any]:
        """Return call counters for metrics."""
        return {
            "l0_calls": self.l0_calls,
            "l1_calls": self.l1_calls,
            "l2_calls": self.l2_calls,
            "macro_plan": self._macro_plan,
        }
