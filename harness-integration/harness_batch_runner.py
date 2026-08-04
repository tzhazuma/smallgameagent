#!/usr/bin/env python3
"""Batch runner for the fps-play-agent-harness with our cloud models.

Reads a game list (format: `<game> / <id>#<url>`), downloads each game HTML into
its harness workspace, and runs `run:autonomous` for each with a configurable
planner provider (default mimo-v2.5 via xiaomi, multimodal-capable).

Usage:
  python3 harness_batch_runner.py --list games.txt --provider mimo-v2.5 --parallel 2

Env needed (source smallgameagent/.env):
  XIAOMI_API_KEY / QWEN_API_KEY / KIMI_API_KEY
  PLAYWRIGHT_BROWSERS_PATH / PLAYWRIGHT_CHROMIUM_PATH
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HARNESS = Path(os.environ.get("HARNESS_DIR", "/home/azuma/Downloads/fps-play-agent-harness"))


def parse_game_list(path):
    games = []
    for line in Path(path).read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            continue
        game_part, url = line.split("#", 1)
        game, gid = game_part.split(" / ")
        games.append({"game": game, "id": gid, "url": url})
    return games


def ensure_workspace(game, url, headless=False):
    gid = f"{game['game']}-{game['id']}"
    ws_input = HARNESS / "games" / gid / "input"
    ws_input.mkdir(parents=True, exist_ok=True)
    index = ws_input / "index.html"
    if not index.exists():
        print(f"  [download] {gid}", flush=True)
        subprocess.run(["curl", "-s", "-L", "-m", "120", "-o", str(index), url], check=True)
    # Ensure runtime.json is headless:false (xvfb)
    cfg = HARNESS / "games" / gid / "config" / "runtime.json"
    if cfg.exists():
        d = json.loads(cfg.read_text())
        d.setdefault("launch", {})["headless"] = headless
        cfg.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        subprocess.run(["node", "./src/cli.mjs", "game:init", "--game-id", gid, "--title", gid],
                       cwd=HARNESS, capture_output=True)
        d = json.loads(cfg.read_text())
        d.setdefault("launch", {})["headless"] = headless
        cfg.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    return gid


def run_one(game, url, provider, model, timeout_min=40, headless=False):
    gid = ensure_workspace(game, url, headless=headless)
    log = f"/tmp/harness_run_{gid}.log"
    if headless:
        cmd = ["env",
               f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH','')}",
               f"PLAYWRIGHT_CHROMIUM_PATH={os.environ.get('PLAYWRIGHT_CHROMIUM_PATH','')}",
               f"PLAYABLE_PLANNER_PROVIDER=harness_http",
               f"PLAYABLE_PLANNER_ENDPOINT=http://127.0.0.1:9100/plan",
               f"PLAYABLE_PLANNER_MODEL={model}",
               f"PLAYABLE_PLANNER_TOKEN_ENV={provider.upper()}_API_KEY",
               "NODE_USE_ENV_PROXY=1",
               "npm", "run", "run:autonomous", "--", "--game-id", gid, "--cognition-mode", "no_vlm_codex_cli",
        ]
    else:
        cmd = [
        "xvfb-run", "-a", "-s", "-screen 0 1280x1024x24",
        "env",
        f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH','')}",
        f"PLAYWRIGHT_CHROMIUM_PATH={os.environ.get('PLAYWRIGHT_CHROMIUM_PATH','')}",
        f"PLAYABLE_PLANNER_PROVIDER=harness_http",
        f"PLAYABLE_PLANNER_ENDPOINT=http://127.0.0.1:9100/plan",
        f"PLAYABLE_PLANNER_MODEL={model}",
        f"PLAYABLE_PLANNER_TOKEN_ENV={provider.upper()}_API_KEY",
        "NODE_USE_ENV_PROXY=1",
        "npm", "run", "run:autonomous", "--", "--game-id", gid, "--cognition-mode", "no_vlm_codex_cli",
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=HARNESS, timeout=timeout_min * 60,
                              stdout=open(log, "w"), stderr=subprocess.STDOUT)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
    elapsed = round((time.time() - t0) / 60, 1)
    # Extract terminal from latest run-report
    terminal = "?"
    runs = sorted((HARNESS / "games" / gid / "runs").glob("*")) if (HARNESS / "games" / gid / "runs").exists() else []
    for r in reversed(runs):
        rr = r / "run-report.json"
        if rr.exists():
            try:
                terminal = json.loads(rr.read_text()).get("terminal", "?")
            except Exception:
                pass
            break
    return {"game": gid, "rc": rc, "terminal": terminal, "minutes": elapsed, "log": log}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="Game list file (game / id#url per line)")
    ap.add_argument("--provider", default="xiaomi", help="Provider env prefix (xiaomi/qwen/kimi)")
    ap.add_argument("--model", default="mimo-v2.5", help="Model name sent to adapter")
    ap.add_argument("--parallel", type=int, default=1, help="Parallel runs")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of games (0=all)")
    ap.add_argument("--adapter", action="store_true", help="Also start the planner-http-adapter (fallback-first)")
    ap.add_argument("--headless", action="store_true", help="Run headless (no xvfb) when WebGL renders")
    args = ap.parse_args()

    games = parse_game_list(args.list)
    if args.limit:
        games = games[:args.limit]
    print(f"[batch] {len(games)} games, provider={args.provider} model={args.model} parallel={args.parallel}", flush=True)

    if args.adapter:
        print("[batch] starting planner-http-adapter (fallback-first)...", flush=True)
        subprocess.Popen(
            ["bash", "-c",
             f"cd {Path(os.environ['PWD'])} 2>/dev/null; source /home/azuma/Downloads/smallgameagent/.env; "
             f"cd {HARNESS}; PLAYABLE_PLANNER_FALLBACK_FIRST=1 NODE_USE_ENV_PROXY=1 node scripts/planner-http-adapter.mjs"],
            stdout=open("/tmp/batch_adapter.log", "w"), stderr=subprocess.STDOUT,
        )
        time.sleep(3)

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futs = {pool.submit(run_one, g, g["url"], args.provider, args.model, headless=args.headless): g for g in games}
        for fut in as_completed(futs):
            g = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"game": g["game"] + "-" + g["id"], "rc": "EXC", "terminal": str(e)[:60], "minutes": 0, "log": ""}
            results.append(r)
            print(f"  {r['game']}: rc={r['rc']} terminal={r['terminal']} {r['minutes']}min", flush=True)

    summary = Path("/tmp/harness_batch_results.json")
    summary.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n[batch] done -> {summary}")
    for r in results:
        print(f"  {r['game']:45s} {str(r['terminal'])[:30]:30s} {r['minutes']}min")


if __name__ == "__main__":
    main()
