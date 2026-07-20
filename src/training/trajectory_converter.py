#!/usr/bin/env python3
"""Convert agent trajectory JSONL files into VLM training samples.

Reads trajectory files from batch experiment results (format: player, action,
keyNumbers, keyFlags, reason per step) and produces training samples in the
colleague's 7-task schema, appending them to the existing
``vlm-training-data-processed-runs/`` dataset.

Text-based tasks (no images required):
  - probe_action_effect
  - information_gain_judgment
  - pulse_response_grounding
  - progression_grounding
  - failure_recovery
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_ROOT = ROOT / "vlm-training-data-processed-runs"
DEFAULT_TRAJ_DIRS = [
    ROOT / "full_matrix_results" / "A_full" / "trajectories",
    ROOT / "full_matrix_results" / "B_tap" / "trajectories",
    ROOT / "multi_game_results" / "trajectories",
    ROOT / "batch_results" / "trajectories",
    ROOT / "rule_update_ab_results" / "trajectories",
]
TRAJ_DIRS: list[Path] = list(DEFAULT_TRAJ_DIRS)

STALL_DISPLACEMENT = 0.05
MIN_GAIN_FIELDS = 3  # >=3 changed fields → "high" info gain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_traj(path: Path) -> list[dict[str, Any]]:
    """Read a trajectory JSONL file, returning a list of step dicts."""
    steps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                steps.append(json.loads(line))
    return steps


def _game_id_from_path(path: Path) -> str:
    """Extract game_id from trajectory filename like SSD_00461P01_rule_seed42.jsonl."""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return stem


def _mode_from_path(path: Path) -> str:
    """Extract agent mode from filename like SSD_00461P01_rule_seed42.jsonl."""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 3:
        return parts[2]
    return "unknown"


def _world_pos(step: dict[str, Any]) -> dict[str, float] | None:
    """Extract world position from a step dict."""
    player = step.get("player")
    if not player:
        return None
    wp = player.get("worldPosition")
    if not wp or "x" not in wp:
        return None
    return {"x": float(wp["x"]), "y": float(wp.get("y", 0)), "z": float(wp["z"])}


def _screen_pos(step: dict[str, Any]) -> dict[str, float] | None:
    """Extract screen position from a step dict."""
    player = step.get("player")
    if not player:
        return None
    sp = player.get("screenPosition")
    if not sp or "x" not in sp:
        return None
    return {"x": float(sp["x"]), "y": float(sp.get("y", 0))}


def _distance(a: dict[str, float] | None, b: dict[str, float] | None) -> float:
    if a is None or b is None:
        return 0.0
    return math.hypot(b["x"] - a["x"], b["z"] - a["z"])


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Return list of keyNumbers paths whose values changed."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    changed = []
    all_keys = set(before) | set(after)
    for k in all_keys:
        bv = before.get(k)
        av = after.get(k)
        if bv != av:
            changed.append(k)
    return changed


def _build_state_summary(step: dict[str, Any]) -> dict[str, Any]:
    """Build colleague-format state_summary_before from a trajectory step."""
    player = step.get("player")
    wp = _world_pos(step)
    sp = _screen_pos(step)
    components = player.get("components", []) if player else []
    return {
        "title": "",
        "ready": True,
        "done": False,
        "win": False,
        "player": {
            "active": player.get("active", True) if player else False,
            "screen_position": sp or {},
            "world_position": wp or {},
        },
        "managers": [c for c in components if "Manager" in c or "Controller" in c][:10],
        "numbers": step.get("keyNumbers") or {},
        "economy": _extract_economy(step.get("keyNumbers") or {}),
        "num_interesting_nodes": 0,
    }


def _extract_economy(numbers: dict[str, Any]) -> dict[str, Any]:
    """Extract economy-related fields from keyNumbers."""
    econ = {}
    for k, v in numbers.items():
        lk = k.lower()
        if any(t in lk for t in ("money", "coin", "wood", "fish", "score", "count")):
            if isinstance(v, (int, float)):
                econ[k] = v
    return econ


def _action_type(action: str) -> str:
    """Map our action names to colleague action_type."""
    return {"move": "move_pulse", "tap": "tap", "wait": "wait"}.get(action, action)


def _parse_pulse_params(reason: str) -> dict[str, Any]:
    """Parse pulse parameters from reason string like 'tap_guide_move_dist=8.04'."""
    params = {"type": "move_pulse", "duration_ms": 320, "stick": {"dx": 0, "dy": 1}}
    m = re.search(r"dist=(\d+\.?\d*)", reason)
    if m:
        dist = float(m.group(1))
        params["duration_ms"] = min(800, max(200, int(dist * 80)))
    return params


def _make_sample_id(game_id: str, task: str, step_idx: int) -> str:
    return f"pr:{game_id}:{task}:{step_idx:04d}"


def _make_messages(task_text: str, state_summary: dict, answer: dict) -> list[dict]:
    """Build the 3-message chat format."""
    return [
        {"role": "system", "content": "You are a game-playing agent that analyzes game states and makes decisions."},
        {
            "role": "user",
            "content": f"{task_text}\n\nContext summary:\n{json.dumps(state_summary, ensure_ascii=False, default=str)}",
        },
        {
            "role": "assistant",
            "content": json.dumps(answer, ensure_ascii=False, default=str),
        },
    ]


# ---------------------------------------------------------------------------
# Task converters
# ---------------------------------------------------------------------------


def convert_probe_action_effect(
    game_id: str, mode: str, steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate probe_action_effect samples from adjacent step pairs."""
    samples = []
    for i in range(len(steps) - 1):
        before, after = steps[i], steps[i + 1]
        state_summary = _build_state_summary(before)
        pb = _world_pos(before)
        pa = _world_pos(after)
        moved = _distance(pb, pa) > STALL_DISPLACEMENT if pb and pa else False
        dist = _distance(pb, pa) if pb and pa else 0.0
        changed = _changed_fields(
            before.get("keyNumbers") or {}, after.get("keyNumbers") or {},
        )
        gain = "high" if len(changed) >= MIN_GAIN_FIELDS else ("medium" if changed else "low_or_unknown")

        answer = {
            "action_type": _action_type(before.get("action", "wait")),
            "action_label": before.get("reason", ""),
            "changed_fields": changed,
            "player_before": pb,
            "player_after": pa,
            "player_moved": moved,
            "player_distance": round(dist, 4),
            "information_gain": gain,
            "completed": bool(after.get("done") or after.get("win")),
        }
        task_text = "Given the before/after evidence and the action that was executed, describe the observed effect of that action on the game state."
        samples.append({
            "sample_id": _make_sample_id(game_id, "probe_action_effect", i + 1),
            "task_type": "probe_action_effect",
            "input": {
                "task": task_text,
                "images": [],
                "backend": {
                    "state_summary_before": state_summary,
                    "unknowns_before": [],
                    "known_facts_before": {},
                },
                "source": {"dataset": "agent-trajectory", "game_id": game_id, "step": i + 1, "mode": mode},
                "action_performed": {
                    "type": _action_type(before.get("action", "wait")),
                    "reason": before.get("reason", ""),
                },
            },
            "target": {"answer": answer},
            "messages": _make_messages(task_text, state_summary, answer),
        })
    return samples


