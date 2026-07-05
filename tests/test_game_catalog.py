"""Tests for ``src.tools.game_catalog``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.game_catalog import (
    GameEntry,
    build_parser,
    generate_json,
    generate_markdown,
    scan_games,
    _extract_game_id,
    _build_name_from_md,
    _build_name_from_filename,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_html(size: int = 20000) -> str:
    """Return an HTML string guaranteed to be at least *size* bytes."""
    return "<html>" + "x" * size + "</html>"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def old_games_dir(tmp_path: Path) -> Path:
    """Create a temporary old-format games directory with flat ``.html`` files."""
    d = tmp_path / "old_games"
    d.mkdir()
    html = _make_html()
    # Two games
    (d / "SSD_00001P01_EN_Test_GameOne.html").write_text(html)
    (d / "SSD_00002P01_EN_Test_GameTwo.html").write_text(html)
    return d


@pytest.fixture
def new_games_dir(tmp_path: Path) -> Path:
    """Create a temporary new-format games directory with subdirectories."""
    d = tmp_path / "new_games"
    d.mkdir()
    html = _make_html()

    # Game with full annotations
    g1 = d / "SSD_00003P01_EN_Annotated"
    g1.mkdir()
    (g1 / "SSD_00003P01_EN_Annotated.html").write_text(html)
    (g1 / "merged.json").write_text(
        json.dumps(
            {
                "playable_id": "SSD_00003P01",
                "taxonomy_candidates": {
                    "playable_category_candidates": [
                        {"label": "arcade idle", "evidence": ["auto collect"]},
                        {"label": "farming simulation", "evidence": ["crops"]},
                    ],
                    "subgenre_candidates": ["tycoon", "resource management"],
                },
                "observed_controls": [
                    {
                        "control_id": "joystick_move",
                        "control_type": "virtual joystick",
                        "visible_or_inferred": "inferred",
                        "target": "player",
                    },
                    {
                        "control_id": "tap_action",
                        "control_type": "tap",
                        "visible_or_inferred": "visible",
                        "target": "button",
                    },
                ],
                "scene_elements": [{"id": f"el{i}"} for i in range(5)],
                "gameplay_flow": {
                    "state_machine": [
                        {"state_id": "s1", "state_name": "State 1"},
                        {"state_id": "s2", "state_name": "State 2"},
                        {"state_id": "s3", "state_name": "State 3"},
                    ],
                },
                "gameplay_task_timeline": [{"step": i, "action": f"action_{i}"} for i in range(8)],
                "resources_economy": {
                    "resources": ["gold"],
                    "currencies": ["cash"],
                },
            },
        )
    )
    (g1 / "01_SSD_00003P01_My Game Title.md").write_text(
        "# 1. SSD_00003P01 My Game Title\n\nDescription here.\n"
    )

    # Game with minimal annotations (empty arrays)
    g2 = d / "SSD_00004P01_EN_Minimal"
    g2.mkdir()
    (g2 / "SSD_00004P01_EN_Minimal.html").write_text(_make_html())
    (g2 / "merged.json").write_text(
        json.dumps(
            {
                "playable_id": "SSD_00004P01",
                "taxonomy_candidates": {},
                "observed_controls": [],
                "scene_elements": [],
                "gameplay_flow": {},
                "gameplay_task_timeline": [],
            },
        )
    )

    return d


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------


class TestGameEntryDefaults:
    """Verify ``GameEntry`` dataclass default values."""

    def test_minimal_creation(self) -> None:
        entry = GameEntry(game_id="SSD_99999P01")
        assert entry.game_id == "SSD_99999P01"
        assert entry.name == ""
        assert entry.path == ""
        assert entry.has_annotations is False
        assert entry.categories == []
        assert entry.subgenres == []
        assert entry.controls == []
        assert entry.scene_elements_count == 0
        assert entry.state_machine_states == 0
        assert entry.task_timeline_steps == 0
        assert entry.html_size_mb == 0.0

    def test_full_creation(self) -> None:
        entry = GameEntry(
            game_id="SSD_00001P01",
            name="Test Game",
            path="/some/path.html",
            has_annotations=True,
            categories=["arcade"],
            subgenres=["tycoon"],
            controls=["joystick"],
            scene_elements_count=10,
            state_machine_states=5,
            task_timeline_steps=20,
            html_size_mb=4.5,
        )
        assert entry.game_id == "SSD_00001P01"
        assert entry.html_size_mb == 4.5


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestExtractGameId:
    def test_extracts_standard_format(self) -> None:
        assert _extract_game_id("SSD_00848P01_foo.html") == "SSD_00848P01"

    def test_extracts_with_subversion(self) -> None:
        assert _extract_game_id("SSD_00522P02_bar") == "SSD_00522P02"

    def test_returns_none_for_no_match(self) -> None:
        assert _extract_game_id("no_game_id_here") is None

    def test_from_full_path(self) -> None:
        assert (
            _extract_game_id("/path/to/SSD_00342P01_EN_CSC_20251218_DLCX_Applovin_game.html")
            == "SSD_00342P01"
        )


class TestBuildNameFromMd:
    def test_parses_first_line(self, tmp_path: Path) -> None:
        d = tmp_path / "gamedir"
        d.mkdir()
        (d / "05_SSD_00342P01_some_desc.md").write_text(
            "# 5. SSD_00342P01 Snow Bear Fighting\n\nBody\n"
        )
        assert _build_name_from_md(d) == "Snow Bear Fighting"

    def test_returns_empty_when_no_md(self, tmp_path: Path) -> None:
        d = tmp_path / "emptydir"
        d.mkdir()
        assert _build_name_from_md(d) == ""

    def test_handles_malformed_md(self, tmp_path: Path) -> None:
        d = tmp_path / "gamedir2"
        d.mkdir()
        (d / "desc.md").write_text("no hash tag here\n")
        # After stripping "# " and number pattern, the content is "no hash tag here"
        result = _build_name_from_md(d)
        assert "no hash tag here" in result


class TestBuildNameFromFilename:
    def test_strips_game_id_prefix(self) -> None:
        fp = Path("SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html")
        name = _build_name_from_filename(fp)
        assert "SSD_00848P01" not in name
        assert "传送带种地" in name

    def test_handles_simple_name(self, tmp_path: Path) -> None:
        fp = tmp_path / "SSD_00001P01_SimpleGame.html"
        name = _build_name_from_filename(fp)
        assert name == "SimpleGame"


# ---------------------------------------------------------------------------
# scan_games
# ---------------------------------------------------------------------------


class TestScanGames:
    def test_both_directories(self, old_games_dir: Path, new_games_dir: Path) -> None:
        entries = scan_games(
            old_games_dir=str(old_games_dir),
            new_games_dir=str(new_games_dir),
        )
        # 2 old + 2 new = 4 unique game IDs
        assert len(entries) == 4

    def test_old_only(self, old_games_dir: Path) -> None:
        entries = scan_games(old_games_dir=str(old_games_dir), new_games_dir="")
        assert len(entries) == 2
        for e in entries:
            assert e.has_annotations is False

    def test_new_only(self, new_games_dir: Path) -> None:
        entries = scan_games(old_games_dir="", new_games_dir=str(new_games_dir))
        assert len(entries) == 2
        annotated = [e for e in entries if e.has_annotations]
        assert len(annotated) == 2  # both games have merged.json (one full, one minimal)
        assert annotated[0].game_id == "SSD_00003P01"

    def test_annotated_game_has_taxonomy(self, new_games_dir: Path) -> None:
        entries = scan_games(old_games_dir="", new_games_dir=str(new_games_dir))
        g3 = next(e for e in entries if e.game_id == "SSD_00003P01")
        assert g3.has_annotations is True
        assert "arcade idle" in g3.categories
        assert "farming simulation" in g3.categories
        assert "tycoon" in g3.subgenres
        assert "virtual joystick" in g3.controls
        assert "tap" in g3.controls
        assert g3.scene_elements_count == 5
        assert g3.state_machine_states == 3
        assert g3.task_timeline_steps == 8

    def test_minimal_annotations(self, new_games_dir: Path) -> None:
        entries = scan_games(old_games_dir="", new_games_dir=str(new_games_dir))
        g4 = next(e for e in entries if e.game_id == "SSD_00004P01")
        assert g4.has_annotations is True
        assert g4.categories == []
        assert g4.subgenres == []
        assert g4.controls == []
        assert g4.scene_elements_count == 0
        assert g4.state_machine_states == 0
        assert g4.task_timeline_steps == 0

    def test_empty_old_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_old"
        empty.mkdir()
        entries = scan_games(old_games_dir=str(empty), new_games_dir="")
        assert entries == []

    def test_empty_new_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_new"
        empty.mkdir()
        entries = scan_games(old_games_dir="", new_games_dir=str(empty))
        assert entries == []

    def test_corrupted_merged_json_is_graceful(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "corrupted_games"
        new_dir.mkdir()
        g = new_dir / "SSD_99999P01_EN_Broken"
        g.mkdir()
        (g / "SSD_99999P01_EN_Broken.html").write_text(_make_html())
        (g / "merged.json").write_text("this is not valid json {{{")
        entries = scan_games(old_games_dir="", new_games_dir=str(new_dir))
        assert len(entries) == 1
        assert entries[0].game_id == "SSD_99999P01"
        assert entries[0].has_annotations is False  # gracefully degraded

    def test_preexisting_merged_json_values(self, new_games_dir: Path) -> None:
        entries = scan_games(old_games_dir="", new_games_dir=str(new_games_dir))
        g3 = next(e for e in entries if e.game_id == "SSD_00003P01")
        assert g3.name == "My Game Title"
        assert g3.path.endswith(".html")
        assert g3.html_size_mb > 0

    def test_extract_game_id_from_json(self, new_games_dir: Path) -> None:
        entries = scan_games(old_games_dir="", new_games_dir=str(new_games_dir))
        g3 = next(e for e in entries if e.game_id == "SSD_00003P01")
        assert json.loads(generate_json([g3]))[0]["game_id"] == "SSD_00003P01"


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------


class TestGenerateJson:
    def test_valid_json_output(self) -> None:
        catalog = [
            GameEntry(game_id="SSD_00001P01", name="Foo", html_size_mb=1.2),
            GameEntry(game_id="SSD_00002P01", name="Bar", has_annotations=True),
        ]
        result = generate_json(catalog)
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["game_id"] == "SSD_00001P01"
        assert data[0]["html_size_mb"] == 1.2
        assert data[1]["has_annotations"] is True

    def test_all_fields_present(self) -> None:
        catalog = [
            GameEntry(
                game_id="SSD_00001P01",
                name="Test",
                path="/p.html",
                has_annotations=True,
                categories=["a", "b"],
                subgenres=["c"],
                controls=["tap"],
                scene_elements_count=3,
                state_machine_states=2,
                task_timeline_steps=1,
                html_size_mb=5.0,
            ),
        ]
        data = json.loads(generate_json(catalog))[0]
        assert set(data.keys()) == {
            "game_id",
            "name",
            "path",
            "has_annotations",
            "categories",
            "subgenres",
            "controls",
            "scene_elements_count",
            "state_machine_states",
            "task_timeline_steps",
            "html_size_mb",
        }


class TestGenerateMarkdown:
    def test_includes_all_games(self) -> None:
        catalog = [
            GameEntry(game_id="SSD_00001P01", name="Alpha"),
            GameEntry(game_id="SSD_00002P01", name="Beta"),
        ]
        md = generate_markdown(catalog)
        assert "SSD_00001P01" in md
        assert "SSD_00002P01" in md
        assert "**Total games:** 2" in md

    def test_grouped_by_category(self) -> None:
        catalog = [
            GameEntry(
                game_id="SSD_00001P01",
                name="Alpha",
                categories=["arcade idle"],
            ),
            GameEntry(
                game_id="SSD_00002P01",
                name="Beta",
                categories=["arcade idle", "puzzle"],
            ),
            GameEntry(
                game_id="SSD_00003P01",
                name="Gamma",
                categories=[],
            ),
        ]
        md = generate_markdown(catalog)
        assert "### arcade idle" in md
        assert "### puzzle" in md
        assert "### Uncategorized" in md

    def test_overview_table(self) -> None:
        catalog = [
            GameEntry(game_id="SSD_00001P01", name="Test", html_size_mb=3.2),
        ]
        md = generate_markdown(catalog)
        assert "| Game ID | Name | Annotations |" in md
        assert "| SSD_00001P01 | Test |" in md


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


class TestCliParser:
    def test_default_values(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.output == ""
        assert args.report == ""
        assert args.old_dir is None
        assert args.new_dir is None
        assert args.verbose is False

    def test_parses_all_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--output",
                "/tmp/cat.json",
                "--report",
                "/tmp/cat.md",
                "--old-dir",
                "/old",
                "--new-dir",
                "/new",
                "-v",
            ],
        )
        assert args.output == "/tmp/cat.json"
        assert args.report == "/tmp/cat.md"
        assert args.old_dir == "/old"
        assert args.new_dir == "/new"
        assert args.verbose is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_nonexistent_directories(self) -> None:
        """scan_games should not crash when given non-existent directories."""
        entries = scan_games(
            old_games_dir="/nonexistent/old",
            new_games_dir="/nonexistent/new",
        )
        assert entries == []

    def test_duplicate_game_id_uses_annotated(self, tmp_path: Path) -> None:
        """When the same game_id appears in both dirs, the annotated one wins."""
        old = tmp_path / "old"
        old.mkdir()
        (old / "SSD_00001P01_EN_Old.html").write_text(_make_html())

        new = tmp_path / "new"
        new.mkdir()
        g = new / "SSD_00001P01_EN_New"
        g.mkdir()
        (g / "SSD_00001P01_EN_New.html").write_text(_make_html())
        (g / "merged.json").write_text(
            json.dumps(
                {
                    "playable_id": "SSD_00001P01",
                    "taxonomy_candidates": {
                        "playable_category_candidates": [{"label": "puzzle"}],
                    },
                    "observed_controls": [],
                    "scene_elements": [],
                    "gameplay_flow": {},
                    "gameplay_task_timeline": [],
                },
            ),
        )

        entries = scan_games(str(old), str(new))
        assert len(entries) == 1
        assert entries[0].has_annotations is True
        assert entries[0].categories == ["puzzle"]

    def test_non_html_files_ignored(self, tmp_path: Path) -> None:
        old = tmp_path / "old"
        old.mkdir()
        (old / "SSD_00001P01_Game.html").write_text(_make_html())
        (old / "readme.txt").write_text("not a game")
        (old / "data.json").write_text("{}")
        entries = scan_games(old_games_dir=str(old), new_games_dir="")
        assert len(entries) == 1
        assert entries[0].game_id == "SSD_00001P01"
