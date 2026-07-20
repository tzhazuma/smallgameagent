#!/usr/bin/env python3
"""Generate a LaTeX PDF report and a PowerPoint deck from REPORT.md."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORT_MD = ROOT / "REPORT.md"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

TEX_PATH = REPORTS_DIR / "smallgameagent_report.tex"
PDF_PATH = REPORTS_DIR / "smallgameagent_report.pdf"
PPTX_PATH = REPORTS_DIR / "smallgameagent_report.pptx"


_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "^": r"\^{}",
    "~": r"\textasciitilde{}",
}


def esc(s: str) -> str:
    """Escape LaTeX special chars in a single pass."""
    return re.sub(r"[\\{}$&%#_^~]", lambda m: _LATEX_SPECIAL[m.group(0)], s)


def inline_fmt(s: str) -> str:
    """Convert markdown inline formatting to LaTeX without double-escaping commands."""
    placeholders: dict[str, Any] = {}
    counter = 0

    def stash(kind: str, content: Any) -> str:
        nonlocal counter
        key = f"__{kind}_{counter:04d}__"
        counter += 1
        placeholders[key] = content
        return key

    s = re.sub(r"`([^`]+)`", lambda m: stash("CODE", m.group(1)), s)
    s = re.sub(r"\*\*([^*]+)\*\*", lambda m: stash("BOLD", m.group(1)), s)
    s = re.sub(r"\*([^*]+)\*", lambda m: stash("ITALIC", m.group(1)), s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: stash("LINK", (m.group(1), m.group(2))), s)

    # Now safe to escape remaining plain text.
    s = esc(s)

    # Restore placeholders with properly escaped content.
    for key, val in placeholders.items():
        if key.startswith("__CODE_"):
            replacement = f"\\texttt{{{esc(val)}}}"
        elif key.startswith("__BOLD_"):
            replacement = f"\\textbf{{{esc(val)}}}"
        elif key.startswith("__ITALIC_"):
            replacement = f"\\textit{{{esc(val)}}}"
        else:  # LINK
            text, url = val
            replacement = f"\\href{{{esc(url)}}}{{{esc(text)}}}"
        s = s.replace(key, replacement)
    return s


def render_table(rows: list[str]) -> str:
    # rows like "| a | b |"
    cells = [[c.strip() for c in r.split("|")[1:-1]] for r in rows]
    if len(cells) < 3:
        return ""
    header = cells[0]
    data = cells[2:]
    n = len(header)
    cols = "|" + "|".join(["l"] * n) + "|"
    lines = ["\\begin{table}[htbp]", "\\centering", "\\small", f"\\begin{{tabular}}{{{cols}}}", "\\hline"]
    lines.append(" & ".join(inline_fmt(c) for c in header) + " \\\\")
    lines.append("\\hline")
    for row in data:
        if len(row) < n:
            row = row + [""] * (n - len(row))
        lines.append(" & ".join(inline_fmt(c) for c in row[:n]) + " \\\\")
        lines.append("\\hline")
    lines.extend(["\\end{tabular}", "\\end{table}"])
    return "\n".join(lines)


_CIRCLED_DIGITS = {chr(0x2460 + i): f"({i+1})" for i in range(20)}


def md_to_latex(md: str) -> str:
    md = "".join(_CIRCLED_DIGITS.get(ch, ch) for ch in md)
    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    in_table = False
    table_rows: list[str] = []

    def close() -> None:
        nonlocal in_list, in_table, table_rows
        if in_list:
            out.append("\\end{itemize}")
            in_list = False
        if in_table:
            out.append(render_table(table_rows))
            table_rows = []
            in_table = False

    for raw in lines:
        stripped = raw.strip()
        # headings
        if stripped.startswith("# "):
            close()
            out.append(f"\\section{{{inline_fmt(stripped[2:])}}}")
            continue
        if stripped.startswith("## "):
            close()
            out.append(f"\\subsection{{{inline_fmt(stripped[3:])}}}")
            continue
        if stripped.startswith("### "):
            close()
            out.append(f"\\subsubsection{{{inline_fmt(stripped[4:])}}}")
            continue
        # table
        if stripped.startswith("|"):
            if not in_table:
                close()
                in_table = True
                table_rows = []
            table_rows.append(stripped)
            continue
        else:
            if in_table:
                out.append(render_table(table_rows))
                table_rows = []
                in_table = False
        # empty
        if not stripped:
            if in_list:
                out.append("\\end{itemize}")
                in_list = False
            out.append("")
            continue
        # bullet
        if stripped.startswith(("- ", "* ")):
            if not in_list:
                out.append("\\begin{itemize}")
                in_list = True
            out.append("  \\item " + inline_fmt(stripped[2:]))
            continue
        # normal paragraph
        if in_list:
            out.append("\\end{itemize}")
            in_list = False
        out.append(inline_fmt(stripped) + "\\\\")
    close()
    return "\n".join(out)


def write_tex(body: str) -> None:
    tex = r"""\documentclass[12pt,a4paper]{ctexart}
