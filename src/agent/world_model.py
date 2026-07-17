"""Versioned world model with epoch-based staleness tracking for game plans.

Motivation: as gameplay progresses, scene topology and capabilities change
(e.g. the ``autoFishing`` flag was observed flipping 12 times within 266 steps,
and new obstacle nodes appear mid-run). Plans derived from outdated facts —
paths, interaction choices, targets — become spatially misaligned ("空间错位").
This module versions every observed entity and maintains two epoch counters
(``scene_epoch`` / ``capability_epoch``). When a write mismatches the epoch an
artifact was created under, only the affected derived artifacts
(:class:`PlanArtifact`) are marked stale so the caller can re-plan locally
instead of restarting globally.

The module is pure data structures and logic: no browser, no API, no I/O,
and is trivially unit-testable.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Pattern

logger = logging.getLogger(__name__)

#: Default regex (case-insensitive) matching flag names with automation /
#: capability semantics, e.g. ``autoFishing``, ``UnlockItem_3``, ``hasCapabilityX``.
DEFAULT_CAPABILITY_PATTERN = r"auto|unlock|capabilit"

#: Staleness reasons.
REASON_ENTITY_CHANGED = "entity_changed"
REASON_CAPABILITY_FLIP = "capability_flip"
REASON_SCENE_SHIFT = "scene_shift"

#: Default plan-kind -> sensitive-epochs mapping. ``interaction`` plans (how to
#: interact) depend on capabilities; ``path``/``route``/``target`` plans depend
#: on scene topology. Unknown kinds conservatively follow
#: :data:`DEFAULT_UNKNOWN_KIND_SENSITIVITY`.
DEFAULT_KIND_SENSITIVITY: dict[str, frozenset[str]] = {
    "interaction": frozenset({"capability"}),
    "path": frozenset({"scene"}),
    "route": frozenset({"scene"}),
    "target": frozenset({"scene"}),
}

#: Sensitivity assumed for plan kinds missing from the mapping: conservative,
#: i.e. sensitive to both epochs (stale-marking is cheap, stale plans are not).
DEFAULT_UNKNOWN_KIND_SENSITIVITY = frozenset({"scene", "capability"})


@dataclass
class EntityRecord:
    """A single versioned fact observed from the game.

    Parameters
    ----------
    entity_id : str
        Raw observation key, e.g. ``"autoFishing"``, ``"gold"``, ``"Arr3D"``.
    kind : str
        Entity category: ``"flag"``, ``"number"``, ``"node"``, ``"capability"``, ...
    value : Any
        Latest observed value (bool, str, or number; kept JSON-serializable).
    version : int
        Monotonically increasing counter, bumped on every value change. Starts
        at 1 when the entity is first seen.
    first_seen_step : int
        Step at which the entity was first observed.
    last_changed_step : int
        Step at which the value last changed.
    """

    entity_id: str
    kind: str
    value: Any
    version: int
    first_seen_step: int
    last_changed_step: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "value": self.value,
            "version": self.version,
            "first_seen_step": self.first_seen_step,
            "last_changed_step": self.last_changed_step,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityRecord:
        """Rebuild an :class:`EntityRecord` from :meth:`to_dict` output."""
        return cls(
            entity_id=str(data["entity_id"]),
            kind=str(data["kind"]),
            value=data.get("value"),
            version=int(data["version"]),
            first_seen_step=int(data.get("first_seen_step", 0)),
            last_changed_step=int(data.get("last_changed_step", 0)),
        )


@dataclass
class PlanArtifact:
    """A derived artifact (path, interaction choice, target, route).

    Parameters
    ----------
    plan_id : str
        Unique identifier of the plan.
    kind : str
        Plan category: ``"path"``, ``"interaction"``, ``"target"``, ``"route"``.
    depends_on : set[str]
        Entity ids the plan was derived from.
    scene_epoch_at_creation : int
        ``scene_epoch`` snapshot taken at creation.
    capability_epoch_at_creation : int
        ``capability_epoch`` snapshot taken at creation.
    entity_versions_at_creation : dict[str, int]
        Version snapshot of every depended-on entity at creation.
    created_step : int
        Step at which the plan was registered.
    stale : bool
        Whether the plan has been marked stale.
    stale_reason : str | None
        One of ``"entity_changed"`` / ``"capability_flip"`` / ``"scene_shift"``.
    stale_step : int | None
        Step at which the plan was marked stale.
    """

    plan_id: str
    kind: str
    depends_on: set[str]
    scene_epoch_at_creation: int
    capability_epoch_at_creation: int
    entity_versions_at_creation: dict[str, int] = field(default_factory=dict)
    created_step: int = 0
    stale: bool = False
    stale_reason: str | None = None
    stale_step: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "plan_id": self.plan_id,
            "kind": self.kind,
            "depends_on": sorted(self.depends_on),
            "scene_epoch_at_creation": self.scene_epoch_at_creation,
            "capability_epoch_at_creation": self.capability_epoch_at_creation,
            "entity_versions_at_creation": dict(self.entity_versions_at_creation),
            "created_step": self.created_step,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
            "stale_step": self.stale_step,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanArtifact:
        """Rebuild a :class:`PlanArtifact` from :meth:`to_dict` output."""
        return cls(
            plan_id=str(data["plan_id"]),
            kind=str(data["kind"]),
            depends_on={str(d) for d in data.get("depends_on", [])},
            scene_epoch_at_creation=int(data.get("scene_epoch_at_creation", 0)),
            capability_epoch_at_creation=int(data.get("capability_epoch_at_creation", 0)),
            entity_versions_at_creation={
                str(k): int(v) for k, v in data.get("entity_versions_at_creation", {}).items()
            },
            created_step=int(data.get("created_step", 0)),
            stale=bool(data.get("stale", False)),
            stale_reason=data.get("stale_reason"),
            stale_step=data.get("stale_step"),
        )


@dataclass
class ChangeReport:
    """Summary of one :meth:`VersionedWorldModel.write_observation` call.

    Parameters
    ----------
    step : int
        Step of the written observation.
    changed_entities : list[str]
        Ids of existing entities whose value changed.
    new_entities : list[str]
        Ids of entities seen for the first time.
    bumped_epochs : dict[str, int]
        Epoch names (``"scene"`` / ``"capability"``) mapped to bump counts.
    stale_plans : list[str]
        Plan ids newly marked stale by this write.
    """

    step: int
    changed_entities: list[str] = field(default_factory=list)
    new_entities: list[str] = field(default_factory=list)
    bumped_epochs: dict[str, int] = field(default_factory=dict)
    stale_plans: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "step": self.step,
            "changed_entities": list(self.changed_entities),
            "new_entities": list(self.new_entities),
            "bumped_epochs": dict(self.bumped_epochs),
            "stale_plans": list(self.stale_plans),
        }


class VersionedWorldModel:
    """World model that versions entities and tracks plan staleness by epochs.

    Parameters
    ----------
    capability_pattern : str | Pattern | None
        Regex (compiled case-insensitively when given as ``str``) matching flag
        names that carry automation/capability semantics. Defaults to
        :data:`DEFAULT_CAPABILITY_PATTERN`.
    kind_sensitivity : Mapping[str, Iterable[str]] | None
        Overrides/extends :data:`DEFAULT_KIND_SENSITIVITY`: plan kind mapped to
        the epochs it is sensitive to (subset of ``{"scene", "capability"}``).
    """

    def __init__(
        self,
        capability_pattern: str | Pattern[str] | None = None,
        kind_sensitivity: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        if capability_pattern is None:
            self._capability_pattern_source: str | None = None
            self._capability_re = re.compile(DEFAULT_CAPABILITY_PATTERN, re.IGNORECASE)
        elif isinstance(capability_pattern, str):
            self._capability_pattern_source = capability_pattern
            self._capability_re = re.compile(capability_pattern, re.IGNORECASE)
        else:
            self._capability_pattern_source = capability_pattern.pattern
            self._capability_re = capability_pattern

        self._sensitivity: dict[str, frozenset[str]] = dict(DEFAULT_KIND_SENSITIVITY)
        if kind_sensitivity:
            for kind, epochs in kind_sensitivity.items():
                self._sensitivity[str(kind)] = frozenset(epochs)

        self.scene_epoch: int = 0
        self.capability_epoch: int = 0

        self._entities: dict[str, EntityRecord] = {}
        self._plans: dict[str, PlanArtifact] = {}
        self._capability_entities: set[str] = set()
        self._node_set: set[str] = set()
        self._node_initialized: bool = False
        self._capability_flips: int = 0
        self._stale_events: int = 0
        self._last_step: int = 0

    # ------------------------------------------------------------------ write

    def write_observation(self, obs: dict[str, Any] | None, step: int) -> ChangeReport:
        """Write one observation frame and mark affected plans stale.

        Rules:

        - ``keyFlags`` value flips bump the entity version; flags matching the
          capability pattern additionally bump ``capability_epoch``.
        - ``guide_or_target_candidates`` set changes (appeared/vanished nodes)
          bump ``scene_epoch`` once per frame. The first frame carrying the key
          only initializes the topology baseline and never bumps.
        - ``keyNumbers`` changes only bump the entity version; continuous
          quantities never bump an epoch.
        - Missing or malformed keys are tolerated silently.

        Parameters
        ----------
        obs : dict | None
            Observation dict as returned by ``ProbeAdapter.observe()``.
        step : int
            Current game step.

        Returns
        -------
        ChangeReport
            Changed entities, bumped epochs, and newly stale plans.
        """
        obs = obs or {}
        self._last_step = step
        report = ChangeReport(step=step)

        flags = obs.get("keyFlags")
        if isinstance(flags, Mapping):
            for name, value in flags.items():
                eid = str(name)
                _, created, changed = self._upsert(eid, "flag", value, step)
                if self._capability_re.search(eid):
                    self._capability_entities.add(eid)
                if created:
                    report.new_entities.append(eid)
                elif changed:
                    report.changed_entities.append(eid)
                    if self._capability_re.search(eid):
                        self.capability_epoch += 1
                        self._capability_flips += 1
                        report.bumped_epochs["capability"] = (
                            report.bumped_epochs.get("capability", 0) + 1
                        )

        numbers = obs.get("keyNumbers")
        if isinstance(numbers, Mapping):
            for name, value in numbers.items():
                eid = str(name)
                _, created, changed = self._upsert(eid, "number", value, step)
                if created:
                    report.new_entities.append(eid)
                elif changed:
                    report.changed_entities.append(eid)

        candidates = obs.get("guide_or_target_candidates")
        if isinstance(candidates, (list, tuple, set)):
            new_set = {str(n) for n in candidates}
            if not self._node_initialized:
                # First topology frame only establishes the baseline.
                self._node_initialized = True
                self._node_set = set(new_set)
                for nid in sorted(new_set):
                    _, created, _ = self._upsert(nid, "node", True, step)
                    if created:
                        report.new_entities.append(nid)
            elif new_set != self._node_set:
                self.scene_epoch += 1
                report.bumped_epochs["scene"] = report.bumped_epochs.get("scene", 0) + 1
                for nid in sorted(new_set - self._node_set):
                    _, created, changed = self._upsert(nid, "node", True, step)
                    if created:
                        report.new_entities.append(nid)
                    elif changed:
                        report.changed_entities.append(nid)
                for nid in sorted(self._node_set - new_set):
                    _, _, changed = self._upsert(nid, "node", False, step)
                    if changed:
                        report.changed_entities.append(nid)
                self._node_set = set(new_set)

        report.stale_plans = self._evaluate_staleness()
        if report.stale_plans:
            logger.info(
                "step %d: %d plan(s) marked stale: %s",
                step,
                len(report.stale_plans),
                report.stale_plans,
            )
        return report

    def _upsert(self, entity_id: str, kind: str, value: Any, step: int) -> tuple:
        """Create or update an entity.

        Returns
        -------
        tuple
            ``(entity, created, changed)``; a first sighting counts as
            ``created`` but not as a value ``changed``.
        """
        ent = self._entities.get(entity_id)
        if ent is None:
            ent = EntityRecord(
                entity_id=entity_id,
                kind=kind,
                value=value,
                version=1,
                first_seen_step=step,
                last_changed_step=step,
            )
            self._entities[entity_id] = ent
            return ent, True, False
        if ent.value != value:
            ent.value = value
            ent.version += 1
            ent.last_changed_step = step
            return ent, False, True
        return ent, False, False

    # ------------------------------------------------------------------ plans

    def register_plan(
        self, plan_id: str, kind: str, depends_on: Iterable[str], step: int
    ) -> PlanArtifact:
        """Register a derived artifact with an epoch/version snapshot.

        Parameters
        ----------
        plan_id : str
            Unique plan identifier; re-registering overwrites the old snapshot.
        kind : str
            Plan category (``"path"`` / ``"interaction"`` / ``"target"`` / ``"route"``).
        depends_on : Iterable[str]
            Entity ids the plan is derived from.
        step : int
            Current game step.

        Returns
        -------
        PlanArtifact
            The registered artifact.
        """
        deps = {str(d) for d in depends_on}
        plan = PlanArtifact(
            plan_id=str(plan_id),
            kind=str(kind),
            depends_on=deps,
            scene_epoch_at_creation=self.scene_epoch,
            capability_epoch_at_creation=self.capability_epoch,
            entity_versions_at_creation={
                eid: self._entities[eid].version for eid in deps if eid in self._entities
            },
            created_step=step,
        )
        self._plans[plan.plan_id] = plan
        logger.debug(
            "registered plan %s (kind=%s, deps=%s, scene_epoch=%d, capability_epoch=%d)",
            plan.plan_id,
            plan.kind,
            sorted(deps),
            plan.scene_epoch_at_creation,
            plan.capability_epoch_at_creation,
        )
        return plan

    def _sensitivity_for(self, kind: str) -> frozenset[str]:
        """Return the sensitive-epoch set for a plan kind."""
        return self._sensitivity.get(kind, DEFAULT_UNKNOWN_KIND_SENSITIVITY)

    def _staleness_reason(self, plan: PlanArtifact) -> str | None:
        """Compute why a plan is stale, or ``None`` when it is still fresh.

        Precedence: ``capability_flip`` > ``scene_shift`` > ``entity_changed``,
        so epoch-level causes are reported over plain version bumps.
        """
        sens = self._sensitivity_for(plan.kind)
        if (
            "capability" in sens
            and self.capability_epoch > plan.capability_epoch_at_creation
            and plan.depends_on & self._capability_entities
        ):
            return REASON_CAPABILITY_FLIP
        if "scene" in sens and self.scene_epoch > plan.scene_epoch_at_creation:
            return REASON_SCENE_SHIFT
        for eid in plan.depends_on:
            ent = self._entities.get(eid)
            if ent is not None and ent.version > plan.entity_versions_at_creation.get(eid, 0):
                return REASON_ENTITY_CHANGED
        return None

    def _evaluate_staleness(self) -> list[str]:
        """Mark all newly stale plans. Idempotent: already-stale plans are skipped.

        Returns
        -------
        list[str]
            Ids of plans transitioned to stale by this call.
        """
        newly_stale: list[str] = []
        for plan in self._plans.values():
            if plan.stale:
                continue
            reason = self._staleness_reason(plan)
            if reason is None:
                continue
            plan.stale = True
            plan.stale_reason = reason
            plan.stale_step = self._last_step
            self._stale_events += 1
            newly_stale.append(plan.plan_id)
        return newly_stale

    def check_staleness(self) -> list[str]:
        """Re-evaluate all plans and mark newly stale ones.

        Idempotent: repeated calls do not double-count stale events.

        Returns
        -------
        list[str]
            Ids of plans newly marked stale by this call.
        """
        return self._evaluate_staleness()

    def is_stale(self, plan_id: str) -> bool:
        """Return whether a registered plan is currently stale.

        Parameters
        ----------
        plan_id : str
            Registered plan identifier.

        Returns
        -------
        bool

        Raises
        ------
        KeyError
            If ``plan_id`` was never registered.
        """
        return self._plans[plan_id].stale

    def get_plan(self, plan_id: str) -> PlanArtifact:
        """Return the :class:`PlanArtifact` for ``plan_id`` (raises ``KeyError``)."""
        return self._plans[plan_id]

    def local_replan_scope(self, stale_plans: Iterable[str | PlanArtifact]) -> set[str]:
        """Compute the entity ids a local re-plan must cover.

        Parameters
        ----------
        stale_plans : Iterable[str | PlanArtifact]
            Stale plan ids or artifacts.

        Returns
        -------
        set[str]
            Union of the depended-on entity ids; unknown plan ids are ignored.
        """
        scope: set[str] = set()
        for item in stale_plans:
            pid = item.plan_id if isinstance(item, PlanArtifact) else str(item)
            plan = self._plans.get(pid)
            if plan is not None:
                scope |= plan.depends_on
        return scope

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict[str, Any]:
        """Return counters for rubrics/reports.

        Returns
        -------
        dict
            Keys: ``entity_count``, ``plan_count``, ``stale_plan_count``,
            ``scene_epoch``, ``capability_epoch``, ``capability_flips``,
            ``stale_events``.
        """
        return {
            "entity_count": len(self._entities),
            "plan_count": len(self._plans),
            "stale_plan_count": sum(1 for p in self._plans.values() if p.stale),
            "scene_epoch": self.scene_epoch,
            "capability_epoch": self.capability_epoch,
            "capability_flips": self._capability_flips,
            "stale_events": self._stale_events,
        }

    # ---------------------------------------------------------- serialization

    def to_dict(self) -> dict[str, Any]:
        """Serialize the whole model to a JSON-compatible dict.

        Returns
        -------
        dict
            Full state including config, entities, plans, and counters;
            restorable via :meth:`from_dict`.
        """
        return {
            "scene_epoch": self.scene_epoch,
            "capability_epoch": self.capability_epoch,
            "capability_flips": self._capability_flips,
            "stale_events": self._stale_events,
            "last_step": self._last_step,
            "node_initialized": self._node_initialized,
            "node_set": sorted(self._node_set),
            "capability_entities": sorted(self._capability_entities),
            "capability_pattern": self._capability_pattern_source,
            "kind_sensitivity": {k: sorted(v) for k, v in self._sensitivity.items()},
            "entities": {eid: ent.to_dict() for eid, ent in self._entities.items()},
            "plans": {pid: plan.to_dict() for pid, plan in self._plans.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VersionedWorldModel:
        """Restore a :class:`VersionedWorldModel` from :meth:`to_dict` output.

        Parameters
        ----------
        data : Mapping[str, Any]
            Serialized state. Missing keys fall back to fresh-model defaults.

        Returns
        -------
        VersionedWorldModel
        """
        model = cls(
            capability_pattern=data.get("capability_pattern"),
            kind_sensitivity=data.get("kind_sensitivity"),
        )
        model.scene_epoch = int(data.get("scene_epoch", 0))
        model.capability_epoch = int(data.get("capability_epoch", 0))
        model._capability_flips = int(data.get("capability_flips", 0))
        model._stale_events = int(data.get("stale_events", 0))
        model._last_step = int(data.get("last_step", 0))
        model._node_initialized = bool(data.get("node_initialized", False))
        model._node_set = {str(n) for n in data.get("node_set", [])}
        model._capability_entities = {str(e) for e in data.get("capability_entities", [])}
        model._entities = {
            str(eid): EntityRecord.from_dict(ed) for eid, ed in data.get("entities", {}).items()
        }
        model._plans = {
            str(pid): PlanArtifact.from_dict(pd) for pid, pd in data.get("plans", {}).items()
        }
        return model
