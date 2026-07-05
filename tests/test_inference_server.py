"""Tests for src.inference.server (GameAgentInference + FastAPI app).

All model loading is mocked — no real weights are downloaded and no real
HTTP server is started.  Uses ``httpx.ASGITransport`` for endpoint testing.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

if TYPE_CHECKING:
    from src.inference.server import GameAgentInference


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dummy_pil_image() -> Image.Image:
    """A 224×224 pure-red PIL Image for testing."""
    return Image.new("RGB", (224, 224), color="red")


@pytest.fixture
def dummy_state_json() -> bytes:
    """Bytes of a minimal game state JSON."""
    return json.dumps({
        "ready": True,
        "done": False,
        "win": False,
        "keyNumbers": {"score": 100},
    }).encode("utf-8")


@pytest.fixture
def dummy_state_dict() -> dict:
    """Minimal game state dict."""
    return {
        "ready": True,
        "done": False,
        "win": False,
        "keyNumbers": {"score": 100},
    }


@pytest.fixture
def mock_transformers_module() -> mock.MagicMock:
    """Return a mock transformers module with the classes we need."""
    mod = mock.MagicMock()

    # Qwen3_5ForConditionalGeneration
    mod.Qwen3_5ForConditionalGeneration = mock.MagicMock()
    mod.AutoModelForVision2Seq = mock.MagicMock()
    mod.AutoProcessor = mock.MagicMock()
    mod.BitsAndBytesConfig = mock.MagicMock()

    return mod


# ---------------------------------------------------------------------------
# GameAgentInference — model loading (fully mocked)
# ---------------------------------------------------------------------------


def _build_mock_model_and_processor(
    model_output_json: dict | None = None,
) -> tuple[mock.MagicMock, mock.MagicMock]:
    """Build a matched mock (model, processor) pair for prediction.

    The mock model's ``generate`` returns token IDs that, when decoded,
    produce the given JSON string.
    """
    if model_output_json is None:
        model_output_json = {
            "action": "move",
            "params": {"dx": 0.5, "dy": 0.2, "duration_ms": 320},
            "reason": "Move toward target",
        }

    output_str = json.dumps(model_output_json)

    # ── Mock processor ─────────────────────────────────────────────────
    processor = mock.MagicMock()
    processor.apply_chat_template.return_value = "<|im_start|>system\n...\n<|im_end|>\n<|im_start|>user\n..."

    # __call__ returns tokenised inputs.
    mock_input_ids = mock.MagicMock()
    mock_input_ids.shape = (1, 42)  # 42 input tokens
    processor_output = mock.MagicMock()
    processor_output.__getitem__ = mock.MagicMock()
    processor_output.shape = mock.MagicMock()
    processor_output.configure_mock(**{
        "input_ids": mock_input_ids,
        "attention_mask": mock.MagicMock(),
        "pixel_values": mock.MagicMock(),
    })
    processor.return_value = processor_output
    processor_output.to.return_value = processor_output

    # tokenizer sub-object for pad/eos tokens.
    processor.tokenizer = mock.MagicMock()
    processor.tokenizer.pad_token_id = 0
    processor.tokenizer.eos_token_id = 1

    # decode returns the JSON string.
    processor.decode.return_value = output_str

    # ── Mock model ──────────────────────────────────────────────────────
    model = mock.MagicMock()
    model.device = "cuda:0"
    mock_param = mock.MagicMock()
    mock_param.device = "cuda:0"
    model.parameters.return_value = iter([mock_param])
    model.eval = mock.MagicMock()

    # generate returns IDs that include input + generated tokens.
    gen_ids = mock.MagicMock()
    gen_ids.__getitem__.return_value = mock.MagicMock()
    # We need the generated_ids slice to be token IDs that decode to JSON.
    # Simulate by having decode return our JSON string.
    mock_output_tensor = mock.MagicMock()
    mock_output_tensor.shape = (50,)

    # The actual generate return is a list of tensors.
    mock_generated = mock.MagicMock()
    mock_generated.__getitem__.return_value = mock_output_tensor
    mock_generated.shape = mock.MagicMock()

    model.generate.return_value = mock_generated

    return model, processor


def _patch_all(
    mock_model: mock.MagicMock,
    mock_processor: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject mock ``transformers`` and ``peft`` into ``sys.modules``.

    The mock ``torch`` is already injected by the autouse fixture in conftest.
    Pathlib ``Path.is_dir`` / ``Path.is_file`` default to False to suppress
    adapter loading (suitable for base-model-only tests).
    """
    import sys

    # Build and inject transformers mock
    transformers_mock = mock.MagicMock(name="transformers")
    transformers_mock.Qwen3_5ForConditionalGeneration = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=mock_model),
    )
    transformers_mock.AutoModelForVision2Seq = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=mock_model),
    )
    transformers_mock.AutoProcessor = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=mock_processor),
    )
    transformers_mock.BitsAndBytesConfig = mock.MagicMock(
        return_value=mock.MagicMock(name="quant_config"),
    )
    sys.modules["transformers"] = transformers_mock
    # Register transformers sub-modules for Gemma4 import path
    sys.modules["transformers.models"] = mock.MagicMock(name="transformers.models")
    sys.modules["transformers.models.gemma4"] = mock.MagicMock(name="transformers.models.gemma4")
    sys.modules["transformers.models.gemma4.modeling_gemma4"] = mock.MagicMock(
        name="transformers.models.gemma4.modeling_gemma4",
    )

    # Build and inject peft mock with sub-modules for package-like import support
    merged = mock.MagicMock()
    merged.merge_and_unload.return_value = mock_model
    peft_mock = mock.MagicMock(name="peft")
    peft_mock.PeftModel = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=merged),
    )
    sys.modules["peft"] = peft_mock
    # Register sub-modules so `import peft.tuners.lora.model` works
    sys.modules["peft.tuners"] = mock.MagicMock(name="peft.tuners")
    sys.modules["peft.tuners.lora"] = mock.MagicMock(name="peft.tuners.lora")
    sys.modules["peft.tuners.lora.model"] = mock.MagicMock(name="peft.tuners.lora.model")
    sys.modules["peft.tuners.lora.bnb"] = mock.MagicMock(name="peft.tuners.lora.bnb")

    # Build and inject bitsandbytes mock for module-level import in server.py
    sys.modules["bitsandbytes"] = mock.MagicMock(name="bitsandbytes")
    sys.modules["bitsandbytes.nn"] = mock.MagicMock(name="bitsandbytes.nn")
    sys.modules["bitsandbytes.nn"].Linear4bit = mock.MagicMock(name="Linear4bit")

    # Pathlib: is_dir → True, is_file → False by default (no adapter)
    monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)


