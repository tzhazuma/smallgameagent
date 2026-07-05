"""Working memory for structured step history and stuck detection.

Provides a :class:`StepRecord` dataclass for individual step snapshots and a
:class:`WorkingMemory` container that manages history, detects stalled gameplay,
and produces prompt-friendly context strings for LLM injection.

Typical usage::

    memory = WorkingMemory(max_history=60)
    memory.push_action(state, {"action": "move", "params": {"dx": 0.5, "dy": 1.0}})
    if memory.detect_stuck((100.0, 200.0)):
        logger.warning("Agent appears stuck")
    prompt = memory.to_prompt_context(n=5)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any

from src.agent import schema

logger = logging.getLogger(__name__)


@dataclass
class StepRecord:
    """A single step snapshot holding state summary, action, and optional image hash.

    Parameters
    ----------
    timestamp:
        Monotonic clock value at creation time.
    state_summary:
        Extracted fields from the game state (ready, done, win, keyNumbers,
        keyFlags).
    action:
        The action dict dispatched by the agent.
    screenshot_hash:
        SHA-256 hex digest of the screenshot PNG bytes, or ``None`` if no
        screenshot was captured.
    """

    timestamp: float = field(default_factory=time.monotonic)
    state_summary: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    screenshot_hash: str | None = None


@dataclass
class ProceduralRule:
    """A reusable gameplay rule with condition, priority, and learning stats.

    Parameters
    ----------
    name:
        Unique identifier for the rule.
    condition:
        Condition string evaluated against game state (e.g. ``"is_stuck"``,
        ``"step_count > 5"``).
    priority:
        Priority level 0-10; higher values are preferred during matching.
    action_template:
        Action dict template dispatched when the rule matches.
    success_rate:
        Exponential-moving-average success rate (updated via
        :meth:`ProceduralMemory.update_success_rate`).
    times_applied:
        Number of times this rule has been applied.
    source:
        Origin of the rule — ``"builtin"``, ``"vlm_extracted"``,
        ``"api_generated"``, or ``"learned"``.
    game_id:
        Optional game identifier the rule is associated with.
    """

    name: str
    condition: str = ""
    priority: int = 0
    action_template: dict[str, Any] = field(
        default_factory=lambda: {"action": "wait", "params": {"duration_ms": 500}}
    )
    success_rate: float = 0.0
    times_applied: int = 0
    source: str = "builtin"
    game_id: str = ""


# ---------------------------------------------------------------------------
# State-summary extraction helpers
# ---------------------------------------------------------------------------

_STATE_KEYS = frozenset({"ready", "done", "win", "keyNumbers", "keyFlags"})


def _extract_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of only the keys relevant for prompt context."""
    return {k: state[k] for k in _STATE_KEYS if k in state}


# ---------------------------------------------------------------------------
# Working memory
# ---------------------------------------------------------------------------


