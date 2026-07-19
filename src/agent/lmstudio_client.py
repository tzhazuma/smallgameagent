"""Client for a locally hosted LM Studio / llama.cpp OpenAI-compatible server.

Targets ``http://127.0.0.1:1234/v1`` by default (LM Studio local server, or a
directly launched ``llama-server`` with the Vulkan backend). Used by the
``vlm`` / ``vlm-struct`` hybrid modes to run local 4-bit VLMs
(Qwen3.5-4B / Qwen3.5-9B / Gemma-4-E4B) instead of cloud APIs.

Only stdlib + the already-installed ``openai`` SDK are used, mirroring the
style of :class:`src.agent.api_client.OpenCodeGoClient`.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)


class LMStudioClient:
    """OpenAI-compatible client for a local LM Studio / llama.cpp endpoint.

    Parameters
    ----------
    base_url:
        Server root. Defaults to ``LMSTUDIO_BASE_URL`` env var, then
        ``http://127.0.0.1:1234/v1``.
    model:
        Default model name. Defaults to ``LMSTUDIO_MODEL`` env var, then the
        first entry of ``GET /models`` at call time (llama.cpp accepts any
        string and serves the loaded model).
    api_key:
        llama.cpp / LM Studio ignore the key; anything non-empty works.
    timeout_s:
        Per-request timeout. Local VLM image requests can take minutes on
        iGPU/CPU encoders, so the default is generous.
    """

    DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
    ENV_BASE_URL = "LMSTUDIO_BASE_URL"
    ENV_MODEL = "LMSTUDIO_MODEL"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "lm-studio",
        timeout_s: float = 600.0,
    ) -> None:
        self._base_url = base_url or os.environ.get(self.ENV_BASE_URL) or self.DEFAULT_BASE_URL
        self._model = model or os.environ.get(self.ENV_MODEL)
        self._client = OpenAI(api_key=api_key, base_url=self._base_url, timeout=timeout_s)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_models(self) -> list[str]:
        """Return model ids known to the server (empty list on failure)."""
        try:
            return [m.id for m in self._client.models.list()]
        except Exception:
            logger.warning("failed to list models from %s", self._base_url, exc_info=True)
            return []

    def resolve_model(self) -> str:
        """Resolve the configured model name, falling back to the server's first."""
        if self._model:
            return self._model
        models = self.list_models()
        return models[0] if models else "local-model"

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Any:
        """Text-only chat completion. Returns the raw SDK response."""
        return self._client.chat.completions.create(
            model=model or self.resolve_model(),
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> Any:
        """Multimodal chat completion (image_url content parts)."""
        return self._client.chat.completions.create(
            model=model or self.resolve_model(),
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.0,
        )

    @staticmethod
    def extract_content(response: Any) -> str:
        """Extract assistant content, tolerating thinking models.

        Qwen3.5 / Gemma-4 are thinking models: reasoning goes to
        ``reasoning_content`` and the final answer to ``content``.
        Some builds fold the chain into ``content``; either way we
        return whichever field has the answer.  When ``content`` is
        empty but ``reasoning_content`` has text, we fall back to it
        so callers can at least attempt to parse a JSON action from
        the reasoning chain.
        """
        message = response.choices[0].message
        content = getattr(message, "content", None) or ""
        if not content.strip():
            reasoning = getattr(message, "reasoning_content", None) or ""
            if reasoning.strip():
                content = reasoning
        return content.strip()

    @staticmethod
    def encode_image_base64(image_path: str | Path) -> str:
        """Read an image file and return a ``data:image/...;base64,...`` URI."""
        path = Path(image_path)
        suffix = path.suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/{mime};base64,{data}"
