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

    Two composite trigger modes are supported:

    - **Absolute**: fire when the rolling average drops below
      ``composite_threshold``.
    - **Relative**: fire when the rolling average drops by more than
      ``relative_decrease_pct`` from the peak average observed in this run.
      This avoids over-triggering on games whose baseline is naturally low.

    A hard ``max_updates_per_run`` cap prevents runaway L2 calls.
    """

    def __init__(
        self,
        composite_threshold: float = DEFAULT_COMPOSITE_THRESHOLD,
        stall_threshold: int = DEFAULT_STALL_THRESHOLD,
        conflict_threshold: int = DEFAULT_CONFLICT_THRESHOLD,
        composite_window: int = 5,
        cooldown_steps: int = 8,
        relative_decrease_pct: float | None = None,
        max_updates_per_run: int = 3,
        rule_params: RuleParameters | None = None,
    ) -> None:
        # Store explicit constructor values so they can override runtime_rules.json
        # defaults seeded into rule_params.  A value equal to the class default
        # is treated as "not explicitly set" and defers to rule_params.
        self._init_composite_threshold = composite_threshold
        self._init_stall_threshold = stall_threshold
        self._init_conflict_threshold = conflict_threshold
        self._init_cooldown_steps = cooldown_steps
        self._init_relative_decrease_pct = relative_decrease_pct
        self._init_max_updates_per_run = max_updates_per_run

        self.composite_threshold = composite_threshold
        self.stall_threshold = stall_threshold
        self.conflict_threshold = conflict_threshold
        self.composite_window = composite_window
        self.cooldown_steps = cooldown_steps
        self.relative_decrease_pct = relative_decrease_pct
        self.max_updates_per_run = max_updates_per_run
        self._rule_params = rule_params
        self._composites: list[float] = []
        self._stall_streak: int = 0
        self._conflict_streak: int = 0
        self._last_step: int = -1
        self._last_trigger_step: int = -cooldown_steps - 1
        self._peak_avg: float = 0.0
        self._updates_this_run: int = 0

    def _param(self, name: str, default: Any, init_value: Any = None) -> Any:
        """Read a trigger parameter with priority: explicit init > rule_params > default.

        If *init_value* is provided and differs from *default*, it is treated as
        an explicit override (e.g. from CLI) and returned immediately.  Otherwise
        the shared ``RuleParameters`` store is consulted (allowing L2 online
        tuning), falling back to *default*.
        """
        if init_value is not None and init_value != default:
            return init_value
        if self._rule_params is not None:
            val = self._rule_params.get(name)
            if val is not None:
                return val
        return default

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

        # Read dynamic thresholds with priority: explicit init > rule_params > class default.
        max_updates = int(self._param(
            "trigger_max_updates_per_run", 3, self._init_max_updates_per_run,
        ))
        cooldown = int(self._param(
            "trigger_cooldown_steps", 8, self._init_cooldown_steps,
        ))
        composite_threshold = float(self._param(
            "trigger_composite_threshold", DEFAULT_COMPOSITE_THRESHOLD, self._init_composite_threshold,
        ))
        stall_threshold = int(self._param(
            "trigger_stall_threshold", DEFAULT_STALL_THRESHOLD, self._init_stall_threshold,
        ))
        conflict_threshold = int(self._param(
            "trigger_conflict_threshold", DEFAULT_CONFLICT_THRESHOLD, self._init_conflict_threshold,
        ))
        relative_decrease_pct = self._param(
            "trigger_relative_decrease_pct", None, self._init_relative_decrease_pct,
        )
        if relative_decrease_pct is not None:
            relative_decrease_pct = float(relative_decrease_pct)

        # Hard cap: stop triggering after max_updates_per_run.
        if self._updates_this_run >= max_updates:
            return None

        # Cooldown: do not spam L2 with back-to-back calls.
        if step - self._last_trigger_step < cooldown:
            return None

        wm = getattr(ctx, "working_memory", None) or {}
        if hasattr(wm, "last_composite") and callable(wm.last_composite):
            composite = float(wm.last_composite(self.composite_window))
        elif isinstance(wm, dict):
            composite = float(wm.get("last_composite", 0.0))
        else:
            composite = float(getattr(wm, "last_composite", 0.0))
        stall = int(wm.get("stall_streak", 0) if isinstance(wm, dict) else getattr(wm, "stuck_streak", 0))
        conflict = int(wm.get("conflict_streak", 0) if isinstance(wm, dict) else 0)

        self._composites.append(composite)
        if len(self._composites) > self.composite_window:
            self._composites.pop(0)

        # Reset streaks when condition clears.
        self._stall_streak = 0 if stall == 0 else self._stall_streak + 1
        self._conflict_streak = 0 if conflict == 0 else self._conflict_streak + 1

        avg_composite = sum(self._composites) / max(1, len(self._composites))

        # Track peak for relative-decrease trigger.
        if avg_composite > self._peak_avg:
            self._peak_avg = avg_composite

        # Relative-decrease trigger (preferred when configured).
        if (
            relative_decrease_pct is not None
            and self._peak_avg > 0
            and len(self._composites) >= self.composite_window
        ):
            drop_pct = (self._peak_avg - avg_composite) / self._peak_avg
            if drop_pct >= relative_decrease_pct:
                self._last_trigger_step = step
                self._updates_this_run += 1
                return f"relative_drop_{drop_pct:.1%}_from_peak_{self._peak_avg:.3f}"

        # Absolute threshold trigger.
        if avg_composite < composite_threshold and len(self._composites) >= self.composite_window:
            self._last_trigger_step = step
            self._updates_this_run += 1
            return f"low_composite_avg_{avg_composite:.3f}"
        if self._stall_streak >= stall_threshold:
            self._last_trigger_step = step
            self._updates_this_run += 1
            return f"stall_streak_{self._stall_streak}"
        if self._conflict_streak >= conflict_threshold:
            self._last_trigger_step = step
            self._updates_this_run += 1
            return f"conflict_streak_{self._conflict_streak}"

        # World-model stale event trigger.
        if world_model is not None:
            stats = getattr(world_model, "stats", lambda: {})()
            if stats.get("stale_events", 0) > getattr(self, "_last_stale_events", 0):
                self._last_stale_events = stats.get("stale_events", 0)
                self._last_trigger_step = step
                self._updates_this_run += 1
                return "world_model_stale"

        return None


class RuleUpdateApplier:
    """Apply structured rule updates to parameters, strategy memory, and optionally source files.

    Code-file updates are gated by an explicit allowlist, a high confidence
    threshold, and a maximum patch size.  Any update that does not meet the
    safety criteria is queued in ``pending_code_updates`` for human review
    instead of being applied.

    Parameter snapshots are captured before each applied update so that a
    watchdog can roll back changes that hurt short-horizon performance.
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
        driver_type: str | None = None,
    ) -> None:
        self._params = params
        self._memory = strategy_memory
        self._history: list[dict[str, Any]] = []
        self._snapshots: list[dict[str, Any]] = []
        self._pending_code_updates: list[dict[str, Any]] = []
        self._code_file_allowlist = [Path(p).resolve() for p in (code_file_allowlist or [])]
        self._code_confidence = code_file_confidence_threshold
        self._code_max_patch = code_file_max_patch_chars
        self._code_max_search = code_file_max_search_chars
        self._code_backup_count = code_file_backup_count
        self._driver_type = driver_type or "unknown"

        # Parameters that are unsafe to change for tap-guide games.
        self._TAP_GUIDE_BLOCKED_PARAMS = frozenset({
            "stuck_escape_threshold",
            "escape_score_radius",
        })

    @property
    def pending_code_updates(self) -> list[dict[str, Any]]:
        """Code-file updates that were not auto-applied."""
        return list(self._pending_code_updates)

    def apply(self, request: RuleUpdateRequest) -> bool:
        """Apply one update request.

        Returns ``True`` when something changed, ``False`` when the request
        type is unsupported or malformed.  A parameter snapshot is captured
        before any state-changing update so it can be rolled back.
        """
        if request.confidence < 0.5:
            logger.info("Rule update confidence %.2f too low; skipped", request.confidence)
            return False

        # Snapshot current parameters before applying any stateful update.
        self._snapshots.append({
            "step": getattr(request, "step", None),
            "params": self._params.to_dict(),
            "request": request.to_dict(),
        })

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
        if request.update_type == "none":
            logger.debug("L2 returned no-op update: %s", request.reason)
            return False

        logger.warning("Unsupported rule update type: %s", request.update_type)
        return False

    def rollback_last(self, n: int = 1) -> bool:
        """Revert the last *n* applied parameter updates.

        Returns ``True`` if a rollback occurred.  Memory entries and code-file
        changes are not undone; this focuses on the most common failure mode
        (bad parameter knobs).
        """
        if n <= 0 or not self._snapshots:
            return False
        # Find the snapshot *before* the last n state-changing updates.
        target_index = max(0, len(self._snapshots) - n)
        target = self._snapshots[target_index]
        self._params._params = dict(target["params"])
        # Discard newer snapshots so they cannot be rolled back to again.
        self._snapshots = self._snapshots[: target_index + 1]
        logger.warning("Rolled back %d rule-update(s) to step %s snapshot", n, target.get("step"))
        return True

    def rollback_to_step(self, step: int) -> bool:
        """Revert to the most recent snapshot taken at or before *step*."""
        target = None
        for snap in reversed(self._snapshots):
            if snap.get("step", 0) <= step:
                target = snap
                break
        if target is None:
            return False
        self._params._params = dict(target["params"])
        # Keep snapshots up to and including the target.
        cutoff = next(
            (i for i, s in enumerate(self._snapshots) if s is target),
            len(self._snapshots) - 1,
        )
        self._snapshots = self._snapshots[: cutoff + 1]
        logger.warning("Rolled back to step %s snapshot", target.get("step"))
        return True

    def _apply_param(self, request: RuleUpdateRequest) -> bool:
        payload = request.payload
        if not isinstance(payload, dict):
            logger.warning("Param update payload is not a dict: %s", payload)
            return False

        # Driver-type safety gate: reject changes to blocked params.
        if self._driver_type == "tap-guide":
            blocked = [k for k in payload if k in self._TAP_GUIDE_BLOCKED_PARAMS]
            if blocked:
                logger.warning(
                    "Param update rejected for tap-guide game: %s (blocked params: %s)",
                    request.target, blocked,
                )
                self._queue_pending(request, f"tap_guide_blocked_params:{blocked}")
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