class WorkingMemory:
    """In-memory ring buffer of step records with stuck detection.

    Parameters
    ----------
    max_history:
        Maximum number of :class:`StepRecord` entries kept in ``_history``.
        Older entries are dropped when new ones are pushed.
    """

    def __init__(self, max_history: int = 60) -> None:
        self._max_history = max_history
        self._history: list[StepRecord] = []
        self._last_activity: float = time.monotonic()
        self._last_player_pos: tuple[float, float] | None = None
        self._stuck_streak: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_action(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        screenshot_bytes: bytes | None = None,
    ) -> None:
        """Create a :class:`StepRecord` and append it to the history ring.

        Parameters
        ----------
        state:
            Full game state dict.  Only a subset of keys (ready, done, win,
            keyNumbers, keyFlags) are kept in the record.
        action:
            The action dict the agent chose.
        screenshot_bytes:
            Raw PNG bytes of the current screenshot, or ``None`` if no
            screenshot was taken.  When provided, a SHA-256 digest is
            computed and stored.
        """
        record = StepRecord(
            state_summary=_extract_state_summary(state),
            action=action,
            screenshot_hash=_compute_hash(screenshot_bytes)
            if screenshot_bytes is not None
            else None,
        )
        self._history.append(record)

        # Trim to max_history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        self._last_activity = time.monotonic()

    def recent_actions(self, n: int = 5) -> list[StepRecord]:
        """Return the *n* most recent step records (or all if fewer exist)."""
        if n <= 0:
            return []
        return self._history[-n:]

    def detect_stuck(self, current_pos: tuple[float, float], threshold: float = 0.05) -> bool:
        """Detect whether the agent is stuck based on repeated non-movement.

        Compares *current_pos* to the last recorded position.  If the Euclidean
        distance is below *threshold* the streak counter is incremented.  When
        the streak reaches **5** or more the method returns ``True``.

        Parameters
        ----------
        current_pos:
            The current (x, y) position of the player.
        threshold:
            Minimum Euclidean distance to count as movement.

        Returns
        -------
        bool
            ``True`` if the stuck streak is >= 5, ``False`` otherwise.
        """
        if self._last_player_pos is not None:
            dx = current_pos[0] - self._last_player_pos[0]
            dy = current_pos[1] - self._last_player_pos[1]
            distance = (dx * dx + dy * dy) ** 0.5

            if distance < threshold:
                self._stuck_streak += 1
            else:
                self._stuck_streak = 0
        else:
            # First call — no history to compare.
            pass

        self._last_player_pos = current_pos
        return self._stuck_streak >= 5

    def to_prompt_context(self, n: int = 5) -> str:
        """Format recent step records as a bullet-point string for prompt injection.

        Each bullet shows the action, state summary, and (when available) the
        first few characters of the screenshot hash.

        Parameters
        ----------
        n:
            Number of recent records to include.
        """
        records = self.recent_actions(n)
        if not records:
            return ""

        lines: list[str] = []
        for i, rec in enumerate(records, 1):
            action_str = rec.action.get("action", "unknown")
            reason = rec.action.get("reason", "")
            hash_suffix = ""
            if rec.screenshot_hash:
                hash_suffix = f" [img:{rec.screenshot_hash[:8]}]"
            lines.append(
                f"- Step: action={action_str}, state={rec.state_summary}, "
                f"reason={reason!r}{hash_suffix}"
            )
        return "\n".join(lines)

    def is_expired(self) -> bool:
        """Return ``True`` if no activity occurred in the last 300 seconds."""
        return (time.monotonic() - self._last_activity) > 300.0

    def reset(self) -> None:
        """Clear all history and reset the stuck detection counters."""
        self._history.clear()
        self._last_player_pos = None
        self._stuck_streak = 0
        self._last_activity = time.monotonic()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def step_count(self) -> int:
        """Number of recorded steps."""
        return len(self._history)

    @property
    def history(self) -> list[StepRecord]:
        """All recorded step records (read-only view)."""
        return list(self._history)

    @property
    def is_stuck(self) -> bool:
        """``True`` if the stuck streak is >= 5."""
        return self._stuck_streak >= 5

    @property
    def stuck_streak(self) -> int:
        """Current stuck streak counter."""
        return self._stuck_streak


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Episodic memory (SQLite-backed session/step persistence)
# ---------------------------------------------------------------------------


