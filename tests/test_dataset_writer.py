"""Tests for src.agent.dataset_writer.

All tests use ``tmp_path`` so no real filesystem side-effects.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.agent.dataset_writer import DatasetWriter


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_state() -> dict:
    """A typical game state dict."""
    return {
        "ready": True,
        "done": False,
        "win": False,
        "keyNumbers": {"score": 150},
        "player": {"name": "Player"},
    }


@pytest.fixture
def sample_decision() -> dict:
    """A typical action decision dict."""
    return {
        "action": "move",
        "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
        "reason": "Move toward target",
    }


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """DatasetWriter instance creation and directory setup."""

    def test_constructor_creates_directory(self, tmp_path: Path) -> None:
        """Output directory is created when it does not exist."""
        output_dir = tmp_path / "my_datasets"
        assert not output_dir.exists()
        writer = DatasetWriter(output_dir=output_dir)
        assert output_dir.is_dir()
        writer.end_session()  # cleanup no-op

    def test_constructor_uses_existing_directory(self, tmp_path: Path) -> None:
        """No error when output directory already exists."""
        output_dir = tmp_path / "existing"
        output_dir.mkdir(parents=True)
        writer = DatasetWriter(output_dir=output_dir)
        assert output_dir.is_dir()
        writer.end_session()

    def test_default_output_dir(self) -> None:
        """Default output dir is './collected_datasets'."""
        writer = DatasetWriter()
        assert writer._output_dir == Path("./collected_datasets")
        # Clean up the created directory.
        writer.end_session()
        import shutil

        shutil.rmtree("./collected_datasets", ignore_errors=True)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """start_session / end_session behaviour."""

    def test_start_session_creates_file_with_correct_pattern(self, tmp_path: Path) -> None:
        """File is created with name pattern ``{game_id}_{timestamp}.jsonl``."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("GameX")
        assert writer.session_path is not None
        assert writer.session_path.suffix == ".jsonl"
        assert writer.session_path.stem.startswith("GameX_")
        # The timestamp portion after game_id_ should be YYYYMMDD_HHMMSS.
        timestamp_part = writer.session_path.stem[len("GameX_") :]
        assert re.match(r"^\d{8}_\d{6}$", timestamp_part)
        writer.end_session()

    def test_session_path_returns_none_before_start(self, tmp_path: Path) -> None:
        """session_path is None before start_session is called."""
        writer = DatasetWriter(output_dir=tmp_path)
        assert writer.session_path is None

    def test_session_path_returns_none_after_end(self, tmp_path: Path) -> None:
        """session_path is None after end_session."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("test_game")
        writer.end_session()
        assert writer.session_path is None

    def test_end_session_returns_valid_path(self, tmp_path: Path) -> None:
        """end_session returns the path to the written JSONL file."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("my_game")
        path = writer.end_session()
        assert path is not None
        assert isinstance(path, Path)
        assert path.exists()
        assert path.suffix == ".jsonl"
        assert "my_game" in path.name

    def test_end_session_on_no_session_returns_none(self, tmp_path: Path) -> None:
        """end_session returns None when no session is active."""
        writer = DatasetWriter(output_dir=tmp_path)
        result = writer.end_session()
        assert result is None


# ---------------------------------------------------------------------------
# Writing steps
# ---------------------------------------------------------------------------


