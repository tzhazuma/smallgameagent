"""Phase contracts and three-layer time alignment for game-agent consistency.

Motivation: multiple unsynchronized sources of truth for the same physical
quantity caused temporal-consistency ("时间一致性") failures in real runs —
e.g. a temperature reading plunging 73% -> 36% between adjacent frames, and
``decision.temp`` vs ``delta.temp`` deviating by more than 0.05 in 68.9% of
steps. On top of that, later strategies silently rewrote steps committed by
earlier strategies, so previously made progress could not be trusted.

This module implements the team's methodology as pure data structures and
logic (stdlib only, no I/O, trivially unit-testable):

- **Three-layer timestamps** (:class:`TimestampedValue`): every value carries
  ``event_time`` (事件时间, when it happened in the world), ``observed_at``
  (实体时间, when the agent observed it), and ``settled_at`` (策略时间, when
  the value was confirmed stable enough to plan against). Two values may only
  be compared directly when they point at the *same moment of truth*
  (:func:`align_tolerance`).
- **Cross-field consistency** (:class:`FieldConsistencyChecker`): synonymous
  field groups (e.g. ``decision.temp`` / ``delta.temp`` / ``obs.temp``) are
  checked per frame; deviations beyond the group tolerance are recorded as
  :class:`ConsistencyViolation`.
- **Phase contracts** (:class:`PhaseContract` + :class:`PhaseGate`): a phase
  declares preconditions, an action whitelist, success predicates and a
  timeout. The gate acts as the commit gate: success is accepted only after a
  settle re-check window, while in-phase guard breaches and timeouts end the
  phase as ``VIOLATED`` / ``TIMEOUT``.
- **Protected prefix locking** (:func:`compute_prefix_hash` /
  :func:`verify_prefix`): previously committed strategy steps are hash-locked;
  rewriting them raises :class:`PrefixViolation`.
- **Failure classification** (:func:`classify_failure`): failures are graded
  by side effects into ``ROLLBACK`` / ``COMPENSATION`` / ``STOP_REPLAN``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

#: Clock mode: integer game steps.
CLOCK_STEP = "step"
#: Clock mode: float seconds from a monotonic clock.
CLOCK_MONOTONIC = "monotonic"

#: Supported predicate operators for :func:`evaluate_predicate`.
PREDICATE_OPS = frozenset({"eq", "ne", "gt", "lt", "ge", "le", "truthy"})

#: Valid ``on_failure`` strategies for :class:`PhaseContract`.
ON_FAILURE_STRATEGIES = frozenset({"rollback", "compensation", "stop_replan"})

#: Length of the hex digest returned by :func:`compute_prefix_hash`.
PREFIX_HASH_LENGTH = 16


# --------------------------------------------------------------------- paths


def get_path(obs: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve a dot-separated path against a nested observation mapping.

    Parameters
    ----------
    obs : Mapping[str, Any]
        Observation dict, e.g. ``{"decision": {"temp": 0.73}}``.
    path : str
        Dot-separated path, e.g. ``"decision.temp"``.

    Returns
    -------
    tuple[bool, Any]
        ``(found, value)``; ``found`` is ``False`` when any segment is missing
        or an intermediate value is not a mapping.
    """
    current: Any = obs
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def evaluate_predicate(pred: Mapping[str, Any], obs: Mapping[str, Any]) -> bool:
    """Evaluate one contract predicate against an observation frame.

    A predicate is a mapping of the form
    ``{"path": "keyFlags.hasNet", "op": "eq|ne|gt|lt|ge|le|truthy", "value": ...}``.
    ``value`` is ignored for ``truthy``. A missing path evaluates to ``False``
    (never raises), and ordered comparisons on incomparable types also yield
    ``False``.

    Parameters
    ----------
    pred : Mapping[str, Any]
        Predicate spec with keys ``path``, ``op`` and optionally ``value``.
    obs : Mapping[str, Any]
        Observation frame.

    Returns
    -------
    bool

    Raises
    ------
    ValueError
        If ``op`` is not one of :data:`PREDICATE_OPS`.
    """
    op = str(pred.get("op", "truthy"))
    if op not in PREDICATE_OPS:
        raise ValueError(f"unknown predicate op: {op!r}")
    found, value = get_path(obs, str(pred.get("path", "")))
    if not found:
        return False
    if op == "truthy":
        return bool(value)
    expected = pred.get("value")
    try:
        if op == "eq":
            return bool(value == expected)
        if op == "ne":
            return bool(value != expected)
        if op == "gt":
            return bool(value > expected)
        if op == "lt":
            return bool(value < expected)
        if op == "ge":
            return bool(value >= expected)
        return bool(value <= expected)
    except TypeError:
        return False


