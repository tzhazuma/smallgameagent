"""OpenAI-compatible API client for OpenCodeGo API.

Provides access to DeepSeek-v4-flash, Mimo-v2.5, and other models
through the OpenCodeGo inference endpoint with retry logic and image encoding.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


#: Default provider-specific settings.  ``api_key`` and ``base_url`` can be
#: overridden via environment variables following the pattern ``{NAME}_API_KEY``
#: and ``{NAME}_BASE_URL``.  Model names follow ``{NAME}_TEXT_MODEL`` and
#: ``{NAME}_VISION_MODEL``.
PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "opencodego": {
        "base_url": "https://opencode.ai/zen/go/v1",
        "text_model": "deepseek-v4-flash",
        "vision_model": "mimo-v2.5",
    },
    "kimi": {
        "base_url": "https://api.kimi.com/coding/v1",
        "text_model": "kimi-k2.7-code",
        "vision_model": "kimi-k2.6",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "text_model": "deepseek-chat",
        "vision_model": "deepseek-chat",
    },
    "xiaomi": {
        "base_url": "https://api.xiaomimimo.com/v1",
        "text_model": "mimo-v2.5",
        "vision_model": "mimo-v2.5",
    },
    "qwen": {
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "text_model": "qwen3.7-max",
        "vision_model": "qwen3.7-max",
    },
}


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

    DEFAULT_BASE_URL = PROVIDER_CONFIGS["opencodego"]["base_url"]
    DEFAULT_TEXT_MODEL = PROVIDER_CONFIGS["opencodego"]["text_model"]
    DEFAULT_VISION_MODEL = PROVIDER_CONFIGS["opencodego"]["vision_model"]
    AUTH_FILE = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    MAX_RETRIES = 3
    RETRY_STATUSES = {429, 503}
    BASE_DELAY = 1.0  # seconds — doubled on each retry
    #: The Console Go proxy answers 400 "Upstream request failed" when an
    #: explicit ``temperature`` is sent for these models — omit the parameter.
    _NO_TEMPERATURE_PREFIXES = ("kimi",)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        text_model: str | None = None,
        vision_model: str | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("OPENCODEGO_API_KEY", "")
        if not api_key:
            api_key = os.environ.get("OPENCODE_API_KEY", "")
        if not api_key:
            api_key = self._read_auth_file_key()
        if not api_key:
            raise ValueError(
                "API key is required. Provide it explicitly or set "
                "the OPENCODE_API_KEY environment variable."
            )

        self._api_key = api_key
        self._base_url = base_url or self.DEFAULT_BASE_URL
        self._text_model = (
            text_model or os.environ.get("OPENCODE_TEXT_MODEL") or self.DEFAULT_TEXT_MODEL
        )
        self._vision_model = (
            vision_model or os.environ.get("OPENCODE_VISION_MODEL") or self.DEFAULT_VISION_MODEL
        )
        self._client = OpenAI(api_key=api_key, base_url=self._base_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
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
            Model name.  When ``None`` the instance text model is used
            (constructor ``text_model`` > ``OPENCODE_TEXT_MODEL`` env var >
            ``deepseek-v4-flash``).
        max_tokens:
            Maximum tokens in the completion.
        temperature:
            Sampling temperature. 0.0 = deterministic. Omitted entirely for
            models matching ``_NO_TEMPERATURE_PREFIXES`` (the Console Go proxy
            rejects any explicit temperature for them with a 400 error).
        """
        model_name = model or self._text_model
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if not model_name.startswith(self._NO_TEMPERATURE_PREFIXES):
            kwargs["temperature"] = temperature
        return self._with_retry(self._client.chat.completions.create, **kwargs)

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
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
            Vision-capable model.  When ``None`` the instance vision model
            is used (constructor ``vision_model`` > ``OPENCODE_VISION_MODEL``
            env var > ``mimo-v2.5``).
        max_tokens:
            Maximum tokens in the completion.
        """
        return self._with_retry(
            self._client.chat.completions.create,
            model=model or self._vision_model,
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

    @classmethod
    def _read_auth_file_key(cls) -> str:
        """Best-effort read of the OpenCode Go key from the local auth file.

        Reads ``["opencode-go"]["key"]`` from :attr:`AUTH_FILE`.  Returns
        an empty string when the file is missing, unreadable, malformed, or
        lacks the field.  The key value is never logged.
        """
        try:
            data = json.loads(cls.AUTH_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        if not isinstance(data, dict):
            return ""
        entry = data.get("opencode-go")
        if not isinstance(entry, dict):
            return ""
        key = entry.get("key")
        return key.strip() if isinstance(key, str) else ""

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


class MultiProviderClient(OpenCodeGoClient):
    """Unified cloud client that switches between configured providers.

    Provider selection order:

    1. ``provider`` constructor argument
    2. ``CLOUD_PROVIDER`` environment variable
    3. ``opencodego`` default

    API keys are read from ``{PROVIDER}_API_KEY`` environment variables.
    If a key is missing, the client falls back to the OpenCodeGo auth file
    only for the ``opencodego`` provider.

    Usage::

        client = MultiProviderClient(provider="kimi")
        resp = client.chat([{"role": "user", "content": "Hi"}])
    """

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        text_model: str | None = None,
        vision_model: str | None = None,
    ) -> None:
        self._provider = (
            provider or os.environ.get("CLOUD_PROVIDER", "opencodego")
        ).lower()
        if self._provider not in PROVIDER_CONFIGS:
            raise ValueError(
                f"Unknown provider '{self._provider}'. "
                f"Supported: {list(PROVIDER_CONFIGS)}"
            )

        cfg = PROVIDER_CONFIGS[self._provider]
        prefix = self._provider.upper()

        resolved_api_key = api_key or os.environ.get(f"{prefix}_API_KEY", "")
        resolved_base_url = base_url or os.environ.get(
            f"{prefix}_BASE_URL", cfg["base_url"]
        )
        resolved_text_model = text_model or os.environ.get(
            f"{prefix}_TEXT_MODEL", cfg["text_model"]
        )
        resolved_vision_model = vision_model or os.environ.get(
            f"{prefix}_VISION_MODEL", cfg["vision_model"]
        )

        if not resolved_api_key and self._provider == "opencodego":
            resolved_api_key = OpenCodeGoClient._read_auth_file_key()

        if not resolved_api_key:
            raise ValueError(
                f"API key required for provider '{self._provider}'. "
                f"Set {prefix}_API_KEY or provide it explicitly."
            )

        self._api_key = resolved_api_key
        self._base_url = resolved_base_url
        self._text_model = resolved_text_model
        self._vision_model = resolved_vision_model
        self._client = OpenAI(api_key=resolved_api_key, base_url=resolved_base_url)

    @property
    def provider(self) -> str:
        """Current provider name."""
        return self._provider

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Any:
        """Send a text-only chat completion request through the active provider."""
        return super().chat(messages, model=model, max_tokens=max_tokens, temperature=temperature)

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> Any:
        """Send a multimodal chat completion request through the active provider."""
        return super().chat_with_vision(messages, model=model, max_tokens=max_tokens)
