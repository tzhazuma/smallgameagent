#!/usr/bin/env python3
"""Capacity control for the tasks platform.

The platform runs 6 tasks concurrently (shared with classmates). Policy:
  - Night (20:00 - 07:59): our A_ tasks may use the full 6 slots.
  - Day   (08:00 - 19:59): keep only MAX_DAY_RUNNING (default 3) of our tasks
    running so classmates get room.

Actions (idempotent):
  1. List tasks; find our A_* running tasks.
  2. If daytime and running_count > MAX_DAY_RUNNING: cancel the newest
     (by created_at) surplus tasks.
  3. Log actions to a local file.
"""
import json
import time
import urllib.request
from pathlib import Path
from datetime import datetime

API = "http://34.24.205.23:4097"
OUT = Path("/home/azuma/Downloads/smallgameagent/fusion-harness/results")
MAX_DAY_RUNNING = 3
DAY_START_H = 8
DAY_END_H = 20


def get(path: str) -> dict | list:
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def post(path: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    now = datetime.now()
    is_day = DAY_START_H <= now.hour < DAY_END_H
    tasks = get("/api/tasks")
    mine = [
        t for t in tasks
        if t.get("title", "").startswith("A_") and t.get("status") == "running"
    ]
    mine.sort(key=lambda t: t.get("createdAt", 0))
    log = {
        "ts": now.isoformat(),
        "is_day": is_day,
        "my_running": len(mine),
        "action": "none",
    }
    if is_day and len(mine) > MAX_DAY_RUNNING:
        surplus = mine[MAX_DAY_RUNNING:]
        cancelled = []
        for t in surplus:
            try:
                post(f"/api/tasks/{t['id']}/cancel")
                cancelled.append(f"{t.get('title')}({t['id'][:8]})")
            except Exception as e:
                cancelled.append(f"{t.get('title')}ERR:{e}")
        log["action"] = f"cancelled surplus: {', '.join(cancelled)}"
    else:
        log["action"] = ("day_ok" if is_day else "night_full_ok")
    with open(OUT / "capacity-control.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(log, ensure_ascii=False) + "\n")
    print(json.dumps(log, ensure_ascii=False))


if __name__ == "__main__":
    main()
