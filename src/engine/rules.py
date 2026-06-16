"""Rule data models and rule engine core for rule-based game playing.

Defines the ``Rule`` and ``RuleSet`` data models, the ``RuleEngine`` that
executes rules against real-time game state, and the strategy factory that
dispatches to the correct strategy implementation for each driver type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from configs.game_profiles import get_profile


# ---------------------------------------------------------------------------
# Rule data models
# ---------------------------------------------------------------------------


@dataclass
class GameRule:
    """A single gameplay rule — a named heuristic with parameters.

    Rules are the output of VLM/API "rule extraction" (Modes 4 & 5)
    and the building block of the rule engine's decision process.
    """

    name: str
    priority: int = 0  # higher = applied first
    condition: str = ""  # human-readable condition description
    action_template: dict[str, Any] = field(default_factory=lambda: {
        "action": "wait",
        "params": {"duration_ms": 500},
    })
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleSet:
    """A collection of rules for a specific game + driver type.

    Serialisable to/from JSON for caching VLM/API-extracted rules.
    """

    game_id: str
    driver_type: str
    rules: list[GameRule] = field(default_factory=list)
    source: str = ""  # "vlm", "api", "manual", "builtin"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------------


class RuleEngine:
    """Executes rules against real-time game state to produce actions.

    Works as a state machine with pluggable strategy modules.  For each
    observation step, the engine:

    1. Checks terminal conditions (win/done).
    2. Selects the current target using the strategy's targeting logic.
    3. Plans a route to the target (direct or waypoint).
    4. Computes pulse duration and dispatches a joystick action.
    """

    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        profile = get_profile(game_id)
        if profile is None:
            raise ValueError(f"Unknown game_id: {game_id}")
        self.profile = profile
        self.driver_type: str = profile.get("driver_type", "follow-guide-audited")

        # State
        self.step_count: int = 0
        self.last_action: dict[str, Any] = {}
        self.stuck_streak: int = 0
        self.last_player_pos: tuple[float, float] | None = None
        self._learned_obstacles: list[dict[str, Any]] = []

    def step(self, state: dict[str, Any], visual: dict[str, Any] | None = None) -> dict[str, Any]:
        """Produce an action for the current *state*.

        Parameters
        ----------
        state:
            Game state from ``ProbeAdapter.observe_fast()``.
        visual:
            Optional result from ``VisualAnalyzer.analyze()``.

        Returns
        -------
        Action dict with ``action``, ``params``, and ``reason`` keys.
        """
        self.step_count += 1

        # 1. Terminal check
        if state.get("win") or state.get("done"):
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "game_over"}

        # 2. Stuck detection
        player = state.get("player") or {}
        wp = player.get("worldPosition") or {}
        current_pos = (wp.get("x", 0), wp.get("z", 0))
        if self.last_player_pos:
            moved = self._world_dist(current_pos, self.last_player_pos)
            if moved < 0.05:
                self.stuck_streak += 1
            else:
                self.stuck_streak = 0
        self.last_player_pos = current_pos

        # 3. Delegation to driver-type-specific strategy
        dt = self.driver_type
        if dt == "follow-guide-audited":
            return self._strategy_follow_guide(state, visual)
        elif dt == "2d-audited":
            return self._strategy_2d(state, visual)
        elif dt == "learned":
            return self._strategy_learned(state, visual)
        elif dt in ("taskguide", "target-arrow", "guide-follow"):
            return self._strategy_generic(state, visual)
        else:
            return self._strategy_follow_guide(state, visual)

    # ------------------------------------------------------------------
    # Strategy: Follow Guide (Family A — 7 games)
    # ------------------------------------------------------------------

    def _strategy_follow_guide(
        self, state: dict[str, Any], visual: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Profile-based guide follower — canonical architecture."""
        import math

        from src.engine.vector import (
            world_vector_from_stick,
            world_distance,
            solve_stick_for_world,
            normalize_world_vector,
        )
        from src.engine.pulse import get_pulse_duration

        profile = self.profile
        basis = profile.get("calibration", {}).get("basis", {})
        input_mode = profile.get("joystick", {}).get("input_mode", "touch")

        # Get player position
        player = state.get("player") or {}
        player_world = player.get("worldPosition") or {}
        px, pz = player_world.get("x", 0), player_world.get("z", 0)

        # Get guide candidates from state
        guide_candidates = state.get("guide_or_target_candidates", [])
        guide_summary = state.get("guideSummary", {})

        # --- Target selection ---
        target = self._select_target(px, pz, guide_candidates, visual, guide_summary)

        if target is None:
            # No target — check completion or wait
            if self._is_completion_state(state, guide_candidates, visual):
                return {"action": "wait", "params": {"duration_ms": 1000},
                        "reason": "completion_detected"}
            # Fallback: try moving in a direction
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "no_target"}

        tx, tz = target

        # --- Compute desired movement ---
        dx_world = tx - px
        dz_world = tz - pz
        dist = math.hypot(dx_world, dz_world)

        # --- Stuck handling ---
        if self.stuck_streak >= 5:
            # Escape rotation
            import random
            escape_angle = random.uniform(-1.0, 1.0)
            dx_stick = math.cos(escape_angle)
            dy_stick = math.sin(escape_angle)
            return {
                "action": "move",
                "params": {
                    "dx": dx_stick,
                    "dy": dy_stick,
                    "duration_ms": get_pulse_duration("follow-guide", 2.0, input_mode),
                },
                "reason": f"stuck_escape_{self.stuck_streak}",
            }

        # --- Compute joystick direction ---
        if dist < 0.5:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "arrived_at_target"}

        dx_stick, dy_stick = solve_stick_for_world(basis, dx_world, dz_world)

        # --- Pulse duration ---
        duration_ms = get_pulse_duration("follow-guide", dist, input_mode)

        return {
            "action": "move",
            "params": {"dx": dx_stick, "dy": dy_stick, "duration_ms": duration_ms},
            "reason": f"follow_guide_target_dist={dist:.2f}",
        }

    # ------------------------------------------------------------------
    # Strategy: 2D (Family B — 00853)
    # ------------------------------------------------------------------

    def _strategy_2d(
        self, state: dict[str, Any], visual: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """2D pixel-coordinate driver."""
        from src.engine.pulse import get_pulse_duration

        player = state.get("player") or {}
        screen_pos = player.get("screenPosition", {})
        px, py = screen_pos.get("x", 0), screen_pos.get("y", 0)

        guide_candidates = state.get("guide_or_target_candidates", [])
        guide_summary = state.get("guideSummary", {})

        # Find nearest target in screen space
        best_target = None
        best_dist = float("inf")

        for candidate in guide_candidates:
            sp = candidate.get("screenPosition", {})
            if sp:
                dx = sp.get("x", 0) - px
                dy = sp.get("y", 0) - py
                d = (dx * dx + dy * dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_target = (sp.get("x", 0), sp.get("y", 0))

        # Also check visual arrows
        if visual and not best_target:
            arrow = visual.get("arrow")
            if arrow and isinstance(arrow, dict):
                ax, ay = arrow.get("x", 0), arrow.get("y", 0)
                dx = ax - px
                dy = ay - py
                best_target = (ax, ay)
                best_dist = (dx * dx + dy * dy) ** 0.5

        if best_target is None or best_dist < 10:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "no_target_2d"}

        # Normalise to joystick coordinates
        profile = self.profile
        anchor = profile.get("joystick", {}).get("anchor", [91, 699])
        radius = profile.get("joystick", {}).get("radius", 50)

        dx_px = best_target[0] - anchor[0]
        dy_px = best_target[1] - anchor[1]
        dist_px = (dx_px * dx_px + dy_px * dy_px) ** 0.5

        if dist_px < 5:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "arrived_2d"}

        dx_stick = dx_px / (radius * 2) if dist_px > 0 else 0
        dy_stick = dy_px / (radius * 2) if dist_px > 0 else 0
        dx_stick = max(-1, min(1, dx_stick))
        dy_stick = max(-1, min(1, dy_stick))

        duration_ms = get_pulse_duration("2d", dist_px)

        return {
            "action": "move",
            "params": {"dx": dx_stick, "dy": dy_stick, "duration_ms": duration_ms},
            "reason": f"follow_2d_target_dist={dist_px:.0f}px",
        }

    # ------------------------------------------------------------------
    # Strategy: Learned (Family C — 00862, 00863 base)
    # ------------------------------------------------------------------

    def _strategy_learned(
        self, state: dict[str, Any], visual: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Runtime-calibrated guide follower (00862-style)."""
        import math

        from src.engine.vector import (
            world_vector_from_stick,
            world_distance,
            solve_stick_for_world,
        )
        from src.engine.pulse import get_pulse_duration

        profile = self.profile
        basis = profile.get("calibration", {}).get("basis", {})
        input_mode = profile.get("joystick", {}).get("input_mode", "touch")

        player = state.get("player") or {}
        player_world = player.get("worldPosition") or {}
        px, pz = player_world.get("x", 0), player_world.get("z", 0)

        guide_summary = state.get("guideSummary", {})
        cur_target = guide_summary.get("curTargetNode") or guide_summary.get("likelyCurrentTarget", {})

        # Try backend target first
        if isinstance(cur_target, dict):
            target_pos = (cur_target.get("x", 0), cur_target.get("z", 0))
        else:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "no_backend_target"}

        dx_world = target_pos[0] - px
        dz_world = target_pos[1] - pz
        dist = math.hypot(dx_world, dz_world)

        if dist < 0.5:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "arrived_learned"}

        dx_stick, dy_stick = solve_stick_for_world(basis, dx_world, dz_world)
        duration_ms = get_pulse_duration("learned", dist)

        return {
            "action": "move",
            "params": {"dx": dx_stick, "dy": dy_stick, "duration_ms": duration_ms},
            "reason": f"learned_target_dist={dist:.2f}",
        }

    # ------------------------------------------------------------------
    # Generic fallback strategy
    # ------------------------------------------------------------------

    def _strategy_generic(
        self, state: dict[str, Any], visual: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Generic fallback — uses state + visual."""
        return self._strategy_follow_guide(state, visual)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _select_target(
        self,
        px: float, pz: float,
        guide_candidates: list[dict[str, Any]],
        visual: dict[str, Any] | None,
        guide_summary: dict[str, Any],
    ) -> tuple[float, float] | None:
        """Score and select the best target candidate.

        Ports ``chooseTargetCandidate()`` from the Node.js drivers.
        """
        import math

        best_score = -float("inf")
        best_pos = None

        # Score each guide candidate
        for candidate in guide_candidates:
            wp = candidate.get("worldPosition", {})
            if not wp:
                continue
            cx, cz = wp.get("x", 0), wp.get("z", 0)
            dx = cx - px
            dz = cz - pz
            dist = math.hypot(dx, dz)
            if dist < 0.01:
                continue

            score = 0.5 - min(dist / 120, 0.25)  # direction + distance

            # Bonus for target nodes
            path = candidate.get("path", "")
            import re
            if re.search(r"/target\d?$", path):
                score += 0.08

            if score > best_score:
                best_score = score
                best_pos = (cx, cz)

        # Visual arrow as target (if no candidates or low score)
        if visual and (best_pos is None or best_score < 0.35):
            stick = visual.get("stick")
            if stick and isinstance(stick, dict):
                from src.engine.vector import world_vector_from_stick
                basis = self.profile.get("calibration", {}).get("basis", {})
                if basis:
                    vx, vz = world_vector_from_stick(basis, stick.get("dx", 0), stick.get("dy", 0))
                    arrow_pos = (px + vx * 3, pz + vz * 3)
                    arrow_score = 0.6  # visual arrow gets high base score
                    if arrow_score > best_score:
                        best_pos = arrow_pos

        return best_pos

    def _is_completion_state(
        self,
        state: dict[str, Any],
        guide_candidates: list[dict[str, Any]],
        visual: dict[str, Any] | None,
    ) -> bool:
        """Check if the game appears to be in a completion state.

        Ports ``isCompletionState()`` from profile-based drivers.
        """
        if state.get("done") or state.get("win"):
            return True

        has_backend_target = bool(
            state.get("target", {}).get("target", {}).get("path")
            or state.get("guideSummary", {}).get("likelyCurrentTarget")
        )
        has_guide_arrows = len(guide_candidates) > 0
        has_visible_guide = bool(visual and visual.get("stick"))

        return not has_backend_target and not has_guide_arrows and not has_visible_guide

    @staticmethod
    def _world_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        import math
        return math.hypot(a[0] - b[0], a[1] - b[1])