\usepackage[margin=2.5cm]{geometry}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{hyperref}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{setspace}
\onehalfspacing

\hypersetup{
  colorlinks=true,
  linkcolor=blue,
  urlcolor=blue,
  citecolor=blue
}

\title{\textbf{smallgameagent 实验报告} \\ \large 基于 LLM/VLM 与规则的小游戏 Agent}
\author{smallgameagent 项目组}
\date{\today}

\begin{document}
\maketitle
\tableofcontents
\newpage
""" + body + "\n\\end{document}\n"
    TEX_PATH.write_text(tex, encoding="utf-8")


def compile_tex() -> None:
    for _ in range(2):
        result = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-output-directory", str(REPORTS_DIR), str(TEX_PATH)],
            cwd=str(REPORTS_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            (REPORTS_DIR / "xelatex.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
            print("xelatex failed; see reports/xelatex.log", file=sys.stderr)
            raise SystemExit(result.returncode)
    print(f"PDF -> {PDF_PATH}")


def write_pptx() -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    def add_title_slide(title: str, subtitle: str) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.3), Inches(1.5))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.size = Pt(40)
        p.font.bold = True
        p.font.name = "Microsoft YaHei"
        p.alignment = PP_ALIGN.CENTER
        tb2 = slide.shapes.add_textbox(Inches(0.5), Inches(4.2), Inches(12.3), Inches(1))
        p2 = tb2.text_frame.paragraphs[0]
        p2.text = subtitle
        p2.font.size = Pt(22)
        p2.font.name = "Microsoft YaHei"
        p2.alignment = PP_ALIGN.CENTER

    def add_bullet_slide(title: str, bullets: list[str]) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(12.3), Inches(0.9))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.name = "Microsoft YaHei"
        content = slide.shapes.add_textbox(Inches(0.6), Inches(1.5), Inches(12.1), Inches(5.6))
        tf = content.text_frame
        tf.word_wrap = True
        for i, b in enumerate(bullets):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = "• " + b
            p.font.size = Pt(20)
            p.font.name = "Microsoft YaHei"
            p.space_after = Pt(10)

    def add_table_slide(title: str, headers: list[str], rows: list[list[str]]) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(12.3), Inches(0.9))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.name = "Microsoft YaHei"
        n_rows = len(rows) + 1
        n_cols = len(headers)
        table = slide.shapes.add_table(n_rows, n_cols, Inches(0.5), Inches(1.5),
                                        Inches(12.3), Inches(5.5)).table
        for j, h in enumerate(headers):
            cell = table.cell(0, j)
            cell.text = h
            for paragraph in cell.text_frame.paragraphs:
                paragraph.font.size = Pt(16)
                paragraph.font.bold = True
                paragraph.font.name = "Microsoft YaHei"
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                cell = table.cell(i + 1, j)
                cell.text = str(val)
                for paragraph in cell.text_frame.paragraphs:
                    paragraph.font.size = Pt(14)
                    paragraph.font.name = "Microsoft YaHei"

    # ---- Slide 1: Title ----
    add_title_slide("smallgameagent 实验报告", "LLM/VLM + 规则驱动的小游戏 Agent\n2026-07-20")

    # ---- Slide 2: 一页结论 ----
    add_bullet_slide("一页结论", [
        "最佳配置：multi-bus + StrategyMemory 读回 = composite 0.300（稳定多 seed）",
        "记忆读回将 composite 从 0.150 翻倍到 0.300——世界模型违规从 3 降到 0",
        "22 个游戏自动分类：7 个 joystick 驱动（A 类）+ 15 个 tap-to-move（B 类）",
        "8/15 B 类游戏 tap-only 驱动达 0.300——tap 目标屏幕坐标即有效交互",
        "云端 API 和本地 VLM 在线闭环均不实用，定位：离线策略生成 + 数据标注",
        "训练数据：24,496 条 7 任务样本（processed-runs 15,083 + 轨迹转换 9,413）"
    ])

    # ---- Slide 3: 架构 ----
    add_bullet_slide("Agent 架构", [
        "L0 执行层：RuleEngine（tap-guide / tap-only），每步 ~0ms",
        "消息总线 AgentBus：OBSERVE / PERCEIVE / DECIDE / VERIFY / CRITIC / MEMORY",
        "6 角色流水线：Observer→StateMapper→DecisionAnalyst→Verifier→Critic→MemoryCurator",
        "StrategyMemory：文件型策略记忆，按 (game_id, phase_id) 索引成功/失败记录",
        "分层规划器 HierarchicalPlanner：L2 云端 API + L1 本地 VLM + L0 规则",
        "10 种注册模式：rule / multi / multi-bus / multi-bus-memory / hierarchical / vlm-local 等"
    ])

    # ---- Slide 4: 记忆读回 A/B ----
    add_table_slide("记忆读回 A/B 验证", 
        ["阶段", "composite", "memory_hits", "wm_violations", "主要决策来源"],
        [
            ["phase1_write", "0.150", "92", "3", "strategy_memory:23, rule_engine:7"],
            ["phase2_read", "0.300", "116", "0", "strategy_memory:29, rule_engine:1"],
            ["control_no_memory", "0.150", "0", "3", "rule_engine:30"],
        ])

    # ---- Slide 5: 全游戏矩阵 ----
    add_table_slide("全游戏 × 多模式批量矩阵（108 runs）",
        ["游戏", "rule", "multi-bus-memory", "multi-bus", "hierarchical"],
        [
            ["00440 清障通车", "0.184", "0.156", "0.156", "0.150"],
            ["00461 塔防", "0.113", "0.300", "0.297", "0.150"],
            ["00483 吸沙抽水", "0.139", "0.300", "0.300", "0.150"],
            ["00496 电网抓丧尸", "0.275", "0.150", "0.150", "0.150"],
            ["00522 地下炸矿", "0.215", "0.240", "0.300", "—"],
        ])

    # ---- Slide 6: 游戏类型分类 ----
    add_bullet_slide("游戏类型自动分类（22 游戏）", [
        "A 类 joystick 驱动（7 个）：00440/00461/00483/00496/00517/00522/00736",
        "  → 校准成功，multi-bus/multi-bus-memory 稳定 0.300",
        "B 类 tap-to-move（15 个）：00219/00332/00342/.../00742",
        "  → joystick + Cocos Actor.move 均 0 位移，需 tap-only 驱动",
        "8/15 B 类达 0.300（00382/00394/00475/00526/00532/00594/00669/00742）",
        "  → tap-only：直接 tap guide 目标屏幕坐标，无需 joystick 校准",
        "7/15 B 类 0.150：tap 有效但无移动，activity=0",
        "自动校准脚本 auto_calibrate.py：4 方向脉冲 + warmup + retry + moveByCocosInput 回退"
    ])

    # ---- Slide 7: 云端 API vs 本地 VLM ----
    add_table_slide("云端 API vs 本地 VLM",
        ["模型", "模态", "struct 解析", "延迟", "结论"],
        [
            ["kimi-k2.7-code", "文本", "—", "2.2s", "最快文本，但无法推断 tap 坐标"],
            ["mimo-v2.5", "视觉", "3/3", "22-38s", "视觉准确但太慢，适合离线"],
            ["kimi-k2.6", "视觉", "0/3", "8-10s", "思考链截断，需更强输出契约"],
            ["gemma-4-E4B", "本地视觉", "3/3", "4.8s/帧", "本地最佳，离线可用"],
            ["Qwen3.5-9B", "本地视觉", "2/3", "16.9s/帧", "边缘可用"],
        ])

    # ---- Slide 8: 关键修复 ----
    add_bullet_slide("关键修复（影响结论可信度）", [
        "probe 终止假阳性：cc.Button._transitionFinished（含 'finish'）被 WIN 正则误命中",
        "  → _is_finished 改为佐证制：win 面板节点 / 胜利 analytics / 非 cc.* 强胜利标志",
        "  → 修复后 00482/00342 从 1 步假阳性（0.700）→ 25 步真实运行（0.150）",
        "rubric 动作盲区：tap 全被当 stall → 修复后 tap 算有效交互",
        "  → multi-bus 的 24 tap/30 步才得到 activity=1.0 的合理评价",
        "generic fallback：22 个无 profile 游戏不再抛错，用未校准基线驱动+采集数据",
        "write_profile 坐标系：设计分辨率（720x1560 左下）→ CSS 视口（375x812 顶左）"
    ])

    # ---- Slide 9: L2 契约修复 ----
    add_bullet_slide("L2 输出契约修复（hierarchical）", [
        "问题：云端 API 的 macro-plan 是抽象文本，L0 无法执行 → hierarchical 全部 activity=0",
        "修复：L2 输出可执行指令队列（tap/move 带坐标 + 设计→CSS 坐标转换）",
        "验证：L2 队列架构正确（24/25 步来自 L2），但纯文本 API 无视觉 → 坐标全是幻觉",
        "  → composite 反而更差（0.055 vs rule 0.114）",
        "修正方向：L2 改为输出目标名称（'UnlockItem_1'），L0 用 probe screenPosition 自行映射坐标",
        "  → L2 不需要视觉也能工作，L0 保留几何准确性"
    ])

    # ---- Slide 10: 训练数据 ----
    add_table_slide("训练数据（24,496 条 7 任务样本）",
        ["任务", "processed-runs", "轨迹转换", "总计"],
        [
            ["next_probe_action", "2,645", "—", "2,645"],
            ["probe_action_effect", "2,645", "+3,040", "5,685"],
            ["field_grounding", "2,645", "—", "2,645"],
            ["information_gain_judgment", "3,054", "+3,040", "6,094"],
            ["pulse_response_grounding", "1,435", "+159", "1,594"],
            ["progression_grounding", "2,645", "+3,165", "5,810"],
            ["failure_recovery", "14", "+9", "23"],
            ["总计", "15,083", "+9,413", "24,496"],
        ])

    # ---- Slide 11: 后续工作 ----
    add_bullet_slide("后续工作", [
        "L2 契约 v2：云端 API 输出目标名称，L0 用 probe 映射坐标（已验证方向正确）",
        "7 个 tap-only 0.150 游戏：用 VLM 视觉定位可交互元素，补充 probe 坐标精度",
        "00496 类确定性游戏：记忆读回帮倒忙，加「rule 连续成功时跳过记忆」开关",
        "ssh5090 QLoRA：24,496 条训练数据已就绪，等可访问后开训",
        "批量实验自动化：CI/CD 中跑 exp_full_matrix.py，每次代码变更自动对比 composite"
    ])

    PPTX_PATH.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(PPTX_PATH))
    print(f"PPTX -> {PPTX_PATH}")


def main() -> None:
    md = REPORT_MD.read_text(encoding="utf-8")
    body = md_to_latex(md)
    write_tex(body)
    compile_tex()
    write_pptx()


if __name__ == "__main__":
    main()
