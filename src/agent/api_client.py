"""OpenAI-compatible API client for OpenCodeGo API.

Provides access to DeepSeek-v4-flash, Mimo-v2.5, and other models
through the OpenCodeGo inference endpoint with retry logic and image encoding.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


class OpenCodeGoClient:
    """OpenAI-compatible client targeting the OpenCodeGo API.

    Supports text chat (deepseek-v4-flash, deepseek-v4-pro) and
    vision chat (mimo-v2.5, mimo-v2.5-pro) with automatic retry on
    transient errors.

    Usage::

        client = OpenCodeGoClient()
        response = client.chat([{"role": "user", "content": "Hello"}])
        print(response.choices[0].message.content)
    """

    DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
    MAX_RETRIES = 3
    RETRY_STATUSES = {429, 503}
    BASE_DELAY = 1.0  # seconds — doubled on each retry

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("OPENCODE_API_KEY", "")
        if not api_key:
            raise ValueError(
                "API key is required. Provide it explicitly or set "
                "the OPENCODE_API_KEY environment variable."
            )

        self._api_key = api_key
        self._base_url = base_url or self.DEFAULT_BASE_URL
        self._client = OpenAI(api_key=api_key, base_url=self._base_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "deepseek-v4-flash",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Any:
        """Send a text-only chat completion request.

        Returns the raw OpenAI SDK response object so callers can
        access ``.choices[0].message.content`` and other fields.

        Parameters
        ----------
        messages:
            List of message dicts conforming to the OpenAI chat format,
            e.g. ``[{"role": "user", "content": "..."}]``.
        model:
            Model name. Defaults to ``deepseek-v4-flash``.
        max_tokens:
            Maximum tokens in the completion.
        temperature:
            Sampling temperature. 0.0 = deterministic.
        """
        return self._with_retry(
            self._client.chat.completions.create,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        model: str = "mimo-v2.5",
        max_tokens: int = 1024,
    ) -> Any:
        """Send a multimodal (vision) chat completion request.

        Messages should include ``image_url`` content parts following
        the OpenAI vision format.  Use :meth:`encode_image_base64` to
        convert local image files into data URIs.

        Parameters
        ----------
        messages:
            List of message dicts with text and/or image_url content.
        model:
            Vision-capable model. Defaults to ``mimo-v2.5``.
        max_tokens:
            Maximum tokens in the completion.
        """
        return self._with_retry(
            self._client.chat.completions.create,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )

    @staticmethod
    def encode_image_base64(image_path: str | Path) -> str:
        """Read an image file and return a ``data:image/...;base64,...`` URI.

        Parameters
        ----------
        image_path:
            Path to a local image file (PNG, JPEG, GIF, WebP).

        Returns
        -------
        str
            A data URI suitable for use as an ``image_url`` in
            OpenAI vision-compatible messages.
        """
        path = Path(image_path)
        suffix = path.suffix.lower().lstrip(".")
        # Normalise common extensions to MIME-friendly names.
        mime_map = {"jpg": "jpeg", "svg": "svg+xml"}
        mime_type = mime_map.get(suffix, suffix)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/{mime_type};base64,{encoded}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _with_retry(self, callable, **kwargs: Any) -> Any:
        """Call *callable* with exponential-backoff retry on 429 / 503."""
        last_exc: Exception | None = None

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                return callable(**kwargs)
            except Exception as exc:
                last_exc = exc
                status = self._extract_status(exc)
                if status not in self.RETRY_STATUSES or attempt >= self.MAX_RETRIES:
                    raise
                delay = self.BASE_DELAY * (2**attempt)
                time.sleep(delay)

        # Should be unreachable — the last failed attempt raises above.
        raise last_exc  # type: ignore[misc]  # pragma: no cover

    @staticmethod
    def _extract_status(exc: Exception) -> int | None:
        """Best-effort extraction of HTTP status code from an exception."""
        if hasattr(exc, "status_code"):
            return exc.status_code  # type: ignore[no-any-return]
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code  # type: ignore[no-any-return]
        return None
