#!/usr/bin/env python3
"""
Converter from ``processed-runs/`` trajectory data to VLM cold-start training format.

Reads per-game run trajectories (screenshots, backend states, actions, deltas,
VLM answer files) produced by the dataset workflow (schema ``dataset_workflow.*``)
and emits the JSONL task layout consumed by
:class:`src.training.data_loader.VLMColdStartDataset`::

    <output_root>/
      dataset-manifest.json
      tasks/<task_name>/{train,val,all}.jsonl
      tasks/<task_name>/assets/images/<game>-step-NNNN-<tag>.<ext>

The two source flavours are normalised internally:

- ``stage2_raw_direct`` — actions carry ``chosen_action`` / ``executed_action``;
  deltas are ``stage2_driver_delta.v1`` with ``player_moved`` /
  ``player_distance`` fields.
- ``stage1_log_extracted`` — actions carry ``action.raw`` + ``decision``;
  deltas are ``delta.v1`` with a flat ``changes`` list.  Some games use a
  ``derived_state.v1`` snapshot (e.g. SSD_00733P01) instead of the usual
  ``browser_snapshot.v1``.

Task mapping (per step, when required evidence exists):

- ``next_probe_action``        state-before + candidates -> action actually taken
- ``probe_action_effect``      state-before + action     -> observed delta/effect
- ``field_grounding``          screenshot + state        -> backend_mapping answer
- ``information_gain_judgment`` action + changed fields  -> information_gain label
- ``pulse_response_grounding`` move pulse + positions    -> displacement grounding
- ``progression_grounding``    state-before              -> current_phase / phase_rule
- ``failure_recovery``         stuck window detection    -> recovery action

CLI:
    python -m src.training.processed_runs_converter \
        --processed-root processed-runs/ \
        --output-root vlm-training-data-processed-runs/ \
        [--games SSD_00219P01,SSD_00332P01] [--limit 20]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Tasks emitted by this converter (subset of data_loader.VALID_TASK_NAMES).
EMITTED_TASKS: tuple[str, ...] = (
    "next_probe_action",
    "probe_action_effect",
    "field_grounding",
    "information_gain_judgment",
    "pulse_response_grounding",
    "progression_grounding",
    "failure_recovery",
)

#: Action types whose intent is to move the player (used for stuck detection
#: and pulse-response extraction).  dwell/wait/tap/click are stationary or
#: UI-level by design and never count as stuck.
MOVE_ACTION_TYPES = frozenset({"move_pulse", "move_sequence", "world_drag", "screen_drag", "drag"})

#: Consecutive non-moving movement steps that mark a stuck window.
STUCK_WINDOW_MIN = 2

#: Split every run into blocks of this many steps; every SPLIT_EVERY-th block
#: goes to validation.  Block-wise (not step-wise) assignment keeps temporal
#: context of neighbouring steps in the same split, and doing it per game
#: stratifies the split by game.
SPLIT_BLOCK_STEPS = 10
SPLIT_EVERY = 10

#: Caps to keep individual samples compact.
MAX_CHANGED_FIELDS = 20
MAX_CANDIDATE_ACTIONS = 8
MAX_STATE_NUMBERS = 12
MAX_MANAGERS = 8

SYSTEM_PROMPT = (
    "You are a vision-language game-playing agent for Cocos Creator HTML5 "
    "playable ads. You observe screenshots plus backend state summaries and "
    "reason about probing, grounding, and recovery actions."
)

TASK_INSTRUCTIONS: dict[str, str] = {
    "next_probe_action": (
        "Given the current screenshot, backend state summary, unknowns, and "
        "candidate actions, choose the single next probe action to execute."
    ),
    "probe_action_effect": (
        "Given the before/after evidence and the action that was executed, "
        "describe the observed effect of that action on the game state."
    ),
    "field_grounding": (
        "Ground the visible game elements (player, guides, current target, "
        "resources) to their backend node paths and state fields."
    ),
    "information_gain_judgment": (
        "Judge whether the executed probe action produced meaningful "
        "information gain about the game backend."
    ),
    "pulse_response_grounding": (
        "Given the movement pulse (stick vector / drag) and the player "
        "position before execution, ground the expected player displacement."
    ),
    "progression_grounding": (
        "Identify the current progression phase of the run and the strategy "
        "rule that applies to it."
    ),
    "failure_recovery": (
        "The agent was stuck (repeated movement actions produced no position "
        "change). Diagnose the situation and choose the recovery action."
    ),
}


# ---------------------------------------------------------------------------
# Small JSON helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None when missing or unparsable."""
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def _slim_position(pos: Any) -> dict[str, Any] | None:
    if not isinstance(pos, dict):
        return None
    return {k: _round(v) for k, v in pos.items() if isinstance(v, (int, float))}