# --------------------------------------------------------- three-layer time


@dataclass
class TimestampedValue:
    """A value annotated with the three time layers used for alignment.

    Parameters
    ----------
    value : Any
        The observed quantity (number, bool, str; kept JSON-serializable).
    event_time : float | int
        事件时间 — when the fact became true in the world.
    observed_at : float | int
        实体时间 — when the agent observed the fact.
    settled_at : float | int | None
        策略时间 — when the value was confirmed stable enough to plan against;
        ``None`` while unsettled.
    source : str
        Origin of the reading, e.g. ``"probe"``, ``"decision"``, ``"delta"``.
    unit : str | None
        Unit of the value, e.g. ``"ratio"``, ``"count"``. Values with
        different units are never considered directly comparable.
    clock : str
        Clock mode declared at construction: ``"step"`` (int game steps) or
        ``"monotonic"`` (float seconds from a monotonic clock). In ``"step"``
        mode all times must be ints; in ``"monotonic"`` mode they are coerced
        to float.
    """

    value: Any
    event_time: float | int
    observed_at: float | int
    settled_at: float | int | None = None
    source: str = "unknown"
    unit: str | None = None
    clock: str = CLOCK_STEP

    def __post_init__(self) -> None:
        if self.clock not in (CLOCK_STEP, CLOCK_MONOTONIC):
            raise ValueError(f"unknown clock mode: {self.clock!r}")
        for name in ("event_time", "observed_at", "settled_at"):
            t = getattr(self, name)
            if t is None:
                if name == "settled_at":
                    continue
                raise TypeError(f"{name} is required, got None")
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                raise TypeError(f"{name} must be numeric, got {t!r}")
            if self.clock == CLOCK_STEP and not isinstance(t, int):
                raise TypeError(f"{name} must be an int step in 'step' clock mode, got {t!r}")
            if self.clock == CLOCK_MONOTONIC:
                setattr(self, name, float(t))

    @property
    def observation_delay(self) -> float | int:
        """Lag between the world event and its observation."""
        return self.observed_at - self.event_time

    def is_stale(self, max_age: float, now: float | int) -> bool:
        """Return whether the underlying truth is older than ``max_age``.

        Staleness is judged on ``event_time`` (the age of the truth the value
        claims), not on when the agent happened to observe it.

        Parameters
        ----------
        max_age : float
            Maximum acceptable age in the value's clock units.
        now : float | int
            Current time in the same clock mode.
        """
        return (now - self.event_time) > max_age

    def aligns_with(self, other: TimestampedValue, tolerance: float) -> bool:
        """Return whether ``other`` points at the same moment of truth.

        See :func:`align_tolerance`.
        """
        return align_tolerance(self, other, tolerance)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "value": self.value,
            "event_time": self.event_time,
            "observed_at": self.observed_at,
            "settled_at": self.settled_at,
            "source": self.source,
            "unit": self.unit,
            "clock": self.clock,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TimestampedValue:
        """Rebuild a :class:`TimestampedValue` from :meth:`to_dict` output."""
        return cls(
            value=data.get("value"),
            event_time=data["event_time"],
            observed_at=data["observed_at"],
            settled_at=data.get("settled_at"),
            source=str(data.get("source", "unknown")),
            unit=data.get("unit"),
            clock=str(data.get("clock", CLOCK_STEP)),
        )