class EpisodicMemory:
    """Persistent session/step memory backed by SQLite.

    Provides long-term storage of gameplay sessions and individual steps,
    with screenshot persistence, session summarization, and automatic
    pruning of old sessions.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Parent directories are created
        automatically.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = schema.get_connection(self._db_path)
        schema.migrate(self._conn)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, game_id: str) -> str:
        """Create a new session record and return its ID.

        Parameters
        ----------
        game_id:
            Identifier for the game being played.

        Returns
        -------
        str
            16-character hex session ID.
        """
        session_id = uuid.uuid4().hex[:16]
        now = datetime.datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO sessions (id, game_id, start_time, total_steps) VALUES (?, ?, ?, 0)",
            (session_id, game_id, now),
        )
        self._conn.commit()
        return session_id

    async def record_step(
        self,
        session_id: str,
        step_number: int,
        state: dict[str, Any],
        action: dict[str, Any],
        mode: str,
        latency_ms: float,
        screenshot_bytes: bytes | None = None,
    ) -> None:
        """Persist a single gameplay step.

        Parameters
        ----------
        session_id:
            Active session identifier.
        step_number:
            1-based step index within the session.
        state:
            Full game state dict.  Serialised with ``json.dumps`` using
            ``default=str`` for non-serialisable values.
        action:
            Action dict that must include ``action`` (name), ``params``
            (parameters dict), and optionally ``reason``.
        mode:
            Driving mode that produced this step (e.g. ``"manual"``,
            ``"api"``, ``"rule"``).
        latency_ms:
            Round-trip latency in milliseconds.
        screenshot_bytes:
            Raw PNG bytes of the screenshot, or ``None``.  When provided
            the image is written to ``memory_screenshots/<session>/
            <step:04d>.png`` relative to the database directory.
        """
        step_id = uuid.uuid4().hex[:16]
        now = datetime.datetime.now(timezone.utc).isoformat()
        state_json = json.dumps(state, default=str)
        action_str: str = action.get("action", "unknown")
        params_json = json.dumps(action.get("params", {}), default=str)
        reason: str = action.get("reason", "") or ""

        screenshot_path: str | None = None
        if screenshot_bytes is not None:
            screenshot_dir = self._db_path.parent / "memory_screenshots" / session_id
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(screenshot_dir / f"{step_number:04d}.png")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            with open(screenshot_path, "wb") as f:
                f.write(screenshot_bytes)

        self._conn.execute(
            "INSERT INTO steps (id, session_id, step_number, timestamp, state_json, "
            "action, params_json, reason, mode, latency_ms, screenshot_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step_id,
                session_id,
                step_number,
                now,
                state_json,
                action_str,
                params_json,
                reason,
                mode,
                latency_ms,
                screenshot_path,
            ),
        )
        self._conn.execute(
            "UPDATE sessions SET total_steps = total_steps + 1 WHERE id = ?",
            (session_id,),
        )
        self._conn.commit()

    async def end_session(self, session_id: str, result: str, score: float = 0.0) -> None:
        """Mark a session as finished.

        Sets ``end_time``, ``result``, and ``score`` on the session
        record.  If the session has more than 10 steps an automatic
        summary is generated.

        Parameters
        ----------
        session_id:
            Session to close.
        result:
            Outcome string (e.g. ``"win"``, ``"lose"``, ``"timeout"``).
        score:
            Numeric score achieved during the session.
        """
        now = datetime.datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE sessions SET end_time = ?, result = ?, score = ? WHERE id = ?",
            (now, result, score, session_id),
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT total_steps FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is not None and row["total_steps"] > 10:
            self.summarize_session(session_id)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def get_session_steps(self, session_id: str) -> list[dict[str, Any]]:
        """Return all steps for a session ordered chronologically.

        Parameters
        ----------
        session_id:
            The session whose steps to retrieve.

        Returns
        -------
        list[dict]
            Row dicts from the ``steps`` table.
        """
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE session_id = ? ORDER BY step_number",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def find_similar(self, game_id: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Return the most recent completed sessions for a given game.

        Parameters
        ----------
        game_id:
            Game identifier to match.
        top_k:
            Maximum number of sessions to return.

        Returns
        -------
        list[dict]
            Dicts with keys ``id``, ``game_id``, ``total_steps``,
            ``result``, ``score``, ``summary``, ``end_time``.
        """
        rows = self._conn.execute(
            "SELECT id, game_id, total_steps, result, score, summary, end_time "
            "FROM sessions WHERE game_id = ? ORDER BY end_time DESC LIMIT ?",
            (game_id, top_k),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    def summarize_session(self, session_id: str, *, api_client: Any = None) -> str:
        """Build a text summary of all steps in a session.

        Reads every step for *session_id*, concatenates a human-readable
        summary (state_json truncated to 500 characters per step), and
        persists the result in ``sessions.summary``.

        Parameters
        ----------
        session_id:
            Session to summarise.
        api_client:
            Reserved for future LLM-based summarisation.

        Returns
        -------
        str
            The generated summary text.
        """
        rows = self._conn.execute(
            "SELECT step_number, state_json, action, reason FROM steps "
            "WHERE session_id = ? ORDER BY step_number",
            (session_id,),
        ).fetchall()

        summary_parts: list[str] = []
        for row in rows:
            state_text: str = row["state_json"]
            if len(state_text) > 500:
                state_text = state_text[:500] + "..."
            summary_parts.append(
                f"Step {row['step_number']}: action={row['action']}, "
                f"state={state_text}, reason={row['reason'] or ''}"
            )

        summary = "\n".join(summary_parts)
        self._conn.execute(
            "UPDATE sessions SET summary = ? WHERE id = ?",
            (summary, session_id),
        )
        self._conn.commit()
        return summary

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _prune_old_sessions(self) -> None:
        """Delete steps from sessions older than 30 days and mark them."""
        self._conn.execute(
            "DELETE FROM steps WHERE session_id IN ("
            "  SELECT id FROM sessions WHERE end_time < date('now', '-30 days')"
            ")"
        )
        self._conn.execute(
            "UPDATE sessions SET summary = '[pruned]' WHERE end_time < date('now', '-30 days')"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Semantic memory (SQLite-backed vector knowledge store)
# ---------------------------------------------------------------------------


class SemanticMemory:
    """Vector-searchable knowledge store backed by SQLite + sqlite-vec.

    Stores knowledge items with embeddings computed via a
    SentenceTransformer model.  Supports querying by game_id, collection,
    and confidence threshold.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    embedding_model:
        HuggingFace model name for SentenceTransformer (lazy-loaded).
    """

    def __init__(
        self,
        db_path: str | Path,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        from src.agent.schema import get_connection

        self._conn = get_connection(self._db_path)

        # Load sqlite-vec extension
        self._vec_available = False
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._vec_available = True
        except Exception:
            logger.warning("sqlite-vec not available; vector search disabled")

        from src.agent.schema import create_semantic_tables

        create_semantic_tables(self._conn)

        self._embedding_model_name = embedding_model
        self._model: Any = None

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _get_embedding(self, text: str) -> list[float]:
        """Compute a 384-dim embedding for *text* using the loaded model."""
        if self._model is None:
            # Lazy-load the sentence transformer model.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._embedding_model_name)
        return self._model.encode(text).tolist()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query(
        self,
        query_str: str,
        game_id: str | None = None,
        collection: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search the knowledge store by semantic similarity.

        Parameters
        ----------
        query_str:
            Natural-language query to search for.
        game_id:
            Optional filter to only return items from a specific game.
        collection:
            Optional filter to only return items from a specific
            collection.
        top_k:
            Maximum number of results to return (default 5).

        Returns
        -------
        list[dict]
            Each dict has keys ``content``, ``metadata`` (nested dict
            with game_id, collection, importance, confidence), and
            ``similarity`` (float in [0, 1]).
        """
        if not self._vec_available:
            logger.warning("Vector search unavailable; returning empty results")
            return []

        embedding = self._get_embedding(query_str)
        vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)

        # vec0 requires a literal LIMIT on the inner MATCH query.
        # Metadata filters go in the outer query after JOIN.
        vec_sql = (
            "SELECT rowid, distance"
            " FROM knowledge_vec"
            " WHERE embedding MATCH ?"
            " ORDER BY distance"
            f" LIMIT {top_k}"
        )
        sql = (
            "SELECT km.rowid, km.id, km.content, km.game_id, km.collection,"
            " km.importance, km.confidence, kv.distance"
            " FROM ("
            f"  {vec_sql}"
            ") kv"
            " JOIN knowledge_meta km ON km.rowid = kv.rowid"
            " WHERE 1=1"
        )
        params: list[Any] = [vec_bytes]

        if game_id is not None:
            sql += " AND km.game_id = ?"
            params.append(game_id)
        if collection is not None:
            sql += " AND km.collection = ?"
            params.append(collection)

        sql += " AND km.confidence >= 0.3"
        sql += " ORDER BY kv.distance"

        rows = self._conn.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "content": row["content"],
                    "metadata": {
                        "game_id": row["game_id"],
                        "collection": row["collection"],
                        "importance": row["importance"],
                        "confidence": row["confidence"],
                    },
                    "similarity": 1.0 - row["distance"],
                }
            )
        return results

    async def add_knowledge(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> str:
        """Store a knowledge item with its embedding.

        Parameters
        ----------
        content:
            The knowledge text to store.
        metadata:
            Optional dict that may contain ``game_id``, ``collection``,
            and ``confidence`` keys.
        importance:
            Numeric importance score (default 0.5).

        Returns
        -------
        str
            The generated UUID for the stored item.
        """
        md = metadata or {}
        knowledge_id = uuid.uuid4().hex
        collection = str(md.get("collection", "general"))
        game_id: str | None = md.get("game_id")
        confidence = float(md.get("confidence", 0.5))

        embedding = self._get_embedding(content)
        vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)

        cursor = self._conn.execute(
            "INSERT INTO knowledge_meta"
            " (id, collection, game_id, content, importance, confidence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (knowledge_id, collection, game_id, content, importance, confidence),
        )
        rowid = cursor.lastrowid

        if self._vec_available:
            self._conn.execute(
                "INSERT INTO knowledge_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, vec_bytes),
            )

        self._conn.commit()
        return knowledge_id

    async def extract_from_session(
        self,
        session_summary: str,
        game_id: str,
        *,
        api_client: Any = None,
    ) -> int:
        """Extract knowledge items from a session summary.

        When *api_client* is provided it is used to extract 3--5
        structured knowledge items.  Otherwise a simple bullet-point
        heuristic is applied.

        Parameters
        ----------
        session_summary:
            Text summary of a gameplay session.
        game_id:
            Game identifier to attach to extracted items.
        api_client:
            Optional ``OpenCodeGoClient`` instance for LLM-based
            extraction.

        Returns
        -------
        int
            Number of knowledge items added.
        """
        if api_client is not None:
            prompt = (
                "Extract 3-5 game knowledge items from this session"
                f" summary. Return each on a new line starting with '- ':\n\n"
                f"{session_summary}"
            )
            try:
                response = api_client.chat(messages=[{"role": "user", "content": prompt}])
                content = response.choices[0].message.content
                items = re.split(r"\n\s*[-*]\s*", content)
            except Exception:
                items = re.split(r"\n\s*[-*]\s*", session_summary)
        else:
            items = re.split(r"\n\s*[-*]\s*", session_summary)

        items = [item.strip() for item in items if item.strip()]
        count = 0
        for item in items:
            await self.add_knowledge(
                item,
                metadata={"game_id": game_id, "collection": "extracted"},
            )
            count += 1
        return count

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Procedural memory (rule storage, matching, learning)
# ---------------------------------------------------------------------------


class ProceduralMemory:
    """Rule-based memory that stores, matches, and learns procedural rules.

    Rules are persisted to a JSON file and support condition-based matching,
    success rate tracking (EMA), and automatic archiving of low-performance
    rules.

    Parameters
    ----------
    json_path:
        Path to the JSON rule file.  If the file does not exist, built-in
        seed rules are created automatically.
    """

    def __init__(self, json_path: str | Path = "procedural_rules.json") -> None:
        self._json_path = Path(json_path)
        self._rules: list[ProceduralRule] = []
        self._load_rules()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def rules(self) -> list[ProceduralRule]:
        """Return a shallow copy of all stored rules."""
        return list(self._rules)

    @property
    def rule_count(self) -> int:
        """Number of stored rules."""
        return len(self._rules)

    # ------------------------------------------------------------------
    # Rule matching
    # ------------------------------------------------------------------

    def match(self, ctx_or_state: Any, working_memory: Any = None) -> ProceduralRule | None:
        """Return the highest-priority matching rule, or ``None``.

        Parameters
        ----------
        ctx_or_state:
            Either an :class:`AgentContext` (has ``probe_state``) or a raw
            state dict.  When a context is passed, ``probe_state`` is used
            as the state dict and ``working_memory`` is extracted from the
            context if not explicitly provided.
        working_memory:
            Optional :class:`WorkingMemory` instance for evaluating
            stuck/step conditions.

        Returns
        -------
        ProceduralRule | None
        """
        state: dict[str, Any]
        if hasattr(ctx_or_state, "probe_state"):
            state = ctx_or_state.probe_state
            if working_memory is None and hasattr(ctx_or_state, "working_memory"):
                working_memory = ctx_or_state.working_memory
        else:
            state = ctx_or_state

        best: ProceduralRule | None = None
        best_priority = -1

        for rule in self._rules:
            if rule.condition and self._evaluate_condition(rule.condition, state, working_memory):
                if rule.priority > best_priority:
                    best = rule
                    best_priority = rule.priority

        return best

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def learn(self, rule: ProceduralRule) -> None:
        """Add a new rule and persist to JSON."""
        self._rules.append(rule)
        self._save()

    def update_success_rate(self, rule_name: str, succeeded: bool) -> None:
        """Update a rule's success rate using exponential moving average.

        ``new_rate = old + (1 / max(1, new_count)) * (int(succeeded) - old)``
        where ``new_count`` is ``times_applied`` **after** incrementing.
        """
        for rule in self._rules:
            if rule.name == rule_name:
                old = rule.success_rate
                rule.times_applied += 1
                n = max(1, rule.times_applied)
                rule.success_rate = old + (1.0 / n) * (int(succeeded) - old)
                self._save()
                return

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_rules(self) -> None:
        """Load rules from JSON, or seed with built-in defaults."""
        if self._json_path.exists():
            raw = json.loads(self._json_path.read_text())
            self._rules = [ProceduralRule(**item) for item in raw]
        else:
            self._rules = [
                ProceduralRule(
                    name="escape_stuck",
                    condition="is_stuck",
                    priority=8,
                    action_template={
                        "action": "move",
                        "params": {"dx": 0.5, "dy": 0.5, "duration_ms": 320},
                    },
                    source="builtin",
                ),
                ProceduralRule(
                    name="follow_guide",
                    condition="has_visual_arrow",
                    priority=6,
                    action_template={
                        "action": "move",
                        "params": {"duration_ms": 320},
                    },
                    source="builtin",
                ),
                ProceduralRule(
                    name="idle_wait",
                    condition="no_target",
                    priority=3,
                    action_template={"action": "wait", "params": {"duration_ms": 500}},
                    source="builtin",
                ),
            ]
            self._save()

    def _save(self) -> None:
        """Write all rules to the JSON file."""
        self._json_path.write_text(json.dumps([asdict(r) for r in self._rules], indent=2))

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    def _evaluate_condition(
        self, condition: str, state: dict[str, Any], working_memory: Any
    ) -> bool:
        """Evaluate a condition string against state and working memory.

        Supported conditions:
        - ``is_stuck`` -> ``working_memory.is_stuck``
        - ``has_visual_arrow`` -> state has arrow/guide indicators
        - ``no_target`` -> state has no ``guide_or_target_candidates``
        - ``stuck_streak >= N`` -> ``working_memory.stuck_streak >= N``
        - ``step_count > N`` -> ``working_memory.step_count > N``
        - ``game_id == "X"`` -> ``state["_game_id"] == X``

        Returns ``False`` for unrecognised conditions.
        """
        condition = condition.strip()

        if condition == "is_stuck":
            if working_memory is not None and hasattr(working_memory, "is_stuck"):
                return bool(working_memory.is_stuck)
            return False

        if condition == "has_visual_arrow":
            return self._has_visual_arrow(state)

        if condition == "no_target":
            return self._no_target(state)

        m = re.match(r"^stuck_streak\s*(>=?|<=?|==|!=)\s*(\d+)$", condition)
        if m:
            return self._eval_numeric_comparison(
                m.group(1), int(m.group(2)), working_memory, "stuck_streak"
            )

        m = re.match(r"^step_count\s*(>=?|<=?|==|!=)\s*(\d+)$", condition)
        if m:
            return self._eval_numeric_comparison(
                m.group(1), int(m.group(2)), working_memory, "step_count"
            )

        m = re.match(r'^game_id\s*==?\s*"([^"]*)"\s*$', condition)
        if m:
            expected = m.group(1)
            return state.get("_game_id", "") == expected

        return False

    @staticmethod
    def _eval_numeric_comparison(
        operator: str,
        value: int,
        working_memory: Any,
        attr_name: str,
    ) -> bool:
        """Evaluate a numeric comparison against a working-memory attribute."""
        if working_memory is None or not hasattr(working_memory, attr_name):
            return False
        actual = getattr(working_memory, attr_name)
        if operator == ">=":
            return actual >= value
        if operator == ">":
            return actual > value
        if operator == "<=":
            return actual <= value
        if operator == "<":
            return actual < value
        if operator == "==":
            return actual == value
        if operator == "!=":
            return actual != value
        return False

    @staticmethod
    def _has_visual_arrow(state: dict[str, Any]) -> bool:
        """Check if the state contains arrow or guide indicators."""
        candidates = state.get("guide_or_target_candidates")
        if candidates and len(candidates) > 0:
            return True
        for key, value in state.items():
            if "arrow" in key.lower():
                if value:
                    return True
        return False

    @staticmethod
    def _no_target(state: dict[str, Any]) -> bool:
        """Check if the state has no guide/target candidates."""
        candidates = state.get("guide_or_target_candidates")
        if candidates is None:
            return True
        if isinstance(candidates, (list, tuple)) and len(candidates) == 0:
            return True
        return False

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _archive_low_performance(self) -> None:
        """Move low-performance rules to an archive JSON file.

        Rules with ``success_rate < 0.2`` AND ``times_applied > 5``
        are moved to ``<filename_stem>_archive.json``.
        """
        keep: list[ProceduralRule] = []
        archive: list[ProceduralRule] = []

        for rule in self._rules:
            if rule.success_rate < 0.2 and rule.times_applied > 5:
                archive.append(rule)
            else:
                keep.append(rule)

        if not archive:
            return

        archive_path = self._json_path.with_name(
            self._json_path.stem + "_archive" + self._json_path.suffix
        )

        existing: list[dict[str, Any]] = []
        if archive_path.exists():
            existing = json.loads(archive_path.read_text())

        for rule in archive:
            existing.append(asdict(rule))

        archive_path.write_text(json.dumps(existing, indent=2))

        self._rules = keep
        self._save()
