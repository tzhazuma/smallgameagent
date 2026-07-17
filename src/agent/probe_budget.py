"""Dynamic probe-budget and adaptive logging for cost-tiered game observations.

Motivation: every agent step pays an observation cost, and the tiers differ by
an order of magnitude — a backend state probe is cheap (P50 ~73 ms) while a
screenshot + VLM pass is expensive (P50 ~397 ms) and dominates log volume
(screenshots account for ~88.7% of log bytes; historical logs reached 8.41 GB).
On top of that, many backend nodes are purely decorative, so reading everything
every step is waste. The team methodology implemented here is:

* **Dynamic probe budget** — observe at the cheapest tier by default and
  escalate only when a trigger fires (:class:`ProbeBudgetManager`).
* **Dynamic log grading** — log sparsely by default and temporarily raise the
  level inside trigger windows (:class:`AdaptiveLogger`).

The module is pure data structures and logic: no browser, no API, no I/O,
stdlib only, and is trivially unit-testable.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

logger = logging.getLogger(__name__)

#: Default estimated observation cost per probe level, in milliseconds.
#: L0 state probe P50 73 ms; L1 component snapshot is a rough mid-tier guess;
#: L2 screenshot + VLM P50 ~397 ms rounded to 400.
DEFAULT_LEVEL_COST_MS: dict[int, float] = {0: 73.0, 1: 150.0, 2: 400.0}

#: Default cooldown: after an escalation, hold the level for this many steps.
DEFAULT_COOLDOWN_STEPS = 3

#: Default budget window length, in steps.
DEFAULT_WINDOW_STEPS = 100

#: Default upper bound on the share of L2 observations within a window.
DEFAULT_L2_MAX_RATIO = 0.20

#: Default decision-confidence threshold below which LOW_CONFIDENCE fires.
DEFAULT_CONFIDENCE_THRESHOLD = 0.4

#: Default actual/expected displacement ratio below which COLLISION_HINT fires.
DEFAULT_COLLISION_RATIO = 0.3

#: Default number of consecutive no-effect steps before ACTION_NO_EFFECT fires.
DEFAULT_NO_EFFECT_STEPS = 2


class ProbeLevel(IntEnum):
    """Observation tiers ordered by cost (cheapest first).

    L0_STATE
        Backend state probe: ``keyNumbers`` / ``keyFlags`` / ``candidates``
        summary. Estimated cost ~73 ms.
    L1_COMPONENTS
        ``snapshotComponents`` component snapshot. Estimated cost ~150 ms.
    L2_SCREENSHOT
        Screenshot + VLM visual pass. Estimated cost ~400 ms.
    """

    L0_STATE = 0
    L1_COMPONENTS = 1
    L2_SCREENSHOT = 2


class EscalationTrigger(Enum):
    """Signals that justify escalating the observation level.

    PHASE_CHANGE
        Candidate set or phase flag changed between two consecutive frames.
    LOW_CONFIDENCE
        Decision confidence fell below the configured threshold.
    ACTION_NO_EFFECT
        Key numbers did not change for a configurable run of steps.
    COLLISION_HINT
        Actual displacement is far smaller than expected (ratio below
        threshold), hinting at an invisible collision.
    SEMANTIC_FLIP
        A boolean in ``keyFlags`` flipped between frames.
    """

    PHASE_CHANGE = "phase_change"
    LOW_CONFIDENCE = "low_confidence"
    ACTION_NO_EFFECT = "action_no_effect"
    COLLISION_HINT = "collision_hint"
    SEMANTIC_FLIP = "semantic_flip"


#: Default trigger -> probe level mapping. Physical-anomaly hints
#: (COLLISION_HINT / ACTION_NO_EFFECT) are usually resolvable from the
#: component tree, so they escalate to L1; semantic/phase uncertainty needs
#: eyes on the screen, so it escalates to L2.
DEFAULT_TRIGGER_LEVELS: dict[EscalationTrigger, ProbeLevel] = {
    EscalationTrigger.COLLISION_HINT: ProbeLevel.L1_COMPONENTS,
    EscalationTrigger.ACTION_NO_EFFECT: ProbeLevel.L1_COMPONENTS,
    EscalationTrigger.PHASE_CHANGE: ProbeLevel.L2_SCREENSHOT,
    EscalationTrigger.LOW_CONFIDENCE: ProbeLevel.L2_SCREENSHOT,
    EscalationTrigger.SEMANTIC_FLIP: ProbeLevel.L2_SCREENSHOT,
}

#: Trigger priority, highest first. Used both to pick the winning trigger when
#: several fire at once and to decide which L2 requests survive a full budget.
#: SEMANTIC_FLIP / PHASE_CHANGE are hard evidence that the world changed;
#: LOW_CONFIDENCE is softer; the rest are physical hints satisfiable at L1.
DEFAULT_TRIGGER_PRIORITY: tuple[EscalationTrigger, ...] = (
    EscalationTrigger.SEMANTIC_FLIP,
    EscalationTrigger.PHASE_CHANGE,
    EscalationTrigger.LOW_CONFIDENCE,
    EscalationTrigger.COLLISION_HINT,
    EscalationTrigger.ACTION_NO_EFFECT,
)

#: Triggers whose L2 requests may evict a previously granted low-priority L2
#: request when the per-window L2 budget is exhausted.
HIGH_PRIORITY_TRIGGERS: frozenset[EscalationTrigger] = frozenset(
    {EscalationTrigger.SEMANTIC_FLIP, EscalationTrigger.PHASE_CHANGE}
)


@dataclass
class TriggerContext:
    """Everything needed to evaluate escalation triggers for one step.

    Parameters
    ----------
    prev_obs : Mapping[str, Any] or None
        Previous frame's observation (may contain ``keyNumbers``, ``keyFlags``,
        ``candidates``, ``phase``).
    curr_obs : Mapping[str, Any] or None
        Current frame's observation, same shape as ``prev_obs``.
    expected_displacement : float or None
        Displacement the taken action was expected to produce.
    actual_displacement : float or None
        Displacement actually observed.
    confidence : float or None
        Decision confidence of the current step, in ``[0, 1]``.
    consecutive_no_effect : int
        Number of consecutive steps so far in which key numbers did not move.
    """

    prev_obs: Mapping[str, Any] | None = None
    curr_obs: Mapping[str, Any] | None = None
    expected_displacement: float | None = None
    actual_displacement: float | None = None
    confidence: float | None = None
    consecutive_no_effect: int = 0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> TriggerContext:
        """Build a context from an arbitrary mapping, ignoring unknown keys.

        Parameters
        ----------
        data : Mapping[str, Any]
            Duck-typed context dict, e.g. a world-model payload.

        Returns
        -------
        TriggerContext
            Context populated from the known keys present in ``data``.
        """
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _flags(obs: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the ``keyFlags`` mapping of an observation, or an empty dict."""
    if not obs:
        return {}
    flags = obs.get("keyFlags")
    return flags if isinstance(flags, Mapping) else {}


