#!/usr/bin/env python3
"""Mode 1 + Mode 5 experiments: 3 games x 10 steps each."""
import asyncio, json, time, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OPENCODE_API_KEY"] = "sk-kYOPPubbbh519b6HuZSabdcuDVBh8iENR8TPElifWzjys98vYNiFr0Z4Dz6c2wXz"
from src.agent.hybrid_agent import HybridAgent
from src.agent.api_client import OpenCodeGoClient

GAMES = [
    ("SSD_00848P01", "playable-agent-12-games-20260608/playables/SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html"),
    ("SSD_00853P01", "playable-agent-12-games-20260608/playables/SSD_00853P01_EN_TZQ_20260430_SH_Applovin_货车很急^有埋点.html"),
    ("SSD_00862P01", "playable-agent-12-games-20260608/playables/SSD_00862P01_EN_TZQ_20260430_DLCX_Applovin_切木鱼^有埋点.html"),
]

async def run_one(gid, gpath, mode, steps=10):
    t0 = time.time()
    try:
        client = OpenCodeGoClient()
        agent = HybridAgent(mode=mode, game_id=gid, api_client=client)
        r = await agent.run_game(gpath, max_steps=steps, headed=False)
    except Exception as e:
        r = {"completed": False, "win": False, "steps": 0, "reason": str(e)[:200]}
    r["game_id"] = gid
    r["elapsed_s"] = round(time.time() - t0, 1)
    r["mode"] = mode
    s = "WIN" if r.get("win") else ("DONE" if r.get("completed") else "STOP")
    print(f"  [{mode}] {gid}: {s} steps={r['steps']} time={r['elapsed_s']}s", flush=True)
    return r

async def main():
    results = []
    print("=== Mode 1: Direct API (DeepSeek text) ===", flush=True)
    for gid, gp in GAMES:
        print(f"  Starting {gid}...", flush=True)
        r = await run_one(gid, gp, "api", 10)
        results.append(r)
    json.dump(results, open("experiment_mode1.json","w"), indent=2, ensure_ascii=False)
    print(f"Mode 1 saved ({len(results)})", flush=True)

    print("=== Mode 5: API -> Rules -> Rule Engine ===", flush=True)
    for gid, gp in GAMES:
        print(f"  Starting {gid}...", flush=True)
        r = await run_one(gid, gp, "api-rule", 10)
        results.append(r)
    json.dump(results, open("experiment_mode5.json","w"), indent=2, ensure_ascii=False)
    print(f"Mode 5 saved ({len(results)})", flush=True)

    # Summary
    print("\n=== ALL RESULTS ===", flush=True)
    for r in results:
        print(f"  [{r['mode']}] {r.get('game_id','?')}: steps={r['steps']} time={r['elapsed_s']}s", flush=True)

asyncio.run(main())
