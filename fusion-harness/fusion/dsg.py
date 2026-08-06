"""Event-sourced Dynamic Scene Graph (Python port, simplified).

Projects a WorldSnapshot into a game-agnostic scene graph of entities and
relations, diffs against current state, and appends events to a JSONL stream.
state_version is monotonic; init() replays the stream.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENTITY_KINDS = (
    "world", "player", "region", "resource", "phase", "control_domain",
    "capability", "target", "obstacle", "structure",
)
RELATION_KINDS = (
    "contains", "blocks_navigation_in", "holds_resource", "has_capability",
    "has_control_domain", "has_phase", "requires_resource",
    "shares_physical_identity_with",
)


def _entity_id(kind: str, identity_key: str) -> str:
    digest = hashlib.sha256(f"{kind}:{identity_key}".encode()).hexdigest()[:24]
    return f"scene-entity-{digest}"


def project_world_to_graph(world: dict) -> tuple[list[dict], list[dict]]:
    """Project a world snapshot into (entities, relations)."""
    entities: list[dict] = []
    relations: list[dict] = []

    world_id = _entity_id("world", world.get("game_id", "unknown"))
    entities.append({
        "id": world_id, "kind": "world",
        "completion": world.get("completion"),
        "failure": world.get("failure"),
        "structural_signature": world.get("structural_signature"),
    })

    player = world.get("player") or {}
    if player.get("position"):
        player_id = _entity_id("player", "player")
        entities.append({
            "id": player_id, "kind": "player",
            "position": player.get("position"),
            "control_domain": player.get("control_domain"),
            "mechanic_state": player.get("mechanic_state"),
        })
        relations.append({"from": world_id, "to": player_id, "kind": "contains"})

    phase = world.get("phase")
    if phase:
        canonical = phase.get("canonical", phase) if isinstance(phase, dict) else phase
        phase_id = _entity_id("phase", str(canonical))
        entities.append({"id": phase_id, "kind": "phase", "canonical": canonical})
        relations.append({"from": world_id, "to": phase_id, "kind": "has_phase"})

    for target in world.get("targets") or []:
        tid = target.get("id", str(target.get("path", "?")))
        target_id = _entity_id("target", tid)
        entities.append({
            "id": target_id, "kind": "target",
            "path": target.get("path"),
            "role_hint": target.get("role_hint"),
            "active": target.get("active"),
            "position": target.get("position"),
        })
        relations.append({"from": world_id, "to": target_id, "kind": "contains"})
        if target.get("requires_resource"):
            relations.append({
                "from": target_id, "to": _entity_id("resource", target["requires_resource"]),
                "kind": "requires_resource",
            })

    for obstacle in world.get("obstacles") or []:
        oid = _entity_id("obstacle", str(obstacle.get("id", "?")))
        entities.append({
            "id": oid, "kind": "obstacle",
            "position": obstacle.get("position"),
            "collision_role": obstacle.get("collision_role"),
        })
        relations.append({"from": world_id, "to": oid, "kind": "contains"})
        relations.append({"from": oid, "to": world_id, "kind": "blocks_navigation_in"})

    for resource in world.get("resources") or []:
        rid = _entity_id("resource", str(resource.get("name", "?")))
        entities.append({
            "id": rid, "kind": "resource",
            "name": resource.get("name"),
            "count": resource.get("count"),
        })
        relations.append({"from": world_id, "to": rid, "kind": "contains"})

    return entities, relations


@dataclass
class DynamicSceneGraph:
    game_id: str
    run_id: str = "run"
    stream_path: Path | None = None
    _entities: dict = field(default_factory=dict)
    _relations: list = field(default_factory=list)
    _state_version: int = 0
    _scene_epoch: int = 0
    _graph_revision: int = 0

    def observe(self, world: dict) -> dict:
        """Diff the new projection and emit an event; return revision info."""
        entities, relations = project_world_to_graph(world)
        new_entities = {e["id"]: e for e in entities}

        ops: list[dict] = []
        for eid, entity in new_entities.items():
            if eid not in self._entities:
                ops.append({"op": "upsert_entity", "entity": entity})
            elif self._entities[eid] != entity:
                ops.append({"op": "upsert_entity", "entity": entity})
        for eid in list(self._entities):
            if eid not in new_entities:
                ops.append({"op": "retire_entity", "entity_id": eid})

        self._entities = new_entities
        self._relations = relations
        self._state_version += 1
        if world.get("scene_epoch") is not None and world["scene_epoch"] != self._scene_epoch:
            self._scene_epoch = int(world["scene_epoch"])
        self._graph_revision += 1

        event = {
            "schema_version": "agent_harness.event_sourced_scene_graph_event.v1",
            "game_id": self.game_id,
            "state_version": self._state_version,
            "scene_epoch": self._scene_epoch,
            "graph_revision": self._graph_revision,
            "ops": ops,
        }
        if self.stream_path:
            with open(self.stream_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return {
            "state_version": self._state_version,
            "scene_epoch": self._scene_epoch,
            "graph_revision": self._graph_revision,
            "ops": len(ops),
        }

    def init_from_stream(self) -> None:
        if not self.stream_path or not self.stream_path.exists():
            return
        for line in self.stream_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            self._state_version = max(self._state_version, event.get("state_version", 0))
            self._graph_revision = max(self._graph_revision, event.get("graph_revision", 0))
            for op in event.get("ops", []):
                if op.get("op") == "upsert_entity":
                    self._entities[op["entity"]["id"]] = op["entity"]
                elif op.get("op") == "retire_entity":
                    self._entities.pop(op.get("entity_id"), None)

    def planner_view(self, limit: int = 24) -> dict:
        """Compact projection for the planner."""
        return {
            "state_version": self._state_version,
            "scene_epoch": self._scene_epoch,
            "entities": list(self._entities.values())[:limit],
            "relations": self._relations[: limit * 2],
        }

    @property
    def state_version(self) -> int:
        return self._state_version
