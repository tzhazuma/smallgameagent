"""Tests for src.agent.harness.

No real browsers are launched — all browser interactions are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src.agent.harness import GameRunner, find_game_html, _VIEWPORT_WIDTH, _VIEWPORT_HEIGHT, _DEVICE_SCALE_FACTOR, IPHONE_USER_AGENT  # noqa: PLC2701


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def games_tmpdir(tmp_path: Path) -> Path:
    """Create a temporary directory with two sample HTML files."""
    game_a = tmp_path / "SSD_00848P01_EN_game_a.html"
    game_b = tmp_path / "SSD_00849P01_EN_game_b.html"
    game_a.write_text("<html></html>")
    game_b.write_text("<html></html>")
    return tmp_path


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Basic instance creation."""

    def test_defaults(self) -> None:
        runner = GameRunner()
        assert runner.headed is False
        assert runner.slow_mo == 0
        assert runner._browser is None

    def test_headed_true(self) -> None:
        runner = GameRunner(headed=True)
        assert runner.headed is True

    def test_custom_games_dir(self) -> None:
        runner = GameRunner(games_dir="/tmp/foo")
        assert runner.games_dir == "/tmp/foo"


# ---------------------------------------------------------------------------
# HTML path resolution
# ---------------------------------------------------------------------------


