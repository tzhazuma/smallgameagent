"""Mimo-v2.5 powered visual analysis for game screenshots.

Provides :class:`VisualAnalyzer`, an async analyser that sends game
screenshots to the Mimo-v2.5 vision model (via :class:`OpenCodeGoClient`)
and returns structured guidance — guide arrows, end cards, obstacles,
and UI elements.

When the API is unavailable the analyser falls back to embedded PIL
colour-thresholding that re-implements the core cyan-guide detection
logic without external dependencies.

Typical usage::

    from src.agent.api_client import OpenCodeGoClient
    from src.agent.visual_analyzer import VisualAnalyzer

    client = OpenCodeGoClient(api_key="sk-...")
    va = VisualAnalyzer(client)
    result = await va.analyze("screenshot.png")
    print(result["guides"])
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    from .api_client import OpenCodeGoClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIMO_SYSTEM_PROMPT = (
    "You are a game screenshot analyzer. Analyze the screenshot of a mobile "
    "HTML5 game and return only valid JSON — no markdown, no explanation, "
    "no code fences. The JSON must follow the schema exactly."
)

_MIMO_USER_PROMPT = """What do you see in this game screenshot? Identify:
(1) blue/cyan guide arrows and their screen positions (center x,y),
(2) any end-game cards — win, lose, or call-to-action (CTA) screens,
(3) obstacles blocking the player's path — give position and approximate size,
(4) UI buttons and their labels/positions,
(5) any player indicators (cargo items, coins, score displays).

Return a single JSON object with these keys:
- "guides": list of {"x": int, "y": int, "confidence": float, "type": "arrow"}
- "end_state": null or {"type": "win"|"lose"|"cta", "confidence": float}
- "obstacles": list of {"x": int, "y": int, "width": int, "height": int}
- "ui_elements": list of {"label": str, "x": int, "y": int, "type": "button"|"text"}
- "player_indicators": {"cargo": str|null, "cargo_count": int|null}

