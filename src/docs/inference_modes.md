# Inference Modes Architecture

## Overview

Six game-playing modes, all sharing the same observation layer (Playwright + ProbeAdapter) and action layer (GameRunner CDP touch dispatch), but differing in the **decision layer**.

```
                  ┌──────────────────────────────────────────────────┐
                  │               Observation Layer                  │
                  │  Playwright → ProbeAdapter → {state, screenshot} │
                  └──────────────────────┬───────────────────────────┘
                                         ▼
                  ┌──────────────────────────────────────────────────┐
                  │              Decision Layer (pluggable)          │
                  │                                                  │
                  │  Mode 1: Direct API Text+Vision (existing)       │
                  │  Mode 2: Direct VLM (server.py exists)          │
                  │  Mode 3: VLM→Struct→API→Action                   │
                  │  Mode 4: VLM→Rules→RuleEngine                    │
                  │  Mode 5: API→Rules→RuleEngine                    │
                  │  Mode 6: Pure Rule Engine (port from Node.js)    │
                  └──────────────────────┬───────────────────────────┘
                                         ▼
                  ┌──────────────────────────────────────────────────┐
                  │                Action Layer                      │
                  │  GameRunner.joystick_pulse() / tap() / wait()    │
                  └──────────────────────────────────────────────────┘
```

---

## Mode 1: Direct API (EXISTS — LLMAgent)

**How it works:**
```
state + screenshot
  → DeepSeek-v4-flash (text → action JSON)
  → Mimo-v2.5 (vision → visual observations)
  → fuse decisions → execute
```

**Files:** `src/agent/llm_agent.py`, `api_client.py`
**Pros:** Works now, no GPU needed
**Cons:** Internet required, API latency, API cost

---

## Mode 2: Direct VLM (SERVER EXISTS — GameAgentInference)

**How it works:**
```
state + screenshot
  → Qwen3.5-4B/Gemma-4-E4B + LoRA (single forward pass → action JSON)
  → execute
```

**Files:** `src/inference/server.py` (741 lines, done)
**Pros:** Offline, low latency, single forward pass
**Cons:** Needs GPU (~30GB VRAM), needs trained adapter

---

## Mode 3: VLM → Structured State → API Text → Action (NEW)

**How it works:**
```
screenshot
  → VLM extracts structured visual info:
     {arrows, player_pos, targets, obstacles, end_card, ui_buttons}
  → Enriched state = probe_state + VLM_visual_struct
  → DeepSeek-v4-flash decides action from enriched state
  → execute
```

**VLM output schema (→ enriched state):**
```json
{
  "has_arrow": true,
  "arrow_screen_pos": [320, 450],
  "arrow_world_dir": {"x": 0.7, "z": 0.7},
  "has_target": true,
  "target_screen_pos": [280, 300],
  "has_obstacle": false,
  "is_end_screen": false,
  "ui_buttons": ["continue"],
  "player_screen_pos": [375, 667]
}
```

**Why this mode?** Combines VLM's visual understanding with API text model's reasoning. The VLM handles what it's good at (perception), the text model handles what it's good at (reasoning/planning).

---

## Mode 4: VLM → Rules → Rule Engine (NEW)

**How it works:**
```
screenshot + state (every N steps)
  → VLM outputs structured rules:
     {"game_mechanics": "follow_cyan_guide",
      "target_type": "arrow",
      "obstacle_handling": "rotate_around",
      "completion_condition": "reach_target"}
  → Rule Engine uses rules + real-time probe state → action
  → execute
  → Periodically re-query VLM to refine/update rules
```

**Rule Engine** is a new Python module that ports the strategy functions from the Node.js `.mjs` game drivers:
- `worldMoveDuration(distance, input_mode)` → pulse timing (all profiles ported from strategy_audit.md §4a)
- `chooseTargetCandidate(player, candidates, visual, guideDirection)` → target scoring (§4c)
- `planWorldRoute(player, target, obstacles)` → pathfinding (§4d)
- `isCompletionState(state, backendSnapshot)` → end detection (§4e)
- `learnedBlockerFromStick()` → obstacle learning (§4b)
- `worldVectorFromStick()` / `screenVectorForWorld()` → vector math

**Rule types the VLM can output:**
- `follow-guide`: Follow cyan guide arrows
- `collect-targets`: Move to resource targets
- `avoid-obstacles`: Path around obstacles
- `complete-level`: Reach exit/completion
- `recover-stuck`: When stuck, try alternate directions

---

## Mode 5: API → Rules → Rule Engine (NEW)

