#!/usr/bin/env python3
"""Compare rule, hierarchical, and tiny beam-search planning variants offline.

Loads a processed run (``processed-runs/<game_id>/steps.jsonl``) and evaluates
four decision strategies on the same trajectory:

1. ``rule`` — pure L0 ``RuleEngine`` baseline.
2. ``hierarchical_mock_N`` — ``HierarchicalPlanner`` with a deterministic mock
   L2 client that replans every *N* steps.
3. ``hierarchical_short`` / ``hierarchical_long`` — same mock L2 but with the
   system prompt constrained to request 3 or 8 intentions, respectively.
4. ``beam_2step`` — a tiny 2-step beam search over ``{move, tap, wait}`` scored
   by a heuristic distance to the recorded future state.

Output is written to ``experiment_search_plan_variants.json`` in the repo root.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agent.hierarchical_planner import HierarchicalPlanner  # noqa: E402
from src.agent.rule_update import RuleParameters  # noqa: E402
from src.engine.rules import RuleEngine  # noqa: E402
from src.experiments import offline_replay as replay  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_ROOT = ROOT / "processed-runs"
ACTION_SPACE = ["move", "tap", "wait"]


def _player_pos(state: dict[str, Any]) -> tuple[float, float, float]:
    """Extract (x, y, z) from a probe state."""
    player = (state or {}).get("player") or {}
    wp = player.get("worldPosition") or {}
    return (float(wp.get("x", 0.0)), float(wp.get("y", 0.0)), float(wp.get("z", 0.0)))


def _key_vector(state: dict[str, Any]) -> dict[str, float]:
    """Return numeric key values from a probe state."""
    return {k: float(v) for k, v in (state or {}).get("keyNumbers", {}).items()}


def _estimate_move_step(states: list[dict[str, Any]]) -> float:
    """Estimate average xz movement per step from recorded states."""
    deltas: list[float] = []
    for prev, curr in zip(states, states[1:]):
        p1 = _player_pos(prev)
        p2 = _player_pos(curr)
        if p1 == (0.0, 0.0, 0.0) or p2 == (0.0, 0.0, 0.0):
            continue
        deltas.append(math.hypot(p2[0] - p1[0], p2[2] - p1[2]))
    return sum(deltas) / len(deltas) if deltas else 1.0


def _first_active_target(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first active candidate from the probe state."""
    for c in (state or {}).get("guide_or_target_candidates", []):
        if c.get("active", True):
            return c
    return None


def _predict_next_state(state: dict[str, Any], action: str, step_size: float) -> dict[str, Any]:
    """Approximate the next state after executing *action* from *state*."""
    pred = {
        "player": {"worldPosition": {"x": 0.0, "y": 0.0, "z": 0.0}},
        "keyNumbers": dict((state or {}).get("keyNumbers", {})),
    }
    x, y, z = _player_pos(state)
    pred["player"]["worldPosition"] = {"x": x, "y": y, "z": z}

    target = _first_active_target(state)
    if action == "move" and target is not None:
        tx, _, tz = _player_pos({"player": target})
        dx, dz = tx - x, tz - z
        dist = math.hypot(dx, dz)
        if dist > 0:
            scale = min(step_size, dist) / dist
            pred["player"]["worldPosition"]["x"] = x + dx * scale
            pred["player"]["worldPosition"]["z"] = z + dz * scale
    return pred


def _state_distance(pred: dict[str, Any], true: dict[str, Any], key_weight: float = 0.05) -> float:
    """Heuristic distance between a predicted state and the recorded next state."""
    pp = _player_pos(pred)
    tp = _player_pos(true)
    dist = math.hypot(pp[0] - tp[0], pp[2] - tp[2])

    pk = _key_vector(pred)
    tk = _key_vector(true)
    key_diff = 0.0
    for k in set(pk) | set(tk):
        key_diff += abs(pk.get(k, 0.0) - tk.get(k, 0.0))
    return dist + key_weight * key_diff


class _MockL2UpdateClient:
    """No-op L2 rule-update client used only for the update path."""

    def __init__(self) -> None:
        self.update_calls = 0

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> SimpleNamespace:
        self.update_calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"update_type": "none", "confidence": 0.0})))]
        )