# ---------------------------------------------------------------------------
# Source normalisation: actions, deltas, states
# ---------------------------------------------------------------------------


def normalize_action(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise a stage1/stage2 action file into a common slim record.

    Returns None when the file does not describe a concrete action.
    """
    if not raw:
        return None

    # stage2_raw_direct: chosen_action + executed_action
    chosen = raw.get("chosen_action")
    executed = raw.get("executed_action")
    if isinstance(chosen, dict):
        out: dict[str, Any] = {"type": chosen.get("type")}
        for key in ("stick", "from", "to", "target", "segments"):
            value = chosen.get(key)
            if value is None and isinstance(executed, dict):
                value = executed.get(key)
            if value is not None:
                out[key] = value
        if chosen.get("x") is not None:
            out["x"] = _round(chosen.get("x"))
            out["y"] = _round(chosen.get("y"))
        out["duration_ms"] = chosen.get("duration_ms")
        out["label"] = chosen.get("label") or (executed or {}).get("label")
        return out

    # stage1_log_extracted: action.raw holds the concrete parameters
    action = raw.get("action")
    if not isinstance(action, dict):
        return None
    raw_action = action.get("raw") if isinstance(action.get("raw"), dict) else {}
    action_type = raw_action.get("type") or action.get("type")
    if not action_type:
        return None
    out = {"type": action_type}
    for key in ("stick", "from", "to"):
        value = raw_action.get(key)
        if value is None and isinstance(action.get("executed"), dict):
            value = action["executed"].get(key)
        if value is not None:
            out[key] = value
    if raw_action.get("x") is not None:
        out["x"] = _round(raw_action.get("x"))
        out["y"] = _round(raw_action.get("y"))
    out["duration_ms"] = raw_action.get("duration_ms") or action.get("duration_ms")
    out["label"] = raw_action.get("label")
    target = action.get("target")
    if isinstance(target, dict):
        out["target"] = {
            k: target.get(k) for k in ("kind", "path", "worldPosition") if target.get(k) is not None
        }
    elif target is not None:
        out["target"] = target
    decision = raw.get("decision")
    if isinstance(decision, dict) and decision.get("reason"):
        out["reason"] = decision["reason"]
    return out


def is_movement_action(action: dict[str, Any] | None) -> bool:
    return bool(action) and action.get("type") in MOVE_ACTION_TYPES


def extract_player_positions(delta: dict[str, Any] | None) -> tuple[dict | None, dict | None]:
    """Extract (player_before, player_after) world positions from a delta file.

    Handles ``stage2_driver_delta.v1`` (explicit fields) and ``delta.v1``
    (player worldPosition entries inside the flat ``changes`` list).
    """
    if not delta:
        return None, None
    if "player_before" in delta or "player_after" in delta:
        return _slim_position(delta.get("player_before")), _slim_position(delta.get("player_after"))

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for change in delta.get("changes") or []:
        path = str(change.get("path", ""))
        if "player" not in path or "worldPosition" not in path:
            continue
        axis = path.rsplit(".", 1)[-1]
        if axis not in ("x", "y", "z"):
            continue
        if isinstance(change.get("before"), (int, float)):
            before[axis] = _round(change["before"])
        if isinstance(change.get("after"), (int, float)):
            after[axis] = _round(change["after"])
    return (before or None), (after or None)


def player_moved(delta: dict[str, Any] | None) -> bool | None:
    """Whether the player position changed; None when unknown."""
    if not delta:
        return None
    if "player_moved" in delta:
        return bool(delta["player_moved"])
    before, after = extract_player_positions(delta)
    if not before or not after:
        return None
    return before != after


def slim_changed_fields(delta: dict[str, Any] | None, answer: dict[str, Any] | None) -> list[dict]:
    """Return a capped list of changed-field entries from delta or answer."""
    source: list[Any] = []
    if delta and isinstance(delta.get("changes"), list):
        source = delta["changes"]
    elif answer and isinstance(answer.get("changed_fields"), list):
        source = answer["changed_fields"]
    slim: list[dict] = []
    for change in source[:MAX_CHANGED_FIELDS]:
        if not isinstance(change, dict):
            continue
        entry = {"path": change.get("path"), "before": change.get("before"), "after": change.get("after")}
        if change.get("delta") is not None:
            entry["delta"] = _round(change["delta"])
        slim.append(entry)
    return slim


def build_state_summary(state_file: dict[str, Any] | None) -> dict[str, Any]:
    """Build a compact, schema-tolerant summary of a state snapshot.

    Handles ``browser_snapshot.v1`` (nested ``state.observe``) and
    ``derived_state.v1`` (flat driver-specific fields).
    """
    if not state_file:
        return {}
    state = state_file.get("state")
    if not isinstance(state, dict):
        return {}

    summary: dict[str, Any] = {}
    observe = state.get("observe")
    if isinstance(observe, dict):
        # browser_snapshot.v1
        summary["title"] = state.get("title")
        summary["ready"] = observe.get("ready")
        summary["done"] = observe.get("done")
        summary["win"] = observe.get("win")
        player = observe.get("player")
        if isinstance(player, dict):
            summary["player"] = {
                "active": player.get("active"),
                "screen_position": _slim_position(player.get("screenPosition")),
                "world_position": _slim_position(player.get("worldPosition")),
            }
        managers = observe.get("managers") or []
        summary["managers"] = [
            m.get("className") for m in managers[:MAX_MANAGERS] if isinstance(m, dict)
        ]
        numbers = observe.get("numbers") or {}
        flat_numbers = {
            k: _round(v) for k, v in numbers.items() if isinstance(v, (int, float))
        }
        summary["numbers"] = dict(list(flat_numbers.items())[:MAX_STATE_NUMBERS])
        money = state.get("moneyResources")
        if isinstance(money, dict) and isinstance(money.get("economy"), dict):
            summary["economy"] = {
                k: _round(v)
                for k, v in money["economy"].items()
                if isinstance(v, (int, float))
            }
        interesting = state.get("interestingNodes") or []
        summary["num_interesting_nodes"] = len(interesting)
        return summary

    # derived_state.v1 / generic fallback: keep scalar and small-dict fields
    for key, value in state.items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            summary[key] = _round(value) if isinstance(value, float) else value
        elif key in ("player", "target") and isinstance(value, dict):
            summary[key] = {
                k: _slim_position(value.get(k)) if k == "worldPosition" else value.get(k)
                for k in ("kind", "path", "worldPosition")
                if value.get(k) is not None
            }
    return summary


# ---------------------------------------------------------------------------
# Step loading
# ---------------------------------------------------------------------------


class StepRecord:
    """All loaded evidence for a single step of one game run."""

    def __init__(self, game_dir: Path, row: dict[str, Any]) -> None:
        self.game_dir = game_dir
        self.game_id: str = row.get("game_id", game_dir.name)
        self.step: int = int(row.get("step", 0))
        self.evidence: dict[str, Any] = row.get("evidence") or {}

        def _rel(section: str, key: str = "") -> Path | None:
            value = row.get(section)
            if isinstance(value, dict):
                value = value.get(key)
            return (game_dir / value) if isinstance(value, str) else None

        self.action_path = _rel("action")
        self.before_state_path = _rel("before", "state")
        self.after_state_path = _rel("after", "state")
        self.before_shot_path = _rel("before", "screenshot")
        self.after_shot_path = _rel("after", "screenshot")
        self.delta_path = _rel("delta")

        answers = row.get("answers") or {}
        self.action_result_path = (
            game_dir / answers["action_result"] if answers.get("action_result") else None
        )
        self.backend_mapping_path = (
            game_dir / answers["backend_mapping"] if answers.get("backend_mapping") else None
        )

        # Lazily-populated payloads
        self._action: dict[str, Any] | None | bool = False
        self._before_state: dict[str, Any] | None | bool = False
        self._delta: dict[str, Any] | None | bool = False
        self._action_result: dict[str, Any] | None | bool = False
        self._backend_mapping: dict[str, Any] | None | bool = False

    # -- lazy loaders (False sentinel = not loaded yet) --

    @property
    def action(self) -> dict[str, Any] | None:
        if self._action is False:
            self._action = normalize_action(_load_json(self.action_path) if self.action_path else None)
        return self._action  # type: ignore[return-value]

    @property
    def before_state(self) -> dict[str, Any] | None:
        if self._before_state is False:
            self._before_state = (
                _load_json(self.before_state_path) if self.before_state_path else None
            )
        return self._before_state  # type: ignore[return-value]

    @property
    def delta(self) -> dict[str, Any] | None:
        if self._delta is False:
            self._delta = _load_json(self.delta_path) if self.delta_path else None
        return self._delta  # type: ignore[return-value]

    @property
    def action_result(self) -> dict[str, Any] | None:
        if self._action_result is False:
            payload = _load_json(self.action_result_path) if self.action_result_path else None
            self._action_result = (payload or {}).get("answer") if payload else None
        return self._action_result  # type: ignore[return-value]

    @property
    def backend_mapping(self) -> dict[str, Any] | None:
        if self._backend_mapping is False:
            payload = _load_json(self.backend_mapping_path) if self.backend_mapping_path else None
            self._backend_mapping = (payload or {}).get("answer") if payload else None
        return self._backend_mapping  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


class ProcessedRunsConverter:
    """Convert ``processed-runs/`` trajectories into VLM cold-start JSONL tasks.

    Parameters
    ----------
    processed_root : str or Path
        Directory containing one subdirectory per game run.
    output_root : str or Path
        Destination dataset directory.  Wiped and regenerated on every
        ``convert()`` call (idempotent full rebuild).
    games : optional list[str]
        Restrict conversion to these game ids (default: all found).
    limit : optional int
        Process at most this many steps per game (debugging aid).
    """

    def __init__(
        self,
        processed_root: str | Path,
        output_root: str | Path,
        games: list[str] | None = None,
        limit: int | None = None,
    ) -> None:
        self.processed_root = Path(processed_root)
        self.output_root = Path(output_root)
        self.games = sorted(games) if games else None
        self.limit = limit

        # Per-task collections, reset by convert()
        self._samples: dict[str, list[dict[str, Any]]] = {t: [] for t in EMITTED_TASKS}
        self._splits: dict[str, str] = {}  # sample_id -> "train" | "val"
        self._linked_images: dict[str, set[str]] = {t: set() for t in EMITTED_TASKS}
        self.stats: dict[str, Any] = {
            "games": {},
            "samples_per_task": {},
            "skipped": {},
        }
        self._skipped: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convert(self) -> dict[str, Any]:
        """Run the full conversion and return statistics."""
        if not self.processed_root.is_dir():
            raise FileNotFoundError(f"processed-runs root not found: {self.processed_root}")

        game_dirs = self._discover_games()
        logger.info("Converting %d game run(s) from %s", len(game_dirs), self.processed_root)

        # Idempotent rebuild: the output directory is fully owned by us.
        if self.output_root.exists():
            shutil.rmtree(self.output_root)
        for task in EMITTED_TASKS:
            (self.output_root / "tasks" / task / "assets" / "images").mkdir(parents=True)

        for game_dir in game_dirs:
            self._convert_game(game_dir)

        self._write_outputs()
        self.stats["samples_per_task"] = {t: len(s) for t, s in self._samples.items()}
        self.stats["skipped"] = dict(sorted(self._skipped.items()))
        return self.stats

    # ------------------------------------------------------------------
    # Game / step iteration
    # ------------------------------------------------------------------

    def _discover_games(self) -> list[Path]:
        game_dirs = []
        for entry in sorted(self.processed_root.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "steps.jsonl").is_file():
                logger.warning("Skipping %s: no steps.jsonl", entry.name)
                continue
            if self.games and entry.name not in self.games:
                continue
            game_dirs.append(entry)
        return game_dirs

    def _convert_game(self, game_dir: Path) -> None:
        rows: list[dict[str, Any]] = []
        with open(game_dir / "steps.jsonl", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        rows.sort(key=lambda r: r.get("step", 0))
        if self.limit:
            rows = rows[: self.limit]

        steps = [StepRecord(game_dir, row) for row in rows]
        logger.info("%s: %d steps", game_dir.name, len(steps))

        candidates = self._build_run_candidates(steps)
        per_task_before = {t: len(s) for t, s in self._samples.items()}

        for record in steps:
            self._emit_step_tasks(record, candidates)
        self._emit_failure_recovery(steps)

        game_stats = {
            "steps": len(steps),
            "samples": {
                t: len(self._samples[t]) - per_task_before[t] for t in EMITTED_TASKS
            },
        }
        self.stats["games"][game_dir.name] = game_stats

    def _build_run_candidates(self, steps: list[StepRecord]) -> list[dict[str, Any]]:
        """Distinct actions of the run, used as candidate-action context."""
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for record in steps:
            action = record.action
            if not action:
                continue
            key = json.dumps([action.get("type"), action.get("label")], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "label": action.get("label") or action.get("type"),
                    "action": {"type": action.get("type")},
                    "reason": action.get("reason"),
                }
            )
        return candidates

    # ------------------------------------------------------------------
    # Per-task emitters
    # ------------------------------------------------------------------

    def _emit_step_tasks(self, record: StepRecord, candidates: list[dict[str, Any]]) -> None:
        self._emit_next_probe_action(record, candidates)
        self._emit_probe_action_effect(record)
        self._emit_field_grounding(record)
        self._emit_information_gain(record)
        self._emit_pulse_response(record)
        self._emit_progression(record)

    def _emit_next_probe_action(self, record: StepRecord, candidates: list[dict[str, Any]]) -> None:
        task = "next_probe_action"
        action = record.action
        if not action:
            return self._skip(task, "no_action")
        state_summary = build_state_summary(record.before_state)
        if not state_summary:
            return self._skip(task, "no_before_state")

        chosen = {
            "label": action.get("label") or action.get("type"),
            "action": {"type": action.get("type")},
            "reason": action.get("reason"),
            "is_chosen": True,
        }
        others = [c for c in candidates if c["action"]["type"] != action.get("type")]
        sample_candidates = ([chosen] + others)[:MAX_CANDIDATE_ACTIONS]

        answer = {"action": action, "reason": action.get("reason")}
        self._add_sample(
            task,
            record,
            extra_input={
                "candidate_actions": sample_candidates,
                "state_summary_before": state_summary,
            },
            answer=answer,
            image_tags=[("before", record.before_shot_path)],
        )

    def _emit_probe_action_effect(self, record: StepRecord) -> None:
        task = "probe_action_effect"
        action = record.action
        if not action:
            return self._skip(task, "no_action")
        delta = record.delta
        answer_ar = record.action_result
        if not delta and not answer_ar:
            return self._skip(task, "no_delta_or_answer")
        state_summary = build_state_summary(record.before_state)
        if not state_summary:
            return self._skip(task, "no_before_state")

        before, after = extract_player_positions(delta)
        answer: dict[str, Any] = {
            "action_type": action.get("type"),
            "action_label": action.get("label"),
            "changed_fields": slim_changed_fields(delta, answer_ar),
        }
        if delta and delta.get("change_count") is not None:
            answer["change_count"] = delta["change_count"]
        if before or after:
            answer["player_before"] = before
            answer["player_after"] = after
        moved = player_moved(delta)
        if moved is not None:
            answer["player_moved"] = moved
        if delta and delta.get("player_distance") is not None:
            answer["player_distance"] = _round(delta["player_distance"])
        if answer_ar:
            answer["information_gain"] = answer_ar.get("information_gain")
            answer["completed"] = answer_ar.get("completed")

        self._add_sample(
            task,
            record,
            extra_input={
                "action_performed": action,
                "state_summary_before": state_summary,
            },
            answer=answer,
            image_tags=[("before", record.before_shot_path), ("after", record.after_shot_path)],
        )

    def _emit_field_grounding(self, record: StepRecord) -> None:
        task = "field_grounding"
        mapping = record.backend_mapping
        if not mapping or not mapping.get("observed_backend_mapping"):
            return self._skip(task, "no_backend_mapping")
        if not (record.before_shot_path and record.before_shot_path.is_file()):
            return self._skip(task, "no_before_screenshot")
        state_summary = build_state_summary(record.before_state)
        if not state_summary:
            return self._skip(task, "no_before_state")

        answer = {
            "observed_backend_mapping": mapping["observed_backend_mapping"],
            "mapping_scope": mapping.get("mapping_scope"),
            "confidence": mapping.get("confidence"),
        }
        self._add_sample(
            task,
            record,
            extra_input={"state_summary_before": state_summary},
            answer=answer,
            image_tags=[("before", record.before_shot_path)],
        )

    def _emit_information_gain(self, record: StepRecord) -> None:
        task = "information_gain_judgment"
        answer_ar = record.action_result
        if not answer_ar or answer_ar.get("information_gain") is None:
            return self._skip(task, "no_action_result_answer")
        if not record.action:
            return self._skip(task, "no_action")

        changed = answer_ar.get("changed_fields") or []
        answer = {
            "information_gain": answer_ar.get("information_gain"),
            "change_count": answer_ar.get("change_count"),
            "num_changed_fields": len(changed),
            "changed_field_paths": [c.get("path") for c in changed[:8] if isinstance(c, dict)],
            "completed": answer_ar.get("completed"),
        }
        self._add_sample(
            task,
            record,
            extra_input={
                "action_performed": record.action,
                "state_summary_before": build_state_summary(record.before_state),
            },
            answer=answer,
            image_tags=[("before", record.before_shot_path), ("after", record.after_shot_path)],
        )

    def _emit_pulse_response(self, record: StepRecord) -> None:
        task = "pulse_response_grounding"
        action = record.action
        if not is_movement_action(action):
            return self._skip(task, "not_movement_action")
        if not (action.get("stick") or (action.get("from") and action.get("to"))):
            return self._skip(task, "no_pulse_vector")
        before, after = extract_player_positions(record.delta)
        if not before or not after:
            return self._skip(task, "no_player_positions")

        pulse: dict[str, Any] = {"type": action.get("type"), "duration_ms": action.get("duration_ms")}
        for key in ("stick", "from", "to", "label"):
            if action.get(key) is not None:
                pulse[key] = action[key]
        displacement = None
        if "x" in before and "x" in after:
            displacement = {
                "dx": _round(after.get("x", 0) - before.get("x", 0)),
                "dz": _round(after.get("z", 0) - before.get("z", 0)),
            }
        answer = {
            "pulse": pulse,
            "player_before": before,
            "player_after": after,
            "displacement": displacement,
            "player_moved": before != after,
        }
        self._add_sample(
            task,
            record,
            extra_input={
                "pulse_action": pulse,
                "player_before": before,
                "state_summary_before": build_state_summary(record.before_state),
            },
            answer=answer,
            image_tags=[("before", record.before_shot_path), ("after", record.after_shot_path)],
        )

    def _emit_progression(self, record: StepRecord) -> None:
        task = "progression_grounding"
        mapping = record.backend_mapping
        if not mapping or not mapping.get("current_phase"):
            return self._skip(task, "no_backend_mapping_phase")
        state_summary = build_state_summary(record.before_state)
        if not state_summary:
            return self._skip(task, "no_before_state")

        answer = {
            "current_phase": mapping.get("current_phase"),
            "phase_rule": mapping.get("phase_rule"),
            "classification_reason": mapping.get("classification_reason"),
        }
        if mapping.get("control_mode") is not None:
            answer["control_mode"] = mapping.get("control_mode")
        self._add_sample(
            task,
            record,
            extra_input={"state_summary_before": state_summary},
            answer=answer,
            image_tags=[("before", record.before_shot_path)],
        )

    def _emit_failure_recovery(self, steps: list[StepRecord]) -> None:
        """Detect stuck windows within a run and emit one sample per window.

        A stuck window is >= STUCK_WINDOW_MIN consecutive movement-intent
        steps whose delta shows no player position change.  The first step
        after the window is the recovery step; its action is the target.
        """
        task = "failure_recovery"
        index = 0
        while index < len(steps):
            record = steps[index]
            if not (is_movement_action(record.action) and player_moved(record.delta) is False):
                index += 1
                continue
            # Found a stuck start; extend the window.
            window = [record]
            cursor = index + 1
            while cursor < len(steps):
                nxt = steps[cursor]
                if is_movement_action(nxt.action) and player_moved(nxt.delta) is False:
                    window.append(nxt)
                    cursor += 1
                else:
                    break
            index = cursor
            if len(window) < STUCK_WINDOW_MIN:
                self._skip(task, "stuck_window_too_short")
                continue
            recovery = steps[cursor] if cursor < len(steps) else None
            if recovery is None or not recovery.action:
                self._skip(task, "no_recovery_step")
                continue
            state_summary = build_state_summary(recovery.before_state) or build_state_summary(
                window[-1].before_state
            )
            answer = {
                "diagnosis": "movement_actions_no_position_change",
                "stuck_steps": [r.step for r in window],
                "stuck_window_length": len(window),
                "stuck_action_labels": [
                    (r.action or {}).get("label") or (r.action or {}).get("type") for r in window
                ],
                "recovery_action": recovery.action,
                "recovery_step": recovery.step,
            }
            self._add_sample(
                task,
                recovery,
                extra_input={
                    "stuck_history": {
                        "stuck_steps": [r.step for r in window],
                        "stuck_window_length": len(window),
                        "last_stuck_action": window[-1].action,
                    },
                    "state_summary_before": state_summary,
                },
                answer=answer,
                image_tags=[("before", recovery.before_shot_path)],
                step_override=window[-1].step,
            )

    # ------------------------------------------------------------------
    # Sample assembly / output
    # ------------------------------------------------------------------

    def _skip(self, task: str, reason: str) -> None:
        key = f"{task}:{reason}"
        self._skipped[key] = self._skipped.get(key, 0) + 1

    def _step_split(self, game_id: str, step: int) -> str:
        """Deterministic per-game block split: every SPLIT_EVERY-th block is val."""
        block = (step - 1) // SPLIT_BLOCK_STEPS
        return "val" if block % SPLIT_EVERY == SPLIT_EVERY - 1 else "train"

    def _add_sample(
        self,
        task: str,
        record: StepRecord,
        extra_input: dict[str, Any],
        answer: dict[str, Any],
        image_tags: list[tuple[str, Path | None]],
        step_override: int | None = None,
    ) -> None:
        step = step_override if step_override is not None else record.step
        sample_id = f"pr:{record.game_id}:{task}:{step:04d}"

        images: list[dict[str, str]] = []
        for tag, path in image_tags:
            if not path or not path.is_file():
                continue
            rel = self._link_image(task, record.game_id, step, tag, path)
            images.append({"path": rel, "role": tag})

        unknowns: list[str] = []
        mapping = record.backend_mapping
        if mapping and isinstance(mapping.get("not_yet_observed_policy"), str):
            unknowns.append(mapping["not_yet_observed_policy"])

        state_summary = extra_input.pop("state_summary_before", {})
        input_payload: dict[str, Any] = {
            "task": TASK_INSTRUCTIONS[task],
            "images": images,
            "backend": {
                "state_summary_before": state_summary,
                "unknowns_before": unknowns,
                "known_facts_before": {},
            },
            "source": {
                "dataset": "processed-runs",
                "game_id": record.game_id,
                "step": record.step,
            },
        }
        input_payload.update(extra_input)

        target = {"answer": answer}
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{TASK_INSTRUCTIONS[task]}\n\n"
                    f"Context summary:\n{json.dumps(input_payload['backend'], ensure_ascii=False)}"
                ),
            },
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        ]

        sample = {
            "sample_id": sample_id,
            "task_type": task,
            "input": input_payload,
            "target": target,
            "messages": messages,
        }
        self._samples[task].append(sample)
        self._splits[sample_id] = self._step_split(record.game_id, step)

    def _link_image(self, task: str, game_id: str, step: int, tag: str, src: Path) -> str:
        """Hard-link (fallback: copy) an image into the task assets dir."""
        filename = f"{game_id}-step-{step:04d}-{tag}{src.suffix.lower()}"
        dest_dir = self.output_root / "tasks" / task / "assets" / "images"
        dest = dest_dir / filename
        if filename not in self._linked_images[task]:
            if dest.exists():
                dest.unlink()
            try:
                os.link(src, dest)
            except OSError:
                shutil.copy2(src, dest)
            self._linked_images[task].add(filename)
        return f"assets/images/{filename}"

    def _write_outputs(self) -> None:
        tasks_dir = self.output_root / "tasks"
        manifest_tasks: dict[str, Any] = {}
        for task in EMITTED_TASKS:
            samples = self._samples[task]
            train = [s for s in samples if self._splits[s["sample_id"]] == "train"]
            val = [s for s in samples if self._splits[s["sample_id"]] == "val"]
            task_dir = tasks_dir / task
            for split, rows in (("train", train), ("val", val), ("all", samples)):
                with open(task_dir / f"{split}.jsonl", "w", encoding="utf-8") as fh:
                    for row in rows:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            manifest_tasks[task] = {
                "train": len(train),
                "val": len(val),
                "all": len(samples),
            }

        manifest = {
            "dataset_name": "vlm-training-data-processed-runs",
            "source": "processed-runs (dataset_workflow.* schemas, 2026-07-07/08)",
            "task_names": list(EMITTED_TASKS),
            "splits": ["train", "val", "all"],
            "split_rule": (
                f"per-game blocks of {SPLIT_BLOCK_STEPS} steps; every "
                f"{SPLIT_EVERY}-th block -> val (~10%, step-context safe)"
            ),
            "games": sorted(self.stats["games"].keys()),
            "tasks": manifest_tasks,
            "sample_total": sum(t["all"] for t in manifest_tasks.values()),
        }
        with open(self.output_root / "dataset-manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--processed-root", default="processed-runs", help="Input run directory")
    parser.add_argument(
        "--output-root",
        default="vlm-training-data-processed-runs",
        help="Output dataset directory (wiped and regenerated)",
    )
    parser.add_argument("--games", default=None, help="Comma-separated game ids to convert")
    parser.add_argument("--limit", type=int, default=None, help="Max steps per game (debug)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    games = [g.strip() for g in args.games.split(",") if g.strip()] if args.games else None
    converter = ProcessedRunsConverter(
        processed_root=args.processed_root,
        output_root=args.output_root,
        games=games,
        limit=args.limit,
    )
    stats = converter.convert()
    logger.info("Samples per task: %s", stats["samples_per_task"])
    logger.info("Skipped: %s", stats["skipped"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