def align_tolerance(a: TimestampedValue, b: TimestampedValue, tolerance: float) -> bool:
    """Return whether two timestamped values may be compared directly.

    Two values point at the same moment of truth only when their event times
    differ by at most ``tolerance``. Values on different clocks can never be
    aligned (programming error), and values with different declared units are
    not comparable.

    Parameters
    ----------
    a, b : TimestampedValue
        Values to align.
    tolerance : float
        Maximum allowed ``|a.event_time - b.event_time|``.

    Returns
    -------
    bool

    Raises
    ------
    ValueError
        If ``a`` and ``b`` use different clock modes.
    """
    if a.clock != b.clock:
        raise ValueError(f"clock mismatch: {a.clock!r} vs {b.clock!r}")
    if a.unit is not None and b.unit is not None and a.unit != b.unit:
        logger.warning("unit mismatch: %r vs %r — values not comparable", a.unit, b.unit)
        return False
    return abs(a.event_time - b.event_time) <= tolerance


# ------------------------------------------------------- field consistency


@dataclass
class ConsistencyViolation:
    """One detected cross-field inconsistency within a single frame.

    Parameters
    ----------
    group : str
        Name of the synonymous field group, e.g. ``"temp"``.
    step : int
        Step at which the violation was detected.
    values : dict[str, Any]
        Resolved values per field path, e.g.
        ``{"decision.temp": 0.73, "delta.temp": 0.36}``.
    deviation : float
        Observed spread (``max - min`` for numbers; ``1.0`` for disagreeing
        bools, ``0.0`` for agreeing ones).
    tolerance : float
        Tolerance that was exceeded.
    unit : str | None
        Unit declared for the group.
    """

    group: str
    step: int
    values: dict[str, Any]
    deviation: float
    tolerance: float
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "group": self.group,
            "step": self.step,
            "values": dict(self.values),
            "deviation": self.deviation,
            "tolerance": self.tolerance,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConsistencyViolation:
        """Rebuild a :class:`ConsistencyViolation` from :meth:`to_dict` output."""
        return cls(
            group=str(data["group"]),
            step=int(data["step"]),
            values=dict(data.get("values", {})),
            deviation=float(data["deviation"]),
            tolerance=float(data["tolerance"]),
            unit=data.get("unit"),
        )


@dataclass
class _FieldGroup:
    """A registered set of synonymous field paths with a shared tolerance."""

    name: str
    paths: list[str]
    tolerance: float
    unit: str | None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _deviation(values: list[Any]) -> float:
    """Compute the spread of one group's values.

    Numbers use ``max - min``; bools (and any other non-numeric values) use
    ``0.0`` when all equal and ``1.0`` otherwise.
    """
    if all(isinstance(v, bool) for v in values):
        return 0.0 if len(set(values)) == 1 else 1.0
    if all(_is_number(v) for v in values):
        nums = [float(v) for v in values]
        return max(nums) - min(nums)
    return 0.0 if all(v == values[0] for v in values) else 1.0


class FieldConsistencyChecker:
    """Checks synonymous field groups for per-frame deviations.

    Register groups of dot-paths that are supposed to carry the same physical
    quantity (e.g. ``["decision.temp", "delta.temp", "obs.temp"]``), then feed
    observation frames via :meth:`check_frame`. Every frame whose group spread
    exceeds the group tolerance is recorded as a :class:`ConsistencyViolation`.
    """

    def __init__(self) -> None:
        self._groups: dict[str, _FieldGroup] = {}
        self.violations: list[ConsistencyViolation] = []

    def register_group(
        self,
        name: str,
        paths: Sequence[str],
        tolerance: float,
        unit: str | None = None,
    ) -> None:
        """Register a synonymous field group.

        Parameters
        ----------
        name : str
            Group identifier, e.g. ``"temp"``. Re-registering overwrites.
        paths : Sequence[str]
            Dot-paths expected to agree, e.g. ``["decision.temp", "delta.temp"]``.
        tolerance : float
            Maximum allowed spread (exclusive — a spread strictly greater than
            this is a violation). Use ``0`` for bool groups.
        unit : str | None
            Unit shared by the group, recorded on violations.
        """
        self._groups[str(name)] = _FieldGroup(
            name=str(name), paths=[str(p) for p in paths], tolerance=float(tolerance), unit=unit
        )

    def check_frame(self, obs: Mapping[str, Any], step: int) -> list[ConsistencyViolation]:
        """Check all registered groups against one observation frame.

        Groups with fewer than two resolvable paths in the frame are skipped.

        Parameters
        ----------
        obs : Mapping[str, Any]
            One observation frame.
        step : int
            Current game step, recorded on violations.

        Returns
        -------
        list[ConsistencyViolation]
            Violations detected in this frame (also appended to
            :attr:`violations`).
        """
        new_violations: list[ConsistencyViolation] = []
        for group in self._groups.values():
            present: dict[str, Any] = {}
            for path in group.paths:
                found, value = get_path(obs, path)
                if found:
                    present[path] = value
            if len(present) < 2:
                continue
            deviation = _deviation(list(present.values()))
            if deviation > group.tolerance:
                violation = ConsistencyViolation(
                    group=group.name,
                    step=step,
                    values=present,
                    deviation=deviation,
                    tolerance=group.tolerance,
                    unit=group.unit,
                )
                self.violations.append(violation)
                new_violations.append(violation)
                logger.warning(
                    "step %d: field group %r inconsistent (deviation %.4g > %.4g): %s",
                    step,
                    group.name,
                    deviation,
                    group.tolerance,
                    present,
                )
        return new_violations

    @property
    def violation_count(self) -> int:
        """Total number of recorded violations."""
        return len(self.violations)

    def stats(self) -> dict[str, Any]:
        """Return counters for rubrics/reports."""
        return {"group_count": len(self._groups), "violation_count": self.violation_count}


