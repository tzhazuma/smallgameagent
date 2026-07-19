"""
Tests for game profile config extraction.

Validates that all 13 game profiles (12 from the Node.js .mjs files plus the
locally added SSD_00461P01 tower-defense profile) have been correctly
extracted into the Python config with all required fields.
"""

import math

from configs.game_profiles import GAME_PROFILES, list_all_game_ids, get_profile

EXPECTED_GAME_IDS = [
    "SSD_00848P01",
    "SSD_00849P01",
    "SSD_00850P01",
    "SSD_00853P01",
    "SSD_00854P01",
    "SSD_00858P01",
    "SSD_00858P02",
    "SSD_00860P01",
    "SSD_00862P01",
    "SSD_00863P01",
    "SSD_00864P01",
    "SSD_00867P01",
    "SSD_00461P01",
]


def test_all_thirteen_games_present():
    """Verify all 13 expected games exist in the profile dict."""
    actual = list_all_game_ids()
    for gid in EXPECTED_GAME_IDS:
        assert gid in actual, f"Missing game: {gid}"
    assert len(actual) >= 13, f"Expected at least 13 games, got {len(actual)}"


def test_get_profile_helper():
    """Verify the get_profile helper returns correct profiles."""
    for gid in EXPECTED_GAME_IDS:
        profile = get_profile(gid)
        assert profile is not None, f"get_profile('{gid}') returned None"
        assert profile["game_id"] == gid


def test_each_game_has_game_id():
    """Every profile has a game_id matching its dict key."""
    for gid, profile in GAME_PROFILES.items():
        assert "game_id" in profile, f"{gid} missing game_id"
        assert profile["game_id"] == gid, f"{gid} game_id mismatch"


def test_each_game_has_label():
    """Every profile has a non-empty label."""
    for gid, profile in GAME_PROFILES.items():
        assert "label" in profile, f"{gid} missing label"
        assert isinstance(profile["label"], str) and len(profile["label"]) > 0


def test_each_game_has_file_pattern():
    """Every profile has a non-empty file_pattern."""
    for gid, profile in GAME_PROFILES.items():
        assert "file_pattern" in profile, f"{gid} missing file_pattern"
        assert isinstance(profile["file_pattern"], str) and len(profile["file_pattern"]) > 0


def test_each_game_has_joystick():
    """Every profile has joystick with anchor and radius."""
    for gid, profile in GAME_PROFILES.items():
        assert "joystick" in profile, f"{gid} missing joystick"
        joystick = profile["joystick"]

        # anchor: [x, y] pair of finite numbers
        assert "anchor" in joystick, f"{gid} joystick missing anchor"
        anchor = joystick["anchor"]
        assert isinstance(anchor, (list, tuple)) and len(anchor) == 2
        x, y = anchor
        assert isinstance(x, (int, float)) and math.isfinite(x), f"{gid} anchor x not finite"
        assert isinstance(y, (int, float)) and math.isfinite(y), f"{gid} anchor y not finite"

        # radius: positive finite number
        assert "radius" in joystick, f"{gid} joystick missing radius"
        radius = joystick["radius"]
        assert isinstance(radius, (int, float)) and radius > 0 and math.isfinite(radius)


def test_joystick_input_mode_is_valid():
    """If input_mode is present, it must be 'touch' or 'mouse'."""
    for gid, profile in GAME_PROFILES.items():
        joystick = profile["joystick"]
        if "input_mode" in joystick:
            assert joystick["input_mode"] in ("touch", "mouse"), \
                f"{gid} invalid input_mode: {joystick['input_mode']}"


def test_each_game_has_calibration():
    """Every profile has calibration with source and basis."""
    for gid, profile in GAME_PROFILES.items():
        assert "calibration" in profile, f"{gid} missing calibration"
        cal = profile["calibration"]

        assert "source" in cal, f"{gid} calibration missing source"
        assert isinstance(cal["source"], str) and len(cal["source"]) > 0

        assert "basis" in cal, f"{gid} calibration missing basis"
        basis = cal["basis"]

        # screen_right: {x, z} finite numbers
        assert "screen_right" in basis, f"{gid} basis missing screen_right"
        sr = basis["screen_right"]
        assert "x" in sr and "z" in sr, f"{gid} screen_right incomplete"
        assert math.isfinite(sr["x"]) and math.isfinite(sr["z"])

        # screen_down: {x, z} finite numbers
        assert "screen_down" in basis, f"{gid} basis missing screen_down"
        sd = basis["screen_down"]
        assert "x" in sd and "z" in sd, f"{gid} screen_down incomplete"
        assert math.isfinite(sd["x"]) and math.isfinite(sd["z"])

        # basis vectors should not be zero
        sr_mag = math.hypot(sr["x"], sr["z"])
        sd_mag = math.hypot(sd["x"], sd["z"])
        assert sr_mag > 0.001, f"{gid} screen_right vector is zero"
        assert sd_mag > 0.001, f"{gid} screen_down vector is zero"


