"""Light-weight strategy memory for game-phase/action-pattern lookup.

Unlike the optional ``sqlite-vec`` based :class:`SemanticMemory`, this module
works with plain JSON files and simple text keys so it can run everywhere.
It stores, per ``game_id`` and ``phase_id``:

- a list of previously tried action patterns,
- success / failure counters,
- optional human-readable notes.

The memory is used by decision makers to prefer historically successful
strategies and to avoid repeating known failures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StrategyMemory:
    """File-backed strategy memory keyed by (game_id, phase_id).

    Parameters
    ----------
    store_path:
        Path to the JSON file used for persistence.
    """

    def __init__(self, store_path: str | Path = "./strategy_memory.json") -> None:
        self._path = Path(store_path)
        self._data: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        game_id: str,
        phase_id: str,
        pattern: dict[str, Any],
        success: bool,
        notes: str = "",
        session_id: str | None = None,
    ) -> None:
        """Record an attempted pattern and its outcome.

        Parameters
        ----------
        session_id:
            Optional identifier for the current play session.  Stored with the
            entry so that ``lookup`` can exclude patterns from the same session
            and avoid online self-reinforcement.
        """
        game = self._data.setdefault(game_id, {})
        entries = game.setdefault(phase_id, [])

        # Try to merge with an existing identical pattern.
        key = json.dumps(pattern, sort_keys=True, ensure_ascii=False)
        for entry in entries:
            if json.dumps(entry.get("pattern"), sort_keys=True, ensure_ascii=False) == key:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if success:
                    entry["successes"] = entry.get("successes", 0) + 1
                else:
                    entry["failures"] = entry.get("failures", 0) + 1
                if notes:
                    entry["notes"] = notes
                break
        else:
            new_entry: dict[str, Any] = {
                "pattern": pattern,
                "attempts": 1,
                "successes": 1 if success else 0,
                "failures": 0 if success else 1,
                "notes": notes,
            }
            if session_id is not None:
                new_entry["session_id"] = session_id
            entries.append(new_entry)
        self._save()

    def lookup(
        self,
        game_id: str,
        phase_id: str,
        top_k: int = 3,
        min_attempts: int = 1,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the best-known patterns for a game phase ordered by success rate.

        Parameters
        ----------
        exclude_session_id:
            If provided, entries recorded in that session are ignored.  This
            prevents the agent from reading back actions it just recorded in
            the same run, which can cause online self-reinforcement loops.
        """
        entries = self._data.get(game_id, {}).get(phase_id, [])
        scored = []
        for e in entries:
            if e.get("attempts", 0) < min_attempts:
                continue
            if exclude_session_id is not None and e.get("session_id") == exclude_session_id:
                continue
            rate = e.get("successes", 0) / max(1, e.get("attempts", 1))
            scored.append((rate, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def phase_id(self, state: dict[str, Any]) -> str:
        """Derive a simple phase id from the current state.

        Uses ``done``, ``win``, and the first key from ``keyFlags`` / ``keyNumbers``.
        """
        flags = state.get("keyFlags") or {}
        numbers = state.get("keyNumbers") or {}
        flag_key = next(iter(flags), "")
        num_key = next(iter(numbers), "")
        parts = [
            "win" if state.get("win") else ("done" if state.get("done") else "play"),
            f"F:{flag_key}" if flag_key else "",
            f"N:{num_key}" if num_key else "",
        ]
        return "_".join(p for p in parts if p) or "default"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to load strategy memory from %s", self._path, exc_info=True)
                self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save strategy memory", exc_info=True)
