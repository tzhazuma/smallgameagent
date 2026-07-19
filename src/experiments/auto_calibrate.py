#!/usr/bin/env python3
"""Auto-calibrate screen→world coordinate mapping for unprofiled games.

Sends 4 cardinal joystick pulses (right, down, left, up) with return pulses,
measures world-position deltas, computes the ``screen_right`` / ``screen_down``
basis vectors, validates consistency, and writes the result into
``configs/game_profiles.py`` (and a standalone JSON for traceability).

Usage::

    python src/experiments/auto_calibrate.py \
        --games SSD_00482P01 SSD_00736P01 SSD_00342P01 SSD_00532P01

    # Or calibrate ALL unprofiled games under _extracted/games/:
    python src/experiments/auto_calibrate.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from configs.game_profiles import GAME_PROFILES
from src.agent.harness import GameRunner
from src.agent.probe_adapter import ProbeAdapter

ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = ROOT / "_extracted" / "games"
PROFILES_PY = ROOT / "configs" / "game_profiles.py"
CALIBRATED_JSON = ROOT / "configs" / "auto_calibrated_profiles.json"

# Default joystick parameters (from GENERIC_PROFILE)
DEFAULT_ANCHOR = [187, 650]
DEFAULT_RADIUS = 60

# Calibration pulse parameters
PULSE_MS = 650
RETURN_MS = 320
SETTLE_MS = 250

# Minimum world displacement to consider a pulse "moved"
MIN_MOVE = 0.1

# Consistency threshold: |delta_dir + delta_opposite| should be small
CONSISTENCY_THRESHOLD = 2.0

DIRECTIONS = [
    {"name": "right", "dx": 1, "dy": 0},
    {"name": "down", "dx": 0, "dy": 1},
    {"name": "left", "dx": -1, "dy": 0},
    {"name": "up", "dx": 0, "dy": -1},
]


def _resolve_html(game_id: str) -> str | None:
    """Find the HTML file for *game_id* under ``_extracted/games/``."""
    for child in GAMES_DIR.iterdir():
        if child.is_dir() and child.name.startswith(game_id):
            htmls = list(child.glob("*.html"))
            if htmls:
                return str(htmls[0])
    return None


def _all_unprofiled_games() -> list[str]:
    """Return game IDs from _extracted/games/ that have no tuned profile."""
    ids = []
    for child in GAMES_DIR.iterdir():
        if not child.is_dir():
            continue
        # Extract game_id prefix (SSD_XXXXXPXX)
        name = child.name
        parts = name.split("_")
        if len(parts) >= 2:
            gid = f"{parts[0]}_{parts[1]}"
        else:
            gid = name
        if gid not in GAME_PROFILES:
            ids.append(gid)
    return sorted(ids)


async def _get_world_pos(probe: ProbeAdapter, page: Any) -> dict[str, float] | None:
    """Get current player worldPosition from probe."""
    state = await probe.observe_fast(page)
    player = state.get("player") or {}
    wp = player.get("worldPosition")
    if wp and "x" in wp:
        return {"x": float(wp["x"]), "y": float(wp.get("y", 0)), "z": float(wp["z"])}
    return None


async def calibrate_game(
    game_id: str,
    html_path: str,
    anchor: list[int] | None = None,
    radius: int | None = None,
    pulse_ms: int = PULSE_MS,
    return_ms: int = RETURN_MS,
    settle_ms: int = SETTLE_MS,
) -> dict[str, Any]:
    """Calibrate one game by sending 4 cardinal joystick pulses.

    Returns a dict with ``basis``, ``samples``, ``valid``, ``drift``, etc.
    """
    anchor = anchor or DEFAULT_ANCHOR
    radius = radius or DEFAULT_RADIUS

    runner = GameRunner(headed=False)
    await runner.start()
    try:
        await runner.open_game(html_path)
        await asyncio.sleep(6)

        probe = ProbeAdapter()
        await probe.inject(runner._page)
        state = await probe.wait_for_ready(runner._page, timeout_ms=30_000)
        if not state.get("ready"):
            return {"game_id": game_id, "valid": False, "reason": "probe_not_ready"}

        # Wait a bit more for the game to fully initialise
        await asyncio.sleep(2)

        # --- Warmup: dismiss tutorial / overlay with dummy pulses ---
        for _ in range(3):
            await runner.joystick_pulse(
                dx=0, dy=1, duration_ms=300,
                anchor=tuple(anchor), radius=radius,
            )
            await asyncio.sleep(0.3)
        # Also try a tap in the center to dismiss "tap to start" screens
        await runner.tap(x=187, y=400, duration_ms=100)
        await asyncio.sleep(1.0)

        samples = []
        for direction in DIRECTIONS:
            before = await _get_world_pos(probe, runner._page)
            if before is None:
                samples.append({**direction, "error": "no_world_pos_before"})
                continue

            # Forward pulse
            await runner.joystick_pulse(
                dx=direction["dx"], dy=direction["dy"],
                duration_ms=pulse_ms, anchor=tuple(anchor), radius=radius,
            )
            await asyncio.sleep(settle_ms / 1000.0)

            after = await _get_world_pos(probe, runner._page)
            if after is None:
                samples.append({**direction, "error": "no_world_pos_after", "before": before})
                continue

            delta_x = after["x"] - before["x"]
            delta_z = after["z"] - before["z"]
            distance = math.hypot(delta_x, delta_z)

            samples.append({
                **direction,
                "before": before,
                "after": after,
                "delta_x": round(delta_x, 4),
                "delta_z": round(delta_z, 4),
                "distance": round(distance, 4),
                "moved": distance > MIN_MOVE,
            })

            # Return pulse
            if return_ms > 0:
                await runner.joystick_pulse(
                    dx=-direction["dx"], dy=-direction["dy"],
                    duration_ms=return_ms, anchor=tuple(anchor), radius=radius,
                )
                await asyncio.sleep(min(settle_ms, 150) / 1000.0)

        # --- Retry directions that didn't move when others did ---
        some_moved = any(s.get("moved") for s in samples if "delta_x" in s)
        if some_moved:
            for i, s in enumerate(samples):
                if s.get("delta_x") is not None and not s.get("moved"):
                    # Retry this direction once
                    before = await _get_world_pos(probe, runner._page)
                    if before is None:
                        continue
                    await runner.joystick_pulse(
                        dx=s["dx"], dy=s["dy"],
                        duration_ms=pulse_ms, anchor=tuple(anchor), radius=radius,
                    )
                    await asyncio.sleep(settle_ms / 1000.0)
                    after = await _get_world_pos(probe, runner._page)
                    if after is None:
                        continue
                    dx2 = after["x"] - before["x"]
                    dz2 = after["z"] - before["z"]
                    dist2 = math.hypot(dx2, dz2)
                    if dist2 > MIN_MOVE:
                        samples[i] = {
                            **s,
                            "before": before,
                            "after": after,
                            "delta_x": round(dx2, 4),
                            "delta_z": round(dz2, 4),
                            "distance": round(dist2, 4),
                            "moved": True,
                            "retried": True,
                        }
                    # Return pulse
                    if return_ms > 0:
                        await runner.joystick_pulse(
                            dx=-s["dx"], dy=-s["dy"],
                            duration_ms=return_ms, anchor=tuple(anchor), radius=radius,
                        )
                        await asyncio.sleep(min(settle_ms, 150) / 1000.0)

        # --- Fallback: if joystick produced no movement, try moveByCocosInput ---
        any_moved = any(s.get("moved") for s in samples if "delta_x" in s)
        if not any_moved:
            samples = []  # reset — joystick didn't work at all
            cocos_dirs = [
                {"name": "right", "dx": 1, "dz": 0},
                {"name": "down", "dx": 0, "dz": 1},
                {"name": "left", "dx": -1, "dz": 0},
                {"name": "up", "dx": 0, "dz": -1},
            ]
            for cd in cocos_dirs:
                before = await _get_world_pos(probe, runner._page)
                if before is None:
                    samples.append({**cd, "method": "cocos", "error": "no_pos_before"})
                    continue
                # Call probe's moveByCocosInput directly
                await probe.move_by_cocos(
                    runner._page, cd["dx"], cd["dz"], pulse_ms,
                )
                await asyncio.sleep(settle_ms / 1000.0)
                after = await _get_world_pos(probe, runner._page)
                if after is None:
                    samples.append({**cd, "method": "cocos", "error": "no_pos_after"})
                    continue
                delta_x = after["x"] - before["x"]
                delta_z = after["z"] - before["z"]
                distance = math.hypot(delta_x, delta_z)
                samples.append({
                    **cd,
                    "method": "cocos",
                    "before": before,
                    "after": after,
                    "delta_x": round(delta_x, 4),
                    "delta_z": round(delta_z, 4),
                    "distance": round(distance, 4),
                    "moved": distance > MIN_MOVE,
                })
                # Return pulse via cocos
                if return_ms > 0:
                    await probe.move_by_cocos(
                        runner._page, -cd["dx"], -cd["dz"], return_ms,
                    )
                    await asyncio.sleep(min(settle_ms, 150) / 1000.0)

        # Final position for drift measurement
        final_pos = await _get_world_pos(probe, runner._page)
        initial_pos = samples[0].get("before") if samples else None
        drift = None
        if initial_pos and final_pos:
            drift = {
                "x": round(final_pos["x"] - initial_pos["x"], 4),
                "z": round(final_pos["z"] - initial_pos["z"], 4),
            }

    finally:
        await runner.close()

    # --- Compute basis ---
    by_name = {s["name"]: s for s in samples if "delta_x" in s}
    right = by_name.get("right")
    down = by_name.get("down")
    left = by_name.get("left")
    up = by_name.get("up")

    valid = True
    reasons = []

    # Check each direction moved
    for name, s in by_name.items():
        if not s.get("moved"):
            valid = False
            reasons.append(f"{name} did not move (dist={s.get('distance', 0):.3f})")

    # Check consistency: right + left ≈ 0, down + up ≈ 0
    if right and left:
        sum_x = right["delta_x"] + left["delta_x"]
        sum_z = right["delta_z"] + left["delta_z"]
        if math.hypot(sum_x, sum_z) > CONSISTENCY_THRESHOLD:
            valid = False
            reasons.append(f"right+left inconsistent: ({sum_x:.2f}, {sum_z:.2f})")
    if down and up:
        sum_x = down["delta_x"] + up["delta_x"]
        sum_z = down["delta_z"] + up["delta_z"]
        if math.hypot(sum_x, sum_z) > CONSISTENCY_THRESHOLD:
            valid = False
            reasons.append(f"down+up inconsistent: ({sum_x:.2f}, {sum_z:.2f})")

    # Build basis from right and down (must both have moved)
    basis = None
    if right and right.get("moved") and down and down.get("moved"):
        basis = {
            "screen_right": {"x": round(right["delta_x"], 4), "z": round(right["delta_z"], 4)},
            "screen_down": {"x": round(down["delta_x"], 4), "z": round(down["delta_z"], 4)},
        }
    else:
        valid = False
        reasons.append("right or down did not produce measurable movement")

    return {
        "game_id": game_id,
        "valid": valid,
        "reasons": reasons,
        "basis": basis,
        "samples": samples,
        "drift": drift,
        "anchor": anchor,
        "radius": radius,
        "pulse_ms": pulse_ms,
        "return_ms": return_ms,
        "settle_ms": settle_ms,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def write_profile(game_id: str, result: dict[str, Any]) -> None:
    """Append or update a calibrated profile in game_profiles.py."""
    basis = result["basis"]
    if basis is None:
        return

    # Read existing file
    text = PROFILES_PY.read_text(encoding="utf-8")

    # Check if game_id already has a profile
    if f'"{game_id}"' in text:
        # Update existing basis — find the calibration block
        import re
        pattern = re.compile(
            rf'("{game_id}":\s*\{{[^}}]*"calibration":\s*\{{[^}}]*"basis":\s*)\{{[^}}]*\}}',
            re.DOTALL,
        )
        new_basis = (
            f'{{\n'
            f'                "screen_right": {json.dumps(basis["screen_right"])},\n'
            f'                "screen_down": {json.dumps(basis["screen_down"])},\n'
            f'            }}'
        )
        text = pattern.sub(rf'\g<1>{new_basis}', text, count=1)
        PROFILES_PY.write_text(text, encoding="utf-8")
        print(f"  Updated basis for {game_id} in game_profiles.py")
    else:
        # Append new profile entry before the closing }
        anchor = result["anchor"]
        radius = result["radius"]
        entry = f'''    "{game_id}": {{
        "game_id": "{game_id}",
        "label": "auto-calibrated",
        "file_pattern": "{game_id}",
        "joystick": {{
            "anchor": {anchor},
            "radius": {radius},
            "input_mode": "touch",
        }},
        "calibration": {{
            "source": "auto_calibrate.py {result['timestamp']}",
            "basis": {{
                "screen_right": {json.dumps(basis["screen_right"])},
                "screen_down": {json.dumps(basis["screen_down"])},
            }},
        }},
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    }},
'''
        # Insert before the closing } of GAME_PROFILES (the line before
        # GENERIC_PROFILE definition).  We look for the pattern:
        #   }\n\n\n#: Generic
        # which uniquely marks the end of GAME_PROFILES.
        marker = "\n}\n\n\n#:"
        idx = text.find(marker)
        if idx == -1:
            # Fallback: insert before the first standalone }\n that closes
            # GAME_PROFILES (heuristic: first }\n at column 0 after the dict start)
            import re
            m = re.search(r'\n\}\n', text)
            idx = m.start() if m else text.rfind("}")
        text = text[:idx] + "\n" + entry + text[idx:]
        PROFILES_PY.write_text(text, encoding="utf-8")
        print(f"  Added profile for {game_id} in game_profiles.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-calibrate game coordinate mappings")
    parser.add_argument("--games", nargs="+", default=[], help="Game IDs to calibrate")
    parser.add_argument("--all", action="store_true", help="Calibrate all unprofiled games")
    parser.add_argument("--pulse-ms", type=int, default=PULSE_MS)
    parser.add_argument("--return-ms", type=int, default=RETURN_MS)
    parser.add_argument("--settle-ms", type=int, default=SETTLE_MS)
    args = parser.parse_args()

    if args.all:
        game_ids = _all_unprofiled_games()
    elif args.games:
        game_ids = args.games
    else:
        print("Specify --games or --all", file=sys.stderr)
        return 1

    print(f"Calibrating {len(game_ids)} games: {game_ids}")

    results = []
    for gid in game_ids:
        html = _resolve_html(gid)
        if html is None:
            print(f"  [skip] {gid}: no HTML found")
            continue
        print(f"  [{gid}] calibrating...", flush=True)
        r = asyncio.run(calibrate_game(
            gid, html,
            pulse_ms=args.pulse_ms,
            return_ms=args.return_ms,
            settle_ms=args.settle_ms,
        ))
        results.append(r)
        status = "VALID" if r["valid"] else f"INVALID: {'; '.join(r.get('reasons', []))}"
        print(f"  [{gid}] {status}")
        if r.get("basis"):
            b = r["basis"]
            print(f"    screen_right=({b['screen_right']['x']:.3f}, {b['screen_right']['z']:.3f}) "
                  f"screen_down=({b['screen_down']['x']:.3f}, {b['screen_down']['z']:.3f})")

    # Write calibrated profiles
    valid_results = [r for r in results if r["valid"] and r["basis"]]
    for r in valid_results:
        write_profile(r["game_id"], r)

    # Write standalone JSON for traceability
    CALIBRATED_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nCalibration data saved to {CALIBRATED_JSON}")
    print(f"Valid: {len(valid_results)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
