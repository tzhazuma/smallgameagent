#!/usr/bin/env python3
"""Monitor all A_ tasks on the cloud platform and log snapshots.

Appends a JSON snapshot to fusion-harness/results/tasks-snapshots.jsonl every
run; also writes a human summary to tasks-status.md.
"""
import json
import time
import urllib.request
from pathlib import Path

API = "http://34.24.205.23:4097"
OUT = Path("/home/azuma/Downloads/smallgameagent/fusion-harness/results")


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    tasks = get("/api/tasks")
    snapshot = {
        "ts": int(time.time() * 1000),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "tasks": [],
    }
    for t in tasks:
        title = t.get("title", "")
        if not title.startswith("A_"):
            continue
        snapshot["tasks"].append({
            "title": title,
            "id": t["id"],
            "status": t.get("status"),
            "model": t.get("model"),
            "effort": t.get("effort"),
        })
    with open(OUT / "tasks-snapshots.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    # Human summary
    lines = [f"# 任务状态快照 {snapshot['iso']}", ""]
    for t in snapshot["tasks"]:
        lines.append(f"- **{t['title']}** ({t['id']}): `{t['status']}`")
    (OUT / "tasks-status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"snapshot written: {len(snapshot['tasks'])} tasks")


if __name__ == "__main__":
    main()
