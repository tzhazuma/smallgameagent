"""Tests for src/training/processed_runs_converter.py.

Uses a synthetic two-game fixture in ``tmp_path`` mirroring the real
``processed-runs/<game>/`` layout (steps.jsonl + actions/states/deltas/
answers/screenshots), then validates the converted output by loading it
with the real ``VLMColdStartDataset``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.training.data_loader import VLMColdStartDataset
from src.training.processed_runs_converter import (
    EMITTED_TASKS,
    ProcessedRunsConverter,
    build_state_summary,
    extract_player_positions,
    normalize_action,
    player_moved,
)

# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------


class TestNormalizeAction:
    def test_stage2_chosen_executed(self) -> None:
        raw = {
            "chosen_action": {"type": "move_pulse", "duration_ms": 300, "label": "go"},
            "executed_action": {"stick": {"dx": 0.5, "dy": -0.5}},
        }
        out = normalize_action(raw)
        assert out is not None
        assert out["type"] == "move_pulse"
        assert out["stick"] == {"dx": 0.5, "dy": -0.5}
        assert out["duration_ms"] == 300
        assert out["label"] == "go"

    def test_stage1_action_raw_with_decision(self) -> None:
        raw = {
            "action": {
                "type": "world_drag",
                "raw": {"type": "world_drag", "from": {"x": 1}, "to": {"x": 2}, "duration_ms": 100},
                "target": {"kind": "node", "path": "Hero/Weapon"},
            },
            "decision": {"reason": "follow guide"},
        }
        out = normalize_action(raw)
        assert out is not None
        assert out["type"] == "world_drag"
        assert out["from"] == {"x": 1}
        assert out["target"] == {"kind": "node", "path": "Hero/Weapon"}
        assert out["reason"] == "follow guide"

    def test_none_and_empty(self) -> None:
        assert normalize_action(None) is None
        assert normalize_action({}) is None
        assert normalize_action({"action": {"raw": {}}}) is None


class TestDeltaHelpers:
    STAGE2_DELTA = {
        "schema": "stage2_driver_delta.v1",
        "player_before": {"x": 1.0, "y": 0, "z": 2.0},
        "player_after": {"x": 1.5, "y": 0, "z": 2.5},
        "player_moved": True,
        "changes": [],
    }
    V1_DELTA = {
        "schema": "delta.v1",
        "changes": [
            {"path": "player.worldPosition.x", "before": 3.0, "after": 3.0},
            {"path": "player.worldPosition.z", "before": 4.0, "after": 4.25},
        ],
    }

    def test_extract_positions_stage2(self) -> None:
        before, after = extract_player_positions(self.STAGE2_DELTA)
        assert before == {"x": 1.0, "y": 0, "z": 2.0}
        assert after == {"x": 1.5, "y": 0, "z": 2.5}

    def test_extract_positions_v1_changes(self) -> None:
        before, after = extract_player_positions(self.V1_DELTA)
        assert before == {"x": 3.0, "z": 4.0}
        assert after == {"x": 3.0, "z": 4.25}

    def test_player_moved(self) -> None:
        assert player_moved(self.STAGE2_DELTA) is True
        assert player_moved(self.V1_DELTA) is True
        assert player_moved({"changes": []}) is None
        assert player_moved(None) is None


class TestBuildStateSummary:
    def test_browser_snapshot(self) -> None:
        state = {
            "schema": "browser_snapshot.v1",
            "state": {
                "title": "TILES",
                "observe": {
                    "ready": True,
                    "done": False,
                    "win": False,
                    "player": {"active": True, "worldPosition": {"x": 1.0, "z": 2.0}},
                    "managers": [{"className": "GameManager"}],
                    "numbers": {"gold": 12.345},
                },
                "moneyResources": {"economy": {"gold": 12.0}},
            },
        }
        s = build_state_summary(state)
        assert s["title"] == "TILES"
        assert s["ready"] is True
        assert s["player"]["world_position"] == {"x": 1.0, "z": 2.0}
        assert s["managers"] == ["GameManager"]
        assert s["numbers"]["gold"] == 12.345
        assert s["economy"] == {"gold": 12.0}

    def test_derived_state_fallback(self) -> None:
        state = {"state": {"phase": "gather", "gold": 5, "autoFishing": True}}
        s = build_state_summary(state)
        assert s["phase"] == "gather"
        assert s["gold"] == 5
        assert s["autoFishing"] is True

    def test_empty(self) -> None:
        assert build_state_summary(None) == {}
        assert build_state_summary({"state": "not-a-dict"}) == {}


# ---------------------------------------------------------------------------
# Integration: synthetic fixture -> convert -> load with VLMColdStartDataset
# ---------------------------------------------------------------------------

GAME_A = "SSD_90001P01"
GAME_B = "SSD_90002P01"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (10, 200, 220)).save(path)


def _make_step(game_dir: Path, step: int, *, moved: bool, movement: bool = True) -> None:
    """Write one step's worth of evidence files plus its steps.jsonl row."""
    tag = f"step-{step:04d}"
    action = (
        {"chosen_action": {"type": "move_pulse", "duration_ms": 200},
         "executed_action": {"stick": {"dx": 0.1, "dy": 0.2}}}
        if movement
        else {"chosen_action": {"type": "wait", "duration_ms": 100}}
    )
    state = {
        "schema": "browser_snapshot.v1",
        "state": {
            "title": "G",
            "observe": {
                "ready": True, "done": False, "win": False,
                "player": {"active": True, "worldPosition": {"x": float(step), "z": 0.0}},
                "managers": [{"className": "GameManager"}],
                "numbers": {"gold": 10 + step},
            },
        },
    }
    delta = {
        "schema": "stage2_driver_delta.v1",
        "player_before": {"x": float(step), "z": 0.0},
        "player_after": {"x": float(step) + (0.5 if moved else 0.0), "z": 0.0},
        "player_moved": moved,
        "changes": [{"path": "numbers.gold", "before": 10 + step, "after": 11 + step, "delta": 1}],
    }
    _write_json(game_dir / "actions" / f"{tag}.action.json", action)
    _write_json(game_dir / "states" / f"{tag}.before.json", state)
    _write_json(game_dir / "deltas" / f"{tag}.delta.json", delta)
    _write_json(game_dir / "answers" / f"{tag}.action_result.json",
                {"answer": {"success": moved, "changed_fields": delta["changes"]}})
    _write_json(game_dir / "answers" / f"{tag}.backend_mapping.json",
                {"answer": {"player": {"path": "Hero"}}})
    _write_png(game_dir / "screenshots" / f"{tag}.before.png")
    return {
        "game_id": game_dir.name,
        "step": step,
        "action": f"actions/{tag}.action.json",
        "before": {"state": f"states/{tag}.before.json", "screenshot": f"screenshots/{tag}.before.png"},
        "delta": f"deltas/{tag}.delta.json",
        "answers": {
            "action_result": f"answers/{tag}.action_result.json",
            "backend_mapping": f"answers/{tag}.backend_mapping.json",
        },
    }


