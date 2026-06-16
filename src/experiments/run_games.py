#!/usr/bin/env python3
"""Run experiments on real games with different modes.

Tests Mode 6 (Pure Rule Engine) and Mode 1 (Direct API) on multiple games.
Reports: steps, win/completion, latency, loss, and action distribution.
"""

import asyncio
import json
import os
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.hybrid_agent import HybridAgent
from src.agent.api_client import OpenCodeGoClient

GAMES = {
    "SSD_00848P01": "playable-agent-12-games-20260608/playables/SSD_00848P01_EN_WZW_20260429_SH_Applovin_传送带种地^有埋点.html",
    "SSD_00853P01": "playable-agent-12-games-20260608/playables/SSD_00853P01_EN_TZQ_20260430_SH_Applovin_货车很急^有埋点.html",
    "SSD_00862P01": "playable-agent-12-games-20260608/playables/SSD_00862P01_EN_TZQ_20260430_DLCX_Applovin_切木鱼^有埋点.html",
    "SSD_00864P01": "playable-agent-12-games-20260608/playables/SSD_00864P01_EN_BYH_20260430_SH_Applovin_打丧尸避难所卡通版^有埋点.html",
    "SSD_00867P01": "playable-agent-12-games-20260608/playables/SSD_00867P01_EN_LSS_20260430_SH_Applovin_招工卖木材^有埋点.html",
}

MAX_STEPS = 30
HEADED = False


async def run_experiment(mode, game_id, game_path, steps=MAX_STEPS, api_client=None, vlm_engine=None):
    """Run one experiment and return results."""
    t0 = time.time()
    try:
        agent = HybridAgent(
            mode=mode, game_id=game_id, api_client=api_client, vlm_engine=vlm_engine,
        )
        result = await agent.run_game(game_path, max_steps=steps, headed=HEADED)
    except Exception as exc:
        result = {"completed": False, "win": False, "steps": 0, "reason": str(exc)[:200]}
    elapsed = time.time() - t0
    result["elapsed_s"] = round(elapsed, 1)
    result["game_id"] = game_id
    result["mode"] = mode
    return result


def format_result(r):
    status = "WIN" if r.get("win") else ("DONE" if r.get("completed") else "STOP")
    return (
        f"  [{r.get('mode','?')}] {r.get('game_id','?')}: "
        f"status={status}  steps={r.get('steps',0)}  "
        f"time={r.get('elapsed_s',0)}s  reason={r.get('reason','')}"
    )


async def run_all():
    os.environ.setdefault("OPENCODE_API_KEY", "sk-placeholder")

    api_client = OpenCodeGoClient()

    results = []

    # --- Mode 6: Pure Rule Engine on all 5 games ---
    print("\n" + "="*60)
    print("EXPERIMENT 1: Mode 6 (Pure Rule Engine)")
    print("="*60)
    for game_id, game_path in GAMES.items():
        print(f"\n  Running {game_id}...")
        r = await run_experiment("rule", game_id, game_path)
        results.append(r)
        print(format_result(r))

    # --- Mode 1: Direct API on first game ---
    print("\n" + "="*60)
    print("EXPERIMENT 2: Mode 1 (Direct API: DeepSeek + Mimo)")
    print("="*60)
    print(f"\n  Running SSD_00848P01...")
    r = await run_experiment("api", "SSD_00848P01", GAMES["SSD_00848P01"], steps=10, api_client=api_client)
    results.append(r)
    print(format_result(r))

    # --- Summary ---
    print("\n" + "="*60)
    print("RESULTS SUMMARY")
    print("="*60)
    for r in results:
        print(format_result(r))

    # Save results
    output_path = Path("experiment_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")

    return results


if __name__ == "__main__":
    asyncio.run(run_all())
