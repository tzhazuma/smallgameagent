"""Tests for src.agent.api_client."""

from __future__ import annotations

import base64
import struct
import zlib
from unittest import mock

import pytest

from src.agent.api_client import MultiProviderClient, OpenCodeGoClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_png() -> bytes:
    """Return the bytes of a valid 1×1 solid-blue PNG (minimal valid PNG)."""

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        full = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(full) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + full + crc

    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1×1, 8-bit RGB
    ihdr = chunk(b"IHDR", ihdr_data)
    raw = zlib.compress(b"\x00\x00\x00\xff")  # filter=0, R=0 G=0 B=255
    idat = chunk(b"IDAT", raw)
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImport:
    """Verify the module is importable."""

    def test_class_exists(self) -> None:
        assert OpenCodeGoClient is not None
        assert callable(OpenCodeGoClient)

    def test_import_from_package(self) -> None:
        from src.agent import OpenCodeGoClient as PkgClient  # noqa: PLC0415

        assert PkgClient is OpenCodeGoClient


class TestImageEncoding:
    """Verify encode_image_base64 produces valid data URIs."""

    def test_encodes_valid_data_uri(self, tmp_path) -> None:
        png = tmp_path / "test.png"
        png.write_bytes(_make_minimal_png())

        uri = OpenCodeGoClient.encode_image_base64(png)

        assert uri.startswith("data:image/png;base64,")
        payload = uri.split(",", 1)[1]
        decoded = base64.b64decode(payload)
        assert decoded == _make_minimal_png()

    def test_jpeg_extension(self, tmp_path) -> None:
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG SOI marker
        uri = OpenCodeGoClient.encode_image_base64(jpg)
        assert uri.startswith("data:image/jpeg;base64,")


