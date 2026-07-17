#!/usr/bin/env python3
"""Mode 5 experiment: API -> Rules -> Rule Engine on 3 games x 20 steps."""
import asyncio
import json
import time
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENCODE_API_KEY", "sk-placeholder")
from src.agent.hybrid_agent import HybridAgent
from src.agent.api_client import OpenCodeGoClient

GAMES = [
    ("SSD_00848P01", "playable-agent-12-games-20260608/playables/SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html"),
    ("SSD_00853P01", "playable-agent-12-games-20260608/playables/SSD_00853P01_EN_TZQ_20260430_SH_Applovin_货车很急^有埋点.html"),
    ("SSD_00862P01", "playable-agent-12-games-20260608/playables/SSD_00862P01_EN_TZQ_20260430_DLCX_Applovin_切木鱼^有埋点.html"),
]

async def main():
    client = OpenCodeGoClient()
    print("=== Mode 5: API -> Rules -> Rule Engine ===", flush=True)
    results = []
    for gid, gpath in GAMES:
        t0 = time.time()
        try:
            agent = HybridAgent(mode="api-rule", game_id=gid, api_client=client)
            r = await agent.run_game(gpath, max_steps=20, headed=False)
        except Exception as e:
            r = {"completed": False, "win": False, "steps": 0, "reason": str(e)[:200]}
        r["game_id"] = gid
        r["elapsed_s"] = round(time.time() - t0, 1)
        r["mode"] = "api-rule"
        status = "WIN" if r.get("win") else ("DONE" if r.get("completed") else "STOP")
        print(f"  {gid}: {status} steps={r['steps']} time={r['elapsed_s']}s", flush=True)
        results.append(r)
    with open("experiment_mode5.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(results)} results", flush=True)

asyncio.run(main())
