"""World ↔ joystick vector math for game-playing agents.

Ports the JavaScript vector functions from the Node.js game drivers
(``follow-guide-audited.mjs`` et al.) into Python.

A calibration *basis* defines how joystick direction maps to world-space
movement for a given game::

    basis = {
        "screen_right": {"x": x1, "z": z1},  # joystick right → world vector
        "screen_down":  {"x": x2, "z": z2},  # joystick down  → world vector
    }

The typical basis is a 2×2 matrix with columns *screen_right* and
*screen_down*, mapping 2D joystick (dx, dy) to 2D world (x, z).
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Vec2 = tuple[float, float]
Basis = dict[str, dict[str, float]]


# ---------------------------------------------------------------------------
# Core vector math
# ---------------------------------------------------------------------------


def world_vector_from_stick(
    basis: Basis,
    dx: float,
    dy: float,
    magnitude: float = 1.0,
) -> Vec2:
    """Convert joystick (dx, dy) to a world-space (x, z) vector.

    Parameters
    ----------
    basis:
        Calibration basis with ``screen_right`` and ``screen_down`` keys.
    dx:
        Joystick X in [-1, 1] (right positive).
    dy:
        Joystick Y in [-1, 1] (down positive, matching screen coords).
    magnitude:
        Scale factor for the output vector (default 1.0).

    Returns
    -------
    ``(world_x, world_z)``
    """
    sr = basis["screen_right"]
    sd = basis["screen_down"]
    wx = (dx * sr["x"] + dy * sd["x"]) * magnitude
    wz = (dx * sr["z"] + dy * sd["z"]) * magnitude
    return (wx, wz)


def screen_vector_for_world(basis: Basis, world_x: float, world_z: float) -> Vec2:
    """Project a world-space (x, z) vector back to screen joystick (dx, dy).

    This is the **inverse** of :func:`world_vector_from_stick`.
    Uses the Cramer's rule solution of the 2×2 basis system.

    Returns
    -------
    ``(dx, dy)`` — normalised joystick direction in [-1, 1].
    """
    sr = basis["screen_right"]
    sd = basis["screen_down"]

    det = sr["x"] * sd["z"] - sr["z"] * sd["x"]
    if abs(det) < 1e-12:
        return (0.0, 0.0)

    dx = (world_x * sd["z"] - world_z * sd["x"]) / det
    dy = (sr["x"] * world_z - sr["z"] * world_x) / det
    return (dx, dy)


def solve_stick_for_world(
    basis: Basis,
    desired_x: float,
    desired_z: float,
    pulse_distance: float | None = None,
) -> Vec2:
    """Solve for the joystick (dx, dy) that produces *desired* world movement.

    When *pulse_distance* is given, the output stick is scaled so the
    resulting world movement covers exactly that distance (useful for
    normalising pulse timing calculations).

    Returns
    -------
    ``(dx, dy)`` — joystick direction to pass to ``GameRunner.joystick_pulse()``.
    """
    dx, dy = screen_vector_for_world(basis, desired_x, desired_z)

    if pulse_distance is not None and pulse_distance > 0:
        actual_distance = math.hypot(desired_x, desired_z)
        if actual_distance > 0:
            scale = pulse_distance / actual_distance
            dx *= scale
            dy *= scale

    # Clamp to [-1, 1]
    mag = math.hypot(dx, dy)
    if mag > 1.0:
        dx /= mag
        dy /= mag

    return (dx, dy)


def normalize_world_vector(vx: float, vz: float) -> Vec2:
    """Return a unit vector in the same direction."""
    mag = math.hypot(vx, vz)
    if mag < 1e-12:
        return (0.0, 0.0)
    return (vx / mag, vz / mag)


def world_distance(ax: float, az: float, bx: float, bz: float) -> float:
    """Euclidean distance between two world points."""
    return math.hypot(ax - bx, az - bz)


# ---------------------------------------------------------------------------
# Pulse-timing interpolation helper
# ---------------------------------------------------------------------------


def interpolate_pulse_duration(
    distance: float,
    tiers: list[tuple[float, float]],
    fallback_range: tuple[float, float] = (360, 500),
) -> int:
    """Look up pulse duration (ms) from a tiered distance→time mapping.

    Parameters
    ----------
    distance:
        World distance to cover.
    tiers:
        Sorted list of ``(max_distance, duration_ms)`` thresholds.
        Last entry should have a large max_distance as catch-all.
    fallback_range:
        ``(min_ms, max_ms)`` when distance exceeds all tiers.

    Returns
    -------
    Pulse duration in milliseconds (int).
    """
    for max_dist, duration in tiers:
        if distance <= max_dist:
            return int(round(duration))

    # Fallback: linear interpolation in the fallback range
    fallback_min, fallback_max = fallback_range
    if distance > tiers[-1][0]:
        return int(round(fallback_max))
    return int(round(fallback_min))
