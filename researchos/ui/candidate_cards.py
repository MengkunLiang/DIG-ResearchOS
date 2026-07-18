"""Unified researcher-facing T4 Candidate cards.

The Gate, Resume path, and read-only Candidate inspection all receive the same
validated Gate1 mapping. This module is intentionally presentation-only: it never
creates research claims, infers evidence, or mutates a Candidate. Controllers and
the StateMachine remain responsible for the durable Candidate artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
from typing import Any, Mapping

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..ideation.models import FinalIdeaCardTranslation


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _excerpt(value: Any, *, max_chars: int, max_sentences: int = 1) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?\.])\s+", text) if part.strip()]
    selected: list[str] = []
    current_length = 0
    for sentence in sentences:
        proposed = current_length + len(sentence) + (1 if selected else 0)
        if selected and (len(selected) >= max_sentences or proposed > max_chars):
            break
        if not selected and len(sentence) > max_chars:
            break
        selected.append(sentence)
        current_length = proposed
    if selected:
        rendered = " ".join(selected)
        return rendered if len(rendered) == len(text) else rendered.rstrip("。；;，, ") + "…"
    cutoff = text[:max_chars].rstrip()
    for boundary in ("。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " "):
        position = cutoff.rfind(boundary)
        if position >= max(24, max_chars // 2):
            cutoff = cutoff[: position + (1 if boundary not in {" ", "，", ","} else 0)].rstrip()
            break
    return cutoff.rstrip("。；;，, ") + "…"


def _list_excerpt(values: Any, *, max_items: int, max_chars: int) -> str:
    if not isinstance(values, list):
        return ""
    rows = [_excerpt(value, max_chars=max_chars) for value in values[:max_items] if _clean(value)]
    return "\n".join(f"{index}. {value}" for index, value in enumerate(rows, start=1))


def _contribution_label(value: Any) -> str:
    return {
        "invention": "方法 / 系统",
        "improvement": "方法改进",
        "exaptation": "跨域迁移",
        "measurement": "测量 / 评估",
        "mechanism": "机制解释",
        "theory": "理论",
        "design": "研究设计",
        "benchmark": "基准",
        "algorithm": "算法",
        "evaluation": "评测",
        "empirical": "实证",
    }.get(_clean(value).lower(), _clean(value) or "未分类")


@dataclass(frozen=True)
class CandidateViewModel:
    """One validated candidate view shared by every Normal UI entry point."""

    candidate_id: str
    lane: str
    title: str
    final_card: dict[str, Any]
    contributions: tuple[dict[str, Any], ...]
    hypotheses: tuple[dict[str, Any], ...]
    evolution_score: dict[str, Any]
    portfolio_role: str
    candidate_stage: str
    evidence_readiness: str
    evidence_references: tuple[dict[str, Any], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateViewModel":
        try:
            final_card = FinalIdeaCardTranslation.model_validate(value.get("final_idea_card")).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise ValueError("完整的 LLM Final Idea Card 是候选展示的前提") from exc
        title = _clean(final_card.get("short_title"))
        if not title:
            raise ValueError("Final Idea Card 缺少 short_title")
        contributions = tuple(item for item in value.get("contributions", []) if isinstance(item, dict))
        hypotheses = tuple(item for item in value.get("candidate_hypotheses", []) if isinstance(item, dict))
        score = value.get("evolution_score") if isinstance(value.get("evolution_score"), dict) else {}
        evidence_references = tuple(item for item in value.get("evidence_references", []) if isinstance(item, dict))
        return cls(
            candidate_id=_clean(value.get("id")) or "?",
            lane=_clean(value.get("lane")) or "候选方向",
            title=title,
            final_card=final_card,
            contributions=contributions,
            hypotheses=hypotheses,
            evolution_score=dict(score),
            portfolio_role=_clean(value.get("portfolio_role")),
            candidate_stage=_clean(value.get("candidate_stage") or value.get("maturity")),
            evidence_readiness=_clean(value.get("evidence_readiness")),
            evidence_references=evidence_references,
        )

    @property
    def border_style(self) -> str:
        return "bright_cyan" if self.lane in {"主方向", "主线"} else "cyan"


class CandidateCardRenderer:
    """Render the shared view model as a compact card or a complete read-only card."""

    @staticmethod
    def _text(value: Any) -> Text:
        return Text(_clean(value), overflow="fold")

    @staticmethod
    def _detail_table(*, style: str, border_style: str, label_width: int = 16) -> Table:
        table = Table(
            expand=True,
            show_header=False,
            show_lines=True,
            box=box.SQUARE,
            border_style=border_style,
            padding=(0, 1),
        )
        table.add_column(style=style, width=label_width, no_wrap=True)
        table.add_column(ratio=1, overflow="fold")
        return table

    @classmethod
    def summary(cls, view: CandidateViewModel) -> Panel:
        """Render the decision-first card used by a Gate or Resume overview."""

        final = view.final_card
        stakeholders = final.get("affected_stakeholders_or_processes") if isinstance(final.get("affected_stakeholders_or_processes"), list) else []
        overview = cls._detail_table(style="bold cyan", border_style="cyan", label_width=18)
        overview.add_row("研究命题", cls._text(_excerpt(final.get("plain_language_summary"), max_chars=190, max_sentences=2)))
        overview.add_row("为何值得研究", cls._text(_excerpt(final.get("why_it_matters"), max_chars=150, max_sentences=1)))
        overview.add_row("现实 / 应用意义", cls._text(_excerpt(final.get("real_world_significance"), max_chars=150, max_sentences=1)))
        if stakeholders:
            overview.add_row("影响对象", cls._text(_list_excerpt(stakeholders, max_items=2, max_chars=60)))
        overview.add_row("建议下一步", cls._text(_excerpt(final.get("recommendation"), max_chars=145, max_sentences=1)))
        components: list[Any] = [Text("研究摘要", style="bold cyan"), overview]

        evidence = cls._detail_table(style="bold yellow", border_style="yellow", label_width=18)
        if view.evidence_readiness:
            evidence.add_row("当前证据状态", cls._text(view.evidence_readiness))
        evidence_summary = _clean(final.get("evidence_status_summary"))
        if evidence_summary:
            evidence.add_row("证据边界", cls._text(_excerpt(evidence_summary, max_chars=190, max_sentences=2)))
        if view.evidence_references:
            references = "\n".join(
                f"《{_clean(item.get('title'))}》\n{_clean(item.get('reading_label'))}；{_clean(item.get('evidence_label'))}"
                for item in view.evidence_references[:2]
                if _clean(item.get("title"))
            )
            if references:
                evidence.add_row("关键材料", cls._text(references))
        if evidence.row_count:
            components.extend([Text("证据状态与优先核验", style="bold yellow"), evidence])

        if view.contributions:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold green", border_style="green")
            table.add_column("核心贡献", width=16, no_wrap=True)
            table.add_column("提出什么", ratio=3, overflow="fold")
            table.add_column("若成立的改变", ratio=2, overflow="fold")
            for index, contribution in enumerate(view.contributions[:2], start=1):
                statement = _clean(contribution.get("statement"))
                changed = _clean(contribution.get("what_changes_if_true"))
                table.add_row(
                    f"贡献 {index} · {_contribution_label(contribution.get('type'))}",
                    cls._text(_excerpt(statement, max_chars=135)),
                    cls._text(_excerpt(changed, max_chars=110) if changed and changed != statement else "完整论证见详情。"),
                )
            if table.row_count:
                components.extend([Text("核心贡献", style="bold green"), table])

        if view.hypotheses:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold yellow", border_style="yellow")
            table.add_column("可检验假设", width=12, no_wrap=True)
            table.add_column("主张", ratio=3, overflow="fold")
            table.add_column("观察信号 / 判据", ratio=2, overflow="fold")
            for index, hypothesis in enumerate(view.hypotheses[:2], start=1):
                statement = _clean(hypothesis.get("statement"))
                signal = _clean(hypothesis.get("prediction") or hypothesis.get("observable_prediction"))
                table.add_row(
                    f"H{index}",
                    cls._text(_excerpt(statement, max_chars=135)),
                    cls._text(_excerpt(signal, max_chars=110) if signal and signal != statement else "完整判别测试见详情。"),
                )
            if table.row_count:
                components.extend([Text("关键假设与验证信号", style="bold yellow"), table])

        dimensions = view.evolution_score.get("dimensions") if isinstance(view.evolution_score.get("dimensions"), dict) else {}
        rationales = view.evolution_score.get("rationales") if isinstance(view.evolution_score.get("rationales"), dict) else {}
        if dimensions:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold blue", border_style="blue")
            table.add_column("评分维度", width=16, no_wrap=True)
            table.add_column("分数", width=7, justify="center", no_wrap=True)
            table.add_column("评分依据", ratio=3, overflow="fold")
            for key, label in (
                ("research_value", "研究价值"),
                ("mechanism_integrity", "机制完整性"),
                ("contribution_distinctiveness", "贡献差异性"),
            ):
                if dimensions.get(key) is not None:
                    rationale = _clean(rationales.get(key))
                    table.add_row(label, f"{dimensions[key]}/5", cls._text(_excerpt(rationale, max_chars=145) if rationale else "完整评分依据见详情。"))
            if table.row_count:
                components.extend([Text("三项决策评分", style="bold blue"), table])

        risks = final.get("risks_and_boundaries") if isinstance(final.get("risks_and_boundaries"), list) else []
        bottleneck = _clean(view.evolution_score.get("dominant_bottleneck"))
        if bottleneck or risks:
            table = cls._detail_table(style="bold red", border_style="red", label_width=16)
            if bottleneck:
                table.add_row("当前扣分点", cls._text(_excerpt(bottleneck, max_chars=145)))
            if risks:
                table.add_row("主要风险", cls._text(_list_excerpt(risks, max_items=2, max_chars=110)))
            components.extend([Text("风险与下一步", style="bold red"), table])

        components.append(
            Text(
                f"上方为简述。输入“查看 {view.candidate_id}”可阅读完整卡片；查看评分、证据、假设、贡献或谱系均为只读，不会确认操作或改变版本。",
                style="dim",
                overflow="fold",
            )
        )
        return Panel(Group(*components), title=f"[bold]{view.candidate_id} · {view.lane} · {view.title}[/bold]", border_style=view.border_style, expand=True)

    @classmethod
    def detail(cls, view: CandidateViewModel) -> Panel:
        """Render the complete card for an explicit read-only Candidate request."""

        final = view.final_card
        overview = cls._detail_table(style="bold cyan", border_style="cyan", label_width=18)
        for label, field in (
            ("研究命题", "plain_language_summary"),
            ("核心命题", "core_thesis"),
            ("为何值得研究", "why_it_matters"),
            ("当前问题", "current_failure"),
            ("科学 / 技术核心", "scientific_technical_core"),
            ("代表性场景", "representative_scenario"),
            ("现实 / 应用意义", "real_world_significance"),
            ("建议下一步", "recommendation"),
        ):
            value = _clean(final.get(field))
            if value:
                overview.add_row(label, cls._text(value))
        components: list[Any] = [Text("完整研究说明", style="bold cyan"), overview]

        innovation = cls._detail_table(style="bold magenta", border_style="magenta", label_width=18)
        for label, field in (("创新性质", "innovation_type"), ("相对变化", "innovation_delta"), ("非惯例理由", "non_routine_explanation")):
            value = _clean(final.get(field))
            if value:
                innovation.add_row(label, cls._text(value))
        if innovation.row_count:
            components.extend([Text("核心创新", style="bold magenta"), innovation])

        if view.contributions:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold green", border_style="green")
            table.add_column("贡献", width=12, no_wrap=True)
            table.add_column("类型", width=15, overflow="fold")
            table.add_column("贡献命题", ratio=2, overflow="fold")
            table.add_column("若成立会改变什么", ratio=2, overflow="fold")
            for index, contribution in enumerate(view.contributions, start=1):
                table.add_row(
                    f"C{index}",
                    _contribution_label(contribution.get("type")),
                    cls._text(contribution.get("statement")),
                    cls._text(contribution.get("what_changes_if_true")),
                )
            components.extend([Text("核心贡献", style="bold green"), table])

        if view.hypotheses:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold yellow", border_style="yellow")
            table.add_column("假设", width=10, no_wrap=True)
            table.add_column("主张", ratio=2, overflow="fold")
            table.add_column("机制", ratio=2, overflow="fold")
            table.add_column("预测 / 判别测试", ratio=3, overflow="fold")
            for index, hypothesis in enumerate(view.hypotheses, start=1):
                prediction = _clean(hypothesis.get("prediction") or hypothesis.get("observable_prediction"))
                test = _clean(hypothesis.get("test") or hypothesis.get("discriminating_test"))
                detail = "\n".join(part for part in (f"预测：{prediction}" if prediction else "", f"测试：{test}" if test else "") if part)
                table.add_row(
                    f"H{index}",
                    cls._text(hypothesis.get("statement")),
                    cls._text(hypothesis.get("mechanism")),
                    cls._text(detail),
                )
            components.extend([Text("关键假设与验证", style="bold yellow"), table])

        dimensions = view.evolution_score.get("dimensions") if isinstance(view.evolution_score.get("dimensions"), dict) else {}
        rationales = view.evolution_score.get("rationales") if isinstance(view.evolution_score.get("rationales"), dict) else {}
        if dimensions:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold blue", border_style="blue")
            table.add_column("评分维度", width=18, no_wrap=True)
            table.add_column("分数", width=8, justify="center", no_wrap=True)
            table.add_column("依据与扣分点", ratio=3, overflow="fold")
            for key, label in (("research_value", "研究价值"), ("mechanism_integrity", "机制完整性"), ("contribution_distinctiveness", "贡献差异性")):
                if dimensions.get(key) is not None:
                    table.add_row(label, f"{dimensions[key]}/5", cls._text(rationales.get(key)))
            components.extend([Text("三项决策评分", style="bold blue"), table])

        implications = final.get("implications") if isinstance(final.get("implications"), list) else []
        impact = cls._detail_table(style="bold bright_cyan", border_style="bright_cyan", label_width=18)
        for implication in implications:
            if not isinstance(implication, dict):
                continue
            statement = _clean(implication.get("statement"))
            if statement:
                impact.add_row(_clean(implication.get("implication_type")) or "研究意义", cls._text(statement))
        if impact.row_count:
            components.extend([Text("现实与研究影响", style="bold bright_cyan"), impact])

        evidence = cls._detail_table(style="bold yellow", border_style="yellow", label_width=18)
        if view.evidence_readiness:
            evidence.add_row("当前证据状态", cls._text(view.evidence_readiness))
        evidence_summary = _clean(final.get("evidence_status_summary"))
        if evidence_summary:
            evidence.add_row("证据边界", cls._text(evidence_summary))
        if evidence.row_count:
            components.extend([Text("证据状态", style="bold yellow"), evidence])

        if view.evidence_references:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold yellow", border_style="yellow")
            table.add_column("关键材料", ratio=2, overflow="fold")
            table.add_column("阅读 / 证据状态", ratio=2, overflow="fold")
            table.add_column("追溯编号", width=22, overflow="fold")
            for reference in view.evidence_references:
                title = _clean(reference.get("title"))
                if not title:
                    continue
                state = "；".join(
                    item for item in (_clean(reference.get("reading_label")), _clean(reference.get("evidence_label"))) if item
                )
                source = _clean(reference.get("source_path"))
                table.add_row(
                    cls._text(title + (f"\n{source}" if source else "")),
                    cls._text(state),
                    cls._text(reference.get("atom_id")),
                )
            if table.row_count:
                components.extend([Text("关键证据材料", style="bold yellow"), table])

        risks = final.get("risks_and_boundaries") if isinstance(final.get("risks_and_boundaries"), list) else []
        risk = cls._detail_table(style="bold red", border_style="red", label_width=18)
        bottleneck = _clean(view.evolution_score.get("dominant_bottleneck"))
        if bottleneck:
            risk.add_row("当前扣分点", cls._text(bottleneck))
        if risks:
            risk.add_row("主要风险", cls._text(_list_excerpt(risks, max_items=len(risks), max_chars=300)))
        if risk.row_count:
            components.extend([Text("风险与后续决策", style="bold red"), risk])
        components.append(Text("只读查看结束后仍停留在当前 Gate。输入推进、优化、再探索或暂停才会进入新的决策流程。", style="dim", overflow="fold"))
        return Panel(Group(*components), title=f"[bold]{view.candidate_id} · 完整说明 · {view.title}[/bold]", border_style=view.border_style, expand=True)

    @classmethod
    def plain_summary(cls, view: CandidateViewModel, *, width: int = 96) -> str:
        """Use the same compact card for redirected/no-colour terminal output."""

        buffer = io.StringIO()
        console = Console(file=buffer, width=max(80, width), no_color=True, highlight=False)
        console.print(cls.summary(view))
        return buffer.getvalue().rstrip()
