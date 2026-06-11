from __future__ import annotations

"""Mechanical support tools for the optional T3.6 survey-paper branch.

These tools organize state, assemble section files, and audit coverage. They
intentionally do not decide taxonomy quality or write scholarly prose; the LLM
does that work section by section.
"""

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .bibtex import (
    bibtex_quality_issues,
    dedupe_bibtex_entries,
    extract_bib_keys_from_text,
    strip_internal_bibtex_notes,
)
from .manuscript import _extract_latex_cites, has_formal_citation
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


SURVEY_SECTION_SEQUENCE = [
    "background",
    "taxonomy",
    "theme_1",
    "theme_2",
    "theme_3",
    "theme_4",
    "comparison",
    "challenges",
    "future",
    "introduction",
    "conclusion",
    "abstract",
]

DEFAULT_MAX_THEME_SECTIONS = 0

SURVEY_BODY_ASSEMBLY_ORDER = [
    "introduction",
    "background",
    "taxonomy",
    "theme_1",
    "theme_2",
    "theme_3",
    "theme_4",
    "comparison",
    "challenges",
    "future",
    "conclusion",
]

SURVEY_SECTION_TITLES = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "background": "Background and Scope",
    "taxonomy": "Taxonomy",
    "theme_1": "Theme 1",
    "theme_2": "Theme 2",
    "theme_3": "Theme 3",
    "theme_4": "Theme 4",
    "comparison": "Comparative Analysis",
    "challenges": "Open Challenges",
    "future": "Future Directions",
    "conclusion": "Conclusion",
}

SURVEY_SECTION_TITLE_ALIASES = {
    "abstract": ("Abstract", "摘要"),
    "introduction": ("Introduction", "引言"),
    "background": (
        "Concepts, Scope, and Search Strategy",
        "Background and Scope",
        "Concepts and Scope",
        "Scope and Method",
        "Literature Search and Analysis Method",
        "概念界定、研究范围与检索方法",
        "概念界定与研究范围",
        "文献检索与分析方法",
    ),
    "taxonomy": (
        "Analytical Framework",
        "Taxonomy",
        "Theory and Analytical Framework",
        "分析框架",
        "理论基础与分析框架",
    ),
    "comparison": (
        "Research Progress and Comparative Evaluation",
        "Research Progress",
        "Comparative Analysis",
        "Comparative Review",
        "主要研究进展与比较评价",
        "研究评述与比较分析",
        "主要研究进展",
    ),
    "challenges": (
        "Critical Assessment and Open Challenges",
        "Open Challenges",
        "Critical Assessment",
        "现有研究不足与开放挑战",
        "现有研究评述",
        "开放挑战",
    ),
    "future": (
        "Future Research Agenda",
        "Future Directions",
        "未来研究方向",
        "未来研究议程",
        "研究展望",
    ),
    "conclusion": ("Conclusion", "结论"),
}

SURVEY_SECTION_FUNCTIONS = {
    "abstract": "State background, review object/problem, framework/findings, and contribution/future agenda.",
    "introduction": "Turn the topic into a clear review problem; explain importance, prior fragmentation, contribution, and roadmap.",
    "background": "Define concepts, scope, inclusion/exclusion boundaries, corpus/search strategy, and analysis method.",
    "taxonomy": "Build the explanatory knowledge structure: taxonomy, mechanism chain, map, or framework that reorganizes the literature.",
    "comparison": "Synthesize research streams through the framework; compare contributions, limitations, evidence boundaries, and relationships.",
    "challenges": "Critically assess unresolved tensions, missing mechanisms, evidence gaps, and mismatches exposed by the comparison.",
    "future": "Translate the critique into concrete, theory-bearing and actionable research agenda items.",
    "conclusion": "Return to the central problem; summarize the framework contribution, overall judgment, implications, and limits.",
}

SURVEY_SECTION_WRITING_CONTRACTS = {
    "abstract": {
        "purpose": "Give a compact, citation-free preview of the review problem, framework, findings, contribution, and future agenda.",
        "required_content": [
            "Research background and why a review is needed.",
            "The review object and central question.",
            "The organizing framework or taxonomy axis.",
            "Main synthesized findings or tensions.",
            "Review contribution and future agenda.",
        ],
        "internal_shape": [
            "One compact paragraph or two very short paragraphs.",
            "No headings, no formal citations, no detailed literature attribution.",
        ],
        "evidence_rules": [
            "Keep claims at survey-summary level; move detailed evidence to the main body.",
        ],
        "avoid": [
            "Do not write a table-of-contents abstract.",
            "Do not use LaTeX abstract wrappers; assemble_survey adds them.",
        ],
    },
    "introduction": {
        "purpose": "Turn the topic into a review problem and establish the paper's second-order contribution.",
        "required_content": [
            "Real-world or field-level motivation for the review.",
            "Why prior work is fragmented, incomplete, or hard to compare.",
            "The central review question.",
            "The paper's contribution as a framework, map, taxonomy, or problem reframing.",
            "A concise roadmap of the article.",
        ],
        "internal_shape": [
            "Problem importance -> literature fragmentation -> review question -> contribution -> article roadmap.",
            "Use representative citations sparingly; do not dump the literature list here.",
        ],
        "evidence_rules": [
            "Use citations as anchors for the field and fragmentation, not as a full review.",
        ],
        "avoid": [
            "Do not promise new experiments or original empirical findings.",
            "Do not start from a generic topic definition if the background section will define terms.",
        ],
    },
    "background": {
        "purpose": "Define the review object, boundaries, public evidence policy, and coverage limits without exposing runtime pipeline internals.",
        "required_content": [
            "Core concepts and terminology.",
            "Inclusion and exclusion boundaries.",
            "A short public-facing account of source types and screening logic when available.",
            "Evidence-level policy in reader-facing language: deeply read work supports claims; lightly read work only informs scope and trends.",
            "Coverage limits that readers must know before the framework section.",
        ],
        "internal_shape": [
            "Concepts -> scope boundaries -> public source strategy -> evidence rules -> coverage limits.",
        ],
        "evidence_rules": [
            "Do not use metadata-only records as claim evidence.",
            "Abstract-only material may signal coverage or emerging themes but must be labeled as weak.",
            "Do not report exact runtime pool counts, queue labels, metadata triage labels, or ResearchOS processing categories in reader-facing prose.",
        ],
        "avoid": [
            "Do not duplicate the taxonomy framework.",
            "Do not hide exclusions or weak evidence boundaries.",
            "Do not write internal process prose such as deduped candidate counts, FULL-TEXT/PARTIAL-TEXT/ABSTRACT-ONLY labels, metadata triage, backlog, or candidate pool accounting.",
        ],
    },
    "taxonomy": {
        "purpose": "Build the main explanatory knowledge structure that replaces a paper-by-paper literature list.",
        "required_content": [
            "The taxonomy/framework dimension and why it organizes the field.",
            "Every taxonomy class, stage, perspective, or mechanism family in survey_plan.taxonomy.tree, unless explicitly marked weak/deferred.",
            "For each class: definition, mechanism, inclusion boundary, representative evidence, adjacent relationship, and limitation.",
            "A short explanation of how the classes connect into an interpretable map.",
            "If compact mode skips theme sections, absorb the would-be theme chapter content here at framework level.",
        ],
        "internal_shape": [
            "Framework rationale -> class-by-class synthesis -> relationships among classes -> framework limitations.",
            "Use subsections or claim-led paragraphs for classes; do not make papers the unit of structure.",
        ],
        "evidence_rules": [
            "Each mature class should be grounded by verified notes/citations.",
            "Weak or metadata-only classes must be described as coverage gaps or resource-upgrade needs.",
        ],
        "avoid": [
            "Do not merely name categories.",
            "Do not offload default taxonomy classes into skipped theme slots.",
        ],
    },
    "comparison": {
        "purpose": "Synthesize research progress by comparing streams, classes, mechanisms, evidence boundaries, and tradeoffs.",
        "required_content": [
            "All major taxonomy classes or research streams introduced earlier.",
            "Comparison across assumptions, mechanisms, methods, datasets/settings, evidence strength, and practical constraints.",
            "Cross-stream tensions, complementarities, and boundary conditions.",
            "Evaluation of each stream's contribution and limitation.",
            "If compact mode skips theme sections, expand the substantive research-progress discussion here.",
        ],
        "internal_shape": [
            "Comparison dimensions -> stream/class comparison -> tensions and tradeoffs -> evaluative synthesis.",
            "Each paragraph should include a claim, representative evidence, comparison, and evaluation.",
        ],
        "evidence_rules": [
            "Do not compare incomparable settings without naming the boundary.",
            "Do not inflate abstract-only or metadata-only hints into settled progress.",
        ],
        "avoid": [
            "Do not write a sequence of author summaries.",
            "Do not repeat taxonomy definitions without evaluating research progress.",
        ],
    },
    "challenges": {
        "purpose": "Derive unresolved problems from the framework and comparison rather than listing generic limitations.",
        "required_content": [
            "Concrete tensions or gaps exposed by taxonomy/comparison.",
            "Why each challenge exists and what it prevents current research from explaining.",
            "Evidence or coverage boundary behind the challenge.",
            "Relation between the challenge and the central question.",
        ],
        "internal_shape": [
            "Challenge claim -> source in prior sections -> why it matters -> what would resolve it.",
        ],
        "evidence_rules": [
            "Resource-upgrade items can motivate a coverage challenge but cannot become evidence-backed conclusions.",
        ],
        "avoid": [
            "Do not write generic 'data/method/theory is insufficient' lists.",
            "Do not introduce new taxonomy classes here.",
        ],
    },
    "future": {
        "purpose": "Turn the critique into a concrete research agenda with mechanisms, settings, methods, or governance paths.",
        "required_content": [
            "Specific future questions derived from the central framework.",
            "Mechanisms, settings, methods, datasets, longitudinal designs, interventions, or governance paths to study.",
            "Near-term feasible directions versus longer-horizon agenda items.",
            "How each direction addresses a named limitation or tension.",
        ],
        "internal_shape": [
            "Agenda item -> unresolved tension -> possible research design/path -> expected theoretical contribution.",
        ],
        "evidence_rules": [
            "Do not introduce unsupported new literature claims.",
            "Connect each direction to evidence already established in earlier sections.",
        ],
        "avoid": [
            "Do not write only 'strengthen theory/empirics/interdisciplinary work'.",
            "Do not turn weak hints into mandatory future directions without caveats.",
        ],
    },
    "conclusion": {
        "purpose": "Close the review by answering the central question and restating the framework contribution and limits.",
        "required_content": [
            "Overall answer to the central review question.",
            "What the taxonomy/framework clarifies.",
            "Main comparative judgment and remaining uncertainty.",
            "Theoretical/practical implications when supported.",
            "Limitations of the review's corpus and evidence.",
        ],
        "internal_shape": [
            "Answer -> contribution -> implications -> limits -> closing future orientation.",
        ],
        "evidence_rules": [
            "Do not introduce new citations, evidence, taxonomy classes, or claims.",
        ],
        "avoid": [
            "Do not simply repeat the section list.",
            "Do not overclaim beyond the coverage audit.",
        ],
    },
}

SURVEY_QUALITY_DIMENSIONS = (
    "clear_problem",
    "scope_boundary",
    "organizing_framework",
    "comparison_and_evaluation",
    "theoretical_lift",
    "future_agenda",
    "real_citations",
)

OPTIONAL_SURVEY_SECTION_PREFIXES = ("theme_",)

SURVEY_SECTION_MIN_CITATIONS = {
    "introduction": 2,
    "background": 4,
    "taxonomy": 4,
    "comparison": 5,
    "challenges": 2,
    "future": 2,
}

_SURVEY_RUNTIME_PROCESS_RE = re.compile(
    r"(?i)"
    r"metadata\s+triage|candidate_count|FULL[_\-\s]?TEXT|PARTIAL[_\-\s]?TEXT|ABSTRACT[_\-\s]?ONLY|"
    r"FULL/PARTIAL\s+notes|metadata[_\-\s]?only|ResearchOS|backlog|候选池|保留候选|"
    r"初筛后共获得\s*\d+\s*篇|经过去重|去重与.{0,12}初筛|"
    r"全文阅读或深度部分阅读|精读笔记|精读文献|覆盖层|覆盖文献|摘要覆盖|"
    r"仅基于摘要信息|尚未获取全文的候选文献|未获取全文候选|"
    r"\d+\s*篇.{0,8}(?:精读|覆盖|候选)"
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z\-]{2,}\b")

_SURVEY_MIN_PLAIN_CHARS = {
    "abstract": {"en": 180, "zh": 500},
    "introduction": {"en": 1400, "zh": 2500},
    "background": {"en": 1300, "zh": 2200},
    "taxonomy": {"en": 1700, "zh": 3000},
    "comparison": {"en": 2600, "zh": 4500},
    "challenges": {"en": 1300, "zh": 2200},
    "future": {"en": 1600, "zh": 2800},
    "conclusion": {"en": 800, "zh": 1500},
}