def evaluate_triggers(
    context: TriggerContext,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    collision_ratio: float = DEFAULT_COLLISION_RATIO,
    no_effect_steps: int = DEFAULT_NO_EFFECT_STEPS,
) -> set[EscalationTrigger]:
    """Evaluate all escalation triggers against one step's context.

    Parameters
    ----------
    context : TriggerContext
        The step's evaluation inputs.
    confidence_threshold : float
        LOW_CONFIDENCE fires when ``confidence`` is strictly below this.
    collision_ratio : float
        COLLISION_HINT fires when ``actual / expected`` is strictly below
        this (and ``expected`` is positive).
    no_effect_steps : int
        ACTION_NO_EFFECT fires when ``consecutive_no_effect`` reaches this.

    Returns
    -------
    set of EscalationTrigger
        Every trigger that fired; empty when nothing is suspicious.
    """
    hits: set[EscalationTrigger] = set()
    prev, curr = context.prev_obs, context.curr_obs

    if prev is not None and curr is not None:
        prev_candidates = set(prev.get("candidates") or ())
        curr_candidates = set(curr.get("candidates") or ())
        if prev_candidates != curr_candidates or prev.get("phase") != curr.get("phase"):
            hits.add(EscalationTrigger.PHASE_CHANGE)

        prev_flags, curr_flags = _flags(prev), _flags(curr)
        for name in prev_flags.keys() & curr_flags.keys():
            old, new = prev_flags[name], curr_flags[name]
            if isinstance(old, bool) and isinstance(new, bool) and old != new:
                hits.add(EscalationTrigger.SEMANTIC_FLIP)
                break

    if context.confidence is not None and context.confidence < confidence_threshold:
        hits.add(EscalationTrigger.LOW_CONFIDENCE)

    if context.consecutive_no_effect >= no_effect_steps:
        hits.add(EscalationTrigger.ACTION_NO_EFFECT)

    expected, actual = context.expected_displacement, context.actual_displacement
    if expected is not None and actual is not None and expected > 0:
        if actual / expected < collision_ratio:
            hits.add(EscalationTrigger.COLLISION_HINT)

    return hits


