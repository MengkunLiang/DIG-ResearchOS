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

from ..time_utils import recent_year_from
from ..runtime.errors import ToolAccessDenied
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class FamilyClassification(BaseModel):
    paper_id: str = Field(description="Normalized paper ID.")
    family: str = Field(description="LLM-assigned method family name.")
    confidence: str = Field(default="high", description="Classification confidence: high/medium/low.")


class SharedAssumption(BaseModel):
    assumption: str = Field(description="Description of the shared assumption.")
    why_questionable: str = Field(description="Why this assumption may not hold.")
    supporting_papers: list[str] = Field(default_factory=list, description="Paper IDs supporting this assumption.")


class Trend(BaseModel):
    trend: str = Field(description="Trend description.")
    recent_papers: list[str] = Field(default_factory=list, description="Recent paper IDs.")
    contrast_papers: list[str] = Field(default_factory=list, description="Older paper IDs for contrast.")


class ResearchQuestion(BaseModel):
    id: str = Field(description="Question ID, e.g. Q1.")
    question: str = Field(description="Research question text.")
    why_unsolved: str = Field(default="", description="Why this question remains open.")
    related_papers: list[str] = Field(default_factory=list, description="Related paper IDs.")


class CrossPaperTension(BaseModel):
    tension: str = Field(description="Cross-paper contradiction or design-rationale tension.")
    competing_rationales: list[str] = Field(default_factory=list, description="Competing rationales in free text.")
    paper_ids: list[str] = Field(default_factory=list, description="Paper IDs involved in this tension.")
    idea_fuel: str = Field(default="", description="How the tension may fuel forward ideation.")


class LLMInsights(BaseModel):
    """LLM-generated insights from the Reader agent.

    When provided, these override the deterministic/heuristic generation
    in the synthesis workbench tool, replacing hardcoded templates with
    domain-specific analysis from the LLM.
    """
    family_classifications: list[FamilyClassification] = Field(
        default_factory=list,
        description="Per-paper method family classifications from LLM analysis.",
    )
    shared_assumptions: list[SharedAssumption] = Field(
        default_factory=list,
        description="LLM-identified shared assumptions across the paper pool.",
    )
    trends: list[Trend] = Field(
        default_factory=list,
        description="LLM-identified technical trends.",
    )
    research_questions: list[ResearchQuestion] = Field(
        default_factory=list,
        description="LLM-generated actionable research questions.",
    )
    cross_paper_tensions: list[CrossPaperTension] = Field(
        default_factory=list,
        description="LLM-identified cross-paper design-rationale tensions.",
    )


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
    domain_map_path: str = Field(
        default="literature/domain_map.json",
        description="Relative workspace path to T2 domain_map.json.",
    )
    output_dir: str = Field(
        default="literature",
        description="Relative workspace directory for synthesis workbench artifacts.",
    )
    max_notes: int = Field(default=80, ge=1, le=300, description="Maximum notes to include.")
    write_final: bool = Field(
        default=False,
        description=(
            "Whether to also write literature/synthesis.md as a baseline draft. "
            "Default is false because final synthesis should be written/revised by the Reader LLM."
        ),
    )
    render_draft: bool = Field(
        default=False,
        description=(
            "Whether to render a prose synthesis_draft.md. Default false keeps the tool as "
            "an evidence workbench/outline builder instead of a deterministic knowledge writer."
        ),
    )
    llm_insights: LLMInsights | None = Field(
        default=None,
        description=(
            "Optional LLM-generated insights from the Reader agent. "
            "When provided, overrides deterministic family classification, "
            "shared assumption generation, trend detection, and research question formulation."
        ),
    )


