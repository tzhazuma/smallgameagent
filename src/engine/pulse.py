"""Pulse timing curves for all game driver types.

Ports the ``worldMoveDuration()``, ``pulseDuration()``, and
``moveDuration()`` functions from the Node.js game drivers.

Each function returns a pulse duration in **milliseconds** given a
movement distance in **world units** (or pixels for 2D games).

Usage::

    from src.engine.pulse import get_pulse_duration

    # Profile-based game (follow-guide-audited)
    ms = get_pulse_duration("follow-guide", distance, input_mode="touch")

    # 2D game (00853)
    ms = get_pulse_duration("2d", distance_px)

    # Custom game (00862)
    ms = get_pulse_duration("learned", distance)
"""

from __future__ import annotations

from typing import Literal

from src.engine.vector import interpolate_pulse_duration

# ---------------------------------------------------------------------------
# Tier tables — each entry: (max_distance, duration_ms)
# ---------------------------------------------------------------------------

# --- Touch input (00848) ---
_TOUCH_TIERS: list[tuple[float, float]] = [
    (0.45, 45),
    (0.75, 65),
    (1.0, 90),
    (1.35, 125),
    (1.8, 165),
    (2.4, 220),
    (3.2, 300),
    (4.4, 400),
    (6.0, 620),  # large direct
]

# --- Mouse input — 00849 (anchor [125, 1130]) ---
_MOUSE_00849_TIERS: list[tuple[float, float]] = [
    (0.45, 10),
    (0.75, 18),
    (1.0, 26),
    (1.35, 38),
    (1.8, 34),  # not a typo — anomalous shorter tier
    (2.4, 48),
    (3.2, 78),
    (4.4, 115),
    (6.5, 230),
    (9.0, 300),
]

# --- Mouse input — 00850/00854/00858/00860 (anchor [91, 699]) ---
_MOUSE_00850_TIERS: list[tuple[float, float]] = [
    (0.65, 0),  # dead zone
    (0.9, 8),
    (1.25, 12),
    (1.6, 18),
    (2.1, 28),
    (2.8, 42),
    (3.8, 70),
    (5.2, 120),
    (7.5, 220),
    (10.0, 320),
]

# --- 2D pixel space — 00853 ---
_PIXEL_2D_TIERS: list[tuple[float, float]] = [
    (35, 0),
    (70, 90),
    (130, 180),
    (220, 320),
    (360, 520),
]

# --- Waypoint scheme (shared fallback) ---
_WAYPOINT_TIERS: list[tuple[float, float]] = [
    (0.45, 45),
    (0.75, 55),
    (1.0, 75),
    (1.35, 105),
    (1.8, 140),
    (2.4, 180),
    (3.2, 230),
    (4.4, 300),
]

# --- 00862 (learned) ---
_LEARNED_00862_TIERS: list[tuple[float, float]] = [
    (0.75, 0),
    (1.2, 90),
    (2.0, 175),
    (3.2, 285),
    (5.0, 410),
    (7.0, 590),
    (10.0, 760),
    (float("inf"), 940),
]

# --- 00863 (taskguide, world-target mode < 4m) ---
_TASKGUIDE_WORLD_TARGET_TIERS: list[tuple[float, float]] = [
    (4.0, 95),
    (float("inf"), 420),  # guide-vector ≤1.4m
]

# --- 00864 (target-arrow, px) ---
_TARGET_ARROW_PX_TIERS: list[tuple[float, float]] = [
    (45, 90),
    (80, 150),
    (150, 240),
    (300, 360),
    (520, 520),
    (800, 680),
    (float("inf"), 820),
]

# --- 00864 (route-around-wall, px) ---
_TARGET_ARROW_ROUTE_TIERS: list[tuple[float, float]] = [
    (75, 110),
    (130, 170),
    (220, 260),
    (float("inf"), 360),
]

