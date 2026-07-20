#!/usr/bin/env python3
"""Generate the final comprehensive report from all experiment JSONs.

Reads every experiment_*.json in the project root, aggregates results,
and writes a summary table into EXPERIMENT_RESULTS.md and REPORT.md.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

EXPERIMENT_FILES = [
    "experiment_multi_agent_matrix.json",
    "experiment_memory_readback.json",
    "experiment_critic_ab.json",
    "experiment_vlm_local_gameplay.json",
    "experiment_cloud_api_matrix.json",
    "experiment_cloud_api_struct.json",
    "experiment_cloud_api_gameplay.json",
    "experiment_hierarchical.json",
    "experiment_api_strategy.json",
    "experiment_vlm_pipeline.json",
    "experiment_local_vlm_matrix.json",
    "experiment_vlm_local_matrix.json",
    "multi_game_results/batch_results.json",
    "full_matrix_results/batch_results_all.json",
    "configs/auto_calibrated_profiles.json",
]


def _load_json(path: Path) -> list | dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def generate_summary() -> str:
    """Generate a comprehensive summary of all experiments."""
    lines = ["# 大规模实验综合分析\n"]

    # --- Auto-calibration summary ---
    cal = _load_json(ROOT / "configs/auto_calibrated_profiles.json")
    if cal:
        valid = [r for r in cal if r.get("valid")]
        invalid = [r for r in cal if not r.get("valid")]
        lines.append("## 自动校准结果\n")
        lines.append(f"- 总游戏数: {len(cal)}")
        lines.append(f"- joystick 驱动 (A 类): {len(valid)}")
        lines.append(f"- tap-to-move (B 类): {len(invalid)}")
        lines.append("")
        lines.append("| 游戏 | 类型 | basis |")
        lines.append("|---|---|---|")
        for r in cal:
            gid = r["game_id"]
            if r.get("valid") and r.get("basis"):
                b = r["basis"]
                lines.append(
                    f"| {gid} | A (joystick) | "
                    f"({b['screen_right']['x']:.2f},{b['screen_right']['z']:.2f}) / "
                    f"({b['screen_down']['x']:.2f},{b['screen_down']['z']:.2f}) |"
                )
            else:
                lines.append(f"| {gid} | B (tap-only) | — |")
        lines.append("")

    # --- Full matrix summary ---
    fm = _load_json(ROOT / "full_matrix_results" / "batch_results_all.json")
    if fm:
        lines.append("## 全游戏 × 多模式批量矩阵\n")
        ok = [r for r in fm if not r.get("error")]
        err = [r for r in fm if r.get("error")]
        lines.append(f"- 总 runs: {len(fm)}, 成功: {len(ok)}, 失败: {len(err)}")
        lines.append("")

        # Per-mode stats
        by_mode: dict[str, list[float]] = defaultdict(list)
        for r in ok:
            by_mode[r["mode"]].append(r["composite"])
        lines.append("### Per-Mode 平均 Composite\n")
        lines.append("| 模式 | Runs | Mean Composite | Mean Activity |")
        lines.append("|---|---|---|---|")
        for mode in sorted(by_mode):
            comps = by_mode[mode]
            acts = [r["activity"] for r in ok if r["mode"] == mode]
            lines.append(
                f"| {mode} | {len(comps)} | {sum(comps)/len(comps):.3f} | "
                f"{sum(acts)/len(acts):.3f} |"
            )
        lines.append("")

        # Per-game best mode
        by_game: dict[str, list[dict]] = defaultdict(list)
        for r in ok:
            by_game[r["game_id"]].append(r)
        lines.append("### Per-Game 最佳模式\n")
        lines.append("| 游戏 | 最佳模式 | Composite | Activity |")
        lines.append("|---|---|---|---|")
        for gid in sorted(by_game):
            runs = by_game[gid]
            best = max(runs, key=lambda r: r["composite"])
            lines.append(
                f"| {gid} | {best['mode']} | {best['composite']:.3f} | {best['activity']:.3f} |"
            )
        lines.append("")

    # --- Key experiments summary ---
    lines.append("## 关键实验汇总\n")

    # Memory readback
    mem = _load_json(ROOT / "experiment_memory_readback.json")
    if mem:
        lines.append("### 记忆读回 A/B\n")
        lines.append("| 阶段 | composite | memory_hits |")
        lines.append("|---|---|---|")
        for r in mem:
            lines.append(f"| {r['name']} | {r['composite']:.3f} | {r.get('memory_hits', 0)} |")
        lines.append("")

    # Critic A/B
    crit = _load_json(ROOT / "experiment_critic_ab.json")
    if crit:
        lines.append("### Critic 反馈循环 A/B\n")
        lines.append("| 配置 | composite | bus_messages | critic_invocations |")
        lines.append("|---|---|---|---|")
        for r in crit:
            lines.append(
                f"| {r['name']} | {r['composite']:.3f} | "
                f"{r['bus_messages']} | {r['critic_invocations']} |"
            )
        lines.append("")

    # VLM local gameplay
    vlm = _load_json(ROOT / "experiment_vlm_local_gameplay.json")
    if vlm:
        lines.append("### 本地 VLM 闭环\n")
        lines.append("| 模式 | composite | activity | latency |")
        lines.append("|---|---|---|---|")
        for r in vlm:
            lines.append(
                f"| {r['name']} | {r['composite']:.3f} | "
                f"{r['activity']:.3f} | {r['elapsed_s']}s |"
            )
        lines.append("")

    # Cloud API gameplay
    api = _load_json(ROOT / "experiment_cloud_api_gameplay.json")
    if api:
        lines.append("### 云端 API 在线 gameplay\n")
        lines.append("| 模型 | composite | move | tap | stall | latency |")
        lines.append("|---|---|---|---|---|---|")
        for r in api:
            d = r.get("details", {})
            lines.append(
                f"| {r.get('text_model', r['name'])} | {r['composite']:.3f} | "
                f"{d.get('move_steps', 0)} | {d.get('tap_steps', 0)} | "
                f"{d.get('stall_steps', 0)} | {r['elapsed_s']}s |"
            )
        lines.append("")

    # Local VLM struct benchmark
    vlm_struct = _load_json(ROOT / "experiment_local_vlm_matrix.json")
    if vlm_struct:
        lines.append("### 本地 VLM struct 基准\n")
        lines.append("| 模型 | 解析成功 | 平均墙钟/帧 | 生成 tok/s |")
        lines.append("|---|---|---|---|")
        for r in vlm_struct:
            s = r.get("summary", {})
            lines.append(
                f"| {r['model']} | {s.get('n_success', 0)}/{s.get('n_frames', 0)} | "
                f"{s.get('mean_latency_s', 'N/A')}s | {s.get('mean_gen_tok_s', 'N/A')} |"
            )
        lines.append("")

    # API strategy (Phase 4)
    api_strat = _load_json(ROOT / "experiment_api_strategy.json")
    if api_strat:
        lines.append("### 云端 API 策略生成\n")
        lines.append("| 游戏 | 模式 | composite | activity | latency/step |")
        lines.append("|---|---|---|---|---|")
        for r in api_strat:
            lines.append(
                f"| {r['game_id']} | {r['name']} | {r['composite']:.3f} | "
                f"{r['activity']:.3f} | {r['latency_per_step']}s |"
            )
        lines.append("")

    # VLM pipeline (Phase 5)
    vlm_pipe = _load_json(ROOT / "experiment_vlm_pipeline.json")
    if vlm_pipe:
        lines.append("### VLM 视觉管线\n")
        lines.append("| 游戏 | 模式 | composite | tap | stall | latency/step |")
        lines.append("|---|---|---|---|---|---|")
        for r in vlm_pipe:
            lines.append(
                f"| {r['game_id']} | {r['name']} | {r['composite']:.3f} | "
                f"{r['tap_steps']} | {r['stall_steps']} | {r['latency_per_step']}s |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    summary = generate_summary()
    out_path = ROOT / "experiment_summary.md"
    out_path.write_text(summary, encoding="utf-8")
    print(f"Summary written to {out_path}")
    print(summary[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