class BuildSynthesisWorkbenchTool(Tool):
    name = "build_synthesis_workbench"
    description = (
        "Build staged T3.5 synthesis artifacts from paper_notes: structured evidence JSON, "
        "an outline, and optionally a baseline draft. Use before final LLM synthesis writing."
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
            domain_map_path = self.policy.resolve_read(params.domain_map_path)
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

        # 读取 abstract-only notes（可选目录）
        abstract_dir = notes_dir.parent / "paper_notes_abstract"
        abstract_notes: list[dict] = []
        if abstract_dir.exists() and abstract_dir.is_dir():
            abstract_notes = [_parse_note(path, evidence_level="ABSTRACT_ONLY") for path in sorted(abstract_dir.glob("*.md"))]
            abstract_notes = [note for note in abstract_notes if note.get("paper_id")]

        if not notes and not abstract_notes:
            return ToolResult(ok=False, content="No parseable paper notes found.", error="empty_notes")

        comparison_rows = _read_comparison_rows(comparison_path) if comparison_path.exists() else []
        missing_areas = missing_path.read_text(encoding="utf-8", errors="replace") if missing_path.exists() else ""
        domain_map = _read_json(domain_map_path) if domain_map_path.exists() else {}
        insights = params.llm_insights
        families = _build_method_families(notes, abstract_notes, llm_insights=insights)
        all_notes = notes + abstract_notes
        workbench = {
            "note_count": len(notes),
            "abstract_note_count": len(abstract_notes),
            "total_note_count": len(all_notes),
            "paper_ids": [note["paper_id"] for note in all_notes],
            "method_families": families,
            "shared_assumption_candidates": _build_shared_assumptions(notes, llm_insights=insights),
            "metric_landscape_hints": _build_metric_landscape_hints(notes, comparison_rows),
            "contribution_space": _build_contribution_space(notes, abstract_notes),
            "cross_paper_tensions": _build_cross_paper_tensions(notes, llm_insights=insights),
            "citation_graph_context": _build_citation_graph_context(domain_map),
            "domain_map_bucket_summary": _build_domain_map_bucket_summary(domain_map),
            "adjacent_transfers": _build_adjacent_transfers(domain_map, all_notes),
            "trend_candidates": _build_trends(notes, llm_insights=insights),
            "research_question_candidates": _build_questions(notes, missing_areas, llm_insights=insights),
            "mechanism_claim_clusters": _build_mechanism_claim_clusters(all_notes),
            "notes": notes,
        }
        # Backward-compatible alias. Treat as mechanical mechanism-claim
        # clusters, not authoritative domain consensus.
        workbench["domain_consensus"] = workbench["mechanism_claim_clusters"]

        outline = _render_outline(workbench, missing_areas)
        draft = _render_synthesis(workbench, missing_areas) if params.render_draft or params.write_final else _render_draft_guidance(workbench)

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
            "draft_is_guidance_only": not (params.render_draft or params.write_final),
        }
        return ToolResult(
            ok=True,
            content=(
                "Built staged synthesis workbench from "
                f"{len(notes)} notes into {data['outputs']['workbench']}, "
                f"{data['outputs']['outline']}, {data['outputs']['draft']}. "
                "Final synthesis remains the Reader LLM's responsibility."
            ),
            data=data,
        )


def _parse_note(path: Path, evidence_level: str = "FULL_TEXT") -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title_match = re.search(r"(?m)^#\s+(.+)$", text)
    paper_id = _field(text, "ID") or path.stem
    status_raw = _field(text, "Status")
    # 从 Status 或参数推断 evidence_level
    if "ABSTRACT-ONLY" in status_raw:
        evidence_level = "ABSTRACT_ONLY"
    return {
        "paper_id": _normalize_ref_id(paper_id),
        "source_file": path.name,
        "title": title_match.group(1).strip() if title_match else path.stem,
        "year": _extract_year(_field(text, "Venue")),
        "venue": _field(text, "Venue"),
        "status": status_raw,
        "evidence_level": evidence_level,
        "method_overview": _section(text, "2. Method Overview") or _section(text, "2. Method Summary"),
        "core_approach_view": _section(text, "A. 核心做法/视角"),
        "bridge_point": _section(text, "B. 桥接点"),
        "key_results": _section(text, "3. Key Results"),
        "limitations": _section(text, "5. Limitations"),
        "relevance": _section(text, "6. Relevance to Our Research"),
        "details": _section(text, "7. Technical Details Worth Noting"),
        "gaps": _section(text, "9. Weaknesses / Gaps"),
        "questions": _section(text, "11. My Questions"),
        "mechanism_claim": _extract_mechanism_claim(text),
        "design_rationale": _extract_design_rationale(text),
        "artifact_design": _extract_artifact_design(text),
        "data_view": _extract_data_view(text),
        "contribution_type": _extract_contribution_type(text),
        "boundary_conditions": _extract_boundary_conditions(text),
        "cross_paper_tension": _extract_cross_paper_tension(text),
    }


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)^##\s+{re.escape(heading)}\s*(?P<body>.*?)(?=^##\s+|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return ""
    body = re.sub(r"\n{3,}", "\n\n", match.group("body").strip())
    return body[:1800]