class TestWriteStep:
    """write_step produces valid JSONL content."""

    def test_write_step_writes_valid_json_line(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """A single write_step produces one valid JSON line."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("test_game")
        writer.write_step(sample_state, "/tmp/screenshot.png", sample_decision)
        path = writer.end_session()
        assert path is not None

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        # Verify required keys exist.
        assert "game_id" in record
        assert "timestamp" in record
        assert "state" in record
        assert "screenshot_rel" in record
        assert "decision" in record

    def test_two_steps_produce_two_jsonl_lines(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """Two writes produce two valid JSONL lines."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("multi_step")
        writer.write_step(sample_state, "/tmp/step0.png", sample_decision)
        writer.write_step(sample_state, "/tmp/step1.png", sample_decision)
        path = writer.end_session()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert "timestamp" in record

    def test_fields_match_expected_format(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """Every written record has the expected keys."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("test_fields")
        writer.write_step(sample_state, "/tmp/screen.png", sample_decision)
        path = writer.end_session()

        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["game_id"] == "test_fields"
        assert isinstance(record["timestamp"], float)
        assert record["state"]["keyNumbers"]["score"] == 150
        assert record["screenshot_rel"] != ""
        assert record["decision"]["action"] == "move"

    def test_empty_state_does_not_crash(
        self,
        tmp_path: Path,
        sample_decision: dict,
    ) -> None:
        """Writing with an empty state dict does not crash."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("empty_state")
        writer.write_step({}, "screenshot.png", sample_decision)
        path = writer.end_session()
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["state"] == {}

    def test_screenshot_path_none_handled(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """screenshot_path=None produces an empty string."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("no_screenshot")
        writer.write_step(sample_state, None, sample_decision)
        path = writer.end_session()
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["screenshot_rel"] == ""

    def test_screenshot_relpath_computed(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """screenshot_rel is the relative path from output_dir."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("relpath_test")
        writer.write_step(sample_state, "/tmp/agent_step_0000.png", sample_decision)
        path = writer.end_session()
        record = json.loads(path.read_text(encoding="utf-8"))
        # The relative path should start with ".." since /tmp is outside tmp_path.
        assert record["screenshot_rel"] != ""


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    """Context manager auto-closes the session."""

    def test_context_manager_auto_closes(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """Exiting context manager closes the file."""
        with DatasetWriter(output_dir=tmp_path) as writer:
            writer.start_session("ctx_test")
            writer.write_step(sample_state, None, sample_decision)
            path_before_exit = writer.session_path
            assert path_before_exit is not None
            assert path_before_exit.exists()

        # After context exit, session_path should be None.
        assert writer.session_path is None
        # The file should still exist on disk.
        assert path_before_exit.exists()
        # Content should be valid.
        content = path_before_exit.read_text(encoding="utf-8")
        record = json.loads(content.strip())
        assert record["game_id"] == "ctx_test"

    def test_context_manager_without_start(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """Context manager handles case where no session was started."""
        with DatasetWriter(output_dir=tmp_path) as writer:
            # No start_session called — end_session in __exit__ is a no-op.
            pass
        # This should not raise — end_session returns None.
        assert writer.session_path is None


# ---------------------------------------------------------------------------
# Output file is valid JSONL
# ---------------------------------------------------------------------------


class TestJSONLValidity:
    """Verifies output JSONL is well-formed."""

    def test_output_file_is_valid_jsonl(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """All lines in the output file parse as valid JSON."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("valid_jsonl")
        for i in range(5):
            writer.write_step(
                {"step": i, **sample_state},
                f"/tmp/screenshot_{i}.png",
                sample_decision,
            )
        path = writer.end_session()
        assert path is not None

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["state"]["step"] == i

    def test_unicode_handling(
        self,
        tmp_path: Path,
        sample_decision: dict,
    ) -> None:
        """Unicode characters in state are handled (ensure_ascii=False)."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("unicode_test")
        state = {"title": "游戏测试", "status": "完成 ✓"}
        writer.write_step(state, None, sample_decision)
        path = writer.end_session()
        raw = path.read_text(encoding="utf-8")
        assert "游戏测试" in raw
        assert "完成 ✓" in raw
        record = json.loads(raw.strip())
        assert record["state"]["title"] == "游戏测试"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case handling."""

    def test_write_step_before_start_is_noop(
        self,
        tmp_path: Path,
        sample_state: dict,
        sample_decision: dict,
    ) -> None:
        """write_step without an active session silently does nothing."""
        writer = DatasetWriter(output_dir=tmp_path)
        # No start_session called — this should not raise.
        writer.write_step(sample_state, None, sample_decision)
        assert writer.session_path is None

    def test_serialization_with_non_serializable_types(
        self,
        tmp_path: Path,
        sample_decision: dict,
    ) -> None:
        """Types that are not JSON-serialisable are handled by ``default=str``."""
        writer = DatasetWriter(output_dir=tmp_path)
        writer.start_session("non_serializable")
        state = {"path": Path("/some/game/file.html"), "count": 42}
        writer.write_step(state, None, sample_decision)
        path = writer.end_session()
        record = json.loads(path.read_text(encoding="utf-8"))
        # ``Path`` objects are serialised via ``str()`` by the ``default`` handler.
        assert record["state"]["path"] == "/some/game/file.html"
        assert record["state"]["count"] == 42
