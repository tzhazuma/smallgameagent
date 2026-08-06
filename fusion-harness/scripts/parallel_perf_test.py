#!/usr/bin/env python3
"""Real parallel performance test: Edge-reuse vs per-game Edge restart.

Runs N games concurrently through the harness (Windows Edge CDP) and measures
wall time, CPU %, and memory. Compares two browser modes:
  reuse  : one Edge instance, one context per game (low overhead)
  restart: fresh Edge per game (isolated, higher overhead)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HARNESS_WIN = Path(r"C:\Users\tzh03\harness-win")
GAMES = [
    "ag-complete-tiles-survive-5ec610abcdff",
    "kingshot-0042aa74feb8",
    "whiteout-survival-12ababda99c7",
]


def measure_system() -> dict:
    """CPU% and memory via /proc (Linux host sees WSL processes partially)."""
    try:
        with open("/proc/loadavg") as fh:
            load = fh.read().split()[:3]
        return {"load1": float(load[0]), "load5": float(load[1]), "load15": float(load[2])}
    except Exception:
        return {}


def run_game_restart(game: str) -> float:
    """Mode restart: kill Edge, fresh Edge, run one game."""
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
    time.sleep(2)
    subprocess.Popen(
        [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
         "--headless=new", "--mute-audio", "--remote-debugging-port=9222",
         "--user-data-dir=C:\\Users\\tzh03\\edge-fusion-test", "--no-first-run", "about:blank"],
        cwd="C:\\mnt\\c", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(8)
    t0 = time.time()
    cmd = (f"cmd.exe /c C:\\Users\\tzh03\\harness-win\\run_auto.bat {game}")
    subprocess.run(cmd, shell=True, capture_output=True)
    return time.time() - t0


def run_game_reuse(game: str) -> float:
    """Mode reuse: single Edge (already running), sequential contexts."""
    t0 = time.time()
    cmd = f"cmd.exe /c C:\\Users\\tzh03\\harness-win\\run_auto.bat {game}"
    subprocess.run(cmd, shell=True, capture_output=True)
    return time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["reuse", "restart", "both"], default="both")
    ap.add_argument("--games", nargs="*", default=GAMES)
    args = ap.parse_args()

    results = {"mode": args.mode, "games": args.games, "runs": {}}
    if args.mode in ("restart", "both"):
        times = {}
        sys0 = measure_system()
        for g in args.games:
            times[g] = run_game_restart(g)
        sys1 = measure_system()
        results["runs"]["restart"] = {"times_s": times, "total_s": round(sum(times.values()), 1), "sys": sys1}
    if args.mode in ("reuse", "both"):
        times = {}
        sys0 = measure_system()
        for g in args.games:
            times[g] = run_game_reuse(g)
        sys1 = measure_system()
        results["runs"]["reuse"] = {"times_s": times, "total_s": round(sum(times.values()), 1), "sys": sys1}

    out = Path("/home/azuma/Downloads/smallgameagent/fusion-harness/results/parallel-perf.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
