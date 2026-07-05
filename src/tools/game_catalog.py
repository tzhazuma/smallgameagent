"""Game catalog CLI tool.

Scans game directories, parses merged.json annotations
where available, and produces JSON / Markdown reports.

Usage
-----
    python -m src.tools.game_catalog --output catalog.json --report catalog.md

Environment variables
---------------------
GAMES_OLD_DIR : str
    Path to the directory containing the original 12 flat HTML games.
GAMES_NEW_DIR : str
    Path to the directory containing the 22 annotated game subdirectories.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

_GAME_ID_RE = re.compile(r"(SSD_\d+P\d+)")

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class GameEntry:
    """Describes a single HTML5 playable game.

    Attributes
    ----------
    game_id:
        Unique playable identifier, e.g. ``"SSD_00848P01"``.
    name:
        Human-readable name extracted from the file / directory / annotation.
    path:
        Absolute path to the ``.html`` game file.
    has_annotations:
        ``True`` when a ``merged.json`` annotation file was found alongside the game.
    categories:
        Category labels from ``taxonomy_candidates.playable_category_candidates``.
    subgenres:
        Sub-genre labels from ``taxonomy_candidates.subgenre_candidates``.
    controls:
        Observed control types (e.g. ``"virtual joystick"``).
    scene_elements_count:
        Number of entries in ``scene_elements``.
    state_machine_states:
        Number of states in ``gameplay_flow.state_machine``.
    task_timeline_steps:
        Number of entries in ``gameplay_task_timeline``.
    html_size_mb:
        Size of the HTML file in megabytes.
    """

    game_id: str
    name: str = ""
    path: str = ""
    has_annotations: bool = False
    categories: list[str] = field(default_factory=list)
    subgenres: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    scene_elements_count: int = 0
    state_machine_states: int = 0
    task_timeline_steps: int = 0
    html_size_mb: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_new_games_dir(override: str | None = None) -> Path:
    """Return the path to the new (annotated) games directory.

    Precedence: override > env var > project-relative default.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("GAMES_NEW_DIR")
    if env:
        return Path(env)
    return _PROJECT_ROOT / "_extracted" / "games"


def _resolve_old_games_dir(override: str | None = None) -> Path:
    """Return the path to the old (flat HTML) games directory.

    Precedence: override > env var.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("GAMES_OLD_DIR")
    if env:
        return Path(env)
    # Attempt a reasonable default relative to project root.
    candidate = (
        _PROJECT_ROOT.parent
        / "delivery"
        / "delivery"
        / "playable-agent-12-games-20260608"
        / "playables"
    )
    if candidate.is_dir():
        return candidate
    return candidate  # will fail later with a clear error


def _extract_game_id(text: str) -> str | None:
    """Extract the first ``SSD_XXXXX`` pattern from *text*."""
    m = _GAME_ID_RE.search(text)
    return m.group(1) if m else None


def _parse_merged_json(path: Path) -> dict[str, Any]:
    """Load and return a ``merged.json`` file, or raise."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_name_from_md(dir_path: Path) -> str:
    """Try to extract a human-readable name from a description ``.md`` file."""
    for child in dir_path.iterdir():
        if child.suffix == ".md":
            try:
                with open(child, encoding="utf-8") as f:
                    line = f.readline().strip()
                # Lines look like: "# 12. SSD_00219P01 收割小麦 / 养牛卖奶"
                # Remove leading "# " and any numbering / game_id prefix.
                name = line.lstrip("# ").strip()
                # Strip leading number + dot (e.g. "12. ")
                name = re.sub(r"^\d+\.\s*", "", name)
                # Strip game_id if present
                gid = _extract_game_id(name)
                if gid:
                    name = name.replace(gid, "").strip()
                if name:
                    return name
            except OSError:
                continue
    return ""


