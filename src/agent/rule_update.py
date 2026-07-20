"""Online rule-update machinery for the hierarchical agent.

This module implements the conservative "scheme A" path: the cloud L2 model
produces a structured rule-update JSON, and this layer applies it to in-memory
parameters and/or the file-backed :class:`StrategyMemory`.  It never rewrites
source code on disk unless explicitly configured to do so.

Triggers and application are intentionally separated so that triggers can be
unit-tested with synthetic contexts, and the LLM call can be mocked.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Default threshold for rule-update trigger.
DEFAULT_COMPOSITE_THRESHOLD = 0.15
DEFAULT_STALL_THRESHOLD = 5
DEFAULT_CONFLICT_THRESHOLD = 3


@dataclass
class RuleUpdateRequest:
    """Structured request produced by L2 and consumed by the applier."""

    update_type: str
    target: str
    reason: str
    payload: dict[str, Any]
    confidence: float = 0.0
    safety: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_type": self.update_type,
            "target": self.target,
            "reason": self.reason,
            "payload": self.payload,
            "confidence": self.confidence,
            "safety": self.safety,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleUpdateRequest":
        return cls(
            update_type=str(data.get("update_type", "param")),
            target=str(data.get("target", "")),
            reason=str(data.get("reason", "")),
            payload=dict(data.get("payload", {})),
            confidence=float(data.get("confidence", 0.0)),
            safety=dict(data.get("safety", {})) if data.get("safety") else None,
        )


class RuleParameters:
    """In-memory parameter store for rule-level knobs.

    The rule engine can read these parameters on every step; updates applied
    here take effect immediately without modifying source files.
    """

    def __init__(self, defaults: dict[str, Any] | None = None) -> None:
        self._params: dict[str, Any] = dict(defaults or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._params[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._params.update(values)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._params)


class RuleUpdateTrigger:
    """Decide when to ask L2 for a rule update.

    The trigger maintains a small rolling window of recent composites, stall
    counts, and L0/L2 conflicts.  When any threshold is crossed it returns a
    reason string; otherwise it returns ``None``.
    """

    def __init__(
        self,
        composite_threshold: float = DEFAULT_COMPOSITE_THRESHOLD,
        stall_threshold: int = DEFAULT_STALL_THRESHOLD,
        conflict_threshold: int = DEFAULT_CONFLICT_THRESHOLD,
        composite_window: int = 5,
    ) -> None:
        self.composite_threshold = composite_threshold
        self.stall_threshold = stall_threshold
        self.conflict_threshold = conflict_threshold
        self.composite_window = composite_window
        self._composites: list[float] = []
        self._stall_streak: int = 0
        self._conflict_streak: int = 0
        self._last_step: int = -1

    def check(
        self,
        ctx: Any,
        world_model: Any | None = None,
    ) -> str | None:
        """Return a trigger reason or ``None`` if no update is needed."""
        step = int(getattr(ctx, "step_number", 0))
        if step == self._last_step:
            return None
        self._last_step = step

        wm = getattr(ctx, "working_memory", None) or {}
        composite = float(wm.get("last_composite", 0.0) if isinstance(wm, dict) else 0.0)
        stall = int(wm.get("stall_streak", 0) if isinstance(wm, dict) else 0)
        conflict = int(wm.get("conflict_streak", 0) if isinstance(wm, dict) else 0)

        self._composites.append(composite)
        if len(self._composites) > self.composite_window:
            self._composites.pop(0)

        # Reset streaks when condition clears.
        self._stall_streak = 0 if stall == 0 else self._stall_streak + 1
        self._conflict_streak = 0 if conflict == 0 else self._conflict_streak + 1

        avg_composite = sum(self._composites) / max(1, len(self._composites))
        if avg_composite < self.composite_threshold and len(self._composites) >= self.composite_window:
            return f"low_composite_avg_{avg_composite:.3f}"
        if self._stall_streak >= self.stall_threshold:
            return f"stall_streak_{self._stall_streak}"
        if self._conflict_streak >= self.conflict_threshold:
            return f"conflict_streak_{self._conflict_streak}"

        # World-model stale event trigger.
        if world_model is not None:
            stats = getattr(world_model, "stats", lambda: {})()
            if stats.get("stale_events", 0) > getattr(self, "_last_stale_events", 0):
                self._last_stale_events = stats.get("stale_events", 0)
                return "world_model_stale"

        return None


class RuleUpdateApplier:
    """Apply structured rule updates to parameters and strategy memory."""

    def __init__(
        self,
        params: RuleParameters,
        strategy_memory: Any | None = None,
    ) -> None:
        self._params = params
        self._memory = strategy_memory
        self._history: list[dict[str, Any]] = []

    def apply(self, request: RuleUpdateRequest) -> bool:
        """Apply one update request.

        Returns ``True`` when something changed, ``False`` when the request
        type is unsupported or malformed.
        """
        if request.confidence < 0.5:
            logger.info("Rule update confidence %.2f too low; skipped", request.confidence)
            return False

        if request.update_type == "param":
            return self._apply_param(request)
        if request.update_type == "memory_entry":
            return self._apply_memory_entry(request)
        if request.update_type == "phase_contract":
            # Phase contracts are stored in parameters under a namespaced key.
            self._params.set(f"phase_contract:{request.target}", request.payload)
            self._record(request)
            return True

        logger.warning("Unsupported rule update type: %s", request.update_type)
        return False

    def _apply_param(self, request: RuleUpdateRequest) -> bool:
        payload = request.payload
        if not isinstance(payload, dict):
            logger.warning("Param update payload is not a dict: %s", payload)
            return False
        self._params.update(payload)
        self._record(request)
        logger.info("Applied param update %s: %s", request.target, payload)
        return True

    def _apply_memory_entry(self, request: RuleUpdateRequest) -> bool:
        if self._memory is None:
            logger.warning("StrategyMemory not available; cannot apply memory_entry")
            return False
        payload = request.payload
        game_id = payload.get("game_id", request.target)
        phase_id = payload.get("phase_id", "default")
        pattern = payload.get("pattern", {})
        success = bool(payload.get("success", True))
        notes = payload.get("notes", request.reason)
        try:
            self._memory.record(game_id, phase_id, pattern, success, notes)
            self._record(request)
            logger.info("Applied memory entry for %s/%s", game_id, phase_id)
            return True
        except Exception:
            logger.exception("Failed to record memory entry")
            return False

    def _record(self, request: RuleUpdateRequest) -> None:
        self._history.append({"step": getattr(request, "step", None), **request.to_dict()})

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)


def update_prompt(
    trigger_reason: str,
    state: dict[str, Any],
    params: dict[str, Any],
    visual_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a prompt for the cloud L2 model requesting a rule update."""
    system = (
        "You are a strategy optimizer for a small-game-playing agent. "
        "The agent has three layers: L0 fast rule engine, L1 local VLM for visual hints, "
        "L2 cloud API for long-range planning and rule updates.\n\n"
        "Output a single JSON object (no markdown fences) with this schema:\n"
        '{"update_type": "param|memory_entry|phase_contract", '
        '"target": "rule_name_or_game_id", '
        '"reason": "why this update helps", '
        '"payload": {...}, '
        '"confidence": 0.0-1.0}\n\n'
        "For update_type=param, payload is {\"param_name\": value}.\n"
        "For update_type=memory_entry, payload is {\"game_id\", \"phase_id\", \"pattern\", \"success\", \"notes\"}.\n"
        "Prefer small, verifiable parameter changes."
    )
    user = {
        "trigger_reason": trigger_reason,
        "state": state,
        "current_params": params,
        "visual_context": visual_context or {},
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def parse_update_response(text: str) -> RuleUpdateRequest | None:
    """Best-effort parse of an L2 JSON response into a request object."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    try:
        return RuleUpdateRequest.from_dict(data)
    except Exception:
        logger.warning("Malformed rule update response: %s", text[:120])
        return None