@dataclass
class ProbeBudgetConfig:
    """Tunables for :class:`ProbeBudgetManager`.

    Parameters
    ----------
    level_cost_ms : dict[int, float]
        Estimated cost of each probe level in milliseconds.
    cooldown_steps : int
        Steps an escalated level is held before falling back to L0.
    window_steps : int
        Budget window length in steps.
    l2_max_ratio : float
        Maximum share of L2 decisions allowed per window.
    trigger_levels : dict[EscalationTrigger, ProbeLevel]
        Trigger -> escalation level mapping.
    trigger_priority : tuple of EscalationTrigger
        Trigger priority, highest first.
    confidence_threshold : float
        Forwarded to :func:`evaluate_triggers`.
    collision_ratio : float
        Forwarded to :func:`evaluate_triggers`.
    no_effect_steps : int
        Forwarded to :func:`evaluate_triggers`.
    """

    level_cost_ms: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_LEVEL_COST_MS))
    cooldown_steps: int = DEFAULT_COOLDOWN_STEPS
    window_steps: int = DEFAULT_WINDOW_STEPS
    l2_max_ratio: float = DEFAULT_L2_MAX_RATIO
    trigger_levels: dict[EscalationTrigger, ProbeLevel] = field(
        default_factory=lambda: dict(DEFAULT_TRIGGER_LEVELS)
    )
    trigger_priority: tuple[EscalationTrigger, ...] = DEFAULT_TRIGGER_PRIORITY
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    collision_ratio: float = DEFAULT_COLLISION_RATIO
    no_effect_steps: int = DEFAULT_NO_EFFECT_STEPS