class TestInit:
    """Constructor behaviour."""

    def test_explicit_key(self) -> None:
        client = OpenCodeGoClient(api_key="sk-test-123")
        assert client._api_key == "sk-test-123"

    def test_env_var_key(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-env-456")
        client = OpenCodeGoClient()
        assert client._api_key == "sk-env-456"

    def test_explicit_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-env-789")
        client = OpenCodeGoClient(api_key="sk-explicit-000")
        assert client._api_key == "sk-explicit-000"

    def test_missing_key_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", tmp_path / "nonexistent.json")
        with pytest.raises(ValueError, match="API key"):
            OpenCodeGoClient()

    def test_custom_base_url(self) -> None:
        custom = "https://custom.example.com/v1"
        client = OpenCodeGoClient(api_key="sk", base_url=custom)
        assert client._base_url == custom

    def test_default_base_url(self) -> None:
        client = OpenCodeGoClient(api_key="sk")
        assert client._base_url == "https://opencode.ai/zen/go/v1"


class TestAuthFileFallback:
    """API key fallback to the OpenCode auth.json file."""

    @staticmethod
    def _write_auth_file(tmp_path, payload: str) -> object:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(payload, encoding="utf-8")
        return auth_file

    def test_auth_file_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        auth_file = self._write_auth_file(
            tmp_path, '{"opencode-go": {"key": "sk-from-auth-file"}}'
        )
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        client = OpenCodeGoClient()
        assert client._api_key == "sk-from-auth-file"

    def test_env_var_beats_auth_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-env-wins")
        auth_file = self._write_auth_file(
            tmp_path, '{"opencode-go": {"key": "sk-from-auth-file"}}'
        )
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        client = OpenCodeGoClient()
        assert client._api_key == "sk-env-wins"

    def test_explicit_beats_auth_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        auth_file = self._write_auth_file(
            tmp_path, '{"opencode-go": {"key": "sk-from-auth-file"}}'
        )
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        client = OpenCodeGoClient(api_key="sk-explicit")
        assert client._api_key == "sk-explicit"

    def test_missing_auth_file_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", tmp_path / "nope.json")
        with pytest.raises(ValueError, match="API key"):
            OpenCodeGoClient()

    def test_missing_field_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        auth_file = self._write_auth_file(tmp_path, '{"other-provider": {"key": "sk-x"}}')
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        with pytest.raises(ValueError, match="API key"):
            OpenCodeGoClient()

    def test_empty_key_field_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        auth_file = self._write_auth_file(tmp_path, '{"opencode-go": {"key": "  "}}')
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        with pytest.raises(ValueError, match="API key"):
            OpenCodeGoClient()

    def test_malformed_json_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        auth_file = self._write_auth_file(tmp_path, "not json {")
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        with pytest.raises(ValueError, match="API key"):
            OpenCodeGoClient()


class TestModelResolution:
    """Text / vision model name resolution (ctor > env > default)."""

    def test_default_models(self) -> None:
        client = OpenCodeGoClient(api_key="sk")
        assert client._text_model == "deepseek-v4-flash"
        assert client._vision_model == "mimo-v2.5"

    def test_env_var_override(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_TEXT_MODEL", "kimi-k2.7-code")
        monkeypatch.setenv("OPENCODE_VISION_MODEL", "mimo-v2.5-pro")
        client = OpenCodeGoClient(api_key="sk")
        assert client._text_model == "kimi-k2.7-code"
        assert client._vision_model == "mimo-v2.5-pro"

    def test_constructor_beats_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_TEXT_MODEL", "kimi-k2.7-code")
        monkeypatch.setenv("OPENCODE_VISION_MODEL", "mimo-v2.5-pro")
        client = OpenCodeGoClient(api_key="sk", text_model="deepseek-v4-pro", vision_model="mimo")
        assert client._text_model == "deepseek-v4-pro"
        assert client._vision_model == "mimo"

    def test_chat_uses_resolved_text_model(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_TEXT_MODEL", "kimi-k2.7-code")
        client = OpenCodeGoClient(api_key="sk")
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(messages=[{"role": "user", "content": "hi"}])
            _, kwargs = m.call_args
            assert kwargs["model"] == "kimi-k2.7-code"

    def test_chat_with_vision_uses_resolved_vision_model(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_VISION_MODEL", "kimi-k2.7-code")
        client = OpenCodeGoClient(api_key="sk")
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat_with_vision(messages=[{"role": "user", "content": "describe"}])
            _, kwargs = m.call_args
            assert kwargs["model"] == "kimi-k2.7-code"

    def test_explicit_model_arg_beats_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENCODE_TEXT_MODEL", "kimi-k2.7-code")
        client = OpenCodeGoClient(api_key="sk")
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(messages=[{"role": "user", "content": "hi"}], model="deepseek-v4-flash")
            _, kwargs = m.call_args
            assert kwargs["model"] == "deepseek-v4-flash"


class TestChatMocked:
    """Model / parameter tests using a mocked OpenAI SDK."""

    @pytest.fixture
    def client(self) -> OpenCodeGoClient:
        return OpenCodeGoClient(api_key="sk-mock")

    def test_chat_sends_model_and_temperature(self, client) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-v4-flash",
                temperature=0.0,
            )
            _, kwargs = m.call_args
            assert kwargs["model"] == "deepseek-v4-flash"
            assert kwargs["temperature"] == 0.0

    def test_chat_default_model(self, client) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(messages=[{"role": "user", "content": "hi"}])
            _, kwargs = m.call_args
            assert kwargs["model"] == "deepseek-v4-flash"

    def test_chat_omits_temperature_for_kimi_models(self, client) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="kimi-k2.7-code",
                temperature=0.0,
            )
            _, kwargs = m.call_args
            assert kwargs["model"] == "kimi-k2.7-code"
            assert "temperature" not in kwargs

    def test_chat_with_vision(self, client) -> None:
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat_with_vision(
                messages=[{"role": "user", "content": "describe"}],
            )
            _, kwargs = m.call_args
            assert kwargs["model"] == "mimo-v2.5"


class TestRetryLogic:
    """Verify retry behaviour on transient errors."""

    @pytest.fixture
    def client(self) -> OpenCodeGoClient:
        return OpenCodeGoClient(api_key="sk-mock")

    def test_retries_on_429(self, client) -> None:
        call_count = 0

        def flaky(**kwargs) -> mock.Mock:  # noqa: ANN003
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                exc = Exception("rate limited")
                exc.status_code = 429
                raise exc
            return mock.Mock()

        with mock.patch.object(client._client.chat.completions, "create", flaky):
            client.chat(messages=[{"role": "user", "content": "hi"}])
        assert call_count == 3

    def test_no_retry_on_400(self, client) -> None:
        call_count = 0

        def bad_request(**kwargs) -> mock.Mock:  # noqa: ANN003
            nonlocal call_count
            call_count += 1
            exc = Exception("bad request")
            exc.status_code = 400
            raise exc

        with mock.patch.object(client._client.chat.completions, "create", bad_request):
            with pytest.raises(Exception, match="bad request"):
                client.chat(messages=[{"role": "user", "content": "hi"}])
        assert call_count == 1


class TestMultiProviderClient:
    """Multi-provider cloud client routing."""

    def test_default_provider_is_opencodego(self, monkeypatch) -> None:
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        monkeypatch.setenv("OPENCODEGO_API_KEY", "sk-test")
        client = MultiProviderClient()
        assert client.provider == "opencodego"
        assert client._text_model == "deepseek-v4-flash"

    def test_kimi_provider_reads_env(self, monkeypatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
        client = MultiProviderClient()
        assert client.provider == "kimi"
        assert client._api_key == "sk-kimi"
        assert client._base_url == "https://api.kimi.com/coding/v1"
        assert client._text_model == "kimi-k2.5"
        assert client._vision_model == "kimi-k2.5"

    def test_xiaomi_provider_defaults_to_mimo(self, monkeypatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "xiaomi")
        monkeypatch.setenv("XIAOMI_API_KEY", "sk-mimo")
        client = MultiProviderClient()
        assert client.provider == "xiaomi"
        assert client._vision_model == "mimo-v2.5"

    def test_unknown_provider_raises(self, monkeypatch) -> None:
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        with pytest.raises(ValueError, match="Unknown provider"):
            MultiProviderClient(provider="not-a-provider")

    def test_explicit_args_override_env(self, monkeypatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env")
        client = MultiProviderClient(
            provider="qwen",
            api_key="sk-qwen-explicit",
            text_model="qwen3.7-max",
        )
        assert client.provider == "qwen"
        assert client._api_key == "sk-qwen-explicit"
        assert client._text_model == "qwen3.7-max"

    def test_chat_routes_to_provider_model(self, monkeypatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
        client = MultiProviderClient()
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat(messages=[{"role": "user", "content": "hi"}])
            _, kwargs = m.call_args
            assert kwargs["model"] == "kimi-k2.5"
            assert "temperature" not in kwargs

    def test_vision_routes_to_provider_vision_model(self, monkeypatch) -> None:
        monkeypatch.setenv("CLOUD_PROVIDER", "xiaomi")
        monkeypatch.setenv("XIAOMI_API_KEY", "sk-mimo")
        client = MultiProviderClient()
        with mock.patch.object(client._client.chat.completions, "create") as m:
            m.return_value = mock.Mock()
            client.chat_with_vision(messages=[{"role": "user", "content": "describe"}])
            _, kwargs = m.call_args
            assert kwargs["model"] == "mimo-v2.5"

    def test_missing_key_raises_with_provider_prefix(self, monkeypatch) -> None:
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        with pytest.raises(ValueError, match="QWEN_API_KEY"):
            MultiProviderClient(provider="qwen")

    def test_opencodego_missing_key_uses_auth_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("OPENCODEGO_API_KEY", raising=False)
        auth_file = tmp_path / "auth.json"
        auth_file.write_text('{"opencode-go": {"key": "sk-auth"}}', encoding="utf-8")
        monkeypatch.setattr(OpenCodeGoClient, "AUTH_FILE", auth_file)
        client = MultiProviderClient(provider="opencodego")
        assert client._api_key == "sk-auth"
