"""Shared fixtures for inference server tests.

Mocks ``torch``, ``transformers``, and ``peft`` modules in ``sys.modules``
so tests run without PyTorch installed.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# torch mock module (injected into sys.modules)
# ---------------------------------------------------------------------------

def _build_mock_torch() -> mock.MagicMock:
    """Return a comprehensive ``torch`` mock with cuda, dtype, and generate."""
    torch_mock = mock.MagicMock(name="torch")
    torch_mock.__version__ = "2.5.0+mock"

    torch_mock.float32 = "float32"
    torch_mock.bfloat16 = "bfloat16"
    torch_mock.float16 = "float16"

    cuda = mock.MagicMock(name="cuda")
    cuda.is_available.return_value = True
    cuda.device_count.return_value = 1
    mock_props = mock.MagicMock(name="device_properties")
    mock_props.total_mem = 32 * 1024**3  # 32 GiB
    cuda.get_device_properties.return_value = mock_props
    cuda.current_device.return_value = 0
    torch_mock.cuda = cuda

    torch_mock.inference_mode = mock.MagicMock()
    torch_mock.inference_mode.return_value.__enter__ = mock.MagicMock()
    torch_mock.inference_mode.return_value.__exit__ = mock.MagicMock()

    torch_mock.manual_seed = mock.MagicMock()
    return torch_mock


# ---------------------------------------------------------------------------
# transformers mock
# ---------------------------------------------------------------------------

def _build_mock_transformers(
    model_mock, processor_mock,
) -> mock.MagicMock:
    """Build a ``transformers`` module mock with Qwen, Gemma, AutoProcessor, BitsAndBytes."""
    mod = mock.MagicMock(name="transformers")

    mod.Qwen3_5ForConditionalGeneration = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=model_mock),
    )
    mod.AutoModelForVision2Seq = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=model_mock),
    )
    mod.AutoProcessor = mock.MagicMock(
        from_pretrained=mock.MagicMock(return_value=processor_mock),
    )
    mod.BitsAndBytesConfig = mock.MagicMock(return_value=mock.MagicMock())
    return mod


def _build_mock_peft(model_mock) -> mock.MagicMock:
    """Build a ``peft`` module mock with PeftModel."""
    mod = mock.MagicMock(name="peft")
    merged = mock.MagicMock()
    merged.merge_and_unload.return_value = model_mock
    mod.PeftModel = mock.MagicMock(from_pretrained=mock.MagicMock(return_value=merged))
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_ml_modules() -> None:
    """Inject mock ``torch``, ``transformers``, and ``peft`` into sys.modules."""
    if "torch" not in sys.modules:
        sys.modules["torch"] = _build_mock_torch()

    sys.modules["torch"].cuda.is_available.return_value = True
    sys.modules["torch"].cuda.device_count.return_value = 1
