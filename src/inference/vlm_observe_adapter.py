"""VLM observe adapter — bridges the fps-play-agent-harness /observe protocol to
our cloud VLM backends (kimi-k2.6 multimodal by default, configurable).

The harness (src/perception/vlm-client.mjs) POSTs:

    {
      schema_version: "agent_harness.perception_request.v1",
      request_id, task_type, base, prompt, max_output_tokens,
      images: [{ mime_type, data_base64 }]
    }

and expects back { request_id, raw_text, model_id, adapter_id, latency_ms,
input_tokens, generated_tokens, output_token_budget }.

This adapter decodes the base64 images, builds an OpenAI-compatible multimodal
message, calls the configured backend, and returns raw_text (the model's JSON
observation) for the harness's guarded parser.

Usage:
    python src/inference/vlm_observe_adapter.py [--port 8765]
    # backend selection via env:
    #   VLM_BACKEND=kimi (default) | qwen | local
    #   KIMI_API_KEY / QWEN_API_KEY / LOCAL_VLM_ENDPOINT
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("vlm-observe-adapter")

app = FastAPI(title="vlm-observe-adapter")

_MIME_BY_TYPE = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}

SYSTEM_PROMPT = (
    "You are a game-frame observer. Read the frame(s) and the task instruction, "
    "then respond with EXACTLY one JSON object matching the required shape. "
    "Never output markdown fences, prose outside JSON, or extra keys."
)


def _extract_json(text: str) -> str:
    """Best-effort extraction of the largest JSON object from model output
    (models sometimes wrap JSON in prose or markdown fences)."""
    if not text:
        return text
    cleaned = text.strip()
    # Strip markdown fences.
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned
        cleaned = cleaned.strip().lstrip("json").strip()
    # Balanced-brace scan from each '{' position, keep the most complete object.
    best = None
    for i, ch in enumerate(cleaned):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for j in range(i, len(cleaned)):
            c = cleaned[j]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[i : j + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
                    break
    if best is None:
        # Fallback: everything between first { and last }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            best = cleaned[start : end + 1]
    return best if best is not None else text


def _call_backend(messages: list[dict], max_tokens: int) -> tuple[str, dict]:
    """Call the configured VLM backend. Returns (raw_text, usage_meta)."""
    backend = os.environ.get("VLM_BACKEND", "kimi").lower()
    if backend == "kimi":
        api_key = os.environ.get("KIMI_API_KEY", "")
        base = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
        model = os.environ.get("KIMI_VISION_MODEL", "kimi-k2.6")
    elif backend == "qwen":
        api_key = os.environ.get("QWEN_API_KEY", "")
        base = os.environ.get("QWEN_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        model = os.environ.get("QWEN_VISION_MODEL", "qwen3.7-max")
    elif backend == "opencodego":
        api_key = os.environ.get("OPENCODEGO_API_KEY", "")
        base = os.environ.get("OPENCODEGO_BASE_URL", "https://opencode.ai/zen/go/v1")
        model = os.environ.get("OPENCODEGO_VISION_MODEL", "mimo-v2.5")
    else:
        raise ValueError(f"unknown VLM_BACKEND: {backend}")
    if not api_key:
        raise RuntimeError(f"no api key configured for VLM backend '{backend}'")

    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        "max_tokens": max_tokens,
    }
    # kimi-k2.6 only allows temperature=1; other backends default fine.
    if not model.lower().startswith("kimi"):
        body["temperature"] = 0.0
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 vlm-observe-adapter",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("VLM_TIMEOUT_S", "180"))) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"backend HTTP {exc.code}: {detail}") from exc
    choice = payload.get("choices", [{}])[0].get("message", {})
    raw_text = choice.get("content") or choice.get("reasoning_content") or ""
    usage = payload.get("usage") or {}
    # Strip prose/markdown so the harness's guarded parser sees pure JSON.
    extracted = _extract_json(raw_text)
    if extracted != raw_text:
        logger.info("extracted JSON from model output (%d -> %d chars)", len(raw_text), len(extracted))
    return extracted, {
        "model_id": payload.get("model", model),
        "input_tokens": usage.get("prompt_tokens"),
        "generated_tokens": usage.get("completion_tokens"),
    }


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "vlm-observe-adapter", "backend": os.environ.get("VLM_BACKEND", "kimi")})


@app.post("/observe")
async def observe(request: Request) -> JSONResponse:
    started = time.time()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)

    request_id = payload.get("request_id") or "unknown"
    prompt = payload.get("prompt") or ""
    max_tokens = int(payload.get("max_output_tokens") or 384)
    images = payload.get("images") or []

    content: list[dict] = []
    for img in images:
        mime = img.get("mime_type", "image/jpeg")
        data_b64 = img.get("data_base64", "")
        # Re-encode as a data URL for the OpenAI-compatible API.
        content.append({"type": "image_url", "image_url": f"data:{mime};base64,{data_b64}"})
    if not content:
        # Fallback: prompt-only (text) observation.
        content = [{"type": "text", "text": prompt}]
    else:
        content = [{"type": "text", "text": prompt}, *content]

    try:
        raw_text, meta = _call_backend([{"role": "user", "content": content}], max_tokens)
    except Exception as exc:
        logger.error("observe %s failed: %s", request_id, exc)
        return JSONResponse(
            {"error": str(exc), "request_id": request_id, "raw_text": "{}"}, status_code=502
        )

    latency_ms = int((time.time() - started) * 1000)
    return JSONResponse({
        "request_id": request_id,
        "raw_text": raw_text,
        "model_id": meta.get("model_id", "unknown"),
        "adapter_id": "vlm-observe-adapter",
        "latency_ms": latency_ms,
        "input_tokens": meta.get("input_tokens"),
        "generated_tokens": meta.get("generated_tokens"),
        "output_token_budget": max_tokens,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("VLM_PORT", "8765")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    logger.info("vlm-observe-adapter listening on %s:%s (backend=%s)", args.host, args.port, os.environ.get("VLM_BACKEND", "kimi"))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
