"""Rich decision views for project workflow mode and default settings.

The workflow profile is operational configuration rather than a research
artifact.  These views deliberately show the few choices that change cost,
automation, and later decision surfaces without exposing its raw JSON receipt.
"""

from __future__ import annotations

from typing import Any, Iterable

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


_LITERATURE_LABELS = {
    "standard_research": "研究论文覆盖 · 40 / 25 / 15",
    "survey_balanced": "综述均衡覆盖 · 80 / 40 / 40",
    "survey_exhaustive": "综述强覆盖 · 90 / 40 / 50",
}

_T4_LABELS = {
    "quick": "快速探索",
    "standard": "标准探索",
    "deep": "深入探索",
    "auto": "按证据与多样性自动决定",
}

_ORIENTATION_LABELS = {
    "ccf_cs": "CCF/CS",
    "utd_is": "UTD/IS",
    "hybrid": "Hybrid",
    "pending_user": "T4 前由你确认",
}


def _settings(profile: dict[str, Any] | None) -> dict[str, Any]:
    value = profile.get("settings") if isinstance(profile, dict) else None
    return value if isinstance(value, dict) else {}


def _mode_label(profile: dict[str, Any] | None) -> str:
    return "Auto" if str((profile or {}).get("mode") or "").casefold() == "auto" else "Copilot"


def workflow_settings_panel(
    profile: dict[str, Any] | None,
    *,
    title: str = "项目默认执行设置",
    previous: dict[str, Any] | None = None,
    impacts: Iterable[str] = (),
    border_style: str = "bright_cyan",
) -> Panel:
    """Return a compact decision panel for one persisted workflow profile."""

    settings = _settings(profile)
    previous_settings = _settings(previous)
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        show_lines=True,
        header_style="bold cyan",
        pad_edge=False,
        expand=True,
    )
    table.add_column("设置", style="bold cyan", width=16)
    table.add_column("当前值", min_width=28, overflow="fold")
    if previous is not None:
        table.add_column("变更前", min_width=24, overflow="fold")

    rows = [
        ("工作方式", _mode_label(profile), _mode_label(previous)),
        (
            "文献覆盖",
            _LITERATURE_LABELS.get(
                str(settings.get("literature_preset") or ""),
                str(settings.get("literature_preset") or "未设置"),
            ),
            _LITERATURE_LABELS.get(
                str(previous_settings.get("literature_preset") or ""),
                str(previous_settings.get("literature_preset") or "未设置"),
            ),
        ),
        (
            "T4 探索",
            _T4_LABELS.get(str(settings.get("t4_mode") or ""), str(settings.get("t4_mode") or "未设置")),
            _T4_LABELS.get(
                str(previous_settings.get("t4_mode") or ""),
                str(previous_settings.get("t4_mode") or "未设置"),
            ),
        ),
        (
            "Proposal",
            "前两条分别正式化后再选择" if settings.get("proposal_tracks") == "top2" else "一条 Proposal",
            "前两条分别正式化后再选择" if previous_settings.get("proposal_tracks") == "top2" else "一条 Proposal",
        ),
        (
            "论文取向",
            _ORIENTATION_LABELS.get(
                str(settings.get("publication_orientation") or ""),
                str(settings.get("publication_orientation") or "未设置"),
            ),
            _ORIENTATION_LABELS.get(
                str(previous_settings.get("publication_orientation") or ""),
                str(previous_settings.get("publication_orientation") or "未设置"),
            ),
        ),
        (
            "综述支线",
            "默认写作并可定向补检"
            if settings.get("survey_policy") == "write_with_supplement"
            else "默认不进入综述支线"
            if settings.get("survey_policy") == "skip"
            else "后续单独询问",
            "默认写作并可定向补检"
            if previous_settings.get("survey_policy") == "write_with_supplement"
            else "默认不进入综述支线"
            if previous_settings.get("survey_policy") == "skip"
            else "后续单独询问",
        ),
    ]
    for label, current, before in rows:
        if previous is None:
            table.add_row(label, current)
        else:
            table.add_row(label, current, before)

    mode_note = (
        "Auto 仅自动通过已授权的常规 Gate；研究范围变化、失败恢复、新颖性裁决和外部执行仍需要你确认。"
        if _mode_label(profile) == "Auto"
        else "Copilot 会在每个研究关键 Gate 等待你的确认；这里保存的是后续 Gate 的默认建议，不会替你作研究判断。"
    )
    body: list[Any] = [table, Text(mode_note, style="dim", overflow="fold")]
    impact_lines = [str(item).strip() for item in impacts if str(item).strip()]
    if impact_lines:
        impact_table = Table(box=box.SIMPLE, show_header=False, pad_edge=False, expand=True)
        impact_table.add_column("影响", style="bold yellow", width=8)
        impact_table.add_column("说明", overflow="fold")
        for item in impact_lines:
            impact_table.add_row("注意", item)
        body.append(impact_table)
    return Panel(Group(*body), title=title, border_style=border_style, expand=True)


def workflow_mode_selector_panel() -> Panel:
    """Return the first-run mode selector without a long Markdown tutorial."""

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        show_lines=True,
        header_style="bold cyan",
        pad_edge=False,
        expand=True,
    )
    table.add_column("输入", justify="right", width=8, style="bold yellow")
    table.add_column("方式", min_width=22, overflow="fold")
    table.add_column("后续行为", overflow="fold")
    table.add_row("Copilot", "协作模式", "每个研究关键 Gate 都由你确认；不默认选择 CCF/CS 或 UTD/IS。")
    table.add_row("Auto research_ccf", "自动研究 · CCF/CS", "默认研究论文覆盖与 CCF/CS 取向；常规 Gate 可自动通过。")
    table.add_row("Auto research_utd", "自动研究 · UTD/IS", "默认研究论文覆盖与 UTD/IS 取向；常规 Gate 可自动通过。")
    table.add_row("Auto survey_ccf", "自动综述 · CCF/CS", "默认综述均衡覆盖，并进入 Survey 支线。")
    table.add_row("Auto survey_utd", "自动综述 · UTD/IS", "默认综述均衡覆盖，并进入 Survey 支线。")
    note = Text(
        "选择模式后还会确认文献覆盖、T4 探索力度和 Proposal 数量。模式不会替你确定研究问题、范围、失败恢复或外部执行。",
        style="dim",
        overflow="fold",
    )
    return Panel(Group(table, note), title="选择项目运行方式", border_style="bright_cyan", expand=True)
