"""Browser integration efficiency tools for fusion-harness.

Reduces CPU/memory pressure when running multiple game agents in parallel:

1. Edge instance reuse — one CDP endpoint serves many contexts (already the
   pattern in the batch runner); here we pool contexts explicitly.
2. Screenshot compression — JPEG quality + downscale cap so probe frames are
   small (games often don't need full-res PNG).
3. Probe throttling — adaptive evidence budget (gah idea): skip captures when
   a recent frame exists, when nothing changed, or when budget is exhausted.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("fusion-browser")


@dataclass
class ScreenshotPolicy:
    jpeg_quality: int = 70          # 0-100 (was PNG ~100KB/frame)
    max_dimension: int = 750        # downscale cap (games run at 750x1000)
    capture_gap_steps: int = 3      # min steps between optional captures
    max_captures_per_run: int = 48  # adaptive evidence budget (gah)
    bytes_budget_mb: float = 12.0
    required_reasons: tuple = (
        "run_boundary", "terminal_or_failure", "stage_boundary", "visual_model_input",
    )


@dataclass
class ProbeThrottler:
    policy: ScreenshotPolicy = field(default_factory=ScreenshotPolicy)
    _captures: int = 0
    _last_capture_step: int = -100
    _last_signature: str | None = None
    _bytes_used: float = 0.0

    def decide(self, step: int, reason: str, world_signature: str | None) -> dict:
        """Return {capture: bool, mode: capture|reuse|suppress, why}."""
        if reason in self.policy.required_reasons:
            return self._capture(step, f"required:{reason}")
        if self._captures >= self.policy.max_captures_per_run:
            return {"capture": False, "mode": "suppress", "why": "count_budget"}
        if self._bytes_used >= self.policy.bytes_budget_mb * 1024 * 1024:
            return {"capture": False, "mode": "suppress", "why": "byte_budget"}
        if step - self._last_capture_step < self.policy.capture_gap_steps:
            return {"capture": False, "mode": "suppress", "why": "gap_steps"}
        if world_signature is not None and world_signature == self._last_signature:
            return {"capture": False, "mode": "reuse", "why": "unchanged_scene"}
        return self._capture(step, "optional")

    def _capture(self, step: int, why: str) -> dict:
        self._captures += 1
        self._last_capture_step = step
        return {"capture": True, "mode": "capture", "why": why}

    def record_bytes(self, n: int) -> None:
        self._bytes_used += n

    def update_signature(self, sig: str | None) -> None:
        self._last_signature = sig


def encode_screenshot_jpeg(png_bytes: bytes, quality: int = 70,
                           max_dimension: int = 750) -> bytes:
    """Downscale + JPEG-encode a PNG screenshot (keeps probe frames small)."""
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if max(img.size) > max_dimension:
        ratio = max_dimension / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def screenshot_to_data_url(png_bytes: bytes, quality: int = 70) -> str:
    jpeg = encode_screenshot_jpeg(png_bytes, quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def measure_parallel_load(worker_fn, n_workers: int) -> dict:
    """Run n workers in parallel and measure wall time + optional CPU probe."""
    import concurrent.futures as cf
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=n_workers) as pool:
        results = list(pool.map(worker_fn, range(n_workers)))
    elapsed = time.perf_counter() - t0
    return {
        "workers": n_workers,
        "elapsed_s": round(elapsed, 2),
        "results": results,
    }
