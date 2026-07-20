#!/usr/bin/env python3
"""Auto-fill tap-only profiles for B-class (non-joystick) games.

Reads ``configs/auto_calibrated_profiles.json`` and adds ``tap-only`` profiles
to ``configs/game_profiles.py`` for games that failed joystick calibration
(B 类: tap-to-move / auto-movement).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CAL_JSON = ROOT / "configs" / "auto_calibrated_profiles.json"
PROFILES_PY = ROOT / "configs" / "game_profiles.py"


def _b_class_games() -> list[str]:
    """Return game IDs that failed calibration (B 类)."""
    if not CAL_JSON.is_file():
        return []
    data = json.loads(CAL_JSON.read_text(encoding="utf-8"))
    return [r["game_id"] for r in data if not r.get("valid")]


def _already_has_profile(game_id: str) -> bool:
    text = PROFILES_PY.read_text(encoding="utf-8")
    return f'"{game_id}"' in text


def _tap_only_entry(game_id: str) -> str:
    return f'''    "{game_id}": {{
        "game_id": "{game_id}",
        "label": "auto-tap-only",
        "file_pattern": "{game_id}",
        "joystick": {{
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        }},
        "calibration": {{
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {{
                "screen_right": {{"x": 0, "z": 0}},
                "screen_down": {{"x": 0, "z": 0}},
            }},
        }},
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    }},
'''


def main() -> int:
    b_games = _b_class_games()
    if not b_games:
        print("No B-class games found in auto_calibrated_profiles.json")
        return 0

    text = PROFILES_PY.read_text(encoding="utf-8")

    added = 0
    for gid in b_games:
        if _already_has_profile(gid):
            print(f"  [skip] {gid}: already has profile")
            continue

        entry = _tap_only_entry(gid)
        # Insert before the closing } of GAME_PROFILES
        marker = "\n}\n\n\n#:"
        idx = text.find(marker)
        if idx == -1:
            m = re.search(r"\n\}\n", text)
            idx = m.start() if m else text.rfind("}")
        text = text[:idx] + "\n" + entry + text[idx:]
        added += 1
        print(f"  [added] {gid} → tap-only")

    if added > 0:
        PROFILES_PY.write_text(text, encoding="utf-8")
        print(f"\nAdded {added} tap-only profiles to game_profiles.py")
    else:
        print("No new profiles to add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