def _build_name_from_filename(html_path: Path) -> str:
    """Extract a descriptive name from an old-format HTML filename.

    The filename looks like::
        SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html

    We strip the extension and the ``SSD_XXXXX`` prefix.
    """
    stem = html_path.stem  # without .html
    gid = _extract_game_id(stem)
    if gid:
        # Remove game_id prefix and leading underscore/space
        name = stem.replace(gid, "", 1).lstrip("_ ")
        return name
    return stem


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_games(
    old_games_dir: str | Path | None = None,
    new_games_dir: str | Path | None = None,
) -> list[GameEntry]:
    """Scan both game directories and return a consolidated list of entries.

    Parameters
    ----------
    old_games_dir:
        Path to the flat-HTML games directory (the original 12 games).
    new_games_dir:
        Path to the annotated games directory (22 subdirectory-based games).

    Returns
    -------
    list[GameEntry]
        One entry per discovered game, sorted by ``game_id``.
    """
    entries: list[GameEntry] = []

    # ---- Old games: flat .html files in a single directory -----------------
    old_dir = _resolve_old_games_dir(old_games_dir)
    if old_dir.is_dir():
        with os.scandir(old_dir) as it:
            for entry in it:
                if not entry.is_file() or not entry.name.lower().endswith(".html"):
                    continue
                fp = Path(entry.path)
                game_id = _extract_game_id(entry.name) or ""
                if not game_id:
                    continue
                name = _build_name_from_filename(fp)
                html_size_mb = round(fp.stat().st_size / (1024 * 1024), 2)
                entries.append(
                    GameEntry(
                        game_id=game_id,
                        name=name,
                        path=str(fp.resolve()),
                        html_size_mb=html_size_mb,
                    )
                )
    else:
        logger.warning("Old games directory not found: %s", old_dir)

    # ---- New games: subdirectories each containing .html + merged.json ------
    new_dir = _resolve_new_games_dir(new_games_dir)
    if new_dir.is_dir():
        with os.scandir(new_dir) as it:
            for subdir in it:
                if not subdir.is_dir():
                    continue
                sub_path = Path(subdir.path)
                html_files = list(sub_path.glob("*.html"))
                if not html_files:
                    continue
                html_fp = html_files[0]
                game_id = _extract_game_id(subdir.name) or ""
                if not game_id:
                    continue
                html_size_mb = round(html_fp.stat().st_size / (1024 * 1024), 2)
                name = _build_name_from_md(sub_path)
                if not name:
                    name = _build_name_from_filename(html_fp)

                entry = GameEntry(
                    game_id=game_id,
                    name=name,
                    path=str(html_fp.resolve()),
                    html_size_mb=html_size_mb,
                )

                # Parse merged.json if present
                mj_path = sub_path / "merged.json"
                if mj_path.is_file():
                    try:
                        data = _parse_merged_json(mj_path)
                        entry.has_annotations = True
                        _populate_from_merged(entry, data, sub_path)
                    except (json.JSONDecodeError, OSError) as exc:
                        logger.warning("Failed to parse %s: %s", mj_path, exc)

                entries.append(entry)
    else:
        logger.warning("New games directory not found: %s", new_dir)

    # Deduplicate by game_id (new games take precedence)
    seen: dict[str, GameEntry] = {}
    for e in entries:
        if e.game_id in seen:
            existing = seen[e.game_id]
            # New-format entry (has_annotations) should take precedence
            if e.has_annotations and not existing.has_annotations:
                seen[e.game_id] = e
            # Keep the first seen otherwise
        else:
            seen[e.game_id] = e

    return sorted(seen.values(), key=lambda x: x.game_id)


