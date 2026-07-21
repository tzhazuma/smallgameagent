"""Tests for src/agent/lmstudio_client.py — all server I/O mocked."""

from __future__ import annotations

from unittest import mock

import pytest

from src.agent.lmstudio_client import LMStudioClient


@pytest.fixture(autouse=True)
def _clear_lmstudio_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real LMSTUDIO_MODEL / LMSTUDIO_BASE_URL from leaking into tests."""
    monkeypatch.delenv("LMSTUDIO_MODEL", raising=False)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)


@pytest.fixture
def client() -> LMStudioClient:
    return LMStudioClient(model="qwen35-4b")


class TestConfig:
    def test_defaults(self) -> None:
        c = LMStudioClient()
        assert c._base_url == LMStudioClient.DEFAULT_BASE_URL
        assert c._model is None

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://10.0.0.2:9999/v1")
        monkeypatch.setenv("LMSTUDIO_MODEL", "gemma4-e4b")
        c = LMStudioClient()
        assert c._base_url == "http://10.0.0.2:9999/v1"
        assert c._model == "gemma4-e4b"

    def test_constructor_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://env:1/v1")
        c = LMStudioClient(base_url="http://arg:2/v1", model="m")
        assert c._base_url == "http://arg:2/v1"


class TestCalls:
    def test_chat_passes_params(self, client: LMStudioClient) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat([{"role": "user", "content": "hi"}], max_tokens=64)
            _, kwargs = m.call_args
            assert kwargs["model"] == "qwen35-4b"
            assert kwargs["max_tokens"] == 64
            assert kwargs["temperature"] == 0.0

    def test_vision_default_temperature(self, client: LMStudioClient) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat_with_vision([{"role": "user", "content": []}])
            _, kwargs = m.call_args
            assert kwargs["temperature"] == 0.0

    def test_resolve_model_falls_back_to_server_list(self) -> None:
        c = LMStudioClient()
        with mock.patch.object(c._client.models, "list") as m:
            m.return_value = [mock.Mock(id="loaded-model.gguf")]
            assert c.resolve_model() == "loaded-model.gguf"

    def test_resolve_model_failure_returns_placeholder(self) -> None:
        c = LMStudioClient()
        with mock.patch.object(c._client.models, "list", side_effect=RuntimeError):
            assert c.resolve_model() == "local-model"
            assert c.list_models() == []


class TestHelpers:
    def test_extract_content_strips(self) -> None:
        resp = mock.Mock()
        resp.choices = [mock.Mock(message=mock.Mock(content="  {\"a\":1}  "))]
        assert LMStudioClient.extract_content(resp) == '{"a":1}'

    def test_extract_content_none(self) -> None:
        resp = mock.Mock()
        resp.choices = [mock.Mock(message=mock.Mock(content=None))]
        assert LMStudioClient.extract_content(resp) == ""

    def test_encode_image_base64(self, tmp_path) -> None:
        png = tmp_path / "x.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        uri = LMStudioClient.encode_image_base64(png)
        assert uri.startswith("data:image/png;base64,")
        jpg = tmp_path / "x.jpg"
        jpg.write_bytes(b"\xff\xd8fake")
        assert LMStudioClient.encode_image_base64(jpg).startswith("data:image/jpeg;base64,")