class _MockL2Planner(HierarchicalPlanner):
    """Hierarchical planner with a deterministic, state-resolving L2 mock."""

    def __init__(self, *, horizon: int = 3, **kwargs: Any) -> None:
        self._mock_horizon = horizon
        super().__init__(**kwargs)

    def _run_l2(self, state: dict[str, Any]) -> None:
        self.l2_calls += 1
        target = _first_active_target(state)
        valid: list[dict[str, Any]] = []
        if target is not None:
            x, y = self._design_to_css(target["screenPosition"]["x"], target["screenPosition"]["y"])
            for _ in range(self._mock_horizon):
                valid.append({"action": "move", "dx": x, "dy": y, "duration_ms": 320})
        else:
            for _ in range(self._mock_horizon):
                valid.append({"action": "wait", "duration_ms": 500})
        self._l2_queue = valid
        self._macro_plan = {"plan": valid, "reason": "mock deterministic plan"}
        logger.debug("Mock L2 plan: %d items, first=%s", len(valid), valid[0])


def _run_rule_baseline(states: list[dict[str, Any]], true_actions: list[dict[str, Any]], game_id: str) -> dict[str, Any]:
    """Run the pure rule engine baseline."""
    rule_engine = RuleEngine(game_id, rule_params=RuleParameters())
    records = []
    for state, true_action in zip(states, true_actions):
        pred = replay.normalize_action(rule_engine.step(state, visual=None))
        records.append(_record(pred, true_action))
    return _summarize("rule", records)


def _run_hierarchical(
    states: list[dict[str, Any]],
    true_actions: list[dict[str, Any]],
    game_id: str,
    name: str,
    l2_interval: int,
    horizon: int = 3,
) -> dict[str, Any]:
    """Run a hierarchical planner variant with the deterministic mock L2."""
    rule_engine = RuleEngine(game_id, rule_params=RuleParameters())
    planner = _MockL2Planner(
        horizon=horizon,
        rule_engine=rule_engine,
        api_client=_MockL2UpdateClient(),
        l1_interval=0,
        l2_interval=l2_interval,
        rule_params=RuleParameters(),
    )
    records = []
    last_composite = 0.0
    stall_streak = 0
    conflict_streak = 0
    for i, (state, true_action) in enumerate(zip(states, true_actions)):
        ctx = replay.build_fake_context(
            step_number=i + 1,
            state=state,
            last_composite=last_composite,
            stall_streak=stall_streak,
            conflict_streak=conflict_streak,
            visual_struct=None,
            metadata={"game_id": game_id},
        )
        pred = replay.normalize_action(planner.step(ctx))
        records.append(_record(pred, true_action))
        type_match, action_match, _ = replay.actions_match(pred, true_action)
        if action_match:
            stall_streak = conflict_streak = 0
            last_composite = 1.0
        else:
            if pred.get("action") == "wait" and true_action.get("action") != "wait":
                stall_streak += 1
            if pred.get("action") != true_action.get("action"):
                conflict_streak += 1
            last_composite = 0.3 if type_match else 0.0
    return _summarize(name, records)


def _run_beam_search(
    states: list[dict[str, Any]],
    true_actions: list[dict[str, Any]],
    game_id: str,
    horizon: int = 2,
) -> dict[str, Any]:
    """Run a tiny beam search over action sequences."""
    step_size = _estimate_move_step(states)
    rule_engine = RuleEngine(game_id, rule_params=RuleParameters())
    records = []
    for i, (state, true_action) in enumerate(zip(states, true_actions)):
        if i + horizon >= len(states):
            pred = replay.normalize_action(rule_engine.step(state, visual=None))
        else:
            pred = _beam_decision(state, states[i + 1 : i + 1 + horizon], step_size)
        records.append(_record(pred, true_action))
    return _summarize(f"beam_{horizon}step", records)