class RuleUpdateWatchdog:
    """Monitor the short-horizon effect of rule updates and roll back bad ones.

    When an update is applied, the watchdog records a pre-update baseline
    (average composite over ``baseline_window`` steps).  It then watches the
    next ``trial_window`` steps.  If the post-update average composite is
    strictly worse than the baseline, **or** activity drops / stall increases
    beyond configured margins, it asks the applier to roll back the last update.

    This implements the "flexible strategy rollback" requested for the
    three-layer architecture: L2 can experiment with rule knobs, but L0/L1
    performance guards prevent a bad cloud suggestion from derailing the run.
    """

    def __init__(
        self,
        applier: RuleUpdateApplier,
        baseline_window: int = 3,
        trial_window: int = 3,
        min_composite_samples: int = 2,
        activity_drop_margin: float = 0.15,
        stall_increase_margin: int = 2,
    ) -> None:
        self._applier = applier
        self._baseline_window = baseline_window
        self._trial_window = trial_window
        self._min_composite_samples = min_composite_samples
        self._activity_drop_margin = activity_drop_margin
        self._stall_increase_margin = stall_increase_margin
        self._baseline_composites: list[float] = []
        self._trial_composites: list[float] = []
        self._baseline_activities: list[float] = []
        self._trial_activities: list[float] = []
        self._baseline_stalls: list[int] = []
        self._trial_stalls: list[int] = []
        self._trialing: bool = False
        self._rollbacks: int = 0

    @property
    def rollbacks(self) -> int:
        return self._rollbacks

    def on_update_applied(self, step: int, composite: float) -> None:
        """Call immediately after the applier applies an update."""
        self._baseline_composites = self._baseline_composites[-self._baseline_window :]
        self._baseline_activities = self._baseline_activities[-self._baseline_window :]
        self._baseline_stalls = self._baseline_stalls[-self._baseline_window :]
        self._trial_composites = []
        self._trial_activities = []
        self._trial_stalls = []
        self._trialing = True
        logger.info(
            "Watchdog started trial at step %d (baseline composites: %s)",
            step,
            self._baseline_composites,
        )

    def observe(
        self,
        step: int,
        composite: float,
        activity: float | None = None,
        stall: int | None = None,
    ) -> bool:
        """Feed one step's metrics into the watchdog.

        Returns ``True`` if a rollback was triggered this step.
        """
        if not self._trialing:
            self._baseline_composites.append(composite)
            if len(self._baseline_composites) > self._baseline_window:
                self._baseline_composites.pop(0)
            if activity is not None:
                self._baseline_activities.append(activity)
                if len(self._baseline_activities) > self._baseline_window:
                    self._baseline_activities.pop(0)
            if stall is not None:
                self._baseline_stalls.append(stall)
                if len(self._baseline_stalls) > self._baseline_window:
                    self._baseline_stalls.pop(0)
            return False

        self._trial_composites.append(composite)
        if activity is not None:
            self._trial_activities.append(activity)
        if stall is not None:
            self._trial_stalls.append(stall)
        if len(self._trial_composites) < self._trial_window:
            return False

        # Trial window full: evaluate.
        self._trialing = False
        if (
            len(self._baseline_composites) < self._min_composite_samples
            or len(self._trial_composites) < self._min_composite_samples
        ):
            return False

        baseline_avg = sum(self._baseline_composites) / len(self._baseline_composites)
        trial_avg = sum(self._trial_composites) / len(self._trial_composites)

        rollback = False
        reasons: list[str] = []

        # Composite degradation.
        if trial_avg < baseline_avg:
            reasons.append(f"composite {trial_avg:.3f} < baseline {baseline_avg:.3f}")
            rollback = True

        # Activity drop.
        if (
            self._baseline_activities
            and self._trial_activities
            and len(self._baseline_activities) >= self._min_composite_samples
            and len(self._trial_activities) >= self._min_composite_samples
        ):
            base_act = sum(self._baseline_activities) / len(self._baseline_activities)
            trial_act = sum(self._trial_activities) / len(self._trial_activities)
            if base_act - trial_act >= self._activity_drop_margin:
                reasons.append(f"activity drop {base_act:.2f}→{trial_act:.2f}")
                rollback = True

        # Stall increase.
        if (
            self._baseline_stalls
            and self._trial_stalls
            and len(self._baseline_stalls) >= self._min_composite_samples
            and len(self._trial_stalls) >= self._min_composite_samples
        ):
            base_stall = sum(self._baseline_stalls) / len(self._baseline_stalls)
            trial_stall = sum(self._trial_stalls) / len(self._trial_stalls)
            if trial_stall - base_stall >= self._stall_increase_margin:
                reasons.append(f"stall increase {base_stall:.1f}→{trial_stall:.1f}")
                rollback = True

        if rollback:
            logger.warning(
                "Watchdog rollback triggered: %s",
                "; ".join(reasons),
            )
            if self._applier.rollback_last(n=1):
                self._rollbacks += 1
                return True
        else:
            logger.info(
                "Watchdog accepted update: trial composite %.3f >= baseline %.3f",
                trial_avg,
                baseline_avg,
            )
        return False