def _populate_from_merged(
    entry: GameEntry,
    data: dict[str, Any],
    sub_path: Path,  # noqa: ARG001
) -> None:
    """Fill *entry* fields from a parsed ``merged.json`` dict."""
    taxonomy = data.get("taxonomy_candidates") or {}
    entry.categories = [
        c["label"]
        for c in taxonomy.get("playable_category_candidates") or []
        if isinstance(c, dict) and "label" in c
    ]
    entry.subgenres = list(taxonomy.get("subgenre_candidates") or [])

    controls_raw = data.get("observed_controls") or []
    entry.controls = sorted(
        {
            c.get("control_type", "")
            for c in controls_raw
            if isinstance(c, dict) and c.get("control_type")
        }
    )

    scene_els = data.get("scene_elements") or []
    entry.scene_elements_count = len(scene_els)

    gameplay_flow = data.get("gameplay_flow") or {}
    sm = gameplay_flow.get("state_machine") or []
    entry.state_machine_states = len(sm)

    timeline = data.get("gameplay_task_timeline") or []
    entry.task_timeline_steps = len(timeline)


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------


def generate_json(catalog: list[GameEntry]) -> str:
    """Return a pretty-printed JSON string for *catalog*."""
    return json.dumps(
        [asdict(e) for e in catalog],
        indent=2,
        ensure_ascii=False,
    )


def generate_markdown(catalog: list[GameEntry]) -> str:
    """Return a Markdown report with an overview table and per-category sections."""
    lines: list[str] = [
        "# Game Catalog",
        "",
        f"**Total games:** {len(catalog)}",
        "",
        "---",
        "",
        "## Overview",
        "",
        "| # | Game ID | Name | Annotations | Category | Subgenres | Controls | Scenes | States | Steps | Size (MB) |",
        "|---|---------|------|-------------|----------|-----------|----------|--------|--------|-------|-----------|",
    ]

    for idx, g in enumerate(catalog, start=1):
        cat_str = ", ".join(g.categories) if g.categories else "—"
        sub_str = ", ".join(g.subgenres) if g.subgenres else "—"
        ctrl_str = ", ".join(g.controls) if g.controls else "—"
        lines.append(
            f"| {idx} | {g.game_id} | {g.name} | "
            f"{'✓' if g.has_annotations else '✗'} | {cat_str} | {sub_str} | "
            f"{ctrl_str} | {g.scene_elements_count} | "
            f"{g.state_machine_states} | {g.task_timeline_steps} | {g.html_size_mb} |"
        )

    lines.extend(["", "---", "", "## By Category", ""])

    # Collect all unique categories
    cat_groups: dict[str, list[GameEntry]] = {}
    for g in catalog:
        cats = g.categories if g.categories else ["Uncategorized"]
        for cat in cats:
            cat_groups.setdefault(cat, []).append(g)

    for cat_name in sorted(cat_groups):
        games = cat_groups[cat_name]
        lines.extend(
            [
                f"### {cat_name}",
                "",
                f"**{len(games)} game(s)**",
                "",
                "| Game ID | Name | Annotations | Subgenres | Controls |",
                "|---------|------|-------------|-----------|----------|",
            ]
        )
        for g in games:
            sub_str = ", ".join(g.subgenres) if g.subgenres else "—"
            ctrl_str = ", ".join(g.controls) if g.controls else "—"
            lines.append(
                f"| {g.game_id} | {g.name} | {'✓' if g.has_annotations else '✗'} | "
                f"{sub_str} | {ctrl_str} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by game_catalog.py — {len(catalog)} games total*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Enumerate and annotate HTML5 playable games.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to write JSON catalog (default: stdout).",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="Path to write Markdown report (default: stdout).",
    )
    parser.add_argument(
        "--old-dir",
        type=str,
        default=None,
        help="Override the old-format games directory.",
    )
    parser.add_argument(
        "--new-dir",
        type=str,
        default=None,
        help="Override the new-format (annotated) games directory.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    catalog = scan_games(
        old_games_dir=args.old_dir,
        new_games_dir=args.new_dir,
    )

    # --- JSON output ---
    json_str = generate_json(catalog)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"JSON catalog written to {args.output}")
    else:
        print(json_str)

    # --- Markdown output ---
    md_str = generate_markdown(catalog)
    if args.report:
        Path(args.report).write_text(md_str, encoding="utf-8")
        print(f"Markdown report written to {args.report}")
    else:
        print(md_str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
