from __future__ import annotations

"""Shared researcher-facing wording for standalone Skill terminal views."""

import re
from typing import Iterable


def humanize_skill_copy(value: object) -> str:
    """Translate implementation-heavy Skill metadata into researcher-facing copy."""

    text = " ".join(str(value or "").split())
    substitutions = (
        (r"(?i)\bfrom (?:the )?relevant sections?\b", "从论文中的相关内容"),
        (r"(?i)\brelevant sections?\b", "论文中的相关内容"),
        (r"(?i)\bpaper sections?\b", "论文内容"),
        (r"相关\s*section", "论文中的相关内容"),
        (r"论文\s*section", "论文中的相应部分"),
        (r"笔记\s*section", "阅读笔记中的相关内容"),
        (r"精确\s*section", "论文中的具体位置"),
        (r"\bsection\s*，", "论文中的相关内容，"),
        (r"\bsection\s*、", "论文中的相关内容、"),
        (r"带锚点的证据", "并标注论文位置的证据"),
        (r"来源锚定卡片", "带来源标注的阅读笔记"),
        (r"阅读卡片", "阅读笔记"),
        (r"阅读卡", "阅读笔记"),
        (r"笔记\s+来源记录", "阅读笔记、来源记录"),
        (r"证据锚点", "可回查的证据位置"),
        (r"来源锚点", "可回查的来源位置"),
        (r"(?i)\bsection-aware\b", "按论文位置整理的"),
        (r"(?i)\bsection[- ]level\b", "论文位置相关的"),
        (r"(?i)\bsection anchor\b", "论文位置"),
        (r"(?i)\bsection coverage\b", "阅读覆盖范围"),
        (r"(?i)\bsection\s*锚点", "论文位置"),
        (r"(?i)\bsection\s*覆盖", "阅读覆盖范围"),
        (r"(?i)\bsection\s*级", "论文位置相关"),
        (r"(?i)\bevidence cards?\b", "论文阅读笔记"),
        (r"(?i)\bpdf note cards?\b", "PDF 阅读笔记"),
        (r"(?i)\bnote cards?\b", "论文阅读笔记"),
        (r"PDF\s*笔记卡", "PDF 阅读笔记"),
        (r"文献笔记卡", "论文阅读笔记"),
        (r"笔记卡", "阅读笔记"),
        (r"文献卡", "论文阅读笔记"),
        (r"(?i)\binput contract\b", "材料要求"),
        (r"(?i)\bintake\b", "材料准备"),
        (r"(?i)\bartifact\b", "输出文件"),
        (r"(?i)\bdeterministic\b", "自动"),
        (r"(?i)\bprovenance\b", "来源记录"),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"笔记\s+来源记录", "阅读笔记、来源记录", text)
    return " ".join(text.split())


def brief_skill_copy(value: object) -> str:
    """Keep a complete first sentence for dense table cells without clipping text."""

    text = humanize_skill_copy(value)
    match = re.search(r"[。！？]|\.\s", text)
    if match:
        return text[: match.end()].strip()
    return text


def summarize_tool_capabilities(tools: Iterable[str]) -> str:
    """Describe enabled capabilities instead of exposing a wall of Tool names."""

    names = {str(name) for name in tools if str(name).strip()}
    categories: list[str] = []
    if any(any(token in name for token in ("search", "fetch", "lookup", "get_work", "get_paper")) for name in names):
        categories.append("文献检索与获取")
    if any(any(token in name for token in ("read", "extract", "grep", "glob", "list_files")) for name in names):
        categories.append("文件阅读与定位")
    if any(any(token in name for token in ("write", "save", "process")) for name in names):
        categories.append("材料与笔记保存")
    if "ask_human" in names:
        categories.append("补充提问")
    if not categories:
        categories.append("研究材料处理")
    return "、".join(categories[:4]) + f"（共 {len(names)} 项功能）"