_SURVEY_SECTION_QUALITY_PATTERNS = {
    "introduction": {
        "problem": r"central problem|review problem|research question|why this review|问题意识|核心问题|研究问题|为什么需要综述",
        "gap": r"gap|fragment|underexplored|insufficient|limitation|不足|割裂|分散|缺乏|尚未",
        "contribution": r"contribution|this survey|we propose|本文|本综述|贡献|框架|结构安排",
    },
    "background": {
        "scope": r"scope|boundary|include|exclude|inclusion|exclusion|范围|边界|纳入|排除",
        "definition": r"define|definition|concept|terminology|概念|界定|内涵|定义",
        "method": r"search|database|corpus|screen|analysis method|检索|数据库|筛选|文献来源|分析方法",
    },
    "taxonomy": {
        "framework": r"framework|taxonomy|classification|dimension|map|chain|机制链|分类|框架|维度|知识结构|风险链条",
        "mechanism": r"mechanism|pathway|source|consequence|governance|机制|来源|后果|治理|传导|嵌入",
        "boundary": r"boundary|distinguish|relationship|adjacent|边界|区别|关系|相邻|互补",
    },
    "comparison": {
        "stream": r"research stream|line of work|literature|现有研究|研究路径|文献|一类研究|另一类研究",
        "compare": r"compare|whereas|in contrast|tradeoff|difference|相比|然而|区别|权衡|比较",
        "evaluate": r"strength|limitation|contribution|evidence|boundary|贡献|局限|证据|评价|不足",
    },
    "challenges": {
        "critique": r"challenge|gap|limitation|tension|unresolved|不足|挑战|张力|断裂|脱节|尚未解决",
        "why": r"because|therefore|implies|resulting|原因|因此|导致|意味着|根源",
    },
    "future": {
        "agenda": r"future|agenda|research should|next step|direction|未来|研究方向|研究议程|后续研究",
        "specific": r"mechanism|design|measure|evaluate|longitudinal|scenario|governance|机制|设计|测量|评估|场景|治理|动态",
    },
    "conclusion": {
        "central_problem": r"central problem|this survey|overall|本文|本综述|总体|核心问题",
        "contribution": r"framework|taxonomy|contribution|implication|框架|分类|贡献|启示|意义",
    },
}


class BuildSurveyStateParams(BaseModel):
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    corpus_decision_path: str = Field(default="drafts/survey/corpus_decision.json")
    expansion_path: str = Field(default="drafts/survey/survey_expansion.json")
    metadata_triage_path: str = Field(default="literature/metadata_triage.md")
    state_output_path: str = Field(default="drafts/survey/survey_state.json")
    section_outline_dir: str = Field(default="drafts/survey/section_outlines")
    max_theme_sections: int = Field(default=DEFAULT_MAX_THEME_SECTIONS, ge=0, le=4)


class UpdateSurveySectionStateParams(BaseModel):
    section_id: str = Field(description="Survey section id, e.g. taxonomy, theme_1, comparison.")
    status: Literal["written", "revised", "skipped"] = Field(default="written")
    state_path: str = Field(default="drafts/survey/survey_state.json")
    section_path: str = Field(default="", description="Defaults to drafts/survey/sections/{section_id}.tex.")
    note: str = Field(default="", description="Optional short status note.")


class AssembleSurveyParams(BaseModel):
    state_path: str = Field(default="drafts/survey/survey_state.json")
    section_dir: str = Field(default="drafts/survey/sections")
    output_path: str = Field(default="drafts/survey/survey.tex")
    title: str = Field(default="", description="Optional title override.")
    related_work_bib_path: str = Field(default="literature/related_work.bib")


class AuditSurveyCoverageParams(BaseModel):
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    state_path: str = Field(default="drafts/survey/survey_state.json")
    survey_tex_path: str = Field(default="drafts/survey/survey.tex")
    related_work_bib_path: str = Field(default="literature/related_work.bib")
    output_json_path: str = Field(default="drafts/survey/survey_audit.json")
    output_md_path: str = Field(default="drafts/survey/survey_audit.md")


class ExportSurveyForIdeationParams(BaseModel):
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    survey_state_path: str = Field(default="drafts/survey/survey_state.json")
    survey_audit_path: str = Field(default="drafts/survey/survey_audit.json")
    survey_tex_path: str = Field(default="drafts/survey/survey.tex")
    insights_output_path: str = Field(default="ideation/survey_insights.json")
    summary_output_path: str = Field(default="drafts/survey/survey_summary.md")


class BindSurveyReviewParams(BaseModel):
    review_path: str = Field(default="drafts/survey/survey_review.md")
    actions_path: str = Field(default="drafts/survey/survey_review_actions.json")
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    state_path: str = Field(default="drafts/survey/survey_state.json")
    survey_tex_path: str = Field(default="drafts/survey/survey.tex")
    survey_audit_json_path: str = Field(default="drafts/survey/survey_audit.json")
    sections_dir: str = Field(default="drafts/survey/sections")
    synthesis_workbench_path: str = Field(default="literature/synthesis_workbench.json")
    domain_map_path: str = Field(default="literature/domain_map.json")
    comparison_table_path: str = Field(default="literature/comparison_table.csv")
    related_work_bib_path: str = Field(default="literature/related_work.bib")


class ExpandSurveyCorpusParams(BaseModel):
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    domain_map_path: str = Field(default="literature/domain_map.json")
    papers_verified_path: str = Field(default="literature/papers_verified.jsonl")
    output_path: str = Field(default="drafts/survey/survey_expansion.json")
    max_queries_per_class: int = Field(default=3, ge=1, le=8)


