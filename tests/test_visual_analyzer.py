"""Tests for src.agent.visual_analyzer.

All tests are fully offline — no real Mimo API calls are ever made.
Synthetic images are generated programmatically via PIL.
"""

from __future__ import annotations

import json
import struct
import zlib
from io import BytesIO
from unittest import mock

import pytest
from PIL import Image

from src.agent.visual_analyzer import (
    VisualAnalyzer,
    _is_cyan_pixel,
    _is_coin_pixel,
    _is_cta_blue,
    _is_lose_red,
    _is_win_green,
    _parse_json_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(width: int, height: int, pixels: list[tuple[int, int, int]] | None = None) -> bytes:
    """Return the bytes of a valid RGB PNG of *width* × *height*.

    If *pixels* is provided it must be a flat list of (r, g, b) tuples
    in row-major order.  Otherwise all pixels default to (128, 128, 128).
    """
    if pixels is None:
        pixels = [(128, 128, 128)] * (width * height)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # filter byte = None
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw_rows.append(r)
            raw_rows.append(g)
            raw_rows.append(b)

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        full = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(full) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + full + crc

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = chunk(b"IHDR", ihdr_data)
    idat = chunk(b"IDAT", zlib.compress(bytes(raw_rows)))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _make_cyan_image() -> bytes:
    """Create a 100×100 PNG with a 20×20 cyan square at top-left."""
    pixels = [(0, 0, 0)] * (100 * 100)
    for y in range(10, 30):
        for x in range(10, 30):
            pixels[y * 100 + x] = (80, 120, 200)  # cyan: low R, high G/B
    return _make_png(100, 100, pixels)


def _make_end_card_image(style: str) -> bytes:
    """Create a 200×200 PNG dominated by a win/lose/cta colour.

    ``style``: ``"win"`` (green), ``"lose"`` (red), or ``"cta"`` (blue).
    """
    colours = {
        "win": (30, 180, 30),
        "lose": (200, 30, 30),
        "cta": (30, 60, 200),
    }
    c = colours[style]
    pixels = [c] * (200 * 200)
    return _make_png(200, 200, pixels)


def _make_obstacle_image() -> bytes:
    """A 100×100 dark image with a black obstacle block."""
    pixels = [(255, 255, 255)] * (100 * 100)
    for y in range(30, 60):
        for x in range(30, 60):
            pixels[y * 100 + x] = (10, 10, 10)
    return _make_png(100, 100, pixels)


def _make_coin_image() -> bytes:
    """A 200×200 PNG with two distinct gold coin regions."""
    pixels = [(0, 0, 0)] * (200 * 200)
    # Coin 1: 15×15 block
    for y in range(20, 35):
        for x in range(20, 35):
            pixels[y * 200 + x] = (220, 180, 30)
    # Coin 2: 15×15 block
    for y in range(100, 115):
        for x in range(100, 115):
            pixels[y * 200 + x] = (210, 170, 25)
    return _make_png(200, 200, pixels)


def _make_mock_client() -> mock.MagicMock:
    """Return a mock OpenCodeGoClient with chat_with_vision wired up."""
    client = mock.MagicMock()
    client.chat_with_vision = mock.MagicMock()
    client.encode_image_base64 = mock.MagicMock(return_value="data:image/png;base64,abc")
    return client


def _mock_response(content: str) -> mock.MagicMock:
    """Build a mock OpenAI response object whose ``message.content`` is *content*."""
    msg = mock.MagicMock()
    msg.content = content
    choice = mock.MagicMock()
    choice.message = msg
    resp = mock.MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Tests — colour classification helpers
# ---------------------------------------------------------------------------


class TestColourClassification:
    """Low-level pixel classifier tests."""

    def test_is_cyan_pixel_match(self) -> None:
        """A pure cyan-blue pixel matches the guide-arrow threshold."""
        assert _is_cyan_pixel(r=80, g=120, b=200) is True

    def test_is_cyan_pixel_no_match_red(self) -> None:
        assert _is_cyan_pixel(r=200, g=120, b=200) is False  # R too high

    def test_is_cyan_pixel_no_match_low_blue(self) -> None:
        assert _is_cyan_pixel(r=80, g=120, b=100) is False  # B too low

    def test_is_win_green_match(self) -> None:
        assert _is_win_green(r=50, g=180, b=30) is True

    def test_is_win_green_no_match_low_g(self) -> None:
        assert _is_win_green(r=50, g=100, b=30) is False

    def test_is_lose_red_match(self) -> None:
        assert _is_lose_red(r=200, g=30, b=40) is True

    def test_is_lose_red_no_match_high_g(self) -> None:
        assert _is_lose_red(r=200, g=100, b=40) is False

    def test_is_cta_blue_match(self) -> None:
        assert _is_cta_blue(r=30, g=60, b=200) is True

    def test_is_cta_blue_no_match_high_r(self) -> None:
        assert _is_cta_blue(r=150, g=60, b=200) is False

    def test_is_coin_pixel_match(self) -> None:
        assert _is_coin_pixel(r=220, g=170, b=30) is True

    def test_is_coin_pixel_no_match_high_b(self) -> None:
        assert _is_coin_pixel(r=220, g=170, b=100) is False


# ---------------------------------------------------------------------------
# Tests — JSON response parsing
# ---------------------------------------------------------------------------


class TestJSONParsing:
    """Verify _parse_json_response handles various LLM output styles."""

    def test_clean_json(self) -> None:
        raw = json.dumps({"guides": [{"x": 200, "y": 500, "confidence": 0.9, "type": "arrow"}]})
        result = _parse_json_response(raw)
        assert len(result["guides"]) == 1
        assert result["guides"][0]["x"] == 200

    def test_fenced_json(self) -> None:
        raw = '```json\n{"guides": [{"x": 300, "y": 400, "confidence": 0.8, "type": "arrow"}]}\n```'
        result = _parse_json_response(raw)
        assert len(result["guides"]) == 1
        assert result["guides"][0]["x"] == 300

    def test_json_with_commentary(self) -> None:
        raw = 'Sure! Here is the JSON:\n{"guides": []}\nHope that helps!'
        result = _parse_json_response(raw)
        assert result["guides"] == []

    def test_invalid_json_returns_skeleton(self) -> None:
        raw = "I cannot analyze this image because it is blank."
        result = _parse_json_response(raw)
        assert result["guides"] == []
        assert result["end_state"] is None
        assert result["player_indicators"]["cargo"] is None

    def test_empty_string(self) -> None:
        result = _parse_json_response("")
        assert result["guides"] == []


# ---------------------------------------------------------------------------
# Tests — VisualAnalyzer constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    """Constructor behaviour."""

    def test_with_api_client(self) -> None:
        client = _make_mock_client()
        va = VisualAnalyzer(client)
        assert va._api_client is client
        assert va._fallback_mode == "pil"
        assert va._cache_ttl == 5.0

    def test_without_api_client(self) -> None:
        va = VisualAnalyzer(api_client=None)
        assert va._api_client is None

    def test_custom_cache_ttl(self) -> None:
        va = VisualAnalyzer(api_client=None, cache_ttl=10.0)
        assert va._cache_ttl == 10.0

    def test_custom_fallback_mode(self) -> None:
        va = VisualAnalyzer(api_client=None, fallback_mode="pil")
        assert va._fallback_mode == "pil"


# ---------------------------------------------------------------------------
# Tests — Mimo response → structured output
# ---------------------------------------------------------------------------


class TestMimoResponseToStructured:
    """Mock Mimo-v2.5 responses and check structured output."""

    @pytest.mark.asyncio
    async def test_parses_guide_arrows(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [
                    {"x": 200, "y": 500, "confidence": 0.9, "type": "arrow"},
                    {"x": 210, "y": 510, "confidence": 0.7, "type": "arrow"},
                ],
                "end_state": None,
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert len(result["guides"]) == 2
        assert result["guides"][0] == {"x": 200, "y": 500, "confidence": 0.9, "type": "arrow"}
        assert result["guides"][1]["confidence"] == 0.7
        assert "raw_response" in result

    @pytest.mark.asyncio
    async def test_parses_end_card_win(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": {"type": "win", "confidence": 0.95},
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["end_state"] is not None
        assert result["end_state"]["type"] == "win"
        assert result["end_state"]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_parses_end_card_lose(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": {"type": "lose", "confidence": 0.88},
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["end_state"]["type"] == "lose"
        assert result["end_state"]["confidence"] == 0.88

    @pytest.mark.asyncio
    async def test_parses_obstacles(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": None,
                "obstacles": [
                    {"x": 100, "y": 300, "width": 50, "height": 50},
                    {"x": 200, "y": 400, "width": 60, "height": 80},
                ],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert len(result["obstacles"]) == 2
        assert result["obstacles"][0]["width"] == 50
        assert result["obstacles"][1]["height"] == 80

    @pytest.mark.asyncio
    async def test_parses_ui_elements(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": None,
                "obstacles": [],
                "ui_elements": [
                    {"label": "Play Now", "x": 375, "y": 1000, "type": "button"},
                    {"label": "Skip", "x": 100, "y": 50, "type": "button"},
                ],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert len(result["ui_elements"]) == 2
        assert result["ui_elements"][0]["label"] == "Play Now"

    @pytest.mark.asyncio
    async def test_parses_player_indicators(self, tmp_path) -> None:
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": None,
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": "money", "cargo_count": 3},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["player_indicators"]["cargo"] == "money"
        assert result["player_indicators"]["cargo_count"] == 3

    @pytest.mark.asyncio
    async def test_fenced_json_response(self, tmp_path) -> None:
        """LLM returns JSON inside markdown fences — should still parse."""
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            '```json\n{"guides": [{"x": 100,"y": 200,"confidence": 0.5,"type": "arrow"}],'
            '"end_state": null,"obstacles": [],"ui_elements": [],'
            '"player_indicators": {"cargo": null, "cargo_count": null}}\n```'
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert len(result["guides"]) == 1
        assert result["guides"][0]["x"] == 100

    @pytest.mark.asyncio
    async def test_raw_response_preserved(self, tmp_path) -> None:
        """The raw LLM output is always included in the result."""
        client = _make_mock_client()
        raw_json = json.dumps({
            "guides": [{"x": 50, "y": 60, "confidence": 0.5, "type": "arrow"}],
            "end_state": None,
            "obstacles": [],
            "ui_elements": [],
            "player_indicators": {"cargo": None, "cargo_count": None},
        })
        client.chat_with_vision.return_value = _mock_response(raw_json)

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["raw_response"] == raw_json


# ---------------------------------------------------------------------------
# Tests — fallback (PIL colour-thresholding)
# ---------------------------------------------------------------------------


class TestFallbackMode:
    """Verify the PIL fallback produces meaningful output."""

    @pytest.mark.asyncio
    async def test_fallback_produces_basic_output(self, tmp_path) -> None:
        """Fallback on a simple image always returns the standard schema."""
        png = tmp_path / "blank.png"
        png.write_bytes(_make_png(100, 100))

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert "guides" in result
        assert "end_state" in result
        assert "obstacles" in result
        assert "ui_elements" in result
        assert "player_indicators" in result
        assert result["raw_response"] == "fallback:pil"

    @pytest.mark.asyncio
    async def test_fallback_detects_cyan_arrows(self, tmp_path) -> None:
        """Fallback finds the cyan guide arrow in a synthetic image."""
        png = tmp_path / "cyan.png"
        png.write_bytes(_make_cyan_image())

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert len(result["guides"]) >= 1, f"Expected at least 1 guide, got {result['guides']}"
        guide = result["guides"][0]
        # The 20×20 cyan square is at (10,10)–(29,29) → centroid ≈ (19, 19)
        assert 15 <= guide["x"] <= 25
        assert 15 <= guide["y"] <= 25
        assert guide["confidence"] > 0.5
        assert guide["type"] == "arrow"

    @pytest.mark.asyncio
    async def test_fallback_detects_end_card_win(self, tmp_path) -> None:
        """All-green image → fallback detects a win end card."""
        png = tmp_path / "win.png"
        png.write_bytes(_make_end_card_image("win"))

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert result["end_state"] is not None
        assert result["end_state"]["type"] == "win"
        assert result["end_state"]["confidence"] > 0.5

    @pytest.mark.asyncio
    async def test_fallback_detects_end_card_lose(self, tmp_path) -> None:
        png = tmp_path / "lose.png"
        png.write_bytes(_make_end_card_image("lose"))

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert result["end_state"] is not None
        assert result["end_state"]["type"] == "lose"

    @pytest.mark.asyncio
    async def test_fallback_detects_end_card_cta(self, tmp_path) -> None:
        png = tmp_path / "cta.png"
        png.write_bytes(_make_end_card_image("cta"))

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert result["end_state"] is not None
        assert result["end_state"]["type"] == "cta"

    @pytest.mark.asyncio
    async def test_fallback_detects_obstacles(self, tmp_path) -> None:
        png = tmp_path / "obstacle.png"
        png.write_bytes(_make_obstacle_image())

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert len(result["obstacles"]) >= 1, f"Expected at least 1 obstacle, got {result['obstacles']}"

    @pytest.mark.asyncio
    async def test_fallback_detects_coins(self, tmp_path) -> None:
        png = tmp_path / "coins.png"
        png.write_bytes(_make_coin_image())

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert result["player_indicators"]["cargo"] == "money"
        assert result["player_indicators"]["cargo_count"] is not None
        assert result["player_indicators"]["cargo_count"] >= 1


# ---------------------------------------------------------------------------
# Tests — caching
# ---------------------------------------------------------------------------


class TestCaching:
    """Hash-based caching of identical screenshots."""

    @pytest.mark.asyncio
    async def test_same_hash_returns_cached_response(self, tmp_path) -> None:
        """Two calls with the same screenshot within TTL return the same object."""
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [{"x": 42, "y": 99, "confidence": 1.0, "type": "arrow"}],
                "end_state": None,
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "cache.png"
        png.write_bytes(_make_png(5, 5))

        va = VisualAnalyzer(client, cache_ttl=10.0)
        result1 = await va.analyze(png)
        result2 = await va.analyze(png)

        # Same object identity (not just equality) — returned from cache.
        assert result1 is result2
        # API should only be called once.
        assert client.chat_with_vision.call_count == 1

    @pytest.mark.asyncio
    async def test_different_image_bypasses_cache(self, tmp_path) -> None:
        """Two different images → two API calls, different results."""
        client = _make_mock_client()
        client.chat_with_vision.side_effect = [
            _mock_response(json.dumps({
                "guides": [{"x": 1, "y": 1, "confidence": 0.5, "type": "arrow"}],
                "end_state": None, "obstacles": [], "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })),
            _mock_response(json.dumps({
                "guides": [{"x": 2, "y": 2, "confidence": 0.5, "type": "arrow"}],
                "end_state": None, "obstacles": [], "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })),
        ]

        png_a = tmp_path / "a.png"
        png_b = tmp_path / "b.png"
        png_a.write_bytes(_make_png(5, 5, [(255, 0, 0)] * 25))
        png_b.write_bytes(_make_png(5, 5, [(0, 255, 0)] * 25))

        va = VisualAnalyzer(client, cache_ttl=10.0)
        r1 = await va.analyze(png_a)
        r2 = await va.analyze(png_b)

        assert r1["guides"][0]["x"] == 1
        assert r2["guides"][0]["x"] == 2
        assert client.chat_with_vision.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_expiry(self, tmp_path) -> None:
        """After TTL expires the image is re-analysed."""
        client = _make_mock_client()
        client.chat_with_vision.side_effect = [
            _mock_response(json.dumps({
                "guides": [{"x": 10, "y": 10, "confidence": 0.5, "type": "arrow"}],
                "end_state": None, "obstacles": [], "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })),
            _mock_response(json.dumps({
                "guides": [{"x": 20, "y": 20, "confidence": 0.5, "type": "arrow"}],
                "end_state": None, "obstacles": [], "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })),
        ]

        png = tmp_path / "expire.png"
        png.write_bytes(_make_png(5, 5))

        va = VisualAnalyzer(client, cache_ttl=0.0)  # immediate expiry
        r1 = await va.analyze(png)
        r2 = await va.analyze(png)

        assert r1 is not r2
        assert client.chat_with_vision.call_count == 2


# ---------------------------------------------------------------------------
# Tests — API unavailable → fallback
# ---------------------------------------------------------------------------


class TestAPIFallback:
    """When the API call fails, the PIL fallback is invoked."""

    @pytest.mark.asyncio
    async def test_fallback_on_api_error(self, tmp_path) -> None:
        """API raises → fallback produces valid output."""
        client = _make_mock_client()
        client.chat_with_vision.side_effect = RuntimeError("connection refused")

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(100, 100))

        va = VisualAnalyzer(client, fallback_mode="pil")
        result = await va.analyze(png)

        assert "guides" in result
        assert "end_state" in result
        assert result["raw_response"] == "fallback:pil"

    @pytest.mark.asyncio
    async def test_no_fallback_mode_raises(self, tmp_path) -> None:
        """When fallback_mode is not 'pil', API errors propagate."""
        client = _make_mock_client()
        client.chat_with_vision.side_effect = RuntimeError("connection refused")

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(100, 100))

        va = VisualAnalyzer(client, fallback_mode="none")
        with pytest.raises(RuntimeError, match="connection refused"):
            await va.analyze(png)

    @pytest.mark.asyncio
    async def test_no_client_always_fallback(self, tmp_path) -> None:
        """When no API client is provided, fallback is always used."""
        png = tmp_path / "test.png"
        png.write_bytes(_make_png(100, 100))

        va = VisualAnalyzer(api_client=None)
        result = await va.analyze(png)

        assert result["raw_response"] == "fallback:pil"
        assert "guides" in result

    @pytest.mark.asyncio
    async def test_api_retry_then_fallback(self, tmp_path) -> None:
        """API client retries then falls back after exhaustion."""
        client = _make_mock_client()
        # Simulate the retry loop in api_client — it tries 4 times (1 + 3 retries).
        # Our mock just raises immediately so only 1 attempt.
        client.chat_with_vision.side_effect = RuntimeError("429 rate limited")

        png = tmp_path / "test.png"
        png.write_bytes(_make_cyan_image())

        va = VisualAnalyzer(client, fallback_mode="pil")
        result = await va.analyze(png)

        # Fallback on a cyan image should still detect the arrow.
        assert len(result["guides"]) >= 1
        assert result["raw_response"] == "fallback:pil"


# ---------------------------------------------------------------------------
# Tests — misc
# ---------------------------------------------------------------------------


class TestMisc:
    """Cover edge cases."""

    @pytest.mark.asyncio
    async def test_normalise_missing_fields(self, tmp_path) -> None:
        """API returns partial JSON — missing fields get defaults."""
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({"guides": [{"x": 5}]})  # missing y, confidence, type
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["guides"][0]["x"] == 5
        assert result["guides"][0]["y"] == 0
        assert result["guides"][0]["confidence"] == 0.5
        assert result["guides"][0]["type"] == "arrow"

    @pytest.mark.asyncio
    async def test_null_end_state_stays_none(self, tmp_path) -> None:
        """Explicit null end_state is preserved as None."""
        client = _make_mock_client()
        client.chat_with_vision.return_value = _mock_response(
            json.dumps({
                "guides": [],
                "end_state": None,
                "obstacles": [],
                "ui_elements": [],
                "player_indicators": {"cargo": None, "cargo_count": None},
            })
        )

        png = tmp_path / "test.png"
        png.write_bytes(_make_png(10, 10))

        va = VisualAnalyzer(client)
        result = await va.analyze(png)

        assert result["end_state"] is None


# ---------------------------------------------------------------------------
# Tests — analyze_pil (synchronous local analysis for rule mode)
# ---------------------------------------------------------------------------


def _make_cyan_pil_image() -> Image.Image:
    """A 100×100 RGB image with a 20×20 cyan block at top-left (centroid ~19,19)."""
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    for y in range(10, 30):
        for x in range(10, 30):
            img.putpixel((x, y), (80, 120, 200))
    return img


def _minimal_probe_state() -> dict:
    """Minimal observe_fast()-style state for RuleEngine.step()."""
    return {
        "ready": True,
        "player": {"worldPosition": {"x": 0.0, "z": 0.0}},
        "guide_or_target_candidates": [],
        "guideSummary": {},
    }


class TestAnalyzePil:
    """VisualAnalyzer.analyze_pil — sync, offline, rules.py-compatible schema."""

    def test_returns_rules_consumed_keys(self) -> None:
        result = VisualAnalyzer(api_client=None).analyze_pil(_make_cyan_pil_image())
        assert set(result) == {"stick", "arrow"}

    def test_stick_schema_and_range(self) -> None:
        result = VisualAnalyzer(api_client=None).analyze_pil(_make_cyan_pil_image())
        stick = result["stick"]
        assert isinstance(stick, dict)
        assert isinstance(stick["dx"], float)
        assert isinstance(stick["dy"], float)
        assert -1.0 <= stick["dx"] <= 1.0
        assert -1.0 <= stick["dy"] <= 1.0

    def test_arrow_schema(self) -> None:
        result = VisualAnalyzer(api_client=None).analyze_pil(_make_cyan_pil_image())
        arrow = result["arrow"]
        assert isinstance(arrow, dict)
        assert isinstance(arrow["x"], int)
        assert isinstance(arrow["y"], int)
        assert 0.0 <= arrow["confidence"] <= 1.0

    def test_cyan_arrow_position_and_stick_direction(self) -> None:
        """Cyan block at (10..29, 10..29) → centroid ≈ (19, 19), up-left of centre."""
        result = VisualAnalyzer(api_client=None).analyze_pil(_make_cyan_pil_image())
        assert abs(result["arrow"]["x"] - 19) <= 1
        assert abs(result["arrow"]["y"] - 19) <= 1
        assert result["stick"]["dx"] < 0  # left of screen centre
        assert result["stick"]["dy"] < 0  # above screen centre

    def test_no_cyan_returns_none_stick_and_arrow(self) -> None:
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        result = VisualAnalyzer(api_client=None).analyze_pil(img)
        assert result == {"stick": None, "arrow": None}

    def test_never_calls_api(self) -> None:
        client = _make_mock_client()
        va = VisualAnalyzer(client)
        result = va.analyze_pil(_make_cyan_pil_image())
        assert result["stick"] is not None
        client.chat_with_vision.assert_not_called()
        client.encode_image_base64.assert_not_called()

    def test_result_feeds_rule_engine(self) -> None:
        """The returned visual dict is consumed by RuleEngine.step() as-is."""
        from src.engine.rules import RuleEngine

        engine = RuleEngine("SSD_00848P01")  # follow-guide-audited profile
        visual = VisualAnalyzer(api_client=None).analyze_pil(_make_cyan_pil_image())
        action = engine.step(_minimal_probe_state(), visual)
        # A visible guide arrow → not a completion state, engine follows it.
        assert action["action"] == "move"
        assert action["reason"].startswith("follow_guide_target_dist")


class TestRuleDecisionMakerVisualPath:
    """Regression: rule mode must call sync analyze_pil, not async analyze."""

    async def test_decide_uses_sync_visual_analysis(self) -> None:
        from src.agent.context import AgentContext
        from src.agent.decision_makers.rule_maker import RuleDecisionMaker
        from src.engine.rules import RuleEngine

        buf = BytesIO()
        _make_cyan_pil_image().save(buf, format="PNG")

        maker = RuleDecisionMaker(
            rule_engine=RuleEngine("SSD_00848P01"),
            visual_analyzer=VisualAnalyzer(api_client=None),
        )
        ctx = AgentContext(screenshot=buf.getvalue(), probe_state=_minimal_probe_state())
        result = await maker.decide(ctx)
        assert result["action"] == "move"
        assert result["reason"].startswith("follow_guide_target_dist")
