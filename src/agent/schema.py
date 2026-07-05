"""SQLite schema management for agent memory persistence.

Provides DDL, migration, and connection helper functions used by
EpisodicMemory (T6) and SemanticMemory (T7).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_VERSION: int = 2

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode, foreign keys, and busy timeout.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file.

    Returns
    -------
    sqlite3.Connection
        Configured database connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and the FTS5 virtual table.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database connection.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "  id TEXT PRIMARY KEY,"
        "  game_id TEXT NOT NULL,"
        "  start_time TIMESTAMP NOT NULL,"
        "  end_time TIMESTAMP,"
        "  total_steps INTEGER DEFAULT 0,"
        "  result TEXT,"
        "  score REAL DEFAULT 0,"
        "  summary TEXT,"
        "  embedding BLOB,"
        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS steps ("
        "  id TEXT PRIMARY KEY,"
        "  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,"
        "  step_number INTEGER NOT NULL,"
        "  timestamp TIMESTAMP NOT NULL,"
        "  state_json TEXT NOT NULL,"
        "  action TEXT NOT NULL,"
        "  params_json TEXT NOT NULL,"
        "  reason TEXT,"
        "  mode TEXT,"
        "  latency_ms REAL,"
        "  screenshot_path TEXT"
        ")"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS steps_fts USING fts5("
        "  state_json, action, reason,"
        "  content='steps', content_rowid='rowid'"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_game ON sessions(game_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_result ON sessions(result)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_steps_session ON steps(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_steps_action ON steps(action)"
    )
    conn.execute(
        "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
    )


def create_semantic_tables(conn: sqlite3.Connection) -> None:
    """Create the knowledge_meta table and the vec0 virtual table.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database connection with sqlite-vec loaded (or attempt
        is made gracefully).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_meta ("
        "  id TEXT PRIMARY KEY,"
        "  collection TEXT DEFAULT 'general',"
        "  game_id TEXT,"
        "  content TEXT NOT NULL,"
        "  importance REAL DEFAULT 0.5,"
        "  confidence REAL DEFAULT 0.5,"
        "  times_used INTEGER DEFAULT 0,"
        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_game ON knowledge_meta(game_id)"
    )
    # vec0 virtual table — may fail if sqlite-vec is not loaded.
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec "
            "USING vec0(embedding float[384])"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database schema up to the latest version.

    Reads the current ``schema_version`` and applies any missing migration
    steps incrementally.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database connection.
    """
    # Check whether schema_version exists — if not, this is a fresh database.
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    table_exists = cursor.fetchone() is not None

    if not table_exists:
        create_tables(conn)
        return

    cursor = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    current_version: int = cursor.fetchone()[0]

    if current_version == 0:
        create_tables(conn)
        return

    if current_version < 2:
        with conn:
            create_semantic_tables(conn)
            conn.execute("UPDATE schema_version SET version = 2")
            current_version = 2


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_schema(conn: sqlite3.Connection) -> bool:
    """Verify that all expected database objects exist.

    Checks for the three regular tables, the FTS5 virtual table, and the four
    indexes.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database connection.

    Returns
    -------
    bool
        ``True`` when every expected table and index is present.
    """
    required_tables = {"schema_version", "sessions", "steps", "steps_fts"}
    required_indexes = {
        "idx_sessions_game",
        "idx_sessions_result",
        "idx_steps_session",
        "idx_steps_action",
    }

    # Check tables (including virtual tables).
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')"
    )
    present_tables = {row[0] for row in cursor.fetchall()}

    if not required_tables.issubset(present_tables):
        return False

    # Check indexes.
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    present_indexes = {row[0] for row in cursor.fetchall()}

    if not required_indexes.issubset(present_indexes):
        return False

    return True
