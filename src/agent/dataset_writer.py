"""Auto-persist game dataset steps to JSONL files on disk.

Each game session writes a separate ``{game_id}_{timestamp}.jsonl`` file
under the configured output directory.  Lines are flushed immediately so
partial runs are never lost.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class DatasetWriter:
    """Write per-step (state, screenshot, decision) records to a JSONL file.

    Parameters
    ----------
    output_dir:
        Directory where JSONL session files are created.  Defaults to
        ``./collected_datasets``.
    """

    def __init__(self, output_dir: str | Path = "./collected_datasets") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._file_handle: Any = None  # the open ``TextIOWrapper``
        self._game_id: str = ""
        self._session_path: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, game_id: str) -> None:
        """Open a new JSONL file for *game_id*.

        The file is created as ``{output_dir}/{game_id}_{YYYYMMDD_HHMMSS}.jsonl``.
        Any previously open session is **not** closed automatically — call
        :meth:`end_session` first if needed.

        Parameters
        ----------
        game_id:
            Unique identifier for the game (typically the HTML filename stem).
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{game_id}_{timestamp}.jsonl"
        self._session_path = self._output_dir / filename
        self._file_handle = self._session_path.open("w", encoding="utf-8")
        self._game_id = game_id

    def write_step(
        self,
        state: dict[str, Any],
        screenshot_path: str | None,
        decision: dict[str, Any],
    ) -> None:
        """Write one JSON line for a single agent step.

        The line includes ``game_id``, ``timestamp``, ``state``,
        ``screenshot_rel`` (relative path from the output directory), and
        ``decision``.  The file is flushed immediately after writing.

        Parameters
        ----------
        state:
            The game state dict from the probe.
        screenshot_path:
            Absolute path to the step screenshot (or ``None``).
        decision:
            The action decision dict from the agent.
        """
        if self._file_handle is None:
            return  # no active session, silently ignore

        screenshot_rel = ""
        if screenshot_path:
            try:
                screenshot_rel = os.path.relpath(screenshot_path, self._output_dir)
            except ValueError:
                screenshot_rel = screenshot_path

        record: dict[str, Any] = {
            "game_id": self._game_id,
            "timestamp": time.time(),
            "state": state,
            "screenshot_rel": screenshot_rel,
            "decision": decision,
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        self._file_handle.write(line + "\n")
        self._file_handle.flush()

    def end_session(self) -> Path | None:
        """Close the current session file and return its path.

        Returns ``None`` if no session is active.
        """
        if self._file_handle is None:
            return None
        try:
            self._file_handle.close()
        except Exception:
            pass
        self._file_handle = None
        result = self._session_path
        self._session_path = None
        self._game_id = ""
        return result

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_path(self) -> Path | None:
        """Path of the currently open session file, or ``None``."""
        return self._session_path

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> DatasetWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.end_session()
