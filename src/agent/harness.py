"""Playwright harness for opening and controlling Cocos Creator HTML5 games.

Provides :class:`GameRunner`, an async context manager that launches a
Chromium browser in an iPhone viewport, opens a local game HTML, injects
probes, captures screenshots, and dispatches CDP touch events for
joystick control.

Typical usage::

    async with GameRunner() as runner:
        await runner.start()
        await runner.open_game("/path/to/game.html")
        await runner.joystick_pulse(0, 1)           # move down
        await runner.screenshot("frame.png")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.async_api import async_playwright

if TYPE_CHECKING:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        CDPSession,
        Page,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

_VIEWPORT_WIDTH = 375
_VIEWPORT_HEIGHT = 812
_DEVICE_SCALE_FACTOR = 3

_GAMES_DIR = str(
    Path(__file__).resolve().parent.parent.parent
    / "playable-agent-12-games-20260608"
    / "playables"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class GameRunner:
    """Browser harness for loading and controlling CSS game HTMLs.

    Launches Chromium with an iPhone 12/13 viewport on `start`, navigates
    to a game HTML via :meth:`open_game`, and exposes joystick / tap
    primitives powered by CDP ``Input.dispatchTouchEvent``.

    Use as an async context manager (``async with GameRunner(...) as runner``)
    or call `start` / `close` directly.
    """

    # -- user-facing settings (set after construction) -----------------------

    headed: bool
    """When `True` the browser window is visible (default)."""

    slow_mo: int
    """Per-operation slowdown in milliseconds (0 = normal speed)."""

    # -- internals -----------------------------------------------------------

    _browser: Browser | None
    _context: BrowserContext | None
    _page: Page | None
    _cdp: CDPSession | None

    def __init__(
        self,
        headed: bool = False,
        slow_mo: int = 0,
        games_dir: str | None = None,
    ) -> None:
        """Create a new harness.

        Parameters
        ----------
        headed:
            Show the browser window (``True``) or run headless (``False``).
        slow_mo:
            Extra delay (ms) between Playwright actions.
        games_dir:
            Directory containing game HTML files.  Defaults to
            ``playable-agent-12-games-20260608/playables/`` relative to
            the repository root.
        """
        self.headed = headed
        self.slow_mo = slow_mo
        self.games_dir = games_dir or _GAMES_DIR

        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "GameRunner":
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, headed: bool | None = None) -> "GameRunner":
        """Launch the Chromium browser.

        Parameters
        ----------
        headed:
            Override the instance-level ``headed`` flag.  When ``None``
            (default) the instance's ``self.headed`` is used.
        """
        if headed is not None:
            self.headed = headed

        pw = await async_playwright().__aenter__()
        self._pw = pw  # keep a-ref for cleanup

        # Use system Chromium if available (bypass Playwright's bundled browser
        # which may not support the host OS, e.g. Ubuntu 26.04).
        import shutil
        chromium_path = os.environ.get(
            "PLAYWRIGHT_CHROMIUM_PATH",
            shutil.which("chromium-browser") or shutil.which("chromium") or None,
        )

        launch_kwargs: dict[str, Any] = {
            "headless": not self.headed,
            "slow_mo": self.slow_mo,
        }
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
            launch_kwargs.setdefault("args", []).append("--no-sandbox")

        # Extra launch flags via env, e.g. WebGL software rendering on
        # headless hosts without a GPU:
        #   PLAYWRIGHT_CHROMIUM_ARGS="--enable-unsafe-swiftshader --in-process-gpu"
        extra_args = os.environ.get("PLAYWRIGHT_CHROMIUM_ARGS", "").split()
        if extra_args:
            launch_kwargs.setdefault("args", []).extend(extra_args)

        self._browser = await pw.chromium.launch(**launch_kwargs)

        self._context = await self._browser.new_context(
            viewport={
                "width": _VIEWPORT_WIDTH,
                "height": _VIEWPORT_HEIGHT,
            },
            device_scale_factor=_DEVICE_SCALE_FACTOR,
            has_touch=True,
            is_mobile=True,
            user_agent=IPHONE_USER_AGENT,
        )

        self._page = await self._context.new_page()

        # Open a CDP session for low-level touch event dispatch.
        self._cdp = await self._context.new_cdp_session(self._page)

        return self

    async def close(self) -> None:
        """Shut down the browser and release all resources."""
        # Close CDP session first (Playwright may not like it if the page
        # is gone before the session).
        if self._cdp is not None:
            try:
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        self._page = None
        self._context = None

        # Shut down the Playwright instance.
        pw = getattr(self, "_pw", None)
        if pw is not None:
            try:
                await pw.__aexit__(None, None, None)
            except Exception:
                pass
            self._pw = None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def open_game(self, html_path: str | Path) -> None:
        """Navigate the browser to a local game HTML file.

        Parameters
        ----------
        html_path:
            Absolute or relative path to a ``.html`` file on disk.
        """
        if self._page is None:
            raise RuntimeError("GameRunner not started — call start() first")

        resolved = str(Path(html_path).resolve())
        url = Path(resolved).as_uri()
        await self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    async def screenshot(self, path: str | None = None) -> bytes:
        """Capture a full-page screenshot.

        Parameters
        ----------
        path:
            Where to write the PNG file.  If ``None`` the screenshot
            bytes are returned and no file is written.

        Returns
        -------
        bytes
            The raw PNG data.
        """
        if self._page is None:
            raise RuntimeError("GameRunner not started")
        return await self._page.screenshot(path=str(path) if path else None, full_page=True)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Touch input (CDP)
    # ------------------------------------------------------------------

    async def joystick_pulse(
        self,
        dx: float,
        dy: float,
        duration_ms: int = 420,
        anchor: tuple[int, int] = (91, 699),
        radius: int = 50,
    ) -> None:
        """Send a joystick drag via CDP touch events.

        Simulates a finger touching down at *anchor*, sliding to
        ``[anchor_x + dx * radius, anchor_y + dy * radius]``, and
        lifting after *duration_ms*.

        Parameters
        ----------
        dx:
            Horizontal component in normalized [-1, 1] space.
        dy:
            Vertical component in normalized [-1, 1] space.
        duration_ms:
            How long to hold the drag (ms).
        anchor:
            Screen coordinates (px, py) where the touch starts.
        radius:
            Maximum drag distance in screen pixels.
        """
        if self._page is None or self._cdp is None:
            raise RuntimeError("GameRunner not started — call start() first")

        target_x = anchor[0] + dx * radius
        target_y = anchor[1] + dy * radius

        touch_point = {
            "id": 1,
            "radiusX": 4,
            "radiusY": 4,
            "force": 1,
            "x": float(anchor[0]),
            "y": float(anchor[1]),
        }

        # touchStart
        await self._cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchStart", "touchPoints": [touch_point], "modifiers": 0},
        )

        # Small delay then move
        await self._page.wait_for_timeout(35)

        # touchMove
        move_point = {**touch_point, "x": float(target_x), "y": float(target_y)}
        await self._cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchMove", "touchPoints": [move_point], "modifiers": 0},
        )

        # Hold
        await self._page.wait_for_timeout(duration_ms)

        # touchEnd
        await self._cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchEnd", "touchPoints": [], "modifiers": 0},
        )

    async def tap(
        self,
        x: float,
        y: float,
        duration_ms: int = 100,
    ) -> None:
        """Dispatch a single tap at screen coordinate *(x, y)* via CDP.

        Parameters
        ----------
        x:
            Horizontal pixel coordinate.
        y:
            Vertical pixel coordinate.
        duration_ms:
            Hold duration between touchStart and touchEnd (ms).
        """
        if self._page is None or self._cdp is None:
            raise RuntimeError("GameRunner not started — call start() first")

        touch_point = {
            "id": 1,
            "radiusX": 4,
            "radiusY": 4,
            "force": 1,
            "x": float(x),
            "y": float(y),
        }

        await self._cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchStart", "touchPoints": [touch_point], "modifiers": 0},
        )
        await self._page.wait_for_timeout(duration_ms)
        await self._cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchEnd", "touchPoints": [], "modifiers": 0},
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def wait(self, ms: int) -> None:
        """Pause execution for *ms* milliseconds.

        Parameters
        ----------
        ms:
            Milliseconds to wait.
        """
        if self._page is None:
            raise RuntimeError("GameRunner not started")
        await self._page.wait_for_timeout(ms)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_game_html(
    game_pattern: str | None = None,
    games_dir: str | None = None,
) -> Path:
    """Return the first matching game HTML under *games_dir*.

    Parameters
    ----------
    game_pattern:
        A case-insensitive substring to match against file names.
        When ``None`` or ``""`` the first ``.html`` file found is returned.
    games_dir:
        Directory to scan.  Defaults to the bundled playables directory.

    Returns
    -------
    Path
        Absolute path to the matched HTML file.

    Raises
    ------
    FileNotFoundError
        If no ``.html`` file is found or no file matches *game_pattern*.
    """
    root = Path(games_dir or _GAMES_DIR)
    if not root.is_dir():
        raise FileNotFoundError(f"Games directory does not exist: {root}")

    candidates: list[Path] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith(".html"):
                candidates.append(Path(entry.path))

    if not candidates:
        raise FileNotFoundError(f"No HTML files found in {root}")

    if game_pattern:
        lowered = game_pattern.lower()
        candidates = [p for p in candidates if lowered in p.name.lower()]
        if not candidates:
            raise FileNotFoundError(
                f"No HTML matching '{game_pattern}' in {root}"
            )

    return candidates[0].resolve()