class TestFindGameHtml:
    """find_game_html() filesystem tests."""

    def test_returns_first_html_when_no_pattern(self, games_tmpdir: Path) -> None:
        result = find_game_html(games_dir=str(games_tmpdir))
        assert result.suffix == ".html"
        assert result.parent == games_tmpdir

    def test_filters_by_pattern(self, games_tmpdir: Path) -> None:
        result = find_game_html("game_b", games_dir=str(games_tmpdir))
        assert "game_b" in result.name.lower()

    def test_raises_when_no_match(self, games_tmpdir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No HTML matching"):
            find_game_html("nonexistent_game", games_dir=str(games_tmpdir))

    def test_raises_when_dir_has_no_html(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No HTML files found"):
            find_game_html(games_dir=str(tmp_path))

    def test_raises_when_dir_missing(self) -> None:
        with pytest.raises(FileNotFoundError, match="does not exist"):
            find_game_html(games_dir="/tmp/nonexistent_dir_xyz_123")


# ---------------------------------------------------------------------------
# Viewport configuration (mocked browser)
# ---------------------------------------------------------------------------


class TestViewportConfig:
    """Verify that start() configures the browser context correctly."""

    def _make_browser_mocks(self) -> dict:
        """Build the mock chain: playwright -> browser -> context -> page."""
        mock_page = mock.AsyncMock()
        mock_context = mock.AsyncMock()
        mock_browser = mock.AsyncMock()
        mock_cdp = mock.AsyncMock()

        # Wire the chain
        mock_context.new_page.return_value = mock_page
        mock_context.new_cdp_session.return_value = mock_cdp
        mock_browser.new_context.return_value = mock_context

        # This is the pw object returned by async_playwright().__aenter__()
        mock_pw_instance = mock.AsyncMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser

        # async_playwright() returns a context-manager-like object
        mock_pw = mock.MagicMock()
        mock_pw.__aenter__ = mock.AsyncMock(return_value=mock_pw_instance)

        return {
            "pw": mock_pw,
            "browser": mock_browser,
            "context": mock_context,
            "page": mock_page,
            "cdp": mock_cdp,
        }

    @pytest.mark.asyncio
    async def test_viewport_dimensions_iphone(self) -> None:
        mocks = self._make_browser_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

        # Validate the new_context call
        mocks["browser"].new_context.assert_called_once()
        _, kwargs = mocks["browser"].new_context.call_args

        vp = kwargs["viewport"]
        assert vp["width"] == _VIEWPORT_WIDTH
        assert vp["height"] == _VIEWPORT_HEIGHT

    @pytest.mark.asyncio
    async def test_device_scale_factor_3(self) -> None:
        mocks = self._make_browser_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

        _, kwargs = mocks["browser"].new_context.call_args
        assert kwargs["device_scale_factor"] == _DEVICE_SCALE_FACTOR

    @pytest.mark.asyncio
    async def test_has_touch_and_is_mobile(self) -> None:
        mocks = self._make_browser_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

        _, kwargs = mocks["browser"].new_context.call_args
        assert kwargs["has_touch"] is True
        assert kwargs["is_mobile"] is True

    @pytest.mark.asyncio
    async def test_iphone_user_agent(self) -> None:
        mocks = self._make_browser_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

        _, kwargs = mocks["browser"].new_context.call_args
        assert kwargs["user_agent"] == IPHONE_USER_AGENT

    @pytest.mark.asyncio
    async def test_headless_false_launches_headed(self) -> None:
        mocks = self._make_browser_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=True)
            await runner.start()

        mock_pw_instance = mocks["pw"].__aenter__.return_value
        mock_pw_instance.chromium.launch.assert_called_once()
        _, kwargs = mock_pw_instance.chromium.launch.call_args
        assert kwargs["headless"] is False


# ---------------------------------------------------------------------------
# CDP touch event dispatch (mocked)
# ---------------------------------------------------------------------------


class TestCDPTouchEvents:
    """Verify that joystick_pulse and tap send the correct CDP commands."""

    def _make_full_mocks(self) -> dict:
        """Full mock chain including CDP session."""
        mock_cdp = mock.AsyncMock()
        mock_cdp.send = mock.AsyncMock()

        mock_page = mock.AsyncMock()
        mock_page.wait_for_timeout = mock.AsyncMock()

        mock_context = mock.AsyncMock()
        mock_context.new_page.return_value = mock_page
        mock_context.new_cdp_session.return_value = mock_cdp

        mock_browser = mock.AsyncMock()
        mock_browser.new_context.return_value = mock_context

        # The pw instance returned by __aenter__()
        mock_pw_instance = mock.AsyncMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser

        # async_playwright() returns a context-manager-like object
        mock_pw = mock.MagicMock()
        mock_pw.__aenter__ = mock.AsyncMock(return_value=mock_pw_instance)

        return {
            "pw": mock_pw,
            "browser": mock_browser,
            "context": mock_context,
            "page": mock_page,
            "cdp": mock_cdp,
        }

    @pytest.mark.asyncio
    async def test_joystick_pulse_sends_touch_sequence(self) -> None:
        mocks = self._make_full_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

            # Joystick push straight right: dx=1, dy=0
            await runner.joystick_pulse(
                dx=1.0,
                dy=0.0,
                duration_ms=420,
                anchor=(91, 699),
                radius=50,
            )

        cdp_calls = mocks["cdp"].send.call_args_list

        # Should be exactly 3 CDP calls: touchStart, touchMove, touchEnd
        assert len(cdp_calls) == 3

        # 1st call: touchStart at anchor position
        start_args = cdp_calls[0][0]
        assert start_args[0] == "Input.dispatchTouchEvent"
        start_params = start_args[1]
        assert start_params["type"] == "touchStart"
        assert len(start_params["touchPoints"]) == 1
        tp = start_params["touchPoints"][0]
        assert tp["x"] == 91.0
        assert tp["y"] == 699.0
        assert tp["id"] == 1
        assert tp["radiusX"] == 4
        assert tp["radiusY"] == 4
        assert tp["force"] == 1

        # 2nd call: touchMove to anchor + (dx * radius, dy * radius)
        move_args = cdp_calls[1][0]
        move_params = move_args[1]
        assert move_params["type"] == "touchMove"
        tp_move = move_params["touchPoints"][0]
        assert tp_move["x"] == 91.0 + 1.0 * 50  # = 141.0
        assert tp_move["y"] == 699.0 + 0.0 * 50  # = 699.0

        # 3rd call: touchEnd with empty touchPoints
        end_args = cdp_calls[2][0]
        end_params = end_args[1]
        assert end_params["type"] == "touchEnd"
        assert end_params["touchPoints"] == []

    @pytest.mark.asyncio
    async def test_joystick_pulse_negative_direction(self) -> None:
        """Joystick push up-left: dx=-1, dy=-1."""
        mocks = self._make_full_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

            await runner.joystick_pulse(
                dx=-1.0,
                dy=-1.0,
                duration_ms=420,
                anchor=(100, 700),
                radius=50,
            )

        cdp_calls = mocks["cdp"].send.call_args_list
        assert len(cdp_calls) == 3

        # touchMove should be at (100-50, 700-50) = (50, 650)
        move_args = cdp_calls[1][0]
        move_params = move_args[1]
        tp = move_params["touchPoints"][0]
        assert tp["x"] == 50.0
        assert tp["y"] == 650.0

    @pytest.mark.asyncio
    async def test_tap_sends_start_and_end(self) -> None:
        mocks = self._make_full_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()

            await runner.tap(x=200, y=300, duration_ms=100)

        cdp_calls = mocks["cdp"].send.call_args_list

        assert len(cdp_calls) == 2

        # touchStart
        start_args = cdp_calls[0][0]
        start_params = start_args[1]
        assert start_params["type"] == "touchStart"
        tp = start_params["touchPoints"][0]
        assert tp["x"] == 200.0
        assert tp["y"] == 300.0

        # touchEnd
        end_args = cdp_calls[1][0]
        end_params = end_args[1]
        assert end_params["type"] == "touchEnd"
        assert end_params["touchPoints"] == []

    @pytest.mark.asyncio
    async def test_tap_default_duration(self) -> None:
        """tap should default to 100 ms hold duration."""
        mocks = self._make_full_mocks()

        with mock.patch(
            "src.agent.harness.async_playwright", return_value=mocks["pw"]
        ):
            runner = GameRunner(headed=False)
            await runner.start()
            await runner.tap(50, 50)

        wait_calls = [
            call for call in mocks["page"].wait_for_timeout.call_args_list
            if call[0][0] == 100
        ]
        assert len(wait_calls) == 1

    @pytest.mark.asyncio
    async def test_joystick_raises_before_start(self) -> None:
        runner = GameRunner(headed=False)
        with pytest.raises(RuntimeError, match="not started"):
            await runner.joystick_pulse(0, 1)

    @pytest.mark.asyncio
    async def test_tap_raises_before_start(self) -> None:
        runner = GameRunner(headed=False)
        with pytest.raises(RuntimeError, match="not started"):
            await runner.tap(100, 100)

    @pytest.mark.asyncio
    async def test_open_game_raises_before_start(self) -> None:
        runner = GameRunner(headed=False)
        with pytest.raises(RuntimeError, match="not started"):
            await runner.open_game("/tmp/game.html")

    @pytest.mark.asyncio
    async def test_screenshot_raises_before_start(self) -> None:
        runner = GameRunner(headed=False)
        with pytest.raises(RuntimeError, match="not started"):
            await runner.screenshot()

    @pytest.mark.asyncio
    async def test_wait_raises_before_start(self) -> None:
        runner = GameRunner(headed=False)
        with pytest.raises(RuntimeError, match="not started"):
            await runner.wait(500)
