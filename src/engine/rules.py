"""Rule data models and rule engine core for rule-based game playing.

Defines the ``Rule`` and ``RuleSet`` data models, the ``RuleEngine`` that
executes rules against real-time game state, and the strategy factory that
dispatches to the correct strategy implementation for each driver type.
"""

from __future__ import annotations

import logging
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from configs.game_profiles import get_profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Obstacle learning constants (potential-field avoidance)
# ---------------------------------------------------------------------------

#: Actual displacement below this fraction of the expected displacement counts
#: as a *blocked* move.
BLOCK_RATIO = 0.3
#: Ignore moves whose expected displacement is below this (noise floor, world units).
BLOCK_MIN_EXPECTED = 0.25
#: Consecutive blocked moves in roughly the same direction before an obstacle
#: point is recorded.
BLOCK_STREAK_THRESHOLD = 2
#: Cosine similarity above which two commanded directions count as "same direction".
BLOCK_SAME_DIR_COS = 0.7
#: Radius (world units) within which a recorded obstacle exerts repulsion.
OBSTACLE_REPULSE_RADIUS = 2.5
#: Strength of the obstacle repulsion relative to the unit target direction.
OBSTACLE_REPULSE_WEIGHT = 1.3
#: Obstacles closer than this are merged (confidence bump instead of a new point).
OBSTACLE_MERGE_DIST = 1.0
#: How far ahead of the player the obstacle point is recorded (world units).
OBSTACLE_LOOKAHEAD = 1.0
#: Number of candidate directions scored by ``_escape_direction``.
ESCAPE_NUM_DIRECTIONS = 8
#: Radius within which obstacles influence the escape-direction score.
ESCAPE_SCORE_RADIUS = 3.0
#: Minimum successful-move samples before blocked-move detection activates.
SPEED_MIN_SAMPLES = 3
#: Prior world-units/second at full stick until enough samples are collected.
SPEED_PRIOR = 14.0


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

        # ── Obstacle learning (activates the previously unused field) ──
        # Each entry: {"x", "z", "step", "count", "dir": (ux, uz)} — a world-space
        # point where commanded movement was repeatedly blocked, plus the blocked
        # direction and a confidence counter.
        self._learned_obstacles: list[dict[str, Any]] = []
        # Commanded move awaiting verification: {"pos", "dir", "expected", "step"}.
        self._prev_move: dict[str, Any] | None = None
        self._block_dir_streak: int = 0
        self._block_last_dir: tuple[float, float] | None = None
        # World-speed estimate (units/second at full stick), median of successful
        # per-move samples; seeded with SPEED_PRIOR.
        self._speed_samples: deque[float] = deque(maxlen=24)
        self._speed_est: float = SPEED_PRIOR

        # ── World model (optional attach, see HybridAgent) ──
        self.world_model: Any = None
        self._current_plan_id: str | None = None
        self.stale_replans: int = 0

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

        # 2b. Verify the previous commanded move against the actual displacement
        # and learn obstacle points from repeatedly blocked directions.
        if wp:
            self._learn_from_last_move(current_pos)
        self.last_player_pos = current_pos

        # 2c. If the world model marked the current follow-target plan stale
        # (scene shift), drop motion state and re-select locally.
        self._check_plan_stale()

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
        from src.engine.vector import (
            solve_stick_for_world,
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

        # Register the follow target as a plan artifact so a later scene shift
        # marks it stale and triggers a local re-plan (see ``_check_plan_stale``).
        self._register_target_plan(guide_candidates)

        # --- Compute desired movement ---
        dx_world = tx - px
        dz_world = tz - pz
        dist = math.hypot(dx_world, dz_world)

        # --- Stuck handling ---
        if self.stuck_streak >= 5:
            # Escape along the candidate direction that points away from the
            # densest cluster of learned obstacles (random when none known).
            esc_wx, esc_wz = self._escape_direction(px, pz)
            dx_stick, dy_stick = solve_stick_for_world(basis, esc_wx, esc_wz)
            duration_ms = get_pulse_duration("follow-guide", 2.0, input_mode)
            self._note_move((px, pz), dx_stick, dy_stick, duration_ms, basis)
            return {
                "action": "move",
                "params": {
                    "dx": dx_stick,
                    "dy": dy_stick,
                    "duration_ms": duration_ms,
                },
                "reason": f"stuck_escape_{self.stuck_streak}",
            }

        # --- Compute joystick direction ---
        if dist < 0.5:
            return {"action": "wait", "params": {"duration_ms": 500},
                    "reason": "arrived_at_target"}

        # Potential-field steering: target direction + repulsion from nearby
        # learned obstacle points.
        steer_wx, steer_wz = self._steer_around_obstacles(px, pz, dx_world, dz_world, dist)

        dx_stick, dy_stick = solve_stick_for_world(basis, steer_wx, steer_wz)

        # --- Pulse duration ---
        duration_ms = get_pulse_duration("follow-guide", dist, input_mode)

        self._note_move((px, pz), dx_stick, dy_stick, duration_ms, basis)

        return {
            "action": "move",
            "params": {"dx": dx_stick, "dy": dy_stick, "duration_ms": duration_ms},
            "reason": f"follow_guide_target_dist={dist:.2f}",
        }

    # ------------------------------------------------------------------
    # Obstacle learning + potential-field avoidance
    # ------------------------------------------------------------------

    def _note_move(
        self,
        pos: tuple[float, float],
        dx_stick: float,
        dy_stick: float,
        duration_ms: float,
        basis: dict[str, Any],
    ) -> None:
        """Record a commanded move so the next step can verify its effect."""
        from src.engine.vector import world_vector_from_stick

        mag = math.hypot(dx_stick, dy_stick)
        if mag < 1e-6 or duration_ms <= 0 or not basis:
            self._prev_move = None
            return
        wx, wz = world_vector_from_stick(basis, dx_stick, dy_stick)
        wmag = math.hypot(wx, wz)
        if wmag < 1e-6:
            self._prev_move = None
            return
        self._prev_move = {
            "pos": pos,
            "dir": (wx / wmag, wz / wmag),
            "expected": mag * (duration_ms / 1000.0) * self._speed_est,
            "step": self.step_count,
        }

    def _learn_from_last_move(self, current_pos: tuple[float, float]) -> None:
        """Compare expected vs actual displacement of the previous move.

        A move whose actual displacement stays below ``BLOCK_RATIO`` of the
        expected one for ``BLOCK_STREAK_THRESHOLD`` consecutive same-direction
        moves records an obstacle point ahead of the blocked direction.
        Successful moves feed the world-speed estimate instead.
        """
        prev = self._prev_move
        self._prev_move = None
        if prev is None:
            return
        expected = prev["expected"]
        ax = current_pos[0] - prev["pos"][0]
        az = current_pos[1] - prev["pos"][1]
        actual = math.hypot(ax, az)
        if expected < BLOCK_MIN_EXPECTED:
            return
        ratio = actual / expected
        if ratio >= BLOCK_RATIO:
            # Successful (or at least unblocked) move — refine the speed estimate.
            dur_dir_mag = expected / self._speed_est if self._speed_est > 0 else 0.0
            if dur_dir_mag > 1e-6:
                self._speed_samples.append(actual / dur_dir_mag)
                if len(self._speed_samples) >= SPEED_MIN_SAMPLES:
                    s = sorted(self._speed_samples)
                    self._speed_est = s[len(s) // 2]
            self._block_dir_streak = 0
            self._block_last_dir = None
            return

        # Blocked move — require a consecutive same-direction repeat.
        d = prev["dir"]
        if (
            self._block_last_dir is not None
            and d[0] * self._block_last_dir[0] + d[1] * self._block_last_dir[1]
            >= BLOCK_SAME_DIR_COS
        ):
            self._block_dir_streak += 1
        else:
            self._block_dir_streak = 1
        self._block_last_dir = d

        if self._block_dir_streak >= BLOCK_STREAK_THRESHOLD:
            self._record_obstacle(
                prev["pos"][0] + d[0] * OBSTACLE_LOOKAHEAD,
                prev["pos"][1] + d[1] * OBSTACLE_LOOKAHEAD,
                d,
            )
            self._block_dir_streak = 0
            self._block_last_dir = None

    def _record_obstacle(self, x: float, z: float, d: tuple[float, float]) -> None:
        """Record (or reinforce) an obstacle point at world ``(x, z)``."""
        for obs in self._learned_obstacles:
            if math.hypot(obs["x"] - x, obs["z"] - z) < OBSTACLE_MERGE_DIST:
                obs["count"] += 1
                obs["step"] = self.step_count
                obs["x"] = (obs["x"] + x) / 2
                obs["z"] = (obs["z"] + z) / 2
                logger.info(
                    "step %d: obstacle reinforced at (%.2f, %.2f) count=%d",
                    self.step_count, obs["x"], obs["z"], obs["count"],
                )
                return
        self._learned_obstacles.append(
            {"x": x, "z": z, "step": self.step_count, "count": 1, "dir": d}
        )
        logger.info(
            "step %d: obstacle learned at (%.2f, %.2f) dir=(%.2f, %.2f)",
            self.step_count, x, z, d[0], d[1],
        )

    def _steer_around_obstacles(
        self, px: float, pz: float, dx_world: float, dz_world: float, dist: float
    ) -> tuple[float, float]:
        """Deflect the desired world vector away from nearby learned obstacles.

        Potential-field method: unit target direction plus, for each obstacle
        within ``OBSTACLE_REPULSE_RADIUS``, a repulsion term pointing from the
        obstacle to the player, weighted by proximity and confidence. The
        result keeps the original magnitude ``dist`` so pulse timing is
        unaffected.
        """
        if dist < 1e-6 or not self._learned_obstacles:
            return (dx_world, dz_world)
        ux, uz = dx_world / dist, dz_world / dist
        rx = rz = 0.0
        for obs in self._learned_obstacles:
            ox = px - obs["x"]
            oz = pz - obs["z"]
            r = math.hypot(ox, oz)
            if r < 1e-6 or r >= OBSTACLE_REPULSE_RADIUS:
                continue
            w = OBSTACLE_REPULSE_WEIGHT * (1 - r / OBSTACLE_REPULSE_RADIUS) * min(obs["count"], 3)
            rx += w * ox / r
            rz += w * oz / r
        if rx == 0.0 and rz == 0.0:
            return (dx_world, dz_world)
        sx, sz = ux + rx, uz + rz
        smag = math.hypot(sx, sz)
        if smag < 1e-6:
            return (dx_world, dz_world)
        return (sx / smag * dist, sz / smag * dist)

    def _escape_direction(self, px: float, pz: float) -> tuple[float, float]:
        """Pick an escape direction (unit world vector) for stuck handling.

        Scores ``ESCAPE_NUM_DIRECTIONS`` evenly spaced directions by alignment
        with the away-from-obstacle direction of every learned obstacle within
        ``ESCAPE_SCORE_RADIUS`` (proximity- and confidence-weighted), and
        returns the best one. Falls back to a random direction when no obstacle
        is known.
        """
        if not self._learned_obstacles:
            angle = random.uniform(0, 2 * math.pi)
            return (math.cos(angle), math.sin(angle))
        best_dir = (0.0, 0.0)
        best_score = -float("inf")
        for k in range(ESCAPE_NUM_DIRECTIONS):
            angle = 2 * math.pi * k / ESCAPE_NUM_DIRECTIONS
            ux, uz = math.cos(angle), math.sin(angle)
            score = 0.0
            for obs in self._learned_obstacles:
                ox = px - obs["x"]
                oz = pz - obs["z"]
                r = math.hypot(ox, oz)
                if r >= ESCAPE_SCORE_RADIUS:
                    continue
                if r < 1e-6:
                    # Standing on the obstacle point: penalise the recorded
                    # blocked direction, reward its opposite.
                    align = -(ux * obs["dir"][0] + uz * obs["dir"][1])
                    score += align * min(obs["count"], 3)
                    continue
                away_x, away_z = ox / r, oz / r
                align = ux * away_x + uz * away_z
                score += align * (1 - r / ESCAPE_SCORE_RADIUS) * min(obs["count"], 3)
            if score > best_score:
                best_score = score
                best_dir = (ux, uz)
        if best_score == -float("inf"):
            angle = random.uniform(0, 2 * math.pi)
            return (math.cos(angle), math.sin(angle))
        return best_dir

    # ------------------------------------------------------------------
    # World-model plan hooks
    # ------------------------------------------------------------------

    def _register_target_plan(self, guide_candidates: list[dict[str, Any]]) -> None:
        """Register the current follow target as a ``target`` plan artifact."""
        if self.world_model is None:
            return
        names = [
            str(c.get("path", "").split("/")[-1] or c.get("name", ""))
            for c in guide_candidates
            if isinstance(c, dict)
        ]
        plan_id = "follow_target"
        self.world_model.register_plan(plan_id, kind="target", depends_on=names,
                                       step=self.step_count)
        self._current_plan_id = plan_id

    def _check_plan_stale(self) -> None:
        """Reset local motion state when the world model marks the plan stale.

        This is the "local re-plan": only the derived state (stuck streak,
        pending move verification, blocked-direction streak) is dropped so the
        strategy re-selects its target from scratch; learned obstacles are
        world knowledge and survive.
        """
        if self.world_model is None or self._current_plan_id is None:
            return
        try:
            stale = self.world_model.is_stale(self._current_plan_id)
        except KeyError:
            self._current_plan_id = None
            return
        if not stale:
            return
        plan = self.world_model.get_plan(self._current_plan_id)
        scope = self.world_model.local_replan_scope([self._current_plan_id])
        logger.info(
            "step %d: plan %s stale (%s) — local replan over %s",
            self.step_count, self._current_plan_id, plan.stale_reason, sorted(scope),
        )
        self.stuck_streak = 0
        self._prev_move = None
        self._block_dir_streak = 0
        self._block_last_dir = None
        self._current_plan_id = None
        self.stale_replans += 1

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
            solve_stick_for_world,
        )
        from src.engine.pulse import get_pulse_duration

        profile = self.profile
        basis = profile.get("calibration", {}).get("basis", {})

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