def _extract_year(value: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _extract_mechanism_claim(text: str) -> dict[str, str]:
    """从 §13 Mechanism Claim 提取三个字段。"""
    section_match = re.search(
        r"(?ms)^## 13\. Mechanism Claim\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
        text,
    )
    if not section_match:
        return {}
    section = section_match.group("section")
    return {
        "stated_mechanism": _field(section, "Stated mechanism"),
        "evidence_type": _field(section, "Evidence type"),
        "supporting_artifact": _field(section, "Supporting artifact"),
    }


def _extract_design_rationale(text: str) -> dict[str, str]:
    section = _numbered_section(text, "14. Design Rationale")
    if not section:
        return {}
    return {
        "rationale": _field(section, "Rationale"),
        "rationale_evidence": _field(section, "Rationale evidence"),
        "rationale_weakness": _field(section, "Rationale weakness"),
    }


def _extract_artifact_design(text: str) -> dict[str, str]:
    section = _numbered_section(text, "15. Artifact & Design Principles")
    if not section:
        return {}
    return {
        "artifact_type": _field(section, "Artifact type"),
        "artifact_description": _field(section, "Artifact description"),
        "design_principles": _field(section, "Design principles"),
    }


def _extract_data_view(text: str) -> dict[str, str]:
    section = _numbered_section(text, "16. Data View & Evaluation Mode")
    if not section:
        return {}
    return {
        "data_view": _field(section, "Data view"),
        "evaluation_mode": _field(section, "Evaluation mode"),
        "validity_concern": _field(section, "Validity concern"),
    }


def _extract_contribution_type(text: str) -> dict[str, str]:
    section = _numbered_section(text, "17. Contribution Type")
    if not section:
        return {}
    return {
        "contribution_type": _field(section, "Contribution type"),
        "contribution_character": _field(section, "Contribution character"),
        "why_not_routine": _field(section, "Why not routine"),
    }


def _extract_boundary_conditions(text: str) -> dict[str, str]:
    section = _numbered_section(text, "18. Boundary Conditions")
    if not section:
        return {}
    return {
        "works_when": _field(section, "Works when"),
        "may_fail_when": _field(section, "May fail when"),
        "untested_boundary": _field(section, "Untested boundary"),
    }


def _extract_cross_paper_tension(text: str) -> dict[str, str]:
    section = _numbered_section(text, "19. Cross-Paper Tension")
    if not section:
        return {}
    return {
        "tension": _field(section, "Tension"),
        "competing_rationale": _field(section, "Competing rationale"),
        "idea_fuel": _field(section, "Idea fuel"),
    }


def _numbered_section(text: str, heading: str) -> str:
    section_match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
        text,
    )
    return section_match.group("section") if section_match else ""


def _normalize_ref_id(value: str) -> str:
    cleaned = value.strip().strip("[]")
    cleaned = cleaned.replace(":", "_").replace("/", "_")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", cleaned)
    return cleaned.strip("_") or "paper"


def _normalize_title_key(value: Any) -> str:
    return re.sub(r"\W+", " ", str(value or "").casefold()).strip()


def _read_comparison_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _build_method_families(
    notes: list[dict[str, Any]],
    abstract_notes: list[dict[str, Any]] | None = None,
    llm_insights: LLMInsights | None = None,
) -> list[dict[str, Any]]:
    all_notes = notes + (abstract_notes or [])
    # Build LLM classification lookup if available
    llm_classifications: dict[str, str] = {}
    if llm_insights and llm_insights.family_classifications:
        for fc in llm_insights.family_classifications:
            llm_classifications[fc.paper_id] = fc.family

    buckets: dict[str, list[dict[str, Any]]] = {}
    for note in all_notes:
        label = _classify_family(note, llm_override=llm_classifications.get(note["paper_id"]))
        buckets.setdefault(label, []).append(note)

    abstract_ids = {n["paper_id"] for n in (abstract_notes or [])}
    families = []
    for label, members in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:5]:
        full_members = [m for m in members if m["paper_id"] not in abstract_ids]
        abs_members = [m for m in members if m["paper_id"] in abstract_ids]
        families.append(
            {
                "name": label,
                "paper_ids": [note["paper_id"] for note in members[:8]],
                "representative_titles": [note["title"] for note in full_members[:4]],
                "core_observations": _top_snippets(full_members or members, "method_overview", limit=3),
                "result_observations": _top_snippets(full_members or members, "key_results", limit=3),
                "_abstract_count": len(abs_members),
            }
        )
    return families