class ProbeBudgetManager:
    """Decides the per-step observation level under an L2 budget.

    Default level is L0. When triggers fire, the level escalates per the
    configured mapping and is held for ``cooldown_steps`` steps (anti-flap).
    Within each ``window_steps`` window at most ``l2_max_ratio`` of decisions
    may be L2; once the window's L2 budget is exhausted, low-priority L2
    requests are downgraded to L1 and counted as suppressed, while
    high-priority ones (:data:`HIGH_PRIORITY_TRIGGERS`) may evict a previously
    granted low-priority L2 request (retroactively counted as suppressed).
    """

    def __init__(self, config: ProbeBudgetConfig | None = None) -> None:
        self.config = config or ProbeBudgetConfig()
        self._priority_rank = {t: i for i, t in enumerate(self.config.trigger_priority)}
        # Cooldown hold state.
        self._hold_level: ProbeLevel = ProbeLevel.L0_STATE
        self._hold_until: int = -1
        # Per-window budget state.
        self._window_index: int = -1
        self._window_l2_used: int = 0
        self._window_l2_low_granted: int = 0
        self._l2_cap: int = max(1, int(self.config.window_steps * self.config.l2_max_ratio))
        # Lifetime stats.
        self._level_counts: dict[ProbeLevel, int] = {level: 0 for level in ProbeLevel}
        self._trigger_hits: dict[EscalationTrigger, int] = {t: 0 for t in EscalationTrigger}
        self._suppressed: int = 0
        self._obs_count: int = 0
        self._obs_latency_ms: float = 0.0
        self._obs_bytes: int = 0

    def _advance_window(self, step: int) -> None:
        """Reset per-window budget counters when ``step`` enters a new window."""
        index = step // self.config.window_steps
        if index != self._window_index:
            self._window_index = index
            self._window_l2_used = 0
            self._window_l2_low_granted = 0

    def _admit_l2(self, high_priority: bool) -> bool:
        """Try to spend one L2 slot of the current window.

        Parameters
        ----------
        high_priority : bool
            Whether the winning trigger is in :data:`HIGH_PRIORITY_TRIGGERS`.

        Returns
        -------
        bool
            True when the L2 request is granted (possibly by evicting an
            earlier low-priority grant), False when it must be downgraded.
        """
        if self._window_l2_used < self._l2_cap:
            self._window_l2_used += 1
            if not high_priority:
                self._window_l2_low_granted += 1
            return True
        if high_priority and self._window_l2_low_granted > 0:
            # Evict the oldest low-priority grant: it is retroactively
            # downgraded so the totals still respect the cap.
            self._window_l2_low_granted -= 1
            self._suppressed += 1
            self._level_counts[ProbeLevel.L2_SCREENSHOT] -= 1
            self._level_counts[ProbeLevel.L1_COMPONENTS] += 1
            return True
        return False

    def decide_level(
        self, step: int, context: TriggerContext | Mapping[str, Any] | None
    ) -> ProbeLevel:
        """Pick the observation level for one step.

        Parameters
        ----------
        step : int
            Zero-based step index; drives windowing and cooldown.
        context : TriggerContext or Mapping[str, Any] or None
            Trigger evaluation inputs. Any duck-typed mapping is accepted
            (unknown keys are ignored), so world-model payloads can be passed
            straight through.

        Returns
        -------
        ProbeLevel
            The level to observe at for this step.
        """
        if context is None:
            ctx = TriggerContext()
        elif isinstance(context, TriggerContext):
            ctx = context
        elif isinstance(context, Mapping):
            ctx = TriggerContext.from_mapping(context)
        else:
            raise TypeError(f"unsupported context type: {type(context)!r}")

        self._advance_window(step)
        triggers = evaluate_triggers(
            ctx,
            confidence_threshold=self.config.confidence_threshold,
            collision_ratio=self.config.collision_ratio,
            no_effect_steps=self.config.no_effect_steps,
        )
        for trigger in triggers:
            self._trigger_hits[trigger] += 1

        level = ProbeLevel.L0_STATE
        if triggers:
            best = min(triggers, key=lambda t: self._priority_rank.get(t, len(self._priority_rank)))
            desired = max(self.config.trigger_levels.get(t, ProbeLevel.L0_STATE) for t in triggers)
            if desired >= ProbeLevel.L2_SCREENSHOT:
                if not self._admit_l2(best in HIGH_PRIORITY_TRIGGERS):
                    desired = ProbeLevel.L1_COMPONENTS
                    self._suppressed += 1
            # Do not downgrade below an active hold (anti-flap).
            if step <= self._hold_until:
                desired = max(desired, self._hold_level)
            level = desired
            self._hold_level = level
            self._hold_until = step + self.config.cooldown_steps
        elif step <= self._hold_until:
            level = self._hold_level

        self._level_counts[level] += 1
        return level

    def record_observation(self, level: ProbeLevel, latency_ms: float, bytes_written: int) -> None:
        """Record the measured cost of one performed observation.

        Parameters
        ----------
        level : ProbeLevel
            Level the observation was taken at.
        latency_ms : float
            Measured wall-clock cost in milliseconds.
        bytes_written : int
            Bytes persisted for this observation (screenshots dominate).
        """
        self._obs_count += 1
        self._obs_latency_ms += latency_ms
        self._obs_bytes += bytes_written

    def stats(self) -> dict[str, Any]:
        """Summarize usage, cost and savings against an all-L2 baseline.

        Returns
        -------
        dict
            ``decisions`` / ``level_counts`` / ``level_share`` per decided
            step; ``avg_observed_latency_ms`` and ``total_bytes_written`` from
            :meth:`record_observation`; ``trigger_hits`` distribution;
            ``suppressed`` count; ``estimated_cost_ms`` versus
            ``baseline_all_l2_ms`` and the derived
            ``estimated_savings_ratio``.
        """
        decisions = sum(self._level_counts.values())
        costs = self.config.level_cost_ms
        estimated = sum(self._level_counts[level] * costs[int(level)] for level in ProbeLevel)
        baseline = decisions * costs[int(ProbeLevel.L2_SCREENSHOT)]
        return {
            "decisions": decisions,
            "level_counts": {level.name: self._level_counts[level] for level in ProbeLevel},
            "level_share": {
                level.name: (self._level_counts[level] / decisions if decisions else 0.0)
                for level in ProbeLevel
            },
            "avg_observed_latency_ms": (
                self._obs_latency_ms / self._obs_count if self._obs_count else 0.0
            ),
            "total_bytes_written": self._obs_bytes,
            "trigger_hits": {t.name: n for t, n in self._trigger_hits.items()},
            "suppressed": self._suppressed,
            "estimated_cost_ms": estimated,
            "baseline_all_l2_ms": baseline,
            "estimated_savings_ratio": (1.0 - estimated / baseline) if baseline else 0.0,
        }