Output only the JSON object, nothing else."""

# Colour threshold for cyan guide arrows (from strategy_audit.md §5a).
# b >= 135, g >= 105, r <= 130, (b-r) >= 50, (g-r) >= 15
_CYAN_B_MIN = 135
_CYAN_G_MIN = 105
_CYAN_R_MAX = 130
_CYAN_BR_DIFF_MIN = 50
_CYAN_GR_DIFF_MIN = 15

# Exclusion zone around the player avatar (avoids the cyan body/ring).
_EXCLUSION_RADIUS = 82

# End-card colour thresholds.
_END_WIN_GREEN_G_MIN = 140
_END_WIN_GREEN_B_MAX = 80
_END_LOSE_RED_R_MIN = 180
_END_LOSE_RED_G_MAX = 60
_END_CTA_BLUE_B_MIN = 160
_END_CTA_BLUE_R_MAX = 80

# Coin / gold detection (station coin cues).
_COIN_R_MIN = 180
_COIN_G_MIN = 150
_COIN_B_MAX = 50

# Minimum connected-component size (pixels) to count as a feature.
_MIN_COMPONENT_SIZE = 80

# Parsing fallback: tolerate markdown fences in LLM output.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class VisualAnalyzer:
    """Analyze game screenshots using Mimo-v2.5 with a PIL fallback.

    Parameters
    ----------
    api_client:
        An :class:`OpenCodeGoClient` instance for the Mimo-v2.5 API.
        When ``None`` every call uses the PIL fallback.
    fallback_mode:
        When ``"pil"`` (default) the analyser automatically degrades to
        PIL colour-thresholding if the API call fails.
    cache_ttl:
        Hash-based cache TTL in seconds.  Identical screenshots within
        this window return the cached result.  Default 5 s.
    """

    _cache: dict[str, tuple[float, dict[str, Any]]]

    def __init__(
        self,
        api_client: OpenCodeGoClient | None,
        fallback_mode: str = "pil",
        cache_ttl: float = 5.0,
    ) -> None:
        self._api_client = api_client
        self._fallback_mode = fallback_mode
        self._cache_ttl = cache_ttl
        self._cache = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def analyze(self, screenshot_path: str | Path) -> dict[str, Any]:
        """Analyze *screenshot_path* and return structured visual guidance.

        Returns a dict with keys ``guides``, ``end_state``, ``obstacles``,
        ``ui_elements``, ``player_indicators``, and ``raw_response``.

        Parameters
        ----------
        screenshot_path:
            Path to a PNG screenshot (typically 375×812 or 750×1334).
        """
        path = Path(screenshot_path)
        img_bytes = path.read_bytes()

        # --- cache check ---
        file_hash = _sha256(img_bytes)
        cached = self._cache.get(file_hash)
        if cached is not None:
            ts, result = cached
            if time.monotonic() - ts <= self._cache_ttl:
                return result

        # --- API path ---
        if self._api_client is not None:
            try:
                result = await self._analyze_via_api(path)
                self._cache[file_hash] = (time.monotonic(), result)
                return result
            except Exception:
                if self._fallback_mode != "pil":
                    raise

        # --- fallback path ---
        result = self._analyze_fallback(path)
        self._cache[file_hash] = (time.monotonic(), result)
        return result

    # ------------------------------------------------------------------
    # API analysis
    # ------------------------------------------------------------------

    async def _analyze_via_api(self, path: Path) -> dict[str, Any]:
        """Send the image to Mimo-v2.5 and parse the JSON response."""
        client = self._api_client
        assert client is not None  # guaranteed by caller

        image_uri = client.encode_image_base64(path)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _MIMO_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _MIMO_USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_uri}},
                ],
            },
        ]

        response = client.chat_with_vision(
            messages=messages,
            model="mimo-v2.5",
            max_tokens=1024,
        )

        raw = response.choices[0].message.content or ""
        parsed = _parse_json_response(raw)
        # Merge parsed with the raw text so callers can inspect both.
        parsed["raw_response"] = raw
        return parsed

    # ------------------------------------------------------------------
    # PIL fallback analysis
    # ------------------------------------------------------------------

    def _analyze_fallback(self, path: Path) -> dict[str, Any]:
        """Run PIL colour-thresholding on the screenshot.

        Re-implements the core cyan-guide detection logic originally
        found in ``detect-cyan-guide.py`` (strategy_audit.md §5a).
        """
        img = Image.open(path).convert("RGB")
        pixels = img.load()  # type: ignore[attr-defined]
        w, h = img.size

        # –– 1. Detect cyan guide arrows ––––––––––––––––––––––––––––––––
        cyan_mask = _make_bool_array(w, h)
        for y in range(h):
            for x in range(w):
                r, g, b = pixels[x, y]
                if _is_cyan_pixel(r, g, b):
                    cyan_mask[y][x] = True

        components = _find_connected_components(cyan_mask, w, h, _MIN_COMPONENT_SIZE)
        guides: list[dict[str, Any]] = []
        for comp in sorted(components, key=len, reverse=True):
            if not comp:
                continue
            avg_x = sum(p[0] for p in comp) // len(comp)
            avg_y = sum(p[1] for p in comp) // len(comp)
            # Confidence scales with component size (capped at 400 px).
            confidence = min(len(comp) / 400.0, 1.0)
            guides.append(
                {"x": avg_x, "y": avg_y, "confidence": round(confidence, 2), "type": "arrow"}
            )

        # –– 2. Detect end cards –––––––––––––––––––––––––––––––––––––––––
        green_count = 0
        red_count = 0
        blue_count = 0
        total = 0
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                r, g, b = pixels[x, y]
                total += 1
                if _is_win_green(r, g, b):
                    green_count += 1
                if _is_lose_red(r, g, b):
                    red_count += 1
                if _is_cta_blue(r, g, b):
                    blue_count += 1

        end_state: dict[str, Any] | None = None
        threshold = 0.08  # 8 % of sampled pixels
        if total > 0:
            if green_count / total > threshold:
                end_state = {"type": "win", "confidence": min(green_count / total * 4, 1.0)}
            elif red_count / total > threshold:
                end_state = {"type": "lose", "confidence": min(red_count / total * 4, 1.0)}
            elif blue_count / total > threshold:
                end_state = {"type": "cta", "confidence": min(blue_count / total * 4, 1.0)}

        # –– 3. Detect obstacles (dark regions) –––––––––––––––––––––––––––
        obstacles: list[dict[str, Any]] = []
        dark_mask = _make_bool_array(w, h)
        for y in range(h):
            for x in range(w):
                r, g, b = pixels[x, y]
                if r < 50 and g < 50 and b < 50:
                    dark_mask[y][x] = True

        dark_comps = _find_connected_components(dark_mask, w, h, _MIN_COMPONENT_SIZE)
        for comp in dark_comps[:5]:
            xs = [p[0] for p in comp]
            ys = [p[1] for p in comp]
            obstacles.append(
                {
                    "x": min(xs),
                    "y": min(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                }
            )

        # –– 4. Detect UI buttons (large bright rectangles) ––––––––––––––
        ui_elements: list[dict[str, Any]] = []
        bright_mask = _make_bool_array(w, h)
        for y in range(h):
            for x in range(w):
                r, g, b = pixels[x, y]
                if r > 200 and g > 200 and b > 200:
                    bright_mask[y][x] = True

        bright_comps = _find_connected_components(bright_mask, w, h, _MIN_COMPONENT_SIZE * 3)
        for comp in bright_comps[:3]:
            xs = [p[0] for p in comp]
            ys = [p[1] for p in comp]
            cx = sum(xs) // len(xs)
            cy = sum(ys) // len(ys)
            ui_elements.append({"label": "button", "x": cx, "y": cy, "type": "button"})

        # –– 5. Detect player indicators (coins / gold) ––––––––––––––––––
        coin_mask = _make_bool_array(w, h)
        for y in range(h):
            for x in range(w):
                r, g, b = pixels[x, y]
                if _is_coin_pixel(r, g, b):
                    coin_mask[y][x] = True

        coin_comps = _find_connected_components(coin_mask, w, h, _MIN_COMPONENT_SIZE // 2)
        player_indicators: dict[str, Any] = {"cargo": None, "cargo_count": None}
        if coin_comps:
            player_indicators = {
                "cargo": "money",
                "cargo_count": len(coin_comps),
            }

        return {
            "guides": guides,
            "end_state": end_state,
            "obstacles": obstacles,
            "ui_elements": ui_elements,
            "player_indicators": player_indicators,
            "raw_response": "fallback:pil",
        }


# ---------------------------------------------------------------------------
# Helpers — colour classification
# ---------------------------------------------------------------------------


def _is_cyan_pixel(r: int, g: int, b: int) -> bool:
    """Return True if (r,g,b) matches the cyan guide-arrow threshold."""
    return (
        b >= _CYAN_B_MIN
        and g >= _CYAN_G_MIN
        and r <= _CYAN_R_MAX
        and (b - r) >= _CYAN_BR_DIFF_MIN
        and (g - r) >= _CYAN_GR_DIFF_MIN
    )


def _is_win_green(r: int, g: int, b: int) -> bool:
    return g >= _END_WIN_GREEN_G_MIN and b <= _END_WIN_GREEN_B_MAX


def _is_lose_red(r: int, g: int, b: int) -> bool:
    return r >= _END_LOSE_RED_R_MIN and g <= _END_LOSE_RED_G_MAX


def _is_cta_blue(r: int, g: int, b: int) -> bool:
    return b >= _END_CTA_BLUE_B_MIN and r <= _END_CTA_BLUE_R_MAX


def _is_coin_pixel(r: int, g: int, b: int) -> bool:
    return r >= _COIN_R_MIN and g >= _COIN_G_MIN and b <= _COIN_B_MAX


# ---------------------------------------------------------------------------
# Helpers — image processing
# ---------------------------------------------------------------------------


def _make_bool_array(w: int, h: int) -> list[list[bool]]:
    """Create a 2-D boolean list of size *h* × *w* initialised to False."""
    return [[False] * w for _ in range(h)]


def _find_connected_components(
    mask: list[list[bool]],
    w: int,
    h: int,
    min_size: int = 1,
) -> list[list[tuple[int, int]]]:
    """Flood-fill connected components from *mask*.

    Returns a list of connected-component pixel lists sorted by
    decreasing size.  Components smaller than *min_size* are discarded.
    """
    visited = [[False] * w for _ in range(h)]
    components: list[list[tuple[int, int]]] = []

    for y in range(h):
        for x in range(w):
            if not mask[y][x] or visited[y][x]:
                continue
            # Flood-fill this component.
            comp: list[tuple[int, int]] = []
            stack = [(y, x)]
            visited[y][x] = True
            while stack:
                cy, cx = stack.pop()
                comp.append((cx, cy))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny][nx] and not visited[ny][nx]:
                        visited[ny][nx] = True
                        stack.append((ny, nx))
            if len(comp) >= min_size:
                components.append(comp)

    return sorted(components, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Helpers — JSON parsing
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Extract a JSON object from potentially-fenced LLM output.

    Tolerates leading/trailing markdown triple-backtick fences and
    surrounding commentary text.
    """
    # Try direct parse first.
    candidates = [_extract_json(raw)]
    if not candidates[0]:
        # Strip markdown fences.
        m = _JSON_FENCE_RE.search(raw)
        if m:
            candidates.append(_extract_json(m.group(1)))
    # Try finding the outermost {}.
    brace_open = raw.find("{")
    brace_close = raw.rfind("}")
    if brace_open >= 0 and brace_close > brace_open:
        candidates.append(_extract_json(raw[brace_open : brace_close + 1]))

    for cand in candidates:
        if cand:
            return _normalise_result(cand)

    # Total failure: return skeleton with raw response.
    return {
        "guides": [],
        "end_state": None,
        "obstacles": [],
        "ui_elements": [],
        "player_indicators": {"cargo": None, "cargo_count": None},
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """Attempt to parse *text* as JSON.  Returns None on failure."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def _normalise_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce the parsed dict to conform to the expected schema."""
    return {
        "guides": [
            {
                "x": int(g.get("x", 0)),
                "y": int(g.get("y", 0)),
                "confidence": float(g.get("confidence", 0.5)),
                "type": str(g.get("type", "arrow")),
            }
            for g in raw.get("guides", [])
            if isinstance(g, dict)
        ],
        "end_state": _normalise_end_state(raw.get("end_state")),
        "obstacles": [
            {
                "x": int(o.get("x", 0)),
                "y": int(o.get("y", 0)),
                "width": int(o.get("width", 0)),
                "height": int(o.get("height", 0)),
            }
            for o in raw.get("obstacles", [])
            if isinstance(o, dict)
        ],
        "ui_elements": [
            {
                "label": str(ue.get("label", "")),
                "x": int(ue.get("x", 0)),
                "y": int(ue.get("y", 0)),
                "type": str(ue.get("type", "button")),
            }
            for ue in raw.get("ui_elements", [])
            if isinstance(ue, dict)
        ],
        "player_indicators": _normalise_indicators(raw.get("player_indicators")),
    }


def _normalise_end_state(val: Any) -> dict[str, Any] | None:
    if val is None or not isinstance(val, dict):
        return None
    t = val.get("type")
    if t not in ("win", "lose", "cta"):
        return None
    conf = float(val.get("confidence", 0.5))
    return {"type": t, "confidence": round(min(max(conf, 0.0), 1.0), 2)}


def _normalise_indicators(val: Any) -> dict[str, Any]:
    if val is None or not isinstance(val, dict):
        return {"cargo": None, "cargo_count": None}
    cargo = val.get("cargo")
    count = val.get("cargo_count")
    return {
        "cargo": str(cargo) if cargo is not None else None,
        "cargo_count": int(count) if count is not None else None,
    }


# ---------------------------------------------------------------------------
# Helpers — hashing
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    """Return the hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()
