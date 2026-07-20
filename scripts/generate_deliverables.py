#!/usr/bin/env python3
"""Generate a LaTeX PDF report and a PowerPoint deck from REPORT.md."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
REPORT_MD = ROOT / "REPORT.md"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

TEX_PATH = REPORTS_DIR / "smallgameagent_report.tex"
PDF_PATH = REPORTS_DIR / "smallgameagent_report.pdf"
PPTX_PATH = REPORTS_DIR / "smallgameagent_report.pptx"


# ---------------------------------------------------------------------------
# PowerPoint theme constants
# ---------------------------------------------------------------------------
_C_PRIMARY = RGBColor(0x1A, 0x23, 0x7E)       # deep indigo
_C_SECONDARY = RGBColor(0x00, 0x96, 0xC7)     # teal
_C_ACCENT = RGBColor(0xFF, 0x6B, 0x35)        # orange
_C_LIGHT = RGBColor(0xF3, 0xF6, 0xF9)         # off-white
_C_DARK = RGBColor(0x1E, 0x29, 0x33)          # near-black
_C_MUTED = RGBColor(0x6C, 0x75, 0x7D)         # gray
_C_SUCCESS = RGBColor(0x28, 0xA7, 0x45)       # green
_C_CARD_BG = RGBColor(0xFF, 0xFF, 0xFF)       # white

_W = Inches(13.333)
_H = Inches(7.5)


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


def _set_fill(shape, color: RGBColor) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.width = Pt(0)


def _set_text_style(
    paragraph,
    text: str,
    font_size: Pt,
    bold: bool = False,
    color: RGBColor = _C_DARK,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    font_name: str = "Microsoft YaHei",
) -> None:
    paragraph.text = text
    paragraph.font.size = font_size
    paragraph.font.bold = bold
    paragraph.font.color.rgb = color
    paragraph.font.name = font_name
    paragraph.alignment = align


def _add_header_bar(slide, title: str) -> None:
    # Top accent bar
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), _W, Inches(1.05))
    _set_fill(bar, _C_PRIMARY)

    tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.25), Inches(12.3), Inches(0.6))
    _set_text_style(tb.text_frame.paragraphs[0], title, Pt(30), bold=True, color=_C_LIGHT)


def _add_footer(slide, page: int, total: int) -> None:
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(7.15), _W, Inches(0.02))
    _set_fill(line, _C_SECONDARY)

    tb = slide.shapes.add_textbox(Inches(0.5), Inches(7.2), Inches(4), Inches(0.25))
    _set_text_style(tb.text_frame.paragraphs[0], "smallgameagent · LLM/VLM + 规则驱动", Pt(10), color=_C_MUTED)
    tb2 = slide.shapes.add_textbox(Inches(11.8), Inches(7.2), Inches(1), Inches(0.25))
    _set_text_style(tb2.text_frame.paragraphs[0], f"{page}/{total}", Pt(10), color=_C_MUTED, align=PP_ALIGN.RIGHT)


def _card(slide, left: float, top: float, width: float, height: float, title: str, bullets: list[str], accent: RGBColor = _C_SECONDARY) -> None:
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    _set_fill(shape, _C_CARD_BG)
    shape.line.color.rgb = accent
    shape.line.width = Pt(2)
    # Title strip
    strip = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(0.45))
    _set_fill(strip, accent)
    tb_title = slide.shapes.add_textbox(Inches(left + 0.08), Inches(top + 0.08), Inches(width - 0.16), Inches(0.35))
    _set_text_style(tb_title.text_frame.paragraphs[0], title, Pt(14), bold=True, color=_C_LIGHT)
    # Body
    tb = slide.shapes.add_textbox(Inches(left + 0.12), Inches(top + 0.55), Inches(width - 0.24), Inches(height - 0.65))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _set_text_style(p, b, Pt(12), color=_C_DARK)
        p.space_after = Pt(4)


def _add_table_slide(prs, title: str, headers: list[str], rows: list[list[str]]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_header_bar(slide, title)
    n_rows = len(rows) + 1
    n_cols = len(headers)
    left = Inches(0.5)
    top = Inches(1.25)
    width = Inches(12.3)
    height = Inches(5.6)
    table = slide.shapes.add_table(n_rows, n_cols, left, top, width, height).table
    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = _C_PRIMARY
        for paragraph in cell.text_frame.paragraphs:
            paragraph.font.size = Pt(13)
            paragraph.font.bold = True
            paragraph.font.color.rgb = _C_LIGHT
            paragraph.font.name = "Microsoft YaHei"
            paragraph.alignment = PP_ALIGN.CENTER
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = table.cell(i + 1, j)
            cell.text = str(val)
            if i % 2 == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = _C_LIGHT
            for paragraph in cell.text_frame.paragraphs:
                paragraph.font.size = Pt(12)
                paragraph.font.name = "Microsoft YaHei"
                paragraph.alignment = PP_ALIGN.CENTER
    return slide


def _add_bullet_slide(prs, title: str, bullets: list[str]) -> tuple:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_header_bar(slide, title)
    content = slide.shapes.add_textbox(Inches(0.6), Inches(1.35), Inches(12.1), Inches(5.5))
    tf = content.text_frame
    tf.word_wrap = True
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _set_text_style(p, b, Pt(18), color=_C_DARK)
        p.space_after = Pt(10)
    return slide


def _add_title_slide(prs, title: str, subtitle: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # Background gradient-ish using two rectangles
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), _W, _H)
    _set_fill(bg, _C_PRIMARY)
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(5.6), _W, Inches(1.9))
    _set_fill(accent, _C_SECONDARY)

    tb = slide.shapes.add_textbox(Inches(0.5), Inches(2.2), Inches(12.3), Inches(1.4))
    _set_text_style(tb.text_frame.paragraphs[0], title, Pt(48), bold=True, color=_C_LIGHT, align=PP_ALIGN.CENTER)
    tb2 = slide.shapes.add_textbox(Inches(0.5), Inches(3.8), Inches(12.3), Inches(1))
    _set_text_style(tb2.text_frame.paragraphs[0], subtitle, Pt(24), color=_C_LIGHT, align=PP_ALIGN.CENTER)
    tb3 = slide.shapes.add_textbox(Inches(0.5), Inches(6.15), Inches(12.3), Inches(0.6))
    _set_text_style(tb3.text_frame.paragraphs[0], "smallgameagent 项目组 · 2026-07", Pt(16), bold=True, color=_C_LIGHT, align=PP_ALIGN.CENTER)


def _add_architecture_slide(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_header_bar(slide, "三层架构：规则打底，云端/本地 VLM 做上层更新")

    # Draw three layered boxes
    layers = [
        ("L2 战略层 · 云端多模态 API", "kimi-k2.7-code / mimo-v2.5 / DeepSeek / Qwen\n长程规划 + 规则更新触发", _C_PRIMARY, 1.4),
        ("L1 战术层 · 本地小 VLM", "qwen3.5-4b / gemma4-e4b（4-bit + KV-cache）\n离线标注 + 视觉上下文", _C_SECONDARY, 3.1),
        ("L0 执行层 · 规则引擎", "tap-guide / tap-only / 障碍学习\n零延迟执行", _C_ACCENT, 4.8),
    ]
    for title, body, color, top in layers:
        rect = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(3.5), Inches(top), Inches(6.3), Inches(1.35))
        _set_fill(rect, color)
        tb = slide.shapes.add_textbox(Inches(3.7), Inches(top + 0.12), Inches(5.9), Inches(0.45))
        _set_text_style(tb.text_frame.paragraphs[0], title, Pt(16), bold=True, color=_C_LIGHT, align=PP_ALIGN.CENTER)
        tb2 = slide.shapes.add_textbox(Inches(3.7), Inches(top + 0.55), Inches(5.9), Inches(0.7))
        _set_text_style(tb2.text_frame.paragraphs[0], body, Pt(12), color=_C_LIGHT, align=PP_ALIGN.CENTER)

    # Side cards
    _card(slide, 0.4, 1.5, 2.7, 2.2, "触发条件", [
        "composite 连续低于阈值",
        "stall 超过 5 步",
        "L0/L2 决策冲突",
        "世界模型 stale 命中",
    ], _C_MUTED)
    _card(slide, 10.2, 1.5, 2.7, 2.2, "更新产物", [
        "参数更新",
        "phase contract",
        "strategy memory",
        "（可选）代码片段",
    ], _C_SUCCESS)

    # Bus / memory at bottom
    bus = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(2.5), Inches(6.35), Inches(8.3), Inches(0.55))
    _set_fill(bus, _C_DARK)
    tb = slide.shapes.add_textbox(Inches(2.7), Inches(6.42), Inches(7.9), Inches(0.45))
    _set_text_style(tb.text_frame.paragraphs[0], "AgentBus · Observer → StateMapper → DecisionAnalyst → Verifier → Critic → MemoryCurator", Pt(12), color=_C_LIGHT, align=PP_ALIGN.CENTER)


def write_pptx() -> None:
    prs = Presentation()
    prs.slide_width = _W
    prs.slide_height = _H

    slides_data: list[tuple[str, Any]] = []

    def register(slide, key: str) -> None:
        slides_data.append((key, slide))

    # ---- Slide 1: Title ----
    _add_title_slide(prs, "smallgameagent 实验进展", "LLM/VLM + 规则驱动的小游戏 Agent\n多 Provider、在线规则更新与批量数据管线")
    register(prs.slides[-1], "title")

    # ---- Slide 2: 一页结论 ----
    s = _add_bullet_slide(prs, "核心结论", [
        "最佳配置仍是 multi-bus + StrategyMemory 读回，稳定达到 composite 0.300（多 seed 一致）。",
        "记忆读回把 composite 从 0.150 提升到 0.300，关键在于世界模型违规从 3 降到 0。",
        "22 个游戏已自动分类：7 个 joystick 驱动 + 15 个 tap-to-move；8/15 的 B 类游戏用 tap-only 驱动即可达 0.300。",
        "云端 API 适合做长程规划与规则更新，但必须在 L2 输出目标名称而非坐标；本地 VLM 更适合离线视觉标注。",
        "多 Provider API 已统一接入：OpenCodeGo、Kimi、DeepSeek、MiMo（xiaomi）、Qwen 均可通过环境变量切换。",
        "训练数据已达 23,596 条 7 任务样本，为后续 QLoRA 微调做好了准备。",
    ])
    register(s, "summary")

    # ---- Slide 3: 架构图 ----
    _add_architecture_slide(prs)
    register(prs.slides[-1], "architecture")

    # ---- Slide 4: 全游戏矩阵 ----
    s = _add_table_slide(prs, "全游戏 × 多模式批量矩阵（节选）", 
        ["游戏", "类型", "rule", "multi-bus-memory", "multi-bus", "hierarchical"],
        [
            ["00461 塔防", "joystick", "0.113", "0.300", "0.297", "0.150"],
            ["00483 吸沙抽水", "joystick", "0.139", "0.300", "0.300", "0.150"],
            ["00496 电网抓丧尸", "joystick", "0.275", "0.150", "0.150", "0.150"],
            ["00522 地下炸矿", "joystick", "0.215", "0.240", "0.300", "—"],
            ["00382 低坑杀鲨鱼", "tap-only", "0.300", "0.300", "—", "—"],
            ["00594 破石收水", "tap-only", "0.300", "0.300", "—", "—"],
            ["00742 加油小镇", "tap-only", "0.300", "0.300", "—", "—"],
        ])
    register(s, "matrix")

    # ---- Slide 5: 记忆读回 A/B ----
    s = _add_table_slide(prs, "记忆读回 A/B 验证", 
        ["阶段", "composite", "activity", "memory_hits", "wm_violations", "主要决策来源"],
        [
            ["phase1_write", "0.150", "1.000", "92", "3", "strategy_memory:23, rule_engine:7"],
            ["phase2_read", "0.300", "1.000", "116", "0", "strategy_memory:29, rule_engine:1"],
            ["control_no_memory", "0.150", "1.000", "0", "3", "rule_engine:30"],
        ])
    register(s, "memory_ab")

    # ---- Slide 6: 云端 API vs 本地 VLM ----
    s = _add_table_slide(prs, "云端 API vs 本地 VLM 基准", 
        ["模型", "模态", "struct 解析", "延迟", "结论"],
        [
            ["kimi-k2.7-code", "文本", "—", "~2.2 s", "最快文本模型，适合长程规划"],
            ["mimo-v2.5", "视觉", "3/3", "~18 s/帧", "视觉准确但慢，适合离线标注"],
            ["kimi-k2.6", "视觉", "0/3", "~4 s/帧", "思考链截断，需更强输出契约"],
            ["gemma-4-E4B", "本地视觉", "3/3", "~5 s/帧", "本地最佳，8GB 显存可跑"],
            ["Qwen3.5-4B", "本地视觉", "0/3", "~12 s/帧", "量化后可用，准确率待提升"],
        ])
    register(s, "cloud_vs_local")

    # ---- Slide 7: 规则在线更新设计 ----
    s = _add_bullet_slide(prs, "规则在线更新：触发 → 决策 → 应用", [
        "触发：composite 连续低迷、stall 超过阈值、世界模型 stale、L0/L2 决策冲突。",
        "L1 本地 VLM：看截图生成结构化视觉上下文，告诉云端 API 当前画面里有哪些可交互元素。",
        "L2 云端 API：输出结构化规则更新（参数 / phase contract / memory entry / 可选代码片段）。",
        "应用：保守方案只改内存参数和 strategy memory；代码级更新需 diff + pytest + 短轨迹验证 + 自动回滚。",
        "效果预期：把「拿到钱就升级」的短视行为，改成「攒够钱批量升级」的长程最优策略。",
    ])
    register(s, "rule_update")

    # ---- Slide 8: 多 Provider API ----
    s = _add_bullet_slide(prs, "多 Provider 云端 API 配置", [
        "新增 MultiProviderClient，统一接入 OpenCodeGo / Kimi / DeepSeek / MiMo（xiaomi）/ Qwen。",
        "API key 统一存放在 .env，已加入 .gitignore，绝不会被提交。",
        "通过 CLOUD_PROVIDER 环境变量即可切换 provider；模型名随 provider 自动默认。",
        "kimi 系列自动省略 temperature 参数，避免 400 错误。",
        "保持 OpenCodeGoClient 兼容，现有调用无需修改。",
    ])
    register(s, "multi_provider")

    # ---- Slide 9: 关键修复 ----
    s = _add_bullet_slide(prs, "关键修复：让评测结果更可信", [
        "probe 终止假阳性：cc.Button._transitionFinished 被 WIN 正则误命中 → 改为佐证制完成判定。",
        "失败面板处理：minify 后组件名变单字母，改按节点名匹配 retry/continue，避开 download 广告陷阱。",
        "rubric 动作盲区：tap 不再被计为 stall，multi-bus 的 activity=1.0 才真实可信。",
        "generic fallback：22 个无 profile 游戏不再抛错，用未校准基线驱动并采集数据。",
        "L2 输出契约：从抽象文本改为可执行指令队列，验证架构正确。",
    ])
    register(s, "fixes")

    # ---- Slide 10: 训练数据 ----
    s = _add_table_slide(prs, "训练数据规模（7 任务格式）", 
        ["任务", "processed-runs", "轨迹转换", "总计"],
        [
            ["next_probe_action", "2,645", "—", "2,645"],
            ["probe_action_effect", "2,645", "+3,040", "5,685"],
            ["field_grounding", "2,645", "—", "2,645"],
            ["information_gain_judgment", "3,054", "+3,040", "6,094"],
            ["pulse_response_grounding", "1,435", "+159", "1,594"],
            ["progression_grounding", "2,645", "+3,165", "5,810"],
            ["failure_recovery", "14", "+9", "23"],
            ["总计", "15,083", "+9,413", "23,596"],
        ])
    register(s, "training_data")

    # ---- Slide 11: 后续工作 ----
    s = _add_bullet_slide(prs, "下一步工作", [
        "跑通本地 4-bit VLM 离线标注管线：qwen3.5-4b / gemma4-e4b，记录延迟与准确率。",
        "在 00461 / 00736 等代表游戏上验证规则在线更新（方案 A）能否降低 stall、提升 composite。",
        "把 L2 输出从「坐标」改为「目标名称」，让 L0 用 probe screenPosition 自行映射。",
        "扩充 failure_recovery 训练样本，解决当前仅 23 条的短板。",
        "ssh5090 可访问后，用 23,596 条样本启动 QLoRA 训练。",
    ])
    register(s, "next_steps")

    # Add footers with page numbers
    total = len(prs.slides)
    for idx, slide in enumerate(prs.slides, start=1):
        if idx == 1:
            # Title slide has its own footer area
            continue
        _add_footer(slide, idx, total)

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
