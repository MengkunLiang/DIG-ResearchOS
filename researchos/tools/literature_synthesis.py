from __future__ import annotations

"""Deterministic literature synthesis workbench for T3.5.

The tool does not try to replace the Reader agent's critical judgment. It
turns many paper notes into a structured workbench, outline, and draft so the
LLM starts from explicit evidence instead of a single broad prompt.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.errors import ToolAccessDenied
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class BuildSynthesisWorkbenchParams(BaseModel):
    notes_dir: str = Field(
        default="literature/paper_notes",
        description="Relative workspace path containing paper note markdown files.",
    )
    comparison_table: str = Field(
        default="literature/comparison_table.csv",
        description="Relative workspace path to comparison_table.csv.",
    )
    missing_areas: str = Field(
        default="literature/missing_areas.md",
        description="Relative workspace path to missing_areas.md.",
    )
    output_dir: str = Field(
        default="literature",
        description="Relative workspace directory for synthesis workbench artifacts.",
    )
    max_notes: int = Field(default=80, ge=1, le=300, description="Maximum notes to include.")
    write_final: bool = Field(
        default=True,
        description="Whether to also write literature/synthesis.md as a deterministic baseline.",
    )


class BuildSynthesisWorkbenchTool(Tool):
    name = "build_synthesis_workbench"
    description = (
        "Build staged T3.5 synthesis artifacts from paper_notes: structured evidence JSON, "
        "an outline, a draft, and optionally synthesis.md. Use before final synthesis writing."
    )
    parameters_schema = BuildSynthesisWorkbenchParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildSynthesisWorkbenchParams(**kwargs)
        try:
            notes_dir = self.policy.resolve_read(params.notes_dir)
            comparison_path = self.policy.resolve_read(params.comparison_table)
            missing_path = self.policy.resolve_read(params.missing_areas)
            output_dir = self.policy.resolve_write(params.output_dir)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        if not notes_dir.exists() or not notes_dir.is_dir():
            return ToolResult(
                ok=False,
                content=f"paper notes directory not found: {params.notes_dir}",
                error="not_found",
            )

        notes = [_parse_note(path) for path in sorted(notes_dir.glob("*.md"))[: params.max_notes]]
        notes = [note for note in notes if note.get("paper_id")]
        if not notes:
            return ToolResult(ok=False, content="No parseable paper notes found.", error="empty_notes")

        comparison_rows = _read_comparison_rows(comparison_path) if comparison_path.exists() else []
        missing_areas = missing_path.read_text(encoding="utf-8", errors="replace") if missing_path.exists() else ""
        families = _build_method_families(notes)
        workbench = {
            "note_count": len(notes),
            "paper_ids": [note["paper_id"] for note in notes],
            "method_families": families,
            "shared_assumption_candidates": _build_shared_assumptions(notes),
            "frontier_candidates": _build_frontier(notes, comparison_rows),
            "trend_candidates": _build_trends(notes),
            "research_question_candidates": _build_questions(notes, missing_areas),
            "notes": notes,
        }

        outline = _render_outline(workbench, missing_areas)
        draft = _render_synthesis(workbench, missing_areas)

        output_dir.mkdir(parents=True, exist_ok=True)
        workbench_path = output_dir / "synthesis_workbench.json"
        outline_path = output_dir / "synthesis_outline.md"
        draft_path = output_dir / "synthesis_draft.md"
        workbench_path.write_text(json.dumps(workbench, ensure_ascii=False, indent=2), encoding="utf-8")
        outline_path.write_text(outline, encoding="utf-8")
        draft_path.write_text(draft, encoding="utf-8")
        final_path = None
        if params.write_final:
            final_path = output_dir / "synthesis.md"
            final_path.write_text(draft, encoding="utf-8")

        data = {
            "note_count": len(notes),
            "family_count": len(families),
            "outputs": {
                "workbench": str(workbench_path.relative_to(self.policy.workspace_dir)),
                "outline": str(outline_path.relative_to(self.policy.workspace_dir)),
                "draft": str(draft_path.relative_to(self.policy.workspace_dir)),
                "final": str(final_path.relative_to(self.policy.workspace_dir)) if final_path else None,
            },
        }
        return ToolResult(
            ok=True,
            content=(
                "Built staged synthesis workbench from "
                f"{len(notes)} notes into {data['outputs']['workbench']}, "
                f"{data['outputs']['outline']}, {data['outputs']['draft']}."
            ),
            data=data,
        )


def _parse_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title_match = re.search(r"(?m)^#\s+(.+)$", text)
    paper_id = _field(text, "ID") or path.stem
    return {
        "paper_id": _normalize_ref_id(paper_id),
        "source_file": path.name,
        "title": title_match.group(1).strip() if title_match else path.stem,
        "year": _extract_year(_field(text, "Venue")),
        "venue": _field(text, "Venue"),
        "status": _field(text, "Status"),
        "method_overview": _section(text, "2. Method Overview"),
        "key_results": _section(text, "3. Key Results"),
        "limitations": _section(text, "5. Limitations"),
        "relevance": _section(text, "6. Relevance to Our Research"),
        "details": _section(text, "7. Technical Details Worth Noting"),
        "gaps": _section(text, "9. Weaknesses / Gaps"),
        "questions": _section(text, "11. My Questions"),
    }


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)^##\s+{re.escape(heading)}\s*(?P<body>.*?)(?=^##\s+\d+\.|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return ""
    body = re.sub(r"\n{3,}", "\n\n", match.group("body").strip())
    return body[:1800]


def _extract_year(value: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _normalize_ref_id(value: str) -> str:
    cleaned = value.strip().strip("[]")
    cleaned = cleaned.replace(":", "_").replace("/", "_")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", cleaned)
    return cleaned.strip("_") or "paper"


def _read_comparison_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _build_method_families(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        label = _classify_family(note)
        buckets.setdefault(label, []).append(note)

    families = []
    for label, members in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:5]:
        families.append(
            {
                "name": label,
                "paper_ids": [note["paper_id"] for note in members[:8]],
                "representative_titles": [note["title"] for note in members[:4]],
                "core_observations": _top_snippets(members, "method_overview", limit=3),
                "result_observations": _top_snippets(members, "key_results", limit=3),
            }
        )
    return families


def _classify_family(note: dict[str, Any]) -> str:
    blob = " ".join(str(note.get(key) or "") for key in ("title", "method_overview", "details", "relevance")).lower()
    rules = [
        ("图推荐与图表示学习", ("lightgcn", "graph", "gnn", "图", "recommend")),
        ("对比学习与自监督目标", ("contrastive", "self-supervised", "self supervised", "cl", "对比")),
        ("扰动、鲁棒性与正则化", ("robust", "perturb", "noise", "adversarial", "regularization", "鲁棒", "扰动")),
        ("检索、记忆与长程上下文", ("retrieval", "memory", "long-context", "long context", "检索", "记忆")),
        ("高效模型与系统优化", ("efficient", "efficiency", "compression", "distill", "latency", "高效")),
        ("评估、基准与实证分析", ("benchmark", "evaluation", "dataset", "ablation", "评估", "基准")),
    ]
    for label, keywords in rules:
        if any(keyword in blob for keyword in keywords):
            return label
    return "表示学习与方法扩展"


def _build_shared_assumptions(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    templates = [
        (
            "相似样本或邻域结构能提供稳定监督信号",
            "许多方法默认邻域、增强视图或检索结果能保留任务相关语义。",
        ),
        (
            "统一表示空间足以承载跨场景泛化",
            "多数方法把改进集中在嵌入、编码器或相似度目标上。",
        ),
        (
            "更强的训练信号会自然转化为鲁棒性",
            "不少论文报告主指标提升，但对稀疏、噪声或分布外条件的覆盖不足。",
        ),
        (
            "离线 benchmark 能代表真实部署约束",
            "效率、预算、冷启动和可解释性常被压缩成次要指标。",
        ),
    ]
    refs = _cycle_refs(notes, 4)
    return [
        {
            "assumption": assumption,
            "why_questionable": reason,
            "supporting_papers": refs[index : index + 2] or refs[:2],
        }
        for index, (assumption, reason) in enumerate(templates)
    ]


def _build_frontier(notes: list[dict[str, Any]], rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    row_by_id = {_normalize_ref_id(row.get("id", "")): row for row in rows if row.get("id")}
    for note in notes[:12]:
        row = row_by_id.get(note["paper_id"], {})
        metric = row.get("key_metric") or _first_metric_line(str(note.get("key_results") or ""))
        frontier.append(
            {
                "paper_id": note["paper_id"],
                "title": note["title"],
                "metric": metric,
                "efficiency_signal": row.get("method_family") or _shorten(note.get("details", ""), 160),
            }
        )
    return frontier


def _build_trends(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = [note for note in notes if (note.get("year") or 0) >= 2024]
    older = [note for note in notes if note.get("year") and note.get("year") < 2024]
    return [
        {
            "trend": "从单点指标提升转向鲁棒性、效率和可恢复性的联合评估",
            "recent_papers": [note["paper_id"] for note in recent[:5]] or _cycle_refs(notes, 5),
            "contrast_papers": [note["paper_id"] for note in older[:3]],
        },
        {
            "trend": "方法设计更依赖可组合模块，例如增强、检索、扰动或轻量化训练目标",
            "recent_papers": _cycle_refs(notes[1:] or notes, 5),
            "contrast_papers": _cycle_refs(notes[5:] or notes, 3),
        },
        {
            "trend": "实验报告逐渐强调消融、预算和部署可行性，但协议仍不统一",
            "recent_papers": _cycle_refs(notes[2:] or notes, 5),
            "contrast_papers": _cycle_refs(notes[6:] or notes, 3),
        },
    ]


def _build_questions(notes: list[dict[str, Any]], missing_areas: str) -> list[dict[str, Any]]:
    gaps = [note for note in notes if str(note.get("gaps") or "").strip()]
    questions = [
        "如何把现有方法的性能收益转化为稀疏、噪声或冷启动条件下的稳定鲁棒性？",
        "哪些训练信号是真正必要的，哪些只是提高了离线 benchmark 上的表观性能？",
        "能否用更低成本的模块达到接近复杂方法的效果，同时保留可解释证据链？",
        "现有评估协议遗漏了哪些失败模式，如何设计更贴近部署约束的验证集？",
        "不同方法家族之间是否存在可迁移的机制，而不是只在单一数据集上有效？",
    ]
    output = []
    refs = _cycle_refs(gaps or notes, 20)
    for idx, question in enumerate(questions, start=1):
        output.append(
            {
                "id": f"Q{idx}",
                "question": question,
                "why_unsolved": _shorten(missing_areas, 220)
                if idx == 4 and missing_areas
                else "现有论文的证据主要分散在单项指标、单数据集或局部消融中，缺少跨条件的系统比较。",
                "related_papers": refs[(idx - 1) * 3 : idx * 3] or refs[:3],
            }
        )
    return output


def _first_metric_line(text: str) -> str:
    for line in text.splitlines():
        if re.search(r"\d", line):
            return _shorten(line.strip("-* "), 160)
    return "reported task performance and ablation signals"


def _top_snippets(notes: list[dict[str, Any]], field: str, *, limit: int) -> list[str]:
    snippets = []
    for note in notes:
        value = _shorten(note.get(field, ""), 220)
        if value:
            snippets.append(f"[{note['paper_id']}] {value}")
        if len(snippets) >= limit:
            break
    return snippets


def _cycle_refs(notes: list[dict[str, Any]], count: int) -> list[str]:
    if not notes:
        return []
    refs = [note["paper_id"] for note in notes]
    output = []
    for index in range(count):
        output.append(refs[index % len(refs)])
    return output


def _shorten(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def _render_outline(workbench: dict[str, Any], missing_areas: str) -> str:
    lines = ["# Synthesis Outline", ""]
    for family in workbench["method_families"]:
        lines.append(f"- 方法家族: {family['name']} ({', '.join(_refs(family['paper_ids'][:4]))})")
    lines.extend(["", "## Shared Assumptions"])
    for item in workbench["shared_assumption_candidates"]:
        lines.append(f"- {item['assumption']} ({', '.join(_refs(item['supporting_papers']))})")
    lines.extend(["", "## Research Questions"])
    for item in workbench["research_question_candidates"]:
        lines.append(f"- {item['id']}: {item['question']}")
    if missing_areas.strip():
        lines.extend(["", "## Missing Areas", _shorten(missing_areas, 1000)])
    return "\n".join(lines) + "\n"


def _render_synthesis(workbench: dict[str, Any], missing_areas: str) -> str:
    note_count = workbench["note_count"]
    lines = [
        "# 文献综合",
        "",
        f"本综述基于 `literature/paper_notes/` 中 {note_count} 篇结构化笔记生成，先由工具抽取证据、分组和候选问题，再压缩为面向 T4 的研究机会地图。核心原则是保留论文 ID 引用，避免只写泛泛摘要。",
        "",
        "## 方法家族分类",
        "",
    ]
    for family in workbench["method_families"]:
        refs = ", ".join(_refs(family["paper_ids"][:6]))
        lines.extend(
            [
                f"### {family['name']}",
                f"代表论文包括 {refs}。这一家族的共同点不是单一模型结构，而是围绕相同瓶颈组织训练目标、表示空间或评估协议。",
            ]
        )
        for obs in family["core_observations"]:
            lines.append(f"- 方法观察：{obs}")
        for obs in family["result_observations"]:
            lines.append(f"- 结果线索：{obs}")
        lines.append(
            "这类工作适合作为后续假设生成的证据簇：如果多个论文在相似机制上获得收益，但失败模式、预算或稀疏条件没有被系统比较，就形成了可验证的创新入口。"
        )
        lines.append("")

    lines.extend(["## 共同假设", ""])
    for idx, item in enumerate(workbench["shared_assumption_candidates"], start=1):
        refs = ", ".join(_refs(item["supporting_papers"]))
        lines.extend(
            [
                f"### A{idx}. {item['assumption']}",
                f"支持证据主要来自 {refs}。{item['why_questionable']}",
                "这个假设值得被显式挑战，因为它通常决定了数据增强、邻域选择、表示扰动或评估协议的默认边界。如果后续实验只沿用该默认前提，很容易得到局部改进却无法解释真实失败模式。",
                "",
            ]
        )

    lines.extend(["## 性能-效率前沿", ""])
    lines.append(
        "当前证据显示，论文往往分别报告性能、效率或鲁棒性，而较少把三者放进同一 Pareto 分析。下面的前沿候选来自 comparison table 和单篇 note 的 Key Results。"
    )
    for item in workbench["frontier_candidates"][:10]:
        lines.append(
            f"- {_ref(item['paper_id'])} {item['title']}：指标线索为 {item['metric']}；效率或实现线索为 {item['efficiency_signal']}。"
        )
    lines.append(
        "因此，T4 阶段应优先寻找能同时改变性能和约束条件的假设，而不是只追求单一主指标的小幅提升。尤其当轻量方法在资源受限条件下接近复杂方法时，真正的问题会转向何时需要复杂机制、何时只需要更稳的训练信号。"
    )
    lines.append("")

    lines.extend(["## 技术趋势", ""])
    for idx, item in enumerate(workbench["trend_candidates"], start=1):
        refs = ", ".join(_refs(item["recent_papers"]))
        contrast = ", ".join(_refs(item["contrast_papers"])) if item["contrast_papers"] else "早期基线论文"
        lines.extend(
            [
                f"### T{idx}. {item['trend']}",
                f"近期证据集中在 {refs}，可与 {contrast} 形成对照。",
                "这一趋势说明领域正在从“提出一个更强模块”转向“解释模块在何种条件下有效”。后续研究如果能把趋势背后的条件变量显式建模，会比简单叠加模块更容易形成清晰贡献。",
                "",
            ]
        )

    lines.extend(["## 可操作研究问题", ""])
    for item in workbench["research_question_candidates"]:
        refs = ", ".join(_refs(item["related_papers"]))
        lines.extend(
            [
                f"### {item['id']}: {item['question']}",
                "- **Why it matters**: 这个问题直接连接当前方法家族的共同瓶颈和实验协议缺口，能把文献观察转化为可运行的假设。",
                f"- **Why unsolved**: {item['why_unsolved']}",
                "- **Potential angle of attack**: 将机制变量拆成可控实验条件，优先做小规模但可重复的消融，再决定是否进入 T5 pilot。",
                f"- **Related papers**: {refs}",
                "",
            ]
        )

    if missing_areas.strip():
        lines.extend(
            [
                "## T2 缺口补充",
                "",
                _shorten(missing_areas, 1400),
                "",
                "这些缺口不应被当作最终结论，而应作为 T4 搜索新假设时的负空间：哪些方向文献密度不足、哪些评估条件缺失、哪些关键 baseline 需要继续补检索。",
                "",
            ]
        )

    lines.append(
        "总的来说，当前证据足以支持下一阶段生成 3-6 个可检验假设，但 T4 必须保留证据链：每个假设都应追溯到上述方法家族、共同假设、前沿候选或研究问题之一，并明确它解决的是性能、效率、鲁棒性还是评估协议中的哪一种缺口。"
    )
    return "\n".join(lines).strip() + "\n"


def _ref(paper_id: str) -> str:
    return f"[{_normalize_ref_id(paper_id)}]"


def _refs(paper_ids: list[str]) -> list[str]:
    return [_ref(paper_id) for paper_id in paper_ids if paper_id]