def _beam_decision(state: dict[str, Any], future_states: list[dict[str, Any]], step_size: float) -> dict[str, Any]:
    """Enumerate action sequences of length *len(future_states)* and pick best."""
    horizon = len(future_states)

    def expand(prefix: list[str], current_state: dict[str, Any]) -> list[tuple[list[str], dict[str, Any]]]:
        if len(prefix) == horizon:
            return [(prefix, current_state)]
        results: list[tuple[list[str], dict[str, Any]]] = []
        for action in ACTION_SPACE:
            next_state = _predict_next_state(current_state, action, step_size)
            results.extend(expand(prefix + [action], next_state))
        return results

    sequences = expand([], state)
    best_seq, best_score = None, float("inf")
    for seq, final_state in sequences:
        score = _state_distance(final_state, future_states[-1])
        if score < best_score:
            best_score = score
            best_seq = seq

    first_action = best_seq[0] if best_seq else "wait"
    target = _first_active_target(state)
    if first_action == "move":
        return {"action": "move", "params": {"target_name": target.get("name", "") if target else ""}, "reason": "beam_search"}
    if first_action == "tap":
        return {"action": "tap", "params": {"target_name": target.get("name", "") if target else ""}, "reason": "beam_search"}
    return {"action": "wait", "params": {"duration_ms": 500}, "reason": "beam_search"}


def _record(pred: dict[str, Any], true_action: dict[str, Any]) -> dict[str, Any]:
    type_match, action_match, cos = replay.actions_match(pred, true_action)
    return {
        "pred_action": pred,
        "true_action": true_action,
        "type_match": type_match,
        "action_match": action_match,
        "move_cosine": round(cos, 4),
    }


def _summarize(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records) or 1
    type_matches = sum(1 for r in records if r["type_match"])
    action_matches = sum(1 for r in records if r["action_match"])
    return {
        "variant": name,
        "steps": len(records),
        "type_match_rate": round(type_matches / n, 4),
        "action_match_rate": round(action_matches / n, 4),
        "type_matches": type_matches,
        "action_matches": action_matches,
        "records": records,
    }


def run_search_plan_variants(game_id: str, max_steps: int | None = None) -> dict[str, Any]:
    """Run all planning variants on one processed game."""
    game_dir = PROCESSED_ROOT / game_id
    if not game_dir.exists():
        raise FileNotFoundError(f"processed run not found: {game_dir}")

    steps = replay.load_steps(game_dir)
    if max_steps is not None:
        steps = steps[:max_steps]

    states: list[dict[str, Any]] = []
    true_actions: list[dict[str, Any]] = []
    for step_record in steps:
        before = replay.load_state(game_dir, step_record.get("before", {}).get("state"))
        if before is None:
            continue
        state = replay.adapt_processed_state(before)
        action_record = replay.load_state(game_dir, step_record.get("action"))
        true_action = replay.adapt_ground_truth(action_record or {"type": "wait"}) or {
            "action": "wait",
            "params": {"duration_ms": 500},
            "reason": "unparseable-gt",
        }
        states.append(state)
        true_actions.append(replay.normalize_action(true_action))

    if not states:
        raise ValueError("no usable states found")

    import src.agent.hierarchical_planner as hp

    original_system = hp._L2_SYSTEM

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []

    results.append(_run_rule_baseline(states, true_actions, game_id))

    for interval in (5, 15):
        results.append(_run_hierarchical(states, true_actions, game_id, f"hierarchical_mock_{interval}", interval, horizon=3))

    hp._L2_SYSTEM = original_system.replace("Output 3-8 intentions.", "Output exactly 3 intentions.")
    results.append(_run_hierarchical(states, true_actions, game_id, "hierarchical_short", 5, horizon=3))

    hp._L2_SYSTEM = original_system.replace("Output 3-8 intentions.", "Output exactly 8 intentions.")
    results.append(_run_hierarchical(states, true_actions, game_id, "hierarchical_long", 5, horizon=8))

    hp._L2_SYSTEM = original_system

    results.append(_run_beam_search(states, true_actions, game_id, horizon=2))

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "game_id": game_id,
        "max_steps": max_steps,
        "total_states": len(states),
        "elapsed_ms": round(elapsed_ms, 2),
        "variants": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare planning variants offline.")
    parser.add_argument("--game", default="SSD_00461P01", help="Processed game ID")
    parser.add_argument("--max-steps", type=int, default=None, help="Limit number of steps")
    parser.add_argument("--output", default="experiment_search_plan_variants.json", help="Output JSON path")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run_search_plan_variants(args.game, max_steps=args.max_steps)
    out_path = Path(args.output)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %d variants to %s", len(summary["variants"]), out_path)
    for variant in summary["variants"]:
        logger.info(
            "%-28s type=%.3f action=%.3f (%d/%d)",
            variant["variant"],
            variant["type_match_rate"],
            variant["action_match_rate"],
            variant["action_matches"],
            variant["steps"],
        )


if __name__ == "__main__":
    main()