**How it works:**
```
screenshot + state
  → DeepSeek-v4-flash (text) + Mimo-v2.5 (vision)
  → Both outputs → Rule Generator → structured rules
  → Rule Engine executes rules → actions
  → Only re-query API when rules fail or state changes significantly
```

**Same Rule Engine as Mode 4**, but rules come from API models instead of VLM.

**Why this mode?** More powerful reasoning (DeepSeek) for rule generation. Lower long-term cost since API calls are infrequent.

---

## Mode 6: Pure Rule Engine (NEW — port from Node.js)

**How it works:**
```
probe state + screenshot (VisualAnalyzer heuristics)
  → Rule Engine (ported Node.js strategy functions)
  → action
  → execute
  → No API calls, no VLM
```

Ports the complete Node.js game driver architecture to Python:
- Profile-based strategy factory: `createStrategy(profile)` from `game_profiles.py`
- Vector math: `worldVectorFromStick`, `screenVectorForWorld`, `solveStickForWorld`, `normalizeWorldVector`
- Pulse timing: All 7 timing curves from strategy_audit.md §4a
- Target selection: `chooseTargetCandidate` scoring (§4c)
- Pathfinding: Line-blocker routing, A* grid pathfinding (§4d)
- Completion detection: `isCompletionState` (§4e)
- Obstacle learning: `learnedBlockerFromStick` (§4b)
- Visual analysis: `VisualAnalyzer` PIL fallback as first-class cyan guide detector

**Driver type → Strategy mapping:**
| Driver Type | Strategy Module | Games |
|---|---|---|
| `follow-guide-audited` | `FollowGuideStrategy` | 00848, 00849, 00850, 00854, 00858P01, 00858P02, 00860 |
| `2d-audited` | `Strategy2D` | 00853 |
| `learned` | `LearnedStrategy` | 00862 |
| `taskguide` | `TaskGuideStrategy` | 00863 |
| `target-arrow` | `TargetArrowStrategy` | 00864 |
| `guide-follow` | `GuideFollowStrategy` | 00867 |

---

## Shared Components

### Rule Engine (new module: `src/engine/`)
```
src/engine/
├── __init__.py
├── router.py          # Mode selection: picks which decision layer to use
├── rules.py           # Rule data models (Rule, RuleSet, RuleEngine)
├── pulse.py           # worldMoveDuration timing curves (all 7 variants)
├── vector.py          # worldVectorFromStick, screenVectorForWorld, etc.
├── targeting.py       # chooseTargetCandidate scoring
├── pathfinding.py     # planWorldRoute, line blockers, A* grid
├── completion.py      # isCompletionState detection
├── strategies/
│   ├── __init__.py
│   ├── follow_guide.py    # Family A: profile-based guide follower
│   ├── strategy_2d.py     # Family B: 2D economy
│   ├── learned.py         # Family C: runtime calibration
│   ├── taskguide.py       # Family D: task guide + visual fallback
│   ├── target_arrow.py    # Family E: A* pathfinding
│   └── guide_follow.py    # Family F: step-specific manual routes
└── visual.py          # Enhanced cyan guide detection (from VisualAnalyzer)
```

### VLM Integration (enhance `src/inference/`)
```
src/inference/
├── server.py           # Existing FastAPI server (Modes 2, 3, 4)
├── struct_extractor.py # NEW: VLM → structured visual state (Mode 3)
└── rule_extractor.py   # NEW: VLM → gameplay rules (Mode 4)
```

### Agent Enhancement (`src/agent/`)
```
src/agent/
├── llm_agent.py        # Existing (Mode 1)
├── vlm_agent.py        # NEW: Agent using VLM inference (Mode 2)
├── hybrid_agent.py     # NEW: Agent with mode switching (Modes 3-6)
└── __init__.py         # Updated exports
```

---

## Implementation Order

1. **Vector math** (`src/engine/vector.py`) — foundation for all rule-based modes
2. **Pulse timing curves** (`src/engine/pulse.py`) — needed by all modes using GameRunner
3. **Rule Engine** (`src/engine/rules.py`) — Rule data model + engine core
4. **Mode 6: Pure Rule Engine** — port FollowGuideStrategy first (covers 7 games)
5. **Mode 3: VLM→Struct→API** — VLM visual struct extractor + enriched state
6. **Mode 4: VLM→Rules→Engine** — VLM rule extractor
7. **Mode 5: API→Rules→Engine** — API rule generator
8. **Test all modes on real games** — run agent on games, compare results
