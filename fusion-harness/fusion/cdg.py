"""Causal Decision Graph — state-transition outcomes for strategy learning.

Python port of gah causal-decision-graph.mjs (simplified): causal state
descriptors -> node ids, transitions accumulate attempts/progress, planner
view flags repeated no-effect edges.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def causal_state_descriptor(world: dict) -> dict:
    """Extract a compact causal signature from a world snapshot."""
    guide = world.get("guide") or {}
    player = world.get("player") or {}
    return {
        "phase": world.get("phase"),
        "control_domain": player.get("control_domain"),
        "guide_target_id": guide.get("target_id"),
        "guide_role": guide.get("role_hint"),
        "resource_signature": world.get("resource_signature"),
        "structural_signature": world.get("structural_signature"),
        "failure_active": bool((world.get("failure") or {}).get("active")),
        "completion_suspected": bool((world.get("completion") or {}).get("suspected")),
    }


def _node_id(descriptor: dict) -> str:
    raw = json.dumps(descriptor, sort_keys=True, ensure_ascii=False)
    return f"cdg-state-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _edge_id(from_id: str, to_id: str, action: dict) -> str:
    raw = json.dumps({"from": from_id, "to": to_id, "action": action}, sort_keys=True)
    return f"cdg-edge-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


@dataclass
class CausalDecisionGraph:
    game_id: str
    stream_path: Path | None = None
    _nodes: dict = field(default_factory=dict)   # node_id -> descriptor
    _edges: dict = field(default_factory=dict)   # edge_id -> stats

    def record_transition(
        self, *, before: dict, after: dict, action: dict,
        semantic_progress: bool = False,
    ) -> dict:
        from_desc = causal_state_descriptor(before)
        to_desc = causal_state_descriptor(after)
        from_id = _node_id(from_desc)
        to_id = _node_id(to_desc)
        edge_id = _edge_id(from_id, to_id, action)
        self._nodes[from_id] = from_desc
        self._nodes[to_id] = to_desc
        stats = self._edges.setdefault(edge_id, {
            "attempts": 0, "semantic_progress_count": 0, "no_effect_count": 0,
        })
        stats["attempts"] += 1
        if semantic_progress:
            stats["semantic_progress_count"] += 1
        else:
            stats["no_effect_count"] += 1
        stats["confidence"] = stats["semantic_progress_count"] / stats["attempts"]
        if self.stream_path:
            event = {
                "schema_version": "agent_harness.causal_decision_graph_event.v1",
                "game_id": self.game_id,
                "edge_id": edge_id,
                "from_id": from_id,
                "to_id": to_id,
                "action": action,
                "stats": stats,
            }
            with open(self.stream_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return {"edge_id": edge_id, "from_id": from_id, "to_id": to_id, "stats": stats}

    def planner_view(self, world: dict, limit: int = 8) -> dict:
        current = causal_state_descriptor(world)
        current_id = _node_id(current)
        out_edges = [
            {"edge_id": eid, "action": self._edges[eid].get("action", {}), "stats": stats}
            for eid, stats in self._edges.items()
            if eid.startswith(current_id[:16]) or True  # simple: all edges
        ]
        # Prefer edges with higher attempts/progress; flag repeated no-effect.
        out_edges.sort(key=lambda e: (e["stats"].get("semantic_progress_count", 0), e["stats"].get("attempts", 0)), reverse=True)
        for e in out_edges:
            stats = e["stats"]
            e["repeated_no_effect"] = stats.get("no_effect_count", 0) >= 2 and stats.get("semantic_progress_count", 0) == 0
        return {
            "current_state_id": current_id,
            "edges": out_edges[:limit],
        }