def _classify_family(note: dict[str, Any], llm_override: str | None = None) -> str:
    # If the LLM agent has provided a classification, use it directly.
    if llm_override:
        return llm_override

    method_text = _shorten(note.get("method_overview") or note.get("title") or "unclassified", 56)
    return f"LLM_REVIEW_REQUIRED: {method_text}"


def _build_shared_assumptions(
    notes: list[dict[str, Any]],
    llm_insights: LLMInsights | None = None,
) -> list[dict[str, Any]]:
    # Use LLM-generated assumptions if available
    if llm_insights and llm_insights.shared_assumptions:
        return [
            {
                "assumption": sa.assumption,
                "why_questionable": sa.why_questionable,
                "supporting_papers": sa.supporting_papers or _cycle_refs(notes, 2),
            }
            for sa in llm_insights.shared_assumptions
        ]

    return _collect_llm_review_assumption_candidates(notes)


def _extract_assumptions_from_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compatible alias for review candidate collection.

    The function no longer maps keyword categories to domain assumptions.
    """
    return _collect_llm_review_assumption_candidates(notes)


def _collect_llm_review_assumption_candidates(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for note in notes:
        snippet = _shorten(note.get("limitations") or note.get("gaps") or note.get("questions"), 220)
        if not snippet:
            continue
        candidates.append(
            {
                "assumption": f"LLM_REVIEW_REQUIRED: derive any shared assumption from [{note['paper_id']}]",
                "why_questionable": snippet,
                "supporting_papers": [note["paper_id"]],
                "review_required": True,
            }
        )
        if len(candidates) >= 6:
            break
    return candidates


def _build_metric_landscape_hints(notes: list[dict[str, Any]], rows: list[dict[str, str]]) -> dict[str, Any]:
    hints: list[dict[str, Any]] = []
    row_by_id = {_normalize_ref_id(row.get("id", "")): row for row in rows if row.get("id")}
    for note in notes[:12]:
        row = row_by_id.get(note["paper_id"], {})
        metric = row.get("key_metric") or _first_metric_line(str(note.get("key_results") or ""))
        hints.append(
            {
                "paper_id": note["paper_id"],
                "title": note["title"],
                "metric": metric,
                "efficiency_signal": row.get("method_family") or _shorten(note.get("details", ""), 160),
            }
        )
    return {
        "semantics": "mechanical_metric_landscape_hint_not_opportunity_map",
        "warning": "Use only for factual metric context; T4 opportunity generation should use contribution_space and cross_paper_tensions.",
        "items": hints,
    }


def _build_contribution_space(
    notes: list[dict[str, Any]],
    abstract_notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build CDR contribution-space hints from note sections.

    This is mechanical organization only; the Reader LLM still decides the
    final contribution-space interpretation.
    """

    all_notes = notes + (abstract_notes or [])
    by_contribution: dict[str, list[str]] = {}
    by_artifact: dict[str, list[str]] = {}
    rationale_snippets: list[dict[str, str]] = []
    for note in all_notes:
        paper_id = note["paper_id"]
        contribution = str((note.get("contribution_type") or {}).get("contribution_type") or "unknown").strip().lower()
        artifact_type = str((note.get("artifact_design") or {}).get("artifact_type") or "unknown").strip().lower()
        by_contribution.setdefault(contribution or "unknown", []).append(paper_id)
        by_artifact.setdefault(artifact_type or "unknown", []).append(paper_id)
        rationale = (note.get("design_rationale") or {}).get("rationale", "")
        if rationale:
            rationale_snippets.append(
                {
                    "paper_id": paper_id,
                    "rationale": _shorten(rationale, 240),
                    "weakness": _shorten((note.get("design_rationale") or {}).get("rationale_weakness", ""), 180),
                    "contribution_type": contribution or "unknown",
                    "artifact_type": artifact_type or "unknown",
                }
            )

    return {
        "semantics": "mechanical_cdr_contribution_space_hints_not_final_synthesis",
        "by_contribution_type": {key: value[:10] for key, value in sorted(by_contribution.items())},
        "by_artifact_type": {key: value[:10] for key, value in sorted(by_artifact.items())},
        "design_rationale_snippets": rationale_snippets[:20],
        "review_tasks": [
            "Cluster papers by competing design rationale rather than by title keywords.",
            "Identify design-rationale gaps and underused problem framings.",
            "Do not treat provenance counts as contribution quality.",
        ],
    }


