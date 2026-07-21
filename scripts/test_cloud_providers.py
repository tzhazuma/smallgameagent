#!/usr/bin/env python3
"""Smoke-test all configured cloud providers.

Sends a simple text prompt and, for vision-capable providers, a small
multimodal request to verify connectivity, model availability, and JSON
structured-output stability. Results are written to
``cloud_provider_smoke_results.json``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent.api_client import MultiProviderClient  # noqa: E402

OUTPUT = ROOT / "cloud_provider_smoke_results.json"

PROVIDERS = ["opencodego", "kimi", "deepseek", "xiaomi", "qwen"]

TEXT_PROMPT = [
    {
        "role": "user",
        "content": 'Give me a JSON object like {"provider_name":"xiaomi","status":"ok"}',
    },
]


def _extract_json(content: str) -> dict | None:
    """Best-effort extract the first JSON object from model output."""
    content = content.strip()
    if not content:
        return None
    # Strip markdown fences if present
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _test_provider(name: str) -> dict:
    result = {
        "provider": name,
        "configured": False,
        "text_ok": False,
        "text_latency_s": None,
        "text_error": "",
        "text_raw": "",
        "vision_ok": False,
        "vision_latency_s": None,
        "vision_error": "",
        "vision_raw": "",
    }
    prefix = name.upper()
    api_key = os.environ.get(f"{prefix}_API_KEY", "")
    if not api_key and name == "opencodego":
        api_key = os.environ.get("OPENCODE_API_KEY", "")
    if not api_key:
        result["text_error"] = "API key not configured"
        return result
    result["configured"] = True

    # Text test
    try:
        t0 = time.time()
        client = MultiProviderClient(provider=name)
        resp = client.chat(TEXT_PROMPT, max_tokens=256, temperature=0.0)
        latency = time.time() - t0
        content = resp.choices[0].message.content or ""
        result["text_raw"] = content[:500]
        parsed = _extract_json(content)
        result["text_ok"] = isinstance(parsed, dict) and len(parsed) > 0
        result["text_latency_s"] = round(latency, 2)
        if not result["text_ok"]:
            result["text_error"] = f"no valid JSON dict (length={len(content)})"
    except Exception as exc:
        result["text_error"] = str(exc)[:200]

    # Vision test (only if text succeeded and provider has vision model)
    if result["text_ok"]:
        try:
            t0 = time.time()
            client = MultiProviderClient(provider=name)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image briefly as JSON with keys: objects (list), color (string)."},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}},
                    ],
                }
            ]
            resp = client.chat_with_vision(messages, max_tokens=256)
            latency = time.time() - t0
            content = resp.choices[0].message.content or ""
            result["vision_raw"] = content[:500]
            parsed = _extract_json(content)
            result["vision_ok"] = isinstance(parsed, dict) and len(parsed) > 0
            result["vision_latency_s"] = round(latency, 2)
            if not result["vision_ok"]:
                result["vision_error"] = "no valid JSON dict"
        except Exception as exc:
            result["vision_error"] = str(exc)[:200]

    return result


def main() -> int:
    results = [_test_provider(name) for name in PROVIDERS]
    summary = {
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "providers": results,
        "available_text": [r["provider"] for r in results if r["text_ok"]],
        "available_vision": [r["provider"] for r in results if r["vision_ok"]],
    }
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Provider smoke test results → {OUTPUT}\n")
    for r in results:
        status = "✅" if r["text_ok"] else "❌"
        print(f"{status} {r['provider']:12s} text={r['text_ok']} ({r['text_latency_s']}s)  vision={r['vision_ok']}  error={r['text_error'] or r['vision_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
