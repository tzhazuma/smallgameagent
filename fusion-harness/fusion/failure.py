"""Failure taxonomy — classify execution failures into 12 codes.

Python port of gah failure-taxonomy.mjs. Classification is deterministic and
ordered; each code maps to a recovery ladder in recovery.py.
"""
from __future__ import annotations

from typing import Any

FAILURE_CODES = (
    "NO_PATH", "OCCLUDED", "OUT_OF_REACH", "STUCK", "OSCILLATING",
    "WRONG_CONTROL_MAPPING", "TARGET_STALE", "NO_RESOURCE", "CAPACITY_FULL",
    "SEMANTIC_UNKNOWN", "WORLD_CHANGED", "TIMEOUT",
)

NO_PATH_STATUSES = ("NO_PATH", "UNREACHABLE", "BLOCKED")


def _movement_trace(execution: dict | None) -> dict:
    """Compact motion observation: efficiency, reversals, oscillation."""
    motion = (execution or {}).get("motion") or {}
    return {
        "player_moved": bool(motion.get("player_moved")),
        "distance_decreased": bool(motion.get("distance_decreased")),
        "reversals": int(motion.get("reversals", 0)),
        "oscillating": bool(motion.get("oscillating")),
        "net_displacement": float(motion.get("net_displacement", 0.0)),
        "pulses_used": int(motion.get("pulses_used", 0)),
    }


def classify_execution_failure(
    *,
    before: dict | None = None,
    after: dict | None = None,
    execution: dict | None = None,
    failure: dict | None = None,
    route_status: str | None = None,
    control_reasons: list[str] | None = None,
    budget_exhausted: bool = False,
    scene_epoch_changed: bool = False,
) -> dict | None:
    """Return a failure classification dict or None (no failure / progress).

    Ordered checks mirror gah:
      1. semantic progress -> None
      2. failure.active -> SEMANTIC_UNKNOWN
      3. target gone -> TARGET_STALE
      4. wrong control -> WRONG_CONTROL_MAPPING
      5. capacity -> CAPACITY_FULL
      6. resource -> NO_RESOURCE
      7. blocked route -> OUT_OF_REACH
      8. no path -> NO_PATH
      9. oscillating -> OSCILLATING (before WORLD_CHANGED)
      10. input without movement -> STUCK; distance not decreasing -> OCCLUDED
      11. scene change -> WORLD_CHANGED (last)
      12. budget -> TIMEOUT
    """
    trace = _movement_trace(execution)

    # 1. Semantic progress
    if execution and execution.get("semantic_progress"):
        return None

    # 2. Active failure
    if failure and failure.get("active"):
        return {
            "code": "SEMANTIC_UNKNOWN",
            "reason": "failure_active",
            "motion": trace,
        }

    # 3. Target stale
    if execution and execution.get("target_stale"):
        return {"code": "TARGET_STALE", "reason": "target_disappeared", "motion": trace}

    # 4. Wrong control mapping
    if control_reasons:
        return {"code": "WRONG_CONTROL_MAPPING", "reason": ";".join(control_reasons), "motion": trace}

    # 5. Capacity full
    if execution and execution.get("capacity_full"):
        return {"code": "CAPACITY_FULL", "reason": "capacity_saturated", "motion": trace}

    # 6. Resource unavailable
    if execution and execution.get("resource_unavailable"):
        return {"code": "NO_RESOURCE", "reason": "resource_unavailable", "motion": trace}

    # 7. Route position blocked
    if route_status == "TARGET_POSITION_BLOCKED":
        return {"code": "OUT_OF_REACH", "reason": "target_position_blocked", "motion": trace}

    # 8. No path
    if route_status in NO_PATH_STATUSES:
        return {"code": "NO_PATH", "reason": f"route_status={route_status}", "motion": trace}

    # 9. Oscillating (before world change)
    if trace["oscillating"] or (trace["reversals"] >= 3 and trace["net_displacement"] < 0.5):
        return {"code": "OSCILLATING", "reason": "direction_reversals_with_low_displacement", "motion": trace}

    # 10a. Input without movement -> STUCK
    if execution and execution.get("submitted_input") and not trace["player_moved"]:
        return {"code": "STUCK", "reason": "input_without_movement", "motion": trace}

    # 10b. Distance not decreasing -> OCCLUDED
    if execution and execution.get("submitted_input") and trace["player_moved"] and not trace["distance_decreased"]:
        return {"code": "OCCLUDED", "reason": "motion_without_path_progress", "motion": trace}

    # 11. World changed (last — avoid masking proven local topology failures)
    if scene_epoch_changed:
        return {"code": "WORLD_CHANGED", "reason": "scene_epoch_changed", "motion": trace}

    # 12. Budget
    if budget_exhausted:
        return {"code": "TIMEOUT", "reason": "pulse_budget_exhausted", "motion": trace}

    return None
