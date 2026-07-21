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


def _add_section_slide(prs, title: str, subtitle: str = "") -> None:
    """A full-bleed section divider with a dark background."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), _W, _H)
    _set_fill(bg, _C_PRIMARY)

    tb = slide.shapes.add_textbox(Inches(0.6), Inches(2.7), Inches(12.1), Inches(1.2))
    _set_text_style(tb.text_frame.paragraphs[0], title, Pt(44), bold=True, color=_C_LIGHT)
    if subtitle:
        tb2 = slide.shapes.add_textbox(Inches(0.6), Inches(4.0), Inches(12.1), Inches(0.8))
        _set_text_style(tb2.text_frame.paragraphs[0], subtitle, Pt(22), color=_C_LIGHT)

    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(4.0 if not subtitle else 4.7), Inches(2), Inches(0.08))
    _set_fill(accent, _C_ACCENT)


def write_pptx() -> None:
    prs = Presentation()
    prs.slide_width = _W
    prs.slide_height = _H

    # ---- Slide 1: Title ----
    _add_title_slide(prs, "smallgameagent 实验进展", "LLM/VLM + 规则驱动的小游戏 Agent\n多 Provider、在线规则更新与批量数据管线")

    # ---- Slide 2: Agenda ----
    _add_bullet_slide(prs, "今天聊什么？", [
        "小游戏 Agent 到底难在哪？",
        "我们的解法：三层快慢分离架构",
        "云端多模型 API 与本地 VLM 的落地配置",
        "规则在线更新：让底层规则也能「进化」",
        "最新实验结果与训练数据管线",
        "下一步我们要攻什么？",
    ])

    # ---- Section: Problem ----
    _add_section_slide(prs, "01 我们在解决什么问题？")

    _add_bullet_slide(prs, "小游戏可玩广告，自动化起来并不 trivial", [
        "空间一致性：场景会随进度改变，云端模型容易「记错」障碍物位置",
        "时间一致性：后续策略会反向改写前面的行为语义，导致推进失败",
        "策略短视：有钱就去升级，而不是攒够再升级，效率很低",
        "运行效率：每个游戏后端数据不同，日志与探针不能一刀切",
        "API 延迟：纯云端决策太慢，无法满足实时逐步控制",
    ])

    # ---- Section: Architecture ----
    _add_section_slide(prs, "02 三层架构")

    _add_architecture_slide(prs)

    _add_bullet_slide(prs, "慢思考 + 快执行，各司其职", [
        "L0 规则引擎：每步 ~0 ms，负责 move / tap 的零延迟执行",
        "L1 本地 VLM：看截图判断当前状态，每 5 步或卡住时做战术修正",
        "L2 云端 API：看 probe state 做长程规划与规则更新，每 15 步或阶段切换触发",
        "核心思路：把贵且慢的调用摊到多步，单步延迟压到接近 0",
    ])

    # ---- Section: Cloud API ----
    _add_section_slide(prs, "03 云端多 Provider API")

    _add_bullet_slide(prs, "一个客户端，切换多家云模型", [
        "统一接入 OpenCodeGo / MiMo、Kimi、DeepSeek、Xiaomi、Qwen",
        ".env 集中管理 key 与 base_url，provider 用 CLOUD_PROVIDER 环境变量切换",
        "当前实测可用：Kimi / xiaomi 文本+多模态；Qwen 文本可用；OpenCodeGo / DeepSeek 余额不足",
        "修复 Kimi base_url 需加 /v1，Qwen 默认模型改为 qwen3.7-max",
        "Kimi 系列自动省略 temperature，避免代理返回 400",
    ])

    # ---- Section: Rule update ----
    _add_section_slide(prs, "04 规则在线更新")

    _add_bullet_slide(prs, "规则不是写死的，触发后才让上层改", [
        "触发器监控：composite 持续低迷 / stall 计数 / L0-L2 冲突 / 世界模型 stale",
        "L2 输出结构化 JSON：param、memory_entry、phase_contract、code_file",
        "默认只修改内存参数与 strategy memory，零风险、即时生效",
        "代码文件改写需 allowlist + 置信度 ≥ 0.9 + 自动备份，未通过则进入待审队列",
        "目标：把「拿到钱就升级」的短视行为，改成「攒够再升级」的长程最优策略",
    ])

    # ---- Section: Local VLM ----
    _add_section_slide(prs, "05 本地 VLM：把 8 GB 显存用到极限")

    _add_bullet_slide(prs, "小模型 + 重量化 = 可落地的视觉层", [
        "基线：4-bit NF4 量化加载 Qwen3.5-4B/9B、Gemma-4-E4B",
        "进一步减显存：KV-cache 量化（q4_0 / q8_0 / q4_k_m）",
        "当前本地测试：Gemma-4-E4B 输出最稳定，3/3 帧可解析",
        "Qwen 系列速度更快，但需要更强的「只输出 JSON」系统提示约束",
        "未来：离线标注 → QLoRA 微调 → 给云端 API 提供视觉上下文",
    ])

    # ---- Section: Results ----
    _add_section_slide(prs, "06 实验结果")

    _add_table_slide(prs, "B 类 15 款 tap-only 游戏：8 款稳定满分，7 款待诊断",
        ["游戏", "rule", "multi-bus-memory", "状态"],
        [
            ["00219 养牛卖奶", "0.150", "0.150", "待诊断"],
            ["00332 圣诞薅羊毛", "0.150", "0.150", "待诊断"],
            ["00342 建造合并", "0.150", "0.150", "待诊断"],
            ["00382 低坑杀鲨鱼", "0.300", "0.300", "✅ 满分"],
            ["00394 车 zip", "0.300", "0.300", "✅ 满分"],
            ["00427 淘金", "0.150", "0.150", "待诊断"],
            ["00434 选项卡捏", "0.150", "0.150", "待诊断"],
            ["00475 太空圈地", "0.300", "0.300", "✅ 满分"],
            ["00482 砍树扩地", "0.150", "0.150", "待诊断"],
            ["00526 通水洗地", "0.300", "0.300", "✅ 满分"],
            ["00532 瀑布巨木", "0.300", "0.300", "✅ 满分"],
            ["00594 破石收水", "0.300", "0.300", "✅ 满分"],
            ["00669 斜挖订单", "0.300", "0.300", "✅ 满分"],
            ["00733 海洋回收", "0.150", "0.150", "待诊断"],
            ["00742 加油小镇", "0.300", "0.300", "✅ 满分"],
        ])

    _add_bullet_slide(prs, "关键发现", [
        "A 类 joystick：00461 / 00483 / 00522 在 multi-bus* 下稳定 0.300，记忆读回是决定性收益",
        "A 类短板：00496 / 00517 / 00736 的 multi-bus activity=0，driver 需要按游戏类型再调优",
        "B 类 tap-only：8/15 游戏 rule 即可 0.300，说明点击目标屏幕坐标的策略本身可行",
        "B 类待诊断：7/15 游戏 0.150（activity=0），可能是目标被遮挡、需要拖拽，或坐标映射偏差",
        "22 游戏全部可驱动、可采集轨迹，为后续 QLoRA 提供了 27,693 条合并样本",
    ])

    # ---- Section: Data ----
    _add_section_slide(prs, "07 训练数据管线")

    _add_bullet_slide(prs, "跑过的轨迹 = 可复用的训练数据", [
        "batch_runner 每步记录 state / action / keyNumbers / reason",
        "trajectory_converter 离线生成 7 任务样本：next_probe_action、information_gain_judgment 等",
        "已累积 27,693 条合并样本（去重后），覆盖 22 个游戏、rule / multi-bus-memory / multi-bus",
        "可直接喂给 Qwen3.5-4B/9B 与 Gemma-4-E4B 的 QLoRA 微调脚本",
    ])

    # ---- Section: Next ----
    _add_section_slide(prs, "08 下一步")

    _add_bullet_slide(prs, "接下来要攻的几件事", [
        "跑完 B 组 15 游戏 × 2 模式 × 2 seeds，拿到完整 22 游戏矩阵",
        "定位并修复 00483 multi-bus / multi-bus-memory activity=0 的问题",
        "让本地 VLM 常驻，验证 hierarchical 三层协同的真实收益",
        "在 5090 服务器上跑 QLoRA 微调，把 VLM 变成专用的画面理解器",
    ])

    # ---- Thanks ----
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), _W, _H)
    _set_fill(bg, _C_PRIMARY)
    tb = slide.shapes.add_textbox(Inches(0.5), Inches(2.9), Inches(12.3), Inches(1.4))
    _set_text_style(tb.text_frame.paragraphs[0], "谢谢，欢迎讨论", Pt(48), bold=True, color=_C_LIGHT, align=PP_ALIGN.CENTER)
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(5.67), Inches(4.3), Inches(2), Inches(0.08))
    _set_fill(accent, _C_ACCENT)

    # Add footers with page numbers
    total = len(prs.slides)
    for idx, slide in enumerate(prs.slides, start=1):
        if idx == 1:
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
