"""Tests for browser integration efficiency tools."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion.browser_eff import (  # noqa: E402
    ProbeThrottler, ScreenshotPolicy, encode_screenshot_jpeg,
    measure_parallel_load, screenshot_to_data_url,
)


def test_throttler_required_always_captures() -> None:
    t = ProbeThrottler()
    assert t.decide(1, "run_boundary", None)["mode"] == "capture"
    assert t.decide(2, "terminal_or_failure", None)["mode"] == "capture"


def test_throttler_reuse_unchanged_scene() -> None:
    t = ProbeThrottler()
    t.decide(1, "observation", "sig-a")
    t.update_signature("sig-a")
    d = t.decide(10, "observation", "sig-a")  # past gap window, scene unchanged
    assert d["mode"] == "reuse" and d["capture"] is False and d["why"] == "unchanged_scene"


def test_throttler_gap_and_budget() -> None:
    t = ProbeThrottler()
    t.decide(1, "observation", "s1")
    assert t.decide(2, "observation", "s2")["why"] == "gap_steps"
    t2 = ProbeThrottler(ScreenshotPolicy(max_captures_per_run=2))
    t2.decide(1, "observation", "s1")
    t2.decide(5, "observation", "s2")
    assert t2.decide(9, "observation", "s3")["why"] == "count_budget"


def test_jpeg_compression() -> None:
    from PIL import Image
    import io
    img = Image.new("RGB", (1500, 2000), (100, 120, 140))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    jpeg = encode_screenshot_jpeg(png, quality=70, max_dimension=750)
    assert len(jpeg) < len(png)
    assert jpeg[:2] == b"\xff\xd8"  # JPEG magic
    assert screenshot_to_data_url(png).startswith("data:image/jpeg;base64,")


def test_parallel_load() -> None:
    import time

    def worker(i):
        time.sleep(0.05)
        return i

    r = measure_parallel_load(worker, 4)
    assert len(r["results"]) == 4
    assert r["elapsed_s"] < 0.5  # threaded, not serial (would be ~0.2s)