#: Recognized adaptive-log levels.
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "TRACE_DUMP")

#: Default capacity of the in-memory DEBUG ring buffer.
DEFAULT_BUFFER_CAPACITY = 200

#: Default length of an escalation window, in steps.
DEFAULT_ESCALATE_STEPS = 3


@dataclass
class LogRecord:
    """One logged entry.

    Parameters
    ----------
    step : int
        Step the entry was emitted at.
    level : str
        One of ``DEBUG`` / ``INFO`` / ``TRACE_DUMP``.
    message : str
        Human-readable message.
    payload : Any
        Optional structured payload (e.g. a screenshot reference).
    size_bytes : int
        Estimated persisted size: message plus payload, in bytes.
    """

    step: int
    level: str
    message: str
    payload: Any
    size_bytes: int


class AdaptiveLogger:
    """Leveled logger that only persists DEBUG output inside trigger windows.

    Levels:

    * ``DEBUG`` — kept only in the in-memory ring buffer, unless an
      escalation window is active, in which case it is also persisted.
    * ``INFO`` — always persisted.
    * ``TRACE_DUMP`` — always persisted; intended for full dumps (including
      screenshot references) inside trigger windows.

    Persistence is modeled by the in-memory :attr:`persisted` list so the
    class stays I/O-free and unit-testable. Per-step cost is O(1): the ring
    buffer is a ``collections.deque(maxlen=...)``.
    """

    def __init__(
        self,
        capacity: int = DEFAULT_BUFFER_CAPACITY,
        escalate_steps: int = DEFAULT_ESCALATE_STEPS,
    ) -> None:
        self.capacity = capacity
        self.escalate_steps = escalate_steps
        self._buffer: deque[LogRecord] = deque(maxlen=capacity)
        self._persisted: list[LogRecord] = []
        self._persisted_bytes: int = 0
        self._window_end: int = -1
        self._window_count: int = 0
        self._last_step: int = 0

    @property
    def persisted(self) -> list[LogRecord]:
        """Records that would have been written to disk, in order."""
        return self._persisted

    @property
    def buffer(self) -> deque[LogRecord]:
        """The in-memory DEBUG ring buffer (oldest entries dropped on overflow)."""
        return self._buffer

    def escalate(self, steps: int | None = None) -> None:
        """Open (or extend) an escalation window starting at the last seen step.

        Parameters
        ----------
        steps : int or None
            Window length in steps; defaults to ``self.escalate_steps``.
        """
        length = self.escalate_steps if steps is None else steps
        self._window_end = max(self._window_end, self._last_step + length)
        self._window_count += 1

    def _in_window(self, step: int) -> bool:
        return step <= self._window_end

    def log(self, step: int, level_hint: str, message: str, payload: Any = None) -> LogRecord:
        """Emit one log entry.

        Parameters
        ----------
        step : int
            Current step index; drives escalation-window expiry.
        level_hint : str
            ``DEBUG`` / ``INFO`` / ``TRACE_DUMP`` (case-insensitive).
        message : str
            Human-readable message.
        payload : Any
            Optional structured payload; its size counts toward persisted
            bytes.

        Returns
        -------
        LogRecord
            The created record.
        """
        level = level_hint.upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"unknown log level: {level_hint!r}")
        self._last_step = step
        size = len(message.encode("utf-8"))
        if payload is not None:
            size += len(str(payload).encode("utf-8"))
        record = LogRecord(
            step=step, level=level, message=message, payload=payload, size_bytes=size
        )
        if level == "DEBUG":
            self._buffer.append(record)
            if self._in_window(step):
                self._persisted.append(record)
                self._persisted_bytes += size
        else:
            self._persisted.append(record)
            self._persisted_bytes += size
        return record

    def stats(self) -> dict[str, Any]:
        """Summarize logging volume.

        Returns
        -------
        dict
            ``persisted_count`` / ``persisted_bytes`` for what would hit disk,
            ``buffered_count`` for the live ring buffer,
            ``escalation_windows`` for how many windows were opened, and
            ``avg_bytes_per_record`` as the mean persisted record size.
        """
        count = len(self._persisted)
        return {
            "persisted_count": count,
            "persisted_bytes": self._persisted_bytes,
            "buffered_count": len(self._buffer),
            "escalation_windows": self._window_count,
            "avg_bytes_per_record": (self._persisted_bytes / count if count else 0.0),
        }