class TestGameAgentInferenceLoading:
    """Model and processor loading with mocked transformers."""

    def test_loads_qwen35_without_adapter(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Qwen3.5-4B loads successfully when no adapter_config.json exists."""
        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)

        # Ensure adapter_config.json does NOT exist.
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        engine = __import__("src.inference.server", fromlist=["GameAgentInference"]).GameAgentInference(
            model_path="Qwen/Qwen3.5-4B",
            device="cuda",
        )

        assert engine.is_ready
        assert engine.model_name == "qwen35-4b"
        assert "cuda" in engine.device

    def test_loads_gemma4_without_adapter(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gemma-4-E4B loads via AutoModelForVision2Seq."""
        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        from src.inference.server import GameAgentInference

        engine = GameAgentInference(
            model_path="google/gemma-4-e4b-it",
            device="cuda",
        )
        assert engine.is_ready
        # gemma family is detected.
        assert "gemma" in engine.model_name

    def test_loads_with_lora_adapter(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When adapter_config.json exists, PeftModel.from_pretrained is used."""

        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)

        # Simulate: model_path is_dir → True, adapter_config.json exists.
        is_dir_calls: list[bool] = []

        def _is_dir(path: str) -> bool:
            is_dir_calls.append(True)
            return True

        is_file_calls: list[bool] = []

        def _is_file(path: str) -> bool:
            is_file_calls.append(True)
            # Only adapter_config.json exists (mask training_metadata.json).
            return "adapter_config.json" in str(path) and "training_metadata" not in str(path)

        monkeypatch.setattr("pathlib.Path.is_dir", _is_dir)
        monkeypatch.setattr("pathlib.Path.is_file", _is_file)

        from src.inference.server import GameAgentInference

        engine = GameAgentInference(
            model_path="/tmp/checkpoints/qwen35-4b-gameplay/final",
        )
        assert engine.is_ready

    def test_loads_with_4bit_quantization(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force 4-bit quantisation when VRAM < 12 GB."""
        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)

        # Override VRAM to 8 GB.
        mock_props = mock.MagicMock()
        mock_props.total_memory = 8 * 1024**3
        monkeypatch.setattr("torch.cuda.get_device_properties", lambda idx: mock_props)

        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        # Verify BitsAndBytesConfig is used by checking the from_pretrained call
        # receives quantization_config.  Qwen3_5ForConditionalGeneration is in
        # the mocked sys.modules["transformers"].
        import sys
        from src.inference.server import GameAgentInference

        game_inf = GameAgentInference("Qwen/Qwen3.5-4B", device="cuda", use_4bit=False)
        assert game_inf.is_ready
        transformers_mock = sys.modules["transformers"]
        call_kwargs = transformers_mock.Qwen3_5ForConditionalGeneration.from_pretrained.call_args.kwargs
        assert "quantization_config" in call_kwargs

    def test_loads_on_cpu(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CPU-only device loads correctly."""
        mock_model, mock_processor = _build_mock_model_and_processor()
        mock_model.device = "cpu"
        mock_param = mock.MagicMock()
        mock_param.device = "cpu"
        mock_model.parameters.return_value = iter([mock_param])
        _patch_all(mock_model, mock_processor, monkeypatch)

        monkeypatch.setattr("torch.cuda.is_available", lambda: False)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        from src.inference.server import GameAgentInference

        engine = GameAgentInference("Qwen/Qwen3.5-4B", device="cpu")
        assert engine.is_ready
        assert engine.device == "cpu"

    def test_multi_gpu_uses_auto_device_map(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When >1 GPU, device_map='auto'."""
        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)

        monkeypatch.setattr("torch.cuda.device_count", lambda: 4)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        import sys
        from src.inference.server import GameAgentInference

        game_inf = GameAgentInference("Qwen/Qwen3.5-4B")
        assert game_inf.is_ready
        transformers_mock = sys.modules["transformers"]
        call_kwargs = transformers_mock.Qwen3_5ForConditionalGeneration.from_pretrained.call_args.kwargs
        assert call_kwargs["device_map"] == "auto"


# ---------------------------------------------------------------------------
# GameAgentInference — prediction
# ---------------------------------------------------------------------------


class TestGameAgentInferencePredict:
    """predict() and predict_async() output parsing and formatting."""

    def _make_engine(
        self,
        monkeypatch: pytest.MonkeyPatch,
        model_output: dict | None = None,
    ) -> GameAgentInference:
        from src.inference.server import GameAgentInference

        mock_model, mock_processor = _build_mock_model_and_processor(model_output)
        _patch_all(mock_model, mock_processor, monkeypatch)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        return GameAgentInference("Qwen/Qwen3.5-4B", device="cuda")

    def test_predict_returns_move_action(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image, dummy_state_dict: dict,
    ) -> None:
        """Standard prediction returns a valid move action dict."""
        engine = self._make_engine(monkeypatch)
        result = engine.predict(dummy_pil_image, dummy_state_dict)

        assert result["action"] == "move"
        assert "params" in result
        assert result["params"]["dx"] == 0.5
        assert result["params"]["dy"] == 0.2
        assert result["params"]["duration_ms"] == 320
        assert "reason" in result
        assert "model_name" in result
        assert "latency_ms" in result

    def test_predict_accepts_json_string(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image,
    ) -> None:
        """predict() accepts pre-serialised JSON strings."""
        engine = self._make_engine(monkeypatch)
        state_str = json.dumps({"ready": True, "score": 200})
        result = engine.predict(dummy_pil_image, state_str)
        assert result["action"] == "move"

    def test_predict_returns_tap_action(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image, dummy_state_dict: dict,
    ) -> None:
        """Model output for a tap action is parsed correctly."""
        tap_output = {
            "action": "tap",
            "params": {"x": 200.0, "y": 400.0, "duration_ms": 150},
            "reason": "Tap the button",
        }
        engine = self._make_engine(monkeypatch, model_output=tap_output)
        result = engine.predict(dummy_pil_image, dummy_state_dict)
        assert result["action"] == "tap"
        assert result["params"]["x"] == 200.0
        assert result["params"]["y"] == 400.0

    def test_predict_returns_wait_action(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image, dummy_state_dict: dict,
    ) -> None:
        """Model output for a wait action is parsed correctly."""
        wait_output = {
            "action": "wait",
            "params": {"duration_ms": 1000},
            "reason": "Waiting for animation",
        }
        engine = self._make_engine(monkeypatch, model_output=wait_output)
        result = engine.predict(dummy_pil_image, dummy_state_dict)
        assert result["action"] == "wait"
        assert result["params"]["duration_ms"] == 1000

    @pytest.mark.asyncio
    async def test_predict_async_like_sync(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image, dummy_state_dict: dict,
    ) -> None:
        """predict_async returns the same result as predict (via thread-pool)."""
        engine = self._make_engine(monkeypatch)
        sync_result = engine.predict(dummy_pil_image, dummy_state_dict)
        async_result = await engine.predict_async(dummy_pil_image, dummy_state_dict)

        assert async_result["action"] == sync_result["action"]
        assert async_result["params"] == sync_result["params"]

    def test_predict_unloaded_model_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Before _load_model completes, predict raises RuntimeError."""
        from src.inference.server import GameAgentInference

        # Patch only cuda so we can call __init__ but skip _load_model.
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)

        engine = GameAgentInference.__new__(GameAgentInference)
        engine._loaded = False  # type: ignore[attr-defined]
        engine._model = None  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="not loaded"):
            dummy = Image.new("RGB", (10, 10))
            engine.predict(dummy, {})  # type: ignore[union-attr]

    def test_latency_is_positive(
        self, monkeypatch: pytest.MonkeyPatch, dummy_pil_image: Image.Image, dummy_state_dict: dict,
    ) -> None:
        """latency_ms field is populated with a positive float."""
        engine = self._make_engine(monkeypatch)
        result = engine.predict(dummy_pil_image, dummy_state_dict)
        assert result["latency_ms"] > 0


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------


class TestWarmup:
    """GameAgentInference._warmup behavior."""

    def test_warmup_completes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warmup runs 3 predict passes and sets _warmup_complete."""
        from src.inference.server import GameAgentInference

        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        engine = GameAgentInference("Qwen/Qwen3.5-4B", device="cuda")
        assert engine._warmup_complete is True
        # 3 warmup passes each call model.generate
        assert engine._model.generate.call_count >= 3

    def test_skip_warmup_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """skip_warmup=True leaves _warmup_complete as False."""
        from src.inference.server import GameAgentInference

        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        engine = GameAgentInference(
            "Qwen/Qwen3.5-4B", device="cuda", skip_warmup=True,
        )
        assert engine._warmup_complete is False
        # model.generate should not have been called by warmup
        assert engine._model.generate.call_count == 0


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


class TestParseModelOutput:
    """_parse_model_output handles valid JSON, fences, and garbage."""

    def test_valid_json_no_fences(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output(
            '{"action":"move","params":{"dx":1},"reason":"test"}',
        )
        assert result["action"] == "move"
        assert result["params"]["dx"] == 1

    def test_json_in_markdown_fence(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output(
            '```json\n{"action":"wait","params":{"duration_ms":500},"reason":"pause"}\n```',
        )
        assert result["action"] == "wait"

    def test_json_in_plain_fence(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output(
            '```\n{"action":"tap","params":{"x":100,"y":200},"reason":"button"}\n```',
        )
        assert result["action"] == "tap"

    def test_json_with_surrounding_text(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output(
            'The best action is {"action":"move","params":{"dx":0.5,"dy":-0.5},"reason":"nw"} here.',
        )
        assert result["action"] == "move"
        assert result["params"]["dx"] == 0.5

    def test_empty_string_fallback(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output("")
        assert result["action"] == "wait"
        assert result["reason"] == "Unable to parse model output"

    def test_whitespace_only_fallback(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output("   \n  ")
        assert result["action"] == "wait"

    def test_no_json_object_fallback(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output("Just move right please.")
        assert result["action"] == "wait"
        assert "Unable" in result["reason"]

    def test_malformed_json_fallback(self) -> None:
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output(
            '{"action": move, "params": {}',
        )
        assert result["action"] == "wait"

    def test_partial_fields_preserved(self) -> None:
        """When output is valid JSON but missing fields, defaults fill in."""
        from src.inference.server import GameAgentInference

        result = GameAgentInference._parse_model_output('{"action":"move"}')
        assert result["action"] == "move"
        assert result["params"] == {}
        assert result["reason"] == ""


# ---------------------------------------------------------------------------
# Model family detection
# ---------------------------------------------------------------------------


class TestDetectModelFamily:
    """_detect_model_family and _resolve_short_name."""

    def test_qwen_detection(self) -> None:
        from src.inference.server import _detect_model_family

        assert _detect_model_family("Qwen/Qwen3.5-4B") == "qwen35"
        assert _detect_model_family("qwen35") == "qwen35"

    def test_gemma_detection(self) -> None:
        from src.inference.server import _detect_model_family

        assert _detect_model_family("google/gemma-4-e4b-it") == "gemma4"
        assert _detect_model_family("gemma4-e4b") == "gemma4"

    def test_default_fallback(self) -> None:
        from src.inference.server import _detect_model_family

        assert _detect_model_family("some-unknown-model") == "qwen35"


class TestResolveShortName:
    """_resolve_short_name produces clean labels."""

    def test_hf_id(self) -> None:
        from src.inference.server import _resolve_short_name

        assert _resolve_short_name("Qwen/Qwen3.5-4B") == "qwen35-4b"

    def test_gemma_hf_id(self) -> None:
        from src.inference.server import _resolve_short_name

        result = _resolve_short_name("google/gemma-4-e4b-it")
        assert "gemma" in result and "e4b" in result


# ---------------------------------------------------------------------------
# FastAPI endpoints (via TestClient, no real HTTP server)
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_mock_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a TestClient with a fully mocked inference engine."""
    from src.inference import server as server_mod
    from src.inference.server import GameAgentInference

    mock_model, mock_processor = _build_mock_model_and_processor()
    _patch_all(mock_model, mock_processor, monkeypatch)
    monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

    # Construct engine and inject into module global.
    engine = GameAgentInference("Qwen/Qwen3.5-4B", device="cuda")
    monkeypatch.setattr(server_mod, "_inference_engine", engine)

    return TestClient(server_mod.app)


def _make_screenshot_upload(image: Image.Image, filename: str = "frame.png") -> tuple[str, bytes, str]:
    """Produce (filename, bytes, content_type) for a multipart upload."""
    buf = BytesIO()
    image.save(buf, format="PNG")
    return filename, buf.getvalue(), "image/png"


class TestHealthEndpoint:
    """GET /health returns status and model metadata."""

    def test_health_ok(self, client_with_mock_engine: TestClient) -> None:
        resp = client_with_mock_engine.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "qwen35-4b"
        assert data["ready"] is True
        assert "torch_version" in data
        assert "cuda_available" in data
        # mocked cuda → available
        assert data["cuda_available"] is True
        assert data["gpu_count"] == 1
        # enhanced fields
        assert data["model_loaded"] is True
        assert data["warmup_complete"] is True  # warmup ran by default
        assert data["vram_mb"] is None or isinstance(data["vram_mb"], float)
        assert isinstance(data["uptime_s"], (int, float))
        assert data["uptime_s"] >= 0
        assert data["model_name"] == "qwen35-4b"

    def test_health_no_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When engine is None, status is 'loading'."""
        from src.inference import server as server_mod

        monkeypatch.setattr(server_mod, "_inference_engine", None)
        client = TestClient(server_mod.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "loading"
        assert data["ready"] is False
        # enhanced fields still present with defaults
        assert data["model_loaded"] is False
        assert data["warmup_complete"] is False
        assert isinstance(data["vram_mb"], (float, type(None)))
        assert data["uptime_s"] >= 0
        assert data["model_name"] == ""

    def test_health_warmup_not_complete(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Health shows warmup_complete=false when skip_warmup is used."""
        from src.inference import server as server_mod
        from src.inference.server import GameAgentInference

        mock_model, mock_processor = _build_mock_model_and_processor()
        _patch_all(mock_model, mock_processor, monkeypatch)
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: True)
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)

        engine = GameAgentInference(
            "Qwen/Qwen3.5-4B", device="cuda", skip_warmup=True,
        )
        monkeypatch.setattr(server_mod, "_inference_engine", engine)
        client = TestClient(server_mod.app)

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["warmup_complete"] is False
        assert data["ready"] is True  # model is loaded, just not warmed up


class TestPredictEndpoint:
    """POST /predict validates inputs and returns action predictions."""

    def test_predict_success(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
        dummy_state_json: bytes,
    ) -> None:
        """Valid screenshot + state → 200 with PredictResponse."""
        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)

        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": (filename, img_bytes, content_type)},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "move"
        assert data["params"]["dx"] == 0.5
        assert data["reason"]
        assert data["model_name"] == "qwen35-4b"
        assert data["latency_ms"] > 0

    def test_predict_missing_screenshot(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """Missing screenshot field → 422 (FastAPI validation)."""
        resp = client_with_mock_engine.post(
            "/predict",
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 422

    def test_predict_missing_state(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
    ) -> None:
        """Missing state field → 422."""
        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)

        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": (filename, img_bytes, content_type)},
        )
        assert resp.status_code == 422

    def test_predict_invalid_state_json(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
    ) -> None:
        """Unparseable state JSON → 400."""
        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)

        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": (filename, img_bytes, content_type)},
            data={"state": "not valid json {{{"},
        )
        assert resp.status_code == 400
        assert "Invalid state" in resp.json()["detail"]

    def test_predict_state_not_dict(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
    ) -> None:
        """State is valid JSON but not a dict → 400."""
        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)

        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": (filename, img_bytes, content_type)},
            data={"state": "[1, 2, 3]"},
        )
        assert resp.status_code == 400
        assert "object" in resp.json()["detail"]

    def test_predict_invalid_image(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """A non-image file uploaded as screenshot → 400."""
        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": ("frame.png", b"not an image", "image/png")},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 400
        assert "Invalid image" in resp.json()["detail"]

    def test_predict_unsupported_media_type(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """Unsupported content type → 415."""
        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": ("frame.gif", b"GIF89a\x00\x00", "image/gif")},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 415

    def test_predict_no_engine(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dummy_pil_image: Image.Image,
        dummy_state_json: bytes,
    ) -> None:
        """When engine is None → 503."""
        from src.inference import server as server_mod

        monkeypatch.setattr(server_mod, "_inference_engine", None)
        client = TestClient(server_mod.app)

        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)
        resp = client.post(
            "/predict",
            files={"screenshot": (filename, img_bytes, content_type)},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 503
        assert "not yet initialised" in resp.json()["detail"]

    def test_predict_accepts_jpeg(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """JPEG upload is accepted."""
        img = Image.new("RGB", (100, 100), color="green")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        img_bytes = buf.getvalue()

        resp = client_with_mock_engine.post(
            "/predict",
            files={"screenshot": ("frame.jpg", img_bytes, "image/jpeg")},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "move"


# ---------------------------------------------------------------------------
# POST /predict/stream (SSE)
# ---------------------------------------------------------------------------


class TestPredictStreamEndpoint:
    """POST /predict/stream produces SSE events."""

    def test_predict_stream_sse_events(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
        dummy_state_json: bytes,
    ) -> None:
        """Stream endpoint returns text/event-stream with token events."""
        from unittest.mock import patch

        # Set up TextIteratorStreamer mock that yields tokens
        tokens = ['{"action":"move","params":{"dx":0.5,"dy":0.2,"duration_ms":320},"reason":"stream test"}']
        mock_streamer = mock.MagicMock()
        mock_streamer.__iter__.return_value = iter(tokens)
        mock_streamer_cls = mock.MagicMock(return_value=mock_streamer)

        # Patch Thread so start/join don't actually run anything
        mock_thread = mock.MagicMock()
        mock_thread_cls = mock.MagicMock(return_value=mock_thread)

        with patch("transformers.TextIteratorStreamer", mock_streamer_cls), \
             patch("threading.Thread", mock_thread_cls):
            filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)
            resp = client_with_mock_engine.post(
                "/predict/stream",
                files={"screenshot": (filename, img_bytes, content_type)},
                data={"state": dummy_state_json.decode("utf-8")},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            # Parse SSE output — events separated by \n\n
            sse_text = resp.text
            events = []
            for part in sse_text.strip().split("\n\n"):
                if part.startswith("data: "):
                    events.append(json.loads(part[6:]))

            # Should have at least 1 token event + final event
            assert len(events) >= 2
            # Token events have "token" key
            assert "token" in events[0]
            # Final event has done=true
            assert events[-1].get("done") is True

    def test_predict_stream_final_event(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
        dummy_state_json: bytes,
    ) -> None:
        """Final SSE event contains action, params, reason, latency_ms."""
        from unittest.mock import patch

        output_json = {'{"action":"move","params":{"dx":0.5,"dy":0.2,"duration_ms":320},"reason":"stream test"}'}
        mock_streamer = mock.MagicMock()
        mock_streamer.__iter__.return_value = iter([output_json])
        mock_streamer_cls = mock.MagicMock(return_value=mock_streamer)
        mock_thread = mock.MagicMock()
        mock_thread_cls = mock.MagicMock(return_value=mock_thread)

        with patch("transformers.TextIteratorStreamer", mock_streamer_cls), \
             patch("threading.Thread", mock_thread_cls):
            filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)
            resp = client_with_mock_engine.post(
                "/predict/stream",
                files={"screenshot": (filename, img_bytes, content_type)},
                data={"state": dummy_state_json.decode("utf-8")},
            )
            assert resp.status_code == 200

            sse_text = resp.text
            events = []
            for part in sse_text.strip().split("\n\n"):
                if part.startswith("data: "):
                    events.append(json.loads(part[6:]))

            final = events[-1]
            assert final["done"] is True
            assert final["action"] == "move"
            assert final["params"]["dx"] == 0.5
            assert final["params"]["dy"] == 0.2
            assert "reason" in final
            assert "latency_ms" in final
            assert final["latency_ms"] > 0
            assert "model_name" in final

    def test_predict_stream_validation(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """Stream endpoint validates inputs like /predict."""
        # Missing screenshot → 422
        resp = client_with_mock_engine.post(
            "/predict/stream",
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 422

    def test_predict_stream_invalid_image(
        self,
        client_with_mock_engine: TestClient,
        dummy_state_json: bytes,
    ) -> None:
        """Invalid image → 400."""
        resp = client_with_mock_engine.post(
            "/predict/stream",
            files={"screenshot": ("frame.png", b"not an image", "image/png")},
            data={"state": dummy_state_json.decode("utf-8")},
        )
        assert resp.status_code == 400
        assert "Invalid image" in resp.json()["detail"]

    def test_predict_stream_invalid_state(
        self,
        client_with_mock_engine: TestClient,
        dummy_pil_image: Image.Image,
    ) -> None:
        """Invalid state JSON → 400."""
        filename, img_bytes, content_type = _make_screenshot_upload(dummy_pil_image)
        resp = client_with_mock_engine.post(
            "/predict/stream",
            files={"screenshot": (filename, img_bytes, content_type)},
            data={"state": "not valid json {{{"},
        )
        assert resp.status_code == 400
        assert "Invalid state" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Serialisation and validation for request/response models."""

    def test_predict_response_valid_move(self) -> None:
        from src.inference.server import ActionParams, PredictResponse

        resp = PredictResponse(
            action="move",
            params=ActionParams(dx=0.5, dy=-0.3, duration_ms=320),
            reason="chase target",
            model_name="qwen35-4b",
            latency_ms=42.5,
        )
        d = resp.model_dump()
        assert d["action"] == "move"
        assert d["params"]["dx"] == 0.5

    def test_predict_response_valid_tap(self) -> None:
        from src.inference.server import ActionParams, PredictResponse

        resp = PredictResponse(
            action="tap",
            params=ActionParams(x=200.0, y=400.0, duration_ms=150),
            reason="click button",
        )
        d = resp.model_dump()
        assert d["action"] == "tap"
        assert d["params"]["x"] == 200.0

    def test_predict_response_valid_wait(self) -> None:
        from src.inference.server import ActionParams, PredictResponse

        resp = PredictResponse(
            action="wait",
            params=ActionParams(duration_ms=1000),
            reason="cooldown",
        )
        assert resp.action == "wait"
        assert resp.params.duration_ms == 1000

    def test_predict_response_defaults(self) -> None:
        from src.inference.server import PredictResponse

        resp = PredictResponse(action="wait")
        assert resp.action == "wait"
        assert resp.params.duration_ms == 320  # default

    def test_action_params_dx_bounds(self) -> None:
        from src.inference.server import ActionParams

        # Within bounds → OK
        p = ActionParams(dx=1.0, dy=-1.0)
        assert p.dx == 1.0

        # Out of bounds → validation error
        with pytest.raises(Exception):
            ActionParams(dx=2.0)

    def test_health_response_defaults(self) -> None:
        from src.inference.server import HealthResponse

        h = HealthResponse()
        assert h.status == "ok"
        assert h.device == "cpu"
        assert h.ready is False

    def test_predict_response_invalid_action_rejected(self) -> None:
        from src.inference.server import PredictResponse

        with pytest.raises(Exception):
            PredictResponse(action="jump")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCLIArgs:
    """_parse_args returns correct defaults and overrides."""

    def test_defaults(self) -> None:
        from src.inference.server import _parse_args

        args = _parse_args([])
        assert args.model == "Qwen/Qwen3.5-4B"
        assert args.adapter == ""
        assert args.device == "cuda"
        assert args.use_4bit is False
        assert args.no_flash_attn is False
        assert args.max_new_tokens == 256
        assert args.temperature == 0.0
        assert args.port == 8000
        assert args.host == "0.0.0.0"

    def test_overrides(self) -> None:
        from src.inference.server import _parse_args

        args = _parse_args([
            "--model", "google/gemma-4-e4b-it",
            "--adapter", "/tmp/lora",
            "--device", "cuda:1",
            "--4bit",
            "--no-flash-attn",
            "--max-new-tokens", "512",
            "--temperature", "0.7",
            "--port", "9999",
            "--host", "127.0.0.1",
        ])
        assert args.model == "google/gemma-4-e4b-it"
        assert args.adapter == "/tmp/lora"
        assert args.device == "cuda:1"
        assert args.use_4bit is True
        assert args.no_flash_attn is True
        assert args.max_new_tokens == 512
        assert args.temperature == 0.7
        assert args.port == 9999
        assert args.host == "127.0.0.1"

    def test_skip_warmup_default(self) -> None:
        """--skip-warmup defaults to False."""
        from src.inference.server import _parse_args

        args = _parse_args([])
        assert args.skip_warmup is False

    def test_skip_warmup_enabled(self) -> None:
        """--skip-warmup flag sets skip_warmup to True."""
        from src.inference.server import _parse_args

        args = _parse_args(["--skip-warmup"])
        assert args.skip_warmup is True
