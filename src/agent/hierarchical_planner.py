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
    "output a JSON object with an 'instructions' array of directly executable actions "
    "and a 'reason' string. Each instruction must be one of:\n"
    '{"action": "tap", "x": <int>, "y": <int>} — tap at screen coordinates\n'
    '{"action": "move", "dx": <float>, "dy": <float>, "duration_ms": <int>} — joystick drag\n'
    '{"action": "wait", "duration_ms": <int>}\n'
    "Output 3-8 instructions. Use screen coordinates from the probe state's "
    "guide_or_target_candidates screenPosition fields (design resolution 720x1560, "
    "bottom-left origin). No markdown fences, no explanation outside JSON."
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
        self._l2_queue: list[dict[str, Any]] = []  # executable instruction queue from L2

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

        # First: consume L2 executable instructions if available
        if self._l2_queue:
            instruction = self._l2_queue.pop(0)
            action = dict(instruction)
            action["reason"] = f"L2_queue:{action.get('action', 'wait')}"
            # Ensure params dict exists
            if "params" not in action:
                action["params"] = {k: v for k, v in action.items()
                                    if k not in ("action", "reason")}
            # Convert design-resolution coords → CSS viewport coords
            # Design: 720x1560, bottom-left origin. CSS: 375x812, top-left origin.
            params = action["params"]
            if action.get("action") == "tap" and "x" in params and "y" in params:
                dx, dy = params["x"], params["y"]
                # Check if coords look like design resolution (>375 wide or >812 tall)
                if dx > 375 or dy > 812:
                    params["x"] = round(dx / 720 * 375, 1)
                    params["y"] = round((1.0 - dy / 1560) * 812, 1)
            return action

        if self._rule_engine is not None:
            action = self._rule_engine.step(state, ctx.visual_struct)
        else:
            action = {"action": "wait", "params": {"duration_ms": 500}, "reason": "no_rule_engine"}

        # Apply tactical override if present
        if self._tactical_override is not None:
            action = self._tactical_override
            self._tactical_override = None  # one-shot

        # Inject L2 context into reason for logging
        if self._l2_queue:
            action = dict(action)
            action["reason"] = f"{action.get('reason', '')}|L2q:{len(self._l2_queue)}left"
        elif self._macro_plan:
            action = dict(action)
            action["reason"] = f"{action.get('reason', '')}|L2:{self._macro_plan.get('reason', '')[:40]}"

        return action

    # ------------------------------------------------------------------
    # L2: Cloud API strategic planning
    # ------------------------------------------------------------------

    def _run_l2(self, state: dict[str, Any]) -> None:
        """Call cloud API for executable instructions."""
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
                    {"role": "user", "content": f"Game state:\n{state_snippet}\n\nInstructions?"},
                ],
                max_tokens=1024,
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
            parsed = _parse_json(text)
            if parsed and "instructions" in parsed:
                instructions = parsed.get("instructions", [])
                # Validate and normalize instructions
                valid = []
                for inst in instructions:
                    action = inst.get("action")
                    if action == "tap" and "x" in inst and "y" in inst:
                        valid.append({"action": "tap", "x": int(inst["x"]), "y": int(inst["y"])})
                    elif action == "move":
                        valid.append({
                            "action": "move",
                            "dx": float(inst.get("dx", 0)),
                            "dy": float(inst.get("dy", 0)),
                            "duration_ms": int(inst.get("duration_ms", 320)),
                        })
                    elif action == "wait":
                        valid.append({"action": "wait", "duration_ms": int(inst.get("duration_ms", 500))})
                if valid:
                    self._l2_queue = valid
                    self._macro_plan = {"instructions": valid, "reason": parsed.get("reason", "")}
                    logger.info("L2 instructions: %d items, first=%s", len(valid), valid[0])
                else:
                    logger.warning("L2 no valid instructions: %s", text[:120])
            elif parsed and "macro_plan" in parsed:
                # Legacy format — keep as context but don't queue
                self._macro_plan = parsed
                logger.info("L2 macro_plan (legacy): %s", parsed.get("macro_plan", "")[:80])
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
            "l2_queue_remaining": len(self._l2_queue),
            "macro_plan": self._macro_plan,
        }