# --- 00867 (guide-follow, generic) ---
_GUIDE_FOLLOW_GENERIC_TIERS: list[tuple[float, float]] = [
    (1.0, 100),
    (2.2, 190),
    (5.0, 340),
    (9.0, 520),
    (16.0, 700),
    (float("inf"), 900),
]

# --- 00867 (route-around-wall, 3D) ---
_GUIDE_FOLLOW_ROUTE_TIERS: list[tuple[float, float]] = [
    (1.4, 120),
    (3.0, 220),
    (6.0, 380),
    (float("inf"), 560),
]

# --- 00867 (money-trigger-sweep, 3D) ---
_GUIDE_FOLLOW_MONEY_TIERS: list[tuple[float, float]] = [
    (1.0, 120),
    (2.2, 220),
    (5.0, 430),
    (9.0, 760),
    (14.0, 1050),
    (float("inf"), 1350),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

InputMode = Literal["touch", "mouse"]


def get_pulse_duration(
    driver_type: str,
    distance: float,
    input_mode: InputMode = "touch",
    **kwargs,
) -> int:
    """Return pulse duration in ms for the given driver type and distance.

    Parameters
    ----------
    driver_type:
        One of ``"follow-guide"``, ``"2d"``, ``"learned"``, ``"taskguide"``,
        ``"target-arrow"``, ``"guide-follow"``.
    distance:
        Movement distance in world units (or pixels for ``"2d"`` and
        ``"target-arrow"``).
    input_mode:
        ``"touch"`` or ``"mouse"`` (only relevant for ``"follow-guide"``).

    Keyword Args
    ------------
    mouse_variant:
        ``"00849"`` or ``"00850"`` (default) for mouse timing curve variant.
    route_mode:
        For ``"target-arrow"``: ``"route"``, ``"continue"``, or ``"generic"``.
        For ``"guide-follow"``: ``"route"``, ``"money"``, or ``"generic"``.

    Returns
    -------
    Pulse duration in milliseconds.
    """
    mouse_variant = kwargs.get("mouse_variant", "00850")
    route_mode = kwargs.get("route_mode", "generic")

    if driver_type == "follow-guide":
        if input_mode == "touch":
            return interpolate_pulse_duration(distance, _TOUCH_TIERS, (460, 740))
        elif mouse_variant == "00849":
            return interpolate_pulse_duration(distance, _MOUSE_00849_TIERS, (330, 420))
        else:
            return interpolate_pulse_duration(distance, _MOUSE_00850_TIERS, (360, 500))

    elif driver_type == "2d":
        return interpolate_pulse_duration(distance, _PIXEL_2D_TIERS, (620, 1050))

    elif driver_type == "learned":
        return interpolate_pulse_duration(distance, _LEARNED_00862_TIERS, (940, 940))

    elif driver_type == "taskguide":
        return interpolate_pulse_duration(distance, _TASKGUIDE_WORLD_TARGET_TIERS, (170, 1100))

    elif driver_type == "target-arrow":
        if route_mode == "route":
            return interpolate_pulse_duration(distance, _TARGET_ARROW_ROUTE_TIERS, (360, 360))
        elif route_mode == "continue":
            return 150
        else:
            return interpolate_pulse_duration(distance, _TARGET_ARROW_PX_TIERS, (820, 820))

    elif driver_type == "guide-follow":
        if route_mode == "route":
            return interpolate_pulse_duration(distance, _GUIDE_FOLLOW_ROUTE_TIERS, (560, 560))
        elif route_mode == "money":
            return interpolate_pulse_duration(distance, _GUIDE_FOLLOW_MONEY_TIERS, (1350, 1350))
        else:
            return interpolate_pulse_duration(distance, _GUIDE_FOLLOW_GENERIC_TIERS, (900, 900))

    # Fallback — waypoint scheme
    return interpolate_pulse_duration(distance, _WAYPOINT_TIERS, (320, 430))