class BuildSurveyStateTool(Tool):
    name = "build_survey_state"
    description = (
        "Build drafts/survey/survey_state.json and per-section outline files from an LLM-authored "
        "survey_plan.json. This is mechanical organization, not taxonomy generation."
    )
    parameters_schema = BuildSurveyStateParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildSurveyStateParams(**kwargs)
        try:
            plan_path = self.policy.resolve_read(params.survey_plan_path)
            plan = _read_json(plan_path)
            corpus_decision = _read_optional_json(
                self.policy,
                params.corpus_decision_path,
            )
            expansion = _read_optional_json(self.policy, params.expansion_path)
            metadata_triage = _read_optional_text(self.policy, params.metadata_triage_path)
            state_path = self.policy.resolve_write(params.state_output_path)
            outline_dir = self.policy.resolve_write(params.section_outline_dir)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")

        outline = _coerce_outline(plan.get("outline"))
        planned_theme_limit = _survey_plan_theme_limit(plan)
        max_theme_sections = params.max_theme_sections
        if max_theme_sections == DEFAULT_MAX_THEME_SECTIONS and planned_theme_limit > 0:
            max_theme_sections = planned_theme_limit
        compact_mode = max_theme_sections == 0
        overflow_count = 0 if compact_mode else _theme_entry_overflow_count(outline, max_theme_sections=max_theme_sections)
        if overflow_count > 0:
            return ToolResult(
                ok=False,
                content=(
                    f"survey_plan outline contains {overflow_count + max_theme_sections} standalone theme sections, "
                    f"but current T3.6 sectioning policy supports {max_theme_sections}. "
                    "Merge taxonomy classes into the Taxonomy/Comparative Analysis sections or explicitly raise "
                    "max_theme_sections for a longer survey."
                ),
                error="too_many_theme_sections",
            )
        theme_entries = [] if compact_mode else _theme_entries(outline, max_theme_sections=max_theme_sections)
        theme_by_slot = {f"theme_{idx}": entry for idx, entry in enumerate(theme_entries, start=1)}
        writing_language = _infer_survey_writing_language(self.policy.workspace_dir, plan)
        taxonomy_classes = _taxonomy_classes(plan)
        theme_coverage_contract = _theme_coverage_contract(plan, taxonomy_classes, compact_mode=compact_mode)
        template_selection = plan.get("template_selection") if isinstance(plan.get("template_selection"), dict) else {}
        if not template_selection:
            template_selection = _read_workspace_json_optional(
                self.policy.workspace_dir / "drafts" / "survey" / "writing_template.json"
            )

        sections: dict[str, dict[str, Any]] = {}
        for section_id in SURVEY_SECTION_SEQUENCE:
            title = SURVEY_SECTION_TITLES[section_id]
            plan_entry = _matching_plan_entry(section_id, outline, theme_by_slot)
            if plan_entry:
                title = str(plan_entry.get("title") or title)
            skipped = section_id.startswith("theme_") and section_id not in theme_by_slot
            sections[section_id] = {
                "status": "skipped" if skipped else "pending",
                "file": f"drafts/survey/sections/{section_id}.tex",
                "outline_file": f"drafts/survey/section_outlines/{section_id}.md",
                "title": title,
                "reader_question": str(plan_entry.get("reader_question") or "") if isinstance(plan_entry, dict) else "",
                "section_argument": str(plan_entry.get("section_argument") or "") if isinstance(plan_entry, dict) else "",
                "covers": list(plan_entry.get("covers") or []) if isinstance(plan_entry, dict) else [],
                "paper_ids": list(plan_entry.get("paper_ids") or []) if isinstance(plan_entry, dict) else [],
                "plan_section_id": str(plan_entry.get("section_id") or section_id) if isinstance(plan_entry, dict) else section_id,
                "writing_contract": _section_writing_contract(section_id),
            }
            if compact_mode and section_id == "taxonomy":
                sections[section_id]["note"] = (
                    "Compact survey mode: write taxonomy classes as subsections/paragraphs here instead of "
                    "creating standalone theme chapters."
                )
                sections[section_id]["absorbs_theme_content"] = True
                sections[section_id]["must_cover_taxonomy_classes"] = [
                    item.get("class_id") or item.get("name") for item in taxonomy_classes if isinstance(item, dict)
                ]
            if compact_mode and section_id == "comparison":
                sections[section_id]["note"] = (
                    "Compact survey mode: compare the taxonomy classes here and reserve challenges/future for "
                    "cross-cutting issues."
                )
                sections[section_id]["absorbs_theme_content"] = True
                sections[section_id]["must_compare_taxonomy_classes"] = [
                    item.get("class_id") or item.get("name") for item in taxonomy_classes if isinstance(item, dict)
                ]

        state = {
            "semantics": "survey_state_for_taxonomy_driven_section_writing_not_final_claims",
            "survey_plan": params.survey_plan_path,
            "input_fingerprints": _input_fingerprints(
                self.policy.workspace_dir,
                {
                    "survey_plan": params.survey_plan_path,
                    "corpus_decision": params.corpus_decision_path,
                    "survey_expansion": params.expansion_path,
                    "metadata_triage": params.metadata_triage_path,
                },
            ),
            "corpus_scope": _corpus_scope(corpus_decision),
            "write_order": [sid for sid in SURVEY_SECTION_SEQUENCE if sections[sid]["status"] != "skipped"],
            "sections": sections,
            "shared_facts": {
                "sectioning_policy": (
                    "compact_survey_default_taxonomy_classes_inside_taxonomy_and_comparison"
                    if compact_mode
                    else "standalone_theme_sections_enabled"
                ),
                "writing_language": writing_language,
                "template_selection": template_selection,
                "central_question": str(plan.get("central_question") or plan.get("review_question") or ""),
                "review_contribution": str(plan.get("review_contribution") or ""),
                "quality_dimensions": list(SURVEY_QUALITY_DIMENSIONS),
                "max_theme_sections": max_theme_sections,
                "taxonomy_dimension": ((plan.get("taxonomy") or {}).get("dimension") if isinstance(plan.get("taxonomy"), dict) else ""),
                "taxonomy_classes": taxonomy_classes,
                "theme_coverage_contract": theme_coverage_contract,
                "evolution_narrative": str(plan.get("evolution_narrative") or ""),
                "scope_boundaries": plan.get("scope_boundaries") or {},
                "quality_plan": plan.get("quality_plan") or {},
                "coverage_selfcheck": plan.get("coverage_selfcheck") or {},
                "resource_upgrade_needs": _merge_resource_upgrade_needs(
                    _resource_upgrade_needs(plan),
                    _metadata_triage_upgrade_needs(metadata_triage),
                ),
                "metadata_triage_boundaries": _metadata_triage_boundaries(metadata_triage),
                "expansion_summary": expansion.get("summary", "") if isinstance(expansion, dict) else "",
            },
            "revision_log": [],
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        outline_dir.mkdir(parents=True, exist_ok=True)
        for section_id, entry in sections.items():
            outline_path = outline_dir / f"{section_id}.md"
            outline_path.write_text(_section_outline_text(section_id, entry, plan), encoding="utf-8")

        return ToolResult(
            ok=True,
            content=f"Built survey_state with {len(state['write_order'])} active sections.",
            data={
                "state_path": params.state_output_path,
                "active_sections": state["write_order"],
                "skipped_sections": [sid for sid, entry in sections.items() if entry["status"] == "skipped"],
            },
        )


class UpdateSurveySectionStateTool(Tool):
    name = "update_survey_section_state"
    description = "Mark one survey section as written/revised/skipped in survey_state.json."
    parameters_schema = UpdateSurveySectionStateParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UpdateSurveySectionStateParams(**kwargs)
        section_id = _normalize_section_id(params.section_id)
        try:
            state_path = self.policy.resolve_write(params.state_path)
            state = _read_json(state_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")
        sections = state.get("sections")
        if not isinstance(sections, dict) or section_id not in sections:
            return ToolResult(ok=False, content=f"Unknown survey section: {section_id}", error="unknown_section")
        if params.status == "skipped" and not section_id.startswith(OPTIONAL_SURVEY_SECTION_PREFIXES):
            return ToolResult(
                ok=False,
                content=f"Survey section {section_id} is mandatory and cannot be marked skipped.",
                error="mandatory_section_skipped",
            )

        section_path = params.section_path.strip() or f"drafts/survey/sections/{section_id}.tex"
        sections[section_id]["status"] = params.status
        sections[section_id]["file"] = section_path
        fingerprint_paths = {
            "section_outline": str(sections[section_id].get("outline_file") or f"drafts/survey/section_outlines/{section_id}.md"),
        }
        if params.status != "skipped":
            fingerprint_paths["section_file"] = section_path
        sections[section_id]["input_fingerprints"] = _input_fingerprints(
            self.policy.workspace_dir,
            fingerprint_paths,
        )
        if params.note.strip():
            sections[section_id]["note"] = params.note.strip()
        log = state.setdefault("revision_log", [])
        if isinstance(log, list):
            log.append({"section_id": section_id, "status": params.status, "note": params.note.strip()})
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ToolResult(ok=True, content=f"Updated survey section {section_id}: {params.status}", data={"section_id": section_id})


class AssembleSurveyTool(Tool):
    name = "assemble_survey"
    description = "Assemble section-level survey LaTeX files into drafts/survey/survey.tex."
    parameters_schema = AssembleSurveyParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AssembleSurveyParams(**kwargs)
        try:
            state = _read_json(self.policy.resolve_read(params.state_path))
            output_path = self.policy.resolve_write(params.output_path)
            section_dir = self.policy.resolve_read(params.section_dir)
            bib_path = self.policy.resolve_read(params.related_work_bib_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")
        if not section_dir.exists() or not section_dir.is_dir():
            return ToolResult(ok=False, content=f"Section dir missing: {params.section_dir}", error="missing_sections")
        if not bib_path.exists() or bib_path.stat().st_size <= 0:
            return ToolResult(
                ok=False,
                content=(
                    f"Missing bibliography for survey assembly: {params.related_work_bib_path}. "
                    "Run/repair T3 related_work.bib before assembling survey.tex."
                ),
                error="missing_bibliography",
            )
        if "@" not in bib_path.read_text(encoding="utf-8", errors="replace"):
            return ToolResult(
                ok=False,
                content=f"Survey bibliography has no BibTeX entries: {params.related_work_bib_path}",
                error="invalid_bibliography",
            )
        title = params.title.strip() or _infer_title(state)
        writing_language = _survey_state_writing_language(state, self.policy.workspace_dir)
        template_selection = _survey_template_selection(state)
        included: list[str] = []
        missing: list[str] = []
        body_sections: list[str] = []

        active_sections = _active_survey_sections(state)
        if "abstract" in active_sections:
            abstract_text, abstract_missing = _read_survey_section_text(
                self.policy,
                state,
                "abstract",
            )
            if abstract_missing:
                missing.append(abstract_missing)
            elif abstract_text.strip():
                abstract_body = _strip_survey_section_heading(abstract_text, "abstract").strip()
                included.append("abstract")
            else:
                missing.append("drafts/survey/sections/abstract.tex")
        else:
            abstract_body = ""

        body_order = [
            section_id for section_id in SURVEY_BODY_ASSEMBLY_ORDER if section_id in active_sections
        ]
        body_order.extend(
            section_id
            for section_id in active_sections
            if section_id not in body_order and section_id != "abstract"
        )
        for section_id in body_order:
            entry = (state.get("sections") or {}).get(section_id, {})
            if isinstance(entry, dict) and entry.get("status") == "skipped":
                continue
            text, missing_rel = _read_survey_section_text(self.policy, state, section_id)
            if missing_rel:
                missing.append(missing_rel)
                continue
            if not text:
                missing.append(f"drafts/survey/sections/{section_id}.tex")
                continue
            body_sections.append(_strip_generated_section_comments(text).strip())
            included.append(section_id)
        cited_keys = set()
        if abstract_body:
            cited_keys.update(_extract_latex_cites(abstract_body))
        for piece in body_sections:
            cited_keys.update(_extract_latex_cites(piece))
        bib_text = bib_path.read_text(encoding="utf-8", errors="replace")
        blocking_bib_issues = _blocking_bibtex_quality_issues(bib_text, cited_keys)
        if blocking_bib_issues:
            return ToolResult(
                ok=False,
                content=(
                    f"Survey bibliography quality check failed for {params.related_work_bib_path}: "
                    + "; ".join(blocking_bib_issues[:12])
                ),
                error="invalid_bibliography_quality",
            )

        repo_root = _repo_root()
        template_path = _resolve_latex_template(
            repo_root,
            template_selection.get("template_family", ""),
            template_selection.get("template_id", ""),
            writing_language,
        )
        tex = _render_survey_document(
            title=title,
            abstract=abstract_body,
            body_sections=body_sections,
            writing_language=writing_language,
            template_selection=template_selection,
            repo_root=repo_root,
        )
        output_path.write_text(tex, encoding="utf-8")
        _copy_latex_template_support_files(template_path, output_path.parent)
        _copy_bibliography_for_survey(self.policy, params.related_work_bib_path, output_path.parent / "references.bib")
        assembly_manifest = {
            "semantics": "survey_assembly_input_fingerprints",
            "input_fingerprints": _input_fingerprints(
                self.policy.workspace_dir,
                {
                    "survey_state": params.state_path,
                    "sections_dir": params.section_dir,
                    "related_work_bib": params.related_work_bib_path,
                    "survey_tex": params.output_path,
                    "references_bib": "drafts/survey/references.bib",
                    **{f"section_{sid}": str(((state.get("sections") or {}).get(sid) or {}).get("file") or "") for sid in included},
                },
            ),
            "included_sections": included,
            "writing_language": writing_language,
            "template_selection": template_selection,
        }
        (output_path.parent / "survey_assembly_manifest.json").write_text(
            json.dumps(assembly_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return ToolResult(
            ok=not missing,
            content=f"Assembled survey.tex with {len(included)} sections." + (f" Missing: {missing}" if missing else ""),
            data={"included_sections": included, "missing_sections": missing, "output_path": params.output_path},
            error="missing_sections" if missing else None,
        )


class AuditSurveyCoverageTool(Tool):
    name = "audit_survey_coverage"
    description = "Deterministically audit survey.tex for taxonomy section coverage, citations, placeholders, and missing sections."
    parameters_schema = AuditSurveyCoverageParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditSurveyCoverageParams(**kwargs)
        try:
            plan = _read_json(self.policy.resolve_read(params.survey_plan_path))
            state = _read_json(self.policy.resolve_read(params.state_path))
            tex_path = self.policy.resolve_read(params.survey_tex_path)
            tex = tex_path.read_text(encoding="utf-8", errors="replace")
            output_json = self.policy.resolve_write(params.output_json_path)
            output_md = self.policy.resolve_write(params.output_md_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")

        bib_keys = _bib_keys_optional(self.policy, params.related_work_bib_path)
        cited = _cited_keys(tex)
        writing_language = _survey_state_writing_language(state, self.policy.workspace_dir)
        section_texts = _survey_section_texts(tex, state)
        checks = []
        checks.append(_check("has_framework_section", "taxonomy" in section_texts, "Survey should include a taxonomy/framework section."))
        checks.append(_check("has_research_progress_section", "comparison" in section_texts, "Survey should include research-progress/comparative evaluation."))
        checks.append(_check("has_critical_assessment_section", "challenges" in section_texts, "Survey should include critical assessment/open challenges."))
        checks.append(_check("has_future_agenda_section", "future" in section_texts, "Survey should include a concrete future research agenda."))
        abstract_text = _extract_survey_abstract(tex)
        if abstract_text.strip():
            section_texts.setdefault("abstract", abstract_text)
        checks.append(_check("has_abstract_environment", bool(abstract_text.strip()), "Survey should place abstract text in a LaTeX abstract environment."))
        checks.append(
            _check(
                "abstract_no_formal_citation",
                not has_formal_citation(abstract_text),
                "Survey abstract must not contain LaTeX citations, author-year citations, or numeric citations.",
            )
        )
        abstract_section_heading = bool(re.search(r"\\section\*?\{\s*Abstract\s*\}", tex, flags=re.IGNORECASE))
        checks.append(
            _check(
                "no_abstract_section_heading",
                not abstract_section_heading,
                "Abstract should be in \\begin{abstract}...\\end{abstract}, not as a body section.",
            )
        )
        intro_pos = _survey_section_position(tex, "Introduction")
        trailing_body_positions = [
            pos
            for title in ("Background and Scope", "Taxonomy", "Comparative Analysis", "Open Challenges", "Future Directions", "Conclusion")
            for pos in [_survey_section_position(tex, title)]
            if pos >= 0
        ]
        checks.append(
            _check(
                "introduction_before_body_sections",
                intro_pos < 0 or not trailing_body_positions or intro_pos < min(trailing_body_positions),
                "Introduction should appear before the main body sections even though it is written late.",
            )
        )
        active_sections = [
            sid
            for sid, entry in (state.get("sections") or {}).items()
            if isinstance(entry, dict) and entry.get("status") != "skipped"
        ]
        missing_status = [
            sid
            for sid in active_sections
            if ((state.get("sections") or {}).get(sid) or {}).get("status") not in {"written", "revised"}
        ]
        checks.append(_check("all_active_sections_written", not missing_status, f"Unwritten sections: {missing_status}"))
        empty_classes = ((plan.get("coverage_selfcheck") or {}).get("empty_classes") if isinstance(plan.get("coverage_selfcheck"), dict) else []) or []
        checks.append(_check("empty_taxonomy_classes_declared", not empty_classes, f"Plan still reports empty classes: {empty_classes}", level_if_fail="WARN"))
        placeholder_hits = sorted(set(re.findall(r"\b(?:TODO|TBD|LLM_REVIEW_REQUIRED|PLACEHOLDER)\b", tex)))
        checks.append(_check("no_placeholder_tokens", not placeholder_hits, f"Placeholder tokens found: {placeholder_hits}"))
        internal_hits = _survey_internal_alignment_hits(tex)
        checks.append(
            _check(
                "no_internal_alignment_labels",
                not internal_hits,
                (
                    "Survey TeX should not expose internal ResearchOS labels such as C1/CID; "
                    f"hits={internal_hits[:8]}"
                    if internal_hits
                    else "No internal ResearchOS labels detected."
                ),
            )
        )
        missing_cites = sorted(cited - bib_keys) if bib_keys else []
        checks.append(_check("all_citations_in_bib", not missing_cites, f"Citation keys missing from bib: {missing_cites}"))
        min_unique_citations = _survey_min_unique_citations(state)
        checks.append(
            _check(
                "has_sufficient_citations",
                len(cited) >= min_unique_citations,
                f"Only {len(cited)} unique citation keys found; minimum={min_unique_citations}.",
            )
        )
        citation_issues = _survey_section_citation_issues(section_texts, state)
        checks.append(
            _check(
                "section_level_citation_density",
                not citation_issues,
                "Citation density issues: " + "; ".join(citation_issues[:8]),
            )
        )
        process_issues = _survey_runtime_process_issues(section_texts)
        checks.append(
            _check(
                "no_runtime_process_prose",
                not process_issues,
                "Runtime process prose found: " + "; ".join(process_issues[:8]),
            )
        )
        bib_quality_issues = _blocking_bibtex_quality_issues(
            self.policy.resolve_read(params.related_work_bib_path).read_text(encoding="utf-8", errors="replace")
            if bib_keys
            else "",
            cited,
        )
        checks.append(
            _check(
                "bibliography_quality",
                not bib_quality_issues,
                "Bibliography quality issues: " + "; ".join(bib_quality_issues[:12]),
            )
        )
        plan_issues = _survey_plan_quality_issues(plan)
        checks.append(
            _check(
                "survey_plan_quality",
                not plan_issues,
                "Plan quality issues: " + "; ".join(plan_issues[:8]),
            )
        )
        language_issues = _survey_language_issues(tex, state, writing_language)
        checks.append(
            _check(
                "survey_language_consistency",
                not language_issues,
                "Language consistency issues: " + "; ".join(language_issues[:8]),
            )
        )
        depth_issues = _survey_depth_issues(tex, state, writing_language)
        checks.append(
            _check(
                "survey_section_depth",
                not depth_issues,
                "Section depth issues: " + "; ".join(depth_issues[:8]),
            )
        )
        compact_theme_issues = _compact_theme_coverage_issues(state, section_texts)
        checks.append(
            _check(
                "compact_theme_content_absorbed",
                not compact_theme_issues,
                "Compact theme coverage issues: " + "; ".join(compact_theme_issues[:8]),
            )
        )

        passed = all(item["passed"] or item["level"] == "WARN" for item in checks)
        audit = {
            "semantics": "deterministic_survey_coverage_audit_not_scientific_judgment",
            "input_fingerprints": _input_fingerprints(
                self.policy.workspace_dir,
                {
                    "survey_plan": params.survey_plan_path,
                    "survey_state": params.state_path,
                    "survey_tex": params.survey_tex_path,
                    "related_work_bib": params.related_work_bib_path,
                    "survey_assembly_manifest": "drafts/survey/survey_assembly_manifest.json",
                },
            ),
            "passed": passed,
            "checks": checks,
            "stats": {
                "active_sections": active_sections,
                "unique_citations": sorted(cited),
                "bib_key_count": len(bib_keys),
                "latex_chars": len(tex),
                "writing_language": writing_language,
                "language_profile": _language_profile(tex),
                "theme_coverage_contract": (
                    (state.get("shared_facts") or {}).get("theme_coverage_contract")
                    if isinstance(state.get("shared_facts"), dict)
                    else {}
                ),
            },
        }
        output_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_md.write_text(_audit_markdown(audit), encoding="utf-8")
        return ToolResult(
            ok=passed,
            content=f"Survey audit {'passed' if passed else 'failed'} with {len(checks)} checks.",
            data=audit,
            error=None if passed else "survey_audit_failed",
        )


class ExportSurveyForIdeationTool(Tool):
    name = "export_survey_for_ideation"
    description = "Export taxonomy/challenge/future-direction survey signals as optional T4 ideation fuel."
    parameters_schema = ExportSurveyForIdeationParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ExportSurveyForIdeationParams(**kwargs)
        try:
            plan = _read_json(self.policy.resolve_read(params.survey_plan_path))
            state = _read_optional_json(self.policy, params.survey_state_path)
            audit = _read_optional_json(self.policy, params.survey_audit_path)
            tex = self.policy.resolve_read(params.survey_tex_path).read_text(encoding="utf-8", errors="replace")
            insights_path = self.policy.resolve_write(params.insights_output_path)
            summary_path = self.policy.resolve_write(params.summary_output_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")
        if audit.get("passed") is not True:
            return ToolResult(
                ok=False,
                content="survey_audit.json has not passed; do not export survey insights to T4.",
                error="survey_audit_not_passed",
            )

        insights = {
            "semantics": "survey_insights_optional_ideation_fuel_not_gate",
            "input_fingerprints": _input_fingerprints(
                self.policy.workspace_dir,
                {
                    "survey_plan": params.survey_plan_path,
                    "survey_state": params.survey_state_path,
                    "survey_audit": params.survey_audit_path,
                    "survey_tex": params.survey_tex_path,
                },
            ),
            "taxonomy": plan.get("taxonomy") or {},
            "evolution_narrative": plan.get("evolution_narrative") or "",
            "coverage_selfcheck": plan.get("coverage_selfcheck") or {},
            "resource_upgrade_needs": _merge_resource_upgrade_needs(
                _resource_upgrade_needs(plan),
                _resource_upgrade_needs(state.get("shared_facts") if isinstance(state.get("shared_facts"), dict) else state),
            ),
            "outline": plan.get("outline") or [],
            "challenge_hints": _extract_section_hints(tex, "challenge"),
            "future_direction_hints": _extract_section_hints(tex, "future"),
            "audit_summary": {
                "passed": audit.get("passed") if isinstance(audit, dict) else None,
                "warnings": [
                    item
                    for item in (audit.get("checks") or [])
                    if isinstance(item, dict) and item.get("level") == "WARN" and not item.get("passed")
                ] if isinstance(audit, dict) else [],
            },
        }
        insights_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = [
            "# Survey Summary for T4 Ideation",
            "",
            "This summary is optional idea-generation fuel, not a gate.",
            "",
            f"- Taxonomy dimension: {((plan.get('taxonomy') or {}).get('dimension') if isinstance(plan.get('taxonomy'), dict) else '')}",
            f"- Outline sections: {len(plan.get('outline') or [])}",
            f"- Resource upgrade needs: {len(insights['resource_upgrade_needs'])}",
            f"- Audit passed: {insights['audit_summary']['passed']}",
            "",
            "## Challenge Hints",
            *[f"- {item}" for item in insights["challenge_hints"][:8]],
            "",
            "## Future Direction Hints",
            *[f"- {item}" for item in insights["future_direction_hints"][:8]],
            "",
            "## Resource Upgrade Needs",
            *[
                "- {paper_or_topic}: {reason} -> {suggested_action}".format(
                    paper_or_topic=item.get("paper_or_topic") or item.get("topic") or "unknown",
                    reason=item.get("reason") or "unspecified",
                    suggested_action=item.get("suggested_action") or "acquire stronger evidence before use",
                )
                for item in insights["resource_upgrade_needs"][:8]
            ],
            "",
        ]
        summary_path.write_text("\n".join(summary), encoding="utf-8")
        return ToolResult(ok=True, content="Exported survey insights for T4.", data={"insights_output_path": params.insights_output_path})


class BindSurveyReviewTool(Tool):
    name = "bind_survey_review"
    description = (
        "Bind survey_review_actions.json to the current survey review inputs by adding input_fingerprints. "
        "Call after writing survey_review.md and survey_review_actions.json."
    )
    parameters_schema = BindSurveyReviewParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BindSurveyReviewParams(**kwargs)
        try:
            review_path = self.policy.resolve_read(params.review_path)
            actions_path = self.policy.resolve_write(params.actions_path)
            actions_read_path = self.policy.resolve_read(params.actions_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")
        if not review_path.exists() or review_path.stat().st_size <= 0:
            return ToolResult(ok=False, content=f"Missing review file: {params.review_path}", error="missing_review")
        try:
            actions = json.loads(actions_read_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return ToolResult(ok=False, content=f"survey_review_actions.json parse failed: {exc}", error="invalid_actions_json")
        if not isinstance(actions, dict):
            return ToolResult(ok=False, content="survey_review_actions.json top-level must be an object", error="invalid_actions_json")
        actions["input_fingerprints"] = _input_fingerprints(
            self.policy.workspace_dir,
            {
                "survey_review": params.review_path,
                "survey_plan": params.survey_plan_path,
                "survey_state": params.state_path,
                "survey_tex": params.survey_tex_path,
                "survey_audit_json": params.survey_audit_json_path,
                "sections_dir": params.sections_dir,
                "synthesis_workbench": params.synthesis_workbench_path,
                "domain_map": params.domain_map_path,
                "comparison_table": params.comparison_table_path,
                "related_work_bib": params.related_work_bib_path,
            },
        )
        actions_path.write_text(json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ToolResult(
            ok=True,
            content="Bound survey review actions to current input fingerprints.",
            data={"actions_path": params.actions_path},
        )


class ExpandSurveyCorpusTool(Tool):
    name = "expand_corpus_for_survey"
    description = (
        "Create a one-shot targeted corpus-expansion plan for empty/weak taxonomy classes. "
        "This does not run a T4->T2 loop and does not assert scholarly gaps."
    )
    parameters_schema = ExpandSurveyCorpusParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ExpandSurveyCorpusParams(**kwargs)
        try:
            plan = _read_json(self.policy.resolve_read(params.survey_plan_path))
            output = self.policy.resolve_write(params.output_path)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")
        domain_map = _read_optional_json(self.policy, params.domain_map_path)
        verified = _read_jsonl_optional(self.policy, params.papers_verified_path)
        weak_classes = _classes_needing_lit(plan)
        queries = []
        for cls in weak_classes:
            label = str(cls)
            adjacent_terms = _adjacent_titles(domain_map)[:3]
            verified_terms = [str(item.get("title") or "") for item in verified[:5] if isinstance(item, dict)]
            base_terms = [term for term in [label, *adjacent_terms, *verified_terms] if term]
            for query in _unique_queries(base_terms, max_count=params.max_queries_per_class):
                queries.append({"class_id": label, "query": query, "purpose": "survey_taxonomy_gap_check"})
        payload = {
            "semantics": "one_shot_survey_corpus_expansion_plan_not_ideation_loop",
            "summary": f"Generated {len(queries)} query hints for {len(weak_classes)} weak taxonomy classes.",
            "classes_needing_more_lit": weak_classes,
            "query_hints": queries,
            "note": "LLM should verify relevance before citing; this tool only organizes expansion hints.",
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ToolResult(ok=True, content=payload["summary"], data=payload)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _read_optional_json(policy: WorkspaceAccessPolicy, rel_path: str) -> dict[str, Any]:
    try:
        path = policy.resolve_read(rel_path)
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        return _read_json(path)
    except Exception:
        return {}


def _read_workspace_json_optional(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        return _read_json(path)
    except Exception:
        return {}


def _read_optional_text(policy: WorkspaceAccessPolicy, rel_path: str) -> str:
    try:
        path = policy.resolve_read(rel_path)
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _infer_survey_writing_language(workspace: Path, plan: dict[str, Any] | None = None) -> str:
    plan = plan or {}
    explicit = _normalize_survey_language(
        plan.get("writing_language")
        or plan.get("manuscript_language")
        or ((plan.get("style") or {}).get("language") if isinstance(plan.get("style"), dict) else "")
    )
    if explicit:
        return explicit
    project_path = workspace / "project.yaml"
    project: dict[str, Any] = {}
    if project_path.exists():
        try:
            loaded = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                project = loaded
        except Exception:
            project = {}
    for key in ("writing_language", "manuscript_language", "target_language", "language"):
        explicit = _normalize_survey_language(project.get(key))
        if explicit:
            return explicit
    constraints = project.get("constraints") if isinstance(project.get("constraints"), dict) else {}
    target_text = " ".join(
        [
            str(project.get("target_venue") or ""),
            " ".join(str(item) for item in project.get("target_venues") or []),
            str(constraints.get("target_venue") or ""),
            " ".join(str(item) for item in constraints.get("target_venues") or []),
        ]
    )
    if _target_text_prefers_zh(target_text):
        return "zh"
    if _target_text_prefers_en(target_text):
        return "en"
    profile_path = workspace / "user_seeds" / "seed_outline_profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            profile = {}
        if isinstance(profile, dict):
            explicit = _normalize_survey_language(profile.get("writing_language") or profile.get("target_language"))
            if explicit:
                return explicit
            lang = str(profile.get("language") or "").strip().lower()
            if lang == "zh":
                return "zh"
            if lang in {"en", "english"}:
                return "en"
    return "en"


def _target_text_prefers_zh(target_text: str) -> bool:
    if not target_text.strip():
        return False
    cjk_chars = len(_CJK_RE.findall(target_text))
    zh_markers = (
        "中文",
        "中国",
        "期刊",
        "学报",
        "核心",
        "北大核心",
        "南大核心",
        "cssci",
        "cscd",
        "ami",
        "wjci",
    )
    return cjk_chars >= 2 or any(marker in target_text.casefold() for marker in zh_markers)


def _target_text_prefers_en(target_text: str) -> bool:
    if not target_text.strip():
        return False
    lowered = target_text.casefold()
    en_markers = (
        "english",
        "journal",
        "conference",
        "transactions",
        "proceedings",
        "quarterly",
        "review",
        "science",
        "systems",
    )
    return any(marker in lowered for marker in en_markers)


def _normalize_survey_language(raw: object) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    if value in {"zh", "chinese", "中文"}:
        return "zh"
    if value in {"en", "english", "英文"}:
        return "en"
    return ""


def _survey_state_writing_language(state: dict[str, Any], workspace: Path) -> str:
    shared = state.get("shared_facts") if isinstance(state.get("shared_facts"), dict) else {}
    value = _normalize_survey_language(shared.get("writing_language") if isinstance(shared, dict) else "")
    return value or _infer_survey_writing_language(workspace)


def _language_profile(text: str) -> dict[str, Any]:
    plain = _plain_latex_text(text)
    cjk = len(_CJK_RE.findall(plain))
    latin_words = len(_LATIN_WORD_RE.findall(plain))
    return {
        "plain_chars": len(plain),
        "cjk_chars": cjk,
        "latin_words": latin_words,
    }


def _plain_latex_text(text: str) -> str:
    text = re.sub(r"\\(?:cite|citep|citet|ref|label|url|href)(?:\[[^\]]*\])*\{[^{}]*\}", " ", text or "")
    text = re.sub(r"\\(?:section|subsection|subsubsection)\*?\{([^{}]*)\}", r" \1 ", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[{}$^_~%&]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _survey_language_issues(tex: str, state: dict[str, Any], writing_language: str) -> list[str]:
    issues: list[str] = []
    sections = _survey_section_texts(tex, state)
    active = _active_survey_sections(state)
    for sid in active:
        text = sections.get(sid, "")
        if not text.strip() and sid == "abstract":
            text = _extract_survey_abstract(tex)
        if not text.strip():
            continue
        profile = _language_profile(text)
        if writing_language == "zh" and profile["latin_words"] > max(80, profile["cjk_chars"] * 0.35):
            issues.append(f"{sid} appears English-heavy for zh survey")
        if writing_language == "en" and profile["cjk_chars"] > max(40, profile["latin_words"] * 1.5):
            issues.append(f"{sid} appears Chinese-heavy for en survey")
    profiles = {sid: _language_profile(text) for sid, text in sections.items() if text.strip()}
    if writing_language == "zh":
        english_heavy = [sid for sid, profile in profiles.items() if profile["latin_words"] > max(80, profile["cjk_chars"] * 0.35)]
        if english_heavy:
            issues.append("English-heavy sections in zh survey: " + ", ".join(english_heavy[:8]))
    if writing_language == "en":
        chinese_heavy = [sid for sid, profile in profiles.items() if profile["cjk_chars"] > max(40, profile["latin_words"] * 1.5)]
        if chinese_heavy:
            issues.append("Chinese-heavy sections in en survey: " + ", ".join(chinese_heavy[:8]))
    return issues


def _survey_depth_issues(tex: str, state: dict[str, Any], writing_language: str) -> list[str]:
    issues: list[str] = []
    sections = _survey_section_texts(tex, state)
    abstract = _extract_survey_abstract(tex)
    if abstract.strip():
        sections["abstract"] = abstract
    for sid in _active_survey_sections(state):
        text = sections.get(sid, "")
        if not text.strip():
            issues.append(f"{sid} missing from survey.tex")
            continue
        profile = _language_profile(text)
        metric = profile["cjk_chars"] if writing_language == "zh" else profile["plain_chars"]
        min_chars = _SURVEY_MIN_PLAIN_CHARS.get(sid, {"en": 600, "zh": 800}).get(writing_language, 600)
        if metric < min_chars:
            issues.append(f"{sid} too short ({metric} < {min_chars})")
        structure_issues = _survey_section_quality_issues(sid, text)
        if structure_issues:
            issues.extend(f"{sid} {item}" for item in structure_issues[:3])
    return issues


def _compact_theme_coverage_issues(state: dict[str, Any], section_texts: dict[str, str]) -> list[str]:
    shared = state.get("shared_facts") if isinstance(state.get("shared_facts"), dict) else {}
    contract = shared.get("theme_coverage_contract") if isinstance(shared.get("theme_coverage_contract"), dict) else {}
    if contract.get("mode") != "compact_theme_slots_skipped_content_must_be_absorbed":
        return []
    class_refs = contract.get("taxonomy_classes") if isinstance(contract.get("taxonomy_classes"), list) else []
    if not class_refs:
        return []
    taxonomy_text = section_texts.get("taxonomy", "")
    comparison_text = section_texts.get("comparison", "")
    issues: list[str] = []
    for item in class_refs:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("class_id") or "").strip()
        if not label:
            continue
        if not _taxonomy_class_mentioned(taxonomy_text, item):
            issues.append(f"taxonomy does not cover compact theme/taxonomy class: {label}")
        if not _taxonomy_class_mentioned(comparison_text, item):
            issues.append(f"comparison does not compare compact theme/taxonomy class: {label}")
    return issues


def _taxonomy_class_mentioned(text: str, item: dict[str, Any]) -> bool:
    name = str(item.get("name") or item.get("label") or "").strip()
    class_id = str(item.get("class_id") or "").strip()
    plain = _plain_latex_text(text).casefold()
    if name and _label_mentioned(plain, name):
        return True
    # Prefer names over terse IDs. IDs are only accepted when no class name exists.
    if not name and class_id and len(class_id) >= 2:
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(class_id.casefold())}(?![A-Za-z0-9])", plain) is not None
    return False


def _label_mentioned(plain_text: str, label: str) -> bool:
    label_norm = _plain_latex_text(label).casefold()
    if not label_norm:
        return False
    if label_norm in plain_text:
        return True
    cjk_label = "".join(_CJK_RE.findall(label_norm))
    if cjk_label:
        cjk_text = "".join(_CJK_RE.findall(plain_text))
        if cjk_label in cjk_text:
            return True
        if len(cjk_label) >= 6 and cjk_label[:4] in cjk_text and cjk_label[-4:] in cjk_text:
            return True
    stopwords = {
        "class",
        "classes",
        "stream",
        "streams",
        "stage",
        "stages",
        "perspective",
        "perspectives",
        "mechanism",
        "mechanisms",
        "risk",
        "risks",
        "approach",
        "approaches",
        "method",
        "methods",
        "model",
        "models",
        "research",
        "studies",
    }
    tokens = [
        token
        for token in re.findall(r"\b[a-z][a-z0-9\-]{3,}\b", label_norm)
        if token not in stopwords
    ]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", plain_text))
    return hits >= len(tokens) if len(tokens) <= 2 else hits >= 2


def _survey_section_texts(tex: str, state: dict[str, Any] | None = None) -> dict[str, str]:
    matches = list(re.finditer(r"\\section\*?\{([^{}]+)\}", tex or "", flags=re.IGNORECASE))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        sid = _survey_section_id_for_heading(match.group(1), state)
        if not sid:
            continue
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(tex)
        sections[sid] = tex[match.end() : end]
    return sections


def _survey_section_id_for_heading(title: str, state: dict[str, Any] | None = None) -> str:
    normalized = _normalize_survey_heading(title)
    if not normalized:
        return ""
    if isinstance(state, dict):
        for section_id, entry in (state.get("sections") or {}).items():
            if not isinstance(entry, dict):
                continue
            state_title = str(entry.get("title") or "").strip()
            if state_title and _heading_matches(normalized, _normalize_survey_heading(state_title)):
                return str(section_id)
    for section_id, aliases in SURVEY_SECTION_TITLE_ALIASES.items():
        if section_id.startswith("theme_"):
            continue
        for alias in aliases:
            alias_norm = _normalize_survey_heading(alias)
            if not alias_norm:
                continue
            if _heading_matches(normalized, alias_norm):
                return section_id
    return ""


def _heading_matches(normalized: str, alias_norm: str) -> bool:
    return bool(
        normalized
        and alias_norm
        and (normalized == alias_norm or normalized.startswith(alias_norm + " ") or alias_norm.startswith(normalized + " "))
    )


def _normalize_survey_heading(value: str) -> str:
    text = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", value or "").strip()
    text = re.sub(r"[:：].*$", "", text).strip()
    text = re.sub(r"[\s_\-]+", " ", text.casefold())
    text = re.sub(r"[^\w\u4e00-\u9fff ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _survey_section_quality_issues(section_id: str, text: str) -> list[str]:
    if section_id == "abstract" or section_id.startswith("theme_"):
        return []
    plain = _plain_latex_text(text)
    issues: list[str] = []
    patterns = _SURVEY_SECTION_QUALITY_PATTERNS.get(section_id, {})
    missing = [
        label
        for label, pattern in patterns.items()
        if not re.search(pattern, plain, flags=re.IGNORECASE)
    ]
    if missing:
        issues.append("lacks survey-argument signals: " + ", ".join(missing))
    if section_id in {"comparison", "challenges", "future"}:
        if _looks_like_paper_by_paper_summary(plain):
            issues.append("looks like paper-by-paper summary rather than synthesis")
    if section_id in {"comparison", "challenges", "future"} and _generic_future_or_gap_text(plain):
        issues.append("contains generic gap/future wording without concrete agenda")
    return issues


def _survey_plan_quality_issues(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    central_question = str(plan.get("central_question") or plan.get("review_question") or "").strip()
    if len(central_question) < 20:
        issues.append("missing_or_weak_central_question")
    scope = plan.get("scope_boundaries")
    if not isinstance(scope, dict) or not (
        scope.get("included") or scope.get("include") or scope.get("excluded") or scope.get("exclude")
    ):
        issues.append("missing_scope_boundaries")
    contribution = str(plan.get("review_contribution") or plan.get("theoretical_contribution") or "").strip()
    quality_plan = plan.get("quality_plan") if isinstance(plan.get("quality_plan"), dict) else {}
    if len(contribution) < 20 and not quality_plan.get("theoretical_lift"):
        issues.append("missing_review_contribution_or_theoretical_lift")
    outline = plan.get("outline") if isinstance(plan.get("outline"), list) else []
    weak_sections: list[str] = []
    for item in outline:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("section_id") or "").strip()
        rationale = " ".join(
            str(item.get(key) or "")
            for key in ("section_argument", "reader_question", "function", "covers_rationale", "rationale")
        ).strip()
        if section_id and section_id in {"background", "taxonomy", "comparison", "challenges", "future"} and len(rationale) < 20:
            weak_sections.append(section_id)
    if weak_sections:
        issues.append("outline lacks section arguments: " + ", ".join(weak_sections[:6]))
    return issues


def _looks_like_paper_by_paper_summary(text: str) -> bool:
    sentences = re.split(r"(?<=[.!?。！？])\s+", text or "")
    authorish = sum(
        1
        for sentence in sentences
        if re.search(r"\bet al\.|提出|发现|认为|研究了|proposed|found|studied|argued", sentence, flags=re.IGNORECASE)
    )
    relation_signals = len(
        re.findall(
            r"compare|whereas|however|in contrast|tradeoff|relationship|limitation|boundary|"
            r"相比|然而|与此不同|关系|权衡|局限|边界|断裂|脱节",
            text or "",
            flags=re.IGNORECASE,
        )
    )
    return authorish >= 6 and relation_signals < 3


def _generic_future_or_gap_text(text: str) -> bool:
    generic_hits = len(
        re.findall(
            r"future research should strengthen|more research is needed|interdisciplinary research|"
            r"未来应加强理论研究|未来应加强实证研究|未来应加强交叉学科研究|需要进一步研究",
            text or "",
            flags=re.IGNORECASE,
        )
    )
    concrete_hits = len(
        re.findall(
            r"mechanism|scenario|measure|dataset|longitudinal|governance|audit|responsibility|"
            r"机制|场景|测量|数据|纵向|治理|审计|责任|评估|干预|组织",
            text or "",
            flags=re.IGNORECASE,
        )
    )
    return generic_hits >= 2 and concrete_hits < 4


def _read_jsonl_optional(policy: WorkspaceAccessPolicy, rel_path: str) -> list[dict[str, Any]]:
    try:
        path = policy.resolve_read(rel_path)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
        return records
    except Exception:
        return []


def _input_fingerprints(workspace: Path, paths: dict[str, str]) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for label, rel_path in paths.items():
        path = workspace / rel_path
        item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
        if path.exists() and path.is_file():
            item["sha256"] = _sha256_file(path)
            item["kind"] = "file"
        elif path.exists() and path.is_dir():
            item["kind"] = "dir"
            children = [child for child in path.rglob("*") if child.is_file()]
            item["file_count"] = len(children)
            item["sha256"] = _sha256_dir(path, children)
        fingerprints[label] = item
    return fingerprints


def _sha256_dir(root: Path, children: list[Path]) -> str:
    digest = hashlib.sha256()
    for child in sorted(children, key=lambda p: p.relative_to(root).as_posix()):
        rel = child.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(str(child.stat().st_size).encode("ascii"))
            digest.update(b"\0")
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_outline(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _theme_entries(outline: list[dict[str, Any]], *, max_theme_sections: int) -> list[dict[str, Any]]:
    if max_theme_sections <= 0:
        return []
    themes = [
        item
        for item in outline
        if str(item.get("section_id") or "").lower().startswith("theme")
        or "theme" in str(item.get("section_id") or "").lower()
    ]
    if themes:
        return themes[:max_theme_sections]
    taxonomy_entries = [
        item
        for item in outline
        if str(item.get("section_id") or "").lower() not in {
            "introduction",
            "intro",
            "background",
            "scope",
            "taxonomy",
            "comparison",
            "comparative_analysis",
            "challenges",
            "open_challenges",
            "future",
            "future_directions",
            "conclusion",
            "abstract",
        }
    ]
    return taxonomy_entries[:max_theme_sections]


def _theme_entry_overflow_count(outline: list[dict[str, Any]], *, max_theme_sections: int) -> int:
    if max_theme_sections <= 0:
        return 0
    themes = [
        item
        for item in outline
        if str(item.get("section_id") or "").lower().startswith("theme")
        or "theme" in str(item.get("section_id") or "").lower()
    ]
    if themes:
        return max(0, len(themes) - max_theme_sections)
    taxonomy_entries = [
        item
        for item in outline
        if str(item.get("section_id") or "").lower() not in {
            "introduction",
            "intro",
            "background",
            "scope",
            "taxonomy",
            "comparison",
            "comparative_analysis",
            "challenges",
            "open_challenges",
            "future",
            "future_directions",
            "conclusion",
            "abstract",
        }
    ]
    return max(0, len(taxonomy_entries) - max_theme_sections)


def _survey_plan_theme_limit(plan: dict[str, Any]) -> int:
    """Return explicit standalone theme-section allowance from a survey plan.

    Compact survey is the default. The LLM may only enable theme sections by
    writing an explicit sectioning_policy object; merely emitting theme_* in
    outline is treated as legacy/over-fragmented plan text and folded away by
    BuildSurveyStateTool.
    """

    raw = plan.get("sectioning_policy")
    if isinstance(raw, str):
        if raw.strip().lower() in {
            "standalone_theme_sections_enabled",
            "allow_theme_sections",
            "long_survey_with_theme_sections",
        }:
            return 1
        return 0
    if not isinstance(raw, dict):
        return 0
    mode = str(raw.get("mode") or raw.get("sectioning_policy") or "").strip().lower()
    if mode not in {"standalone_theme_sections", "allow_theme_sections", "long_survey_with_theme_sections"}:
        return 0
    try:
        limit = int(raw.get("max_theme_sections") or raw.get("theme_section_limit") or 1)
    except (TypeError, ValueError):
        limit = 1
    return max(0, min(limit, 4))


def _matching_plan_entry(
    section_id: str,
    outline: list[dict[str, Any]],
    theme_by_slot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if section_id in theme_by_slot:
        return theme_by_slot[section_id]
    aliases = {
        "background": {"background", "scope", "background_scope"},
        "taxonomy": {"taxonomy"},
        "comparison": {"comparison", "comparative_analysis"},
        "challenges": {"challenges", "open_challenges"},
        "future": {"future", "future_directions"},
        "introduction": {"introduction", "intro"},
        "conclusion": {"conclusion"},
        "abstract": {"abstract"},
    }.get(section_id, {section_id})
    for item in outline:
        raw = str(item.get("section_id") or "").strip().lower()
        if raw in aliases:
            return item
    return {}


def _taxonomy_classes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    taxonomy = plan.get("taxonomy")
    if not isinstance(taxonomy, dict):
        return []
    tree = taxonomy.get("tree")
    if not isinstance(tree, list):
        return []
    return [item for item in tree if isinstance(item, dict)]


def _theme_coverage_contract(
    plan: dict[str, Any],
    taxonomy_classes: list[dict[str, Any]],
    *,
    compact_mode: bool,
) -> dict[str, Any]:
    class_refs = []
    for item in taxonomy_classes:
        if not isinstance(item, dict):
            continue
        class_id = str(item.get("class_id") or "").strip()
        name = str(item.get("name") or item.get("label") or "").strip()
        if not (class_id or name):
            continue
        class_refs.append(
            {
                "class_id": class_id,
                "name": name,
                "required_in": ["taxonomy", "comparison"] if compact_mode else [],
                "paper_ids": [str(p) for p in item.get("paper_ids") or []],
            }
        )
    return {
        "mode": (
            "compact_theme_slots_skipped_content_must_be_absorbed"
            if compact_mode
            else "standalone_theme_sections_enabled"
        ),
        "reason": (
            "Default compact survey keeps taxonomy classes inside Taxonomy and Comparative Analysis to avoid fragmented theme chapters."
            if compact_mode
            else "Some taxonomy classes may be written as standalone theme sections because sectioning_policy explicitly enabled them."
        ),
        "taxonomy_classes": class_refs,
        "taxonomy_section_obligation": (
            "Define every mature taxonomy class/stage/perspective and its boundary."
            if compact_mode
            else "Define the overarching framework and explain how standalone theme sections fit."
        ),
        "comparison_section_obligation": (
            "Compare the same classes/streams by assumptions, evidence, limitations, settings, and relationships."
            if compact_mode
            else "Compare both framework-level classes and any standalone theme sections."
        ),
    }


def _section_writing_contract(section_id: str) -> dict[str, Any]:
    if section_id.startswith("theme_"):
        return {
            "purpose": "Optional standalone theme slot used only when sectioning_policy explicitly enables long-survey theme chapters.",
            "required_content": [
                "If skipped, write no prose and mark skipped.",
                "If enabled, explain why this theme cannot be integrated into Taxonomy or Comparative Analysis.",
                "Define the theme, evidence base, relation to the main framework, and limitations.",
            ],
            "internal_shape": [
                "Theme argument -> evidence synthesis -> relation to framework -> evaluative limitation.",
            ],
            "evidence_rules": [
                "Do not use theme sections to hide weak or metadata-only evidence.",
            ],
            "avoid": [
                "Do not create theme chapters by default.",
                "Do not duplicate taxonomy or comparison prose.",
            ],
        }
    return dict(SURVEY_SECTION_WRITING_CONTRACTS.get(section_id) or {})


def _resource_upgrade_needs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized weak-evidence upgrade needs from an LLM survey plan."""

    raw = plan.get("resource_upgrade_needs")
    if not isinstance(raw, list):
        return []
    needs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        paper_or_topic = str(item.get("paper_or_topic") or item.get("topic") or item.get("paper_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        suggested_action = str(item.get("suggested_action") or item.get("action") or "").strip()
        if not (paper_or_topic or reason or suggested_action):
            continue
        needs.append(
            {
                "paper_or_topic": paper_or_topic or "unspecified",
                "reason": reason or "weak_evidence",
                "suggested_action": suggested_action or "acquire abstract/PDF before using as evidence",
                "allowed_use": "resource_upgrade_hint_not_survey_or_idea_evidence",
            }
        )
    return needs


def _merge_resource_upgrade_needs(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("paper_or_topic") or "").casefold().strip(),
                str(item.get("reason") or "").casefold().strip(),
                str(item.get("suggested_action") or "").casefold().strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    return merged


def _metadata_triage_upgrade_needs(text: str) -> list[dict[str, Any]]:
    items = _metadata_triage_section_items(text, "Likely Useful To Upgrade")
    needs: list[dict[str, Any]] = []
    for item in items[:25]:
        needs.append(
            {
                "paper_or_topic": item,
                "reason": "metadata_triage_likely_useful_but_not_evidence",
                "suggested_action": "Acquire abstract/PDF and promote to abstract/deep note before citing or using as mechanism evidence.",
                "allowed_use": "resource_upgrade_hint_not_survey_or_idea_evidence",
                "source": "literature/metadata_triage.md",
            }
        )
    return needs


def _metadata_triage_boundaries(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    do_not_use = _metadata_triage_section_items(text, "Do Not Use As Evidence")
    low_evidence = _metadata_triage_section_items(text, "Low Evidence / Defer")
    count_match = re.search(r"candidate_count:\s*(\d+)", text)
    return {
        "source": "literature/metadata_triage.md",
        "allowed_use": "coverage_gap_and_resource_upgrade_only_not_claim_evidence",
        "candidate_count": int(count_match.group(1)) if count_match else None,
        "likely_useful_to_upgrade_count": len(_metadata_triage_section_items(text, "Likely Useful To Upgrade")),
        "low_evidence_defer_count": len(low_evidence),
        "do_not_use_as_evidence_count": len(do_not_use),
        "do_not_use_examples": do_not_use[:12],
        "low_evidence_examples": low_evidence[:12],
    }


def _metadata_triage_section_items(text: str, heading: str) -> list[str]:
    if not text.strip():
        return []
    pattern = rf"(?ims)^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text)
    if not match:
        return []
    items: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        normalized = re.sub(r"^\-\s*", "", stripped).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        if normalized:
            items.append(normalized[:280])
    return items


def _corpus_scope(decision: dict[str, Any]) -> str:
    scope = str(decision.get("scope") or decision.get("corpus_scope") or "").strip().lower()
    if scope in {"complete", "full", "expand"}:
        return "complete"
    if scope in {"conservative", "existing"}:
        return "conservative"
    return "unspecified"


def _section_outline_text(section_id: str, entry: dict[str, Any], plan: dict[str, Any]) -> str:
    title = entry.get("title") or SURVEY_SECTION_TITLES.get(section_id, section_id)
    covers = entry.get("covers") or []
    paper_ids = entry.get("paper_ids") or []
    sectioning_policy = plan.get("sectioning_policy") if isinstance(plan.get("sectioning_policy"), (dict, str)) else "compact"
    reader_question = _sanitize_runtime_process_instruction(str(entry.get("reader_question") or ""))
    section_argument = _sanitize_runtime_process_instruction(str(entry.get("section_argument") or entry.get("function") or ""))
    central_question = _sanitize_runtime_process_instruction(str(plan.get("central_question") or plan.get("review_question") or ""))
    contract = entry.get("writing_contract") if isinstance(entry.get("writing_contract"), dict) else _section_writing_contract(section_id)
    compact_contract = _section_compact_theme_contract(section_id, entry, plan)
    lines = [
        f"# {title}",
        "",
        f"- section_id: {section_id}",
        f"- plan_section_id: {entry.get('plan_section_id', section_id)}",
        f"- sectioning_policy: {json.dumps(sectioning_policy, ensure_ascii=False)}",
        f"- central_question: {central_question or 'LLM must preserve the review central question from survey_plan'}",
        f"- section_role: {SURVEY_SECTION_FUNCTIONS.get(section_id, 'Survey section role')}",
        f"- reader_question: {reader_question or 'LLM must recover the reader question from survey_plan and this section role.'}",
        f"- section_argument: {section_argument or 'LLM must write a section-level argument, not a topic label.'}",
        f"- covers: {', '.join(str(item) for item in covers) if covers else 'LLM should map taxonomy classes here'}",
        f"- paper_ids: {', '.join(str(item) for item in paper_ids) if paper_ids else 'LLM should select from notes/bib'}",
        "",
        "## Section Writing Contract",
        f"- purpose: {contract.get('purpose') or SURVEY_SECTION_FUNCTIONS.get(section_id, 'Write this survey section.')}",
        *_outline_contract_items("required_content", contract.get("required_content")),
        *_outline_contract_items("internal_shape", contract.get("internal_shape")),
        *_outline_contract_items("evidence_rules", contract.get("evidence_rules")),
        *_outline_contract_items("avoid", contract.get("avoid")),
        "",
        "## Citation Requirements",
        *_section_citation_requirement_lines(section_id),
        "",
        "## Survey Quality Standard",
        "- A survey is a second-order research contribution: it reorganizes literature around a question, not a list of papers.",
        "- Every section needs an internal argument. Use claim -> evidence -> comparison -> evaluation paragraphs.",
        "- State relationships among research streams: differences, complementarities, tensions, missing mechanisms, and boundary conditions.",
        "- Avoid encyclopedia headings and author-by-author summaries.",
        "",
        "## Writing Skill",
        *_section_writing_skill(section_id),
        "",
        "## Sectioning Guidance",
        _sectioning_guidance(section_id, plan),
        "",
        *_compact_theme_outline_block(compact_contract),
        "## Global Taxonomy Snapshot",
        json.dumps(plan.get("taxonomy") or {}, ensure_ascii=False, indent=2)[:3000],
        "",
    ]
    return "\n".join(lines)


def _sanitize_runtime_process_instruction(text: str) -> str:
    if not text:
        return ""
    if _SURVEY_RUNTIME_PROCESS_RE.search(text):
        return (
            "Use reader-facing scope, concept, evidence-boundary, and synthesis language; "
            "do not report internal corpus counts, reading-status labels, or metadata triage process details."
        )
    return text


def _outline_contract_items(label: str, raw_items: object) -> list[str]:
    items = [str(item).strip() for item in raw_items or [] if str(item).strip()] if isinstance(raw_items, list) else []
    if not items:
        return [f"- {label}: unspecified"]
    return [f"- {label}:"] + [f"  - {item}" for item in items]


def _section_citation_requirement_lines(section_id: str) -> list[str]:
    if section_id == "abstract":
        return [
            "- minimum_unique_citations: 0",
            "- rule: Abstract must not contain formal citations.",
        ]
    minimum = SURVEY_SECTION_MIN_CITATIONS.get(section_id, 0)
    if minimum <= 0:
        return [
            "- minimum_unique_citations: 0",
            "- rule: Do not introduce new evidence claims; cite only if needed for continuity.",
        ]
    return [
        f"- minimum_unique_citations: {minimum}",
        "- rule: Use exact keys from related_work.bib and distribute citations across claim-bearing paragraphs.",
        "- rule: Citation count is not a target by itself; every citation must anchor a concept, stream, comparison, challenge, or agenda item.",
        "- rule: Do not cite metadata-only or explicitly weak/do_not_cite records as mechanism evidence.",
    ]


def _section_compact_theme_contract(section_id: str, entry: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    compact_mode = _survey_plan_theme_limit(plan) == 0
    if not compact_mode and not entry.get("absorbs_theme_content"):
        return {}
    if section_id not in {"taxonomy", "comparison"} and not section_id.startswith("theme_"):
        return {}
    return _theme_coverage_contract(plan, _taxonomy_classes(plan), compact_mode=compact_mode)


def _compact_theme_outline_block(contract: dict[str, Any]) -> list[str]:
    if not contract:
        return []
    class_refs = contract.get("taxonomy_classes") if isinstance(contract.get("taxonomy_classes"), list) else []
    lines = [
        "## Compact Theme Coverage Contract",
        f"- mode: {contract.get('mode') or 'unspecified'}",
        f"- reason: {contract.get('reason') or 'unspecified'}",
        f"- taxonomy_section_obligation: {contract.get('taxonomy_section_obligation') or 'unspecified'}",
        f"- comparison_section_obligation: {contract.get('comparison_section_obligation') or 'unspecified'}",
    ]
    if class_refs:
        lines.append("- taxonomy_classes_to_cover:")
        for item in class_refs:
            if not isinstance(item, dict):
                continue
            label = str(item.get("name") or item.get("class_id") or "").strip()
            if not label:
                continue
            class_id = str(item.get("class_id") or "").strip()
            required_in = ", ".join(str(x) for x in item.get("required_in") or []) or "see sectioning policy"
            lines.append(f"  - {class_id + ': ' if class_id else ''}{label} (required_in: {required_in})")
    lines.extend(
        [
            "- audit_rule: in compact mode, every listed class must appear in both Taxonomy and Comparative Analysis; skipped theme slots do not remove content obligations.",
            "",
        ]
    )
    return lines


def _section_writing_skill(section_id: str) -> list[str]:
    common = [
        "- Write one coherent survey section only; do not write adjacent sections.",
        "- Use the plan's central question and framework as the organizing axis, not the synthesis.md design-rationale fuel structure.",
        "- Paragraphs should follow claim -> evidence -> comparison -> evaluation; do not list papers one by one.",
        "- Cite only exact keys from related_work.bib; do not invent or approximate citation keys.",
        "- Do not expose internal ResearchOS labels such as C1, [C1], CID, ResearchOS alignment, TODO/TBD/PLACEHOLDER, or LLM_REVIEW_REQUIRED.",
        "- Treat abstract-only or metadata-only material as coverage/resource-upgrade context, not as verified mechanism evidence.",
    ]
    specific: dict[str, list[str]] = {
        "abstract": [
            "- Write only the abstract body: no heading, no LaTeX abstract environment, and no citations.",
            "- Summarize background, review object/problem, framework/findings, and contribution/future agenda in one compact paragraph.",
            "- Avoid claims that require detailed evidence attribution; those belong in the main sections.",
        ],
        "introduction": [
            "- Start from the field problem, not a topic label: why this review is needed now, what existing work has not explained, and what question the paper answers.",
            "- State the review contribution as a framework/map/problem consciousness, not as 'we summarize many papers'.",
            "- Cite sparingly: use representative anchors for the field, not a long literature list.",
            "- Make the contribution of the survey explicit without promising experiments or original empirical results.",
        ],
        "background": [
            "- Define core concepts, inclusion/exclusion boundaries, source strategy, and public evidence rules without exposing runtime pipeline accounting.",
            "- Explain what is inside the review and what is deliberately outside it.",
            "- Do not duplicate the framework section; use background to set scope, terms, and evidence rules.",
            "- Separate established foundations from weak or emerging evidence.",
            "- Do not write exact internal candidate counts, queue labels, metadata triage labels, or FULL/PARTIAL/ABSTRACT-ONLY runtime tags in the paper body.",
        ],
        "taxonomy": [
            "- Carry the main explanatory framework here, using subsections or compact paragraphs for classes, stages, perspectives, or mechanisms.",
            "- For each class, explain the mechanism, inclusion boundary, representative evidence, and relation to adjacent classes or stages.",
            "- The framework should help readers understand relationships among studies, not merely name categories.",
            "- Avoid paper-by-paper summaries; papers support the class definition rather than becoming the structure.",
        ],
        "comparison": [
            "- Organize the main research progress around problem types or framework dimensions, not around individual papers.",
            "- Compare streams across assumptions, mechanisms, methods, evidence strength, settings, and practical constraints.",
            "- Surface tensions and tradeoffs that are not visible inside individual classes.",
            "- Each subsection should end with a short evaluation: contribution, limitation, and relation to the next stream.",
        ],
        "challenges": [
            "- Derive challenges from gaps, contradictions, weak evidence, and deployment boundaries identified earlier.",
            "- Keep challenges specific enough to guide research; avoid generic statements that could fit any field.",
            "- Explain why each challenge exists and what it prevents current research from explaining.",
            "- Mark metadata-only/resource-upgrade hints as unresolved coverage needs, not evidence-backed conclusions.",
        ],
        "future": [
            "- Turn the framework and critique into concrete research directions, study designs, benchmarks, mechanisms, or governance questions.",
            "- Distinguish near-term feasible work from speculative agenda items.",
            "- Avoid generic phrases such as 'strengthen theoretical research' unless followed by a specific question, mechanism, and empirical route.",
            "- Do not introduce new unsupported literature claims; reuse evidence already established in earlier sections.",
        ],
        "conclusion": [
            "- Answer the central question again: what the framework clarifies, what remains uncertain, and how future work should use the survey.",
            "- Do not introduce new evidence, new citations, or new taxonomy classes.",
            "- Keep the ending concise and intellectually honest about coverage limits.",
        ],
    }
    if section_id.startswith("theme_"):
        return [
            "- This is an optional standalone theme slot, not a default chapter.",
            "- If survey_state marks this section skipped, do not write prose; call update_survey_section_state with status='skipped'.",
            "- If explicitly enabled, write a focused theme chapter only when it cannot be integrated into Taxonomy or Comparative Analysis.",
            *common,
        ]
    return [*(specific.get(section_id) or ["- Write compact professional survey prose for this section's reader-facing function."]), *common]


def _sectioning_guidance(section_id: str, plan: dict[str, Any]) -> str:
    policy = plan.get("sectioning_policy") if isinstance(plan.get("sectioning_policy"), dict) else {}
    mode = str(policy.get("mode") or plan.get("sectioning_policy") or "compact").strip().lower()
    if section_id.startswith("theme_"):
        return (
            "This optional theme slot is skipped in compact mode. If survey_state marks it skipped, do not write prose; "
            "call update_survey_section_state(..., status='skipped') and finish."
        )
    if section_id == "taxonomy":
        return (
            "Carry the main taxonomy here. Use subsections or paragraphs for classes, risk-chain stages, perspectives, "
            "or mechanism families; do not offload them into separate theme chapters unless sectioning_policy explicitly enables them."
        )
    if section_id == "comparison":
        return (
            "Compare taxonomy classes across assumptions, mechanisms, evidence strength, data/evaluation settings, and limitations. "
            "Avoid paper-by-paper laundry lists."
        )
    if section_id in {"challenges", "future"}:
        return (
            "Write cross-cutting issues grounded in taxonomy and comparison. Keep directions concrete and evidence-aware; "
            "do not inflate weak or metadata-only hints into firm claims."
        )
    if mode.startswith("standalone"):
        return "Standalone theme sections are enabled, but this section should still remain compact and avoid duplicated theme prose."
    return "Use compact professional survey structure and avoid unnecessary section fragmentation."


def _normalize_section_id(raw: str) -> str:
    value = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "intro": "introduction",
        "background_scope": "background",
        "comparative_analysis": "comparison",
        "open_challenges": "challenges",
        "future_directions": "future",
    }
    return aliases.get(value, value)


def _infer_title(state: dict[str, Any]) -> str:
    dimension = ((state.get("shared_facts") or {}).get("taxonomy_dimension") or "").strip()
    if dimension:
        return f"A Taxonomy-Driven Survey of {dimension}"
    return "A Taxonomy-Driven Survey"


def _escape_latex_title(title: str) -> str:
    return title.replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")


def _copy_bibliography_for_survey(
    policy: WorkspaceAccessPolicy,
    rel_bib_path: str,
    target_path: Path,
) -> None:
    try:
        bib_path = policy.resolve_read(rel_bib_path)
    except Exception:
        return
    if not bib_path.exists():
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = strip_internal_bibtex_notes(bib_path.read_text(encoding="utf-8", errors="replace"))
    target_path.write_text(dedupe_bibtex_entries(cleaned), encoding="utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _copy_latex_template_support_files(template_path: Path | None, target_dir: Path) -> None:
    if not template_path or not template_path.exists():
        return
    for source in template_path.parent.iterdir():
        if source.suffix.lower() not in {".sty", ".cls", ".bst"}:
            continue
        target = target_dir / source.name
        try:
            target.write_bytes(source.read_bytes())
        except OSError:
            continue


_BIBTEX_BLOCKING_MARKERS = (
    "duplicate_key",
    "invalid_key",
    "missing_or_unknown_title",
    "missing_year",
    "missing_author_or_organization",
    "marked_irrelevant",
    "contains_unknown_placeholder",
    "placeholder_doi",
    "missing_booktitle",
    "likely_journal_record_as_inproceedings",
    "unbalanced_braces",
)


def _blocking_bibtex_quality_issues(bib_text: str, cited_keys: set[str]) -> list[str]:
    issues = bibtex_quality_issues(bib_text, require_author=True)
    if not issues:
        return []
    blocking: list[str] = []
    for item in issues:
        if not any(marker in item for marker in _BIBTEX_BLOCKING_MARKERS):
            continue
        if item == "unbalanced_braces" or item == "no_parseable_bibtex_entries":
            blocking.append(item)
            continue
        key = item.split(":", 1)[0].strip()
        if key in cited_keys or "duplicate_key" in item:
            blocking.append(item)
    return blocking


def _survey_template_selection(state: dict[str, Any]) -> dict[str, str]:
    shared = state.get("shared_facts") if isinstance(state.get("shared_facts"), dict) else {}
    selection = shared.get("template_selection") if isinstance(shared, dict) else {}
    if not isinstance(selection, dict):
        selection = {}
    family = str(selection.get("template_family") or "").strip().lower()
    template_id = str(selection.get("template_id") or "").strip().lower()
    language = str(selection.get("writing_language") or "").strip().lower()
    return {
        "template_family": family,
        "template_id": template_id,
        "writing_language": language,
    }


def _render_survey_document(
    *,
    title: str,
    abstract: str,
    body_sections: list[str],
    writing_language: str,
    template_selection: dict[str, str],
    repo_root: Path,
) -> str:
    family = str(template_selection.get("template_family") or "").strip().lower()
    template_id = str(template_selection.get("template_id") or "").strip().lower()
    template_path = _resolve_latex_template(repo_root, family, template_id, writing_language)
    body = _survey_document_body(title=title, abstract=abstract, body_sections=body_sections, bib_stem="references")
    if template_path and template_path.exists():
        template = template_path.read_text(encoding="utf-8", errors="replace")
        rendered = _replace_template_document_body(template, body, bib_stem="references")
    else:
        rendered = _fallback_survey_document(
            title=title,
            abstract=abstract,
            body_sections=body_sections,
            writing_language=writing_language,
            bib_stem="references",
        )
    return rendered


def _strip_generated_section_comments(text: str) -> str:
    return re.sub(
        r"(?m)\A(?:%\s*===.*?===\s*\n|%\s*Section:.*\n|\s*)+",
        "",
        text or "",
    )


def _fallback_survey_document(
    *,
    title: str,
    abstract: str,
    body_sections: list[str],
    writing_language: str,
    bib_stem: str,
) -> str:
    cjk_packages = [
        "\\usepackage{iftex}",
        "\\usepackage{newunicodechar}",
        "\\ifXeTeX",
        "  \\usepackage{fontspec}",
        "  \\usepackage{xeCJK}",
        "  \\IfFontExistsTF{Noto Serif CJK SC}{\\setCJKmainfont{Noto Serif CJK SC}[ItalicFont=Noto Serif CJK SC, ItalicFeatures={FakeSlant=0.2}]}{}",
        "  \\IfFontExistsTF{Noto Sans CJK SC}{\\setCJKsansfont{Noto Sans CJK SC}}{}",
        "  \\IfFontExistsTF{Noto Serif CJK SC}{\\setCJKmonofont{Noto Serif CJK SC}}{}",
        "\\fi",
        "\\newunicodechar{≠}{\\ensuremath{\\ne}}",
        "\\newunicodechar{≤}{\\ensuremath{\\le}}",
        "\\newunicodechar{≥}{\\ensuremath{\\ge}}",
        "\\newunicodechar{×}{\\ensuremath{\\times}}",
        "\\newunicodechar{→}{\\ensuremath{\\to}}",
        "\\newunicodechar{←}{\\ensuremath{\\leftarrow}}",
        "\\newunicodechar{–}{--}",
        "\\newunicodechar{—}{---}",
    ] if writing_language == "zh" else []
    pieces = [
        "\\documentclass[11pt]{article}",
        "\\usepackage[margin=1in]{geometry}",
        "\\usepackage{booktabs}",
        "\\usepackage{hyperref}",
        "\\usepackage{natbib}",
        *cjk_packages,
        "\\begin{document}",
        _survey_document_body(title=title, abstract=abstract, body_sections=body_sections, bib_stem=bib_stem),
        "\\end{document}",
        "",
    ]
    return "\n\n".join(pieces)


def _survey_document_body(*, title: str, abstract: str, body_sections: list[str], bib_stem: str) -> str:
    parts = [
        "\\title{" + _escape_latex_title(title) + "}",
        "\\author{}",
        "\\date{}",
        "\\maketitle",
        "\\begin{abstract}\n" + abstract.strip() + "\n\\end{abstract}",
        *[section.strip() for section in body_sections if section.strip()],
        "\\bibliographystyle{plainnat}",
        f"\\bibliography{{{bib_stem}}}",
    ]
    return "\n\n".join(parts)


def _replace_template_document_body(template: str, body: str, *, bib_stem: str) -> str:
    preamble, begin_cmd, rest = _split_template_at_begin_document(template)
    if not begin_cmd:
        return template.strip() + "\n\n" + body + "\n"
    end_match = re.search(r"\\end\{document\}", rest, flags=re.IGNORECASE)
    suffix = rest[end_match.end() :] if end_match else ""
    preamble = _remove_template_title_author(preamble)
    preamble, bib_style = _extract_template_bib_style(preamble, rest)
    body = _set_document_bibliography(
        body,
        bib_stem=bib_stem,
        bib_style=bib_style or "plainnat",
    )
    return preamble.rstrip() + "\n\n" + begin_cmd + "\n" + body.strip() + "\n\\end{document}" + suffix


def _split_template_at_begin_document(template: str) -> tuple[str, str, str]:
    match = re.search(r"\\begin\{document\}", template or "", flags=re.IGNORECASE)
    if not match:
        return template, "", ""
    return template[: match.start()], match.group(0), template[match.end() :]


def _remove_template_title_author(preamble: str) -> str:
    cleaned = re.sub(r"(?ms)^\\title\{.*?\}\s*", "", preamble)
    cleaned = re.sub(r"(?ms)^\\author\{.*?\}\s*", "", cleaned)
    cleaned = re.sub(r"(?m)^\\date\{.*?\}\s*", "", cleaned)
    return cleaned


def _extract_template_bib_style(preamble: str, body: str = "") -> tuple[str, str]:
    combined = (preamble or "") + "\n" + (body or "")
    match = re.search(r"\\bibliographystyle\{([^}]*)\}", combined)
    style = match.group(1).strip() if match else ""
    cleaned = re.sub(r"\\bibliographystyle\{[^}]*\}\s*", "", preamble or "")
    return cleaned, style


def _set_document_bibliography(body: str, *, bib_stem: str, bib_style: str) -> str:
    body = re.sub(r"\\bibliographystyle\{[^}]*\}\s*", "", body or "")
    body = re.sub(r"\\bibliography\{[^}]*\}", lambda _m: f"\\bibliography{{{bib_stem}}}", body)
    if "\\bibliography{" not in body:
        body = body.rstrip() + f"\n\n\\bibliography{{{bib_stem}}}\n"
    return re.sub(
        r"(\\bibliography\{[^}]*\})",
        lambda m: f"\\bibliographystyle{{{bib_style}}}\n" + m.group(1),
        body,
        count=1,
    )


def _resolve_latex_template(repo_root: Path, family: str, template_id: str, writing_language: str) -> Path | None:
    base = repo_root / "latex_templete"
    candidates: list[Path] = []
    if family == "basic_zh" or writing_language == "zh":
        candidates.append(base / "normal" / "basic_zh.tex")
    elif family == "basic_en":
        candidates.append(base / "normal" / "basic_en.tex")
    elif family == "utd":
        tid = template_id or "informs"
        if tid in {"informs", "mnsc", "isre", "isr", "ijds"}:
            candidates.append(base / "utd" / "informs" / "informs_fallback.tex")
        candidates.append(base / "utd" / "informs_basic.tex")
    elif family == "ccf":
        tid = template_id or "neurips"
        if tid == "neurips":
            candidates.append(base / "ccf-latex-templates" / "NeurIPS" / "neurips_2026.tex")
        elif tid == "kdd":
            candidates.extend((base / "ccf-latex-templates" / "SIGKDD").glob("*.tex"))
    if not candidates:
        candidates.append(base / "normal" / ("basic_zh.tex" if writing_language == "zh" else "basic_en.tex"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _active_survey_sections(state: dict[str, Any]) -> list[str]:
    sections = state.get("sections") if isinstance(state.get("sections"), dict) else {}
    if isinstance(state.get("write_order"), list):
        order = state.get("write_order") or []
    elif sections:
        order = list(sections.keys())
    else:
        order = SURVEY_SECTION_SEQUENCE
    active: list[str] = []
    for section_id in order:
        sid = str(section_id)
        entry = sections.get(sid) if isinstance(sections, dict) else {}
        if isinstance(entry, dict) and entry.get("status") == "skipped":
            continue
        if sid not in active:
            active.append(sid)
    return active


def _read_survey_section_text(
    policy: WorkspaceAccessPolicy,
    state: dict[str, Any],
    section_id: str,
) -> tuple[str, str]:
    entry = (state.get("sections") or {}).get(section_id, {})
    file_rel = str(entry.get("file") or f"drafts/survey/sections/{section_id}.tex") if isinstance(entry, dict) else f"drafts/survey/sections/{section_id}.tex"
    try:
        file_path = policy.resolve_read(file_rel)
    except ToolAccessDenied:
        return "", file_rel
    if not file_path.exists():
        return "", file_rel
    text = file_path.read_text(encoding="utf-8", errors="replace").strip()
    text = _strip_survey_document_wrappers(text)
    return text, ""


def _strip_survey_document_wrappers(text: str) -> str:
    cleaned = re.sub(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}", "", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"\\usepackage(?:\[[^\]]*\])?\{[^}]+\}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\\begin\{document\}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\\end\{document\}", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _strip_survey_section_heading(text: str, section_id: str) -> str:
    title = re.escape(SURVEY_SECTION_TITLES.get(section_id, section_id))
    aliases = [title]
    if section_id == "abstract":
        aliases.append("Abstract")
    pattern = r"^\s*\\section\*?\{\s*(?:" + "|".join(dict.fromkeys(aliases)) + r")\s*\}\s*"
    text = re.sub(pattern, "", text or "", count=1, flags=re.IGNORECASE).strip()
    if section_id == "abstract":
        match = re.fullmatch(
            r"\s*\\begin\{abstract\}(.*?)\\end\{abstract\}\s*",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match:
            text = match.group(1)
    return (text or "").strip()


def _bib_keys_optional(policy: WorkspaceAccessPolicy, rel_path: str) -> set[str]:
    try:
        path = policy.resolve_read(rel_path)
        if not path.exists():
            return set()
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    return set(extract_bib_keys_from_text(text))


def _cited_keys(text: str) -> set[str]:
    return _extract_latex_cites(text)


def _survey_min_unique_citations(state: dict[str, Any]) -> int:
    active = [
        sid
        for sid in _active_survey_sections(state)
        if sid not in {"abstract", "conclusion"} and not sid.startswith("theme_")
    ]
    if not active:
        return 0
    return max(6, min(14, sum(SURVEY_SECTION_MIN_CITATIONS.get(sid, 0) for sid in active) // 2))


def _survey_section_citation_issues(section_texts: dict[str, str], state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for section_id in _active_survey_sections(state):
        if section_id in {"abstract", "conclusion"} or section_id.startswith("theme_"):
            continue
        text = section_texts.get(section_id, "")
        if not text.strip():
            continue
        cited = _extract_latex_cites(text)
        minimum = SURVEY_SECTION_MIN_CITATIONS.get(section_id, 0)
        if len(cited) < minimum:
            issues.append(f"{section_id} has {len(cited)} unique citations; minimum={minimum}")
    return issues


def _survey_runtime_process_issues(section_texts: dict[str, str]) -> list[str]:
    issues: list[str] = []
    for section_id, text in section_texts.items():
        plain = _plain_latex_text(text)
        hits = sorted({match.group(0).strip() for match in _SURVEY_RUNTIME_PROCESS_RE.finditer(plain)})
        if hits:
            issues.append(f"{section_id}: " + ", ".join(hits[:6]))
    return issues


def _extract_survey_abstract(tex: str) -> str:
    match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex or "", flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _survey_section_position(tex: str, title: str) -> int:
    pattern = r"\\section\*?\{\s*" + re.escape(title) + r"\s*\}"
    match = re.search(pattern, tex or "", flags=re.IGNORECASE)
    return match.start() if match else -1


def _survey_internal_alignment_hits(text: str) -> list[str]:
    patterns = [
        r"%\s*\[[^\]]*\bC\d+\b[^\]]*\]",
        r"\[\s*C\d+(?:\s*,\s*C\d+)*\s*\]",
        r"\bC\d+\s*[:：]",
        r"\bC\d+\s*[\.)]",
        r"\bC\d+\s+(?:is|are|shows?|supports?|contribution|claim|gap|motivation|rationale|experiment|analysis)\b",
        r"\b(?:contribution|claim|gap|motivation|rationale|experiment|analysis)\s+C\d+\b",
        r"\bCID\s*(?:-|:|：)?\s*C?\d+\b",
        r"\binternal alignment (?:id|lane)\s*(?:-|:|：)?\s*C?\d+\b",
        r"\bResearchOS\s+(?:alignment|trace|CID)\b",
    ]
    hits: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            if value and value not in hits:
                hits.append(value[:120])
            if len(hits) >= 20:
                return hits
    return hits


def _check(name: str, passed: bool, detail: str, *, level_if_fail: str = "FAIL") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "level": "PASS" if passed else level_if_fail,
        "detail": detail,
    }


def _audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Survey Coverage Audit",
        "",
        f"- passed: {audit.get('passed')}",
        f"- active_sections: {', '.join(audit.get('stats', {}).get('active_sections', []))}",
        f"- unique_citations: {len(audit.get('stats', {}).get('unique_citations', []))}",
        "",
        "## Checks",
    ]
    for item in audit.get("checks") or []:
        marker = "PASS" if item.get("passed") else item.get("level", "FAIL")
        lines.append(f"- [{marker}] {item.get('name')}: {item.get('detail')}")
    lines.append("")
    return "\n".join(lines)


def _extract_section_hints(tex: str, keyword: str) -> list[str]:
    lowered = keyword.lower()
    lines = []
    for raw in tex.splitlines():
        line = re.sub(r"\s+", " ", raw.strip())
        if len(line) < 30:
            continue
        if lowered in line.lower() or (keyword == "challenge" and "open problem" in line.lower()):
            lines.append(line[:300])
    return lines[:12]


def _classes_needing_lit(plan: dict[str, Any]) -> list[str]:
    selfcheck = plan.get("coverage_selfcheck") if isinstance(plan.get("coverage_selfcheck"), dict) else {}
    classes = list(selfcheck.get("classes_needing_more_lit") or [])
    classes.extend(selfcheck.get("empty_classes") or [])
    if not classes:
        for item in _taxonomy_classes(plan):
            paper_ids = item.get("paper_ids") if isinstance(item, dict) else None
            if isinstance(paper_ids, list) and len(paper_ids) <= 1:
                classes.append(str(item.get("class_id") or item.get("name") or "unknown"))
    return list(dict.fromkeys(str(item) for item in classes if str(item).strip()))


def _adjacent_titles(domain_map: dict[str, Any]) -> list[str]:
    titles = []
    for item in domain_map.get("adjacent") or []:
        if isinstance(item, dict) and item.get("title"):
            titles.append(str(item["title"]))
    return titles


def _unique_queries(base_terms: list[str], *, max_count: int) -> list[str]:
    queries: list[str] = []
    for term in base_terms:
        cleaned = re.sub(r"\s+", " ", term).strip()
        if not cleaned:
            continue
        for query in (cleaned, f"{cleaned} survey", f"{cleaned} taxonomy"):
            if query not in queries:
                queries.append(query)
            if len(queries) >= max_count:
                return queries
    return queries