def test_calibration_basis_vectors_are_not_parallel():
    """screen_right and screen_down should not be colinear (degenerate basis)."""
    for gid, profile in GAME_PROFILES.items():
        basis = profile["calibration"]["basis"]
        sr = basis["screen_right"]
        sd = basis["screen_down"]
        # 2D cross product magnitude (determinant) should be non-zero
        det = sr["x"] * sd["z"] - sd["x"] * sr["z"]
        assert abs(det) > 0.001, f"{gid} basis vectors are colinear (det={det})"


def test_each_game_has_arrival_threshold():
    """Every profile has a finite positive ground_arrival_threshold."""
    for gid, profile in GAME_PROFILES.items():
        assert "ground_arrival_threshold" in profile, f"{gid} missing ground_arrival_threshold"
        val = profile["ground_arrival_threshold"]
        assert isinstance(val, (int, float)) and val > 0 and math.isfinite(val)


def test_each_game_has_target_dwell_ms():
    """Every profile has a finite positive target_dwell_ms."""
    for gid, profile in GAME_PROFILES.items():
        assert "target_dwell_ms" in profile, f"{gid} missing target_dwell_ms"
        val = profile["target_dwell_ms"]
        assert isinstance(val, (int, float)) and val > 0 and math.isfinite(val)


def test_each_game_has_driver_type():
    """Every profile has a valid driver_type."""
    valid_driver_types = {
        "follow-guide-audited",
        "2d-audited",
        "learned",
        "taskguide",
        "target-arrow",
        "guide-follow",
        "tap-guide",
    }
    for gid, profile in GAME_PROFILES.items():
        assert "driver_type" in profile, f"{gid} missing driver_type"
        assert profile["driver_type"] in valid_driver_types, \
            f"{gid} unknown driver_type: {profile['driver_type']}"


def test_profile_values_have_correct_types():
    """Verify type consistency across all numeric fields."""
    for gid, profile in GAME_PROFILES.items():
        assert isinstance(profile["game_id"], str)
        assert isinstance(profile["label"], str)
        assert isinstance(profile["file_pattern"], str)
        assert isinstance(profile["ground_arrival_threshold"], (int, float))
        assert isinstance(profile["target_dwell_ms"], (int, float))
        assert isinstance(profile["joystick"]["radius"], (int, float))
        assert isinstance(profile["joystick"]["anchor"], (list, tuple))
        for coord in profile["joystick"]["anchor"]:
            assert isinstance(coord, (int, float))


def test_driver_type_distribution():
    """Verify we have the expected spread of driver types."""
    types = {}
    for profile in GAME_PROFILES.values():
        dt = profile["driver_type"]
        types[dt] = types.get(dt, 0) + 1

    # All 6 driver types should appear at least once
    expected_types = {
        "follow-guide-audited",
        "2d-audited",
        "learned",
        "taskguide",
        "target-arrow",
        "guide-follow",
    }
    for dt in expected_types:
        assert dt in types, f"Driver type '{dt}' not found in any profile"
        assert types[dt] >= 1, f"Driver type '{dt}' has zero profiles"


def test_ground_arrival_threshold_in_plausible_range():
    """Thresholds should be in a reasonable pixel range (10-200)."""
    for gid, profile in GAME_PROFILES.items():
        val = profile["ground_arrival_threshold"]
        assert 10 <= val <= 200, f"{gid} ground_arrival_threshold {val} outside [10, 200]"


def test_target_dwell_ms_in_plausible_range():
    """Dwell times should be in a reasonable ms range (500-10000)."""
    for gid, profile in GAME_PROFILES.items():
        val = profile["target_dwell_ms"]
        assert 500 <= val <= 10000, f"{gid} target_dwell_ms {val} outside [500, 10000]"
