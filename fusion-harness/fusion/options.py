"""Deterministic Option system (Python port of gah option-catalog).

An Option is a deterministic atomic operation the planner may select.
The planner can only pick allow-listed Options; the engine compiles the
Option into raw primitives and supervises execution. No AI port during
execution.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable


def _finite(value: Any, name: str, lo: float, hi: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    v = float(value)
    if not math.isfinite(v) or v < lo or v > hi:
        raise ValueError(f"{name} must be in [{lo}, {hi}], got {v}")
    return v


def _point(value: Any, name: str, viewport: tuple[int, int] | None = None) -> dict:
    if not isinstance(value, dict) or "x" not in value or "y" not in value:
        raise ValueError(f"{name} must be a point {{x, y}}")
    x = _finite(value["x"], f"{name}.x", 0, 10000)
    y = _finite(value["y"], f"{name}.y", 0, 10000)
    if viewport:
        _finite(x, f"{name}.x", 0, viewport[0])
        _finite(y, f"{name}.y", 0, viewport[1])
    return {"x": round(x), "y": round(y)}


@dataclass
class Option:
    """A registered deterministic operation."""

    name: str
    risk: str  # none | low | medium | high
    requires_target: bool = False
    observable_effects: tuple[str, ...] = ()
    description: str = ""
    compile: Callable[[dict, dict | None], list[dict]] = field(
        default=lambda p, c: []
    )


def _compile_observe_settle(parameters: dict, _ctx: dict | None) -> list[dict]:
    return [{"type": "wait", "duration_ms": int(_finite(parameters.get("duration_ms", 500), "duration_ms", 100, 2500))}]


def _compile_probe_tap(parameters: dict, ctx: dict | None) -> list[dict]:
    viewport = (ctx or {}).get("viewport")
    return [{
        "type": "tap",
        "point": _point(parameters.get("point"), "point", viewport),
        "hold_ms": int(_finite(parameters.get("hold_ms", 80), "hold_ms", 30, 800)),
    }]


def _compile_probe_joystick(parameters: dict, _ctx: dict | None) -> list[dict]:
    dx = _finite(parameters.get("dx"), "dx", -1, 1)
    dy = _finite(parameters.get("dy"), "dy", -1, 1)
    if math.hypot(dx, dy) < 0.2:
        raise ValueError("joystick vector is too small")
    return [{
        "type": "move_pulse",
        "stick": {"dx": dx, "dy": dy},
        "duration_ms": int(_finite(parameters.get("duration_ms", 350), "duration_ms", 80, 1500)),
    }]


def _compile_explore_sweep(parameters: dict, _ctx: dict | None) -> list[dict]:
    dx = _finite(parameters.get("dx", 0), "dx", -1, 1)
    dy = _finite(parameters.get("dy", -1), "dy", -1, 1)
    magnitude = math.hypot(dx, dy)
    if magnitude < 0.5:
        raise ValueError("sector sweep vector is too small")
    steps = int(round(_finite(parameters.get("steps", 4), "steps", 4, 6)))
    duration_ms = int(_finite(parameters.get("duration_ms", 220), "duration_ms", 150, 300))
    fx, fy = dx / magnitude, dy / magnitude
    pulses = []
    for index in range(steps):
        offset = 0.0 if index % 2 == 0 else -0.64
        cos, sin = math.cos(offset), math.sin(offset)
        pulses.append({
            "type": "move_pulse",
            "stick": {"dx": fx * cos - fy * sin, "dy": fx * sin + fy * cos},
            "duration_ms": duration_ms,
            "exploration_segment": True,
        })
    return pulses


def _compile_approach_target(parameters: dict, _ctx: dict | None) -> list[dict]:
    target_id = parameters.get("target_id")
    if not isinstance(target_id, str) or not target_id:
        raise ValueError("target_id is required")
    waypoints = parameters.get("waypoints") or []
    if not isinstance(waypoints, list) or len(waypoints) > 16:
        raise ValueError("waypoints must be a list of 1-16 points")
    return [{
        "type": "approach_target",
        "target_id": target_id,
        "tolerance": _finite(parameters.get("tolerance", 0.45), "tolerance", 0.05, 4),
        "max_pulses": int(round(_finite(parameters.get("max_pulses", 8), "max_pulses", 1, 20))),
        "waypoints": waypoints,
        "waypoint_tolerance": _finite(parameters.get("waypoint_tolerance", 0.75), "waypoint_tolerance", 0.1, 2.5),
    }]


def _compile_dwell(parameters: dict, _ctx: dict | None) -> list[dict]:
    return [{
        "type": "dwell_at_target",
        "target_id": parameters.get("target_id"),
        "duration_ms": int(_finite(parameters.get("duration_ms", 500), "duration_ms", 100, 3000)),
    }]


def _compile_recover_reverse(parameters: dict, _ctx: dict | None) -> list[dict]:
    return [{
        "type": "move_pulse",
        "stick": {"dx": _finite(parameters.get("dx", 0), "dx", -1, 1),
                  "dy": _finite(parameters.get("dy", 0.5), "dy", -1, 1)},
        "duration_ms": int(_finite(parameters.get("duration_ms", 300), "duration_ms", 80, 1200)),
    }]


def _compile_verify_completion(_parameters: dict, _ctx: dict | None) -> list[dict]:
    return [{"type": "verify_completion"}]


OPTION_NAMES = (
    "observe_settle", "probe_tap", "probe_drag", "probe_joystick",
    "explore_sector_sweep", "approach_target", "dwell_at_target",
    "recover_reverse", "verify_completion",
)


def build_option_catalog() -> dict[str, Option]:
    return {
        "observe_settle": Option(
            "observe_settle", "none", False,
            ("state_settles", "any_relevant_progress"),
            "Wait for state to settle without input.",
            _compile_observe_settle,
        ),
        "probe_tap": Option(
            "probe_tap", "low", False,
            ("any_relevant_progress", "scene_changes", "completion_suspected"),
            "Tap one bounded screen point.",
            _compile_probe_tap,
        ),
        "probe_drag": Option(
            "probe_drag", "medium", False,
            ("any_relevant_progress", "scene_changes", "completion_suspected"),
            "Drag between two bounded screen points.",
            lambda p, c: [{
                "type": "drag",
                "from": _point(p.get("from"), "from", (c or {}).get("viewport")),
                "to": _point(p.get("to"), "to", (c or {}).get("viewport")),
                "duration_ms": int(_finite(p.get("duration_ms", 400), "duration_ms", 100, 1600)),
                "steps": int(round(_finite(p.get("steps", 8), "steps", 2, 30))),
            }],
        ),
        "probe_joystick": Option(
            "probe_joystick", "low", False,
            ("player_position_changes",),
            "One short virtual-joystick pulse.",
            _compile_probe_joystick,
        ),
        "explore_sector_sweep": Option(
            "explore_sector_sweep", "medium", False,
            ("player_position_changes", "any_relevant_progress", "scene_changes"),
            "Fan-shaped control-space sweep.",
            _compile_explore_sweep,
        ),
        "approach_target": Option(
            "approach_target", "medium", True,
            ("player_position_changes", "distance_to_target_decreases",
             "target_value_decreases", "any_relevant_progress"),
            "Approach a canonical target through a bounded route.",
            _compile_approach_target,
        ),
        "dwell_at_target": Option(
            "dwell_at_target", "low", True,
            ("target_value_decreases", "scene_changes", "any_relevant_progress"),
            "Dwell near a target to trigger interaction.",
            _compile_dwell,
        ),
        "recover_reverse": Option(
            "recover_reverse", "low", False,
            ("player_position_changes", "failure_clears"),
            "Reverse pulse to escape a stuck state.",
            _compile_recover_reverse,
        ),
        "verify_completion": Option(
            "verify_completion", "none", False,
            ("settled_completion",),
            "Run fixed completion evaluation without input.",
            _compile_verify_completion,
        ),
    }


class OptionCatalog:
    """Registry of allow-listed Options with resolve().

    resolve(intent) -> {option, risk, requires_target, parameters, primitives}
    """

    def __init__(self, enabled: set[str] | None = None) -> None:
        self._definitions = build_option_catalog()
        self.enabled = set(enabled) if enabled is not None else set(self._definitions)

    def planner_descriptions(self) -> list[dict]:
        return [
            {
                "name": name,
                "risk": opt.risk,
                "requires_target": opt.requires_target,
                "observable_effects": list(opt.observable_effects),
                "description": opt.description,
            }
            for name, opt in sorted(self._definitions.items())
            if name in self.enabled
        ]

    def resolve(self, intent: dict, context: dict | None = None) -> dict:
        option_name = intent.get("option")
        if option_name not in self.enabled:
            raise ValueError(f"Option is disabled: {option_name}")
        definition = self._definitions[option_name]
        parameters = intent.get("parameters") or {}
        primitives = definition.compile(parameters, context)
        return {
            "option": option_name,
            "risk": definition.risk,
            "requires_target": definition.requires_target,
            "parameters": parameters,
            "primitives": primitives,
        }


def compile_option(intent: dict, catalog: OptionCatalog | None = None,
                   context: dict | None = None) -> list[dict]:
    """Convenience: resolve an intent to compiled primitives."""
    catalog = catalog or OptionCatalog()
    return catalog.resolve(intent, context)["primitives"]
