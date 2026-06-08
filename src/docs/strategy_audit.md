# Strategy Profile & Game Driver Audit

> **Audit date:** 2026-06-08
> **Scope:** All 12 game strategy profiles and drivers in `playable-agent-12-games-20260608/playable-automation/game-drivers/`
> **Purpose:** Map every game's driver type, profile parameters, visual detection, calibration methodology, and strategy factory functions.

---

## 1. Inventory Summary

| # | Game ID | Directory | Driver File | Profile File | Visual Detector | Record/Teach | Learn |
|---|---------|-----------|-------------|--------------|-----------------|--------------|-------|
| 1 | SSD_00848P01 | `ssd-00848p01-conveyor-farming/` | `follow-guide-audited.mjs` | ✅ `ssd-00848p01-conveyor-farming-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 2 | SSD_00849P01 | `ssd-00849p01-formation-defense/` | `follow-guide-audited.mjs` | ✅ `ssd-00849p01-formation-defense-profile.mjs` | `detect-cyan-guide.py` | — | `learn-00849-controls.mjs` + `learn-00849-backend-state.mjs` |
| 3 | SSD_00850P01 | `ssd-00850p01-chariot-harvest/` | `follow-guide-audited.mjs` | ✅ `ssd-00850p01-chariot-harvest-profile.mjs` | `detect-cyan-guide.py` | — | `learn-00850-controls.mjs` + `learn-00850-backend-state.mjs` |
| 4 | SSD_00853P01 | `ssd-00853p01-truck-hurry/` | `follow-00853-2d-audited.mjs` | ✅ `ssd-00853p01-truck-hurry-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 5 | SSD_00854P01 | `ssd-00854p01-giant-wood-processing/` | `follow-guide-audited.mjs` | ✅ `ssd-00854p01-giant-wood-processing-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 6 | SSD_00858P01 | `ssd-00858p01-tab-optimization/` | `follow-guide-audited.mjs` | ✅ `ssd-00858p01-tab-optimization-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 7 | SSD_00858P02 | `ssd-00858p02-tab-optimization-pressure/` | `follow-guide-audited.mjs` | ✅ `ssd-00858p02-tab-optimization-pressure-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 8 | SSD_00860P01 | `ssd-00860p01-mechanism-camp/` | `follow-guide-audited.mjs` | ✅ `ssd-00860p01-mechanism-camp-profile.mjs` | `detect-cyan-guide.py` | — | — |
| 9 | SSD_00862P01 | `ssd-00862p01-wood-fish-cutting/` | `run-00862-learned.mjs` | ❌ (hardcoded fallback) | — | — | ✅ runtime calibration |
| 10 | SSD_00863P01 | `ssd-00863p01-pa-car-logging/` | `run-00863-taskguide.mjs` | ❌ (hardcoded fallback) | `detect-cyan-guide-00863.py` | — | ✅ runtime calibration |
| 11 | SSD_00864P01 | `ssd-00864p01-zombie-shelter-cartoon/` | `run-00864-target-arrow.mjs` | ❌ (hardcoded mapping) | — | `manual-teach-recorder-00864.mjs` | `inspect-00864-obstacles.mjs` + `inspect-00864-state.mjs` |
| 12 | SSD_00867P01 | `ssd-00867p01-hire-sell-wood/` | `run-00867-guide-follow.mjs` | ❌ (hardcoded refs) | — | `manual-teach-recorder-00867.mjs` | `inspect-00867-state.mjs` |

---

## 2. Driver Architectures

### 2a. `follow-guide-audited.mjs` (Profile-based Guide Follower)

**Used by:** SSD_00848P01, SSD_00849P01, SSD_00850P01, SSD_00854P01, SSD_00858P01, SSD_00858P02, SSD_00860P01

This is the **canonical architecture**. Each game has a dedicated profile file and the same driver imports `createSsdXXXXStrategy()` from the profile. The driver then uses the returned strategy functions for:

- `worldMoveDuration(distance, opts)` → pulse timing
- `chooseTargetCandidate(player, candidates, visual, guideWorldDirection)` → target selection
- `planWorldRoute(player, target, obstacles)` → pathfinding
- `isCompletionState(state, coordinate, backendSnapshot)` → end detection

The driver also runs `detect-cyan-guide.py` via subprocess (line ~579) whenever `backendOnly` is false, feeding the visual guide direction as supplementary input.

**Key observation:** Profiles share identity via object aliasing — see §3.

### 2b. `follow-00853-2d-audited.mjs` (2D Coordinate Variant)

**Used by:** SSD_00853P01 (Truck Hurry)

This is a **completely different architecture**. Instead of a profile-based strategy factory, it:
- Uses hardcoded 2D pixel coordinate `stationTargets` (line 50-61) and `learnedCoinTargets` (line 63-69)
- Operates in 2D screen space (x,y) with `coordinateMode: "cocos-2d-ui"` in the profile
- Has `stickFor2d(player, target)` (line 76) instead of world-vector-to-stick mapping
- Uses `moveDuration(distancePx)` (line 83) based on pixel distance, not world distance
- Has `chooseTarget(state, ...)` (line 548) — an imperative decision function, not a strategy factory
- Uses `profile.joystick.anchor` (line 392) for mouse-pulse but ignores the calibration basis
- Profiles as `ssd00850p01ChariotHarvestProfile` incorrectly — see §3

**Coordinate mode:** `cocos-2d-ui` — this game uses flat 2D UI coordinates, not 3D world projection.

### 2c. `run-00862-learned.mjs` (Independent Pre-learned)

**Used by:** SSD_00862P01 (Wood Fish Cutting)

This is a **self-contained runner** that:
- Has **no profile file** and **no visual detector**
- Uses `learned00862FallbackBasis` (line 33-36) — a hardcoded calibration basis
- Runs its own `calibrate()` function (line 305-342) at startup: fires 4 directional pulses and computes the basis from player deltas
- Uses `solveStickForWorld(basis, desired)` (line 103) to convert world → stick
- Uses `pulseDuration(distance)` (line 111) in pure world units
- Target selection: reads backend `GuideManager` via `getGuideSummary()` (line 153)
- Completion detection: `completionSignal()` (line 249) checks navigation events and `AnalyticsManager.is100Complete`
- Obstacle handling: `stuckStreak` with escape rotation (line 471-481)

### 2d. `run-00863-taskguide.mjs` (TaskGuide-based + Visual Fallback)

**Used by:** SSD_00863P01 (PA Car Logging)

This runner:
- Has **no profile file**; uses `learned00863FallbackBasis` (line 39-42)
- Runs its own runtime `calibrate()` (line 415-442) — same 4-pulse pattern
- Uses **dual targeting**: first tries `TaskGuideMy` backend component (ground + top arrow), then falls back to `Arrow_Item`, then to visual guide via `detect-cyan-guide-00863.py`
- Has `targetFromState()` (line 288) — a cascading decision tree with 5 modes
- Special money-pickup dwelling logic (line 598-646)
- Obstacle escape via rotated sticks (line 694-707)

### 2e. `run-00864-target-arrow.mjs` (2D TargetArrow Follower with A*)

**Used by:** SSD_00864P01 (Zombie Shelter Cartoon)

This is the **most architecturally distinct** driver:
- **No profile file**; uses hardcoded `coordinateMapping` (line 657-666)
- Operates in 2D x/y screen space
- Has a **full A* grid pathfinder** (`planGridRoute`, line 188-303): 52px cells, 8-directional neighbors, octile heuristic
- Has a **corner route planner** (`planWaypoint`, line 305-356) for single-wall cases
- Detects `AirWall` colliders from the Cocos scene graph as obstacles
- Target selection: `instructionFromState()` (line 566-631) with 4 modes
- Has `inspect-00864-obstacles.mjs` and `inspect-00864-state.mjs` for offline scene analysis
- Has `manual-teach-recorder-00864.mjs` for recording human playthroughs

### 2f. `run-00867-guide-follow.mjs` (GuideManager Follower with Step Routing)

**Used by:** SSD_00867P01 (Hire Sell Wood)

This driver:
- **No profile file**; reads `GuideManager` component refs directly from Cocos scene
- Has special **step-specific manual routes**:
  - `step4MoneyRoute()` — 10 waypoints for money trigger sweep (line 356-372)
  - `step5ElevatorRoute()` — 5 waypoints for elevator approach (line 412-421)
  - `step5EntryEscapeRoute()` — 6 waypoints (line 445-455)
  - `step8ElevatorRoute()` — 4 waypoints (line 479-487)
  - `step8BottomEscapeRoute()` — 6 waypoints (line 511-521)
  - `step7UpperDownRoute()` — 4 waypoints (line 560-568)
  - `step8UpperDownRoute()` — 4 waypoints (line 592-599)
- Has `planGridRouteXZ()` — 3D A* with 1.1m cells (line 741-854)
- Has `planCornerRouteXZ()` — 3D corner routing (line 856-907)
- Obstacle detection: reads `BoxCollider`/`Collider`/`RigidBody` from scene graph
- Has `inspect-00867-state.mjs` and `manual-teach-recorder-00867.mjs`
- Uses `GuideManager.step` (0-8+) to gate manual routing logic

---

## 3. Profile Parameters

### 3a. Games with Dedicated Profiles

| Game | Profile Export Name | joystick.anchor | joystick.radius | joystick.inputMode | groundArrivalThreshold | targetDwellMs | Calibration Source |
|------|-------------------|-----------------|-----------------|-------------------|----------------------|--------------|-------------------|
| **00848** | `ssd00848p01ConveyorFarmingProfile` | `[91, 699]` | `50` | touch (default) | `85` | `4000` | `verified 2026-05-27 by verify-coordinate-mapping.mjs; no up/down inversion` |
| **00849** | `ssd00849p01FormationDefenseProfile` | `[125, 1130]` | `90` | `mouse` | `85` | `4000` | `verified 2026-05-28 by learn-00849-controls.mjs; mouse drag lower-left-large` |
| **00850** | `ssd00850p01ChariotHarvestProfile` | `[91, 699]` | `50` | `mouse` | `55` | `4000` | `learn-00850-controls 2026-05-28T11-22-37: mouse anchor 91,699 radius 50; down maps to +x,+z` |
| **00853** | `ssd00850p01ChariotHarvestProfile` (aliased) | `[270, 480]` | `82` | `mouse` | `45` | `4000` | `00853 manual teach log 2026-05-29: Cocos 2D UI coords, x/y mapped into driver x/z` |
| **00854** | `ssd00850p01ChariotHarvestProfile` (aliased) | `[91, 699]` | `50` | `mouse` | `55` | `4000` | `00854 manual/auto logs: solved from joystick pulses and observed world deltas on 2026-05-29` |
| **00858P01** | `ssd00850p01ChariotHarvestProfile` (aliased) | `[91, 699]` | `50` | `mouse` | `55` | `1800` | `00858 verified by four-direction joystick pulses on 2026-05-29: screen right=(+X,+Z), screen down=(-X,+Z)` |
| **00858P02** | `ssd00850p01ChariotHarvestProfile` (aliased) | `[91, 699]` | `50` | `mouse` | `55` | `1800` | `00858P02 starts from the verified 00858P01 mapping on 2026-05-29: screen right=(+X,+Z), screen down=(-X,+Z)` |
| **00860** | `ssd00850p01ChariotHarvestProfile` (aliased) | `[375, 1135]` | `90` | `mouse` | `55` | `1800` | `00860 control mapping verified on 2026-05-29: joystick right maps to world +X and joystick down maps to world +Z` |

### 3b. Calibration Basis

| Game | screenRight (x,z) | screenDown (x,z) | Calibration Verification |
|------|-------------------|------------------|------------------------|
| **00848** | `(2.1227, 2.1227)` | `(-2.0652, 2.0652)` | ✅ Verified by `verify-coordinate-mapping.mjs` |
| **00849** | `(1, -1)` | `(1, 1)` | ✅ Verified by `learn-00849-controls.mjs` |
| **00850** | `(1, -1)` | `(1, 1)` | ✅ Verified by `learn-00850-controls` |
| **00853** | `(0.838, 0.652)` | `(-0.814, 0.657)` | ⚠️ Estimated from `manual teach log` |
| **00854** | `(0.838, 0.652)` | `(-0.814, 0.657)` | ⚠️ Estimated from `manual/auto logs` |
| **00858P01** | `(0.7071, 0.7071)` | `(-0.7071, 0.7071)` | ✅ Verified by `four-direction joystick pulses` |
| **00858P02** | `(0.7071, 0.7071)` | `(-0.7071, 0.7071)` | ✅ Inherited from verified 00858P01 |
| **00860** | `(1, 0)` | `(0, 1)` | ✅ Verified by `control mapping` |
| **00862** | fallback `(0.3663, 0.9305)` | fallback `(-0.9509, 0.3095)` | 🔄 Runtime calibration (4-pulse) with fallback |
| **00863** | fallback `(0.7071, -0.7071)` | fallback `(0.7071, 0.7071)` | 🔄 Runtime calibration (4-pulse) with fallback |
| **00864** | n/a (2D) | n/a (2D) | 📝 Manual empirical mapping `2026-05-29T16-03-59` |
| **00867** | n/a (implied: screenUp=z-, screenDown=z+) | n/a (implied) | 🔧 Discovered via `inspect-00867-state.mjs` |

### 3c. Critical: Profile Aliasing Issue

Several games **incorrectly export their profile under another game's name**. For example:

```javascript
// In ssd-00853p01-truck-hurry-profile.mjs (line 1):
export const ssd00850p01ChariotHarvestProfile = { /* ... */ };
// Then (line 24):
export const ssd00854p01GiantWoodProcessingProfile = ssd00850p01ChariotHarvestProfile;
```

The pattern is repeated in **00854, 00858P01, 00858P02, and 00860** — all export a variable named `ssd00850p01ChariotHarvestProfile` with their own values, then alias `ssd00854p01GiantWoodProcessingProfile = ssd00850p01ChariotHarvestProfile`.

The actual profile export used by the driver (`follow-guide-audited.mjs`) for each game is:
- 00854: imports `ssd00854p01GiantWoodProcessingProfile` = the 00850-alias (same game data)
- 00858P01/P02: imports `ssd00854p01GiantWoodProcessingProfile` = the 00850-alias
- 00860: imports `ssd00854p01GiantWoodProcessingProfile` = the 00850-alias

This means the **strategy factory function name is also reused** — e.g., 00858P01 calls `createSsd00854p01GiantWoodProcessingStrategy()` for its game logic. The actual game-specific data (anchor, radius, calibration basis) is correctly set in each file's profile object, but the **export/import naming is misleading**.

---

## 4. Strategy Factory Functions

### 4a. `worldMoveDuration(distance, opts)` — Pulse Timing

**Found in:** profile-based games (00848-00860 with profiles)

Each profile-based game defines `worldMoveDuration` inside its `create*Strategy()` factory. The function returns pulse duration in ms based on world distance.

There are **three distinct timing schemes** depending on `inputMode`:

#### Touch input (default) — 00848 only:
```
distance ≤ 0.45 → 45ms
distance ≤ 0.75 → 65ms
distance ≤ 1.0  → 90ms
distance ≤ 1.35 → 125ms
distance ≤ 1.8  → 165ms
distance ≤ 2.4  → 220ms
distance ≤ 3.2  → 300ms
distance ≤ 4.4  → 400ms
Large direct (≥6.0): 620-740ms (via source-specific formula)
Fallback: 460-620ms (capped by source)
```

#### Mouse input (00849, 00850, 00854, 00858, 00860):
**00849** (anchor `[125,1130]`, radius 90):
```
distance ≤ 0.45 → 10ms
distance ≤ 0.75 → 18ms
distance ≤ 1.0  → 26ms
distance ≤ 1.35 → 38ms
distance ≤ 1.8  → 34ms  ← shorter than the previous tier (not a typo)
distance ≤ 2.4  → 48ms
distance ≤ 3.2  → 78ms
distance ≤ 4.4  → 115ms
distance ≤ 6.5  → 230ms
distance ≤ 9.0  → 300ms
Fallback: 330-420ms
```

**00850/00854/00858/00860** (anchor `[91,699]`, radius 50):
```
distance ≤ 0.65 → 0ms    ← dead zone
distance ≤ 0.9  → 8ms
distance ≤ 1.25 → 12ms
distance ≤ 1.6  → 18ms
distance ≤ 2.1  → 28ms
distance ≤ 2.8  → 42ms
distance ≤ 3.8  → 70ms
distance ≤ 5.2  → 120ms
distance ≤ 7.5  → 220ms
distance ≤ 10.0 → 320ms
Fallback: 360-500ms
```

**00853 (2D driver)** — independent pulse function `moveDuration(distancePx)` (not in profile):
```
distancePx ≤ 35  → 0ms
distancePx ≤ 70  → 90ms
distancePx ≤ 130 → 180ms
distancePx ≤ 220 → 320ms
distancePx ≤ 360 → 520ms
Fallback: 620-1050ms
```

#### Waypoint scheme (shared fallback for all touch/mouse games):
```
distance ≤ 0.45 → 45ms
distance ≤ 0.75 → 55ms
distance ≤ 1.0  → 75ms
distance ≤ 1.35 → 105ms
distance ≤ 1.8  → 140ms
distance ≤ 2.4  → 180ms
distance ≤ 3.2  → 230ms
distance ≤ 4.4  → 300ms
Fallback: 320-430ms
```

#### Games without profile (custom schemes):

**00862** — `pulseDuration(distance)` (line 111-121):
```
distance > 10  → 940ms
distance > 7   → 760ms
distance > 5   → 590ms
distance > 3.2 → 410ms
distance > 2   → 285ms
distance > 1.2 → 175ms
distance > 0.75 → 90ms
otherwise → 0ms
```

**00863** — `pulseDuration(distance, mode)` (line 136-147):
```
mode "visual-guide-stick" → 520ms (fixed)
mode "guide-vector"      → >1.4m: 620ms, ≤1.4m: 420ms
mode "world-target" + <4m → 95ms
Generic: distance > 28 → 1100ms, >18 → 920ms, >10 → 760ms, >5 → 520ms, >2.4 → 320ms, else 170ms
```

**00864** — `pulseDuration(distance, mode)` (line 358-374):
```
mode "route-around-wall": >220px → 360ms, >130px → 260ms, >75px → 170ms, else 110ms
mode "continue-last-direction": 150ms (fixed)
Generic (px): >800 → 820ms, >520 → 680ms, >300 → 520ms, >150 → 360ms, >80 → 240ms, >45 → 150ms, else 90ms
```

**00867** — `pulseDuration(distance, mode)` (line 95-118):
```
mode "bootstrap-drag-up": 520ms
mode "route-around-wall": >6m → 560ms, >3m → 380ms, >1.4m → 220ms, else 120ms
mode "money-trigger-sweep": >14m → 1350ms, >9m → 1050ms, >5m → 760ms, >2.2m → 430ms, >1m → 220ms, else 120ms
Generic: >16m → 900ms, >9m → 700ms, >5m → 520ms, >2.2m → 340ms, >1m → 190ms, else 100ms
```

### 4b. `learnedBlockerFromStick(step, player, stick, basis, radiusHint)` — Obstacle Learning

**Found in:** 00848, 00849, 00850, 00853, 00854, 00858P01, 00858P02, 00860 (all profile-based games)

Identical implementation in every profile-based game:
```javascript
const vector = normalizeWorldVector(worldVectorFromStick(basis, stick, 1));
return {
  name: `learned-blocker-step-${step}`,
  path: `/runtime/learned-blocker/${step}`,
  learned: true,
  expiresAtStep: step + 10,
  worldPosition: {
    x: player.x + vector.x * 1.15,
    y: 0,
    z: player.z + vector.z * 1.15
  },
  radius: radiusHint  // default 1.25
};
```

**Not present in:** 00862, 00863, 00864, 00867 (these use different escape mechanisms — escape rotations, A* rerouting, or step-specific manual routes).

### 4c. `chooseTargetCandidate(player, candidates, visual, guideWorldDirection)` — Target Selection

**Found in:** all profile-based games (identical implementation)

The scoring algorithm:
1. Compute `desired` = vector from player to each candidate
2. Project to screen space via `screenVectorForWorld(calibration.basis, desired)`
3. Score = `directionScore - distancePenalty + targetBonus + arrowBonus`
   - `directionScore`: `backendGuideDirection` dot product (if available), else `visualDirection` dot product
   - `distancePenalty`: `min(distance / 120, 0.25)`
   - `targetBonus`: `0.08` if path matches `/target\d?$`
   - `arrowBonus`: `0.12` if kind is `enemy-arrow-ground`
4. Return null if best directionScore < 0.35

**Games without this function:** 00862, 00863, 00864, 00867 use their own target-selection logic:
- **00862**: reads `GuideManager._curTargetNode` via `getGuideSummary()` (line 153)
- **00863**: cascading `targetFromState()` with 5 modes (line 288-402)
- **00864**: `instructionFromState()` with TargetArrow + A* (line 566-631)
- **00867**: `instructionFromState()` with GuideManager refs (line 329-349)

### 4d. `planWorldRoute(player, target, obstacles)` — Pathfinding

**Found in:** all profile-based games (identical implementation)

The algorithm:
1. Find line blockers via `lineBlockers(player, target, obstacles, 0.75)`
2. If none: return direct route
3. For first blocker: compute 6 waypoint candidates (3 distances × 2 sides)
4. Score each by path length + hits penalty + clearance penalty
5. Return best candidate as waypoint

**Other games' pathfinding:**
- **00864**: `planGridRoute()` (A* with 52px cells) + `planWaypoint()` (corner routing)
- **00867**: `planGridRouteXZ()` (A* with 1.1m cells) + `planCornerRouteXZ()` (corner routing)
- **00862**: no pathfinding — uses `solveStickForWorld()` and escape rotations when stuck
- **00863**: no pathfinding — uses stick blending and escape rotations when stuck

### 4e. `isCompletionState(state, coordinate, backendSnapshot)` — End Detection

**Found in:** profile-based games (00848, 00849, 00850, 00853, 00854, 00858P01, 00858P02)

```javascript
function isCompletionState(state, coordinate, backendSnapshot) {
  if (state?.obs?.done || state?.obs?.win) return true;
  const hasBackendTarget = Boolean(backendSnapshot?.path || state?.target?.target?.path);
  const hasGuideArrows = (state?.target?.guideArrows?.length ?? 0) > 0;
  const hasVisibleGuide = Boolean(coordinate?.coord);
  return !hasBackendTarget && !hasGuideArrows && !hasVisibleGuide;
}
```

**00860** variant — same but **skips `state?.obs?.done || state?.obs?.win`** check (line 274-279):
```javascript
// 00860 does NOT check obs.done/obs.win
function isCompletionState(state, coordinate, backendSnapshot) {
  const hasBackendTarget = Boolean(backendSnapshot?.path || state?.target?.target?.path);
  const hasGuideArrows = (state?.target?.guideArrows?.length ?? 0) > 0;
  const hasVisibleGuide = Boolean(coordinate?.coord);
  return !hasBackendTarget && !hasGuideArrows && !hasVisibleGuide;
}
```

**Other games' completion detection:**
- **00862**: checks navigation events (`window.open`, `mraid.open`), `AnalyticsManager.is100Complete`, or active end UI nodes (line 249-268)
- **00863**: checks navigation events + visual end card detection via screenshot (line 262-274)
- **00864**: checks backend summary + sustained inactivity of target/playerArrow (line 633-642)
- **00867**: checks backend summary + `GuideManager.isGameOver` + sustained arrow inactivity (line 1005-1015)

---

## 5. Visual Detection

### 5a. `detect-cyan-guide.py` (Shared)

**Used by:** 00848, 00849, 00850, 00853, 00854, 00858P01, 00858P02, 00860

- Scans for **cyan pixels** in the screenshot: `b >= 135, g >= 105, r <= 130, (b-r) >= 50, (g-r) >= 15` (line 20)
- Ignores pixels within 82px of the player's screen position (the "cyan ring/body" area)
- Flood-fills connected components, filters out blobs with area < 18
- Returns:
  - `target`: the largest cyan component (arrow/triangle)
  - `targetKind`: `"arrow"` or `"triangle"`
  - `stick`: direction from player to component center (normalized)
  - `endCard`: detected if a large play button or logo fills the screen
  - `stationCue`: nearby UI cues

### 5b. `detect-cyan-guide-00863.py` (Game-specific variant)

**Used by:** 00863

Same cyan detection logic, but with a different scoring pipeline tuned for the logging game's specific arrow shapes.

### 5c. Games Without Visual Detection

- **00862** — no visual detection at all; pure backend state probe
- **00864** — no visual detection; reads Cocos scene graph directly for `AirWall`/`Collider` obstacles
- **00867** — no visual detection; reads Cocos scene graph for obstacles and GuideManager refs

---

## 6. Recording/Teaching Tools

| Tool | Game | Purpose |
|------|------|---------|
| `manual-teach-recorder-00864.mjs` | 00864 | Records human input events + backend state snapshots for up to `--minutes` (default 15). Captures pointer/touch/keyboard events via injected event listeners. |
| `manual-teach-recorder-00867.mjs` | 00867 | Same pattern for 00867. Also captures navigation events via `navigationProbeSource`. |

---

## 7. Calibration & Learning Files

| File | Game | Purpose |
|------|------|---------|
| `learn-00849-controls.mjs` (247 lines) | 00849 | Discovers joystick-to-world mapping by pulsing in 4 directions and measuring player deltas. Supports both touch (CDP) and mouse drag modes. Writes calibration to profile. |
| `learn-00849-backend-state.mjs` | 00849 | Probes backend state structure to understand the game's component hierarchy and data flow. |
| `learn-00850-controls.mjs` (250 lines) | 00850 | Same as 00849 but for Chariot Harvest. Produced calibration `2026-05-28T11-22-37`. |
| `learn-00850-backend-state.mjs` | 00850 | Backend state exploration for Chariot Harvest. |
| `inspect-00864-obstacles.mjs` (216 lines) | 00864 | Walks the Cocos scene graph, identifies all nodes with `Collider`/`RigidBody` components, and logs their rects, sizes, and positions. Used to reverse-engineer the game's wall layout. |
| `inspect-00864-state.mjs` (188 lines) | 00864 | Opens the game, logs initial state, performs center tap, then fires 4 directional pulses to measure the control mapping. |
| `inspect-00867-state.mjs` (273 lines) | 00867 | Same pattern for 00867 — opens game, logs colliders, performs 4-pulse calibration. |

---

## 8. Architecture Families

### Family A: "Profile-based Guide Follower" (7 games)
**00848, 00849, 00850, 00854, 00858P01, 00858P02, 00860**

- Shared: `follow-guide-audited.mjs` + game-specific profile + `detect-cyan-guide.py`
- Strategy factory provides 8 functions, all identically structured
- Calibration verified via learn scripts or manual logs
- Differences: anchor position, radius, timing curves, `targetEdgeArrivalRadius` patterns, `targetDwellDuration` patterns

### Family B: "2D Economy Driver" (1 game)
**00853**

- Custom `follow-00853-2d-audited.mjs`
- 2D pixel coordinates, hardcoded station stations
- Uses mouse input with joystick anchor/radius from profile
- No strategy factory

### Family C: "Independent Runtime Calibration" (2 games)
**00862, 00863**

- No profile files; runtime calibration via 4-direction pulse
- Custom runners with self-contained logic
- 00863 adds visual fallback via `detect-cyan-guide-00863.py`

### Family D: "Custom 2D/3D Route Planners" (2 games)
**00864, 00867**

- Manual teaching recorders, inspection tools, scene-graph obstacle detection
- A* grid pathfinding
- Step-specific manual routes (00867 has 7 distinct route matrices)
- 00864: 2D x/y with A* grid (52px cells)
- 00867: 3D x/z with A* grid (1.1m cells)

---

## 9. Key Findings

1. **Profile aliasing is systemic.** 5 games (00853, 00854, 00858P01, 00858P02, 00860) export their profile data under the name `ssd00850p01ChariotHarvestProfile`, and their strategy factory under `createSsd00854p01GiantWoodProcessingStrategy`. While the values within each file are correct for that game, the cross-file naming is misleading and could cause import errors if not carefully maintained.

2. **`groundArrivalThreshold` varies.** Ranges from `45` (00853) to `85` (00848, 00849). The 00849 profile uses `85` despite being mouse-based (others use `55` with mouse).

3. **`targetDwellMs` clusters.** 00848/00849/00850/00853/00854 use `4000ms`; 00858P01/P02/00860 use `1800ms` (faster-paced games).

4. **00860 has a distinct `isCompletionState`.** It does NOT check `state?.obs?.done || state?.obs?.win` — only relies on target/guide absence.

5. **00849's mouse timing curve is anomalous.** For distance 1.35-1.8, pulse duration decreases from 38ms to 34ms (line 41-42 of its profile). This may be an intentional adaptation for the formation-defense game's movement mechanics.

6. **00864's wall detection is the most sophisticated.** It reads `AirWall` colliders from Cocos physics and runs a full A* pathfinder. No other game does this.

7. **00867 has the most complex routing logic.** With 7 step-specific manual route matrices plus A* grid and corner routing, it has the most game-specific code of any driver.

8. **00862 and 00863 share a pattern** — both use runtime calibration (4 pulses, measure world deltas, compute basis), have a hardcoded fallback basis if calibration fails, and use escape rotations when stuck.
