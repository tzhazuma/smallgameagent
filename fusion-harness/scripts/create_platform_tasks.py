#!/usr/bin/env python3
"""批量创建云端平台任务（zhihao-test 分支 / gpt-5.6-luna / no_vlm_codex_session）。

特性:
- URL 去重: 拒绝与已有任务或历史任务重复的游戏 URL (修复 A_10 与 A_02.2 重复问题)
- 白天/夜间容量感知提示
- 幂等创建: 打印 taskId 供后续跟踪
"""
from __future__ import annotations

import json
import sys
import urllib.request

PLATFORM = "http://34.24.205.23:4097"
REPO = "https://github.com/fps-research/game-agent-harness"
BRANCH_MODE = "no_vlm_codex_session"

PROMPT_TEMPLATE = """从 https://github.com/fps-research/game-agent-harness 的 zhihao-test 分支最新提交，以 no_vlm_codex_session 模式运行html网页，尝试的地址在下面，自主探索直至通关或框架达到明确终止条件。
尝试地址：{url}
开始前必须核验仓库根目录、HEAD、remote 和工作树；确认使用的是我这个分支下的最新的版本。游戏独立工作区必须建立在当前仓库的 games/{gid}/ 下，只把网页下载的html文件复制到 input/，不得继承旧游戏的配置、探针、记忆、策略或 runs。
必须连接平台 managed Chromium 的 CDP，不得自行启动浏览器或回退到 Xvfb。严格通过框架探针、规划器和确定性动作审批链执行；不修改游戏文件，不绕过框架控制游戏，也不得把游戏名称、节点名称、资源名称、坐标或专用策略写入通用框架。
首次通关后保存 source capsule 和稳定策略，并从干净初始状态独立复现。只有 fixed evaluator 为 SETTLED_COMPLETE、acceptance gate 通过且适用的 stage audit 通过后，才报告完成。
时间限制：从任务开始运行起总时长不得超过 2 小时。若接近 2 小时仍未达成通关，必须停止新的探索，完成当前 run 的评估与产物保存（run-report、acceptance-gate、events 等），按框架明确终止条件收尾并汇报；不得无限期继续探索。
最后汇报实际分支与完整 SHA、game ID、run ID、游戏结果、独立复现结果、浏览器/CDP 模式、最终停止的原因及主要输出的路径。"""


def api_get(path: str):
    with urllib.request.urlopen(f"{PLATFORM}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def api_post(path: str, body: dict):
    req = urllib.request.Request(
        f"{PLATFORM}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def collect_existing_urls() -> set[str]:
    """收集平台上所有任务的尝试 URL，用于去重。"""
    urls: set[str] = set()
    try:
        tasks = api_get("/api/tasks")
        if isinstance(tasks, dict):
            tasks = tasks.get("tasks", tasks.get("data", []))
        for t in tasks:
            prompt = t.get("prompt", "")
            for line in prompt.splitlines():
                line = line.strip()
                if "playable-html-proxy" in line and "/index.html" in line:
                    url = line.split("尝试地址：")[-1].strip()
                    urls.add(url)
    except Exception as e:  # noqa: BLE001 - 平台不可用时降级为仅本地
        print(f"[warn] 无法拉取平台任务列表做去重: {e}", file=sys.stderr)
    return urls


def create_task(title: str, url: str, existing: set[str], dry_run: bool = False) -> str | None:
    game = url.split("/st-complete/")[-1].split("/index.html")[0]
    gid = game.replace("/", "-")
    if url in existing:
        print(f"[skip] {title} 与已有任务 URL 重复: {game}")
        return None
    body = {
        "repo": REPO,
        "prompt": PROMPT_TEMPLATE.format(url=url, gid=gid),
        "title": title,
        "mode": "standard",
        "backend": "codex",
        "model": "gpt-5.6-luna",
        "effort": "xhigh",
    }
    if dry_run:
        print(f"[dry] {title} -> {gid}")
        return None
    try:
        r = api_post("/api/tasks", body)
        print(f"[ok] {title} -> {r.get('taskId', r)}")
        return r.get("taskId")
    except Exception as e:  # noqa: BLE001
        print(f"[fail] {title}: {e}")
        return None


def main() -> None:
    games_file = sys.argv[1] if len(sys.argv) > 1 else None
    if not games_file or not len(sys.argv) > 2:
        print("用法: create_platform_tasks.py <games.txt> <起始编号> [dry]")
        print("games.txt 每行: <game>/<id>#<url> 或纯 <url>")
        sys.exit(1)
    prefix = sys.argv[2]
    dry = len(sys.argv) > 3 and sys.argv[3] == "dry"

    existing = collect_existing_urls()
    print(f"已有任务 URL 数: {len(existing)}")

    with open(games_file) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    n = 0
    for line in lines:
        url = line.split("#")[-1].strip()
        if not url.startswith("http"):
            continue
        n += 1
        create_task(f"{prefix}.{n}", url, existing, dry_run=dry)


if __name__ == "__main__":
    main()