def convert_information_gain(
    game_id: str, mode: str, steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate information_gain_judgment samples from adjacent step pairs."""
    samples = []
    for i in range(len(steps) - 1):
        before, after = steps[i], steps[i + 1]
        state_summary = _build_state_summary(before)
        changed = _changed_fields(
            before.get("keyNumbers") or {}, after.get("keyNumbers") or {},
        )
        gain = "high" if len(changed) >= MIN_GAIN_FIELDS else ("medium" if changed else "low_or_unknown")

        answer = {
            "information_gain": gain,
            "change_count": len(changed),
            "num_changed_fields": len(changed),
            "changed_field_paths": changed,
            "completed": bool(after.get("done") or after.get("win")),
        }
        task_text = "Judge the information gain of the last action based on state changes."
        samples.append({
            "sample_id": _make_sample_id(game_id, "information_gain_judgment", i + 1),
            "task_type": "information_gain_judgment",
            "input": {
                "task": task_text,
                "images": [],
                "backend": {
                    "state_summary_before": state_summary,
                    "unknowns_before": [],
                    "known_facts_before": {},
                },
                "source": {"dataset": "agent-trajectory", "game_id": game_id, "step": i + 1, "mode": mode},
                "action_performed": {
                    "type": _action_type(before.get("action", "wait")),
                    "reason": before.get("reason", ""),
                },
            },
            "target": {"answer": answer},
            "messages": _make_messages(task_text, state_summary, answer),
        })
    return samples


def convert_pulse_response(
    game_id: str, mode: str, steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate pulse_response_grounding samples from move-action steps."""
    samples = []
    for i in range(len(steps) - 1):
        before, after = steps[i], steps[i + 1]
        if before.get("action") != "move":
            continue
        state_summary = _build_state_summary(before)
        pb = _world_pos(before)
        pa = _world_pos(after)
        if not pb or not pa:
            continue
        pulse = _parse_pulse_params(before.get("reason", ""))
        dx = pa["x"] - pb["x"]
        dz = pa["z"] - pb["z"]
        moved = math.hypot(dx, dz) > STALL_DISPLACEMENT

        answer = {
            "pulse": pulse,
            "player_before": pb,
            "player_after": pa,
            "displacement": {"dx": round(dx, 4), "dz": round(dz, 4)},
            "player_moved": moved,
        }
        task_text = "Given a joystick pulse and the resulting world displacement, describe the pulse-to-movement mapping."
        samples.append({
            "sample_id": _make_sample_id(game_id, "pulse_response_grounding", i + 1),
            "task_type": "pulse_response_grounding",
            "input": {
                "task": task_text,
                "images": [],
                "backend": {
                    "state_summary_before": state_summary,
                    "unknowns_before": [],
                    "known_facts_before": {},
                },
                "source": {"dataset": "agent-trajectory", "game_id": game_id, "step": i + 1, "mode": mode},
                "pulse_action": pulse,
                "player_before": pb,
            },
            "target": {"answer": answer},
            "messages": _make_messages(task_text, state_summary, answer),
        })
    return samples


def convert_progression(
    game_id: str, mode: str, steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate progression_grounding samples from each step."""
    samples = []
    for i, step in enumerate(steps):
        state_summary = _build_state_summary(step)
        numbers = step.get("keyNumbers") or {}
        economy = _extract_economy(numbers)

        # Infer phase from economy values
        total_econ = sum(v for v in economy.values() if isinstance(v, (int, float)))
        if step.get("done") or step.get("win"):
            phase = "completed"
        elif total_econ > 100:
            phase = "mid_game"
        elif total_econ > 0:
            phase = "early_game"
        else:
            phase = "starting"

        answer = {
            "current_phase": phase,
            "phase_rule": f"economy_total={total_econ}",
            "classification_reason": f"economy fields: {list(economy.keys())[:5]}",
        }
        task_text = "Classify the current game progression phase based on the backend state."
        samples.append({
            "sample_id": _make_sample_id(game_id, "progression_grounding", i + 1),
            "task_type": "progression_grounding",
            "input": {
                "task": task_text,
                "images": [],
                "backend": {
                    "state_summary_before": state_summary,
                    "unknowns_before": [],
                    "known_facts_before": {},
                },
                "source": {"dataset": "agent-trajectory", "game_id": game_id, "step": i + 1, "mode": mode},
            },
            "target": {"answer": answer},
            "messages": _make_messages(task_text, state_summary, answer),
        })
    return samples


def convert_failure_recovery(
    game_id: str, mode: str, steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate failure_recovery samples from stall windows."""
    samples = []
    stall_start = -1
    for i in range(len(steps)):
        step = steps[i]
        pb = _world_pos(step)
        pa = _world_pos(steps[i + 1]) if i + 1 < len(steps) else None
        moved = _distance(pb, pa) > STALL_DISPLACEMENT if pb and pa else False
        action = step.get("action", "wait")

        if not moved and action != "tap" and stall_start == -1:
            stall_start = i
        elif (moved or action == "tap") and stall_start != -1:
            # Stall window ended
            window_len = i - stall_start
            if window_len >= 2:
                recovery_action = steps[i] if i < len(steps) else steps[-1]
                diagnosis = f"stall_{window_len}_steps"
                state_summary = _build_state_summary(steps[stall_start])

                answer = {
                    "diagnosis": diagnosis,
                    "stuck_steps": list(range(stall_start + 1, i + 1)),
                    "stuck_window_length": window_len,
                    "stuck_action_labels": [steps[j].get("action", "wait") for j in range(stall_start, i)],
                    "recovery_action": {
                        "type": _action_type(recovery_action.get("action", "wait")),
                        "reason": recovery_action.get("reason", ""),
                    },
                    "recovery_step": i + 1,
                }
                task_text = "Diagnose the stuck state and describe the recovery action."
                samples.append({
                    "sample_id": _make_sample_id(game_id, "failure_recovery", len(samples) + 1),
                    "task_type": "failure_recovery",
                    "input": {
                        "task": task_text,
                        "images": [],
                        "backend": {
                            "state_summary_before": state_summary,
                            "unknowns_before": [],
                            "known_facts_before": {},
                        },
                        "source": {"dataset": "agent-trajectory", "game_id": game_id, "step": stall_start + 1, "mode": mode},
                        "stuck_history": [steps[j] for j in range(stall_start, min(i, stall_start + 5))],
                    },
                    "target": {"answer": answer},
                    "messages": _make_messages(task_text, state_summary, answer),
                })
            stall_start = -1
    return samples


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _append_samples(task: str, samples: list[dict[str, Any]]) -> int:
    """Append samples to the task's train.jsonl, returning count appended."""
    if not samples:
        return 0
    out_path = DATASET_ROOT / "tasks" / task / "train.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    return len(samples)


def _update_manifest(counts: dict[str, int]) -> None:
    """Update dataset-manifest.json with new sample counts."""
    manifest_path = DATASET_ROOT / "dataset-manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    # Count total lines per task
    for task in ["next_probe_action", "probe_action_effect", "field_grounding",
                 "information_gain_judgment", "pulse_response_grounding",
                 "progression_grounding", "failure_recovery"]:
        train_path = DATASET_ROOT / "tasks" / task / "train.jsonl"
        val_path = DATASET_ROOT / "tasks" / task / "val.jsonl"
        train_count = sum(1 for _ in open(train_path)) if train_path.is_file() else 0
        val_count = sum(1 for _ in open(val_path)) if val_path.is_file() else 0
        manifest.setdefault("tasks", {})
        manifest["tasks"][task] = {"train": train_count, "val": val_count, "all": train_count + val_count}

    manifest["total_samples"] = sum(
        t.get("all", 0) for t in manifest.get("tasks", {}).values()
    )
    manifest["updated_by"] = "trajectory_converter"
    manifest["updated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert agent trajectory JSONL files into VLM training samples.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        action="append",
        help="Directory containing trajectory .jsonl files (can be given multiple times). Defaults to built-in list.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output dataset directory (default: vlm-training-data-processed-runs)",
    )
    args = parser.parse_args(argv)

    # Allow CLI to override the dataset root and trajectory dirs.
    global DATASET_ROOT, TRAJ_DIRS
    DATASET_ROOT = args.output_dir if args.output_dir else DATASET_ROOT
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    TRAJ_DIRS = list(args.input_dir) if args.input_dir else list(DEFAULT_TRAJ_DIRS)

    # Collect all trajectory files
    traj_files = []
    for traj_dir in TRAJ_DIRS:
        if traj_dir.is_dir():
            for f in sorted(traj_dir.glob("*.jsonl")):
                # Skip timestamped duplicates
                if "_seed" in f.stem:
                    traj_files.append(f)

    print(f"Found {len(traj_files)} trajectory files")

    all_counts: dict[str, int] = defaultdict(int)
    for traj_path in traj_files:
        game_id = _game_id_from_path(traj_path)
        mode = _mode_from_path(traj_path)
        steps = _read_traj(traj_path)
        if not steps:
            continue

        # Generate samples for each task
        for task_name, converter in [
            ("probe_action_effect", convert_probe_action_effect),
            ("information_gain_judgment", convert_information_gain),
            ("pulse_response_grounding", convert_pulse_response),
            ("progression_grounding", convert_progression),
            ("failure_recovery", convert_failure_recovery),
        ]:
            samples = converter(game_id, mode, steps)
            n = _append_samples(task_name, samples)
            all_counts[task_name] += n

    # Update manifest
    _update_manifest(all_counts)

    # Print summary
    print("\n=== Conversion Summary ===")
    total_new = 0
    for task, count in sorted(all_counts.items()):
        print(f"  {task}: +{count}")
        total_new += count
    print(f"  TOTAL NEW: {total_new}")

    # Count existing
    total_existing = 0
    for task in ["next_probe_action", "probe_action_effect", "field_grounding",
                 "information_gain_judgment", "pulse_response_grounding",
                 "progression_grounding", "failure_recovery"]:
        train_path = DATASET_ROOT / "tasks" / task / "train.jsonl"
        if train_path.is_file():
            total_existing += sum(1 for _ in open(train_path))
    print(f"  TOTAL (with existing): {total_existing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
