"""Bridge between Python Playwright and the Cocos Creator probe JavaScript.

The probe (browser-probe-source.js) is an ESM module that exports a
template-literal string containing an IIFE.  On execution the IIFE installs
``window.__playableAgentProbe`` which exposes ``observe()``, ``observeFast()``,
``moveByCocosInput()``, etc.

This module provides a thin Python wrapper so callers never need to write raw
``page.evaluate(...)`` calls.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

DEFAULT_PROBE_PATH = Path(
    "/home/azuma/delivery/delivery/playable-agent-12-games-20260608/"
    "playable-automation/vendor/cocos-probe/browser-probe-source.js"
)

# Regex that strips the ESM export wrapper to leave the raw IIFE source.
# The file format is::
#
#   export const browserProbeSource = String.raw `
#   (function installPlayableAgentProbe() {
#     ...
#   })();`;
#
_ESM_TEMPLATE_RE = re.compile(
    r"^export\s+const\s+\w+\s*=\s*String\.raw\s*`\n(.*)`;\s*$",
    re.DOTALL,
)


def _extract_probe_source(raw: str) -> str:
    r"""Strip the ESM ``String.raw\`...\`;`` wrapper from the probe file.

    Returns the bare IIFE source ready for ``page.evaluate()`` /
    ``page.add_init_script()``.
    """
    # Fast path: drop first and last lines if they are the wrapper.
    lines = raw.split("\n")
    if len(lines) < 3:
        # Fall back to regex.
        m = _ESM_TEMPLATE_RE.match(raw)
        if m:
            return m.group(1)
        raise ValueError("Probe source file does not match expected ESM format")
    # Strip leading/trailing wrapper lines.
    first = lines[0].strip()
    if first.startswith("export const") and "String.raw" in first:
        lines = lines[1:]

    # Strip the closing template-literal suffix (``\`;\``) from the last line.
    last_idx = len(lines) - 1
    while last_idx >= 0 and not lines[last_idx].strip():
        last_idx -= 1
    if last_idx >= 0 and lines[last_idx].strip().endswith("`;"):
        lines[last_idx] = lines[last_idx].rstrip().removesuffix("`;").rstrip()
    return "\n".join(lines)


def _build_js_invoke(expr: str) -> str:
    """Build a JS expression that calls ``__playableAgentProbe.<expr>``."""
    return f"(window.__playableAgentProbe && window.__playableAgentProbe.{expr})"


_NOT_READY: dict[str, bool] = {"ready": False}


class ProbeAdapter:
    """Inject the Cocos Creator probe into a Playwright page and wrap its API.

    Parameters
    ----------
    probe_source_path:
        Path to ``browser-probe-source.js``.  Defaults to the vendored copy.

    Examples
    --------
    >>> adapter = ProbeAdapter()
    >>> await adapter.inject(page)
    >>> state = await adapter.observe(page)
    >>> print(state["ready"], state["win"])
    """

    def __init__(self, probe_source_path: str | None = None) -> None:
        path = Path(probe_source_path) if probe_source_path else DEFAULT_PROBE_PATH
        raw = path.read_text(encoding="utf-8")
        self._source = _extract_probe_source(raw)
        if not self._source.strip():
            raise ValueError(f"Empty probe source extracted from {path}")

    # -- injection ----------------------------------------------------------

    async def inject(self, page: Page) -> None:
        """Register the probe as an init script *and* run it immediately."""
        await page.add_init_script(self._source)
        await page.evaluate(self._source)

    # -- observation --------------------------------------------------------

    async def _eval(self, page: Page, expr: str) -> Any:
        """Evaluate ``window.__playableAgentProbe.<expr>``."""
        js = _build_js_invoke(expr)
        return await page.evaluate(js)

    async def observe(self, page: Page) -> dict:
        """Call ``probe.observe()`` and return the result as a Python dict."""
        try:
            result = await self._eval(page, "observe()")
            return result if isinstance(result, dict) else _NOT_READY
        except Exception:
            return _NOT_READY

    async def observe_fast(self, page: Page) -> dict:
        """Call ``probe.observeFast()`` — richer data including chains."""
        try:
            result = await self._eval(page, "observeFast()")
            return result if isinstance(result, dict) else _NOT_READY
        except Exception:
            return _NOT_READY

    async def wait_for_ready(
        self,
        page: Page,
        timeout_ms: int = 18000,
        poll_interval_ms: int = 500,
    ) -> dict:
        """Poll ``observe()`` until ``ready is True`` or *timeout_ms* elapses."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            state = await self.observe(page)
            if state.get("ready"):
                return state
            await asyncio.sleep(poll_interval_ms / 1000.0)
        return _NOT_READY

    # -- movement (bypass joystick) -----------------------------------------

    async def move_by_cocos(
        self,
        page: Page,
        dx: float,
        dz: float,
        duration_ms: int,
        options: dict | None = None,
    ) -> dict:
        """Direct Cocos ``Actor.move()`` — bypasses the joystick layer.

        Returns ``{ok, backend, elapsedMs, ...}`` or ``{ok: false, reason}``.
        """
        opts = options or {}
        js = (
            f"window.__playableAgentProbe &&"
            f" window.__playableAgentProbe.moveByCocosInput("
            f"{dx}, {dz}, {duration_ms}, {opts})"
        )
        try:
            result = await page.evaluate(js)
            return result if isinstance(result, dict) else {"ok": False}
        except Exception:
            return {"ok": False}

    # -- specialised queries ------------------------------------------------

    async def get_guide_summary(self, page: Page) -> dict:
        """Call ``probe.getGuideSummary()``."""
        try:
            result = await self._eval(page, "getGuideSummary()")
            return result if isinstance(result, dict) else _NOT_READY
        except Exception:
            return _NOT_READY

    async def get_completion_summary(self, page: Page) -> dict:
        """Call ``probe.getCompletionSummary()``."""
        try:
            result = await self._eval(page, "getCompletionSummary()")
            return result if isinstance(result, dict) else _NOT_READY
        except Exception:
            return _NOT_READY

    async def get_raw_scene_graph(
        self, page: Page, max_nodes: int = 500
    ) -> list:
        """Return the raw scene node list via ``probe.dumpScene()``."""
        try:
            result = await self._eval(page, "dumpScene()")
            if not isinstance(result, dict) or not result.get("ready"):
                return []
            nodes = result.get("nodes", [])
            return nodes[:max_nodes] if isinstance(nodes, list) else []
        except Exception:
            return []

    async def snapshot_components(self, page: Page, pattern: str) -> dict:
        """Call ``probe.snapshotComponents(pattern)``.

        Returns a dict keyed by ``componentClass@nodePath`` with
        ``{className, nodePath, booleanFields, numericFields, ...}``.
        """
        js = (
            f"window.__playableAgentProbe &&"
            f" window.__playableAgentProbe.snapshotComponents({pattern!r})"
        )
        try:
            result = await page.evaluate(js)
            if not isinstance(result, list):
                return _NOT_READY
            # Index by id for convenient lookup.
            return {item["id"]: item for item in result if isinstance(item, dict)}
        except Exception:
            return _NOT_READY