@pytest.fixture
def processed_root(tmp_path: Path) -> Path:
    root = tmp_path / "processed-runs"
    for game, pattern in (
        (GAME_A, [(1, True), (2, True), (3, True), (4, True)]),
        # game B: steps 2-3 stuck (movement but no displacement), step 4 recovers
        (GAME_B, [(1, True), (2, False), (3, False), (4, True)]),
    ):
        game_dir = root / game
        game_dir.mkdir(parents=True)
        rows = [_make_step(game_dir, s, moved=m) for s, m in pattern]
        (game_dir / "steps.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
        )
    return root


class TestConvertEndToEnd:
    def test_full_convert_and_dataset_load(self, processed_root: Path, tmp_path: Path) -> None:
        out = tmp_path / "dataset"
        conv = ProcessedRunsConverter(processed_root, out)
        stats = conv.convert()

        # output structure
        assert (out / "dataset-manifest.json").is_file()
        for task in EMITTED_TASKS:
            assert (out / "tasks" / task).is_dir()
        assert stats["games"][GAME_A]["steps"] == 4
        assert sum(stats["samples_per_task"].values()) > 0

        # loadable by the real dataset class, images resolve
        ds = VLMColdStartDataset(out, "next_probe_action", split="train")
        assert len(ds) > 0
        item = ds[0]
        assert item["sample_id"]
        assert item["task_type"] == "next_probe_action"
        assert item["images"] and isinstance(item["images"][0], Image.Image)
        assert item["messages"]
        assert item["target_raw"].get("answer") is not None

        # no (sample_id) appears in both splits
        train_ids = {json.loads(line)["sample_id"]
                     for line in (out / "tasks" / "next_probe_action" / "train.jsonl")
                     .read_text().splitlines() if line.strip()}
        val_path = out / "tasks" / "next_probe_action" / "val.jsonl"
        if val_path.is_file():
            val_ids = {json.loads(line)["sample_id"]
                       for line in val_path.read_text().splitlines() if line.strip()}
            assert train_ids.isdisjoint(val_ids)

    def test_failure_recovery_from_stuck_window(self, processed_root: Path, tmp_path: Path) -> None:
        out = tmp_path / "dataset"
        ProcessedRunsConverter(processed_root, out).convert()
        fr_path = out / "tasks" / "failure_recovery" / "train.jsonl"
        val_path = out / "tasks" / "failure_recovery" / "val.jsonl"
        lines = []
        for p in (fr_path, val_path):
            if p.is_file():
                lines += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        assert lines, "expected failure_recovery samples from game B's stuck window"
        assert any(GAME_B in s["sample_id"] for s in lines)

    def test_idempotent_rerun(self, processed_root: Path, tmp_path: Path) -> None:
        out = tmp_path / "dataset"
        first = ProcessedRunsConverter(processed_root, out).convert()
        second = ProcessedRunsConverter(processed_root, out).convert()
        assert first["samples_per_task"] == second["samples_per_task"]

    def test_games_filter_and_limit(self, processed_root: Path, tmp_path: Path) -> None:
        out = tmp_path / "dataset"
        stats = ProcessedRunsConverter(processed_root, out, games=[GAME_A], limit=2).convert()
        assert list(stats["games"].keys()) == [GAME_A]
        assert stats["games"][GAME_A]["steps"] == 2

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ProcessedRunsConverter(tmp_path / "nope", tmp_path / "o").convert()