# ---------------------------------------------------------- phase contract


class PhaseStatus(StrEnum):
    """Lifecycle status of a phase under a :class:`PhaseGate`."""

    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    VIOLATED = "violated"


#: Statuses from which a phase never resumes without a fresh ``enter()``.
TERMINAL_STATUSES = frozenset({PhaseStatus.SUCCESS, PhaseStatus.TIMEOUT, PhaseStatus.VIOLATED})


class FailureAction(StrEnum):
    """Graded failure handling, matching ``PhaseContract.on_failure`` values."""

    ROLLBACK = "rollback"
    COMPENSATION = "compensation"
    STOP_REPLAN = "stop_replan"


@dataclass
class PhaseContract:
    """Declarative contract governing one phase of a strategy.

    Parameters
    ----------
    phase_id : str
        Unique phase identifier.
    preconditions : list[dict]
        Predicates (see :func:`evaluate_predicate`) that must all hold to
        *enter* the phase. They double as in-phase guards: if any of them
        stops holding while the phase is running, the gate marks the phase
        ``VIOLATED`` (e.g. a temperature guard ``{"path": "obs.temp",
        "op": "le", "value": 0.8}``).
    allowed_actions : list[str] | None
        Action-name whitelist enforced while running; ``None`` means no
        restriction.
    success_predicate : list[dict]
        Predicates that must all hold for the phase to complete. An empty
        list means the phase never completes on its own (it can only time out
        or be violated).
    timeout_steps : int
        Maximum phase length in steps; ``tick`` at
        ``entered_step + timeout_steps`` or later ends the phase ``TIMEOUT``.
    on_failure : str
        Failure strategy, one of ``"rollback"`` / ``"compensation"`` /
        ``"stop_replan"`` (see :class:`FailureAction`).
    protected_prefix_hash : str | None
        Expected hash of the protected strategy-step prefix, as produced by
        :func:`compute_prefix_hash`; ``None`` disables prefix locking.
    """

    phase_id: str
    preconditions: list[dict] = field(default_factory=list)
    allowed_actions: list[str] | None = None
    success_predicate: list[dict] = field(default_factory=list)
    timeout_steps: int = 100
    on_failure: str = "stop_replan"
    protected_prefix_hash: str | None = None

    def __post_init__(self) -> None:
        if self.on_failure not in ON_FAILURE_STRATEGIES:
            raise ValueError(f"unknown on_failure strategy: {self.on_failure!r}")
        for kind, preds in (("preconditions", self.preconditions),
                            ("success_predicate", self.success_predicate)):
            for pred in preds:
                op = pred.get("op", "truthy") if isinstance(pred, Mapping) else None
                if op not in PREDICATE_OPS:
                    raise ValueError(f"unknown predicate op in {kind}: {op!r}")
                if not isinstance(pred, Mapping) or "path" not in pred:
                    raise ValueError(f"predicate in {kind} missing 'path': {pred!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "phase_id": self.phase_id,
            "preconditions": [dict(p) for p in self.preconditions],
            "allowed_actions": list(self.allowed_actions) if self.allowed_actions else None,
            "success_predicate": [dict(p) for p in self.success_predicate],
            "timeout_steps": self.timeout_steps,
            "on_failure": self.on_failure,
            "protected_prefix_hash": self.protected_prefix_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PhaseContract:
        """Rebuild a :class:`PhaseContract` from :meth:`to_dict` output."""
        return cls(
            phase_id=str(data["phase_id"]),
            preconditions=[dict(p) for p in data.get("preconditions", [])],
            allowed_actions=(
                [str(a) for a in data["allowed_actions"]]
                if data.get("allowed_actions") is not None
                else None
            ),
            success_predicate=[dict(p) for p in data.get("success_predicate", [])],
            timeout_steps=int(data.get("timeout_steps", 100)),
            on_failure=str(data.get("on_failure", "stop_replan")),
            protected_prefix_hash=data.get("protected_prefix_hash"),
        )


@dataclass
class PhaseEvent:
    """One state migration recorded by :class:`PhaseGate`.

    Parameters
    ----------
    phase_id : str
        Phase this event belongs to.
    step : int
        Step at which the migration happened.
    from_status : str
        Status before the migration (``PhaseStatus`` value).
    to_status : str
        Status after the migration (``PhaseStatus`` value).
    reason : str
        Human-readable cause, e.g. ``"entered phase"`` or the failed guard.
    """

    phase_id: str
    step: int
    from_status: str
    to_status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "phase_id": self.phase_id,
            "step": self.step,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PhaseEvent:
        """Rebuild a :class:`PhaseEvent` from :meth:`to_dict` output."""
        return cls(
            phase_id=str(data["phase_id"]),
            step=int(data["step"]),
            from_status=str(data["from_status"]),
            to_status=str(data["to_status"]),
            reason=str(data["reason"]),
        )


class PhaseGate:
    """State machine enforcing a :class:`PhaseContract` (the commit gate).

    Parameters
    ----------
    settle_delay_steps : int
        Settle re-check window for success acceptance: the first time all
        success predicates hold, the gate stays ``RUNNING`` and only accepts
        ``SUCCESS`` if they still hold at a tick at least this many steps
        later. If the predicates drop before the re-check, the pending success
        is cancelled and the phase continues as ``RUNNING``. ``0`` accepts
        success immediately. Kept on the gate (not the contract) because it is
        an acceptance-policy knob, not part of the phase declaration.
    """

    def __init__(self, settle_delay_steps: int = 1) -> None:
        if settle_delay_steps < 0:
            raise ValueError("settle_delay_steps must be >= 0")
        self.settle_delay_steps = int(settle_delay_steps)
        self._status = PhaseStatus.IDLE
        self._contract: PhaseContract | None = None
        self._entered_step = 0
        self._pending_success_step: int | None = None
        self.events: list[PhaseEvent] = []
        self.blocked_actions = 0

    @property
    def status(self) -> PhaseStatus:
        """Current lifecycle status."""
        return self._status

    @property
    def active_contract(self) -> PhaseContract | None:
        """The contract currently governing the gate, if any."""
        return self._contract

    @property
    def entered_step(self) -> int:
        """Step at which the current phase was entered."""
        return self._entered_step

    @property
    def pending_success_step(self) -> int | None:
        """Step of the first (yet unsettled) success observation, if any."""
        return self._pending_success_step

    def enter(self, contract: PhaseContract, obs: Mapping[str, Any], step: int) -> list[str]:
        """Attempt to enter a phase under ``contract``.

        All preconditions must hold in ``obs``. On success the gate resets and
        transitions to ``RUNNING`` (re-entering from any state is allowed and
        restarts the phase).

        Parameters
        ----------
        contract : PhaseContract
            Contract to govern the phase.
        obs : Mapping[str, Any]
            Current observation frame.
        step : int
            Current game step.

        Returns
        -------
        list[str]
            Rejection reasons, one per failed precondition; empty list means
            the phase was entered.
        """
        reasons = [
            f"precondition not satisfied: {pred}"
            for pred in contract.preconditions
            if not evaluate_predicate(pred, obs)
        ]
        if reasons:
            logger.info(
                "step %d: entry into phase %r rejected: %s", step, contract.phase_id, reasons
            )
            return reasons
        self._contract = contract
        self._entered_step = step
        self._pending_success_step = None
        self._transition(PhaseStatus.RUNNING, step, "entered phase")
        return []

    def check_action_allowed(self, action: str) -> bool:
        """Return whether ``action`` is permitted in the current phase.

        Actions are unrestricted when no phase is running or the contract has
        no whitelist (``allowed_actions is None``). Blocked attempts are
        counted in :attr:`blocked_actions` and logged.
        """
        contract = self._contract
        if self._status != PhaseStatus.RUNNING or contract is None:
            return True
        if contract.allowed_actions is None:
            return True
        allowed = action in contract.allowed_actions
        if not allowed:
            self.blocked_actions += 1
            logger.warning(
                "action %r blocked by phase %r (allowed: %s)",
                action,
                contract.phase_id,
                contract.allowed_actions,
            )
        return allowed

    def tick(self, obs: Mapping[str, Any], step: int) -> PhaseStatus:
        """Advance the state machine with one observation frame.

        Evaluation order per tick: guards (contract preconditions) ->
        ``VIOLATED``; success predicates (with settle re-check) -> ``SUCCESS``;
        timeout -> ``TIMEOUT``. Ticks on a terminal status are no-ops.

        Parameters
        ----------
        obs : Mapping[str, Any]
            Current observation frame.
        step : int
            Current game step.

        Returns
        -------
        PhaseStatus

        Raises
        ------
        RuntimeError
            If no phase has been entered yet.
        """
        contract = self._contract
        if contract is None:
            raise RuntimeError("no active phase; call enter() first")
        if self._status in TERMINAL_STATUSES:
            return self._status

        failed_guards = [
            pred for pred in contract.preconditions if not evaluate_predicate(pred, obs)
        ]
        if failed_guards:
            self._transition(PhaseStatus.VIOLATED, step, f"guard(s) breached: {failed_guards}")
            return self._status

        if contract.success_predicate and all(
            evaluate_predicate(pred, obs) for pred in contract.success_predicate
        ):
            if self._pending_success_step is None:
                self._pending_success_step = step
                logger.info(
                    "step %d: phase %r success first observed; settle re-check at step >= %d",
                    step,
                    contract.phase_id,
                    step + self.settle_delay_steps,
                )
            if step - self._pending_success_step >= self.settle_delay_steps:
                self._transition(PhaseStatus.SUCCESS, step, "success predicates settled")
                return self._status
        elif self._pending_success_step is not None:
            logger.info(
                "step %d: phase %r settle re-check failed; back to RUNNING",
                step,
                contract.phase_id,
            )
            self._pending_success_step = None

        if step - self._entered_step >= contract.timeout_steps:
            self._transition(PhaseStatus.TIMEOUT, step, f"timeout after {contract.timeout_steps}")
        return self._status

    def _transition(self, to: PhaseStatus, step: int, reason: str) -> None:
        event = PhaseEvent(
            phase_id=self._contract.phase_id if self._contract else "",
            step=step,
            from_status=self._status.value,
            to_status=to.value,
            reason=reason,
        )
        self.events.append(event)
        logger.info(
            "phase %r: %s -> %s at step %d (%s)",
            event.phase_id,
            event.from_status,
            event.to_status,
            step,
            reason,
        )
        self._status = to


# ------------------------------------------------------- prefix protection


class PrefixViolation(Exception):
    """Raised when previously committed strategy steps were rewritten.

    Attributes
    ----------
    expected_hash : str
        Hash committed earlier.
    actual_hash : str
        Hash recomputed over the current steps.
    prefix_len : int | None
        Protected prefix length used for hashing (``None`` = all steps).
    """

    def __init__(self, expected_hash: str, actual_hash: str, prefix_len: int | None) -> None:
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.prefix_len = prefix_len
        super().__init__(
            f"protected prefix rewritten: expected hash {expected_hash}, "
            f"got {actual_hash} (prefix_len={prefix_len})"
        )


def _canonical_json(steps: Sequence[Mapping[str, Any]]) -> str:
    """Serialize strategy steps to a canonical JSON string for hashing."""
    return json.dumps(list(steps), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_prefix_hash(
    strategy_steps: Sequence[Mapping[str, Any]], prefix_len: int | None = None
) -> str:
    """Hash the protected prefix of a strategy-step sequence.

    The steps are serialized as canonical JSON (sorted keys, compact
    separators) and hashed with sha256; the first 16 hex chars are returned.

    Parameters
    ----------
    strategy_steps : Sequence[Mapping[str, Any]]
        Ordered strategy steps; each step must be JSON-serializable.
    prefix_len : int | None
        By convention only the first ``prefix_len`` steps are protected;
        ``None`` protects all given steps. Appending steps beyond the
        protected prefix never changes the hash.

    Returns
    -------
    str
        First 16 hex characters of the sha256 digest.
    """
    steps = list(strategy_steps)
    prefix = steps if prefix_len is None else steps[:prefix_len]
    digest = hashlib.sha256(_canonical_json(prefix).encode("utf-8")).hexdigest()
    return digest[:PREFIX_HASH_LENGTH]


def verify_prefix(
    strategy_steps: Sequence[Mapping[str, Any]],
    expected_hash: str,
    prefix_len: int | None = None,
) -> bool:
    """Verify that the protected prefix of ``strategy_steps`` is untouched.

    Parameters
    ----------
    strategy_steps : Sequence[Mapping[str, Any]]
        Current strategy steps (may include appended steps beyond the
        protected prefix).
    expected_hash : str
        Hash committed earlier via :func:`compute_prefix_hash`.
    prefix_len : int | None
        Protected prefix length; must match the length used when the expected
        hash was computed.

    Returns
    -------
    bool
        ``True`` when the prefix matches.

    Raises
    ------
    PrefixViolation
        If the recomputed prefix hash differs from ``expected_hash``.
    """
    actual = compute_prefix_hash(strategy_steps, prefix_len)
    if actual != expected_hash:
        logger.error(
            "protected prefix rewritten: expected %s, got %s (prefix_len=%s)",
            expected_hash,
            actual,
            prefix_len,
        )
        raise PrefixViolation(expected_hash, actual, prefix_len)
    return True


# ------------------------------------------------------ failure classification


@dataclass
class FailureContext:
    """Facts about a failed phase used for failure grading.

    Parameters
    ----------
    side_effects : list[str]
        Descriptions of partial side effects already applied (e.g.
        ``["net_cast", "bait_consumed"]``). Empty means the failure was
        side-effect free.
    world_advanced : bool
        Whether the world has irreversibly advanced past the point the phase
        was planned against (e.g. scene epoch changed, resources consumed).
    """

    side_effects: list[str] = field(default_factory=list)
    world_advanced: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {"side_effects": list(self.side_effects), "world_advanced": self.world_advanced}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FailureContext:
        """Rebuild a :class:`FailureContext` from :meth:`to_dict` output."""
        return cls(
            side_effects=[str(s) for s in data.get("side_effects", [])],
            world_advanced=bool(data.get("world_advanced", False)),
        )


def classify_failure(context: FailureContext) -> FailureAction:
    """Grade a failure by its side effects.

    Rules (most severe first):

    - world already advanced -> :attr:`FailureAction.STOP_REPLAN` (stop and
      request re-planning; rollback/compensation are unsafe or meaningless);
    - partial side effects but world not advanced ->
      :attr:`FailureAction.COMPENSATION` (run a compensation action sequence);
    - no side effects -> :attr:`FailureAction.ROLLBACK` (restore the snapshot
      taken at phase entry).

    Parameters
    ----------
    context : FailureContext
        Side-effect facts about the failure.

    Returns
    -------
    FailureAction
    """
    if context.world_advanced:
        action = FailureAction.STOP_REPLAN
    elif context.side_effects:
        action = FailureAction.COMPENSATION
    else:
        action = FailureAction.ROLLBACK
    logger.info(
        "failure classified as %s (side_effects=%d, world_advanced=%s)",
        action.value,
        len(context.side_effects),
        context.world_advanced,
    )
    return action
