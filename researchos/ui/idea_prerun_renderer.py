"""Rich pre-run readiness view for T4, without internal JSON leakage."""

from __future__ import annotations

from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..ideation.models import T4RunConfig
from ..ideation.prerun import T4InputInspection


def render_t4_prerun(
    inspection: T4InputInspection,
    config: T4RunConfig,
    *,
    console: Console | None = None,
    file: TextIO | None = None,
) -> None:
    """Render the first-use T4 confirmation screen using workspace facts."""

    output = console or Console(file=file, highlight=False)
    status_label = {
        "ready": "可以开始",
        "ready_with_warnings": "可以开始，但有注意事项",
        "blocked": "暂时无法开始",
    }[inspection.status]
    output.print(
        Panel(
            Text(
                "T4 会根据论文阅读笔记形成一组可比较的研究方向，再独立评分并演化出更成熟的候选。"
                "本阶段不会宣称外部 novelty，也不会改写已有论文材料。",
                overflow="fold",
            ),
            title=f"T4 · 研究方向形成与演化 · {status_label}",
            border_style="bright_cyan" if inspection.status != "blocked" else "bright_red",
            expand=True,
        )
    )
    materials = inspection.materials
    table = Table(title="本轮可用材料", expand=True, show_header=True, header_style="bold cyan")
    table.add_column("材料", ratio=2)
    table.add_column("状态", ratio=1)
    for label, key in (
        ("主线全文阅读笔记", "core_deep_cards"),
        ("主线摘要阅读笔记", "core_abstract_cards"),
        ("Bridge 独立全文阅读笔记", "bridge_deep_cards"),
        ("Bridge 独立摘要阅读笔记", "bridge_abstract_cards"),
        ("已确认的 Cross-domain 候选方向", "cross_domain_configured_tracks"),
        ("Cross-domain 候选方向轨道", "cross_domain_tracks"),
        ("Cross-domain 检索记录（灵感/比较线索）", "cross_domain_retrieved_records"),
        ("其中摘要级记录", "cross_domain_abstract_records"),
        ("已关联的可核验阅读笔记", "cross_domain_linked_reading_notes"),
        ("综合分析材料", "synthesis_workbench"),
        ("研究领域图谱", "domain_map"),
        ("用户提供的初始想法", "user_seed_ideas"),
        ("研究约束", "user_constraints"),
    ):
        table.add_row(label, str(materials.get(key, "未提供")))
    output.print(table)

    if inspection.status == "blocked":
        for issue in inspection.blocking_issues:
            output.print(
                Panel(
                    Text(
                        f"缺少：{issue['artifact']}\n原因：{issue['why']}\n下一步：{issue['how_to_fix']}",
                        overflow="fold",
                    ),
                    title="T4 需要先补充材料",
                    border_style="bright_red",
                    expand=True,
                )
            )
        output.print(Text("尚未调用模型；补齐材料后可安全 resume。", style="dim"))
        return

    profile = config.target_profile
    profile_label = {
        "utd_is": "UTD / INFORMS · Theory and phenomenon",
        "ccf_cs": "CCF / CS · Technical and computational",
        "management_is": "UTD / Management & IS",
        "technical_cs": "CCF A / Technical",
        "hybrid": "Hybrid / Cross-disciplinary",
        "custom": "自定义",
    }[profile.profile_type]
    profile_source = "、".join(profile.inferred_from) if profile.inferred_from else "系统默认"
    round_description = {
        "quick": "只形成并评分初始候选集 P0，不生成 Child。",
        "standard": "完成两轮 P0 → P1 → P2：在第一轮发散后继续深化机制、反事实和验证设计，再进行比较。",
        "deep": "连续运行三轮 Evolution，保留每一代中间结果。",
        "auto": "由 Controller 依据候选质量与多样性判断是否值得继续下一轮。",
    }[config.mode]
    output.print(
        Panel(
            Text(
                f"本轮建议：{profile_label}（来源：{profile_source}）\n"
                f"本轮流程：P0 → P{config.rounds}（初始候选集形成后，经过独立评分、演化与存活选择；每一代都可回退）。\n"
                f"运行方式：{round_description}\n"
                f"预计时间：取决于模型响应和材料量；所有 Population、评分和谱系都会保存，可 resume 或 rollback。\n"
                "接下来请选一种运行方式。选择后才会调用模型，已有版本不会被删除。",
                overflow="fold",
            ),
            title="本轮会做什么",
            border_style="green",
            expand=True,
        )
    )
    for warning in inspection.warnings:
        output.print(Panel(Text(warning, overflow="fold"), title="注意", border_style="yellow", expand=True))
