#!/usr/bin/env python3
"""Generate a LaTeX PDF report and a PowerPoint deck from REPORT.md."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_MD = ROOT / "REPORT.md"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

TEX_PATH = REPORTS_DIR / "smallgameagent_report.tex"
PDF_PATH = REPORTS_DIR / "smallgameagent_report.pdf"
PPTX_PATH = REPORTS_DIR / "smallgameagent_report.pptx"


def esc(s: str) -> str:
    """Escape LaTeX special chars."""
    s = s.replace("\\", "\\textbackslash{}")
    s = s.replace("{", "\\{")
    s = s.replace("}", "\\}")
    s = s.replace("$", "\\$")
    s = s.replace("&", "\\&")
    s = s.replace("%", "\\%")
    s = s.replace("#", "\\#")
    s = s.replace("_", "\\_")
    s = s.replace("^", "\\^{}")
    s = s.replace("~", "\\textasciitilde{}")
    return s


def inline_fmt(s: str) -> str:
    # code
    s = re.sub(r"`([^`]+)`", lambda m: f"\\texttt{{{esc(m.group(1))}}}", s)
    # bold
    s = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"\\textbf{{{esc(m.group(1))}}}", s)
    # italic
    s = re.sub(r"\*([^*]+)\*", lambda m: f"\\textit{{{esc(m.group(1))}}}", s)
    # links [text](url)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f"\\href{{{esc(m.group(2))}}}{{{esc(m.group(1))}}}", s)
    # plain escape for remaining text
    return esc(s)


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


def md_to_latex(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    in_table = False
    table_rows: list[str] = []

    def close() -> None:
        nonlocal in_list, in_table
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
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

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

    add_title_slide("smallgameagent 实验报告", "LLM/VLM + 规则驱动的小游戏 Agent")

    add_bullet_slide("背景与四个核心问题", [
        "空间一致性：场景深入后交互方式/障碍物变化导致错位",
        "时间一致性：场景与策略随时间演化，后续策略破坏前期功能",
        "策略优化：Codex 按部就班、缺乏经济循环与机会成本抽象",
        "效率：需要动态探针、灵活回退、动态日志，降低时延与观测成本"
    ])

    add_bullet_slide("空间一致性方案与效果", [
        "根因：failCount 翻转 → LosePanel 激活 → 输入被面板阻断",
        "VersionedWorldModel：entity/scene/capability 三版本 + stale 检测",
        "探针新增 findPanelButtons：按节点名匹配、设计坐标→CSS 像素映射",
        "A/B（00461，300 步）：composite 0.425→0.473，activity 0.54→0.92，墙钟 −42%"
    ])

    add_bullet_slide("时间一致性方案", [
        "阶段契约机制 phase_contract.py",
        "三层时间戳：event / observed / settled",
        "settle 复查、受保护前缀 hash、失败分级 rollback/compensation/stop+replan",
        "29 单测覆盖 190 步真实温度偏差复刻"
    ])

    add_bullet_slide("策略优化方案", [
        "经济决策模拟器穷举真值，kimi-k2.7-code 三迭代",
        "关键：先保证输出契约（16K tokens、末行 JSON）再谈最优性",
        "决定性 batch 场景：朴素 0.711 → 引导 1.000",
        "结论：显式给出经济循环+往返/机会成本抽象，可充分发挥 Codex 探索能力"
    ])

    add_bullet_slide("效率优化方案", [
        "动态探针预算 probe_budget.py：L0/L1/L2 五级，五类触发器",
        "L2 截图窗口上限 20%，高优先可挤占，冷却防抖",
        "AdaptiveLogger：DEBUG 环形内存 + 触发窗口落盘",
        "50 步无变化场景节省观测成本 ~82%"
    ])

    add_bullet_slide("本地 VLM 实测画像", [
        "Intel 核显 Vulkan Q4_K_M 文本生成：",
        "  Qwen3.5-4B  2.75 tok/s",
        "  Qwen3.5-9B  0.72 tok/s",
        "  gemma-4-E4B 0.67 tok/s",
        "视觉编码必须 --no-mmproj-offload 回 CPU；4 帧 struct 解析率 0/4",
        "当前本地 VLM 仅适合作离线标注，不适合在线逐步控制"
    ])

    add_bullet_slide("训练准备与后续工作", [
        "processed-runs → 15,083 样本 / 7 任务，VLMColdStartDataset 已验证",
        "train_qwen35.py（QLoRA 4bit NF4 + ZeRO-2）就绪",
        "ssh5090 可访问后：bash scripts/scp_to_ssh5090.sh 并开训",
        "下一步：面板泛化、9B/E4B 调优、22 游戏 benchmark、verifiers 对抗数据"
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
