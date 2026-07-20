"""
Game profiles extracted from Node.js playable-agent strategy profile .mjs files.

Each profile contains per-game parameters: joystick config, calibration basis,
arrival thresholds, dwell timing, and driver type. These drive the automation
agent's movement and targeting logic per game.

Source: playable-agent-12-games-20260608/playable-automation/game-drivers/
Extracted: 2026-06-08

8 games have dedicated *-profile.mjs files.
4 games (00862, 00863, 00864, 00867) have inline configs in their run-*.mjs files.
SSD_00461P01 (tower defense) was added locally on 2026-07-16 from live probe
reconnaissance of the local HTML (floating joystick; screen->world basis
measured via probe worldPosition deltas).
"""

from pathlib import Path

GAME_PROFILES = {
    "SSD_00848P01": {
        "game_id": "SSD_00848P01",
        "label": "传送带种地",
        "file_pattern": "SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地",
        "joystick": {
            "anchor": [91, 699],
            "radius": 50,
        },
        "calibration": {
            "source": "verified 2026-05-27 by verify-coordinate-mapping.mjs; no up/down inversion",
            "basis": {
                "screen_right": {"x": 2.1227, "z": 2.1227},
                "screen_down": {"x": -2.0652, "z": 2.0652},
            },
        },
        "ground_arrival_threshold": 85,
        "target_dwell_ms": 4000,
        "driver_type": "follow-guide-audited",
    },
    "SSD_00849P01": {
        "game_id": "SSD_00849P01",
        "label": "结阵防御",
        "file_pattern": "SSD_00849P01_EN_WZW_20260429_TGQC_Applovin_结阵防御",
        "joystick": {
            "anchor": [125, 1130],
            "radius": 90,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "verified 2026-05-28 by learn-00849-controls.mjs; mouse drag lower-left-large",
            "basis": {
                "screen_right": {"x": 1, "z": -1},
                "screen_down": {"x": 1, "z": 1},
            },
        },
        "ground_arrival_threshold": 85,
        "target_dwell_ms": 4000,
        "driver_type": "follow-guide-audited",
    },
    "SSD_00850P01": {
        "game_id": "SSD_00850P01",
        "label": "Chariot Harvest",
        "file_pattern": "SSD_00850P01_EN_HWT_20260429_DLCX_Applovin_战车收割",
        "joystick": {
            "anchor": [91, 699],
            "radius": 50,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "learn-00850-controls 2026-05-28T11-22-37: mouse anchor 91,699 radius 50; down maps to +x,+z",
            "basis": {
                "screen_right": {"x": 1, "z": -1},
                "screen_down": {"x": 1, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "follow-guide-audited",
    },
    "SSD_00853P01": {
        "game_id": "SSD_00853P01",
        "label": "Truck Hurry",
        "file_pattern": "SSD_00853P01_EN_TZQ_20260430_SH_Applovin_货车很急",
        "joystick": {
            "anchor": [270, 480],
            "radius": 82,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00853 manual teach log 2026-05-29: Cocos 2D UI coords, x/y mapped into driver x/z",
            "basis": {
                "screen_right": {"x": 0.838, "z": 0.652},
                "screen_down": {"x": -0.814, "z": 0.657},
            },
        },
        "ground_arrival_threshold": 45,
        "target_dwell_ms": 4000,
        "driver_type": "2d-audited",
    },
    "SSD_00854P01": {
        "game_id": "SSD_00854P01",
        "label": "Giant Wood Processing",
        "file_pattern": "SSD_00854P01_EN_TZQ_20260429_DLCX_Applovin_巨木处理",
        "joystick": {
            "anchor": [91, 699],
            "radius": 50,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00854 manual/auto logs: solved from joystick pulses and observed world deltas on 2026-05-29",
            "basis": {
                "screen_right": {"x": 0.838, "z": 0.652},
                "screen_down": {"x": -0.814, "z": 0.657},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "follow-guide-audited",
    },
    "SSD_00858P01": {
        "game_id": "SSD_00858P01",
        "label": "Tab Optimization",
        "file_pattern": "SSD_00858P01_EN_QMY_20260430_DLCX_Applovin_选项卡优化",
        "joystick": {
            "anchor": [91, 699],
            "radius": 50,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00858 verified by four-direction joystick pulses on 2026-05-29: screen right=(+X,+Z), screen down=(-X,+Z)",
            "basis": {
                "screen_right": {"x": 0.7071, "z": 0.7071},
                "screen_down": {"x": -0.7071, "z": 0.7071},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 1800,
        "driver_type": "2d-audited",
    },
    "SSD_00858P02": {
        "game_id": "SSD_00858P02",
        "label": "Tab Optimization Pressure",
        "file_pattern": "SSD_00858P02_EN_QMY_20260430_DLCX_Applovin_选项卡优化压力版",
        "joystick": {
            "anchor": [91, 699],
            "radius": 50,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00858P02 starts from the verified 00858P01 mapping on 2026-05-29: screen right=(+X,+Z), screen down=(-X,+Z)",
            "basis": {
                "screen_right": {"x": 0.7071, "z": 0.7071},
                "screen_down": {"x": -0.7071, "z": 0.7071},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 1800,
        "driver_type": "2d-audited",
    },
    "SSD_00860P01": {
        "game_id": "SSD_00860P01",
        "label": "Mechanism Camp",
        "file_pattern": "SSD_00860P01_EN_WXD_20260430_techai_Applovin_机关营地",
        "joystick": {
            "anchor": [375, 1135],
            "radius": 90,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00860 control mapping verified on 2026-05-29: joystick right maps to world +X and joystick down maps to world +Z",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 1800,
        "driver_type": "follow-guide-audited",
    },
    "SSD_00862P01": {
        "game_id": "SSD_00862P01",
        "label": "砍树割鱼",
        "file_pattern": "SSD_00862P01",
        "joystick": {
            "anchor": [375, 520],
            "radius": 120,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "inline learned 00862 fallback basis from run-00862-learned.mjs; per-run calibration with fallback",
            "basis": {
                "screen_right": {"x": 0.3663, "z": 0.9305},
                "screen_down": {"x": -0.9509, "z": 0.3095},
            },
        },
        "ground_arrival_threshold": 50,
        "target_dwell_ms": 1000,
        "driver_type": "learned",
    },
    "SSD_00863P01": {
        "game_id": "SSD_00863P01",
        "label": "PA载具伐木",
        "file_pattern": "SSD_00863P01",
        "joystick": {
            "anchor": [375, 780],
            "radius": 128,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "inline taskguide fallback basis from run-00863-taskguide.mjs; per-run calibration with fallback",
            "basis": {
                "screen_right": {"x": 0.7071, "z": -0.7071},
                "screen_down": {"x": 0.7071, "z": 0.7071},
            },
        },
        "ground_arrival_threshold": 45,
        "target_dwell_ms": 1700,
        "driver_type": "taskguide",
    },
    "SSD_00864P01": {
        "game_id": "SSD_00864P01",
        "label": "僵尸庇护所卡通",
        "file_pattern": "SSD_00864P01",
        "joystick": {
            "anchor": [375, 790],
            "radius": 124,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00864 2D x/y mapping from run-00864-target-arrow.mjs calibration run 2026-05-29; screen right=world x+, screen up=world y+",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": -1},
            },
        },
        "ground_arrival_threshold": 38,
        "target_dwell_ms": 1400,
        "driver_type": "target-arrow",
    },
    "SSD_00867P01": {
        "game_id": "SSD_00867P01",
        "label": "雇佣卖木",
        "file_pattern": "SSD_00867P01",
        "joystick": {
            "anchor": [375, 720],
            "radius": 124,
            "input_mode": "mouse",
        },
        "calibration": {
            "source": "00867 guide-follow from run-00867-guide-follow.mjs; direct world-vector-to-stick mapping (no explicit calibration basis)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 45,
        "target_dwell_ms": 1500,
        "driver_type": "guide-follow",
    },
    "SSD_00461P01": {
        "game_id": "SSD_00461P01",
        "label": "塔防营地-箭塔升级",
        "file_pattern": "SSD_00461P01_EN_WNK_20260116_RBN_Applovin_塔防来着",
        "joystick": {
            # Floating joystick (JoystickControl under Canvas): a touch drag
            # anywhere on screen moves the Hero, so the anchor only needs to
            # be a spot clear of UI buttons.
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "measured 2026-07-16 via probe worldPosition deltas: 700ms screen-right pulse -> world +X only (+10.1); screen-down pulse -> world +Z only (+10.2); no inversion",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },
    "SSD_00736P01": {
        "game_id": "SSD_00736P01",
        "label": "养蛙迭代捕鱼养龟",
        "file_pattern": "SSD_00736P01_EN_LSS_20260402_DLCX_Applovin_养蛙迭代捕鱼养龟",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T06:02:54",
            "basis": {
                "screen_right": {"x": 1.4428, "z": -2.899},
                "screen_down": {"x": 1.4858, "z": 2.8986},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },
    "SSD_00440P01": {
        "game_id": "SSD_00440P01",
        "label": "auto-calibrated",
        "file_pattern": "SSD_00440P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T18:25:02",
            "basis": {
                "screen_right": {"x": -6.4212, "z": 2.3371},
                "screen_down": {"x": -2.3371, "z": -6.4212},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00483P01": {
        "game_id": "SSD_00483P01",
        "label": "auto-calibrated",
        "file_pattern": "SSD_00483P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T18:26:18",
            "basis": {
                "screen_right": {"x": 3.4093, "z": -3.4093},
                "screen_down": {"x": 2.4155, "z": 2.4155},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00496P01": {
        "game_id": "SSD_00496P01",
        "label": "auto-calibrated",
        "file_pattern": "SSD_00496P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T18:26:40",
            "basis": {
                "screen_right": {"x": 0.7508, "z": -0.9005},
                "screen_down": {"x": 0.8193, "z": 3.5725},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00517P01": {
        "game_id": "SSD_00517P01",
        "label": "auto-calibrated",
        "file_pattern": "SSD_00517P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T18:27:03",
            "basis": {
                "screen_right": {"x": 7.4241, "z": -0.0},
                "screen_down": {"x": 0.0, "z": 9.4355},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00522P02": {
        "game_id": "SSD_00522P02",
        "label": "auto-calibrated",
        "file_pattern": "SSD_00522P02",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto_calibrate.py 2026-07-20T18:27:25",
            "basis": {
                "screen_right": {"x": 4.9198, "z": 0.0},
                "screen_down": {"x": -0.267, "z": 3.4628},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-guide",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00219P01": {
        "game_id": "SSD_00219P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00219P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00332P01": {
        "game_id": "SSD_00332P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00332P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00342P01": {
        "game_id": "SSD_00342P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00342P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00382P01": {
        "game_id": "SSD_00382P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00382P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00394P01": {
        "game_id": "SSD_00394P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00394P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00427P01": {
        "game_id": "SSD_00427P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00427P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00434P01": {
        "game_id": "SSD_00434P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00434P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00475P01": {
        "game_id": "SSD_00475P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00475P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00482P01": {
        "game_id": "SSD_00482P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00482P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00526P01": {
        "game_id": "SSD_00526P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00526P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00532P01": {
        "game_id": "SSD_00532P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00532P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00594P02": {
        "game_id": "SSD_00594P02",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00594P02",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00669P01": {
        "game_id": "SSD_00669P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00669P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00733P01": {
        "game_id": "SSD_00733P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00733P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

    "SSD_00742P01": {
        "game_id": "SSD_00742P01",
        "label": "auto-tap-only",
        "file_pattern": "SSD_00742P01",
        "joystick": {
            "anchor": [187, 650],
            "radius": 60,
            "input_mode": "touch",
        },
        "calibration": {
            "source": "auto-fill-tap-only (joystick calibration failed)",
            "basis": {
                "screen_right": {"x": 1, "z": 0},
                "screen_down": {"x": 0, "z": 1},
            },
        },
        "ground_arrival_threshold": 55,
        "target_dwell_ms": 4000,
        "driver_type": "tap-only",
        "design_resolution": [720, 1560],
        "viewport": [375, 812],
    },

}


#: Generic fallback profile used for games that have no hand-tuned profile.
#: It assumes a floating joystick (touch drag anywhere) and a unit world↔screen
#: basis.  The basis is *uncalibrated*, so movement direction will be wrong for
#: most games — this profile exists so the framework can still load, drive and
#: collect trajectories from unprofiled games (generalisation / data collection),
#: not to score well on them.  ``is_generic`` lets callers tell the two apart.
GENERIC_PROFILE = {
    "game_id": "__generic__",
    "label": "generic-fallback (uncalibrated)",
    "file_pattern": "",
    "joystick": {
        "anchor": [187, 650],
        "radius": 60,
        "input_mode": "touch",
    },
    "calibration": {
        "source": "generic fallback: unit basis, UNCALIBRATED — direction unreliable",
        "basis": {
            "screen_right": {"x": 1, "z": 0},
            "screen_down": {"x": 0, "z": 1},
        },
    },
    "ground_arrival_threshold": 55,
    "target_dwell_ms": 4000,
    "driver_type": "tap-guide",
    "design_resolution": [720, 1560],
    "viewport": [375, 812],
    "is_generic": True,
}


def get_profile(game_id):
    """Get a game profile by game_id string (e.g. 'SSD_00848P01').

    Returns ``None`` when the game has no hand-tuned profile.  Callers that
    want a drivable fallback for any game should use :func:`get_profile_or_generic`.
    """
    return GAME_PROFILES.get(game_id)


def get_profile_or_generic(game_id):
    """Return the tuned profile for *game_id*, or a generic fallback.

    The generic fallback is uncalibrated (see :data:`GENERIC_PROFILE`); the
    returned dict carries ``is_generic=True`` in that case so callers can
    report calibrated vs uncalibrated runs separately.
    """
    profile = GAME_PROFILES.get(game_id)
    if profile is not None:
        return profile
    generic = dict(GENERIC_PROFILE)
    generic["game_id"] = game_id
    return generic


def list_all_game_ids():
    """Return sorted list of all game IDs."""
    return sorted(GAME_PROFILES.keys())


# ---------------------------------------------------------------------------
# Game-type classification from auto-calibration results
# ---------------------------------------------------------------------------

_AUTO_CAL_PATH = Path(__file__).resolve().parent / "auto_calibrated_profiles.json"


def get_game_type(game_id: str) -> str:
    """Return the game type for *game_id* based on auto-calibration results.

    Returns:
        "A" — joystick-driven (calibration VALID or has tuned profile with real basis)
        "B" — tap-to-move / auto-movement (calibration INVALID or tap-only profile)
        "C" — probe cannot load / unknown
    """
    # Tap-only profiles are B class (auto-filled after failed calibration)
    profile = GAME_PROFILES.get(game_id)
    if profile is not None:
        if profile.get("driver_type") == "tap-only":
            return "B"
        # Real calibrated profiles (tap-guide / follow-guide / etc.)
        return "A"

    # Check auto-calibration results
    if _AUTO_CAL_PATH.is_file():
        import json as _json
        try:
            data = _json.loads(_AUTO_CAL_PATH.read_text(encoding="utf-8"))
            for r in data:
                if r.get("game_id") == game_id:
                    if r.get("valid") and r.get("basis"):
                        return "A"
                    if "probe_not_ready" in str(r.get("reasons", [])):
                        return "C"
                    return "B"
        except (OSError, _json.JSONDecodeError):
            pass
    return "C"


def get_driver_for_type(game_id: str) -> str:
    """Return the recommended driver_type for *game_id* based on its type."""
    gtype = get_game_type(game_id)
    if gtype == "B":
        return "tap-only"
    return "tap-guide"