def _build_cross_paper_tensions(
    notes: list[dict[str, Any]],
    *,
    llm_insights: LLMInsights | None = None,
) -> list[dict[str, Any]]:
    if llm_insights and llm_insights.cross_paper_tensions:
        return [
            {
                "tension": item.tension,
                "competing_rationales": item.competing_rationales,
                "paper_ids": item.paper_ids,
                "idea_fuel": item.idea_fuel,
                "source": "llm_insight",
            }
            for item in llm_insights.cross_paper_tensions
        ]

    tensions: list[dict[str, Any]] = []
    for note in notes:
        cpt = note.get("cross_paper_tension") or {}
        tension = str(cpt.get("tension") or "").strip()
        if not tension:
            continue
        tensions.append(
            {
                "tension": _shorten(tension, 260),
                "competing_rationales": [_shorten(cpt.get("competing_rationale", ""), 220)],
                "paper_ids": [note["paper_id"]],
                "idea_fuel": _shorten(cpt.get("idea_fuel", ""), 220),
                "source": "paper_note_section_19",
                "requires_llm_synthesis": True,
            }
        )
    return tensions[:12]


def _build_citation_graph_context(domain_map: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(domain_map, dict) or not domain_map:
        return {
            "semantics": "citation_graph_context_unavailable",
            "citation_edges": [],
            "review_note": "No domain_map.json was available; Reader LLM should rely on paper notes and explicitly mention this limitation.",
        }
    return {
        "semantics": "mechanical_citation_graph_context_not_final_literature_structure",
        "domain_map_semantics": domain_map.get("semantics", ""),
        "citation_edges": domain_map.get("citation_edges", [])[:200],
        "core_ids": [item.get("id") for item in domain_map.get("core", []) if isinstance(item, dict)][:30],
        "adjacent_ids": [item.get("id") for item in domain_map.get("adjacent", []) if isinstance(item, dict)][:30],
        "boundary_ids": [item.get("id") for item in domain_map.get("boundary", []) if isinstance(item, dict)][:30],
        "warnings": domain_map.get("warnings", []),
    }


def _build_domain_map_bucket_summary(domain_map: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(domain_map, dict) or not domain_map:
        return {"core": 0, "adjacent": 0, "boundary": 0, "warnings": ["domain_map_missing"]}
    return {
        "semantics": "domain_map_bucket_counts_for_llm_review",
        "core": len(domain_map.get("core", []) or []),
        "adjacent": len(domain_map.get("adjacent", []) or []),
        "boundary": len(domain_map.get("boundary", []) or []),
        "edge_count": len(domain_map.get("citation_edges", []) or []),
        "warnings": domain_map.get("warnings", []),
    }


def _build_adjacent_transfers(
    domain_map: dict[str, Any],
    all_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Seed adjacent-transfer candidates without inventing domain knowledge."""

    if not isinstance(domain_map, dict):
        return []
    adjacent_nodes = [item for item in domain_map.get("adjacent", []) if isinstance(item, dict)]
    if not adjacent_nodes:
        return []

    note_by_id = {note.get("paper_id"): note for note in all_notes if note.get("paper_id")}
    title_to_note = {_normalize_title_key(note.get("title", "")): note for note in all_notes if note.get("title")}
    transfers: list[dict[str, Any]] = []
    for node in adjacent_nodes[:12]:
        node_id = str(node.get("id") or "").strip()
        note = note_by_id.get(node_id) or note_by_id.get(_normalize_ref_id(node_id))
        if note is None:
            note = title_to_note.get(_normalize_title_key(node.get("title", "")))
        mechanism = ""
        bridge = ""
        if note:
            mechanism = (
                str(note.get("core_approach_view") or "").strip()
                or str(note.get("method_overview") or "").strip()
                or str((note.get("mechanism_claim") or {}).get("stated_mechanism") or "").strip()
            )
            bridge = str(note.get("bridge_point") or "").strip()
        transfers.append(
            {
                "mechanism": _shorten(mechanism, 260) or "LLM_REVIEW_REQUIRED: infer possible transferable mechanism from adjacent paper metadata/note",
                "source_field": "adjacent_domain_or_theory_bridge",
                "source_papers": [node_id] if node_id else [],
                "bridges_to_core": node.get("bridges_to_core", []),
                "why_unused_in_target": "LLM_REVIEW_REQUIRED: compare this adjacent mechanism with core target-domain design rationales",
                "transfer_hypothesis_hint": _shorten(bridge or node.get("why_adjacent", ""), 260)
                or "LLM_REVIEW_REQUIRED: formulate a transfer hypothesis only after reading the note.",
                "evidence_level": str(note.get("evidence_level") if note else "metadata_or_domain_map_hint"),
                "semantics": "adjacent_transfer_seed_for_llm_review_not_claim",
            }
        )
    return transfers


def _build_trends(
    notes: list[dict[str, Any]],
    llm_insights: LLMInsights | None = None,
) -> list[dict[str, Any]]:
    # Use LLM-generated trends if available
    if llm_insights and llm_insights.trends:
        return [
            {
                "trend": t.trend,
                "recent_papers": t.recent_papers or _cycle_refs(notes, 5),
                "contrast_papers": t.contrast_papers or _cycle_refs(notes[3:] or notes, 3),
            }
            for t in llm_insights.trends
        ]

    # Fallback: expose chronological evidence only; LLM must infer trends.
    recent_start_year = recent_year_from(2)
    recent = [note for note in notes if (note.get("year") or 0) >= recent_start_year]
    older = [note for note in notes if note.get("year") and note.get("year") < recent_start_year]
    return [
        {
            "trend": "LLM_REVIEW_REQUIRED: infer trend from chronological evidence",
            "recent_papers": [note["paper_id"] for note in recent[:5]] or _cycle_refs(notes, 5),
            "contrast_papers": [note["paper_id"] for note in older[:3]],
            "review_required": True,
        },
    ]


def _extract_trends_from_notes(
    notes: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    older: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return chronological review hints, not domain-specific trend labels."""
    if not notes:
        return []
    return [
        {
            "trend": "LLM_REVIEW_REQUIRED: compare recent and older methods",
            "recent_papers": [n["paper_id"] for n in recent[:5]],
            "contrast_papers": [n["paper_id"] for n in older[:3]],
            "review_required": True,
        }
    ]


def _build_questions(
    notes: list[dict[str, Any]],
    missing_areas: str,
    llm_insights: LLMInsights | None = None,
) -> list[dict[str, Any]]:
    # Use LLM-generated questions if available
    if llm_insights and llm_insights.research_questions:
        return [
            {
                "id": rq.id,
                "question": rq.question,
                "why_unsolved": rq.why_unsolved or _shorten(missing_areas, 220) if missing_areas else "",
                "related_papers": rq.related_papers or _cycle_refs(notes, 3),
            }
            for rq in llm_insights.research_questions
        ]

    # Fallback: expose paper-authored questions/gaps only; LLM must formulate.
    gaps = [note for note in notes if str(note.get("gaps") or "").strip()]
    questions = _extract_questions_from_notes(notes, gaps, missing_areas)
    if questions:
        return questions
    refs = _cycle_refs(gaps or notes, 3)
    return [{
        "id": "Q_REVIEW",
        "question": "LLM_REVIEW_REQUIRED: formulate actionable research questions from notes and missing areas",
        "why_unsolved": _shorten(missing_areas, 220) if missing_areas else "No LLM-generated question was provided.",
        "related_papers": refs,
        "review_required": True,
    }]


def _extract_questions_from_notes(
    notes: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    missing_areas: str,
) -> list[dict[str, Any]]:
    """Extract paper-authored question snippets without inventing domain templates."""
    questions: list[dict[str, Any]] = []
    source_notes = gaps or notes
    for idx, note in enumerate(source_notes[:6], start=1):
        snippet = _shorten(note.get("questions") or note.get("gaps") or note.get("limitations"), 220)
        if not snippet:
            continue
        questions.append(
            {
                "id": f"Q_REVIEW_{idx}",
                "question": f"LLM_REVIEW_REQUIRED: turn note gap into a research question for [{note['paper_id']}]",
                "why_unsolved": snippet,
                "related_papers": [note["paper_id"]],
                "review_required": True,
            }
        )
    return questions[:5]


def _build_mechanism_claim_clusters(all_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate mechanism claim hints across papers for LLM review.

    The output deliberately avoids final consensus/challengeability judgment.
    Similarity clusters are mechanical hints only; the Reader/Ideation LLM must
    decide whether a claim is a real domain consensus and whether it is worth
    challenging.
    """
    claims: list[dict[str, Any]] = []
    for note in all_notes:
        mc = note.get("mechanism_claim") or {}
        stated = mc.get("stated_mechanism", "").strip()
        if not stated or "LLM_REVIEW_REQUIRED" in stated:
            continue
        evidence_type = mc.get("evidence_type", "unknown")
        evidence_level = note.get("evidence_level", "FULL_TEXT")
        claims.append({
            "paper_id": note["paper_id"],
            "title": note.get("title", ""),
            "mechanism": stated,
            "evidence_type": evidence_type,
            "evidence_level": evidence_level,
            "abstract_only": evidence_level == "ABSTRACT_ONLY",
        })

    if not claims:
        return []

    # Group claims by rough keyword similarity
    clusters: list[dict[str, Any]] = []
    for claim in claims:
        assigned = False
        for cluster in clusters:
            if _mechanism_similar(claim["mechanism"], cluster["representative_mechanism"]):
                cluster["papers"].append(claim)
                assigned = True
                break
        if not assigned:
            clusters.append({
                "representative_mechanism": claim["mechanism"],
                "papers": [claim],
            })

    consensus = []
    for cluster in clusters:
        papers = cluster["papers"]
        evidence_types = [p["evidence_type"] for p in papers]
        evidence_levels = [p["evidence_level"] for p in papers]
        weak_hint_count = sum(et in ("claimed_untested", "empirical_correlation", "abstract_claim_hint") for et in evidence_types)
        abstract_only_count = sum(1 for el in evidence_levels if el == "ABSTRACT_ONLY")

        consensus.append({
            "mechanism": cluster["representative_mechanism"],
            "paper_count": len(papers),
            "paper_ids": [p["paper_id"] for p in papers[:6]],
            "evidence_types": evidence_types,
            "evidence_strength_hint": "llm_review_required",
            "has_untested_claims": weak_hint_count > 0,
            "weak_evidence_hint_count": weak_hint_count,
            "abstract_only_count": abstract_only_count,
            "challengeable_hint": weak_hint_count > 0 or len(papers) == 1,
            "challengeable": weak_hint_count > 0 or len(papers) == 1,
            "requires_llm_judgment": True,
            "semantics": "mechanical_mechanism_claim_cluster_not_domain_consensus",
        })

    consensus.sort(key=lambda c: (not c["challengeable_hint"], -c["paper_count"]))
    return consensus[:10]


def _build_domain_consensus(all_notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compatible alias for mechanism-claim cluster hints."""

    return _build_mechanism_claim_clusters(all_notes)


def _mechanism_similar(m1: str, m2: str) -> bool:
    """Quick keyword overlap check for mechanism similarity."""
    words1 = set(re.findall(r"\w{3,}", m1.lower()))
    words2 = set(re.findall(r"\w{3,}", m2.lower()))
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2)
    return overlap >= max(2, min(len(words1), len(words2)) // 2)


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
    lines.extend(["", "## Contribution-Space Map"])
    contribution_space = workbench.get("contribution_space", {})
    for item in contribution_space.get("design_rationale_snippets", [])[:6]:
        lines.append(
            f"- [{item.get('paper_id')}] {item.get('contribution_type')} / "
            f"{item.get('artifact_type')}: {item.get('rationale')}"
        )
    tensions = workbench.get("cross_paper_tensions", [])
    if tensions:
        lines.extend(["", "## Cross-Paper Tensions"])
        for item in tensions[:6]:
            refs = ", ".join(_refs(item.get("paper_ids", [])))
            lines.append(f"- {item.get('tension', '')} ({refs})")
    adjacent_transfers = workbench.get("adjacent_transfers", [])
    lines.extend(["", "## Adjacent Transfers / 邻接领域可迁移机制"])
    if adjacent_transfers:
        for item in adjacent_transfers[:6]:
            refs = ", ".join(_refs(item.get("source_papers", [])))
            lines.append(f"- {item.get('mechanism', '')} ({refs}) -> {item.get('transfer_hypothesis_hint', '')}")
    else:
        lines.append("- No adjacent-transfer seed was detected; final synthesis should state whether this is a retrieval limitation.")
    mechanism_clusters = workbench.get("mechanism_claim_clusters") or workbench.get("domain_consensus", [])
    if mechanism_clusters:
        lines.extend(["", "## Mechanism Claim Clusters For LLM Review"])
        challengeable = [c for c in mechanism_clusters if c.get("challengeable_hint") or c.get("challengeable")]
        for item in challengeable[:5]:
            lines.append(f"- [review hint] {item['mechanism'][:100]} ({item['paper_count']} papers)")
    lines.extend(["", "## Research Questions"])
    for item in workbench["research_question_candidates"]:
        lines.append(f"- {item['id']}: {item['question']}")
    if missing_areas.strip():
        lines.extend(["", "## Missing Areas", _shorten(missing_areas, 1000)])
    return "\n".join(lines) + "\n"


def _render_draft_guidance(workbench: dict[str, Any]) -> str:
    """Render non-final writing guidance from the evidence workbench.

    This file intentionally avoids producing polished domain conclusions. It is
    a scaffold that tells the Reader LLM which evidence clusters exist and where
    human/LLM judgment is still required.
    """

    lines = [
        "# Synthesis Draft Guidance",
        "",
        "This is not a final literature synthesis. It is a structured writing aid produced from paper notes.",
        "The Reader LLM must inspect the workbench, verify classifications, add domain reasoning, and write `synthesis.md`.",
        "",
        "## Evidence Clusters To Review",
        "",
    ]
    for family in workbench.get("method_families", []):
        refs = ", ".join(_refs(family.get("paper_ids", [])[:6]))
        lines.extend(
            [
                f"### {family.get('name', 'Unclassified')}",
                f"- Candidate papers: {refs}",
                "- LLM review needed: confirm whether these papers really share a method family, split or merge if necessary.",
            ]
        )
        for obs in family.get("core_observations", [])[:3]:
            lines.append(f"- Evidence snippet: {obs}")
        lines.append("")

    lines.extend(["## Candidate Assumptions To Verify", ""])
    for item in workbench.get("shared_assumption_candidates", []):
        refs = ", ".join(_refs(item.get("supporting_papers", [])))
        lines.append(f"- {item.get('assumption', '')} | supporting papers: {refs}")
        if item.get("why_questionable"):
            lines.append(f"  Review question: {item['why_questionable']}")

    lines.extend(["", "## Candidate Research Questions To Refine", ""])
    for item in workbench.get("research_question_candidates", []):
        refs = ", ".join(_refs(item.get("related_papers", [])))
        lines.append(f"- {item.get('id', 'Q?')}: {item.get('question', '')} | related papers: {refs}")

    lines.extend(["", "## Contribution-Space And Tensions To Review", ""])
    contribution_space = workbench.get("contribution_space", {})
    for item in contribution_space.get("design_rationale_snippets", [])[:8]:
        lines.append(
            f"- [{item.get('paper_id')}] {item.get('contribution_type')} / "
            f"{item.get('artifact_type')}: {item.get('rationale')}"
        )
    for item in workbench.get("cross_paper_tensions", [])[:8]:
        refs = ", ".join(_refs(item.get("paper_ids", [])))
        lines.append(f"- Tension: {item.get('tension', '')} | papers: {refs}")

    lines.extend(["", "## Adjacent Transfers To Review", ""])
    for item in workbench.get("adjacent_transfers", [])[:8]:
        refs = ", ".join(_refs(item.get("source_papers", [])))
        lines.append(
            f"- Transfer seed: {item.get('mechanism', '')} | source papers: {refs} | "
            f"bridge: {item.get('transfer_hypothesis_hint', '')}"
        )
    if not workbench.get("adjacent_transfers"):
        lines.append("- No adjacent-transfer seed was detected; Reader LLM should explain coverage limits instead of inventing one.")

    lines.extend(
        [
            "",
            "## Required LLM Work",
            "",
            "- Re-read `synthesis_workbench.json` and the most important paper notes before writing final claims.",
            "- Treat all heuristic fields as hints, not conclusions.",
            "- Write `literature/synthesis.md` with explicit paper-ID evidence and no unsupported template prose.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _render_synthesis(workbench: dict[str, Any], missing_areas: str) -> str:
    guidance = _render_draft_guidance(workbench)
    if missing_areas.strip():
        guidance += "\n## Missing Areas Context For LLM Review\n\n"
        guidance += _shorten(missing_areas, 1400) + "\n"
    guidance += (
        "\n> This file is a scaffold only. Do not submit it as final synthesis. "
        "The Reader LLM must write the final `literature/synthesis.md`.\n"
    )
    return guidance


def _ref(paper_id: str) -> str:
    return f"[{_normalize_ref_id(paper_id)}]"


def _refs(paper_ids: list[str]) -> list[str]:
    return [_ref(paper_id) for paper_id in paper_ids if paper_id]
