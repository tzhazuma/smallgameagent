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

from src.agent.rule_update import (
    RuleParameters,
    RuleUpdateApplier,
    RuleUpdateTrigger,
    parse_update_response,
)

if TYPE_CHECKING:
    from src.agent.context import AgentContext

logger = logging.getLogger(__name__)

_L2_SYSTEM = (
    "You are a game strategy planner. Given the current game state JSON, "
    "output a JSON object with a 'plan' array of directly executable intentions "
    "and a 'reason' string. Each intention must be one of:\n"
    '{"action_hint": "tap", "target_name": "<candidate name or path substring>"} — tap the named target\n'
    '{"action_hint": "move", "target_name": "<candidate name or path substring>"} — move toward the named target\n'
    '{"action_hint": "wait", "duration_ms": <int>} — wait\n'
    "Output 3-8 intentions. Use target_name values that appear in the probe state's "
    "guide_or_target_candidates list (name or path fields). If no target is available, output wait. "
    "No markdown fences, no explanation outside JSON."
)

_L2_UPDATE_SYSTEM = (
    "You are a strategy optimizer for a small-game-playing agent. "
    "The agent has three layers: L0 fast rule engine, L1 local VLM for visual hints, "
    "L2 cloud API for long-range planning and rule updates.\n\n"
    "Output a single JSON object (no markdown fences) with this schema:\n"
    '{"update_type": "param|memory_entry|phase_contract|code_file", '
    '"target": "rule_name_or_game_id_or_file", '
    '"reason": "why this update helps", '
    '"payload": {...}, '
    '"confidence": 0.0-1.0}\n\n'
    "For update_type=param, payload is {\"param_name\": value}.\n"
    "For update_type=memory_entry, payload is {\"game_id\", \"phase_id\", \"pattern\", \"success\", \"notes\"}.\n"
    "For update_type=code_file, payload is {\"file_path\", \"search\", \"replace\"}.\n"
    "Code-file updates only apply to allow-listed files; large or low-confidence patches are queued for review.\n"
    "Prefer small, verifiable parameter changes."
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
        rule_params: RuleParameters | None = None,
        strategy_memory: Any | None = None,
        rule_update_allowlist: list[str] | None = None,
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

        # Rule update machinery
        self._rule_params = rule_params or RuleParameters()
        self._rule_applier = RuleUpdateApplier(
            self._rule_params,
            strategy_memory,
            code_file_allowlist=rule_update_allowlist,
        )
        self._rule_trigger = RuleUpdateTrigger()

        # Call counters for metrics
        self.l0_calls: int = 0
        self.l1_calls: int = 0
        self.l2_calls: int = 0
        self.l2_update_calls: int = 0

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

        # --- Rule update trigger (conservative scheme A) ---
        trigger_reason = self._rule_trigger.check(ctx, getattr(ctx.working_memory, "world_model", None))
        if trigger_reason and self._api_client is not None:
            self._run_l2_rule_update(trigger_reason, state, ctx)

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
            resolved = self._resolve_instruction(instruction, state)
            if resolved:
                return resolved
            # If resolution fails, drop this instruction and continue to rule engine
            # (the next instruction remains queued for the next step).

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

    def _resolve_instruction(
        self,
        instruction: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Convert an L2 intention into a concrete action.

        Supports:
        - Named targets: {action_hint: tap/move, target_name: "..."}
        - Legacy coordinates: {action: tap, x, y} / {action: move, dx, dy, duration_ms}
        - Wait: {action: wait, duration_ms}
        """
        action_type = instruction.get("action") or instruction.get("action_hint")
        reason = instruction.get("reason", "")

        if action_type == "wait":
            return {
                "action": "wait",
                "params": {"duration_ms": int(instruction.get("duration_ms", 500))},
                "reason": f"L2_queue:wait:{reason[:40]}",
            }

        # Legacy coordinate format
        if action_type == "tap" and "x" in instruction and "y" in instruction:
            x, y = self._design_to_css(instruction["x"], instruction["y"])
            return {
                "action": "tap",
                "params": {"x": x, "y": y},
                "reason": f"L2_queue:tap:{reason[:40]}",
            }
        if action_type == "move" and ("dx" in instruction or "dy" in instruction):
            return {
                "action": "move",
                "params": {
                    "dx": float(instruction.get("dx", 0)),
                    "dy": float(instruction.get("dy", 0)),
                    "duration_ms": int(instruction.get("duration_ms", 320)),
                },
                "reason": f"L2_queue:move:{reason[:40]}",
            }

        # Named-target format
        target_name = instruction.get("target_name")
        if target_name and action_type in ("tap", "move"):
            candidate = self._resolve_named_target(target_name, state)
            if candidate is None:
                logger.warning("L2 target not found: %s", target_name)
                return None
            x, y = self._design_to_css(candidate["screenPosition"]["x"], candidate["screenPosition"]["y"])
            if action_type == "tap":
                return {
                    "action": "tap",
                    "params": {"x": x, "y": y},
                    "reason": f"L2_queue:tap:{target_name}:{reason[:40]}",
                }
            return {
                "action": "move",
                "params": {
                    "dx": x,
                    "dy": y,
                    "duration_ms": int(instruction.get("duration_ms", 320)),
                },
                "reason": f"L2_queue:move:{target_name}:{reason[:40]}",
            }

        logger.warning("Unsupported L2 instruction: %s", instruction)
        return None

    @staticmethod
    def _resolve_named_target(target_name: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Find a guide_or_target_candidates entry whose name/path matches target_name."""
        candidates = state.get("guide_or_target_candidates") or []
        if not isinstance(candidates, list):
            return None
        target_lower = str(target_name).lower()
        for cand in candidates:
            name = str(cand.get("name", "")).lower()
            path = str(cand.get("path", "")).lower()
            if target_lower in name or target_lower in path or name in target_lower:
                return cand
        return None

    @staticmethod
    def _design_to_css(dx: float, dy: float) -> tuple[float, float]:
        """Convert design-resolution coords (720x1560, bottom-left origin) to CSS viewport (375x812, top-left)."""
        if dx <= 375 and dy <= 812:
            return float(dx), float(dy)
        x = round(dx / 720 * 375, 1)
        y = round((1.0 - dy / 1560) * 812, 1)
        return x, y

    # ------------------------------------------------------------------
    # L2: Cloud API strategic planning
    # ------------------------------------------------------------------

    def _run_l2(self, state: dict[str, Any]) -> None:
        """Call cloud API for a plan of named-target intentions."""
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
                max_tokens=1024,
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
            parsed = _parse_json(text)
            plan = parsed.get("plan") if isinstance(parsed, dict) else None
            instructions = parsed.get("instructions") if isinstance(parsed, dict) else None

            valid: list[dict[str, Any]] = []
            if isinstance(plan, list):
                for item in plan:
                    hint = item.get("action_hint")
                    target = item.get("target_name")
                    if hint in ("tap", "move") and target:
                        valid.append({"action": hint, "target_name": str(target), "reason": item.get("reason", "")})
                    elif hint == "wait":
                        valid.append({"action": "wait", "duration_ms": int(item.get("duration_ms", 500))})
            elif isinstance(instructions, list):
                # Backward compatibility: old coordinate-based format.
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
                self._macro_plan = {"plan": valid, "reason": parsed.get("reason", "") if isinstance(parsed, dict) else ""}
                logger.info("L2 plan: %d items, first=%s", len(valid), valid[0])
            elif isinstance(parsed, dict) and "macro_plan" in parsed:
                self._macro_plan = parsed
                logger.info("L2 macro_plan (legacy): %s", parsed.get("macro_plan", "")[:80])
            else:
                logger.warning("L2 unparseable: %s", text[:120])
        except Exception as exc:
            logger.warning("L2 API call failed: %s", exc)

    # ------------------------------------------------------------------
    # L2: Rule update (conservative scheme A)
    # ------------------------------------------------------------------

    def _run_l2_rule_update(
        self,
        trigger_reason: str,
        state: dict[str, Any],
        ctx: "AgentContext",
    ) -> None:
        """Ask cloud API for a structured rule update and apply it."""
        self.l2_update_calls += 1
        state_snippet = json.dumps(
            {k: state.get(k) for k in
             ("player", "keyNumbers", "keyFlags", "guide_or_target_candidates")
             if k in state},
            default=str, ensure_ascii=False,
        )[:1500]
        visual_context = getattr(ctx, "visual_struct", None) or {}
        try:
            resp = self._api_client.chat(
                [
                    {"role": "system", "content": _L2_UPDATE_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "trigger_reason": trigger_reason,
                                "state": state_snippet,
                                "current_params": self._rule_params.to_dict(),
                                "visual_context": visual_context,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
                max_tokens=512,
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
            request = parse_update_response(text)
            if request is not None:
                applied = self._rule_applier.apply(request)
                if applied:
                    logger.info("Applied rule update: %s", request.to_dict())
                else:
                    logger.warning("Rule update not applied: %s", request.to_dict())
            else:
                logger.warning("L2 rule update unparseable: %s", text[:120])
        except Exception as exc:
            logger.warning("L2 rule update call failed: %s", exc)

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
            "l2_update_calls": self.l2_update_calls,
            "l2_queue_remaining": len(self._l2_queue),
            "macro_plan": self._macro_plan,
            "rule_params": self._rule_params.to_dict(),
            "rule_update_history": self._rule_applier.history(),
        }
