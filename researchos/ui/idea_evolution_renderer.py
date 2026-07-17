"""Researcher-facing progress rendering for T4 evolutionary ideation.

The renderer only reports lifecycle facts that have already been persisted by
the controller.  It never turns evidence into a claim or exposes raw model
payloads in the normal CLI.  Small internal transitions stay on one quiet
line; the few milestones a researcher may want to inspect use Rich panels.
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
        "整理论文阅读笔记，并标注每段材料可以支持到什么程度。",
    ),
    EvolutionPhase.OPPORTUNITY_MAP: (
        2,
        "研究机会探索（Opportunity Map）",
        "从已有材料、竞争解释与跨域线索中提出值得验证的问题，而不是生成最终候选。",
    ),
    EvolutionPhase.FORMATION: (
        3,
        "多视角 Idea 发散（Multi-route Generation）",
        "从文献、定向 Brainstorm、反例视角和 Cross-domain / Bridge 形成彼此不重复的初始候选池 P0。",
    ),
    EvolutionPhase.GENOME_FAMILY: (
        4,
        "Idea Genome 与 Idea Family",
        "记录每个候选的核心组成、来源和可比较的研究方向家族。",
    ),
    EvolutionPhase.SCORING: (
        5,
        "独立评分（Independent Scoring）",
        "由独立评分 Agent 比较候选；生成候选的 Agent 不会为自己的结果打分。",
    ),
    EvolutionPhase.EVOLUTION_PLANNING: (
        6,
        "Evolution Planning",
        "选择可继续发展的 Parent，并制定可审计的 Mutation Child 或 Crossover Child 计划。",
    ),
    EvolutionPhase.OFFSPRING: (
        7,
        "Child 生成与重新评分",
        "生成通过计划约束的 Child，并与 Parent 一起重新评分。",
    ),
    EvolutionPhase.SURVIVAL: (
        8,
        "Survival Selection 与 Portfolio",
        "保留多样且可靠的 Active Candidates，并形成供你选择的 Portfolio。",
    ),
}

_RICH_COMPLETION_PHASES = {
    EvolutionPhase.EVIDENCE_ROUTING,
    EvolutionPhase.FORMATION,
    EvolutionPhase.SCORING,
    EvolutionPhase.SURVIVAL,
}


def render_t4_evolution_phase(
    phase: EvolutionPhase,
    status: str,
    payload: dict[str, Any],
    *,
    console: Console | None = None,
    file: TextIO | None = None,
) -> None:
    """Render one compact T4 lifecycle update from artifact-backed metrics."""

    output = console or Console(file=file, highlight=False)
    position, title, purpose = _PHASE_VIEW.get(
        phase,
        (0, phase.value.replace("_", " ").title(), "正在更新 T4 的运行状态。"),
    )
    default_round = 0 if position <= 4 else 1
    round_number = _safe_int(payload.get("round_number")) if payload.get("round_number") is not None else default_round
    status_text = {
        "started": "开始",
        "completed": "已完成",
        "rescoring": "重新评分中",
        "reused": "已复用已完成结果",
    }.get(status, status.replace("_", " "))

    # A route is an internal unit of P0 formation, not a separate researcher
    # decision.  Keep these events as one aggregate line.  LLM heartbeats own
    # the live wait indicator, while this line records durable progress only.
    if phase == EvolutionPhase.FORMATION and status in {"route_started", "route_completed", "route_reused"}:
        if status == "route_started":
            return
        completed = _safe_int(payload.get("completed_routes"))
        total = _safe_int(payload.get("total_routes"))
        candidates = _safe_int(payload.get("candidate_count"))
        unavailable = "；该路径暂未形成候选" if str(payload.get("status") or "") == "unsupported" else ""
        state = "复用已有结果" if status == "route_reused" else "已完成"
        output.print(
            Text(
                f"T4 · P0 候选生成 · {completed}/{total} 条路径{state} · 本路径形成 {candidates} 个候选{unavailable}",
                style="dim",
                overflow="fold",
            )
        )
        return

    # Child generation is the only evolutionary phase where a researcher needs
    # object-level progress before the final Portfolio exists.  These events
    # come from the persisted EvolutionPlan, Child checkpoint, independent
    # ScoreReport, and Survival result; the renderer only formats those facts.
    # Keeping them as ordinary lines prevents nested panels and makes a slow
    # provider call readable in a narrow terminal or captured log.
    if phase == EvolutionPhase.OFFSPRING and status.startswith("child_"):
        _render_offspring_event(output, status=status, payload=payload)
        return

    # Interaction review can emit an intermediate Evolution Planning update
    # before the controller has selected Parents or compiled Child plans. It
    # is meaningful progress, but no count exists yet. Never render a dash as
    # though it were a known number of Parents or Children.
    if phase == EvolutionPhase.EVOLUTION_PLANNING and status != "completed":
        output.print(
            Text(
                f"T4 · 第 {round_number} 轮 · {title} {status_text}："
                "正在比较 Candidate 并制定 Evolution Plan；Parent 与 Child 数量会在该阶段完成后公布。",
                style="dim",
                overflow="fold",
            )
        )
        return

    # Frequent phase starts are intentionally a single muted status line.  A
    # normal run has several of them and rendering every one as a table makes
    # the terminal harder, not easier, to read.
    if status == "started":
        output.print(
            Text(
                f"T4 · 第 {round_number} 轮 · {title} 开始：{purpose}",
                style="dim",
                overflow="fold",
            )
        )
        return

    metrics = _metric_rows(phase, payload)
    if phase not in _RICH_COMPLETION_PHASES:
        summary = _compact_completion(phase, payload)
        output.print(
            Text(
                f"T4 · 第 {round_number} 轮 · {title} {status_text}：{summary}",
                style="dim",
                overflow="fold",
            )
        )
        return

    output.print(
        Panel(
            Text(purpose, overflow="fold"),
            title=f"T4 · 第 {round_number} 轮 · {title} · {status_text}",
            border_style="green" if status in {"completed", "reused"} else "bright_cyan",
            expand=True,
        )
    )
    if metrics:
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(style="bold cyan", ratio=1)
        table.add_column(ratio=2, overflow="fold")
        for label, value in metrics:
            table.add_row(label, value)
        output.print(table)
    warning = _warning_text(phase, payload)
    if warning:
        output.print(Panel(Text(warning, overflow="fold"), title="注意", border_style="yellow", expand=True))


def _metric_rows(phase: EvolutionPhase, payload: dict[str, Any]) -> list[tuple[str, str]]:
    if phase == EvolutionPhase.EVIDENCE_ROUTING:
        by_level = payload.get("counts_by_reading_level") if isinstance(payload.get("counts_by_reading_level"), dict) else {}
        by_domain = payload.get("counts_by_domain_role") if isinstance(payload.get("counts_by_domain_role"), dict) else {}
        return [
            ("已索引的笔记片段", _number(payload.get("atom_count"))),
            ("全文 / 部分全文依据", _number(sum(_safe_int(by_level.get(key)) for key in ("full_text", "partial_text")))),
            ("摘要级线索", _number(_safe_int(by_level.get("abstract_only")))),
            ("Bridge 依据", _number(_safe_int(by_domain.get("bridge")))),
            ("建议补读", _number(len(payload.get("reading_upgrade_candidates") or []))),
        ]
    if phase == EvolutionPhase.OPPORTUNITY_MAP:
        return [("研究机会", _number(payload.get("opportunity_count")))]
    if phase == EvolutionPhase.FORMATION:
        routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
        return [("各路径结果", _route_summary(routes)), ("初始候选集 P0", _number(payload.get("candidate_count")))]
    if phase == EvolutionPhase.GENOME_FAMILY:
        return [("Idea Families", _number(payload.get("family_count")))]
    if phase == EvolutionPhase.SCORING:
        return [("已完成独立评分", _number(payload.get("candidate_count")))]
    if phase == EvolutionPhase.EVOLUTION_PLANNING:
        return [("已选 Parent", _number(payload.get("parent_count")))]
    if phase == EvolutionPhase.OFFSPRING:
        return [("已生成 Child", _number(payload.get("offspring_count")))]
    if phase == EvolutionPhase.SURVIVAL:
        return [
            ("进入本轮的候选", _number(payload.get("input_count"))),
            ("新生成 Child", _number(payload.get("offspring_count"))),
            ("保留的 Active Candidates", _number(payload.get("active_count"))),
            ("归档候选", _number(payload.get("archived_count"))),
            ("供你选择的 Portfolio", _number(payload.get("portfolio_count"))),
        ]
    return []


def _compact_completion(phase: EvolutionPhase, payload: dict[str, Any]) -> str:
    if phase == EvolutionPhase.OPPORTUNITY_MAP:
        return f"已形成 {_number(payload.get('opportunity_count'))} 个可验证研究机会；下一阶段将从不同视角发散初始 Idea。"
    if phase == EvolutionPhase.GENOME_FAMILY:
        return f"已为 {_number(payload.get('candidate_count'))} 个候选建立谱系，并识别 {_number(payload.get('family_count'))} 个 Idea Families。"
    if phase == EvolutionPhase.EVOLUTION_PLANNING:
        return (
            f"已保留 {_number(payload.get('parent_count'))} 个 Parent，计划生成 "
            f"{_number(payload.get('mutation_count'))} 个 Mutation Child 和 {_number(payload.get('crossover_count'))} 个 Crossover Child。"
        )
    if phase == EvolutionPhase.OFFSPRING:
        return f"已生成 {_number(payload.get('offspring_count'))} 个 Child，正在与 Parent 一起重新评分。"
    return "阶段结果已保存。"


def _warning_text(phase: EvolutionPhase, payload: dict[str, Any]) -> str:
    # A start event intentionally has no metrics.  Only an explicit completed
    # `atom_count: 0` means there are genuinely no notes to route.
    if phase == EvolutionPhase.EVIDENCE_ROUTING and "atom_count" in payload and _safe_int(payload.get("atom_count")) == 0:
        return "当前没有可用的论文阅读笔记。T4 仍可参考综合材料，但候选的证据基础会较弱；建议先补充文献阅读。"
    if phase == EvolutionPhase.FORMATION:
        routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
        unsupported = [item for item in routes if isinstance(item, dict) and item.get("status") == "unsupported"]
        if unsupported:
            return f"有 {len(unsupported)} 条生成路径当前证据不足，系统已保留其原因，不会据此虚构候选。"
    return ""


def _route_summary(routes: list[dict[str, Any]]) -> str:
    entries = []
    for route in routes:
        name = _route_label(str(route.get("route") or "route"))
        status = "可用" if str(route.get("status") or "") == "supported" else "暂不形成候选"
        count = len(route.get("candidate_ids") or []) if isinstance(route.get("candidate_ids"), list) else 0
        entries.append(f"{name}：{count}（{status}）")
    return "；".join(entries) if entries else "-"


def _route_label(value: str) -> str:
    labels = {
        "evidence_routed_literature": "文献路径",
        "informed_brainstorm": "定向 Brainstorm",
        "cross_domain_bridge": "Cross-domain / Bridge",
    }
    return labels.get(value, value.replace("_", " "))


def _render_offspring_event(output: Console, *, status: str, payload: dict[str, Any]) -> None:
    parent = _offspring_parent_label(payload)
    routes = _offspring_routes(payload)
    operator = _offspring_operator_label(payload)
    target = _first_nonempty(payload.get("expected_improvements")) or _first_nonempty(payload.get("modify_genes"))
    progress = _offspring_progress(payload)
    child_id = str(payload.get("child_id") or "").strip()
    child_title = str(payload.get("child_title") or "").strip()
    child = " · ".join(part for part in (child_id, child_title) if part) or "本次 Child"

    if status == "child_started":
        parts = [f"→ {parent} 开始演化"]
        if routes:
            parts.append(f"来源 Route：{routes}")
        parts.append(f"方式：{operator}")
        if target:
            parts.append(f"目标：{target}")
        if progress:
            parts.append(progress)
        output.print(Text(" · ".join(parts), style="dim", overflow="fold"))
        return

    if status == "child_reused":
        output.print(
            Text(
                f"✓ {child} 已复用已保存结果 · {operator}{_with_progress(progress)}",
                style="dim",
                overflow="fold",
            )
        )
        return

    if status == "child_created":
        preserved = _first_nonempty(payload.get("preserve_genes"))
        changed = _first_nonempty(payload.get("expected_improvements")) or _first_nonempty(payload.get("modify_genes"))
        parts = [f"✓ {child} 已生成", operator]
        if preserved:
            parts.append(f"保留：{preserved}")
        if changed:
            parts.append(f"变化：{changed}")
        if progress:
            parts.append(progress)
        output.print(Text(" · ".join(parts), style="green", overflow="fold"))
        return

    if status == "child_scored":
        score_text = _offspring_score_text(payload.get("scores"))
        parts = [f"◓ {child} 已完成独立评分"]
        if score_text:
            parts.append(score_text)
        if progress:
            parts.append(progress)
        output.print(Text(" · ".join(parts), style="dim", overflow="fold"))
        return

    if status == "child_survival":
        survives = bool(payload.get("survives"))
        outcome = "进入 Survival Selection" if survives else "未进入 Survival Selection"
        output.print(
            Text(
                f"{'✓' if survives else '×'} {child} {outcome} · Parent 保留{_with_progress(progress)}",
                style="green" if survives else "yellow",
                overflow="fold",
            )
        )
        return

    if status == "child_deferred":
        reason = str(payload.get("deferral_reason") or "").strip()
        revisit = str(payload.get("revisit_condition") or "").strip()
        parts = [f"× {parent} 本轮未生成 Child"]
        if reason:
            parts.append(reason)
        if revisit:
            parts.append(f"可在以下条件重试：{revisit}")
        if progress:
            parts.append(progress)
        output.print(Text(" · ".join(parts), style="yellow", overflow="fold"))
        return

    reason = str(payload.get("failure_reason") or "").strip()
    parts = [f"× {parent} 的 Child 未保留", "两次生成或结构修复后仍未通过计划约束；Parent 保留"]
    if reason:
        parts.append(f"记录：{reason}")
    if progress:
        parts.append(progress)
    output.print(Text(" · ".join(parts), style="yellow", overflow="fold"))


def _offspring_parent_label(payload: dict[str, Any]) -> str:
    titles = _string_items(payload.get("parent_titles"))
    identifiers = _string_items(payload.get("parent_ids"))
    if titles:
        return " / ".join(titles)
    if identifiers:
        return " / ".join(identifiers)
    return "当前 Parent"


def _offspring_routes(payload: dict[str, Any]) -> str:
    return " / ".join(_route_label(item) for item in _string_items(payload.get("parent_routes")))


def _offspring_operator_label(payload: dict[str, Any]) -> str:
    raw = " ".join(str(payload.get("operator") or payload.get("child_type") or "").split()).casefold()
    labels = {
        "mutation": "Targeted Mutation",
        "crossover": "Crossover",
        "targeted mutation": "Targeted Mutation",
        "mechanism mutation": "Mechanism Mutation",
        "validation mutation": "Validation Mutation",
        "boundary mutation": "Boundary Mutation",
    }
    return labels.get(raw, raw.replace("_", " ").title() or "Evolution")


def _offspring_score_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    labels = (
        ("research_value", "研究价值"),
        ("mechanism_integrity", "机制完整性"),
        ("contribution_distinctiveness", "贡献差异性"),
    )
    parts: list[str] = []
    for key, label in labels:
        try:
            number = float(value.get(key))
        except (TypeError, ValueError):
            continue
        parts.append(f"{label} {number:.1f}")
    return " · ".join(parts)


def _offspring_progress(payload: dict[str, Any]) -> str:
    try:
        completed = max(0, int(payload.get("completed") or 0))
        total = max(0, int(payload.get("total") or 0))
    except (TypeError, ValueError):
        return ""
    return f"{completed}/{total}" if total else ""


def _with_progress(progress: str) -> str:
    return f" · {progress}" if progress else ""


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [" ".join(str(item).split()) for item in value if " ".join(str(item).split())]


def _first_nonempty(value: Any) -> str:
    items = _string_items(value)
    return items[0] if items else ""


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> str:
    return str(_safe_int(value)) if value is not None else "-"
