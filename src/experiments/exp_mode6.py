#!/usr/bin/env python3
"""Mode 6 experiment: Pure Rule Engine on 5 games x 50 steps."""
import asyncio
import json
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.agent.hybrid_agent import HybridAgent

GAMES = [
    ("SSD_00848P01", "follow-guide-audited", "playable-agent-12-games-20260608/playables/SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html"),
    ("SSD_00853P01", "2d-audited", "playable-agent-12-games-20260608/playables/SSD_00853P01_EN_TZQ_20260430_SH_Applovin_货车很急^有埋点.html"),
    ("SSD_00862P01", "learned", "playable-agent-12-games-20260608/playables/SSD_00862P01_EN_TZQ_20260430_DLCX_Applovin_切木鱼^有埋点.html"),
    ("SSD_00864P01", "target-arrow", "playable-agent-12-games-20260608/playables/SSD_00864P01_EN_BYH_20260430_SH_Applovin_打丧尸避难所卡通版^有埋点.html"),
    ("SSD_00867P01", "guide-follow", "playable-agent-12-games-20260608/playables/SSD_00867P01_EN_LSS_20260430_SH_Applovin_招工卖木材^有埋点.html"),
]

async def run_one(gid, dt, gpath, steps=50):
    t0 = time.time()
    try:
        agent = HybridAgent(mode="rule", game_id=gid)
        r = await agent.run_game(gpath, max_steps=steps, headed=False)
    except Exception as e:
        r = {"completed": False, "win": False, "steps": 0, "reason": str(e)[:200]}
    r["game_id"] = gid
    r["driver_type"] = dt
    r["elapsed_s"] = round(time.time() - t0, 1)
    r["mode"] = "rule"
    status = "WIN" if r.get("win") else ("DONE" if r.get("completed") else "STOP")
    print(f"  {gid} ({dt}): {status} steps={r['steps']} time={r['elapsed_s']}s")
    return r

async def main():
    print("=== Mode 6: Pure Rule Engine (50 steps x 5 games) ===", flush=True)
    results = []
    for gid, dt, gpath in GAMES:
        r = await run_one(gid, dt, gpath)
        results.append(r)
    with open("experiment_mode6.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(results)} results to experiment_mode6.json", flush=True)

asyncio.run(main())
