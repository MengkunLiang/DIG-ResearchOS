"""Rich, researcher-facing status panels for T4 evolutionary ideation.

The renderer receives only deterministic lifecycle metrics. It never creates
or paraphrases scientific claims, and it deliberately avoids raw artifacts,
model prompts, and private reasoning in normal CLI output.
"""

from __future__ import annotations

from typing import Any, TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..ideation.models import EvolutionPhase


_PHASE_VIEW: dict[EvolutionPhase, tuple[int, str, str]] = {
    EvolutionPhase.EVIDENCE_ROUTING: (
        1,
        "Evidence Routing",
        "整理主线与 Bridge 的论文阅读笔记，并为每段材料标注允许支持的结论范围。",
    ),
    EvolutionPhase.OPPORTUNITY_MAP: (
        2,
        "Opportunity Map",
        "将文献中的 tension、assumption、mechanism gap、failure boundary 和 bridge 线索整理为可研究的问题。",
    ),
    EvolutionPhase.FORMATION: (
        3,
        "Multi-route Generation",
        "通过 Literature、Informed Brainstorm、补充 Route 和 Cross-domain/Bridge 形成初始 Population P0。",
    ),
    EvolutionPhase.GENOME_FAMILY: (
        4,
        "Idea Genome & Family",
        "记录每个 Candidate 的 problem、mechanism、contribution、hypothesis 与 evidence lineage，并识别可比较的 Idea Family。",
    ),
    EvolutionPhase.SCORING: (
        5,
        "Independent Scoring",
        "由独立 Scoring Agent 进行匿名评价；Generator 不参与自身候选的评分。",
    ),
    EvolutionPhase.EVOLUTION_PLANNING: (
        6,
        "Evolution Planning",
        "选择 Parent，并为 Mutation Child 和通过 Compatibility Check 的 Crossover Child 建立可审计计划。",
    ),
    EvolutionPhase.OFFSPRING: (
        7,
        "Offspring & Rescoring",
        "生成 Child 后，与 Parent 一起匿名重评；Parent 不会因为生成 Child 而消失。",
    ),
    EvolutionPhase.SURVIVAL: (
        8,
        "Survival & Portfolio",
        "执行 Idea Contract、Family-level Survival Selection，并从保留的 Active Candidates 中形成可供选择的 Portfolio。",
    ),
}


def render_t4_evolution_phase(
    phase: EvolutionPhase,
    status: str,
    payload: dict[str, Any],
    *,
    console: Console | None = None,
    file: TextIO | None = None,
) -> None:
    """Render one concise T4 phase panel from artifact-backed lifecycle data."""

    output = console or Console(file=file, highlight=False)
    position, title, purpose = _PHASE_VIEW.get(phase, (0, phase.value.replace("_", " ").title(), "正在更新 T4 运行状态。"))
    status_text = {
        "started": "正在进行",
        "completed": "已完成",
        "rescoring": "正在重评",
        "reused": "已恢复已完成结果",
    }.get(status, status.replace("_", " "))
    output.print(
        Panel(
            Text(purpose, overflow="fold"),
            title=f"T4 · Round {0 if position <= 4 else 1} · Phase {position}/8 · {title} · {status_text}",
            border_style="bright_cyan" if status != "completed" else "green",
            expand=True,
        )
    )
    metrics = _metric_rows(phase, payload)
    if metrics:
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(style="bold cyan", ratio=1)
        table.add_column(ratio=2, overflow="fold")
        for label, value in metrics:
            table.add_row(label, value)
        output.print(table)
    warning = _warning_text(phase, payload)
    if warning:
        output.print(Panel(Text(warning, overflow="fold"), title="Warning", border_style="yellow", expand=True))


