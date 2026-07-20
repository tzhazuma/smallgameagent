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
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default threshold for rule-update trigger.
DEFAULT_COMPOSITE_THRESHOLD = 0.15
DEFAULT_STALL_THRESHOLD = 5
DEFAULT_CONFLICT_THRESHOLD = 3

#: Safety knobs for code-file updates.  These are intentionally conservative:
#: code-file updates are disabled by default (empty allowlist) and require a
#: high confidence + small patch size when enabled.
DEFAULT_CODE_FILE_CONFIDENCE_THRESHOLD = 0.9
DEFAULT_CODE_FILE_MAX_PATCH_CHARS = 2000
DEFAULT_CODE_FILE_MAX_SEARCH_CHARS = 500
DEFAULT_CODE_FILE_BACKUP_COUNT = 3


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
    """Apply structured rule updates to parameters, strategy memory, and optionally source files.

    Code-file updates are gated by an explicit allowlist, a high confidence
    threshold, and a maximum patch size.  Any update that does not meet the
    safety criteria is queued in ``pending_code_updates`` for human review
    instead of being applied.
    """

    def __init__(
        self,
        params: RuleParameters,
        strategy_memory: Any | None = None,
        code_file_allowlist: list[str] | None = None,
        code_file_confidence_threshold: float = DEFAULT_CODE_FILE_CONFIDENCE_THRESHOLD,
        code_file_max_patch_chars: int = DEFAULT_CODE_FILE_MAX_PATCH_CHARS,
        code_file_max_search_chars: int = DEFAULT_CODE_FILE_MAX_SEARCH_CHARS,
        code_file_backup_count: int = DEFAULT_CODE_FILE_BACKUP_COUNT,
    ) -> None:
        self._params = params
        self._memory = strategy_memory
        self._history: list[dict[str, Any]] = []
        self._pending_code_updates: list[dict[str, Any]] = []
        self._code_file_allowlist = [Path(p).resolve() for p in (code_file_allowlist or [])]
        self._code_confidence = code_file_confidence_threshold
        self._code_max_patch = code_file_max_patch_chars
        self._code_max_search = code_file_max_search_chars
        self._code_backup_count = code_file_backup_count

    @property
    def pending_code_updates(self) -> list[dict[str, Any]]:
        """Code-file updates that were not auto-applied."""
        return list(self._pending_code_updates)

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
        if request.update_type == "code_file":
            return self._apply_code_file(request)

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

    def _apply_code_file(self, request: RuleUpdateRequest) -> bool:
        """Apply a code-file patch if safety checks pass.

        The expected payload is::

            {
              "file_path": "configs/runtime_rules.json",
              "search": "<exact existing substring>",
              "replace": "<new substring>"
            }

        If the file is not in the allowlist, the confidence is too low, or the
        patch is too large, the update is queued in ``pending_code_updates``
        instead of being applied.
        """
        payload = request.payload
        if not isinstance(payload, dict):
            logger.warning("code_file payload is not a dict: %s", payload)
            return False

        file_path = payload.get("file_path", "")
        search = payload.get("search", "")
        replace = payload.get("replace", "")
        if not file_path or not isinstance(search, str) or not isinstance(replace, str):
            logger.warning("code_file payload missing file_path/search/replace")
            return False

        path = Path(file_path).resolve()

        # Safety gate 1: allowlist.
        allowed = any(
            path == allowed_path or path.is_relative_to(allowed_path)
            for allowed_path in self._code_file_allowlist
        )
        if not allowed:
            logger.warning("Code-file update rejected: %s not in allowlist", path)
            self._queue_pending(request, "file_not_in_allowlist")
            return False

        # Safety gate 2: confidence.
        if request.confidence < self._code_confidence:
            logger.warning(
                "Code-file update confidence %.2f below threshold %.2f; pending review",
                request.confidence,
                self._code_confidence,
            )
            self._queue_pending(request, "confidence_below_threshold")
            return False

        # Safety gate 3: patch size.
        patch_chars = len(search) + len(replace)
        if patch_chars > self._code_max_patch:
            logger.warning(
                "Code-file update patch too large (%d > %d chars); pending review",
                patch_chars,
                self._code_max_patch,
            )
            self._queue_pending(request, "patch_too_large")
            return False
        if len(search) > self._code_max_search:
            logger.warning(
                "Search block too large (%d > %d chars); pending review",
                len(search),
                self._code_max_search,
            )
            self._queue_pending(request, "search_block_too_large")
            return False

        # Safety gate 4: file must exist and be a regular file inside the repo.
        if not path.is_file():
            logger.warning("Code-file target does not exist: %s", path)
            self._queue_pending(request, "file_not_found")
            return False

        # Apply the patch.
        try:
            original = path.read_text(encoding="utf-8")
            if search not in original:
                logger.warning("Search block not found in %s; pending review", path)
                self._queue_pending(request, "search_block_not_found")
                return False
            if original.count(search) > 1:
                logger.warning("Search block ambiguous in %s; pending review", path)
                self._queue_pending(request, "ambiguous_search_block")
                return False

            new_content = original.replace(search, replace, 1)
            self._backup_file(path)
            path.write_text(new_content, encoding="utf-8")
            self._record(request)
            logger.info(
                "Applied code-file update to %s (+%d/-%d chars)",
                path,
                len(replace),
                len(search),
            )
            return True
        except Exception:
            logger.exception("Failed to apply code-file update to %s", path)
            return False

    def _backup_file(self, path: Path) -> None:
        """Keep up to N backups of a file before modifying it."""
        stem = path.name
        backup_dir = path.parent / ".rule_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Rotate existing backups.
        for i in range(self._code_backup_count - 1, 0, -1):
            src = backup_dir / f"{stem}.{i - 1}.bak"
            if src.is_file():
                shutil.move(str(src), str(backup_dir / f"{stem}.{i}.bak"))
        shutil.copy2(str(path), str(backup_dir / f"{stem}.0.bak"))

    def _queue_pending(self, request: RuleUpdateRequest, reason: str) -> None:
        self._pending_code_updates.append(
            {
                "step": getattr(request, "step", None),
                "pending_reason": reason,
                **request.to_dict(),
            }
        )

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
