"""Visual detection for rule-based game playing.

Ports the ``detect-cyan-guide.py`` from the Node.js game drivers into a
Python-callable function.  Detects cyan guide arrows, guide triangles,
end cards, coins, and cargo from a PIL screenshot image.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image


def detect_cyan_guide(
    pil_image: Image.Image,
    player_screen_x: float = 375.0,
    player_screen_y: float = 667.0,
) -> dict[str, Any]:
    """Analyse a game screenshot for cyan guide arrows, triangles, and end cards.

    Parameters
    ----------
    pil_image:
        RGB or RGBA PIL Image of the game frame.
    player_screen_x, player_screen_y:
        Player's screen-space position (used to exclude the player's own
        cyan glow from detection).

    Returns
    -------
    dict with keys:
        - ``target``: best target candidate (arrow or fallback)
        - ``targetKind``: ``"arrow"``, ``"triangle"``, or ``"none"``
        - ``stick``: ``{"dx": ..., "dy": ...}`` direction from player to target
        - ``guideTarget``: weighted guide triangle cluster or ``None``
        - ``guideStick``: direction to guide cluster or ``None``
        - ``endCard``: ``{"largePlayButton": bool, ...}``
        - ``stationCue``: coin/cargo detection
        - ``arrowCandidates`` / ``guideCandidates`` / ``components``
    """
    img = pil_image.convert("RGBA")
    w, h = img.size
    pix = np.array(img)

    # ---- Step 1: Cyan pixel mask (guide arrows / triangles) ----
    r, g, b, a = pix[:, :, 0], pix[:, :, 1], pix[:, :, 2], pix[:, :, 3]

    cyan_mask = (
        (a >= 128)
        & (b >= 135) & (g >= 105) & (r <= 130)
        & ((b.astype(int) - r.astype(int)) >= 50)
        & ((g.astype(int) - r.astype(int)) >= 15)
    )

    # Exclude player's cyan ring/body area
    yy, xx = np.ogrid[:h, :w]
    dist_from_player = np.sqrt((xx - player_screen_x) ** 2 + (yy - player_screen_y) ** 2)
    cyan_mask = cyan_mask & (dist_from_player >= 82)

    # Y-range filter (same as original: y 160-930)
    cyan_mask[:160, :] = False
    if h > 930:
        cyan_mask[930:, :] = False

    # ---- Step 2: Connected components on cyan mask ----
    components = _connected_components(cyan_mask, min_area=18, player_x=player_screen_x, player_y=player_screen_y)

    # ---- Step 3: Classify components ----
    arrow_candidates = [
        c for c in components
        if c["area"] >= 600
        and c["height"] >= 35
        and 20 <= c["width"] <= 80
        and c["height"] >= c["width"] * 0.75
        and c["distanceFromPlayer"] >= 90
    ]
    arrow_candidates.sort(
        key=lambda c: (
            c["area"] * 1.2
            + c["height"] * 10
            - abs(c["width"] - 38) * 6
            - max(0, 140 - c["distanceFromPlayer"]) * 2
        ),
        reverse=True,
    )

    guide_candidates = [
        c for c in components
        if 60 <= c["area"] <= 520
        and 12 <= c["width"] <= 46
        and 7 <= c["height"] <= 30
        and 80 <= c["distanceFromPlayer"] <= 380
    ]
    guide_candidates.sort(key=lambda c: c["distanceFromPlayer"])

    # ---- Step 4: Guide triangle cluster ----
    guide_target = None
    if guide_candidates:
        chosen = guide_candidates[:4]
        total_weight = 0.0
        gx = 0.0
        gy = 0.0
        for c in chosen:
            weight = 1.0 + c["distanceFromPlayer"] / 260.0
            gx += c["center"][0] * weight
            gy += c["center"][1] * weight
            total_weight += weight
        guide_target = {
            "center": [gx / total_weight, gy / total_weight],
            "components": chosen,
        }

    # ---- Step 5: Pick best target ----
    target = arrow_candidates[0] if arrow_candidates else (components[0] if components else None)
    is_arrow = bool(arrow_candidates and target and target == arrow_candidates[0])

    if target:
        if is_arrow:
            ground_point = [
                (target["bbox"][0] + target["bbox"][2]) / 2,
                target["bbox"][3],
            ]
        else:
            ground_point = target["center"]
        dx = ground_point[0] - player_screen_x
        dy = ground_point[1] - player_screen_y
        length = math.hypot(dx, dy) or 1.0
        stick = {"dx": dx / length, "dy": dy / length}
    else:
        stick = None

    guide_stick = None
    if guide_target:
        gdx = guide_target["center"][0] - player_screen_x
        gdy = guide_target["center"][1] - player_screen_y
        glen = math.hypot(gdx, gdy) or 1.0
        guide_stick = {"dx": gdx / glen, "dy": gdy / glen}

    # ---- Step 6: End card detection ----
    end_card = _detect_end_card(pix, w, h)

    # ---- Step 7: Station cues (coin / cargo) ----
    station_cue = _detect_station_cue(pix, player_screen_x, player_screen_y)

    result: dict[str, Any] = {
        "target": target,
        "targetKind": "arrow" if is_arrow else ("cyan" if target else "none"),
        "groundPoint": [round(target["center"][0], 2), round(target["center"][1], 2)] if target else None,
        "stick": stick,
        "guideTarget": guide_target,
        "guideStick": guide_stick,
        "arrowCandidates": arrow_candidates[:10],
        "guideCandidates": guide_candidates[:12],
        "components": components[:20],
        "stationCue": station_cue,
        "endCard": end_card,
        "player": [player_screen_x, player_screen_y],
        "image": [w, h],
    }
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connected_components(
    mask: np.ndarray,
    min_area: int = 18,
    player_x: float = 375.0,
    player_y: float = 667.0,
) -> list[dict[str, Any]]:
    """Find connected components in a boolean 2D mask."""
    from scipy import ndimage as ndi

    labeled, num_features = ndi.label(mask)
    components = []
    for i in range(1, num_features + 1):
        ys, xs = np.where(labeled == i)
        area = len(xs)
        if area < min_area:
            continue
        cx = float(xs.mean())
        cy = float(ys.mean())
        dist = math.hypot(cx - player_x, cy - player_y)
        components.append({
            "area": area,
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            "center": [round(cx, 2), round(cy, 2)],
            "width": int(xs.max()) - int(xs.min()) + 1,
            "height": int(ys.max()) - int(ys.min()) + 1,
            "distanceFromPlayer": round(dist, 2),
            "score": round(dist + min(area, 1200) * 0.04 + max(int(xs.max()) - int(xs.min()) + 1, int(ys.max()) - int(ys.min()) + 1) * 0.8, 2),
        })
    components.sort(key=lambda c: c["score"], reverse=True)
    return components


def _detect_end_card(pix: np.ndarray, w: int, h: int) -> dict[str, Any]:
    """Detect end-card buttons (orange/gold play buttons)."""
    r, g, b = pix[:, :, 0].astype(int), pix[:, :, 1].astype(int), pix[:, :, 2].astype(int)
    # Orange/gold button color: r>=220, g>=135, b<=95, r-g<=130
    button_mask = (
        (r >= 220) & (g >= 135) & (b <= 95) & ((r - g) <= 130)
    )
    # Crop to lower-center region
    x0, x1 = int(w * 0.12), int(w * 0.88)
    y0, y1 = int(h * 0.45), int(h * 0.78)
    button_mask[:y0, :] = False
    button_mask[y1:, :] = False
    button_mask[:, :x0] = False
    button_mask[:, x1:] = False

    from scipy import ndimage as ndi
    labeled, num_features = ndi.label(button_mask)
    buttons = []
    for i in range(1, num_features + 1):
        ys, xs = np.where(labeled == i)
        area = len(xs)
        if area < 8000:
            continue
        width = int(xs.max()) - int(xs.min()) + 1
        height = int(ys.max()) - int(ys.min()) + 1
        if width < 250 or height < 45:
            continue
        cx = float(xs.mean())
        if abs(cx - w / 2) > w * 0.22:
            continue
        buttons.append({
            "area": area,
            "center": [round(cx, 2), round(float(ys.mean()), 2)],
            "width": width,
            "height": height,
        })

    return {
        "largePlayButton": bool(buttons),
        "buttonComponents": buttons[:3],
    }


def _detect_station_cue(
    pix: np.ndarray,
    player_x: float,
    player_y: float,
) -> dict[str, Any]:
    """Detect coins and cargo near the player."""
    r, g, b = pix[:, :, 0].astype(int), pix[:, :, 1].astype(int), pix[:, :, 2].astype(int)

    # Local search region
    local_x0 = int(player_x - 185)
    local_x1 = int(player_x + 185)
    local_y0 = int(player_y - 260)
    local_y1 = int(player_y + 190)

    # Coin detection (gold)
    coin_mask = (
        (r >= 185) & (g >= 125) & (b <= 90)
        & ((r - b) >= 105) & ((g - b) >= 55)
    )
    coin_mask[:local_y0, :] = False
    coin_mask[local_y1:, :] = False
    coin_mask[:, :local_x0] = False
    coin_mask[:, local_x1:] = False

    from scipy import ndimage as ndi
    labeled, _ = ndi.label(coin_mask)
    coins = _components_from_labeled(labeled, min_area=18, player_x=player_x, player_y=player_y)
    coins = [c for c in coins if 4 <= c["width"] <= 80 and 4 <= c["height"] <= 80 and c["distanceFromPlayer"] <= 180]
    coins.sort(key=lambda c: (c["area"], -c["distanceFromPlayer"]), reverse=True)

    # Red cargo detection
    cargo_mask = (
        (r >= 165) & (g <= 115) & (b <= 105)
        & ((r - g) >= 45) & ((r - b) >= 45)
    )
    cargo_x0 = int(player_x - 80)
    cargo_x1 = int(player_x + 80)
    cargo_y0 = int(player_y - 360)
    cargo_y1 = int(player_y + 20)
    cargo_mask[:cargo_y0, :] = False
    cargo_mask[cargo_y1:, :] = False
    cargo_mask[:, :cargo_x0] = False
    cargo_mask[:, cargo_x1:] = False
    labeled_cargo, _ = ndi.label(cargo_mask)
    cargo_comps = _components_from_labeled(labeled_cargo, min_area=80, player_x=player_x, player_y=player_y)
    cargo_red_area = sum(c["area"] for c in cargo_comps)

    # Carried coin cargo detection
    carried_mask = (
        (r >= 185) & (g >= 125) & (b <= 95)
        & ((r - b) >= 95) & ((g - b) >= 45)
    )
    carried_mask[:cargo_y0, :] = False
    carried_mask[cargo_y1:, :] = False
    carried_mask[:, :cargo_x0] = False
    carried_mask[:, cargo_x1:] = False
    labeled_carried, _ = ndi.label(carried_mask)
    carried_coins = _components_from_labeled(labeled_carried, min_area=18, player_x=player_x, player_y=player_y)
    carried_coins = [
        c for c in carried_coins
        if c["center"][1] <= player_y - 18
        and abs(c["center"][0] - player_x) <= 90
        and 4 <= c["width"] <= 95
        and 4 <= c["height"] <= 95
    ]
    carried_coin_area = sum(c["area"] for c in carried_coins)
    carried_coin_height_span = (
        max(c["bbox"][3] for c in carried_coins) - min(c["bbox"][1] for c in carried_coins) + 1
        if carried_coins else 0
    )

    return {
        "needsCoin": bool(coins),
        "coinComponents": coins[:5],
        "carryingRedCargo": cargo_red_area >= 650,
        "cargoRedArea": cargo_red_area,
        "carryingCoinCargo": carried_coin_area >= 700 or carried_coin_height_span >= 95,
        "coinCargoArea": carried_coin_area,
        "coinCargoHeightSpan": carried_coin_height_span,
        "coinCargoComponents": sorted(carried_coins, key=lambda c: (c["area"], c["height"]), reverse=True)[:8],
        "searchBox": [local_x0, local_y0, local_x1, local_y1],
    }


def _components_from_labeled(
    labeled: np.ndarray,
    min_area: int = 18,
    player_x: float = 375.0,
    player_y: float = 667.0,
) -> list[dict[str, Any]]:
    """Extract component stats from a labeled array."""
    n_features = labeled.max()
    components = []
    for i in range(1, n_features + 1):
        ys, xs = np.where(labeled == i)
        area = len(xs)
        if area < min_area:
            continue
        components.append({
            "area": area,
            "center": [round(float(xs.mean()), 2), round(float(ys.mean()), 2)],
            "width": int(xs.max()) - int(xs.min()) + 1,
            "height": int(ys.max()) - int(ys.min()) + 1,
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            "distanceFromPlayer": round(math.hypot(float(xs.mean()) - player_x, float(ys.mean()) - player_y), 2),
        })
    return components