def update_prompt(
    trigger_reason: str,
    state: dict[str, Any],
    params: dict[str, Any],
    visual_context: dict[str, Any] | None = None,
    param_schema: dict[str, Any] | None = None,
    driver_type: str | None = None,
) -> list[dict[str, Any]]:
    """Build a prompt for the cloud L2 model requesting a rule update.

    When *param_schema* is provided it is included in the user message so the
    model knows what each parameter means and what range is valid.
    When *driver_type* is provided, driver-specific safety rules are included.
    """
    safety_rules = ""
    if driver_type == "tap-guide":
        safety_rules = (
            "\n\nCRITICAL SAFETY RULES:\n"
            "- For 'tap-guide' games: Do NOT change stuck_escape_threshold or escape_score_radius. "
            "These parameters control joystick escape behavior which does not exist in tap-guide mode. "
            "Changing them has no effect or may break the tap-guide mechanism. "
            "Focus on trigger/watchdog parameters instead.\n"
        )
    elif driver_type and "joystick" in driver_type.lower():
        safety_rules = (
            "\n\nSAFETY NOTE:\n"
            "- For 'joystick' games: stuck_escape_threshold and escape_score_radius are the primary tunables. "
            "Lower threshold = escape sooner when stuck.\n"
        )
    else:
        safety_rules = (
            "\n\nSAFETY NOTE:\n"
            "- Trigger/watchdog parameters are always safe to adjust for any driver type.\n"
            "- Engine knob changes (stuck_escape_threshold, etc.) should be conservative "
            "and only when the trigger reason clearly relates to that behavior.\n"
        )

    system = (
        "You are a strategy optimizer for a small-game-playing agent. "
        "Your ONLY job is to decide whether to update the agent's rules/parameters. "
        "Do NOT output a gameplay plan, action list, or explanation.\n\n"
        "Respond with a single JSON object (no markdown fences, no thinking tags) exactly matching this schema:\n"
        "{\n"
        '  "update_type": "param|memory_entry|phase_contract|code_file|none",\n'
        '  "target": "rule_name_or_game_id_or_file",\n'
        '  "reason": "why this update helps",\n'
        '  "payload": {...},\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        "Tunable parameters (use these exact names in payload for param updates):\n"
        "- Engine knobs: stuck_escape_threshold (int), target_lock_max_steps (int), coin_save_buffer (float), obstacle_repulse_weight (float), escape_score_radius (float)\n"
        "- Trigger sensitivity: trigger_composite_threshold (float), trigger_stall_threshold (int), trigger_cooldown_steps (int), trigger_relative_decrease_pct (float or null), trigger_max_updates_per_run (int)\n"
        "- Watchdog margins: watchdog_activity_drop_margin (float), watchdog_stall_increase_margin (int)\n"
        + safety_rules +
        "Examples:\n"
        '- Safe tap-guide update: {"update_type":"param","target":"trigger","reason":"too many L2 calls","payload":{"trigger_cooldown_steps":12,"trigger_max_updates_per_run":1},"confidence":0.8}\n'
        '- Safe joystick update: {"update_type":"param","target":"escape","reason":"hero is stuck too often","payload":{"stuck_escape_threshold":3},"confidence":0.85}\n'
        '- To do nothing: {"update_type":"none","target":"","reason":"performance is acceptable","payload":{},"confidence":0.0}\n'
        '- To update a config file: {"update_type":"code_file","target":"runtime_rules.json","reason":"reduce lock time","payload":{"file_path":"configs/runtime_rules.json","search":"\\\"target_lock_max_steps\\\": 8","replace":"\\\"target_lock_max_steps\\\": 5"},"confidence":0.9}\n\n'
        "Rules:\n"
        "1. update_type MUST be one of: param, memory_entry, phase_contract, code_file, none.\n"
        "2. For param updates, payload is a flat dict of parameter names to numeric values.\n"
        "3. Code-file updates only apply to allow-listed files; large or low-confidence patches are queued for review.\n"
        "4. Prefer small, verifiable parameter changes.\n"
        "5. If no update is needed, return update_type=\"none\" with confidence 0.0."
    )
    user: dict[str, Any] = {
        "trigger_reason": trigger_reason,
        "state": state,
        "current_params": params,
        "visual_context": visual_context or {},
    }
    if param_schema:
        user["param_schema"] = param_schema
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
    ]


def parse_update_response(text: str) -> RuleUpdateRequest | None:
    """Best-effort parse of an L2 JSON response into a request object.

    Rejects responses that look like gameplay plans (keys such as ``plan``,
    ``macro_plan``, ``actions``) or that lack a valid ``update_type``.
    """
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
    if not isinstance(data, dict):
        return None
    # Reject plan-format responses that some models emit despite the system prompt.
    if any(k in data for k in ("plan", "macro_plan", "actions", "sub_goals")):
        logger.warning("L2 returned a plan instead of a rule update: %s", text[:120])
        return None
    valid_types = {"param", "memory_entry", "phase_contract", "code_file", "none"}
    if data.get("update_type") not in valid_types:
        logger.warning("L2 rule update missing/invalid update_type: %s", text[:120])
        return None
    try:
        return RuleUpdateRequest.from_dict(data)
    except Exception:
        logger.warning("Malformed rule update response: %s", text[:120])
        return None