def _metric_rows(phase: EvolutionPhase, payload: dict[str, Any]) -> list[tuple[str, str]]:
    if phase == EvolutionPhase.EVIDENCE_ROUTING:
        by_level = payload.get("counts_by_reading_level") if isinstance(payload.get("counts_by_reading_level"), dict) else {}
        by_domain = payload.get("counts_by_domain_role") if isinstance(payload.get("counts_by_domain_role"), dict) else {}
        return [
            ("Paper-note sections indexed", _number(payload.get("atom_count"))),
            ("Full/Partial reading", _number(sum(_safe_int(by_level.get(key)) for key in ("full_text", "partial_text")))),
            ("Abstract-only recall", _number(_safe_int(by_level.get("abstract_only")))),
            ("Bridge sources", _number(_safe_int(by_domain.get("bridge")))),
            ("Reading upgrades to consider", _number(len(payload.get("reading_upgrade_candidates") or []))),
        ]
    if phase == EvolutionPhase.OPPORTUNITY_MAP:
        types = payload.get("types") if isinstance(payload.get("types"), list) else []
        return [("Opportunity Queries", _number(payload.get("opportunity_count"))), ("Evidence sections available", _number(payload.get("evidence_atoms"))), ("Covered opportunity types", _short_list(types))]
    if phase == EvolutionPhase.FORMATION:
        routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
        if routes and isinstance(routes[0], dict):
            return [("Route results", _route_summary(routes)), ("Initial Population P0", _number(payload.get("candidate_count")))]
        return [("Active Routes", _short_list(routes)), ("Target Idea Seeds", _number(payload.get("target_seed_count")))]
    if phase == EvolutionPhase.GENOME_FAMILY:
        return [
            ("Population", str(payload.get("population_id") or "P0")),
            ("Candidates", _number(payload.get("candidate_count"))),
            ("Idea Families", _number(payload.get("family_count"))),
        ]
    if phase == EvolutionPhase.SCORING:
        return [("Population", str(payload.get("population_id") or "P0")), ("Candidates scored", _number(payload.get("candidate_count")))]
    if phase == EvolutionPhase.EVOLUTION_PLANNING:
        return [
            ("Parents retained", _number(payload.get("parent_count"))),
            ("Mutation Child plans", _number(payload.get("mutation_count"))),
            ("Crossover Child plans", _number(payload.get("crossover_count"))),
        ]
    if phase == EvolutionPhase.OFFSPRING:
        return [
            ("Planned offspring", _number(payload.get("planned_offspring"))),
            ("Generated offspring", _number(payload.get("offspring_count"))),
            ("Union for independent rescoring", _number(payload.get("union_count"))),
        ]
    if phase == EvolutionPhase.SURVIVAL:
        return [
            ("Population", str(payload.get("population_id") or "P1")),
            ("P0 candidates", _number(payload.get("p0_count"))),
            ("Offspring", _number(payload.get("offspring_count"))),
            ("P1 active candidates", _number(payload.get("active_count"))),
            ("Archived candidates", _number(payload.get("archived_count"))),
            ("Visible Portfolio", _number(payload.get("portfolio_count"))),
        ]
    return []


def _warning_text(phase: EvolutionPhase, payload: dict[str, Any]) -> str:
    if phase == EvolutionPhase.EVIDENCE_ROUTING and _safe_int(payload.get("atom_count")) == 0:
        return "No paper reading notes were found. T4 can inspect synthesis artifacts, but evidence-linked candidates will be limited."
    if phase == EvolutionPhase.FORMATION:
        routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
        unsupported = [item for item in routes if isinstance(item, dict) and item.get("status") == "unsupported"]
        if unsupported:
            return f"{len(unsupported)} Route did not have enough current evidence and was preserved as unsupported rather than fabricated."
    if phase == EvolutionPhase.EVOLUTION_PLANNING and _safe_int(payload.get("crossover_count")) == 0:
        return "No compatible Crossover Child was planned. The run continues with Mutation Child candidates."
    return ""


def _route_summary(routes: list[dict[str, Any]]) -> str:
    entries = []
    for route in routes:
        name = str(route.get("route") or "route").replace("_", " ")
        status = str(route.get("status") or "unknown")
        count = len(route.get("candidate_ids") or []) if isinstance(route.get("candidate_ids"), list) else 0
        entries.append(f"{name}: {count} ({status})")
    return "; ".join(entries)


def _short_list(values: list[Any], *, maximum: int = 5) -> str:
    items = [str(value).replace("_", " ") for value in values if str(value).strip()]
    if not items:
        return "-"
    suffix = f" +{len(items) - maximum}" if len(items) > maximum else ""
    return ", ".join(items[:maximum]) + suffix


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> str:
    return str(_safe_int(value)) if value is not None else "-"
