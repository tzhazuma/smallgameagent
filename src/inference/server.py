"""FastAPI inference server for the fine-tuned VLM game-playing model.

Provides a :class:`GameAgentInference` engine that loads Qwen3.5-4B or
Gemma-4-E4B with a LoRA adapter, plus a FastAPI app exposing synchronous
and asynchronous prediction endpoints.

Usage::

    python src/inference/server.py \
        --model Qwen/Qwen3.5-4B \
        --adapter checkpoints/qwen35-4b-gameplay/final \
        --port 8000

Hardware:
  - ssh5090 (4× RTX 5090 32 GB): full bfloat16 loading.
  - Local RTX 4060 (8 GB): 4-bit NF4 quantisation via BitsAndBytes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import torch as torch  # noqa: F811
    from PIL.Image import Image as PILImage

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inference")

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

_MODEL_CATALOG = frozenset({"qwen35-4b", "gemma4-e4b"})


class ActionParams(BaseModel):
    """Parameters for a single action."""

    dx: float | None = Field(
        default=None, ge=-1.0, le=1.0, description="Normalised joystick X (-1 left, 1 right)"
    )
    dy: float | None = Field(
        default=None, ge=-1.0, le=1.0, description="Normalised joystick Y (-1 up, 1 down)"
    )
    duration_ms: int = Field(default=320, ge=0, description="Action duration in milliseconds")
    x: float | None = Field(default=None, description="Tap X coordinate")
    y: float | None = Field(default=None, description="Tap Y coordinate")


class PredictResponse(BaseModel):
    """Structured prediction returned by the VLM."""

    action: str = Field(
        ..., description="Action type: 'move', 'tap', or 'wait'",
        pattern=r"^(move|tap|wait)$",
    )
    params: ActionParams = Field(default_factory=ActionParams)
    reason: str = Field(default="", description="Human-readable rationale")
    model_name: str = Field(default="", description="Model identifier")
    latency_ms: float = Field(default=0.0, description="Inference wall-clock time (ms)")


class HealthResponse(BaseModel):
    """Health-check endpoint response."""

    status: str = Field(default="ok")
    model: str = Field(default="")
    device: str = Field(default="cpu")
    ready: bool = Field(default=False)
    torch_version: str = Field(default="")
    cuda_available: bool = Field(default=False)
    gpu_count: int = Field(default=0)
    model_loaded: bool = Field(default=False, description="Whether the model is loaded")
    warmup_complete: bool = Field(default=False, description="Whether warmup has finished")
    vram_mb: float | None = Field(default=None, description="Allocated VRAM in MB")
    uptime_s: float = Field(default=0.0, description="Server uptime in seconds")
    model_name: str = Field(default="", description="Short model identifier")


# ---------------------------------------------------------------------------
# Prompt template (chat-format compatible)
# ---------------------------------------------------------------------------

_INFERENCE_SYSTEM_PROMPT = (
    "You are a fine-tuned game-playing agent.  You receive a screenshot of "
    "the current game frame plus a JSON dump of the game state.  Output "
    "**only** a single JSON object with keys 'action', 'params', and "
    "'reason'.  No markdown fences, no extra commentary.\n\n"
    "## Available Actions\n"
    "- move:  Joystick drag.  params: {'dx': float(-1..1), 'dy': float(-1..1), 'duration_ms': int}\n"
    "- tap:   Screen tap.  params: {'x': float, 'y': float, 'duration_ms': int}\n"
    "- wait:  Do nothing.   params: {'duration_ms': int}\n"
)

_USER_TEXT_TEMPLATE = "Game state:\n{state_json}\n\nWhat is the best next action?"

# ---------------------------------------------------------------------------
# Model catalog helpers
# ---------------------------------------------------------------------------

_NORMALISE = re.compile(r"[^a-z0-9-]+")


def _normalise_model_id(raw: str) -> str:
    """Collapse a HuggingFace model ID into a short canonical key."""
    return _NORMALISE.sub("", raw.lower())


def _resolve_short_name(model_path_or_id: str) -> str:
    """Given a path or HF id, return a human-readable short name."""
    path = Path(model_path_or_id)
    if path.is_dir():
        # Attempt to find training_metadata.json → base_model
        meta_path = path / "training_metadata.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                base = meta.get("base_model", "")
                if base:
                    return _normalise_model_id(base.split("/")[-1])
            except (json.JSONDecodeError, OSError):
                pass
        return _normalise_model_id(path.name)
    # HF hub ID
    return _normalise_model_id(model_path_or_id.split("/")[-1])


def _detect_model_family(model_id: str) -> str:
    """Return 'qwen35' or 'gemma4' based on the model identifier."""
    norm = _normalise_model_id(model_id)
    if "gemma" in norm or "gemma4" in norm:
        return "gemma4"
    return "qwen35"  # default


# ---------------------------------------------------------------------------
# GameAgentInference — model loading & prediction
# ---------------------------------------------------------------------------


class GameAgentInference:
    """Load a fine-tuned VLM and expose synchronous / asynchronous prediction.

    Parameters
    ----------
    model_path:
        HuggingFace model ID (e.g. ``"Qwen/Qwen3.5-4B"``) or local
        checkpoint directory containing an ``adapter_config.json``.
    adapter_path:
        Optional path to a LoRA adapter (PEFT checkpoint).  When provided,
        the base model is loaded first and the adapter is merged / wrapped
        on top.  When *model_path* itself is a PEFT checkpoint, this may be
        the same directory.
    device:
        Torch device string (``"cuda"``, ``"cuda:0"``, ``"cpu"``).
        Overridden by ``device_map="auto"`` when multiple GPUs are available.
    use_4bit:
        Enable 4-bit NF4 quantisation via BitsAndBytes for memory-constrained
        GPUs (≥8 GB).  Automatically disabled when VRAM ≥24 GB or CPU-only.
    use_flash_attn:
        Enable Flash Attention 2 (default True; falls back to SDPA on error).
    max_new_tokens:
        Maximum generated tokens per prediction (default 256).
    temperature:
        Generation temperature (default 0.0 for greedy).
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        model_path: str,
        adapter_path: str | None = None,
        device: str = "cuda",
        use_4bit: bool = False,
        use_flash_attn: bool = True,
        max_new_tokens: int = 256,
        temperature: float = 0.0,
        skip_warmup: bool = False,
    ) -> None:
        self._model_path = model_path
        self._adapter_path = adapter_path
        self._device = device
        self._use_4bit = use_4bit
        self._use_flash_attn = use_flash_attn
        self._max_new_tokens = max_new_tokens
        self._temperature = temperature

        self._model_family = _detect_model_family(model_path)
        self._short_name = _resolve_short_name(model_path)
        self._loaded = False
        self._warmup_complete: bool = False
        self._start_time: float = time.time()

        # These are set by _load_model.
        self._model: Any = None
        self._processor: Any = None

        self._skip_warmup = skip_warmup
        self._load_model()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Load the VLM + LoRA adapter into GPU memory.

        Auto-detects whether the path is a PEFT checkpoint (contains
        ``adapter_config.json``) vs a raw base model.  When 4-bit is
        requested and VRAM is sufficient on the primary GPU the model is
        quantised via ``BitsAndBytesConfig``.
        """
        import torch

        logger.info(
            "Loading model: path=%s adapter=%s family=%s 4bit=%s flash_attn=%s",
            self._model_path,
            self._adapter_path,
            self._model_family,
            self._use_4bit,
            self._use_flash_attn,
        )

        # ── Resolve device map ─────────────────────────────────────────
        gpu_count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        if gpu_count > 1:
            device_map: str | dict[str, str] = "auto"
        else:
            device_map = {"": self._device if torch.cuda.is_available() else "cpu"}

        # ── Auto-detect 4-bit based on VRAM ─────────────────────────────
        use_quant = self._use_4bit
        if not use_quant and torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            if vram_gb < 12.0:  # RTX 4060 8 GB → enable quant
                use_quant = True
                logger.info("VRAM %.1f GB < 12 GB → enabling 4-bit quantisation", vram_gb)

        quant_config: Any = None
        torch_dtype: torch.dtype = torch.float32 if device_map == "cpu" else torch.bfloat16

        if use_quant and torch.cuda.is_available():
            from transformers import BitsAndBytesConfig

            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            logger.info("Using 4-bit NF4 quantisation")

        # ── Resolve attention implementation ────────────────────────────
        attn_impl = "sdpa"
        if self._use_flash_attn and torch.cuda.is_available():
            attn_impl = "flash_attention_2"

        # ── Load base model ─────────────────────────────────────────────
        base_model = self._load_base_model(
            self._model_path,
            device_map=device_map,
            torch_dtype=torch_dtype,
            quant_config=quant_config,
            attn_impl=attn_impl,
        )

        # ── Load LoRA adapter ───────────────────────────────────────────
        adapter_dir = self._adapter_path or self._model_path
        adapter_path_obj = Path(adapter_dir)

        if self._model_family == "gemma4" and getattr(base_model, "peft_config", None):
            model = base_model
            logger.info("Gemma4 LoRA already loaded from train_gemma4")
        elif (adapter_path_obj / "adapter_config.json").is_file():
            from peft import PeftModel
            logger.info("Loading LoRA from %s", adapter_dir)
            model = PeftModel.from_pretrained(base_model, str(adapter_dir))
        else:
            model = base_model
            logger.info("No adapter found, using base model")
        # ── Load processor ──────────────────────────────────────────────
        from transformers import AutoProcessor

        processor = AutoProcessor.from_pretrained(
            self._model_path, trust_remote_code=True,
        )
        # Clamp image resolution (Qwen-specific)
        if hasattr(processor, "image_processor"):
            if hasattr(processor.image_processor, "max_pixels"):
                processor.image_processor.max_pixels = 1_003_520  # ~980×1024
            if hasattr(processor.image_processor, "min_pixels"):
                processor.image_processor.min_pixels = 256 * 256

        self._model = model
        self._processor = processor
        self._loaded = True
        logger.info("Model loaded successfully (%s)", self._short_name)

        if not self._skip_warmup:
            self._warmup()

    def _load_base_model(
        self,
        model_id: str,
        device_map: str | dict[str, str],
        torch_dtype: torch.dtype,
        quant_config: Any,
        attn_impl: str,
    ) -> Any:
        """Load the base VLM with the correct architecture class."""
        kwargs: dict[str, Any] = {
            "device_map": device_map,
            "torch_dtype": torch_dtype,
            "trust_remote_code": True,
        }
        if quant_config is not None:
            kwargs["quantization_config"] = quant_config
        if True:  # always pass attn_implementation explicitly
            kwargs["attn_implementation"] = attn_impl

        if self._model_family == "qwen35":
            return self._load_qwen35(model_id, kwargs)
        return self._load_gemma4(model_id, kwargs)

    
    @staticmethod
    def _load_qwen35(model_id: str, kwargs: dict[str, Any]) -> Any:
        """Load Qwen3.5-4B with Qwen3_5ForConditionalGeneration, fallback to
        AutoModelForMultimodalLM."""
        try:
            from transformers import Qwen3_5ForConditionalGeneration

            logger.info("Loading Qwen3.5-4B via Qwen3_5ForConditionalGeneration")
            return Qwen3_5ForConditionalGeneration.from_pretrained(model_id, **kwargs)
        except (ValueError, ImportError, OSError):
            logger.warning(
                "Qwen3_5ForConditionalGeneration unavailable; falling back to "
                "AutoModelForMultimodalLM",
            )
            from transformers import AutoModelForMultimodalLM

            return AutoModelForMultimodalLM.from_pretrained(model_id, **kwargs)

    
    @staticmethod
    def _load_gemma4(model_id: str, kwargs: dict[str, Any]) -> Any:
        """Load Gemma-4-E4B with ClippableLinear replacement for PEFT."""
        import torch
        import transformers.models.gemma4.modeling_gemma4 as _gemma_model

        # Monkey-patch Gemma4VisionModel for bool pixel_position_ids
        _orig_vision_forward = _gemma_model.Gemma4VisionModel.forward
        def _patched_vision_forward(self, pixel_values, pixel_position_ids=None, **kw):
            if isinstance(pixel_position_ids, bool) or pixel_position_ids is None:
                if pixel_values is not None and isinstance(pixel_values, torch.Tensor):
                    bsz = pixel_values.shape[0]
                    num_patches = pixel_values.shape[1]
                    side = int(num_patches ** 0.5)
                    while side > 0:
                        if num_patches % side == 0:
                            break
                        side -= 1
                    h, w = side, num_patches // side
                    y_coords = torch.arange(h, device=pixel_values.device).repeat_interleave(w)
                    x_coords = torch.arange(w, device=pixel_values.device).repeat(h)
                    grid = torch.stack([x_coords, y_coords], dim=-1)
                    pixel_position_ids = grid.unsqueeze(0).expand(bsz, -1, -1).contiguous()
                else:
                    pixel_position_ids = None
            return _orig_vision_forward(self, pixel_values, pixel_position_ids=pixel_position_ids, **kw)
        _gemma_model.Gemma4VisionModel.forward = _patched_vision_forward

        from transformers import AutoModelForMultimodalLM
        logger.info("Loading Gemma-4-E4B base model")
        model = AutoModelForMultimodalLM.from_pretrained(model_id, **kwargs)

        # Replace Gemma4ClippableLinear with standard Linear for PEFT
        def replace_clippable(module, parent_name=""):
            for name, child in list(module.named_children()):
                child_name = parent_name + "." + name if parent_name else name
                if type(child).__name__ == "Gemma4ClippableLinear":
                    setattr(module, name, child.linear)
                else:
                    replace_clippable(child, child_name)

        replace_clippable(model)
        logger.info("Replaced Gemma4ClippableLinear -> Linear (PEFT compat)")
        return model

    # ------------------------------------------------------------------
    # Warmup
    # ------------------------------------------------------------------

    def _warmup(self) -> None:
        """Run dummy inference passes to trigger JIT compilation and CUDA warmup.

        Creates a tiny black 1×1 image and calls :meth:`predict` three
        times, then clears the CUDA cache.
        """
        from PIL import Image

        logger.info("Starting model warmup (3 passes)...")
        t0 = time.perf_counter()

        dummy_image = Image.new("RGB", (1, 1), color=0)
        dummy_state: dict[str, Any] = {"ready": True, "_warmup": True}

        for i in range(3):
            self.predict(dummy_image, dummy_state)
            logger.debug("Warmup pass %d/3 complete", i + 1)

        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        elapsed = round(time.perf_counter() - t0, 2)
        self._warmup_complete = True
        logger.info("Warmup complete in %.2fs", elapsed)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, screenshot: PILImage, state_json: dict[str, Any] | str) -> dict[str, Any]:
        """Synchronous prediction — accepts a PIL Image and game state.

        Parameters
        ----------
        screenshot:
            A PIL Image of the current game frame.
        state_json:
            A ``dict`` (or pre-serialised JSON ``str``) with the latest
            Cocos probe output.

        Returns
        -------
        dict
            ``{"action": "move", "params": {...}, "reason": "..."}``
        """
        import torch

        if not self._loaded:
            raise RuntimeError("Model not loaded")

        t0 = time.perf_counter()

        # ── Serialise state ─────────────────────────────────────────────
        state_str: str
        if isinstance(state_json, dict):
            state_str = json.dumps(state_json, indent=2, default=str, ensure_ascii=False)
        else:
            state_str = state_json

        # ── Build chat messages ─────────────────────────────────────────
        messages = [
            {"role": "system", "content": _INFERENCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": screenshot},
                    {"type": "text", "text": _USER_TEXT_TEMPLATE.format(state_json=state_str)},
                ],
            },
        ]

        # ── Apply chat template ─────────────────────────────────────────
        prompt_text: str = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        # ── Tokenise ────────────────────────────────────────────────────
        inputs = self._processor(
            text=prompt_text,
            images=[screenshot],
            return_tensors="pt",
        ).to(self._model.device)

        # ── Generate ────────────────────────────────────────────────────
        with torch.inference_mode():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=self._max_new_tokens,
                temperature=self._temperature if self._temperature > 0 else None,
                do_sample=self._temperature > 0,
                pad_token_id=self._processor.tokenizer.pad_token_id,
                eos_token_id=self._processor.tokenizer.eos_token_id,
            )

        # ── Decode ──────────────────────────────────────────────────────
        # Strip input tokens — only keep newly generated ones.
        input_len = inputs["input_ids"].shape[1]
        generated_ids = outputs[0][input_len:]
        raw_output: str = self._processor.decode(generated_ids, skip_special_tokens=True)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        logger.debug("Inference took %.1f ms", latency_ms)

        # ── Parse JSON ──────────────────────────────────────────────────
        parsed = self._parse_model_output(raw_output)
        parsed.setdefault("model_name", self._short_name)
        parsed.setdefault("latency_ms", round(latency_ms, 1))
        return parsed

    async def predict_async(
        self, screenshot: PILImage, state_json: dict[str, Any] | str,
    ) -> dict[str, Any]:
        """Asynchronous prediction — wraps :meth:`predict` for FastAPI.

        Offloads the CPU-bound model call to a thread-pool executor so the
        event loop is not blocked during generation.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.predict, screenshot, state_json)

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    _JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
    _JSON_BARE_RE = re.compile(r"\{[\s\S]*\}")

    @classmethod
    def _parse_model_output(cls, text: str) -> dict[str, Any]:
        """Extract a JSON action dict from raw model output.

        Handles markdown fences, surrounding text, and missing fields.
        Always returns a valid dict with at least ``action``, ``params``,
        and ``reason`` keys.
        """
        fallback: dict[str, Any] = {
            "action": "wait",
            "params": {"duration_ms": 500},
            "reason": "Unable to parse model output",
        }

        if not text or not text.strip():
            return fallback

        cleaned = cls._JSON_FENCE_RE.sub("", text).strip()

        if not cleaned.startswith("{"):
            match = cls._JSON_BARE_RE.search(cleaned)
            if match:
                cleaned = match.group(0)
            else:
                logger.warning("No JSON object found in: %s", text[:200])
                return fallback

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed for: %s", cleaned[:200])
            return fallback

        if not isinstance(parsed, dict):
            return fallback

        return {
            "action": parsed.get("action", "wait"),
            "params": parsed.get("params", {}),
            "reason": parsed.get("reason", ""),
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        """Short name of the loaded model (e.g. ``"qwen35-4b"``)."""
        return self._short_name

    @property
    def device(self) -> str:
        """Device the model is on (e.g. ``"cuda:0"``)."""
        if self._model is not None:
            return str(next(self._model.parameters()).device)
        return "cpu"

    @property
    def is_ready(self) -> bool:
        """Whether the model is loaded and ready for inference."""
        return self._loaded


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

# Global singleton — initialised at startup via the CLI or ASGI lifespan.
_inference_engine: GameAgentInference | None = None

app = FastAPI(
    title="SmallGameAgent Inference",
    description="Local VLM inference server for game-playing agents",
    version="0.1.0",
)


def _engine() -> GameAgentInference:
    """Get the global inference engine, raising if not initialised."""
    if _inference_engine is None:
        raise HTTPException(status_code=503, detail="Inference engine not yet initialised")
    return _inference_engine


# ---------------------------------------------------------------------------
# POST /predict
# ---------------------------------------------------------------------------


@app.post("/predict", response_model=PredictResponse)
async def predict_endpoint(
    screenshot: UploadFile = File(..., description="PNG screenshot of the current game frame"),
    state: str = Form(..., description="JSON-encoded game state from the Cocos probe"),
) -> PredictResponse:
    """Accept a game screenshot + state JSON and return a predicted action.

    - **screenshot**: PNG image file (multipart).
    - **state**: JSON string containing the game state (probe output).
    """
    from PIL import Image

    engine = _engine()

    # ── Validate image ──────────────────────────────────────────────────
    if screenshot.content_type not in (None, "image/png", "image/jpeg", "image/webp"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {screenshot.content_type}. Use PNG, JPEG, or WebP.",
        )

    try:
        image_bytes = await screenshot.read()
        pil_image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    # ── Validate state JSON ─────────────────────────────────────────────
    try:
        state_dict: dict[str, Any] = json.loads(state)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state JSON: {exc}") from exc

    if not isinstance(state_dict, dict):
        raise HTTPException(status_code=400, detail="State must be a JSON object")

    # ── Predict ─────────────────────────────────────────────────────────
    try:
        result = await engine.predict_async(pil_image, state_dict)
    except Exception as exc:
        logger.exception("Prediction failed")
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc

    return PredictResponse(**result)


# ---------------------------------------------------------------------------
# POST /predict/stream (SSE streaming)
# ---------------------------------------------------------------------------


@app.post("/predict/stream")
async def predict_stream_endpoint(
    screenshot: UploadFile = File(..., description="PNG screenshot of the current game frame"),
    state: str = Form(..., description="JSON-encoded game state from the Cocos probe"),
) -> StreamingResponse:
    """Accept a game screenshot + state JSON and stream tokens via SSE.

    Produces ``text/event-stream`` with one ``data:`` line per token and a
    final event containing the full parsed action.
    """
    from PIL import Image

    engine = _engine()

    # ── Validate image ──────────────────────────────────────────────────
    if screenshot.content_type not in (None, "image/png", "image/jpeg", "image/webp"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {screenshot.content_type}. Use PNG, JPEG, or WebP.",
        )

    try:
        image_bytes = await screenshot.read()
        pil_image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    # ── Validate state JSON ─────────────────────────────────────────────
    try:
        state_dict: dict[str, Any] = json.loads(state)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state JSON: {exc}") from exc

    if not isinstance(state_dict, dict):
        raise HTTPException(status_code=400, detail="State must be a JSON object")

    async def _event_generator() -> str:  # type: ignore[misc]  # generator yields str
        import torch

        t0 = time.perf_counter()

        # ── Preprocess (same as /predict) ───────────────────────────────
        state_str = json.dumps(state_dict, indent=2, default=str, ensure_ascii=False)
        messages = [
            {"role": "system", "content": _INFERENCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": _USER_TEXT_TEMPLATE.format(state_json=state_str)},
                ],
            },
        ]
        prompt_text = engine._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = engine._processor(
            text=prompt_text, images=[pil_image], return_tensors="pt",
        ).to(engine._model.device)

        # ── Try streaming ───────────────────────────────────────────────
        raw_output = ""
        streamed = False

        try:
            from transformers import TextIteratorStreamer

            streamer = TextIteratorStreamer(
                engine._processor.tokenizer, skip_prompt=True,
            )
            gen_kwargs: dict[str, Any] = {
                **inputs,
                "streamer": streamer,
                "max_new_tokens": engine._max_new_tokens,
                "temperature": engine._temperature if engine._temperature > 0 else None,
                "do_sample": engine._temperature > 0,
                "pad_token_id": engine._processor.tokenizer.pad_token_id,
                "eos_token_id": engine._processor.tokenizer.eos_token_id,
            }

            thread = Thread(target=engine._model.generate, kwargs=gen_kwargs)
            thread.start()

            for token_text in streamer:
                raw_output += token_text
                yield f"data: {json.dumps({'token': token_text})}\n\n"

            thread.join()
            streamed = True

        except (ImportError, AttributeError, TypeError, RuntimeError) as exc:
            logger.warning("Streaming not supported, falling back: %s", exc)

        if not streamed:
            with torch.inference_mode():
                outputs = engine._model.generate(
                    **inputs,
                    max_new_tokens=engine._max_new_tokens,
                    temperature=engine._temperature if engine._temperature > 0 else None,
                    do_sample=engine._temperature > 0,
                    pad_token_id=engine._processor.tokenizer.pad_token_id,
                    eos_token_id=engine._processor.tokenizer.eos_token_id,
                )
            input_len = inputs["input_ids"].shape[1]
            generated_ids = outputs[0][input_len:]
            raw_output = engine._processor.decode(generated_ids, skip_special_tokens=True)

        # ── Final event ─────────────────────────────────────────────────
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        parsed = engine._parse_model_output(raw_output)
        parsed["model_name"] = engine._short_name
        parsed["latency_ms"] = latency_ms
        parsed["done"] = True
        yield f"data: {json.dumps(parsed)}\n\n"

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_endpoint() -> HealthResponse:
    """Return server health status and model metadata."""
    import torch

    engine = _inference_engine

    vram_mb: float | None = None
    if torch.cuda.is_available():
        try:
            alloc = torch.cuda.memory_allocated()
            vram_mb = round(alloc / (1024**2), 1)
        except Exception:
            pass

    uptime_s = round(time.time() - engine._start_time, 1) if engine is not None else 0.0

    return HealthResponse(
        status="ok" if (engine is not None and engine.is_ready) else "loading",
        model=engine.model_name if engine is not None else "",
        device=engine.device if engine is not None else "cpu",
        ready=engine.is_ready if engine is not None else False,
        torch_version=torch.__version__,
        cuda_available=torch.cuda.is_available(),
        gpu_count=torch.cuda.device_count() if torch.cuda.is_available() else 0,
        model_loaded=engine.is_ready if engine is not None else False,
        warmup_complete=engine._warmup_complete if engine is not None else False,
        vram_mb=vram_mb,
        uptime_s=uptime_s,
        model_name=engine.model_name if engine is not None else "",
    )


