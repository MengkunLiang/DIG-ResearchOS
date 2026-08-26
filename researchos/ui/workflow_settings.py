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

from ..latex_templates import LatexTemplateEntry, ccf_template_entry


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


def _template_expectation(settings: dict[str, Any]) -> str:
    """Describe a durable template choice without inventing a venue."""

    orientation = str(settings.get("publication_orientation") or "").strip()
    if orientation == "ccf_cs":
        entry = ccf_template_entry(str(settings.get("template_id") or ""))
        if entry is not None:
            return f"{entry.label}（已确认，Survey 与 T8 复用）"
        return "T1 立即选择具体 CCF/CS 会议模板（不默认基础英文）"
    if orientation == "utd_is":
        return "INFORMS ISRE 2024（UTD/IS 默认）"
    return "在对应写作 Gate 选择"


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
            "LaTeX 模板",
            _template_expectation(settings),
            _template_expectation(previous_settings),
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


def workflow_execution_setup_guide_panel() -> Panel:
    """Explain the editable T1 defaults before asking for a free-form reply.

    The compact settings table intentionally prioritizes the selected values.
    This companion view restores the operational meaning and directly usable
    examples, so a researcher does not have to infer what ``80 / 40 / 40`` or
    ``top2`` changes from an internal preset name.
    """

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        show_lines=True,
        header_style="bold cyan",
        pad_edge=False,
        expand=True,
    )
    table.add_column("参数", style="bold cyan", width=13)
    table.add_column("实际含义", min_width=32, overflow="fold")
    table.add_column("可直接输入", min_width=25, overflow="fold")
    table.add_row(
        "文献覆盖",
        "三元组依次为：保留候选 / T3 精读 / 摘要级轻读。强覆盖会增加检索、阅读时间和模型成本。",
        "“标准研究覆盖”\n“综述均衡覆盖”\n“全面综述覆盖”",
    )
    table.add_row(
        "T4 探索",
        "控制 Candidate 形成与演化的探索力度；auto 根据证据质量与候选多样性决定，不改变研究范围。",
        "“快速探索”\n“标准探索”\n“深入探索”",
    )
    table.add_row(
        "Proposal",
        "一条：只正式化排序最高的 Candidate。两条：前两条分别形成独立 Proposal，之后再明确选择一条进入 T5。",
        "“一条 Proposal”\n“两个 Proposal”",
    )
    note = Text(
        "输入“1”或“确认”接受当前显示的设置。提出修改后，系统会先展示未保存预览，必须再次确认才会生效。",
        style="dim",
        overflow="fold",
    )
    return Panel(Group(table, note), title="参数含义与输入示例", border_style="bright_cyan", expand=True)


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
    table.add_column("选择", justify="right", width=8, style="bold yellow")
    table.add_column("运行方式", min_width=27, overflow="fold")
    table.add_column("后续行为", overflow="fold")
    table.add_row("1", "Copilot · 协作", "每个研究关键 Gate 都由你确认；不默认选择 CCF/CS 或 UTD/IS。")
    table.add_row("2", "Auto · 研究 · CCF/CS", "默认研究论文覆盖与 CCF/CS 取向；常规 Gate 可自动通过。")
    table.add_row("3", "Auto · 研究 · UTD/IS", "默认研究论文覆盖与 UTD/IS 取向；常规 Gate 可自动通过。")
    table.add_row("4", "Auto · 综述 · CCF/CS", "默认综述均衡覆盖，并进入 Survey 支线。")
    table.add_row("5", "Auto · 综述 · UTD/IS", "默认综述均衡覆盖，并进入 Survey 支线。")
    table.add_row("6", "Auto · 强覆盖综述 · UTD/IS", "默认综述强覆盖，并进入 Survey 支线。")
    note = Text(
        "输入编号即可；也兼容 Auto survey_ccf 等命令和自然语言。选择后还会确认文献覆盖、T4 探索力度和 Proposal 数量。CCF/CS 会随即选择具体会议模板，供 Survey 与 T8 复用。",
        style="dim",
        overflow="fold",
    )
    return Panel(Group(table, note), title="选择项目运行方式", border_style="bright_cyan", expand=True)


def workflow_ccf_template_selector_panel(entries: Iterable[LatexTemplateEntry]) -> Panel:
    """Render the concrete CCF menu that follows the T1 default settings."""

    available = list(entries)
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        show_lines=True,
        header_style="bold cyan",
        pad_edge=False,
        expand=True,
    )
    table.add_column("选择", justify="right", width=8, style="bold yellow")
    table.add_column("会议模板", min_width=20, overflow="fold")
    table.add_column("本地支持", overflow="fold")
    for index, entry in enumerate(available, start=1):
        table.add_row(str(index), entry.label, entry.availability_label)
    if not available:
        table.add_row("-", "未发现可用 CCF 模板", "请检查 latex_templete/ccf-latex-templates")
    note = Text(
        "这里确定的是具体会议模板，不是研究结论。确认后会保存为未来 Survey 与 T8 的共同默认；可用 configure-workflow 显式改动，已有 TeX 不会被静默重写。",
        style="dim",
        overflow="fold",
    )
    return Panel(Group(table, note), title="选择 CCF/CS 会议 LaTeX 模板", border_style="bright_cyan", expand=True)
