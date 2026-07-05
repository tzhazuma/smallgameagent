"""Tests for src.agent.memory — StepRecord and WorkingMemory.

Pure unit tests with no external dependencies.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import MagicMock, patch

from src.agent.memory import (
    EpisodicMemory,
    ProceduralMemory,
    ProceduralRule,
    SemanticMemory,
    StepRecord,
    WorkingMemory,
)
from src.agent.schema import (
    _SCHEMA_VERSION,
    create_tables,
    get_connection,
    migrate,
    verify_schema,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_state() -> dict:
    return {
        "ready": True,
        "done": False,
        "win": False,
        "player": {"name": "Player", "path": "Canvas/Player"},
        "keyNumbers": {"score": 150, "coins": 3},
        "keyFlags": {"isGameOver": False, "isWin": False},
        "harvestChain": {"nodes": []},
    }


@pytest.fixture
def sample_action() -> dict:
    return {
        "action": "move",
        "params": {"dx": 0.5, "dy": 1.0, "duration_ms": 320},
        "reason": "Move toward target",
    }


# ---------------------------------------------------------------------------
# StepRecord construction
# ---------------------------------------------------------------------------


class TestStepRecord:
    """StepRecord dataclass construction and defaults."""

    def test_default_construction(self) -> None:
        record = StepRecord()
        assert record.timestamp > 0
        assert record.state_summary == {}
        assert record.action == {}
        assert record.screenshot_hash is None

    def test_custom_construction(self) -> None:
        record = StepRecord(
            state_summary={"ready": True},
            action={"action": "wait"},
            screenshot_hash="abcd1234",
        )
        assert record.state_summary == {"ready": True}
        assert record.action == {"action": "wait"}
        assert record.screenshot_hash == "abcd1234"


# ---------------------------------------------------------------------------
# WorkingMemory basics
# ---------------------------------------------------------------------------


class TestWorkingMemory:
    """WorkingMemory management, push, history, stuck detection, and prompt formatting."""

    # -- Construction & properties -------------------------------------------

    def test_constructs_with_defaults(self) -> None:
        mem = WorkingMemory()
        assert mem._max_history == 60
        assert mem.step_count == 0
        assert mem.history == []
        assert mem.is_stuck is False

    def test_constructs_with_custom_max(self) -> None:
        mem = WorkingMemory(max_history=10)
        assert mem._max_history == 10

    # -- push_action ---------------------------------------------------------

    def test_push_action_adds_record_with_timestamp(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action)
        assert mem.step_count == 1
        record = mem.history[0]
        assert record.timestamp > 0
        assert record.state_summary == {
            "ready": True,
            "done": False,
            "win": False,
            "keyNumbers": {"score": 150, "coins": 3},
            "keyFlags": {"isGameOver": False, "isWin": False},
        }
        assert record.action == sample_action

    def test_push_action_truncates_at_max_history(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory(max_history=3)
        for _ in range(5):
            mem.push_action(sample_state, sample_action)
        assert mem.step_count == 3, f"Expected 3, got {mem.step_count}"

    def test_push_action_sets_screenshot_hash_when_bytes_provided(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action, screenshot_bytes=b"fake-png-bytes")
        record = mem.history[0]
        assert record.screenshot_hash is not None
        assert len(record.screenshot_hash) == 64  # SHA-256 hex digest

    def test_push_action_sets_screenshot_hash_none_when_no_bytes(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action)
        assert mem.history[0].screenshot_hash is None

    def test_push_action_updates_last_activity(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        old = mem._last_activity
        time.sleep(0.001)
        mem.push_action(sample_state, sample_action)
        assert mem._last_activity > old

    # -- recent_actions ------------------------------------------------------

    def test_recent_actions_returns_correct_count(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        for _ in range(10):
            mem.push_action(sample_state, sample_action)
        recent = mem.recent_actions(3)
        assert len(recent) == 3

    def test_recent_actions_returns_max_available_when_n_exceeds_stored(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action)
        recent = mem.recent_actions(10)
        assert len(recent) == 1

    def test_recent_actions_returns_empty_when_n_is_zero_or_negative(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action)
        assert mem.recent_actions(0) == []
        assert mem.recent_actions(-1) == []

    # -- detect_stuck --------------------------------------------------------

    def test_detect_stuck_returns_false_when_position_changes(self) -> None:
        mem = WorkingMemory()
        assert mem.detect_stuck((0.0, 0.0)) is False
        assert mem.detect_stuck((10.0, 10.0)) is False  # big move, no streak

    def test_detect_stuck_returns_true_after_five_consecutive_non_moves(self) -> None:
        mem = WorkingMemory()
        for _ in range(5):
            assert mem.detect_stuck((100.0, 100.0)) is False
        # 6th call — streak >= 5
        assert mem.detect_stuck((100.0, 100.0)) is True

    def test_detect_stuck_resets_streak_on_movement(self) -> None:
        mem = WorkingMemory()
        # Build up streak (first call only sets pos, next 4 calls each +1)
        for _ in range(5):
            mem.detect_stuck((50.0, 50.0))
        assert mem._stuck_streak == 4
        # Move away — streak resets
        assert mem.detect_stuck((60.0, 60.0)) is False
        assert mem._stuck_streak == 0

    def test_detect_stuck_streak_clears_on_first_call(self) -> None:
        """First call sets _last_player_pos but does not increment streak."""
        mem = WorkingMemory()
        mem.detect_stuck((10.0, 20.0))
        assert mem._stuck_streak == 0
        assert mem._last_player_pos == (10.0, 20.0)

    def test_detect_stuck_with_custom_threshold(self) -> None:
        mem = WorkingMemory()
        mem.detect_stuck((0.0, 0.0))
        # Small movement below threshold (0.02 < 0.05 default) still counts as stuck
        mem.detect_stuck((0.01, 0.01))
        assert mem._stuck_streak == 1
        # Movement above threshold resets
        mem.detect_stuck((0.1, 0.0))
        assert mem._stuck_streak == 0

    # -- to_prompt_context ---------------------------------------------------

    def test_to_prompt_context_empty_history(self) -> None:
        mem = WorkingMemory()
        assert mem.to_prompt_context() == ""

    def test_to_prompt_context_formats_records(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action, screenshot_bytes=b"img-data")
        output = mem.to_prompt_context(n=1)
        assert "Step:" in output
        assert "action=move" in output
        assert "img:" in output
        assert "Move toward target" in output

    def test_to_prompt_context_respects_n(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        for _ in range(5):
            mem.push_action(sample_state, sample_action)
        output = mem.to_prompt_context(n=3)
        lines = output.strip().split("\n")
        assert len(lines) == 3

    # -- is_expired ----------------------------------------------------------

    def test_is_expired_false_with_recent_activity(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        mem.push_action(sample_state, sample_action)
        assert mem.is_expired() is False

    def test_is_expired_true_after_inactivity(self) -> None:
        mem = WorkingMemory()
        # Force _last_activity far into the past
        mem._last_activity = 0.0
        assert mem.is_expired() is True

    # -- reset ---------------------------------------------------------------

    def test_reset_clears_history_and_counters(
        self,
        sample_state: dict,
        sample_action: dict,
    ) -> None:
        mem = WorkingMemory()
        for _ in range(3):
            mem.push_action(sample_state, sample_action)
        mem.detect_stuck((50.0, 50.0))  # sets pos, streak=0
        mem.detect_stuck((50.0, 50.0))  # streak=1
        mem.detect_stuck((50.0, 50.0))  # streak=2
        assert mem.step_count == 3
        assert mem._stuck_streak == 2
        mem.reset()
        assert mem.step_count == 0
        assert mem.history == []
        assert mem._stuck_streak == 0
        assert mem._last_player_pos is None

    # -- is_stuck property ---------------------------------------------------

    def test_is_stuck_property(self) -> None:
        mem = WorkingMemory()
        assert mem.is_stuck is False
        for _ in range(6):
            mem.detect_stuck((0.0, 0.0))
        assert mem.is_stuck is True

    # -- history returns a copy ----------------------------------------------

    def test_history_returns_copy(self) -> None:
        mem = WorkingMemory()
        mem.push_action({"ready": True}, {"action": "wait"})
        h = mem.history
        h.clear()
        assert mem.step_count == 1  # original list unchanged


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    """Test suite for schema DDL, migrations, and connection helper."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _insert_session(conn: sqlite3.Connection, session_id: str = "ses_001") -> None:
        conn.execute(
            "INSERT INTO sessions (id, game_id, start_time) VALUES (?, ?, ?)",
            (session_id, "test_game", "2026-07-05T00:00:00"),
        )

    @staticmethod
    def _insert_step(
        conn: sqlite3.Connection,
        step_id: str = "stp_001",
        session_id: str = "ses_001",
        step_number: int = 1,
        state_json: str = '{"x": 100, "y": 200}',
        action: str = "move",
        params_json: str = '{"dx": 0.5, "dy": 1.0}',
    ) -> None:
        conn.execute(
            "INSERT INTO steps (id, session_id, step_number, timestamp, state_json, "
            "action, params_json, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step_id,
                session_id,
                step_number,
                "2026-07-05T00:00:01",
                state_json,
                action,
                params_json,
                "test reason",
            ),
        )

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_get_connection_returns_working_conn(self, tmp_path: Path) -> None:
        """get_connection should open a writable database with WAL mode."""
        db = tmp_path / "test.db"
        conn = get_connection(db)

        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE demo (v TEXT)")
        conn.execute("INSERT INTO demo (v) VALUES ('hello')")
        row = conn.execute("SELECT v FROM demo").fetchone()
        assert row[0] == "hello"

        conn.close()

    def test_create_tables_succeeds(self, tmp_path: Path) -> None:
        """create_tables should run without error on a fresh database."""
        db = tmp_path / "create.db"
        conn = get_connection(db)
        create_tables(conn)
        conn.close()

    def test_all_tables_exist_after_create(self, tmp_path: Path) -> None:
        """All six objects should be present after create_tables."""
        db = tmp_path / "tables.db"
        conn = get_connection(db)
        create_tables(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')"
            ).fetchall()
        }
        assert "schema_version" in tables
        assert "sessions" in tables
        assert "steps" in tables
        assert "steps_fts" in tables

        conn.close()

    def test_verify_schema_returns_true(self, tmp_path: Path) -> None:
        """verify_schema should return True after create_tables."""
        db = tmp_path / "verify.db"
        conn = get_connection(db)
        create_tables(conn)
        assert verify_schema(conn) is True
        conn.close()

    def test_verify_schema_returns_false_on_empty(self, tmp_path: Path) -> None:
        """verify_schema should return False for a completely empty database."""
        db = tmp_path / "empty.db"
        conn = get_connection(db)
        assert verify_schema(conn) is False
        conn.close()

    def test_indexes_are_created(self, tmp_path: Path) -> None:
        """All four expected indexes should exist after create_tables."""
        db = tmp_path / "indexes.db"
        conn = get_connection(db)
        create_tables(conn)

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name IS NOT NULL"
            ).fetchall()
        }
        assert "idx_sessions_game" in indexes
        assert "idx_sessions_result" in indexes
        assert "idx_steps_session" in indexes
        assert "idx_steps_action" in indexes

        conn.close()

    def test_fts5_query_returns_matching_rows(self, tmp_path: Path) -> None:
        """FTS5 should find rows that match a keyword in state_json or action."""
        db = tmp_path / "fts.db"
        conn = get_connection(db)
        create_tables(conn)

        self._insert_session(conn)
        self._insert_step(
            conn,
            state_json='{"x": 100, "y": 200, "target": "coin"}',
            action="collect",
        )

        conn.execute("INSERT INTO steps_fts(steps_fts) VALUES('rebuild')")

        rows = conn.execute(
            "SELECT action FROM steps_fts WHERE steps_fts MATCH ?", ("coin",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "collect"

        conn.close()

    def test_foreign_key_cascade_deletes_steps(self, tmp_path: Path) -> None:
        """Deleting a session should cascade-delete its steps."""
        db = tmp_path / "fk.db"
        conn = get_connection(db)
        create_tables(conn)

        self._insert_session(conn)
        self._insert_step(conn)

        before = conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        assert before == 1

        conn.execute("DELETE FROM sessions WHERE id = 'ses_001'")

        after = conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        assert after == 0

        conn.close()

    def test_wal_mode_active(self, tmp_path: Path) -> None:
        """The connection should be in WAL journal mode."""
        db = tmp_path / "wal.db"
        conn = get_connection(db)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL"
        conn.close()

    def test_schema_version_set_after_creation(self, tmp_path: Path) -> None:
        """Schema version should equal _SCHEMA_VERSION after create_tables."""
        db = tmp_path / "version.db"
        conn = get_connection(db)
        create_tables(conn)

        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == _SCHEMA_VERSION

        conn.close()

    def test_migration_v0_to_v1(self, tmp_path: Path) -> None:
        """Calling migrate on an empty database should create full schema."""
        db = tmp_path / "migrate.db"
        conn = get_connection(db)
        migrate(conn)

        assert verify_schema(conn) is True

        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == _SCHEMA_VERSION

        conn.close()

    def test_migrate_idempotent(self, tmp_path: Path) -> None:
        """Calling migrate twice should not raise or corrupt anything."""
        db = tmp_path / "idempotent.db"
        conn = get_connection(db)

        migrate(conn)
        migrate(conn)

        assert verify_schema(conn) is True

        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == _SCHEMA_VERSION

        conn.close()


# ---------------------------------------------------------------------------
# Episodic memory tests
# ---------------------------------------------------------------------------


class TestEpisodicMemory:
    """EpisodicMemory SQLite persistence, session/step lifecycle, and summarization."""

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def memory(self, tmp_path: Path) -> EpisodicMemory:
        """Create a temporary EpisodicMemory backed by a fresh SQLite DB."""
        db_path = tmp_path / "test_episodic.db"
        mem = EpisodicMemory(db_path)
        yield mem
        mem.close()

    # ------------------------------------------------------------------
    # start_session
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_start_session_returns_uuid(self, memory: EpisodicMemory) -> None:
        """start_session should return a 16-character hex string."""
        session_id = await memory.start_session("test_game")
        assert len(session_id) == 16
        assert all(c in "0123456789abcdef" for c in session_id)

    @pytest.mark.asyncio
    async def test_start_session_inserts_correct_game_id(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """start_session should persist the game_id in the sessions table."""
        session_id = await memory.start_session("my_game")
        row = memory._conn.execute(
            "SELECT game_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row is not None
        assert row["game_id"] == "my_game"

    # ------------------------------------------------------------------
    # record_step
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_record_step_inserts_correct_fields(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """record_step should insert a row with all expected column values."""
        session_id = await memory.start_session("test_game")
        state = {"x": 100, "y": 200}
        action = {
            "action": "move",
            "params": {"dx": 0.5},
            "reason": "go right",
        }
        await memory.record_step(session_id, 1, state, action, "manual", 150.0)

        rows = memory._conn.execute(
            "SELECT * FROM steps WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["step_number"] == 1
        assert row["action"] == "move"
        assert json.loads(row["state_json"]) == state
        assert json.loads(row["params_json"]) == {"dx": 0.5}
        assert row["reason"] == "go right"
        assert row["mode"] == "manual"
        assert row["latency_ms"] == 150.0

    @pytest.mark.asyncio
    async def test_record_step_saves_screenshot_to_correct_path(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """record_step with screenshot_bytes should write the PNG file."""
        db_path = memory._db_path
        session_id = await memory.start_session("test_game")
        await memory.record_step(
            session_id,
            1,
            {},
            {"action": "tap"},
            "auto",
            50.0,
            screenshot_bytes=b"png-data",
        )
        expected = db_path.parent / "memory_screenshots" / session_id / "0001.png"
        assert expected.exists()
        assert expected.read_bytes() == b"png-data"

    @pytest.mark.asyncio
    async def test_record_step_updates_total_steps(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """record_step should increment sessions.total_steps."""
        session_id = await memory.start_session("test_game")
        for i in range(3):
            await memory.record_step(
                session_id,
                i + 1,
                {},
                {"action": "move"},
                "auto",
                10.0,
            )

        row = memory._conn.execute(
            "SELECT total_steps FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row["total_steps"] == 3

    # ------------------------------------------------------------------
    # end_session
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_end_session_updates_end_time_and_result(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """end_session should set end_time, result, and score."""
        session_id = await memory.start_session("test_game")
        await memory.end_session(session_id, "win", 95.5)

        row = memory._conn.execute(
            "SELECT end_time, result, score FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row["end_time"] is not None
        assert row["result"] == "win"
        assert row["score"] == 95.5

    @pytest.mark.asyncio
    async def test_end_session_triggers_auto_summarize(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """end_session should auto-summarize when total_steps > 10."""
        session_id = await memory.start_session("test_game")
        for i in range(11):
            await memory.record_step(
                session_id,
                i + 1,
                {"x": i},
                {"action": "move", "reason": f"step {i}"},
                "auto",
                10.0,
            )
        await memory.end_session(session_id, "win", 100.0)

        row = memory._conn.execute(
            "SELECT summary FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row is not None
        assert row["summary"] is not None
        assert len(row["summary"]) > 0

    # ------------------------------------------------------------------
    # get_session_steps
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_session_steps_returns_all_chronologically(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """get_session_steps should return steps ordered by step_number."""
        session_id = await memory.start_session("test_game")
        for i in range(5):
            await memory.record_step(
                session_id,
                i + 1,
                {"n": i},
                {"action": "move"},
                "auto",
                10.0,
            )

        steps = await memory.get_session_steps(session_id)
        assert len(steps) == 5
        for i, step in enumerate(steps):
            assert step["step_number"] == i + 1

    # ------------------------------------------------------------------
    # find_similar
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_find_similar_returns_most_recent(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """find_similar should return most recent sessions for the same game_id."""
        sid1 = await memory.start_session("game_a")
        await memory.record_step(sid1, 1, {}, {"action": "tap"}, "auto", 10.0)
        await memory.end_session(sid1, "win")

        sid2 = await memory.start_session("game_a")
        await memory.record_step(sid2, 1, {}, {"action": "tap"}, "auto", 10.0)
        await memory.end_session(sid2, "lose")

        sid3 = await memory.start_session("game_a")
        await memory.record_step(sid3, 1, {}, {"action": "tap"}, "auto", 10.0)
        await memory.end_session(sid3, "win")

        results = await memory.find_similar("game_a", top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == sid3
        assert results[1]["id"] == sid2

    @pytest.mark.asyncio
    async def test_find_similar_empty_for_unknown_game_id(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """find_similar should return empty list for unknown game_id."""
        results = await memory.find_similar("nonexistent_game")
        assert results == []

    # ------------------------------------------------------------------
    # summarize_session
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_summarize_session_generates_non_empty_text(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """summarize_session should produce text and persist it."""
        session_id = await memory.start_session("test_game")
        for i in range(3):
            await memory.record_step(
                session_id,
                i + 1,
                {"x": i},
                {"action": "move", "reason": f"step {i}"},
                "auto",
                10.0,
            )

        summary = memory.summarize_session(session_id)
        assert len(summary) > 0
        assert "action=move" in summary

        row = memory._conn.execute(
            "SELECT summary FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assert row["summary"] == summary

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_lists(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """Queries on an empty database should return empty lists."""
        steps = await memory.get_session_steps("nonexistent")
        assert steps == []

        results = await memory.find_similar("any_game")
        assert results == []

    @pytest.mark.asyncio
    async def test_close_closes_connection(self, tmp_path: Path) -> None:
        """close() should prevent further database operations."""
        db_path = tmp_path / "close_test.db"
        mem = EpisodicMemory(db_path)
        mem.close()

        with pytest.raises(sqlite3.ProgrammingError):
            mem._conn.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_steps_isolated_between_sessions(
        self,
        memory: EpisodicMemory,
    ) -> None:
        """Steps from different sessions should not mix."""
        sid1 = await memory.start_session("game_a")
        sid2 = await memory.start_session("game_b")

        await memory.record_step(
            sid1,
            1,
            {"data": "a1"},
            {"action": "move"},
            "auto",
            10.0,
        )
        await memory.record_step(
            sid2,
            1,
            {"data": "b1"},
            {"action": "tap"},
            "auto",
            20.0,
        )
        await memory.record_step(
            sid1,
            2,
            {"data": "a2"},
            {"action": "jump"},
            "auto",
            15.0,
        )

        steps1 = await memory.get_session_steps(sid1)
        steps2 = await memory.get_session_steps(sid2)

        assert len(steps1) == 2
        assert len(steps2) == 1
        assert steps1[0]["action"] == "move"
        assert steps1[1]["action"] == "jump"
        assert steps2[0]["action"] == "tap"


# ---------------------------------------------------------------------------
# Semantic memory tests
# ---------------------------------------------------------------------------


class TestSemanticMemory:
    """SemanticMemory vector search, knowledge CRUD, and extraction."""

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def mem(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SemanticMemory:
        """Create a SemanticMemory with mocked embeddings."""
        db = tmp_path / "semantic.db"
        m = SemanticMemory(db)
        monkeypatch.setattr(m, "_get_embedding", lambda text: [0.1] * 384)
        yield m
        m.close()

    @pytest.fixture
    def mock_api_client(self) -> MagicMock:
        """Return a MagicMock that mimics OpenCodeGoClient.chat."""
        client = MagicMock()
        client.chat.return_value.choices[0].message.content = (
            "- The coin appears after breaking the box\n"
            "- Jump timing must be precise near the edge\n"
            "- Enemy patrols follow a fixed path"
        )
        return client

    # ------------------------------------------------------------------
    # add_knowledge
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_knowledge_stores_correct_fields(self, mem: SemanticMemory) -> None:
        """add_knowledge should persist all fields in knowledge_meta."""
        kid = await mem.add_knowledge(
            "Break the box to reveal the coin",
            metadata={"game_id": "game_001", "collection": "tips", "confidence": 0.8},
            importance=0.9,
        )
        row = mem._conn.execute(
            "SELECT * FROM knowledge_meta WHERE id = ?",
            (kid,),
        ).fetchone()
        assert row is not None
        assert row["id"] == kid
        assert row["game_id"] == "game_001"
        assert row["collection"] == "tips"
        assert row["content"] == "Break the box to reveal the coin"
        assert row["importance"] == 0.9
        assert row["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_add_knowledge_stores_importance(self, mem: SemanticMemory) -> None:
        """add_knowledge should store the importance value correctly."""
        kid = await mem.add_knowledge(
            "Important knowledge",
            metadata={"game_id": "test"},
            importance=0.3,
        )
        row = mem._conn.execute(
            "SELECT importance FROM knowledge_meta WHERE id = ?",
            (kid,),
        ).fetchone()
        assert row["importance"] == 0.3

    @pytest.mark.asyncio
    async def test_add_knowledge_defaults_confidence_to_0_5(
        self,
        mem: SemanticMemory,
    ) -> None:
        """add_knowledge should default confidence to 0.5 when not provided."""
        kid = await mem.add_knowledge(
            "Default confidence",
            metadata={"game_id": "test"},
        )
        row = mem._conn.execute(
            "SELECT confidence FROM knowledge_meta WHERE id = ?",
            (kid,),
        ).fetchone()
        assert row["confidence"] == 0.5

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_query_returns_results_by_game_id(self, mem: SemanticMemory) -> None:
        """query should filter results by game_id."""
        await mem.add_knowledge("Item A", {"game_id": "game_a"})
        await mem.add_knowledge("Item B", {"game_id": "game_b"})

        results = await mem.query("test query", game_id="game_a")
        assert len(results) == 1
        assert results[0]["content"] == "Item A"

    @pytest.mark.asyncio
    async def test_query_collection_filtering(self, mem: SemanticMemory) -> None:
        """query should filter by collection."""
        await mem.add_knowledge("General tip", {"game_id": "g", "collection": "general"})
        await mem.add_knowledge("Extracted tip", {"game_id": "g", "collection": "extracted"})

        results = await mem.query("test query", collection="extracted")
        assert len(results) == 1
        assert results[0]["content"] == "Extracted tip"

    @pytest.mark.asyncio
    async def test_query_filters_low_confidence_items(self, mem: SemanticMemory) -> None:
        """query should exclude items with confidence < 0.3."""
        await mem.add_knowledge("Low conf", {"game_id": "g", "confidence": 0.1})
        await mem.add_knowledge("High conf", {"game_id": "g", "confidence": 0.9})

        results = await mem.query("test query", game_id="g")
        assert len(results) == 1
        assert results[0]["content"] == "High conf"

    @pytest.mark.asyncio
    async def test_query_empty_db_returns_empty(self, mem: SemanticMemory) -> None:
        """query on an empty database should return []."""
        results = await mem.query("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_multiple_game_id_filtering(self, mem: SemanticMemory) -> None:
        """query should correctly isolate between different game_ids."""
        await mem.add_knowledge("G1 item", {"game_id": "g1"})
        await mem.add_knowledge("G2 item A", {"game_id": "g2"})
        await mem.add_knowledge("G2 item B", {"game_id": "g2"})

        g1_results = await mem.query("query", game_id="g1")
        g2_results = await mem.query("query", game_id="g2")

        assert len(g1_results) == 1
        assert len(g2_results) == 2

    @pytest.mark.asyncio
    async def test_query_similarity_in_range(self, mem: SemanticMemory) -> None:
        """query results should have similarity in [0, 1]."""
        await mem.add_knowledge("Some knowledge", {"game_id": "g"})
        results = await mem.query("test query", game_id="g")
        assert len(results) == 1
        sim = results[0]["similarity"]
        assert 0.0 <= sim <= 1.0

    # ------------------------------------------------------------------
    # extract_from_session
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_extract_from_session_with_api_client(
        self,
        mem: SemanticMemory,
        mock_api_client: MagicMock,
    ) -> None:
        """extract_from_session should create items when api_client is given."""
        summary = "Session where the player broke boxes and collected coins."
        count = await mem.extract_from_session(
            summary,
            "game_001",
            api_client=mock_api_client,
        )
        assert count == 3
        mock_api_client.chat.assert_called_once()

        rows = mem._conn.execute(
            "SELECT content, collection, game_id FROM knowledge_meta",
        ).fetchall()
        assert len(rows) == 3
        for row in rows:
            assert row["collection"] == "extracted"
            assert row["game_id"] == "game_001"

    @pytest.mark.asyncio
    async def test_extract_from_session_uses_regex_fallback(
        self,
        mem: SemanticMemory,
    ) -> None:
        """extract_from_session should parse bullet points without API client."""
        summary = (
            "- First insight: always jump at the edge\n"
            "- Second insight: collect coins first\n"
            "- Third insight: avoid red enemies"
        )
        count = await mem.extract_from_session(summary, "game_002")
        assert count == 3

        rows = mem._conn.execute(
            "SELECT content FROM knowledge_meta",
        ).fetchall()
        assert len(rows) == 3

    # ------------------------------------------------------------------
    # close / edge cases
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_close_disconnects(self, tmp_path: Path) -> None:
        """close() should prevent further database operations."""
        db = tmp_path / "close_sem.db"
        m = SemanticMemory(db)
        m.close()
        with pytest.raises(sqlite3.ProgrammingError):
            m._conn.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_missing_sqlite_vec_graceful(self, tmp_path: Path) -> None:
        """SemanticMemory should handle missing sqlite-vec gracefully."""
        with patch.dict("sys.modules", {"sqlite_vec": None}):
            db = tmp_path / "no_vec.db"
            m = SemanticMemory(db)
            assert m._vec_available is False
            results = await m.query("test")
            assert results == []
            m.close()


# ---------------------------------------------------------------------------
# Procedural memory tests
# ---------------------------------------------------------------------------


class TestProceduralMemory:
    """ProceduralMemory rule matching, learning, persistence, and condition eval."""

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def json_path(self, tmp_path: Path) -> Path:
        return tmp_path / "test_rules.json"

    @pytest.fixture
    def mem(self, json_path: Path) -> ProceduralMemory:
        return ProceduralMemory(json_path)

    @pytest.fixture
    def wm_stuck(self) -> WorkingMemory:
        """WorkingMemory with a stuck streak >= 5."""
        wm = WorkingMemory()
        for _ in range(6):
            wm.detect_stuck((0.0, 0.0))
        return wm

    @pytest.fixture
    def wm_not_stuck(self) -> WorkingMemory:
        """WorkingMemory that is not stuck."""
        wm = WorkingMemory()
        wm.detect_stuck((0.0, 0.0))
        return wm

    @pytest.fixture
    def wm_some_steps(self) -> WorkingMemory:
        """WorkingMemory with 10 steps pushed."""
        wm = WorkingMemory()
        for _ in range(10):
            wm.push_action({"ready": True}, {"action": "wait"})
        return wm

    # ------------------------------------------------------------------
    # Initialisation & seed rules
    # ------------------------------------------------------------------

    def test_fresh_init_has_builtin_seed_rules(self, json_path: Path) -> None:
        """_load_rules should seed 3 built-in rules on fresh init."""
        mem = ProceduralMemory(json_path)
        assert mem.rule_count == 3
        names = {r.name for r in mem.rules}
        assert names == {"escape_stuck", "follow_guide", "idle_wait"}

    def test_load_rules_restores_from_json(self, json_path: Path) -> None:
        """Round-trip: create mem, persist, re-initialise from same path."""
        mem1 = ProceduralMemory(json_path)
        mem1.learn(
            ProceduralRule(
                name="test_rule",
                condition="is_stuck",
                priority=5,
                source="learned",
            )
        )
        assert mem1.rule_count == 4

        mem2 = ProceduralMemory(json_path)
        assert mem2.rule_count == 4
        names = {r.name for r in mem2.rules}
        assert "test_rule" in names

    # ------------------------------------------------------------------
    # match()
    # ------------------------------------------------------------------

    def test_match_returns_highest_priority_when_multiple_match(
        self,
        mem: ProceduralMemory,
        wm_stuck: WorkingMemory,
    ) -> None:
        """match should return the rule with the highest priority."""
        state: dict[str, Any] = {"_game_id": "test"}
        # escape_stuck (pri=8), idle_wait (pri=3)
        rule = mem.match(state, working_memory=wm_stuck)
        assert rule is not None
        assert rule.name == "escape_stuck"
        assert rule.priority == 8

    def test_match_returns_none_when_no_conditions_match(
        self,
        json_path: Path,
        wm_not_stuck: WorkingMemory,
    ) -> None:
        """match should return None when no rule condition is met."""
        # Create a mem with a single rule that won't match
        json_path.write_text(
            json.dumps(
                [
                    {
                        "name": "never_matches",
                        "condition": "is_stuck",
                        "priority": 5,
                        "action_template": {"action": "wait", "params": {}},
                        "success_rate": 0.0,
                        "times_applied": 0,
                        "source": "builtin",
                        "game_id": "",
                    },
                ]
            )
        )
        mem = ProceduralMemory(json_path)
        state: dict[str, Any] = {"_game_id": "test"}
        rule = mem.match(state, working_memory=wm_not_stuck)
        assert rule is None

    def test_match_empty_rules_returns_none(self, json_path: Path) -> None:
        """match should return None when the rules list is empty."""
        # Write empty list to file
        json_path.write_text("[]")
        mem = ProceduralMemory(json_path)
        assert mem.rule_count == 0
        rule = mem.match({}, working_memory=None)
        assert rule is None

    def test_match_with_agent_context_like_object(
        self,
        mem: ProceduralMemory,
        wm_stuck: WorkingMemory,
    ) -> None:
        """match should extract probe_state and working_memory from context-like obj."""

        class FakeContext:
            def __init__(self) -> None:
                self.probe_state: dict[str, Any] = {"_game_id": "test"}
                self.working_memory = wm_stuck

        rule = mem.match(FakeContext())
        assert rule is not None
        assert rule.name == "escape_stuck"

    # ------------------------------------------------------------------
    # learn()
    # ------------------------------------------------------------------

    def test_learn_adds_rule_and_persists(self, json_path: Path) -> None:
        """learn should add a rule and persist it to JSON."""
        mem = ProceduralMemory(json_path)
        new_rule = ProceduralRule(
            name="custom_rule",
            condition="step_count > 5",
            priority=7,
            source="learned",
        )
        mem.learn(new_rule)
        assert mem.rule_count == 4
        assert mem.rules[-1].name == "custom_rule"

        # Verify persistence
        mem2 = ProceduralMemory(json_path)
        assert mem2.rule_count == 4
        assert mem2.rules[-1].name == "custom_rule"

    # ------------------------------------------------------------------
    # update_success_rate()
    # ------------------------------------------------------------------

    def test_success_rate_ema_first_success(self, mem: ProceduralMemory) -> None:
        """EMA: first success should set rate to 1.0."""
        mem.update_success_rate("idle_wait", succeeded=True)
        rule = [r for r in mem.rules if r.name == "idle_wait"][0]
        assert rule.success_rate == 1.0
        assert rule.times_applied == 1

    def test_success_rate_ema_failure_then_success(self, mem: ProceduralMemory) -> None:
        """EMA: 0.0 -> 1.0 -> 0.5 pattern."""
        mem.update_success_rate("idle_wait", succeeded=True)
        mem.update_success_rate("idle_wait", succeeded=False)
        rule = [r for r in mem.rules if r.name == "idle_wait"][0]
        assert rule.times_applied == 2
        # After first success: 0 + 1/1 * (1-0) = 1.0
        # After failure: 1.0 + 1/2 * (0-1.0) = 1.0 - 0.5 = 0.5
        assert rule.success_rate == 0.5

    # ------------------------------------------------------------------
    # _evaluate_condition()
    # ------------------------------------------------------------------

    def test_eval_is_stuck_true(
        self,
        mem: ProceduralMemory,
        wm_stuck: WorkingMemory,
    ) -> None:
        """_evaluate_condition should return True for is_stuck when stuck."""
        assert mem._evaluate_condition("is_stuck", {}, wm_stuck) is True

    def test_eval_is_stuck_false(
        self,
        mem: ProceduralMemory,
        wm_not_stuck: WorkingMemory,
    ) -> None:
        """_evaluate_condition should return False for is_stuck when not stuck."""
        assert mem._evaluate_condition("is_stuck", {}, wm_not_stuck) is False

    def test_eval_stuck_streak_ge(
        self,
        mem: ProceduralMemory,
        wm_stuck: WorkingMemory,
    ) -> None:
        """_evaluate_condition should parse stuck_streak >= N."""
        assert mem._evaluate_condition("stuck_streak >= 5", {}, wm_stuck) is True
        assert mem._evaluate_condition("stuck_streak >= 10", {}, wm_stuck) is False

    def test_eval_step_count_gt(
        self,
        mem: ProceduralMemory,
        wm_some_steps: WorkingMemory,
    ) -> None:
        """_evaluate_condition should parse step_count > N."""
        assert mem._evaluate_condition("step_count > 5", {}, wm_some_steps) is True
        assert mem._evaluate_condition("step_count > 15", {}, wm_some_steps) is False

    def test_eval_game_id_eq(
        self,
        mem: ProceduralMemory,
    ) -> None:
        """_evaluate_condition should parse game_id == \"X\"."""
        state = {"_game_id": "my_game"}
        assert mem._evaluate_condition('game_id == "my_game"', state, None) is True
        assert mem._evaluate_condition('game_id == "other"', state, None) is False

    def test_eval_unknown_condition_returns_false(
        self,
        mem: ProceduralMemory,
    ) -> None:
        """_evaluate_condition should return False for unrecognised conditions."""
        assert mem._evaluate_condition("bogus_condition", {}, None) is False

    # ------------------------------------------------------------------
    # has_visual_arrow / no_target helpers
    # ------------------------------------------------------------------

    def test_has_visual_arrow_with_candidates(self, mem: ProceduralMemory) -> None:
        """_has_visual_arrow should return True when guide_or_target_candidates exists."""
        state = {"guide_or_target_candidates": [{"x": 100, "y": 200}]}
        assert mem._has_visual_arrow(state) is True

    def test_has_visual_arrow_with_arrow_key(self, mem: ProceduralMemory) -> None:
        """_has_visual_arrow should return True when any key contains 'arrow'."""
        state = {"some_arrow": True}
        assert mem._has_visual_arrow(state) is True

    def test_has_visual_arrow_false(self, mem: ProceduralMemory) -> None:
        """_has_visual_arrow should return False when no arrow/guide indicators."""
        state = {"score": 100}
        assert mem._has_visual_arrow(state) is False

    def test_no_target_without_candidates(self, mem: ProceduralMemory) -> None:
        """_no_target should return True when guide_or_target_candidates missing."""
        assert mem._no_target({"score": 100}) is True

    def test_no_target_with_empty_list(self, mem: ProceduralMemory) -> None:
        """_no_target should return True when guide_or_target_candidates is empty list."""
        assert mem._no_target({"guide_or_target_candidates": []}) is True

    # ------------------------------------------------------------------
    # _archive_low_performance()
    # ------------------------------------------------------------------

    def test_archive_low_performance_moves_rules(self, json_path: Path) -> None:
        """_archive_low_performance should move low-perf rules to archive."""
        mem = ProceduralMemory(json_path)
        # Add a low-perf rule (success_rate < 0.2, times_applied > 5)
        low_perf = ProceduralRule(
            name="bad_rule",
            condition="is_stuck",
            priority=1,
            success_rate=0.1,
            times_applied=10,
            source="learned",
        )
        mem.learn(low_perf)
        assert mem.rule_count == 4

        mem._archive_low_performance()

        # Should now have 3 rules (bad_rule removed)
        assert mem.rule_count == 3
        assert all(r.name != "bad_rule" for r in mem.rules)

        # Archive file should exist with bad_rule
        archive_path = json_path.with_name(json_path.stem + "_archive" + json_path.suffix)
        assert archive_path.exists()
        archive_data = json.loads(archive_path.read_text())
        assert len(archive_data) == 1
        assert archive_data[0]["name"] == "bad_rule"

    def test_archive_does_not_remove_good_rules(self, json_path: Path) -> None:
        """_archive_low_performance should keep rules with good performance."""
        mem = ProceduralMemory(json_path)
        # All builtin rules have success_rate=0, times_applied=0
        # So they won't be archived
        mem._archive_low_performance()
        assert mem.rule_count == 3
        assert {r.name for r in mem.rules} == {"escape_stuck", "follow_guide", "idle_wait"}