# ASGI lifespan — load model on startup


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Load the inference engine once at startup, clean up at shutdown."""
    global _inference_engine
    # Engine is loaded externally via main(); this is a no-op placeholder.
    yield
    _inference_engine = None


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the VLM inference server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--model",
        type=str,
        default="Qwen/Qwen3.5-4B",
        help="HuggingFace model ID or local checkpoint path",
    )
    parser.add_argument(
        "--adapter",
        type=str,
        default="",
        help="Path to LoRA adapter (PEFT checkpoint directory)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda",
        help="Torch device (default: cuda)",
    )
    parser.add_argument(
        "--4bit",
        action="store_true",
        dest="use_4bit",
        help="Force 4-bit NF4 quantisation (auto-enabled when VRAM < 12 GB)",
    )
    parser.add_argument(
        "--no-flash-attn",
        action="store_true",
        dest="no_flash_attn",
        help="Disable Flash Attention 2 (fall back to SDPA)",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=256,
        help="Maximum generated tokens (default: 256)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Generation temperature (default: 0.0 greedy)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Skip model warmup (3 dummy inference passes)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    args = _parse_args(argv)
    adapter = args.adapter if args.adapter else None

    # Use the ASGI lifespan to initialise the engine.
    global _inference_engine

    logger.info(
        "Creating inference engine: model=%s adapter=%s 4bit=%s",
        args.model, adapter, args.use_4bit,
    )
    _inference_engine = GameAgentInference(
        model_path=args.model,
        adapter_path=adapter,
        device=args.device,
        use_4bit=args.use_4bit,
        use_flash_attn=not args.no_flash_attn,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        skip_warmup=args.skip_warmup,
    )

    # Swap lifespan with one that uses the already-loaded engine.
    @asynccontextmanager
    async def _loaded_lifespan(_app: FastAPI):
        yield

    app.router.lifespan_context = _loaded_lifespan

    logger.info("Starting server on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

# PEFT Gemma-4 patch - applied at module level
import peft.tuners.lora.model as _peft_lora_model
import peft.tuners.lora.bnb as _peft_lora_bnb
import bitsandbytes as _bnb

_orig_create_new_module = _peft_lora_model.LoraModel._create_new_module

def _new_module_patch(lora_config, adapter_name, target, **kwargs):
    cls_name = type(target).__name__
    if cls_name == "Gemma4ClippableLinear" and hasattr(target, "linear"):
        inner = target.linear
        if isinstance(inner, _bnb.nn.Linear4bit):
            kwargs.pop(device_map, None)
            result = _peft_lora_bnb.dispatch_bnb_4bit(inner, adapter_name, config=lora_config, **kwargs)
            if result is not None:
                result.linear = inner
                return result
    return _orig_create_new_module(lora_config, adapter_name, target, **kwargs)

_peft_lora_model.LoraModel._create_new_module = staticmethod(_new_module_patch)

