#!/usr/bin/env python3
"""Cloud API visual-structure extraction matrix.

Uses the working OpenCodeGo endpoint to extract the same structured visual
state from local game frames with the multimodal models requested by the team:

- mimo-v2.5 (vision)
- kimi-k2.6 (vision)

Records latency, token usage (when available), and whether the response can be
parsed into the canonical visual-structure JSON schema.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.agent.api_client import OpenCodeGoClient

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FRAMES = [ROOT / "_frame_0.png", ROOT / "_frame_1.png", ROOT / "_frame_2.png"]

STRUCT_PROMPT = (
    "You are a game state visual analyser. Given a screenshot of a mobile game frame, "
    "extract structured information about what you see. Return ONLY valid JSON with these fields:\n\n"
    '{"has_arrow": true/false, "arrow_screen_x": float or null, "arrow_screen_y": float or null, '
    '"arrow_world_dx": float or null, "arrow_world_dz": float or null, "has_target": true/false, '
    '"target_screen_x": float or null, "target_screen_y": float or null, "has_obstacle": true/false, '
    '"obstacle_screen_x": float or null, "obstacle_screen_y": float or null, '
    '"is_end_screen": true/false, "end_screen_type": "win"/"lose"/null, "ui_buttons": ["text", ...], '
    '"player_screen_x": float or null, "player_screen_y": float or null, '
    '"has_guide_indicator": true/false, "guide_direction": "up"/"down"/"left"/"right"/null, '
    '"extra_notes": "string"}\n\n'
    "Output only the JSON object, no markdown fences."
)


def _extract_json(text: str) -> dict | None:
    text = text or ""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _run_struct(client: OpenCodeGoClient, model: str, frame: Path) -> dict:
    image_url = OpenCodeGoClient.encode_image_base64(frame)
    t0 = time.perf_counter()
    try:
        resp = client.chat_with_vision(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": STRUCT_PROMPT},
                    ],
                }
            ],
            model=model,
            max_tokens=1024,
        )
        elapsed = time.perf_counter() - t0
        content = resp.choices[0].message.content or ""
        parsed = _extract_json(content)
        usage = getattr(resp, "usage", None)
        return {
            "model": model,
            "frame": frame.name,
            "ok": True,
            "latency_s": round(elapsed, 3),
            "parsed": parsed is not None,
            "extracted": parsed,
            "content_preview": content[:300],
            "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "model": model,
            "frame": frame.name,
            "ok": False,
            "latency_s": round(elapsed, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:400],
        }


def main() -> int:
    frames = [Path(f) for f in sys.argv[1:]] or DEFAULT_FRAMES
    missing = [f for f in frames if not f.exists()]
    if missing:
        print(f"Missing frames: {missing}", file=sys.stderr)
        return 1

    client = OpenCodeGoClient()
    models = ["mimo-v2.5", "kimi-k2.6"]
    results = []
    for model in models:
        for frame in frames:
            print(f"[{model}] {frame.name} ...", flush=True)
            results.append(_run_struct(client, model, frame))

    out_path = ROOT / "experiment_cloud_api_struct.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out_path}")
    for r in results:
        if r["ok"]:
            print(
                f"  [{r['model']}/{r['frame']}] parsed={r['parsed']} "
                f"latency={r['latency_s']}s"
            )
        else:
            print(f"  [{r['model']}/{r['frame']}] FAIL:{r['error_type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
