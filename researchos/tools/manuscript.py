from __future__ import annotations

"""Manuscript writing support tools.

These tools do mechanical organization for T8. They do not write scientific
claims. The Writer LLM remains responsible for argumentation, section prose,
claim selection, and venue-aware framing.
"""

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..literature_citations import load_or_build_citation_map
from ..literature_identity import is_paper_note_file
from .base import Tool, ToolResult
from .bibtex import extract_bib_keys_from_text, strip_internal_bibtex_notes
from .citation_alignment import citation_alignment_issues, citation_support_text_by_key
from .manuscript_registries import (
    build_claim_ledger_seed,
    build_cdr_claim_ledger_seed,
    build_figure_registry_seed,
    validate_claim_ledger,
    validate_cdr_claim_ledger,
    validate_figure_registry,
)
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


CORE_SECTIONS = [
    "abstract",
    "introduction",
    "related_work",
    "methodology",
    "experiments",
    "analysis",
    "conclusion",
]

SECTION_WRITING_SEQUENCE = [
    "methodology",
    "experiments",
    "related_work",
    "analysis",
    "introduction",
    "conclusion",
    "abstract",
]

SECTION_TITLES = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related_work": "Related Work",
    "methodology": "Method",
    "experiments": "Experiments",
    "analysis": "Analysis",
    "conclusion": "Conclusion",
}

SECTION_ALIASES = {
    "intro": "introduction",
    "introduction": "introduction",
    "related": "related_work",
    "related-work": "related_work",
    "related_work": "related_work",
    "literature_review": "related_work",
    "literature-review": "related_work",
    "method": "methodology",
    "methods": "methodology",
    "methodology": "methodology",
    "approach": "methodology",
    "experiment": "experiments",
    "experiments": "experiments",
    "evaluation": "experiments",
    "results": "experiments",
    "discussion": "analysis",
    "analysis": "analysis",
    "limitation": "conclusion",
    "limitations": "conclusion",
    "conclusion": "conclusion",
    "abstract": "abstract",
}

SECTION_WRITING_CONTRACTS = {
    "abstract": {
        "purpose": "Compress the final paper into a citation-free problem, gap, approach, evidence, and contribution summary.",
        "required_content": [
            "Problem and why it matters.",
            "Specific gap addressed by the paper.",
            "Approach/artifact at a high level.",
            "Key result only if supported by result artifacts.",
            "Contribution type and evidence boundary.",
        ],
        "internal_shape": [
            "Problem -> gap -> approach -> key evidence -> contribution.",
            "One compact paragraph unless the target venue expects a structured abstract.",
        ],
        "evidence_rules": [
            "No formal citations.",
            "No number absent from result artifacts or the already written body.",
        ],
        "avoid": [
            "Do not introduce terms, claims, datasets, or results not present in the body.",
            "Do not include abstract wrappers or section headings.",
        ],
    },
    "introduction": {
        "purpose": "Establish the problem, gap, contribution, and evidence promise in a form the rest of the paper can fulfill.",
        "required_content": [
            "Focused problem motivation.",
            "Two to four concrete gaps tied to prior work or practice.",
            "The proposed idea/artifact and why it is different.",
            "Contribution bullets with evidence commitments.",
            "Result headline only when evidence supports it.",
        ],
        "internal_shape": [
            "Problem -> gap -> approach -> contributions -> evidence headline/roadmap.",
        ],
        "evidence_rules": [
            "Every cited gap must use a real BibTeX key.",
            "Every contribution must map conceptually to CDR/alignment rows without printing internal IDs.",
        ],
        "avoid": [
            "Do not write a broad literature essay before the problem.",
            "Do not promise experiments, baselines, or deployments that are missing.",
        ],
    },
    "related_work": {
        "purpose": "Position the paper against competing rationales and nearest prior work using citation-backed synthesis.",
        "required_content": [
            "Two to four prior-work streams organized by rationale, not by authors.",
            "Representative citations for each stream.",
            "Shared limitations or tensions in each stream.",
            "Precise positioning of this paper's contribution.",
        ],
        "internal_shape": [
            "Stream rationale -> representative evidence -> shared limitation/tension -> positioning.",
        ],
        "evidence_rules": [
            "Use only keys from related_work.bib.",
            "Do not use low-quality/do_not_cite materials for core positioning.",
        ],
        "avoid": [
            "Do not write X et al. paragraph chains.",
            "Do not reveal the method in full before Method.",
        ],
    },
    "methodology": {
        "purpose": "Explain what the artifact/method is, why it is designed that way, and how it operates.",
        "required_content": [
            "Method overview and inputs/outputs.",
            "Core components and responsibilities.",
            "Design rationale and rejected alternatives.",
            "Algorithm/procedure or implementation details when available.",
            "Scope assumptions and failure modes known before experiments.",
        ],
        "internal_shape": [
            "Overview -> components -> design choices -> procedure/implementation -> assumptions.",
        ],
        "evidence_rules": [
            "Do not use experimental outcomes as method justification.",
            "Tie design choices to hypotheses, idea scorecard, exp plan, or code/config artifacts.",
        ],
        "avoid": [
            "Do not list files or internal CIDs as the method structure.",
            "Do not mix evaluation setup into method unless it defines the artifact.",
        ],
    },
    "experiments": {
        "purpose": "Convert result artifacts into reproducible evidence that answers the paper's research questions.",
        "required_content": [
            "Research questions or evaluation objectives.",
            "Datasets/tasks/splits/baselines/metrics/seeds/compute when available.",
            "Main results with source-backed numbers.",
            "Ablation, robustness, failure, or sensitivity evidence when available.",
            "Evidence boundary if any protocol is dry-run, mock-only, or incomplete.",
        ],
        "internal_shape": [
            "RQ/objective -> setup -> main results -> ablation/analysis bridge -> evidence boundary.",
        ],
        "evidence_rules": [
            "Every number must appear in result_metrics, evidence pack, ablations, or run artifacts.",
            "Do not upgrade mock/dry-run evidence into real empirical claims.",
        ],
        "avoid": [
            "Do not start with a dataset inventory without explaining the evaluation objective.",
            "Do not include tables or figures that cannot be generated from artifacts.",
        ],
    },
    "analysis": {
        "purpose": "Interpret whether the evidence supports the design rationale and rule out or weaken alternatives.",
        "required_content": [
            "Mechanism interpretation tied to method and experiments.",
            "Alternative explanations and what evidence does or does not rule out.",
            "Failure cases, sensitivity, or boundary conditions.",
            "Implications for the contribution claim.",
        ],
        "internal_shape": [
            "Design rationale -> evidence interpretation -> alternative explanation -> boundary/implication.",
        ],
        "evidence_rules": [
            "Use only completed evidence artifacts or clearly state limits.",
            "Do not restate result tables without interpretation.",
        ],
        "avoid": [
            "Do not introduce new method details or new result numbers.",
            "Do not treat speculation as analysis.",
        ],
    },
    "conclusion": {
        "purpose": "Close the paper by restating what was learned, what remains limited, and what follows.",
        "required_content": [
            "Concise answer to the problem framed in Introduction.",
            "Main contribution and transferable design knowledge.",
            "Limitations subsection covering evidence and validity boundaries.",
            "Future work that follows from limitations.",
        ],
        "internal_shape": [
            "Answer -> contribution -> limitations -> future work.",
        ],
        "evidence_rules": [
            "No new citations, numbers, datasets, baselines, or claims.",
            "Limitations must reflect actual evidence pack/result boundaries.",
        ],
        "avoid": [
            "Do not copy the abstract.",
            "Do not add new promises or unsupported impact claims.",
        ],
    },
}

MANUSCRIPT_SECTION_MIN_CITATIONS = {
    "introduction": 2,
    "related_work": 6,
    "methodology": 1,
    "experiments": 0,
    "analysis": 1,
    "conclusion": 0,
    "abstract": 0,
}

_LATEX_CITATION_COMMAND_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|supercite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{[^}]+\}",
    flags=re.IGNORECASE,
)
_AUTHOR_YEAR_CITATION_RE = re.compile(
    r"(?:"
    r"\b[A-Z][A-Za-z][A-Za-z'\-]+"
    r"(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z][A-Za-z'\-]+|&\s+[A-Z][A-Za-z][A-Za-z'\-]+))?"
    r"\s*\((?:19|20)\d{2}\)"
    r"|"
    r"\b[A-Z][A-Za-z][A-Za-z'\-]+"
    r"(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z][A-Za-z'\-]+|&\s+[A-Z][A-Za-z][A-Za-z'\-]+))?"
    r"\s*,\s*(?:19|20)\d{2}"
    r"|"
    r"\([A-Z][A-Za-z][A-Za-z'\-]+"
    r"(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z][A-Za-z'\-]+|&\s+[A-Z][A-Za-z][A-Za-z'\-]+))?"
    r"\s*,\s*(?:19|20)\d{2}(?:\s*;\s*[A-Z][A-Za-z][A-Za-z'\-]+"
    r"(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z][A-Za-z'\-]+|&\s+[A-Z][A-Za-z][A-Za-z'\-]+))?"
    r"\s*,\s*(?:19|20)\d{2})*\)"
    r")"
)
_NUMERIC_CITATION_RE = re.compile(r"\[(?:\d{1,3})(?:\s*[,;-]\s*\d{1,3})*\]")


def has_latex_citation_command(text: str) -> bool:
    """Return True when text contains an explicit LaTeX citation command."""

    return bool(_LATEX_CITATION_COMMAND_RE.search(text or ""))


def has_formal_citation(text: str) -> bool:
    """Return True for citation formats that should not appear in an Abstract."""

    text = text or ""
    return bool(
        _LATEX_CITATION_COMMAND_RE.search(text)
        or _AUTHOR_YEAR_CITATION_RE.search(text)
        or _NUMERIC_CITATION_RE.search(text)
    )


class BuildManuscriptResourceIndexParams(BaseModel):
    output_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Relative path for the manuscript resource index JSON.",
    )
    include_previews: bool = Field(
        default=True,
        description="Whether to include short text previews for major source artifacts.",
    )


class PlanManuscriptSectionsParams(BaseModel):
    resource_index_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Resource index generated by build_manuscript_resource_index.",
    )
    output_path: str = Field(
        default="drafts/section_plan.json",
        description="Relative path for the section plan JSON.",
    )
    target_venue: str = Field(default="", description="Target venue or journal/conference name.")
    paper_type: Literal["empirical", "systems", "theory", "survey", "auto"] = Field(
        default="auto",
        description="Paper type hint; auto uses project/results artifacts.",
    )


class PlanManuscriptEvidenceParams(BaseModel):
    resource_index_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Resource index generated by build_manuscript_resource_index.",
    )
    evidence_output_path: str = Field(
        default="drafts/evidence_plan.json",
        description="Relative path for the evidence and claim-slot plan.",
    )
    figure_output_path: str = Field(
        default="drafts/figure_table_plan.json",
        description="Relative path for the figure/table plan.",
    )
    target_venue: str = Field(default="", description="Target venue or journal/conference name.")


class BuildManuscriptRegistriesParams(BaseModel):
    resource_index_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Resource index generated by build_manuscript_resource_index.",
    )
    evidence_plan_path: str = Field(
        default="drafts/evidence_plan.json",
        description="Evidence plan generated by plan_manuscript_evidence.",
    )
    figure_table_plan_path: str = Field(
        default="drafts/figure_table_plan.json",
        description="Figure/table plan generated by plan_manuscript_evidence.",
    )
    cdr_output_path: str = Field(
        default="drafts/cdr_claim_ledger.json",
        description="Relative path for the CDR claim ledger seed.",
    )
    claim_output_path: str = Field(
        default="drafts/claim_ledger.json",
        description="Relative path for the generic claim ledger seed.",
    )
    figure_output_path: str = Field(
        default="drafts/figure_registry.json",
        description="Relative path for the figure/table registry seed.",
    )


class BuildAlignmentMatrixParams(BaseModel):
    cdr_claim_ledger_path: str = Field(
        default="drafts/cdr_claim_ledger.json",
        description="CDR claim ledger seed generated by build_manuscript_registries.",
    )
    evidence_plan_path: str = Field(
        default="drafts/evidence_plan.json",
        description="Evidence plan generated by plan_manuscript_evidence.",
    )
    figure_table_plan_path: str = Field(
        default="drafts/figure_table_plan.json",
        description="Figure/table plan generated by plan_manuscript_evidence.",
    )
    synthesis_path: str = Field(
        default="literature/synthesis.md",
        description="Literature synthesis used only for mechanical anchors and previews.",
    )
    hypotheses_path: str = Field(
        default="ideation/hypotheses.md",
        description="Hypotheses artifact used to seed H1/H2 links.",
    )
    idea_scorecard_path: str = Field(
        default="ideation/idea_scorecard.yaml",
        description="Idea scorecard used for contribution-type hints when available.",
    )
    output_path: str = Field(
        default="drafts/alignment_matrix.json",
        description="Relative path for the alignment matrix seed JSON.",
    )


class InitializeManuscriptStateParams(BaseModel):
    outline_path: str = Field(default="drafts/outline.md", description="Global outline path.")
    resource_index_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Resource index generated by build_manuscript_resource_index.",
    )
    section_plan_path: str = Field(
        default="drafts/section_plan.json",
        description="Section plan generated by plan_manuscript_sections.",
    )
    evidence_plan_path: str = Field(
        default="drafts/evidence_plan.json",
        description="Evidence plan generated by plan_manuscript_evidence.",
    )
    figure_table_plan_path: str = Field(
        default="drafts/figure_table_plan.json",
        description="Figure/table plan generated by plan_manuscript_evidence.",
    )
    alignment_matrix_path: str = Field(
        default="drafts/alignment_matrix.json",
        description="Alignment matrix seed generated by build_alignment_matrix.",
    )
    state_output_path: str = Field(
        default="drafts/paper_state.json",
        description="Relative path for the shared manuscript state JSON.",
    )
    section_outline_dir: str = Field(
        default="drafts/section_outlines",
        description="Directory for per-section outline markdown files.",
    )
    target_venue: str = Field(default="", description="Target venue or journal/conference name.")


class UpdateManuscriptSectionStateParams(BaseModel):
    section_id: str = Field(description="Section id, e.g. methodology, experiments, introduction.")
    state_path: str = Field(default="drafts/paper_state.json", description="Shared manuscript state JSON.")
    section_path: str = Field(
        default="",
        description="Relative section file path. Defaults to drafts/sections/{section_id}.tex.",
    )
    status: Literal["written", "revised"] = Field(
        default="written",
        description="Section completion status to record in paper_state.json.",
    )


class AssembleManuscriptParams(BaseModel):
    section_dir: str = Field(default="drafts/sections", description="Directory containing section drafts.")
    output_path: str = Field(default="drafts/paper.tex", description="LaTeX output path.")
    outline_path: str = Field(default="drafts/outline.md", description="Outline path.")
    title: str = Field(default="", description="Optional paper title override.")
    target_venue: str = Field(default="", description="Target venue name used in comments only.")
    venue_style: Literal["is", "ccf_a", "both", "auto"] = Field(
        default="auto",
        description="Writing style selected by T8-STYLE-GATE; when 'both', also emits drafts/is and drafts/ccf_a variants.",
    )
    template_family: str = Field(default="", description="Template family selected by T8-STYLE-GATE.")
    template_id: str = Field(default="", description="Template id selected by T8-STYLE-GATE.")
    writing_language: Literal["zh", "en", "auto"] = Field(default="auto", description="Manuscript language selected by T8-STYLE-GATE.")


class PrepareSubmissionBundleParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex", description="Source manuscript LaTeX path.")
    bib_path: str = Field(default="literature/related_work.bib", description="Source bibliography path.")
    bundle_dir: str = Field(default="submission/bundle", description="Submission bundle directory.")
    main_filename: str = Field(default="main.tex", description="Main TeX filename inside the bundle.")
    references_filename: str = Field(default="references.bib", description="Bibliography filename inside the bundle.")
    copy_figures: bool = Field(default=True, description="Copy drafts/figures and figures into bundle/figures when present.")


class AuditManuscriptClaimsParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex", description="Paper draft path.")
    output_path: str = Field(default="drafts/manuscript_audit.md", description="Audit report path.")
    resource_index_path: str = Field(
        default="drafts/manuscript_resource_index.json",
        description="Resource index path.",
    )


class AuditWritingCraftParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex", description="Paper draft path.")
    sections_dir: str = Field(default="drafts/sections", description="Section draft directory.")
    related_work_bib_path: str = Field(default="literature/related_work.bib", description="Bibliography used for citation/context alignment.")
    paper_state_path: str = Field(default="drafts/paper_state.json", description="Shared paper state JSON.")
    alignment_matrix_path: str = Field(
        default="drafts/alignment_matrix.json",
        description="Alignment matrix JSON generated by build_alignment_matrix and refined by Writer.",
    )
    cdr_claim_ledger_path: str = Field(
        default="drafts/cdr_claim_ledger.json",
        description="CDR claim ledger path used for row-count checks.",
    )
    venue_style: Literal["is", "ccf_a", "both", "auto"] = Field(
        default="auto",
        description="Writing style selected by T8-STYLE-GATE.",
    )
    output_path: str = Field(default="drafts/craft_audit.md", description="Markdown audit report path.")
    also_audit_style_variants: bool = Field(
        default=True,
        description="When venue_style is 'both', also write drafts/is/craft_audit.* and drafts/ccf_a/craft_audit.* if variant papers exist.",
    )


class BuildManuscriptRevisionPatchesParams(BaseModel):
    round_num: int = Field(default=1, description="Review round number.")
    review_report_path: str = Field(
        default="",
        description="Review report path. Defaults to drafts/review_rounds/round_{round_num}.md.",
    )
    section_review_dir: str = Field(
        default="",
        description="Section review directory. Defaults to drafts/review_rounds/round_{round_num}_sections.",
    )
    output_path: str = Field(
        default="",
        description="Patch list output path. Defaults to drafts/patches/round_{round_num}_patches.json.",
    )
    include_low: bool = Field(default=True, description="Include Low-severity issues in the patch list.")


class BindReviewRoundParams(BaseModel):
    round_num: int = Field(default=1, description="Review round number.")
    output_path: str = Field(
        default="",
        description="Fingerprint JSON path. Defaults to drafts/review_rounds/round_{round_num}_fingerprints.json.",
    )


class BuildManuscriptResourceIndexTool(Tool):
    name = "build_manuscript_resource_index"
    description = (
        "Build a mechanical index of manuscript-writing resources: project, literature synthesis, "
        "paper notes, bibliography, hypotheses, novelty audit, experiment results, ablations, "
        "figures, tables, code artifacts, and logs. It only records provenance and previews."
    )
    parameters_schema = BuildManuscriptResourceIndexParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildManuscriptResourceIndexParams(**kwargs)
        try:
            output_path = self.policy.resolve_write(params.output_path)
            ws = self.policy.workspace_dir
            index = build_resource_index(ws, include_previews=params.include_previews)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"resource index failed: {exc}", error="index_failed")

        return ToolResult(
            ok=True,
            content=(
                f"Built manuscript resource index at {params.output_path}: "
                f"{len(index.get('artifacts', []))} artifacts, "
                f"{len(index.get('figures', []))} figures, "
                f"{len(index.get('tables', []))} tables."
            ),
            data=index,
        )


class PlanManuscriptSectionsTool(Tool):
    name = "plan_manuscript_sections"
    description = (
        "Create a mechanical section writing plan from the resource index. The plan maps each "
        "standard paper section to required inputs, evidence files, expected outputs, and LLM review notes."
    )
    parameters_schema = PlanManuscriptSectionsParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = PlanManuscriptSectionsParams(**kwargs)
        try:
            index_candidate = self.policy.workspace_dir / params.resource_index_path
            if index_candidate.exists():
                index_path = self.policy.resolve_read(params.resource_index_path)
                index = json.loads(index_path.read_text(encoding="utf-8"))
            else:
                index = build_resource_index(self.policy.workspace_dir, include_previews=False)
            plan = build_section_plan(index, target_venue=params.target_venue, paper_type=params.paper_type)
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError as exc:
            return ToolResult(ok=False, content=f"missing resource index: {exc}", error="missing_index")
        except Exception as exc:
            return ToolResult(ok=False, content=f"section planning failed: {exc}", error="planning_failed")

        return ToolResult(
            ok=True,
            content=f"Wrote section plan to {params.output_path} with {len(plan['sections'])} sections.",
            data=plan,
        )


class PlanManuscriptEvidenceTool(Tool):
    name = "plan_manuscript_evidence"
    description = (
        "Create mechanical evidence, claim-slot, figure, and table plans from the manuscript "
        "resource index. It does not decide scientific claims; it lists provenance and slots the "
        "Writer LLM must fill or mark unsupported."
    )
    parameters_schema = PlanManuscriptEvidenceParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = PlanManuscriptEvidenceParams(**kwargs)
        try:
            index_candidate = self.policy.workspace_dir / params.resource_index_path
            if index_candidate.exists():
                index_path = self.policy.resolve_read(params.resource_index_path)
                index = json.loads(index_path.read_text(encoding="utf-8"))
            else:
                index = build_resource_index(self.policy.workspace_dir, include_previews=False)
            evidence_plan, figure_plan = build_evidence_and_figure_plans(
                index,
                target_venue=params.target_venue,
            )
            evidence_path = self.policy.resolve_write(params.evidence_output_path)
            figure_path = self.policy.resolve_write(params.figure_output_path)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            figure_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(
                json.dumps(evidence_plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            figure_path.write_text(
                json.dumps(figure_plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"evidence planning failed: {exc}", error="planning_failed")

        return ToolResult(
            ok=True,
            content=(
                f"Wrote evidence plan to {params.evidence_output_path} and figure/table plan "
                f"to {params.figure_output_path}."
            ),
            data={"evidence_plan": evidence_plan, "figure_table_plan": figure_plan},
        )


class BuildManuscriptRegistriesTool(Tool):
    name = "build_manuscript_registries"
    description = (
        "Build mechanical CDR claim, generic claim, and figure/table registry seeds for T8. "
        "The tool does not write final scientific claims; it organizes existing evidence slots, "
        "CDR fields, citation pools, and visual slots for the Writer/Reviewer LLMs."
    )
    parameters_schema = BuildManuscriptRegistriesParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildManuscriptRegistriesParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            index = _read_optional_json(self.policy, params.resource_index_path)
            evidence_plan = _read_optional_json(self.policy, params.evidence_plan_path)
            figure_plan = _read_optional_json(self.policy, params.figure_table_plan_path)
            paper_state = _read_optional_json(self.policy, "drafts/paper_state.json")
            source_texts = _load_cdr_source_texts(ws)

            cdr_ledger = build_cdr_claim_ledger_seed(
                evidence_plan=evidence_plan,
                resource_index=index,
                source_texts=source_texts,
            )
            claim_ledger = build_claim_ledger_seed(
                evidence_plan,
                paper_state=paper_state,
                resource_index=index,
            )
            figure_registry = build_figure_registry_seed(
                figure_plan,
                resource_index=index,
            )
            known_artifacts = [
                str(item.get("path"))
                for item in index.get("artifacts", [])
                if isinstance(item, dict) and item.get("path")
            ]
            cdr_issues = validate_cdr_claim_ledger(cdr_ledger)
            claim_issues = validate_claim_ledger(claim_ledger, known_artifacts=known_artifacts)
            figure_issues = validate_figure_registry(figure_registry, known_artifacts=known_artifacts)
            blocking = cdr_issues + claim_issues + figure_issues
            if blocking:
                return ToolResult(
                    ok=False,
                    content="registry validation failed: " + "; ".join(blocking[:8]),
                    error="registry_validation_failed",
                    data={
                        "cdr_issues": cdr_issues,
                        "claim_issues": claim_issues,
                        "figure_issues": figure_issues,
                    },
                )

            outputs = {
                params.cdr_output_path: cdr_ledger,
                params.claim_output_path: claim_ledger,
                params.figure_output_path: figure_registry,
            }
            for rel, data in outputs.items():
                path = self.policy.resolve_write(rel)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"registry build failed: {exc}", error="registry_build_failed")

        return ToolResult(
            ok=True,
            content=(
                f"Wrote CDR ledger to {params.cdr_output_path}, claim ledger to "
                f"{params.claim_output_path}, and figure registry to {params.figure_output_path}."
            ),
            data={
                "cdr_claims": len(cdr_ledger.get("contribution_claims", [])),
                "claims": len(claim_ledger.get("claims", [])),
                "visuals": len(figure_registry.get("visuals", [])),
                "outputs": list(outputs),
            },
        )


class BuildAlignmentMatrixTool(Tool):
    name = "build_alignment_matrix"
    description = (
        "Build a mechanical alignment-matrix seed for manuscript writing. It maps CIDs to "
        "motivation, contribution, related-work gap, method design choice, experiment, and "
        "analysis slots. It only seeds provenance and TODO fields; the Writer LLM must fill "
        "academic wording after reading the artifacts."
    )
    parameters_schema = BuildAlignmentMatrixParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildAlignmentMatrixParams(**kwargs)
        try:
            cdr_ledger = _read_optional_json(self.policy, params.cdr_claim_ledger_path)
            evidence_plan = _read_optional_json(self.policy, params.evidence_plan_path)
            figure_plan = _read_optional_json(self.policy, params.figure_table_plan_path)
            synthesis = _read_optional_text(self.policy, params.synthesis_path)
            hypotheses = _read_optional_text(self.policy, params.hypotheses_path)
            idea_scorecard = _read_optional_text(self.policy, params.idea_scorecard_path)
            matrix = build_alignment_matrix_seed(
                cdr_ledger=cdr_ledger,
                evidence_plan=evidence_plan,
                figure_plan=figure_plan,
                synthesis_text=synthesis,
                hypotheses_text=hypotheses,
                idea_scorecard_text=idea_scorecard,
                source_paths={
                    "cdr_claim_ledger": params.cdr_claim_ledger_path,
                    "evidence_plan": params.evidence_plan_path,
                    "figure_table_plan": params.figure_table_plan_path,
                    "synthesis": params.synthesis_path,
                    "hypotheses": params.hypotheses_path,
                    "idea_scorecard": params.idea_scorecard_path,
                },
            )
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"alignment matrix build failed: {exc}", error="alignment_build_failed")

        return ToolResult(
            ok=True,
            content=f"Wrote alignment matrix seed to {params.output_path} with {len(matrix.get('rows', []))} rows.",
            data={"path": params.output_path, "row_count": len(matrix.get("rows", [])), "rows": matrix.get("rows", [])},
        )


class InitializeManuscriptStateTool(Tool):
    name = "initialize_manuscript_state"
    description = (
        "Initialize drafts/paper_state.json and drafts/section_outlines/*.md for true "
        "section-by-section manuscript writing. It creates mechanical shared-fact candidates "
        "and per-section writing briefs; the Writer LLM must refine claims and prose."
    )
    parameters_schema = InitializeManuscriptStateParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = InitializeManuscriptStateParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            outline = _read_optional_text(self.policy, params.outline_path)
            index = _read_optional_json(self.policy, params.resource_index_path)
            section_plan = _read_optional_json(self.policy, params.section_plan_path)
            evidence_plan = _read_optional_json(self.policy, params.evidence_plan_path)
            figure_plan = _read_optional_json(self.policy, params.figure_table_plan_path)
            alignment_matrix = _read_optional_json(self.policy, params.alignment_matrix_path)
            state = build_paper_state(
                outline_path=params.outline_path,
                outline_text=outline,
                index=index,
                section_plan=section_plan,
                evidence_plan=evidence_plan,
                figure_plan=figure_plan,
                alignment_matrix=alignment_matrix,
                alignment_matrix_path=params.alignment_matrix_path,
                section_outline_dir=params.section_outline_dir,
                target_venue=params.target_venue,
            )
            state["input_fingerprints"] = build_paper_state_input_fingerprints(
                ws,
                {
                    "outline": params.outline_path,
                    "resource_index": params.resource_index_path,
                    "section_plan": params.section_plan_path,
                    "evidence_plan": params.evidence_plan_path,
                    "figure_table_plan": params.figure_table_plan_path,
                    "alignment_matrix": params.alignment_matrix_path,
                    "related_work_bib": "literature/related_work.bib",
                    "experiment_evidence_pack": "drafts/experiment_evidence_pack.json",
                },
            )
            state_path = self.policy.resolve_write(params.state_output_path)
            outline_dir = self.policy.resolve_write(
                f"{params.section_outline_dir.rstrip('/')}/_manifest.txt"
            ).parent
            state_path.parent.mkdir(parents=True, exist_ok=True)
            outline_dir.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            outlines = build_section_outlines(
                state,
                section_plan=section_plan,
                evidence_plan=evidence_plan,
                figure_plan=figure_plan,
                outline_text=outline,
            )
            for section_id, text in outlines.items():
                path = self.policy.resolve_write(
                    f"{params.section_outline_dir.rstrip('/')}/{section_id}.md"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"manuscript state initialization failed: {exc}", error="state_init_failed")

        return ToolResult(
            ok=True,
            content=(
                f"Initialized manuscript state at {params.state_output_path} and "
                f"{len(outlines)} section outlines under {params.section_outline_dir}."
            ),
            data={
                "state_path": params.state_output_path,
                "section_outline_dir": params.section_outline_dir,
                "sections": state.get("section_order", []),
            },
        )


class UpdateManuscriptSectionStateTool(Tool):
    name = "update_manuscript_section_state"
    description = (
        "Record that one manuscript section file has been written or revised in paper_state.json. "
        "This is a mechanical status update after the Writer LLM creates the section prose."
    )
    parameters_schema = UpdateManuscriptSectionStateParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UpdateManuscriptSectionStateParams(**kwargs)
        section_id = normalize_section_id(params.section_id)
        section_rel = params.section_path or f"drafts/sections/{section_id}.tex"
        try:
            state_path = self.policy.resolve_read(params.state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            section_path = self.policy.resolve_read(section_rel)
            text = section_path.read_text(encoding="utf-8")
            if "\\documentclass" in text or "\\begin{document}" in text or "\\end{document}" in text:
                return ToolResult(
                    ok=False,
                    content=f"section file contains whole-document wrapper: {section_rel}",
                    error="section_wrapper_detected",
                )
            sections = state.setdefault("sections", {})
            entry = sections.setdefault(section_id, {})
            entry.update(
                {
                    "status": params.status,
                    "file": section_rel,
                    "chars": len(text.strip()),
                    "last_excerpt": text.strip()[:400],
                }
            )
            state["current_section"] = section_id
            state["last_written_section"] = section_id
            revision_log = state.setdefault("revision_log", [])
            if isinstance(revision_log, list):
                revision_log.append(
                    {
                        "section_id": section_id,
                        "status": params.status,
                        "file": section_rel,
                        "chars": len(text.strip()),
                    }
                )
            write_path = self.policy.resolve_write(params.state_path)
            write_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError as exc:
            return ToolResult(ok=False, content=f"missing state or section file: {exc}", error="missing_file")
        except Exception as exc:
            return ToolResult(ok=False, content=f"section state update failed: {exc}", error="state_update_failed")

        return ToolResult(
            ok=True,
            content=f"Updated {params.state_path}: {section_id} -> {params.status} ({len(text.strip())} chars).",
            data={"section_id": section_id, "state_path": params.state_path, "section_path": section_rel},
        )


class AssembleManuscriptTool(Tool):
    name = "assemble_manuscript"
    description = (
        "Assemble section drafts from drafts/sections/*.tex or *.md into drafts/paper.tex. "
        "This is mechanical assembly only; it does not generate missing scientific prose."
    )
    parameters_schema = AssembleManuscriptParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AssembleManuscriptParams(**kwargs)
        try:
            section_dir = self.policy.resolve_read(params.section_dir)
            output_path = self.policy.resolve_write(params.output_path)
            outline_path = self.policy.resolve_read(params.outline_path) if (self.policy.workspace_dir / params.outline_path).exists() else None
            assembled = assemble_sections(
                section_dir,
                title=params.title,
                target_venue=params.target_venue,
                outline_text=outline_path.read_text(encoding="utf-8") if outline_path else "",
                venue_style=params.venue_style,
                template_family=params.template_family,
                template_id=params.template_id,
                writing_language=params.writing_language,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(assembled, encoding="utf-8")
            _copy_latex_template_support_files(
                _resolve_latex_template(_repo_root(), params.template_family, params.template_id, params.writing_language),
                output_path.parent,
            )
            _copy_manuscript_bibliography(self.policy, "literature/related_work.bib", output_path.parent / "related_work.bib")
            variant_outputs = _write_style_variant_manuscripts(
                self.policy,
                assembled,
                venue_style=params.venue_style,
                target_venue=params.target_venue,
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError as exc:
            return ToolResult(ok=False, content=f"missing section draft: {exc}", error="missing_sections")
        except Exception as exc:
            return ToolResult(ok=False, content=f"assembly failed: {exc}", error="assembly_failed")

        return ToolResult(
            ok=True,
            content=f"Assembled manuscript to {params.output_path} ({len(assembled)} chars).",
            data={"path": params.output_path, "chars": len(assembled), "style_variants": variant_outputs},
        )


class PrepareSubmissionBundleTool(Tool):
    name = "prepare_submission_bundle"
    description = (
        "Mechanically prepare submission/bundle before T9 compilation: copy drafts/paper.tex to "
        "main.tex, copy literature/related_work.bib to references.bib, rewrite bibliography commands "
        "to use references, and copy figure assets. It does not write scientific prose."
    )
    parameters_schema = PrepareSubmissionBundleParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = PrepareSubmissionBundleParams(**kwargs)
        try:
            paper_path = self.policy.resolve_read(params.paper_path)
            bib_path = self.policy.resolve_read(params.bib_path)
            bundle_marker = self.policy.resolve_write(f"{params.bundle_dir.rstrip('/')}/.bundle_marker")
            bundle_dir = bundle_marker.parent
            main_path = self.policy.resolve_write(f"{params.bundle_dir.rstrip('/')}/{params.main_filename}")
            references_path = self.policy.resolve_write(
                f"{params.bundle_dir.rstrip('/')}/{params.references_filename}"
            )
            manifest_path = self.policy.resolve_write(f"{params.bundle_dir.rstrip('/')}/bundle_manifest.json")
            tex = paper_path.read_text(encoding="utf-8")
            tex = rewrite_bibliography_to_references(tex, Path(params.references_filename).stem)
            bundle_dir.mkdir(parents=True, exist_ok=True)
            references_path.write_text(
                strip_internal_bibtex_notes(bib_path.read_text(encoding="utf-8", errors="replace")),
                encoding="utf-8",
            )
            copied_figures, tex = _copy_submission_figures(
                self.policy,
                tex=tex,
                bundle_dir=params.bundle_dir.rstrip("/"),
                enabled=params.copy_figures,
            )
            copied_support_files = _copy_submission_latex_support_files(
                self.policy,
                tex=tex,
                source_dir=paper_path.parent,
                bundle_dir=params.bundle_dir.rstrip("/"),
            )
            main_path.write_text(tex, encoding="utf-8")
            manifest = build_submission_bundle_manifest(
                self.policy.workspace_dir,
                paper_path=paper_path,
                bib_path=bib_path,
                main_path=main_path,
                references_path=references_path,
                copied_figures=copied_figures,
                copied_support_files=copied_support_files,
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError as exc:
            return ToolResult(ok=False, content=f"missing submission input: {exc}", error="missing_input")
        except Exception as exc:
            return ToolResult(ok=False, content=f"submission bundle preparation failed: {exc}", error="bundle_failed")

        return ToolResult(
            ok=True,
            content=(
                f"Prepared submission bundle at {params.bundle_dir}: "
                f"{params.main_filename}, {params.references_filename}, {len(copied_figures)} figure files."
            ),
            data={
                "bundle_dir": params.bundle_dir,
                "main_tex": f"{params.bundle_dir.rstrip('/')}/{params.main_filename}",
                "references_bib": f"{params.bundle_dir.rstrip('/')}/{params.references_filename}",
                "copied_figures": copied_figures,
                "copied_support_files": copied_support_files,
                "bundle_manifest": f"{params.bundle_dir.rstrip('/')}/bundle_manifest.json",
            },
        )


class AuditManuscriptClaimsTool(Tool):
    name = "audit_manuscript_claims"
    description = (
        "Mechanically audit a manuscript for citation keys, numeric values, figure/table references, "
        "and required section presence. It returns issues for LLM/human review, not final scientific judgment."
    )
    parameters_schema = AuditManuscriptClaimsParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditManuscriptClaimsParams(**kwargs)
        try:
            paper_path = self.policy.resolve_read(params.paper_path)
            paper = paper_path.read_text(encoding="utf-8")
            index_candidate = self.policy.workspace_dir / params.resource_index_path
            if index_candidate.exists():
                index_path = self.policy.resolve_read(params.resource_index_path)
                index = json.loads(index_path.read_text(encoding="utf-8"))
            else:
                index = build_resource_index(self.policy.workspace_dir, include_previews=False)
            report = audit_manuscript(paper, index)
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report, encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"manuscript audit failed: {exc}", error="audit_failed")

        issue_count = report.count("- [ ]")
        return ToolResult(
            ok=True,
            content=f"Wrote manuscript audit to {params.output_path}; open issues: {issue_count}.",
            data={"path": params.output_path, "open_issues": issue_count},
        )


class AuditWritingCraftTool(Tool):
    name = "audit_writing_craft"
    description = (
        "Run deterministic writing-craft and alignment checks after manuscript assembly. "
        "It detects standalone Limitations sections, abstract citation commands, weak CID coverage hints, "
        "AI boilerplate, and other mechanically checkable writing issues."
    )
    parameters_schema = AuditWritingCraftParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditWritingCraftParams(**kwargs)
        try:
            paper = self.policy.resolve_read(params.paper_path).read_text(
                encoding="utf-8",
                errors="replace",
            )
            sections_path = self.policy.resolve_read(params.sections_dir)
            section_texts = _read_section_texts(sections_path)
            try:
                related_work_bib = _read_optional_text(self.policy, params.related_work_bib_path)
            except ToolAccessDenied:
                related_work_bib = ""
            support_text_by_key = citation_support_text_by_key(self.policy.workspace_dir)
            paper_state = _read_optional_json(self.policy, params.paper_state_path)
            alignment_matrix = _read_optional_json(self.policy, params.alignment_matrix_path)
            cdr_ledger = _read_optional_json(self.policy, params.cdr_claim_ledger_path)
            audit_doc = audit_writing_craft(
                paper=paper,
                section_texts=section_texts,
                related_work_bib=related_work_bib,
                support_text_by_key=support_text_by_key,
                paper_state=paper_state,
                alignment_matrix=alignment_matrix,
                cdr_ledger=cdr_ledger,
                venue_style=params.venue_style,
            )
            audit_doc["json"]["input_fingerprints"] = craft_audit_input_fingerprints(
                self.policy.workspace_dir,
                paper_path=params.paper_path,
                sections_dir=params.sections_dir,
                related_work_bib_path=params.related_work_bib_path,
                paper_state_path=params.paper_state_path,
                alignment_matrix_path=params.alignment_matrix_path,
                cdr_claim_ledger_path=params.cdr_claim_ledger_path,
            )
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(audit_doc["markdown"], encoding="utf-8")
            json_path = output_path.with_suffix(".json")
            json_path.write_text(
                json.dumps(audit_doc["json"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            variant_audits = _write_style_variant_craft_audits(
                self.policy,
                params=params,
                section_texts=section_texts,
                related_work_bib=related_work_bib,
                support_text_by_key=support_text_by_key,
                paper_state=paper_state,
                alignment_matrix=alignment_matrix,
                cdr_ledger=cdr_ledger,
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"writing craft audit failed: {exc}", error="craft_audit_failed")

        fail_count = sum(1 for item in audit_doc["json"]["checks"] if item["level"] == "FAIL")
        warn_count = sum(1 for item in audit_doc["json"]["checks"] if item["level"] == "WARN")
        return ToolResult(
            ok=True,
            content=(
                f"Wrote writing craft audit to {params.output_path}; "
                f"FAIL={fail_count}, WARN={warn_count}."
            ),
            data={
                "path": params.output_path,
                "json_path": str(json_path.relative_to(self.policy.workspace_dir)),
                "fail_count": fail_count,
                "warn_count": warn_count,
                "style_variant_audits": variant_audits,
            },
        )


class BuildManuscriptRevisionPatchesTool(Tool):
    name = "build_manuscript_revision_patches"
    description = (
        "Convert section-aware reviewer reports into a mechanical patch list for T8 revision. "
        "It only locates issues by section/severity/source; the Writer LLM still decides the "
        "scientific wording and how to revise each target section."
    )
    parameters_schema = BuildManuscriptRevisionPatchesParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildManuscriptRevisionPatchesParams(**kwargs)
        round_num = max(1, int(params.round_num or 1))
        review_rel = params.review_report_path or f"drafts/review_rounds/round_{round_num}.md"
        section_dir_rel = params.section_review_dir or f"drafts/review_rounds/round_{round_num}_sections"
        output_rel = params.output_path or f"drafts/patches/round_{round_num}_patches.json"

        try:
            review_text = ""
            review_candidate = self.policy.workspace_dir / review_rel
            if review_candidate.exists():
                review_text = self.policy.resolve_read(review_rel).read_text(
                    encoding="utf-8",
                    errors="replace",
                )

            section_reviews: dict[str, str] = {}
            section_dir = self.policy.workspace_dir / section_dir_rel
            if section_dir.exists() and section_dir.is_dir():
                for section_id in CORE_SECTIONS:
                    path = section_dir / f"{section_id}.md"
                    if path.exists():
                        rel = path.relative_to(self.policy.workspace_dir).as_posix()
                        section_reviews[section_id] = self.policy.resolve_read(rel).read_text(
                            encoding="utf-8",
                            errors="replace",
                        )

            patch_doc = build_revision_patch_list(
                round_num=round_num,
                review_text=review_text,
                section_reviews=section_reviews,
                include_low=params.include_low,
            )
            output_path = self.policy.resolve_write(output_rel)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(patch_doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"revision patch planning failed: {exc}", error="patch_planning_failed")

        return ToolResult(
            ok=True,
            content=f"Wrote revision patch list to {output_rel} with {len(patch_doc['patches'])} patches.",
            data={"path": output_rel, "patch_count": len(patch_doc["patches"]), "patches": patch_doc["patches"]},
        )


class BindReviewRoundTool(Tool):
    name = "bind_review_round"
    description = (
        "Bind a T8 reviewer round to the current manuscript, audit, evidence, and bibliography inputs. "
        "Call after writing round_N.md and round_N_sections/*.md."
    )
    parameters_schema = BindReviewRoundParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BindReviewRoundParams(**kwargs)
        round_num = max(1, int(params.round_num or 1))
        output_rel = params.output_path or f"drafts/review_rounds/round_{round_num}_fingerprints.json"
        payload = {
            "version": "1.0",
            "semantics": "review_round_input_fingerprints",
            "round": round_num,
            "input_fingerprints": review_round_input_fingerprints(self.policy.workspace_dir),
        }
        try:
            output = self.policy.resolve_write(output_rel)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        return ToolResult(
            ok=True,
            content=f"Bound review round {round_num} to current input fingerprints.",
            data={"path": output_rel, "round": round_num},
        )


def build_resource_index(workspace: Path, *, include_previews: bool = True) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for rel in [
        "project.yaml",
        "literature/synthesis.md",
        "literature/synthesis_workbench.json",
        "literature/comparison_table.csv",
        "literature/related_work.bib",
        "ideation/hypotheses.md",
        "ideation/exp_plan.yaml",
        "ideation/idea_scorecard.yaml",
        "ideation/novelty_audit.md",
        "ideation/risks.md",
        "novelty/novelty_report.md",
        "novelty/must_add_baselines.md",
        "experiments/results_summary.json",
        "experiments/integrity_audit.json",
        "experiments/evidence_index.json",
        "experiments/experimental_claims.json",
        "experiments/ablations.csv",
        "experiments/iteration_log.md",
        "experiments/seed_ensemble_summary.json",
        "experiments/iteration_diversity_check.md",
        "drafts/experiment_evidence_pack.json",
        "drafts/result_to_claim.json",
        "drafts/paper_claim_audit.md",
        "drafts/paper_claim_audit.json",
        "drafts/cdr_claim_ledger.json",
        "drafts/claim_ledger.json",
        "drafts/figure_registry.json",
    ]:
        path = workspace / rel
        if path.exists():
            artifacts.append(_artifact_entry(workspace, path, include_preview=include_previews))

    note_patterns = [
        "literature/paper_notes/*.md",
        "literature/paper_notes_abstract/*.md",
        "literature/paper_notes_bridge/**/*.md",
    ]
    for pattern in note_patterns:
        for path in sorted(workspace.glob(pattern)):
            if is_paper_note_file(path):
                artifacts.append(_artifact_entry(workspace, path, include_preview=False))

    for pattern in [
        "experiments/runs/**/*",
        "experiments/configs/**/*",
        "experiments/code/**/*",
    ]:
        for path in sorted(workspace.glob(pattern)):
            if path.is_file():
                artifacts.append(_artifact_entry(workspace, path, include_preview=False))

    figures = [_media_entry(workspace, path) for path in _glob_media(workspace, kind="figure")]
    tables = [_media_entry(workspace, path) for path in _glob_media(workspace, kind="table")]
    bib_keys = _extract_bib_keys(workspace / "literature" / "related_work.bib")
    citation_refs = _extract_citation_reference_summary(workspace)
    citation_quality = _extract_citation_quality_summary(workspace / "literature" / "notes_manifest.json")
    paper_note_cards = _extract_paper_note_cards(workspace)
    result_metrics = _extract_result_metrics(workspace / "experiments" / "results_summary.json")
    result_metrics.extend(_extract_evidence_pack_metrics(workspace / "drafts" / "experiment_evidence_pack.json"))
    result_metrics = _dedupe_metric_records(result_metrics)
    ablation_columns = _csv_columns(workspace / "experiments" / "ablations.csv")

    return {
        "version": "1.0",
        "semantics": "mechanical_resource_index_not_scientific_summary",
        "artifacts": artifacts,
        "figures": figures,
        "tables": tables,
        "bib_keys": bib_keys,
        "citation_map_summary": citation_refs["summary"],
        "citation_ref_by_note_id": citation_refs["citation_ref_by_note_id"],
        "note_id_by_bib_key": citation_refs["note_id_by_bib_key"],
        "unmapped_note_ids": citation_refs["unmapped_note_ids"],
        "citation_quality": citation_quality,
        "paper_note_cards": paper_note_cards,
        "result_metrics": result_metrics,
        "ablation_columns": ablation_columns,
        "writing_guidance": {
            "use_llm_for": [
                "claim selection",
                "section argumentation",
                "method explanation",
                "positioning against prior work",
                "limitations and threat interpretation",
            ],
            "use_tools_for": [
                "resource indexing",
                "citation-key extraction",
                "figure/table inventory",
                "section assembly",
                "numeric/citation audit hints",
            ],
        },
    }


def _extract_citation_reference_summary(workspace: Path) -> dict[str, Any]:
    try:
        citation_map = load_or_build_citation_map(workspace / "literature")
    except Exception:
        citation_map = {}
    entries = citation_map.get("entries") if isinstance(citation_map, dict) else []
    citation_ref_by_note_id: dict[str, str] = {}
    note_id_by_bib_key: dict[str, list[str]] = {}
    unmapped_note_ids: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            note_id = str(entry.get("note_id") or "").strip()
            if not note_id:
                continue
            citation_ref = str(entry.get("citation_ref") or "").strip()
            bib_key = str(entry.get("bib_key") or "").strip()
            if citation_ref:
                citation_ref_by_note_id[note_id] = citation_ref
            if bib_key:
                note_id_by_bib_key.setdefault(bib_key, []).append(note_id)
            else:
                unmapped_note_ids.append(note_id)
    return {
        "summary": {
            "available": bool(citation_map),
            "note_count": int(citation_map.get("note_count") or 0) if isinstance(citation_map, dict) else 0,
            "bib_entry_count": int(citation_map.get("bib_entry_count") or 0) if isinstance(citation_map, dict) else 0,
            "mapped_bib_count": int(citation_map.get("mapped_bib_count") or 0) if isinstance(citation_map, dict) else 0,
            "usage_rule": (
                "When converting synthesis provenance to TeX, replace [note:<id>] with citation_ref_by_note_id[id] "
                "only if it starts with \\cite; otherwise treat the note as provenance/upgrade context, not a formal citation."
            ),
        },
        "citation_ref_by_note_id": citation_ref_by_note_id,
        "note_id_by_bib_key": {key: sorted(set(ids)) for key, ids in note_id_by_bib_key.items()},
        "unmapped_note_ids": sorted(set(unmapped_note_ids)),
    }


def _extract_citation_quality_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "available": False,
            "usage_rule": "citation quality not available; inspect paper notes and evidence levels manually",
        }
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "error": "notes_manifest_unreadable"}
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    by_use: dict[str, int] = {}
    high_quality_ids: list[str] = []
    low_or_do_not_cite: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") != "complete":
            continue
        use = str(entry.get("citation_use") or "unknown")
        by_use[use] = by_use.get(use, 0) + 1
        try:
            score = float(entry.get("citation_quality_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        paper_id = str(entry.get("canonical_id") or entry.get("paper_id") or "").strip()
        if score >= 0.55 and use in {"core_evidence", "supporting_context"} and paper_id:
            high_quality_ids.append(paper_id)
        if (score < 0.55 or use == "do_not_cite") and paper_id:
            low_or_do_not_cite.append(paper_id)
    return {
        "available": True,
        "source": "literature/notes_manifest.json",
        "by_use": by_use,
        "core_or_supporting_ids": high_quality_ids[:40],
        "low_or_do_not_cite_ids": low_or_do_not_cite[:40],
        "usage_rule": "Prefer score>=0.55 core_evidence/supporting_context for claims; lower scores are background or upgrade leads.",
    }


def _extract_paper_note_cards(workspace: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    try:
        citation_map = load_or_build_citation_map(workspace / "literature")
    except Exception:
        citation_map = {}
    entries = citation_map.get("entries") if isinstance(citation_map, dict) else []
    by_note_id: dict[str, dict[str, Any]] = {}
    by_source_file: dict[str, dict[str, Any]] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            note_id = str(entry.get("note_id") or "").strip()
            if note_id:
                by_note_id[note_id] = entry
            source_file = str(entry.get("source_file") or "").strip()
            if source_file:
                by_source_file[source_file] = entry

    cards: list[dict[str, Any]] = []
    note_roots = [
        workspace / "literature" / "paper_notes",
        workspace / "literature" / "paper_notes_bridge",
        workspace / "literature" / "paper_notes_abstract",
    ]
    for root in note_roots:
        if not root.exists():
            continue
        pattern = "**/*.md" if root.name == "paper_notes_bridge" else "*.md"
        for path in sorted(root.glob(pattern)):
            if not is_paper_note_file(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            note_id = path.stem
            entry = by_source_file.get(path.name) or by_note_id.get(note_id) or {}
            card = {
                "note_id": str(entry.get("note_id") or note_id),
                "paper_id": _markdown_field(text, "ID") or str(entry.get("paper_id") or note_id),
                "title": _first_markdown_heading(text) or str(entry.get("title") or note_id),
                "path": _rel_path(workspace, path),
                "bib_key": str(entry.get("bib_key") or ""),
                "citation_ref": str(entry.get("citation_ref") or ""),
                "evidence_level": _evidence_level_from_note(text),
                "citation_use": _markdown_field(text, "Citation Use") or "unknown",
                "citation_quality_score": _parse_float(_markdown_field(text, "Citation Quality Score")),
                "problem_motivation": _note_section_excerpt(text, "1. Problem & Motivation"),
                "method_overview": _note_section_excerpt(text, "2. Method Overview", "2. Method Summary"),
                "core_approach_view": _note_section_excerpt(
                    text,
                    "A. Core Approach / Perspective",
                    "A. 核心做法/视角",
                ),
                "bridge_point": _note_section_excerpt(text, "B. Bridge Point", "B. 桥接点"),
                "key_results": _note_section_excerpt(text, "3. Key Results", "3. Key Claimed Results"),
                "gaps": _note_section_excerpt(text, "9. Weaknesses / Gaps"),
                "raw_abstract": _note_section_excerpt(text, "Raw Abstract", limit=480),
                "reading_coverage": _note_section_excerpt(text, "12. Reading Coverage", "Source"),
                "mechanism_claim": _note_section_excerpt(text, "13. Mechanism Claim"),
                "design_rationale": _note_section_excerpt(text, "14. Design Rationale"),
                "artifact_design": _note_section_excerpt(text, "15. Artifact & Design Principles"),
                "data_view": _note_section_excerpt(text, "16. Data View & Evaluation Mode"),
                "boundary_conditions": _note_section_excerpt(text, "18. Boundary Conditions"),
                "cross_paper_tension": _note_section_excerpt(text, "19. Cross-Paper Tension"),
            }
            card["sections_available"] = [
                key
                for key in (
                    "problem_motivation",
                    "method_overview",
                    "core_approach_view",
                    "bridge_point",
                    "key_results",
                    "gaps",
                    "mechanism_claim",
                    "design_rationale",
                    "artifact_design",
                    "data_view",
                    "boundary_conditions",
                    "cross_paper_tension",
                    "raw_abstract",
                )
                if str(card.get(key) or "").strip()
            ]
            card["claim_usable"] = _paper_note_card_claim_usable(card)
            warning = _paper_note_card_quality_warning(card)
            if warning:
                card["quality_warning"] = warning
            cards.append(card)
    cards.sort(
        key=lambda item: (
            not bool(item.get("claim_usable")),
            str(item.get("evidence_level") or "") == "ABSTRACT_ONLY",
            -float(item.get("citation_quality_score") or 0.0),
            str(item.get("title") or ""),
        )
    )
    return cards[:limit]


def _paper_note_card_claim_usable(card: dict[str, Any]) -> bool:
    use = str(card.get("citation_use") or "").strip().lower()
    if use in {"do_not_cite", "do-not-cite", "excluded", "unrelated"}:
        return False
    if card.get("citation_allowed") is False:
        return False
    try:
        score = float(card.get("citation_quality_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < 0.55:
        return False
    combined = " ".join(
        str(card.get(key) or "")
        for key in (
            "problem_motivation",
            "method_overview",
            "core_approach_view",
            "bridge_point",
            "gaps",
            "mechanism_claim",
            "design_rationale",
            "boundary_conditions",
        )
    )
    if "与项目无关" in combined or "unrelated" in combined.lower():
        return False
    return True


def _paper_note_card_quality_warning(card: dict[str, Any]) -> str:
    use = str(card.get("citation_use") or "").strip().lower()
    if use in {"do_not_cite", "do-not-cite", "excluded", "unrelated"}:
        return "do_not_use_for_claims"
    if card.get("citation_allowed") is False:
        return "citation_not_allowed"
    try:
        score = float(card.get("citation_quality_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < 0.55:
        return "low_citation_quality"
    combined = " ".join(
        str(card.get(key) or "")
        for key in ("core_approach_view", "bridge_point", "gaps", "mechanism_claim", "design_rationale")
    )
    if "与项目无关" in combined or "unrelated" in combined.lower():
        return "unrelated_to_project"
    return ""


def _markdown_field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+)$", text or "")
    return match.group(1).strip() if match else ""


def _evidence_level_from_note(text: str) -> str:
    status = _markdown_field(text, "Status")
    if "ABSTRACT-ONLY" in status:
        return "ABSTRACT_ONLY"
    if "PARTIAL-TEXT" in status:
        return "PARTIAL_TEXT"
    if "FULL-TEXT" in status:
        return "FULL_TEXT"
    return "UNKNOWN"


def _note_section_excerpt(text: str, *headings: str, limit: int = 320) -> str:
    for heading in headings:
        match = re.search(
            rf"(?ms)^##\s+{re.escape(heading)}\s*(?P<body>.*?)(?=^##\s+|\Z)",
            text or "",
        )
        if not match:
            continue
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        if body:
            return _shorten(body, limit)
    return ""


def _parse_float(value: str) -> float:
    match = re.search(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", str(value or ""))
    if not match:
        return 0.0
    try:
        return min(1.0, max(0.0, float(match.group(0))))
    except ValueError:
        return 0.0


def build_section_plan(index: dict[str, Any], *, target_venue: str = "", paper_type: str = "auto") -> dict[str, Any]:
    artifact_paths = {item["path"] for item in index.get("artifacts", [])}
    has_results = "experiments/results_summary.json" in artifact_paths
    has_ablations = "experiments/ablations.csv" in artifact_paths
    has_bib = "literature/related_work.bib" in artifact_paths
    sections = []

    def section(name: str, required: list[str], outputs: list[str], llm_tasks: list[str]) -> None:
        sections.append(
            {
                "id": name,
                "draft_path": f"drafts/sections/{name}.tex",
                "required_inputs": required,
                "available_inputs": [path for path in required if path in artifact_paths],
                "missing_inputs": [path for path in required if path not in artifact_paths],
                "expected_outputs": outputs,
                "llm_tasks": llm_tasks,
                "writing_contract": _section_writing_contract(name),
                "tool_notes": "Tools provide provenance and inventories only; Writer LLM must write and verify the section.",
            }
        )

    section(
        "abstract",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/abstract.md",
            "drafts/alignment_matrix.json",
            "drafts/sections/introduction.tex",
            "drafts/sections/methodology.tex",
            "drafts/sections/experiments.tex",
            "drafts/sections/analysis.tex",
            "drafts/sections/conclusion.tex",
            "experiments/results_summary.json",
        ],
        ["problem/method/result/contribution compressed to venue-appropriate abstract"],
        ["Write last after all main sections exist; do not introduce claims absent from the paper."],
    )
    section(
        "introduction",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/introduction.md",
            "drafts/alignment_matrix.json",
            "drafts/cdr_claim_ledger.json",
            "literature/synthesis.md",
            "ideation/hypotheses.md",
            "experiments/results_summary.json",
            "drafts/sections/methodology.tex",
            "drafts/sections/experiments.tex",
            "drafts/sections/related_work.tex",
        ],
        ["motivation funnel", "technical gap", "contributions", "result headline"],
        ["Frame the problem from broad setting to precise bottleneck; include honest contribution bullets."],
    )
    section(
        "related_work",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/related_work.md",
            "drafts/alignment_matrix.json",
            "literature/synthesis.md",
            "literature/synthesis_workbench.json",
            "literature/domain_map.json",
            "literature/comparison_table.csv",
            "literature/paper_notes",
            "literature/related_work.bib",
            "ideation/idea_scorecard.yaml",
        ],
        ["taxonomy", "contrastive positioning", "citation-backed paragraphs"],
        [
            "Use synthesis families, adjacent transfers, domain-map buckets, and nearest-prior-work hints; cite only keys present in bibliography."
        ],
    )
    section(
        "methodology",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/methodology.md",
            "drafts/alignment_matrix.json",
            "ideation/hypotheses.md",
            "ideation/exp_plan.yaml",
            "ideation/idea_scorecard.yaml",
            "ideation/novelty_audit.md",
            "ideation/_design_rationale_tuples",
            "experiments/configs",
            "experiments/code",
        ],
        ["method overview", "algorithm/procedure", "implementation details"],
        ["Explain mechanism and design choices; separate proposed method from experimental protocol."],
    )
    section(
        "experiments",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/experiments.md",
            "drafts/alignment_matrix.json",
            "experiments/results_summary.json",
            "experiments/ablations.csv",
            "experiments/runs",
            "experiments/configs",
            "experiments/seed_ensemble_summary.json",
            "ideation/exp_plan.yaml",
            "drafts/figure_table_plan.json",
            "novelty/must_add_baselines.md",
        ],
        ["setup", "datasets", "baselines", "metrics", "main results", "ablations"],
        ["Every number must come from results artifacts; if stats are missing, remove or weaken the claim in final TeX and record the evidence boundary in natural language."],
    )
    section(
        "analysis",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/analysis.md",
            "drafts/alignment_matrix.json",
            "drafts/sections/methodology.tex",
            "drafts/sections/experiments.tex",
            "experiments/ablations.csv",
            "experiments/iteration_log.md",
            "ideation/novelty_audit.md",
        ],
        ["mechanism interpretation", "failure cases", "sensitivity analysis"],
        ["Connect ablations back to hypotheses and alternative explanations."],
    )
    section(
        "conclusion",
        [
            "drafts/paper_state.json",
            "drafts/section_outlines/conclusion.md",
            "drafts/alignment_matrix.json",
            "drafts/sections/introduction.tex",
            "drafts/sections/experiments.tex",
            "ideation/risks.md",
            "experiments/iteration_log.md",
            "ideation/novelty_audit.md",
            "experiments/results_summary.json",
        ],
        ["concise contribution recap", "limitations subsection", "future work"],
        [
            "Do not overclaim beyond validated results.",
            "Include a concrete \\subsection{Limitations} with evidence boundaries and threats to validity.",
        ],
    )

    return {
        "version": "1.0",
        "target_venue": target_venue,
        "paper_type": paper_type,
        "has_results": has_results,
        "has_ablations": has_ablations,
        "has_bibliography": has_bib,
        "recommended_flow": [
            "resource_index",
            "outline",
            "section_plan",
            "section_draft_by_state_machine",
            "assembly",
            "claim_audit",
            "review",
            "revision",
            "submission",
        ],
        "sections": sections,
        "figures_available": index.get("figures", []),
        "tables_available": index.get("tables", []),
    }


def build_evidence_and_figure_plans(
    index: dict[str, Any],
    *,
    target_venue: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_paths = {item["path"] for item in index.get("artifacts", [])}
    metrics = index.get("result_metrics", [])
    bib_keys = index.get("bib_keys", [])
    figures = index.get("figures", [])
    tables = index.get("tables", [])

    def available(*paths: str) -> list[str]:
        return [path for path in paths if path in artifact_paths]

    claim_slots = [
        {
            "slot_id": "intro_problem_gap",
            "section": "introduction",
            "claim_type": "problem_gap",
            "candidate_evidence": available(
                "literature/synthesis.md",
                "literature/comparison_table.csv",
                "ideation/hypotheses.md",
            ),
            "citation_pool": bib_keys,
            "llm_task": "State the research gap only after checking synthesis and paper notes; do not treat retrieval coverage hints as proven gaps.",
            "cdr_field": "problem_frame",
        },
        {
            "slot_id": "intro_contribution_headline",
            "section": "introduction",
            "claim_type": "contribution",
            "candidate_evidence": available(
                "ideation/hypotheses.md",
                "ideation/idea_scorecard.yaml",
                "experiments/results_summary.json",
                "experiments/ablations.csv",
                "drafts/experiment_evidence_pack.json",
                "drafts/result_to_claim.json",
            ),
            "result_metric_candidates": metrics,
            "llm_task": "Write contribution bullets that each map to a hypothesis and at least one result artifact.",
            "cdr_field": "contribution_type",
        },
        {
            "slot_id": "related_work_positioning",
            "section": "related_work",
            "claim_type": "prior_work_contrast",
            "candidate_evidence": available(
                "literature/synthesis.md",
                "literature/comparison_table.csv",
                "literature/related_work.bib",
            ),
            "citation_pool": bib_keys,
            "llm_task": "Build a taxonomy and contrast the proposed mechanism against cited method families.",
            "cdr_field": "cross_paper_tension",
        },
        {
            "slot_id": "method_mechanism",
            "section": "methodology",
            "claim_type": "method_mechanism",
            "candidate_evidence": available(
                "ideation/hypotheses.md",
                "ideation/exp_plan.yaml",
                "ideation/idea_scorecard.yaml",
                "experiments/configs",
            ),
            "llm_task": "Explain the proposed mechanism and algorithmic procedure from hypotheses and exp_plan; tools do not infer the method.",
            "cdr_field": "design_rationale",
        },
        {
            "slot_id": "experiments_main_result",
            "section": "experiments",
            "claim_type": "empirical_result",
            "candidate_evidence": available(
                "experiments/results_summary.json",
                "experiments/ablations.csv",
                "experiments/seed_ensemble_summary.json",
                "experiments/iteration_log.md",
                "experiments/integrity_audit.json",
                "drafts/experiment_evidence_pack.json",
                "drafts/result_to_claim.json",
            ),
            "result_metric_candidates": metrics,
            "llm_task": "Report only metrics present in result artifacts and state seed/baseline context.",
            "cdr_field": "evaluation_mode",
        },
        {
            "slot_id": "analysis_mechanism_evidence",
            "section": "analysis",
            "claim_type": "mechanism_interpretation",
            "candidate_evidence": available(
                "experiments/ablations.csv",
                "experiments/iteration_log.md",
                "ideation/novelty_audit.md",
                "drafts/experiment_evidence_pack.json",
                "drafts/result_to_claim.json",
            ),
            "llm_task": "Interpret why ablations support or weaken the claimed mechanism; note alternative explanations.",
            "cdr_field": "design_rationale",
        },
        {
            "slot_id": "conclusion_limitations_boundary",
            "section": "conclusion",
            "claim_type": "evidence_boundary",
            "candidate_evidence": available(
                "ideation/risks.md",
                "ideation/novelty_audit.md",
                "experiments/iteration_log.md",
                "experiments/integrity_audit.json",
                "drafts/result_to_claim.json",
                "novelty/novelty_report.md",
            ),
            "llm_task": "In the Conclusion limitations subsection, state external-executor provenance, mock/dry-run status, data, baseline, seed/compute, and reproducibility boundaries honestly.",
            "cdr_field": "boundary_conditions",
        },
    ]

    evidence_plan = {
        "version": "1.0",
        "target_venue": target_venue,
        "semantics": "mechanical_claim_slots_not_final_claims",
        "claim_slots": claim_slots,
        "global_evidence_inventory": {
            "artifacts": sorted(artifact_paths),
            "bib_keys": bib_keys,
            "result_metrics": metrics,
            "ablation_columns": index.get("ablation_columns", []),
            "figures": figures,
            "tables": tables,
        },
        "rules": [
            "LLM must fill claim wording; this plan only lists slots and provenance.",
            "Every numeric claim must point to result_metrics or an experiment artifact.",
            "Every citation claim must use keys from bib_keys.",
            "Unsupported slots remain unsupported in this plan; final TeX must remove or weaken those claims, or describe the evidence boundary in natural-language limitations.",
        ],
    }

    figure_slots = [
        {
            "figure_id": "fig:method_overview",
            "type": "schematic",
            "status": "needs_llm_or_code_generation",
            "source_artifacts": available("ideation/exp_plan.yaml", "ideation/hypotheses.md"),
            "intended_section": "methodology",
            "message_slot": "method_mechanism",
            "notes": "A method schematic is useful when the proposed mechanism has multiple steps; generate only if it clarifies the method.",
        },
        {
            "figure_id": "fig:main_results",
            "type": "result_plot",
            "status": "available_or_generate_from_results",
            "source_artifacts": available("experiments/results_summary.json", "experiments/runs"),
            "intended_section": "experiments",
            "message_slot": "experiments_main_result",
            "notes": "Plot headline metric vs. strongest baselines when result artifacts contain comparable runs.",
        },
        {
            "figure_id": "fig:ablation",
            "type": "ablation_plot",
            "status": "available_or_generate_from_csv",
            "source_artifacts": available("experiments/ablations.csv"),
            "intended_section": "analysis",
            "message_slot": "analysis_mechanism_evidence",
            "notes": "Use ablation CSV to visualize which mechanism component drives the effect.",
        },
        {
            "table_id": "tab:main_results",
            "type": "result_table",
            "status": "generate_from_results",
            "source_artifacts": available("experiments/results_summary.json"),
            "intended_section": "experiments",
            "message_slot": "experiments_main_result",
            "notes": "Prefer a compact table when baselines, metrics, and seed statistics are clearer than a plot.",
        },
        {
            "table_id": "tab:related_work",
            "type": "comparison_table",
            "status": "derive_from_literature_table",
            "source_artifacts": available("literature/comparison_table.csv", "literature/synthesis.md"),
            "intended_section": "related_work",
            "message_slot": "related_work_positioning",
            "notes": "Use only verified comparison fields; do not imply absent prior-work properties.",
        },
    ]

    figure_plan = {
        "version": "1.0",
        "target_venue": target_venue,
        "semantics": "mechanical_figure_table_plan_not_generated_figures",
        "existing_figures": figures,
        "existing_tables": tables,
        "planned_visuals": figure_slots,
        "generation_guidance": [
            "Generate plots from results/ablations/run artifacts, not from manuscript prose.",
            "Captions must state data source and metric definitions.",
            "Use accessible colors and avoid overloaded multi-metric figures.",
            "If data is missing, keep the visual ungenerated in this plan and remove unsupported figure/table references from final TeX.",
        ],
    }
    return evidence_plan, figure_plan


def _load_cdr_source_texts(workspace: Path) -> dict[str, str]:
    """Load short source previews for mechanical CDR ledger seeding."""

    previews: dict[str, str] = {}
    for rel in [
        "ideation/idea_scorecard.yaml",
        "ideation/hypotheses.md",
        "ideation/novelty_audit.md",
        "experiments/results_summary.json",
        "literature/synthesis.md",
        "literature/synthesis_workbench.json",
    ]:
        path = workspace / rel
        if path.exists() and path.is_file():
            previews[rel] = path.read_text(encoding="utf-8", errors="replace")[:6000]
    return previews


def build_alignment_matrix_seed(
    *,
    cdr_ledger: dict[str, Any],
    evidence_plan: dict[str, Any],
    figure_plan: dict[str, Any],
    synthesis_text: str,
    hypotheses_text: str,
    idea_scorecard_text: str,
    source_paths: dict[str, str],
) -> dict[str, Any]:
    """Seed the contribution alignment matrix without making final claims."""

    contribution_chains = cdr_ledger.get("contribution_chains", []) if isinstance(cdr_ledger, dict) else []
    contribution_claims = cdr_ledger.get("contribution_claims", []) if isinstance(cdr_ledger, dict) else []
    cdr_tuple = cdr_ledger.get("cdr_tuple", {}) if isinstance(cdr_ledger, dict) else {}
    hypotheses = _extract_hypothesis_ids(hypotheses_text)
    synthesis_anchor = _first_markdown_heading(synthesis_text)
    contribution_type = str(cdr_tuple.get("contribution_type") or "").strip() or _extract_contribution_type_hint(idea_scorecard_text)
    scorecard_hints = _extract_scorecard_alignment_hints(idea_scorecard_text)
    rows: list[dict[str, Any]] = []

    claim_by_id = {
        str(claim.get("claim_id") or ""): claim
        for claim in contribution_claims
        if isinstance(claim, dict) and claim.get("claim_id")
    }
    if isinstance(contribution_chains, list) and contribution_chains:
        lanes = [item for item in contribution_chains if isinstance(item, dict)]
    else:
        lanes = _fallback_alignment_lanes(contribution_claims)

    used_cids: set[str] = set()
    for idx, lane in enumerate(lanes, start=1):
        if not isinstance(lane, dict):
            continue
        cid = _normalize_cid(str(lane.get("cid") or f"C{idx}"), idx, used_cids)
        used_cids.add(cid)
        source_claim_ids = _unique_strings(lane.get("source_claim_ids", []))
        source_claims = [claim_by_id[claim_id] for claim_id in source_claim_ids if claim_id in claim_by_id]
        primary_claim = source_claims[0] if source_claims else {}
        cdr_field = str(primary_claim.get("cdr_field") or "").strip()
        required_sections = [
            normalize_section_id(str(item))
            for claim in source_claims
            for item in claim.get("required_section", [])
            if str(item).strip()
        ]
        if not required_sections:
            required_sections = ["introduction", "related_work", "methodology", "experiments", "analysis", "conclusion"]
        evidence_refs = _unique_strings(
            [
                ref
                for claim in source_claims
                for ref in claim.get("evidence_artifacts", [])
            ]
        )
        row_contribution_type = str(lane.get("contribution_type") or "").strip() or contribution_type
        scorecard_hint = _scorecard_hint_for_lane(scorecard_hints, lane=lane, idx=idx)
        row = {
            "cid": cid,
            "source_claim_ids": source_claim_ids,
            "source_slot_ids": _unique_strings([claim.get("source_slot_id") for claim in source_claims]),
            "hypothesis": str(lane.get("hypothesis") or "").strip()
            or (hypotheses[(idx - 1) % len(hypotheses)] if hypotheses else "LLM_REVIEW_REQUIRED"),
            "motivation": "LLM_REVIEW_REQUIRED",
            "contribution": _first_nonempty([claim.get("claim") or claim.get("claim_text") for claim in source_claims])
            or "LLM_REVIEW_REQUIRED",
            "contribution_type": row_contribution_type or "LLM_REVIEW_REQUIRED",
            "related_gap": {
                "papers": _citation_pool_for_alignment(evidence_plan, section="related_work"),
                "tension": "LLM_REVIEW_REQUIRED",
                "synthesis_anchor": synthesis_anchor or source_paths.get("synthesis", "literature/synthesis.md"),
                "nearest_prior_work": scorecard_hint.get("nearest_prior_work", {}),
            },
            "counterfactual": scorecard_hint.get("counterfactual_check", "LLM_REVIEW_REQUIRED"),
            "counterfactual_note": scorecard_hint.get("counterfactual_note", "LLM_REVIEW_REQUIRED"),
            "nearest_prior_work": scorecard_hint.get("nearest_prior_work", {}),
            "novelty_signal": scorecard_hint.get("novelty_signal", "LLM_REVIEW_REQUIRED"),
            "design_choice": (
                _design_choice_hint(cdr_tuple, primary_claim, required_sections)
                if source_claims or cdr_field in {"design_rationale", "artifact", "design_principles"}
                else "LLM_REVIEW_REQUIRED"
            ),
            "experiment": _experiment_alignment_hint(evidence_plan, figure_plan, idx=idx, evidence_refs=evidence_refs),
            "analysis": "LLM_REVIEW_REQUIRED",
            "required_sections": required_sections,
            "evidence_artifacts": evidence_refs,
            "status": "seed_needs_llm_completion",
        }
        rows.append(row)

    return {
        "version": "1.0",
        "semantics": "alignment_matrix_seed_not_final_scientific_judgment",
        "source_paths": source_paths,
        "rows": rows,
        "rules": [
            "This is a mechanical seed; Writer LLM must complete motivation, related_gap, design_choice, experiment, and analysis after reading artifacts.",
            "Rows are contribution lanes, not raw CDR evidence slots; final contribution wording belongs in the Writer outline.",
            "CIDs are internal alignment identifiers for tools/review only; do not emit C1/C2 labels or CID trace comments in final TeX prose.",
            "Each final contribution bullet in Introduction should map conceptually to one cid in paper_state/alignment_matrix, but the bullet should use natural prose.",
            "Do not treat TODO or LLM_REVIEW_REQUIRED cells as validated scientific facts.",
        ],
    }


def normalize_section_id(section_id: str) -> str:
    key = section_id.strip().lower().replace(" ", "_")
    return SECTION_ALIASES.get(key, key)


def build_paper_state(
    *,
    outline_path: str,
    outline_text: str,
    index: dict[str, Any],
    section_plan: dict[str, Any],
    evidence_plan: dict[str, Any],
    figure_plan: dict[str, Any],
    alignment_matrix: dict[str, Any],
    alignment_matrix_path: str,
    section_outline_dir: str,
    target_venue: str,
) -> dict[str, Any]:
    metrics = index.get("result_metrics", []) if isinstance(index, dict) else []
    bib_keys = index.get("bib_keys", []) if isinstance(index, dict) else []
    citation_ref_by_note_id = index.get("citation_ref_by_note_id", {}) if isinstance(index, dict) else {}
    note_id_by_bib_key = index.get("note_id_by_bib_key", {}) if isinstance(index, dict) else {}
    unmapped_note_ids = index.get("unmapped_note_ids", []) if isinstance(index, dict) else []
    paper_note_cards = index.get("paper_note_cards", []) if isinstance(index, dict) else []
    sections: dict[str, dict[str, Any]] = {}
    planned_sections = {
        normalize_section_id(str(item.get("id", ""))): item
        for item in section_plan.get("sections", [])
        if isinstance(item, dict) and item.get("id")
    } if isinstance(section_plan, dict) else {}
    for section_id in SECTION_WRITING_SEQUENCE:
        plan_entry = planned_sections.get(section_id, {})
        sections[section_id] = {
            "status": "pending",
            "file": f"drafts/sections/{section_id}.tex",
            "outline": f"{section_outline_dir.rstrip('/')}/{section_id}.md",
            "required_inputs": list(plan_entry.get("required_inputs", [])),
            "available_inputs": list(plan_entry.get("available_inputs", [])),
            "missing_inputs": list(plan_entry.get("missing_inputs", [])),
            "expected_outputs": list(plan_entry.get("expected_outputs", [])),
            "writing_contract": plan_entry.get("writing_contract") if isinstance(plan_entry.get("writing_contract"), dict) else _section_writing_contract(section_id),
        }

    claim_slots = [
        {
            "slot_id": item.get("slot_id"),
            "section": normalize_section_id(str(item.get("section", ""))),
            "claim_type": item.get("claim_type"),
            "candidate_evidence": item.get("candidate_evidence", []),
            "result_metric_candidates": item.get("result_metric_candidates", []),
            "citation_pool": item.get("citation_pool", []),
        }
        for item in evidence_plan.get("claim_slots", [])
        if isinstance(item, dict)
    ] if isinstance(evidence_plan, dict) else []
    visuals = figure_plan.get("planned_visuals", []) if isinstance(figure_plan, dict) else []

    return {
        "version": "1.0",
        "semantics": "shared_state_for_section_by_section_writing_not_final_claims",
        "target_venue": target_venue,
        "outline": outline_path,
        "input_fingerprints": {},
        "section_order": SECTION_WRITING_SEQUENCE,
        "sections": sections,
        "current_section": None,
        "last_written_section": None,
        "shared_facts": {
            "source": "mechanical_candidates_from_artifacts; Writer LLM must verify wording",
            "title_candidates": _extract_title_candidates(outline_text),
            "bib_keys": bib_keys,
            "citation_ref_by_note_id": citation_ref_by_note_id if isinstance(citation_ref_by_note_id, dict) else {},
            "note_id_by_bib_key": note_id_by_bib_key if isinstance(note_id_by_bib_key, dict) else {},
            "unmapped_note_ids": unmapped_note_ids if isinstance(unmapped_note_ids, list) else [],
            "paper_note_cards": paper_note_cards if isinstance(paper_note_cards, list) else [],
            "result_metrics": metrics,
            "claim_slots": claim_slots,
            "planned_visuals": visuals,
            "alignment_matrix_path": alignment_matrix_path,
            "alignment_matrix": alignment_matrix.get("rows", []) if isinstance(alignment_matrix, dict) else [],
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "rules": [
            "Each T8-SEC-* task writes exactly one section file.",
            "Numbers must come from shared_facts.result_metrics or experiment artifacts.",
            "Citation keys must come from shared_facts.bib_keys.",
            "If a section needs a missing fact, read the source artifact and update the state with provenance rather than inventing it.",
            "Use alignment_matrix CIDs as section-to-section anchors; fill academic wording with LLM judgment, not tool guesses.",
            "Do not write visible C1/C2 labels or LaTeX CID trace comments in section TeX; keep traceability in paper_state/alignment_matrix.",
            "Tools maintain state and assembly; LLM writes section prose and scientific reasoning.",
        ],
        "revision_log": [],
    }


def build_paper_state_input_fingerprints(
    workspace: Path,
    paths: dict[str, str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, rel in paths.items():
        path = workspace / rel
        item: dict[str, Any] = {"path": rel, "exists": path.exists()}
        if path.exists() and path.is_file():
            item["sha256"] = _sha256_path(path)
        out[label] = item
    return out


def build_section_outlines(
    state: dict[str, Any],
    *,
    section_plan: dict[str, Any],
    evidence_plan: dict[str, Any],
    figure_plan: dict[str, Any],
    outline_text: str,
) -> dict[str, str]:
    plan_by_section = {
        normalize_section_id(str(item.get("id", ""))): item
        for item in section_plan.get("sections", [])
        if isinstance(item, dict) and item.get("id")
    } if isinstance(section_plan, dict) else {}
    slots_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_plan.get("claim_slots", []) if isinstance(evidence_plan, dict) else []:
        if not isinstance(item, dict):
            continue
        slots_by_section.setdefault(normalize_section_id(str(item.get("section", ""))), []).append(item)
    visuals_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in figure_plan.get("planned_visuals", []) if isinstance(figure_plan, dict) else []:
        if not isinstance(item, dict):
            continue
        visuals_by_section.setdefault(normalize_section_id(str(item.get("intended_section", ""))), []).append(item)
    alignment_rows = []
    shared = state.get("shared_facts", {}) if isinstance(state, dict) else {}
    if isinstance(shared, dict) and isinstance(shared.get("alignment_matrix"), list):
        alignment_rows = [item for item in shared.get("alignment_matrix", []) if isinstance(item, dict)]
    note_cards = shared.get("paper_note_cards", []) if isinstance(shared, dict) and isinstance(shared.get("paper_note_cards"), list) else []

    outlines: dict[str, str] = {}
    for section_id in SECTION_WRITING_SEQUENCE:
        plan_entry = plan_by_section.get(section_id, {})
        local_outline = _extract_outline_section(outline_text, section_id)
        lines = [
            f"# Section Outline: {SECTION_TITLES.get(section_id, section_id)}",
            "",
            "## Purpose",
            _section_purpose(section_id),
            "",
            "## Global Outline Notes",
            local_outline or "- No explicit local outline found; use the global outline and section plan.",
            "",
            "## Required Inputs",
        ]
        for path in plan_entry.get("required_inputs", []):
            lines.append(f"- `{path}`")
        if not plan_entry.get("required_inputs"):
            lines.append("- See `drafts/paper_state.json` and `drafts/section_plan.json`.")
        lines.extend(["", "## Expected Outputs"])
        for item in plan_entry.get("expected_outputs", []):
            lines.append(f"- {item}")
        if not plan_entry.get("expected_outputs"):
            lines.append("- Section prose that advances the paper argument without whole-document wrapper.")
        contract = plan_entry.get("writing_contract") if isinstance(plan_entry.get("writing_contract"), dict) else _section_writing_contract(section_id)
        lines.extend(["", "## Section Writing Contract"])
        lines.append(f"- purpose: {contract.get('purpose') or _section_purpose(section_id)}")
        lines.extend(_contract_items("required_content", contract.get("required_content")))
        lines.extend(_contract_items("internal_shape", contract.get("internal_shape")))
        lines.extend(_contract_items("evidence_rules", contract.get("evidence_rules")))
        lines.extend(_contract_items("avoid", contract.get("avoid")))
        lines.extend(["", "## CDR Responsibility"])
        lines.append(f"- {_section_cdr_responsibility(section_id)}")
        lines.append("- Use `drafts/cdr_claim_ledger.json` as a ledger seed; do not treat it as final prose.")
        lines.extend(["", "## Internal Alignment IDs"])
        responsible = _responsible_cids_for_section(section_id, alignment_rows)
        if responsible:
            for item in responsible:
                cid = item.get("cid")
                column = item.get("column")
                value = item.get("value")
                lines.append(f"- `{cid}`: internal `{column}` alignment lane; current seed = {value!r}")
        else:
            lines.append("- No seeded internal alignment id found; if the paper has a contribution, read `drafts/alignment_matrix.json` and fill the missing mapping.")
        lines.extend(["", "## Note Card Retrieval Plan"])
        lines.extend(_note_card_retrieval_lines(section_id, note_cards))
        lines.extend(["", "## Claim Slots"])
        for slot in slots_by_section.get(section_id, []):
            lines.append(
                "- `{}` ({}) cdr_field={} evidence={} citations={}".format(
                    slot.get("slot_id"),
                    slot.get("claim_type", "claim"),
                    slot.get("cdr_field", ""),
                    slot.get("candidate_evidence", []),
                    slot.get("citation_pool", []),
                )
            )
        if not slots_by_section.get(section_id):
            lines.append("- No direct claim slot; avoid adding unsupported new claims.")
        lines.extend(["", "## Figure/Table Slots"])
        for visual in visuals_by_section.get(section_id, []):
            vid = visual.get("figure_id") or visual.get("table_id") or "visual"
            lines.append(
                f"- `{vid}` status={visual.get('status')} sources={visual.get('source_artifacts', [])}"
            )
        if not visuals_by_section.get(section_id):
            lines.append("- No planned visual required for this section.")
        lines.extend(
            [
                "",
                "## Writing Rules",
                "- Write only this section.",
                "- Do not include `\\documentclass`, `\\begin{document}`, or `\\end{document}`.",
                "- Use only BibTeX keys and result numbers traceable through `paper_state.json` or source artifacts.",
                "- Do not print internal CIDs such as `C1`, `[C1]`, or `% [C1]` in this TeX section; use natural prose and keep traceability in `paper_state.json` / `alignment_matrix.json`.",
                "- Keep CDR fields as reasoning scaffolds; the Writer LLM must decide the final scientific wording.",
                "- For unsupported material, delete or weaken the claim in final TeX, or state the evidence boundary in natural-language limitations; do not emit literal TODO/TBD placeholders.",
                "",
            ]
        )
        outlines[section_id] = "\n".join(lines)
    return outlines


def _note_card_retrieval_lines(section_id: str, note_cards: list[Any]) -> list[str]:
    section_targets = {
        "introduction": [
            "Use note cards for problem framing, high-quality gap evidence, and nearest prior work.",
            "Inspect note sections: §6 Relevance, §9 Weaknesses / Gaps, §13 Mechanism Claim, §18 Boundary Conditions, §19 Cross-Paper Tension.",
        ],
        "related_work": [
            "Use note cards to build rationale streams and citation-backed contrasts instead of relying only on synthesis.md.",
            "Inspect note sections: §2 Method Overview, §6 Relevance, §9 Weaknesses / Gaps, §13 Mechanism Claim, §14 Design Rationale, §18 Boundary Conditions, §19 Cross-Paper Tension.",
        ],
        "methodology": [
            "Use note cards only for design precedents and rejected alternatives; do not cite prior work as evidence for this paper's results.",
            "Inspect note sections: §2 Method Overview, §14 Design Rationale, §15 Artifact & Design Principles, §18 Boundary Conditions.",
        ],
        "experiments": [
            "Use note cards for baseline, metric, dataset, and protocol context; all reported numbers still need experiment artifacts.",
            "Inspect note sections: §3 Key Results, §12 Reading Coverage, §16 Data View & Evaluation Mode.",
        ],
        "analysis": [
            "Use note cards to interpret mechanisms, alternative explanations, and boundary conditions.",
            "Inspect note sections: §13 Mechanism Claim, §14 Design Rationale, §18 Boundary Conditions, §19 Cross-Paper Tension.",
        ],
        "conclusion": [
            "Use note cards only to restate established boundaries and future work; do not introduce new citations or new claims.",
            "Inspect note sections: §9 Weaknesses / Gaps and §18 Boundary Conditions if limitations need grounding.",
        ],
        "abstract": [
            "Do not cite note cards in the abstract. Use them only to verify that the abstract does not introduce unsupported claims.",
        ],
    }
    lines = list(section_targets.get(section_id) or ["Use paper note cards only when they directly support this section's claim."])
    cards = _section_note_cards(section_id, note_cards, limit=8)
    if not cards:
        if any(isinstance(card, dict) for card in note_cards):
            lines.append("- Indexed note cards exist, but none meet the claim-usable threshold for this section; read `literature/paper_notes/` directly and use weak cards only as background or limitations.")
        else:
            lines.append("- No structured note cards are indexed; read `literature/paper_notes/` and `literature/synthesis_workbench.json` directly if citations are needed.")
        return [f"- {line}" if not line.lstrip().startswith("-") else line for line in lines]
    lines.append("- Relevant indexed note cards:")
    fields = _note_card_fields_for_section(section_id)
    for card in cards:
        citation = str(card.get("citation_ref") or "").strip() or f"[note:{card.get('note_id')}]"
        title = _shorten(card.get("title"), 110)
        score = card.get("citation_quality_score")
        use = card.get("citation_use") or "unknown"
        evidence = card.get("evidence_level") or "unknown"
        path = card.get("path") or ""
        lines.append(f"  - {citation} {title} | evidence={evidence} | use={use} | score={score} | note={path}")
        for cue in _note_card_section_cues(card, fields, limit=2):
            lines.append(f"    - {cue}")
    lines.append("- Before using a citation, read the matching note section and verify that the sentence-level claim matches the note evidence.")
    return [f"- {line}" if not line.lstrip().startswith("-") else line for line in lines]


def _note_card_fields_for_section(section_id: str) -> tuple[str, ...]:
    return {
        "introduction": ("problem_motivation", "gaps", "mechanism_claim", "boundary_conditions"),
        "related_work": (
            "method_overview",
            "core_approach_view",
            "bridge_point",
            "gaps",
            "design_rationale",
            "cross_paper_tension",
        ),
        "methodology": ("method_overview", "core_approach_view", "design_rationale", "artifact_design", "boundary_conditions"),
        "experiments": ("key_results", "reading_coverage", "data_view"),
        "analysis": ("mechanism_claim", "design_rationale", "boundary_conditions", "cross_paper_tension"),
        "conclusion": ("gaps", "boundary_conditions", "cross_paper_tension"),
    }.get(section_id, ("method_overview", "gaps", "mechanism_claim"))


def _note_card_section_cues(card: dict[str, Any], fields: tuple[str, ...], *, limit: int) -> list[str]:
    labels = {
        "problem_motivation": "§1",
        "method_overview": "§2",
        "core_approach_view": "A",
        "bridge_point": "B",
        "key_results": "§3",
        "gaps": "§9",
        "reading_coverage": "§12",
        "mechanism_claim": "§13",
        "design_rationale": "§14",
        "artifact_design": "§15",
        "data_view": "§16",
        "boundary_conditions": "§18",
        "cross_paper_tension": "§19",
        "raw_abstract": "Raw abstract",
    }
    cues: list[str] = []
    for field in fields:
        value = _shorten(card.get(field), 150)
        if not value:
            continue
        cues.append(f"{labels.get(field, field)}: {value}")
        if len(cues) >= limit:
            break
    return cues


def _section_note_cards(section_id: str, note_cards: list[Any], *, limit: int) -> list[dict[str, Any]]:
    cards = [
        card
        for card in note_cards
        if isinstance(card, dict) and _paper_note_card_claim_usable(card)
    ]
    if not cards:
        return []
    if section_id == "abstract":
        return []
    fields = _note_card_fields_for_section(section_id)

    def score(card: dict[str, Any]) -> tuple[float, float, str]:
        text_bonus = sum(1 for field in fields if str(card.get(field) or "").strip())
        evidence_penalty = 0.5 if str(card.get("evidence_level") or "") == "ABSTRACT_ONLY" else 0.0
        quality = float(card.get("citation_quality_score") or 0.0)
        has_cite = 0.2 if str(card.get("citation_ref") or "").startswith("\\cite") else 0.0
        return (text_bonus + quality + has_cite - evidence_penalty, quality, str(card.get("title") or ""))

    ranked = sorted(cards, key=score, reverse=True)
    return ranked[:limit]


def _section_writing_contract(section_id: str) -> dict[str, Any]:
    return dict(SECTION_WRITING_CONTRACTS.get(section_id) or {})


def _contract_items(label: str, raw_items: object) -> list[str]:
    items = [str(item).strip() for item in raw_items or [] if str(item).strip()] if isinstance(raw_items, list) else []
    if not items:
        return [f"- {label}: unspecified"]
    return [f"- {label}:"] + [f"  - {item}" for item in items]


def assemble_sections(
    section_dir: Path,
    *,
    title: str = "",
    target_venue: str = "",
    outline_text: str = "",
    venue_style: str = "auto",
    template_family: str = "",
    template_id: str = "",
    writing_language: str = "auto",
) -> str:
    if not section_dir.exists():
        raise FileNotFoundError(section_dir)
    section_texts: dict[str, str] = {}
    for name in CORE_SECTIONS:
        for suffix in (".tex", ".md"):
            path = section_dir / f"{name}{suffix}"
            if path.exists():
                section_texts[name] = _strip_document_wrappers(path.read_text(encoding="utf-8"))
                break
    legacy_limitations = ""
    for suffix in (".tex", ".md"):
        legacy_path = section_dir / f"limitations{suffix}"
        if legacy_path.exists():
            legacy_limitations = _strip_document_wrappers(legacy_path.read_text(encoding="utf-8"))
            break
    missing = [name for name in CORE_SECTIONS if name not in section_texts and name not in {"analysis"}]
    if missing:
        raise FileNotFoundError(f"missing section drafts: {', '.join(missing)}")

    title = title or _extract_title(outline_text) or "ResearchOS Manuscript Draft"
    body_parts = []
    abstract = _strip_abstract_section_markup(section_texts.get("abstract", "")).strip()
    for name in ["introduction", "related_work", "methodology", "experiments", "analysis", "conclusion"]:
        text = section_texts.get(name, "").strip()
        if not text:
            continue
        if name == "conclusion":
            text = _merge_legacy_limitations_into_conclusion(text, legacy_limitations)
        if not re.search(r"\\section\*?\{", text):
            heading = _latex_section_title(name)
            text = f"\\section{{{heading}}}\n{text}"
        body_parts.append(text)

    body = _manuscript_document_body(title=title, abstract=abstract, body_parts=body_parts, bib_stem="related_work")
    family = str(template_family or "").strip().lower()
    template = str(template_id or "").strip().lower()
    language = str(writing_language or "auto").strip().lower()
    template_path = _resolve_latex_template(_repo_root(), family, template, language)
    if template_path and template_path.exists():
        template_text = template_path.read_text(encoding="utf-8", errors="replace")
        if _is_informs_template(template_path, template_text):
            rendered = _render_informs_document(
                template_text,
                title=title,
                abstract=abstract,
                body_parts=body_parts,
                bib_stem="related_work",
            )
        elif _is_ccf_template(template_path, "neurips"):
            rendered = _render_neurips_document(
                title=title,
                abstract=abstract,
                body_parts=body_parts,
                bib_stem="related_work",
            )
        elif _is_ccf_template(template_path, "icml"):
            rendered = _render_icml_document(
                title=title,
                abstract=abstract,
                body_parts=body_parts,
                bib_stem="related_work",
            )
        elif _is_ccf_template(template_path, "iclr"):
            rendered = _render_iclr_document(
                title=title,
                abstract=abstract,
                body_parts=body_parts,
                bib_stem="related_work",
            )
        else:
            rendered = _replace_template_document_body(template_text, body)
    else:
        rendered = _fallback_manuscript_document(
            title=title,
            abstract=abstract,
            body_parts=body_parts,
            writing_language=language,
            template_family=family,
            bib_stem="related_work",
        )
    return rendered


def _manuscript_document_body(*, title: str, abstract: str, body_parts: list[str], bib_stem: str) -> str:
    return (
        f"\\title{{{_escape_latex_braces(title)}}}\n"
        "\\author{}\n"
        "\\maketitle\n"
        f"\\begin{{abstract}}\n{abstract}\n\\end{{abstract}}\n\n"
        + "\n\n".join(body_parts)
        + f"\n\n\\bibliographystyle{{plainnat}}\n\\bibliography{{{bib_stem}}}\n"
    )


def _fallback_manuscript_document(
    *,
    title: str,
    abstract: str,
    body_parts: list[str],
    writing_language: str,
    template_family: str,
    bib_stem: str,
) -> str:
    is_zh = writing_language == "zh" or template_family == "basic_zh"
    documentclass = "\\documentclass{article}\n"
    cjk_packages = (
        "\\usepackage{iftex}\n"
        "\\usepackage{newunicodechar}\n"
        "\\ifXeTeX\n"
        "  \\usepackage{fontspec}\n"
        "  \\usepackage{xeCJK}\n"
        "  \\IfFontExistsTF{Noto Serif CJK SC}{\\setCJKmainfont{Noto Serif CJK SC}[ItalicFont=Noto Serif CJK SC, ItalicFeatures={FakeSlant=0.2}]}{}\n"
        "  \\IfFontExistsTF{Noto Sans CJK SC}{\\setCJKsansfont{Noto Sans CJK SC}}{}\n"
        "  \\IfFontExistsTF{Noto Serif CJK SC}{\\setCJKmonofont{Noto Serif CJK SC}}{}\n"
        "\\fi\n"
        "\\newunicodechar{≠}{\\ensuremath{\\ne}}\n"
        "\\newunicodechar{≤}{\\ensuremath{\\le}}\n"
        "\\newunicodechar{≥}{\\ensuremath{\\ge}}\n"
        "\\newunicodechar{×}{\\ensuremath{\\times}}\n"
        "\\newunicodechar{→}{\\ensuremath{\\to}}\n"
        "\\newunicodechar{←}{\\ensuremath{\\leftarrow}}\n"
        "\\newunicodechar{–}{--}\n"
        "\\newunicodechar{—}{---}\n"
        if is_zh
        else ""
    )
    return (
        documentclass
        + "\\usepackage{graphicx}\n"
        + "\\usepackage{amsmath}\n"
        + "\\usepackage{booktabs}\n"
        + "\\usepackage{natbib}\n"
        + cjk_packages
        + "\\usepackage{hyperref}\n"
        + "\\begin{document}\n"
        + _manuscript_document_body(title=title, abstract=abstract, body_parts=body_parts, bib_stem=bib_stem)
        + "\\end{document}\n"
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_latex_template(repo_root: Path, family: str, template_id: str, writing_language: str) -> Path | None:
    base = repo_root / "latex_templete"
    family = str(family or "").strip().lower()
    template_id = str(template_id or "").strip().lower()
    writing_language = str(writing_language or "").strip().lower()
    candidates: list[Path] = []
    if family == "basic_zh":
        candidates.append(base / "normal" / "basic_zh.tex")
    elif family == "basic_en":
        candidates.append(base / "normal" / "basic_en.tex")
    elif family == "utd":
        tid = template_id or "informs"
        if tid in {"informs", "mnsc", "isre", "isr", "ijds"}:
            candidates.append(
                base
                / "utd"
                / "informs"
                / "INFORMS-ISRE-Template-6-10-2024"
                / "INFORMS-ISRE-Template.tex"
            )
            candidates.append(base / "utd" / "informs" / "informs_fallback.tex")
        candidates.append(base / "utd" / "informs_basic.tex")
    elif family == "ccf":
        tid = template_id or "neurips"
        if tid == "neurips":
            candidates.append(base / "ccf-latex-templates" / "NeurIPS" / "neurips_2026.tex")
        elif tid == "kdd":
            candidates.append(base / "ccf-latex-templates" / "SIGKDD" / "kdd_basic.tex")
            candidates.extend((base / "ccf-latex-templates" / "SIGKDD").glob("*.tex"))
        elif tid == "icml":
            candidates.append(base / "ccf-latex-templates" / "ICML" / "example_paper.tex")
        elif tid == "iclr":
            candidates.append(base / "ccf-latex-templates" / "ICLR" / "iclr2026_basic.tex")
            candidates.append(base / "ccf-latex-templates" / "ICLR" / "iclr2026_conference.sty")
    if not candidates:
        candidates.append(base / "normal" / ("basic_zh.tex" if writing_language == "zh" else "basic_en.tex"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _replace_template_document_body(template: str, body: str) -> str:
    match = re.search(r"\\begin\{document\}", template or "", flags=re.IGNORECASE)
    if not match:
        return template.strip() + "\n\n" + body.strip() + "\n"
    preamble = template[: match.start()]
    rest = template[match.end() :]
    end_match = re.search(r"\\end\{document\}", rest, flags=re.IGNORECASE)
    suffix = rest[end_match.end() :] if end_match else ""
    preamble = re.sub(r"(?ms)^\\title\{.*?\}\s*", "", preamble)
    preamble = re.sub(r"(?ms)^\\author\{.*?\}\s*", "", preamble)
    preamble = re.sub(r"(?m)^\\date\{.*?\}\s*", "", preamble)
    preamble, bib_style = _extract_template_bib_style(preamble, rest)
    body = _set_document_bibliography(
        body,
        bib_stem="related_work",
        bib_style=bib_style or "plainnat",
    )
    return preamble.rstrip() + "\n\n\\begin{document}\n" + body.strip() + "\n\\end{document}" + suffix


def _is_informs_template(template_path: Path | None, template_text: str) -> bool:
    path_text = template_path.as_posix().lower() if template_path else ""
    return "\\documentclass" in template_text and "informs4" in template_text and "/utd/informs/" in path_text


def _is_ccf_template(template_path: Path | None, template_id: str) -> bool:
    if not template_path:
        return False
    path_text = template_path.as_posix().lower()
    aliases = {
        "neurips": "/ccf-latex-templates/neurips/",
        "kdd": "/ccf-latex-templates/sigkdd/",
        "icml": "/ccf-latex-templates/icml/",
        "iclr": "/ccf-latex-templates/iclr/",
    }
    return aliases.get(template_id, "") in path_text


def _render_informs_document(
    template: str,
    *,
    title: str,
    abstract: str,
    body_parts: list[str],
    bib_stem: str,
) -> str:
    preamble, begin_cmd, rest = _split_template_at_begin_document(template)
    if not begin_cmd:
        return template.strip() + "\n\n" + _manuscript_document_body(
            title=title,
            abstract=abstract,
            body_parts=body_parts,
            bib_stem=bib_stem,
        )
    preamble = _prepare_informs_preamble(preamble)
    title_tex = _escape_latex_braces(title or "ResearchOS Manuscript Draft")
    short_title = _short_latex_running_text(title or "ResearchOS Draft", limit=72)
    abstract_tex = _strip_abstract_section_markup(abstract).strip() or "Abstract text."
    body = "\n\n".join(part.strip() for part in body_parts if part.strip())
    return (
        preamble.rstrip()
        + "\n\n\\begin{document}\n\n"
        + "\\RUNAUTHOR{Anonymous Author(s)}\n"
        + f"\\RUNTITLE{{{short_title}}}\n"
        + f"\\TITLE{{{title_tex}}}\n\n"
        + "\\ARTICLEAUTHORS{%\n"
        + "\\AUTHOR{Anonymous Author(s)}\n"
        + "\\AFF{Affiliation omitted for review}\n"
        + "}\n\n"
        + "\\ABSTRACT{%\n"
        + abstract_tex
        + "\n}%\n\n"
        + "\\KEYWORDS{ResearchOS draft, literature review, information systems}\n\n"
        + "\\maketitle\n\n"
        + body
        + f"\n\n\\bibliographystyle{{informs2014}}\n\\bibliography{{{bib_stem}}}\n\n"
        + "\\end{document}\n"
    )


def _render_neurips_document(
    *,
    title: str,
    abstract: str,
    body_parts: list[str],
    bib_stem: str,
) -> str:
    title_tex = _escape_latex_braces(title or "ResearchOS Manuscript Draft")
    abstract_tex = _strip_abstract_section_markup(abstract).strip() or "Abstract text."
    body = "\n\n".join(part.strip() for part in body_parts if part.strip())
    return (
        "\\documentclass{article}\n\n"
        "\\usepackage{neurips_2026}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{url}\n"
        "\\usepackage{booktabs}\n"
        "\\usepackage{amsfonts}\n"
        "\\usepackage{nicefrac}\n"
        "\\usepackage{microtype}\n"
        "\\usepackage{xcolor}\n\n"
        f"\\title{{{title_tex}}}\n"
        "\\author{Anonymous Author(s)}\n\n"
        "\\begin{document}\n\n"
        "\\maketitle\n\n"
        f"\\begin{{abstract}}\n{abstract_tex}\n\\end{{abstract}}\n\n"
        + body
        + f"\n\n\\bibliographystyle{{plainnat}}\n\\bibliography{{{bib_stem}}}\n\n"
        "\\end{document}\n"
    )


def _render_icml_document(
    *,
    title: str,
    abstract: str,
    body_parts: list[str],
    bib_stem: str,
) -> str:
    title_tex = _escape_latex_braces(title or "ResearchOS Manuscript Draft")
    short_title = _short_latex_running_text(title or "ResearchOS Draft", limit=64)
    abstract_tex = _strip_abstract_section_markup(abstract).strip() or "Abstract text."
    body = "\n\n".join(part.strip() for part in body_parts if part.strip())
    return (
        "\\documentclass{article}\n\n"
        "\\usepackage{microtype}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{subcaption}\n"
        "\\usepackage{booktabs}\n"
        "\\usepackage{hyperref}\n"
        "\\newcommand{\\theHalgorithm}{\\arabic{algorithm}}\n"
        "\\usepackage{icml2026}\n"
        "\\usepackage{amsmath}\n"
        "\\usepackage{amssymb}\n"
        "\\usepackage{mathtools}\n"
        "\\usepackage{amsthm}\n"
        "\\usepackage[capitalize,noabbrev]{cleveref}\n\n"
        f"\\icmltitlerunning{{{short_title}}}\n\n"
        "\\begin{document}\n\n"
        "\\twocolumn[\n"
        f"  \\icmltitle{{{title_tex}}}\n"
        "  \\begin{icmlauthorlist}\n"
        "    \\icmlauthor{Anonymous Author(s)}{anon}\n"
        "  \\end{icmlauthorlist}\n"
        "  \\icmlaffiliation{anon}{Affiliation omitted for review}\n"
        "  \\icmlcorrespondingauthor{Anonymous Author}{anon@example.com}\n"
        "  \\icmlkeywords{ResearchOS draft, machine learning}\n"
        "  \\vskip 0.3in\n"
        "]\n\n"
        "\\printAffiliationsAndNotice{}\n\n"
        f"\\begin{{abstract}}\n{abstract_tex}\n\\end{{abstract}}\n\n"
        + body
        + f"\n\n\\bibliography{{{bib_stem}}}\n\\bibliographystyle{{icml2026}}\n\n"
        "\\end{document}\n"
    )


def _render_iclr_document(
    *,
    title: str,
    abstract: str,
    body_parts: list[str],
    bib_stem: str,
) -> str:
    title_tex = _escape_latex_braces(title or "ResearchOS Manuscript Draft")
    abstract_tex = _strip_abstract_section_markup(abstract).strip() or "Abstract text."
    body = "\n\n".join(part.strip() for part in body_parts if part.strip())
    return (
        "\\documentclass{article}\n\n"
        "\\usepackage{times}\n"
        "\\usepackage{iclr2026_conference}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{url}\n"
        "\\usepackage{booktabs}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{amsmath}\n"
        "\\usepackage{amssymb}\n\n"
        f"\\title{{{title_tex}}}\n"
        "\\author{Anonymous Author(s)}\n\n"
        "\\begin{document}\n\n"
        "\\maketitle\n\n"
        f"\\begin{{abstract}}\n{abstract_tex}\n\\end{{abstract}}\n\n"
        + body
        + f"\n\n\\bibliographystyle{{plainnat}}\n\\bibliography{{{bib_stem}}}\n\n"
        "\\end{document}\n"
    )


def _split_template_at_begin_document(template: str) -> tuple[str, str, str]:
    match = re.search(r"\\begin\{document\}", template or "", flags=re.IGNORECASE)
    if not match:
        return template, "", ""
    return template[: match.start()], match.group(0), template[match.end() :]


def _prepare_informs_preamble(preamble: str) -> str:
    cleaned = re.sub(
        r"\\documentclass\[[^\]]*\]\{informs4\}",
        r"\\documentclass[isre,dblanonrev]{informs4}",
        preamble or "",
        count=1,
    )
    cleaned = re.sub(r"(?m)^\\MANUSCRIPTNO\{[^}]*\}", r"\\MANUSCRIPTNO{}", cleaned)
    cleaned = re.sub(
        r"(?m)^\\RequirePackage\{(?:tgtermes|newtxtext|newtxmath)\}\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?m)^\\usepackage\{(?:algorithm|algpseudocode)\}\s*",
        "",
        cleaned,
    )
    return cleaned


def _short_latex_running_text(value: str, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip() or text[:limit].rstrip()
    return _escape_latex_braces(text)


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


def _copy_latex_template_support_files(template_path: Path | None, target_dir: Path) -> None:
    if not template_path or not template_path.exists():
        return
    for source in _template_support_sources(template_path):
        if not _is_template_support_file(source):
            continue
        try:
            (target_dir / source.name).write_bytes(source.read_bytes())
        except OSError:
            continue


def _template_support_sources(template_path: Path) -> list[Path]:
    support = list(template_path.parent.iterdir())
    if _is_ccf_template(template_path, "iclr") and template_path.suffix.lower() == ".sty":
        shell = template_path.parent / "iclr2026_basic.tex"
        if shell.exists():
            support.append(shell)
    return support


def _is_template_support_file(source: Path) -> bool:
    suffix = source.suffix.lower()
    if suffix in {".sty", ".cls", ".bst"}:
        return True
    if source.name in {"checklist.tex", "iclr2026_basic.tex"}:
        return True
    return source.stem.lower() == "informs_logo" and suffix in {".pdf", ".eps"}


def _copy_manuscript_bibliography(
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
    try:
        target_path.write_text(
            strip_internal_bibtex_notes(bib_path.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8",
        )
    except OSError:
        return


_SUPPORT_FILE_COMMAND_RE = re.compile(
    r"\\(?:bibliographystyle|usepackage|documentclass|input|include)(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}"
)
_LATEX_SUPPORT_SUFFIXES = {".bst", ".cls", ".sty"}


def _copy_submission_latex_support_files(
    policy: WorkspaceAccessPolicy,
    *,
    tex: str,
    source_dir: Path,
    bundle_dir: str,
) -> list[str]:
    copied: list[str] = []
    for source in _submission_latex_support_candidates(tex, source_dir):
        try:
            source_rel = source.resolve().relative_to(policy.workspace_dir.resolve()).as_posix()
            checked = policy.resolve_read(source_rel)
        except (ValueError, ToolAccessDenied):
            continue
        if not checked.exists() or not checked.is_file():
            continue
        target_rel = f"{bundle_dir}/{checked.name}"
        target = policy.resolve_write(target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(checked.read_bytes())
        if target_rel not in copied:
            copied.append(target_rel)
    return copied


def _submission_latex_support_candidates(tex: str, source_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for match in _SUPPORT_FILE_COMMAND_RE.finditer(tex or ""):
        command = match.group(0)
        names = [item.strip() for item in match.group(1).split(",") if item.strip()]
        for name in names:
            if not name or "/" in name or "\\" in name:
                continue
            suffixes: list[str]
            if "\\bibliographystyle" in command:
                suffixes = [".bst"]
            elif "\\documentclass" in command:
                suffixes = [".cls"]
            else:
                suffixes = [".sty"]
            raw = Path(name)
            if raw.suffix.lower() in _LATEX_SUPPORT_SUFFIXES:
                paths = [source_dir / raw]
            else:
                paths = [source_dir / f"{name}{suffix}" for suffix in suffixes]
            for path in paths:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(path)
            for path in _transitive_latex_support_candidates(name, source_dir):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(path)
    if re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{informs4\}", tex or ""):
        for name in ("informs_Logo.pdf", "informs_Logo.eps"):
            path = source_dir / name
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(path)
    return candidates


def _transitive_latex_support_candidates(name: str, source_dir: Path) -> list[Path]:
    stem = Path(name).stem
    if stem == "icml2026":
        return [
            source_dir / "algorithm.sty",
            source_dir / "algorithmic.sty",
            source_dir / "fancyhdr.sty",
            source_dir / "natbib.sty",
            source_dir / "icml2026.bst",
        ]
    if stem == "neurips_2026":
        return [source_dir / "checklist.tex"]
    if stem == "iclr2026_conference":
        return [source_dir / "iclr2026_basic.tex"]
    return []


def _write_style_variant_manuscripts(
    policy: WorkspaceAccessPolicy,
    assembled: str,
    *,
    venue_style: str,
    target_venue: str,
) -> list[str]:
    if venue_style != "both":
        return []
    outputs: list[str] = []
    for style in ("is", "ccf_a"):
        rel_path = f"drafts/{style}/paper.tex"
        path = policy.resolve_write(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_annotate_style_variant(assembled, style=style, target_venue=target_venue), encoding="utf-8")
        outputs.append(rel_path)
    return outputs


def _annotate_style_variant(text: str, *, style: str, target_venue: str) -> str:
    comment = (
        f"% ResearchOS style variant: {style}\n"
        f"% Target venue: {target_venue or 'unspecified'}\n"
        "% This variant shares the same alignment matrix and section sources; "
        "Writer/Reviewer should revise density and framing for this style.\n"
    )
    return text.replace("\\documentclass", comment + "\\documentclass", 1)


def audit_manuscript(paper: str, index: dict[str, Any]) -> str:
    bib_keys = set(index.get("bib_keys", []))
    cited = _extract_latex_cites(paper)
    missing_cites = sorted(cited - bib_keys)
    numbers = sorted(set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:%|x|×|ms|s|h|GPU-h)?", paper)))
    result_values = {
        str(item.get("value"))
        for item in index.get("result_metrics", [])
        if item.get("value") is not None
    }
    unsupported_numbers = [num for num in numbers if not any(num.strip().startswith(val) for val in result_values)]
    figure_refs = sorted(set(re.findall(r"\\ref\{fig:([^}]+)\}", paper)))
    table_refs = sorted(set(re.findall(r"\\ref\{tab:([^}]+)\}", paper)))
    section_titles = [title.lower() for title in re.findall(r"\\section\*?\{([^}]+)\}", paper)]
    section_aliases = {
        "Introduction": ["introduction"],
        "Related Work": ["related work", "literature review", "background"],
        "Method": ["method", "methodology", "approach", "model"],
        "Experiments": ["experiments", "evaluation", "results"],
        "Conclusion": ["conclusion", "conclusions"],
    }
    missing_sections = [
        sec
        for sec, aliases in section_aliases.items()
        if not any(alias in title for title in section_titles for alias in aliases)
    ]

    lines = ["# Manuscript Mechanical Audit", "", "该报告是机械审计 hint，不是最终学术判断。", ""]
    lines.append("## Citation Keys")
    if missing_cites:
        for key in missing_cites:
            lines.append(f"- [ ] Missing BibTeX key: `{key}`")
    else:
        lines.append("- [x] All detected citation keys exist in related_work.bib")

    lines.append("\n## Numeric Values")
    if unsupported_numbers:
        for num in unsupported_numbers[:30]:
            lines.append(f"- [ ] Verify numeric value appears in results artifacts or justify as non-result number: `{num}`")
    else:
        lines.append("- [x] Detected numeric values match indexed result values or no numbers detected")

    lines.append("\n## Figures And Tables")
    lines.append(f"- Detected figure refs: {figure_refs or 'none'}")
    lines.append(f"- Detected table refs: {table_refs or 'none'}")
    if not index.get("figures"):
        lines.append("- [ ] No figure files indexed; decide whether paper needs generated plots/schematics.")
    if not index.get("tables"):
        lines.append("- [ ] No table files indexed beyond CSV artifacts; ensure LaTeX tables are generated from results.")

    lines.append("\n## Required Sections")
    if missing_sections:
        for sec in missing_sections:
            lines.append(f"- [ ] Missing or nonstandard section: {sec}")
    else:
        lines.append("- [x] Core sections detected")
    lines.append("")
    return "\n".join(lines)


def audit_writing_craft(
    *,
    paper: str,
    section_texts: dict[str, str],
    related_work_bib: str = "",
    support_text_by_key: dict[str, str] | None = None,
    paper_state: dict[str, Any],
    alignment_matrix: dict[str, Any],
    cdr_ledger: dict[str, Any],
    venue_style: str,
) -> dict[str, Any]:
    rows = _alignment_rows(alignment_matrix, paper_state)
    cids = [str(row.get("cid")) for row in rows if str(row.get("cid") or "").strip()]
    checks: list[dict[str, Any]] = []

    def add(name: str, level: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "level": "PASS" if passed else level,
                "passed": passed,
                "detail": detail,
            }
        )

    contribution_chains = cdr_ledger.get("contribution_chains", []) if isinstance(cdr_ledger, dict) else []
    intro = section_texts.get("introduction", "")
    related = section_texts.get("related_work", "")
    experiments = section_texts.get("experiments", "")
    analysis = section_texts.get("analysis", "")
    conclusion = section_texts.get("conclusion", "")
    raw_abstract = section_texts.get("abstract", "")
    abstract = _strip_abstract_section_markup(raw_abstract)
    contribution_count = _count_intro_contributions(intro)
    gap_count = _count_intro_gaps(intro)

    expected_count = len(contribution_chains) if isinstance(contribution_chains, list) and contribution_chains else len(cids)
    add(
        "matrix_row_count",
        "FAIL",
        bool(rows)
        and 3 <= len(rows) <= 6
        and (expected_count == 0 or len(rows) == expected_count),
        (
            f"matrix rows={len(rows)}, contribution lanes={expected_count}, "
            f"intro contribution bullets={contribution_count}"
        ),
    )
    add(
        "intro_contribution_count",
        "WARN",
        3 <= contribution_count <= 6,
        f"detected introduction contribution bullets={contribution_count}; suggested range 3-6; reviewer/LLM should verify prose if 0",
    )
    if gap_count:
        add("intro_gap_count", "WARN", gap_count <= 3, f"detected gap/motivation markers={gap_count}; target <=3")
        add(
            "intro_gap_eq_contribution",
            "WARN",
            contribution_count == 0 or gap_count == contribution_count,
            f"gap/motivation markers={gap_count}, contribution bullets={contribution_count}",
        )
    else:
        add("intro_gap_count", "WARN", True, "no explicit numbered gap markers detected; reviewer/LLM should verify prose")

    add(
        "no_standalone_limitations",
        "FAIL",
        not re.search(r"\\section\*?\{\s*Limitations\s*\}", paper, flags=re.IGNORECASE),
        "Limitations must be a Conclusion subsection, not an independent section.",
    )
    add(
        "conclusion_has_limitations_subsection",
        "FAIL",
        bool(re.search(r"\\subsection\*?\{\s*Limitations\s*\}", conclusion, flags=re.IGNORECASE)),
        "Conclusion must include \\subsection{Limitations}.",
    )
    add(
        "abstract_no_cite",
        "FAIL",
        not has_formal_citation(abstract),
        "Abstract must not contain formal citations; cite prior work in Introduction or Related Work.",
    )
    add(
        "abstract_no_section_heading",
        "FAIL",
        not re.search(r"\\(?:section|subsection)\*?\{", raw_abstract, flags=re.IGNORECASE),
        "Abstract text should be plain abstract prose, not a section/subsection heading.",
    )
    citation_density_issues = _manuscript_section_citation_issues(section_texts)
    add(
        "section_level_citation_density",
        "FAIL",
        not citation_density_issues,
        (
            "Citation density issues: " + "; ".join(citation_density_issues)
            if citation_density_issues
            else "Claim-bearing sections meet minimum unique citation counts."
        ),
    )
    citation_alignment = citation_alignment_issues(
        tex=paper,
        bibtex=related_work_bib,
        support_text_by_key=support_text_by_key,
    )
    add(
        "citation_claim_alignment",
        "FAIL",
        not citation_alignment,
        (
            "Citation/claim alignment issues: " + "; ".join(citation_alignment[:8])
            if citation_alignment
            else "Citation contexts are topically aligned with cited BibTeX titles, paper-note support text, or explicit evidence boundaries."
        ),
    )
    internal_label_hits = _internal_label_leakages(paper, cids)
    add(
        "no_internal_label_leakage",
        "FAIL",
        not internal_label_hits,
        (
            "Final TeX should not expose internal alignment IDs such as C1/[C1] or CID trace comments; "
            f"hits={internal_label_hits[:8]}"
            if internal_label_hits
            else "No internal C1/C2/CID labels detected in final TeX."
        ),
    )
    placeholder_hits = _placeholder_hits(paper)
    add(
        "no_placeholder_tokens",
        "FAIL",
        not placeholder_hits,
        (
            "Final TeX still contains planning placeholders: " + ", ".join(placeholder_hits[:10])
            if placeholder_hits
            else "No TODO/TBD/LLM_REVIEW_REQUIRED/PLACEHOLDER tokens detected in final TeX."
        ),
    )
    word_count = len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", abstract))
    if venue_style == "is":
        passed = 200 <= word_count <= 300
        target = "200-300"
    else:
        passed = 150 <= word_count <= 300
        target = "150-300"
    add("abstract_wordcount", "WARN", passed or word_count == 0, f"abstract words={word_count}; target={target} for style={venue_style}")

    for row in rows:
        cid = str(row.get("cid") or "").strip()
        if not cid:
            continue
        add(
            f"cid_{cid}_experiment_artifact",
            "WARN",
            _row_experiment_refs_present(row, experiments),
            f"Experiments should mention the table/metric/ablation refs seeded for internal alignment id {cid} when the row has concrete evidence.",
        )

    add(
        "no_orphan_related_work",
        "WARN",
        _related_work_topics_are_substantive(related),
        "Related Work subsections should contain citations, tension/nearest-prior-work discussion, or enough substantive contrastive prose.",
    )
    add(
        "related_work_laundry_list",
        "WARN",
        not _looks_like_laundry_list(related),
        "Related Work has repeated et al. listing patterns; ensure rationale/tension structure.",
    )
    add(
        "sectioning_granularity",
        "WARN",
        _sectioning_granularity_is_reasonable(paper),
        "Manuscript has too many fine-grained subsections for a section-by-section draft; merge artifact/id/paper-level fragments into functional argument sections.",
    )
    add(
        "paragraph_function_signal",
        "WARN",
        _paragraphs_have_argument_functions(paper),
        "Many paragraphs look like citations or artifact mentions without an argument function; each paragraph should define, compare, justify, evidence, analyze, or bound a claim.",
    )
    add(
        "related_work_pre_t5_signal_consumption",
        "WARN",
        _related_work_consumes_pre_t5_signals(related, rows),
        "Related Work should visibly consume nearest-prior-work, adjacent-transfer, or cross-paper tension signals from Pre-T5 artifacts.",
    )
    boilerplate_hits = _ai_boilerplate_hits(paper)
    add(
        "ai_boilerplate",
        "WARN",
        not boilerplate_hits,
        "Detected boilerplate phrases: " + ", ".join(boilerplate_hits[:10]) if boilerplate_hits else "No banned boilerplate phrases detected.",
    )
    punctuation_hits = _mechanical_punctuation_style_hits(paper)
    add(
        "mechanical_punctuation_style",
        "WARN",
        not punctuation_hits,
        (
            "Detected repeated colon/dash template style: " + ", ".join(punctuation_hits[:10])
            if punctuation_hits
            else "No excessive colon/dash template style detected."
        ),
    )
    add(
        "claim_strength_match",
        "WARN",
        not _improvement_overclaims(cdr_ledger, paper),
        "Improvement-type contribution should avoid first/novel paradigm overclaiming.",
    )
    add(
        "number_traceability",
        "WARN",
        _numbers_have_known_values(paper, paper_state),
        "Detected manuscript numeric values should be traceable to paper_state.shared_facts.result_metrics or source artifacts; reviewer/LLM should verify any unmatched numbers.",
    )
    add(
        "conclusion_no_new_claim_hint",
        "WARN",
        _conclusion_numbers_and_cites_seen_before(paper, conclusion),
        "Conclusion should not introduce new numeric/citation claims.",
    )

    markdown = _format_craft_audit_markdown(checks, rows, venue_style)
    return {
        "markdown": markdown,
        "json": {
            "version": "1.0",
            "semantics": "deterministic_writing_craft_audit_not_scientific_judgment",
            "venue_style": venue_style,
            "alignment_cids": cids,
            "checks": checks,
        },
    }


def _write_style_variant_craft_audits(
    policy: WorkspaceAccessPolicy,
    *,
    params: AuditWritingCraftParams,
    section_texts: dict[str, str],
    related_work_bib: str,
    support_text_by_key: dict[str, str] | None,
    paper_state: dict[str, Any],
    alignment_matrix: dict[str, Any],
    cdr_ledger: dict[str, Any],
) -> list[str]:
    if params.venue_style != "both" or not params.also_audit_style_variants:
        return []
    outputs: list[str] = []
    for style in ("is", "ccf_a"):
        paper_rel = f"drafts/{style}/paper.tex"
        paper_path = policy.workspace_dir / paper_rel
        if not paper_path.exists():
            continue
        paper = policy.resolve_read(paper_rel).read_text(encoding="utf-8", errors="replace")
        variant_section_texts = _read_section_texts_from_paper(paper) or section_texts
        audit_doc = audit_writing_craft(
            paper=paper,
            section_texts=variant_section_texts,
            related_work_bib=related_work_bib,
            support_text_by_key=support_text_by_key,
            paper_state=paper_state,
            alignment_matrix=alignment_matrix,
            cdr_ledger=cdr_ledger,
            venue_style=style,
        )
        audit_doc["json"]["input_fingerprints"] = craft_audit_input_fingerprints(
            policy.workspace_dir,
            paper_path=paper_rel,
            sections_dir=params.sections_dir,
            related_work_bib_path=params.related_work_bib_path,
            paper_state_path=params.paper_state_path,
            alignment_matrix_path=params.alignment_matrix_path,
            cdr_claim_ledger_path=params.cdr_claim_ledger_path,
        )
        markdown_rel = f"drafts/{style}/craft_audit.md"
        markdown_path = policy.resolve_write(markdown_rel)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(audit_doc["markdown"], encoding="utf-8")
        json_path = markdown_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(audit_doc["json"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        outputs.extend([markdown_rel, str(json_path.relative_to(policy.workspace_dir))])
    return outputs


def build_revision_patch_list(
    *,
    round_num: int,
    review_text: str,
    section_reviews: dict[str, str],
    include_low: bool = True,
) -> dict[str, Any]:
    patches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for section_id in CORE_SECTIONS:
        text = section_reviews.get(section_id, "")
        for issue in _extract_review_issues(text, default_section=section_id):
            if issue["severity"] == "low" and not include_low:
                continue
            key = (issue["target_section"], issue["severity"], issue["specific_issue"][:160])
            if key in seen:
                continue
            seen.add(key)
            patches.append(issue)

    for issue in _extract_review_issues(review_text, default_section="global"):
        if issue["severity"] == "low" and not include_low:
            continue
        key = (issue["target_section"], issue["severity"], issue["specific_issue"][:160])
        if key in seen:
            continue
        seen.add(key)
        patches.append(issue)

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    patches.sort(key=lambda item: (severity_rank.get(item["severity"], 3), _section_sort_key(item["target_section"])))
    for idx, patch in enumerate(patches, start=1):
        patch["patch_id"] = f"R{round_num}-P{idx:03d}"

    return {
        "version": "1.0",
        "semantics": "mechanical_review_issue_locations_not_final_revision_decisions",
        "round": round_num,
        "patches": patches,
        "rules": [
            "Patch list only localizes review issues; Writer LLM decides the actual academic revision.",
            "Prefer revising drafts/sections/<section>.tex, then reassemble drafts/paper.tex.",
            "Global patches may touch multiple sections, but should still be decomposed by the Writer when possible.",
            "Do not invent new results, citations, or claims while resolving patches.",
        ],
    }


def _extract_review_issues(text: str, *, default_section: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    issues: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        clean = line.strip()
        if not clean:
            continue
        severity = _detect_severity(clean)
        actionable_marker = bool(re.match(r"^[-*]\s*(?:\[[ xX]\]\s*)?", clean))
        heading_marker = clean.startswith("###") and ("问题" in clean or "issue" in clean.lower())
        if severity is None and not (actionable_marker or heading_marker):
            continue
        if severity is None:
            severity = "medium"
        issue_text = re.sub(r"^[-*]\s*(?:\[[ xX]\]\s*)?", "", clean).strip()
        issue_text = re.sub(r"^\[(?:High|Medium|Low|高|中|低)\]\s*", "", issue_text, flags=re.IGNORECASE)
        issue_text = re.sub(r"\*\*严重程度\*\*\s*[:：]\s*(High|Medium|Low)\s*", "", issue_text, flags=re.IGNORECASE).strip()
        if len(issue_text) < 8:
            continue
        section_id = _infer_section_from_text(issue_text, default_section=default_section)
        issues.append(
            {
                "patch_id": "",
                "target_section": section_id,
                "target_file": (
                    f"drafts/sections/{section_id}.tex" if section_id in CORE_SECTIONS else "drafts/paper.tex"
                ),
                "issue_type": _infer_issue_type(issue_text),
                "severity": severity,
                "specific_issue": issue_text[:1200],
                "suggested_action": "Revise the target section using source artifacts, paper_state.json, and reviewer context.",
                "source": {
                    "default_section": default_section,
                    "review_line": line_no,
                },
            }
        )
    return issues


def _detect_severity(text: str) -> str | None:
    low = text.lower()
    if re.search(r"\bhigh\b|严重程度\s*[:：]\s*高|\[高\]|高优先级", low):
        return "high"
    if re.search(r"\bmedium\b|严重程度\s*[:：]\s*中|\[中\]|中优先级", low):
        return "medium"
    if re.search(r"\blow\b|严重程度\s*[:：]\s*低|\[低\]|低优先级", low):
        return "low"
    if "major" in low or "主要问题" in text:
        return "high"
    if "minor" in low or "次要问题" in text:
        return "low"
    return None


def _infer_issue_type(text: str) -> str:
    low = text.lower()
    if any(token in low for token in ["citation", "\\cite", "bibtex", "引用", "文献"]):
        return "citation"
    if any(token in low for token in ["number", "metric", "数字", "指标", "result", "结果"]):
        return "factual"
    if any(token in low for token in ["evidence", "support", "证据", "支撑", "overclaim", "过度"]):
        return "missing_evidence"
    if any(token in low for token in ["clarity", "logic", "逻辑", "表达", "清晰", "过渡"]):
        return "clarity"
    if any(token in low for token in ["format", "latex", "格式"]):
        return "format"
    return "content"


def _infer_section_from_text(text: str, *, default_section: str) -> str:
    if default_section in CORE_SECTIONS:
        return default_section
    lowered = text.lower().replace("-", "_")
    aliases = [
        ("abstract", ["abstract", "摘要"]),
        ("introduction", ["introduction", "intro", "引言", "导言"]),
        ("related_work", ["related work", "related_work", "literature review", "相关工作", "文献综述"]),
        ("methodology", ["methodology", "method", "approach", "方法"]),
        ("experiments", ["experiments", "experiment", "evaluation", "results", "实验", "评估", "结果"]),
        ("analysis", ["analysis", "discussion", "分析", "讨论"]),
        ("conclusion", ["limitations", "limitation", "局限", "conclusion", "结论"]),
    ]
    for section_id, names in aliases:
        if any(name in lowered or name in text for name in names):
            return section_id
    return "global"


def _section_sort_key(section_id: str) -> int:
    if section_id in CORE_SECTIONS:
        return CORE_SECTIONS.index(section_id)
    return len(CORE_SECTIONS)


def _normalize_cid(raw: str, idx: int, used: set[str]) -> str:
    match = re.search(r"\bC\d+\b", raw, flags=re.IGNORECASE)
    cid = match.group(0).upper() if match else f"C{idx}"
    while cid in used:
        idx += 1
        cid = f"C{idx}"
    return cid


def _extract_hypothesis_ids(text: str) -> list[str]:
    found = re.findall(r"\bH\d+\b", text, flags=re.IGNORECASE)
    return [item.upper() for item in dict.fromkeys(found)]


def _first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip()[:120]
    return ""


def _extract_contribution_type_hint(text: str) -> str:
    match = re.search(r"\b(invention|improvement|exaptation|routine)\b", text, flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _extract_scorecard_alignment_hints(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        return []
    ideas = data.get("ideas") if isinstance(data, dict) else None
    if not isinstance(ideas, list):
        return []
    hints: list[dict[str, Any]] = []
    for item in ideas:
        if not isinstance(item, dict):
            continue
        idea = item.get("idea") if isinstance(item.get("idea"), dict) else {}
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        nearest = item.get("nearest_prior_work") or idea.get("nearest_prior_work") or {}
        if not isinstance(nearest, dict):
            nearest = {}
        hints.append(
            {
                "idea_id": str(idea.get("id") or item.get("idea_id") or "").strip(),
                "hypothesis_refs": _unique_strings(item.get("hypothesis_refs", [])),
                "status": str(decision.get("status") or "").strip().lower(),
                "counterfactual_check": item.get("counterfactual_check") or idea.get("counterfactual_check") or "",
                "counterfactual_note": item.get("counterfactual_note") or idea.get("counterfactual_note") or "",
                "nearest_prior_work": nearest,
                "novelty_signal": item.get("novelty_signal") or idea.get("novelty_signal") or "",
            }
        )
    selected = [hint for hint in hints if hint.get("status") == "selected"]
    return selected or hints


def _scorecard_hint_for_lane(
    hints: list[dict[str, Any]],
    *,
    lane: dict[str, Any],
    idx: int,
) -> dict[str, Any]:
    if not hints:
        return {}
    lane_hypothesis = str(lane.get("hypothesis") or "").lstrip("#").strip().upper()
    if lane_hypothesis:
        for hint in hints:
            refs = {str(ref).lstrip("#").strip().upper() for ref in hint.get("hypothesis_refs", [])}
            if lane_hypothesis in refs:
                return hint
    return hints[(idx - 1) % len(hints)]


def _alignment_sections_for_field(field: str) -> list[str]:
    return {
        "problem_frame": ["introduction", "related_work"],
        "design_rationale": ["methodology", "analysis"],
        "artifact": ["methodology"],
        "design_principles": ["methodology", "conclusion"],
        "data_view": ["experiments"],
        "evaluation_mode": ["experiments", "analysis"],
        "contribution_type": ["introduction", "conclusion"],
        "boundary_conditions": ["conclusion"],
        "cross_paper_tension": ["related_work"],
    }.get(field, ["introduction"])


def _fallback_alignment_lanes(contribution_claims: Any) -> list[dict[str, Any]]:
    claims = [claim for claim in contribution_claims if isinstance(claim, dict)] if isinstance(contribution_claims, list) else []
    target_count = min(4, max(3, len(claims) if claims else 3))
    lanes: list[dict[str, Any]] = []
    for idx in range(target_count):
        source_ids: list[str] = []
        if claims:
            for offset in range(idx, len(claims), target_count):
                claim_id = str(claims[offset].get("claim_id") or "").strip()
                if claim_id:
                    source_ids.append(claim_id)
        lanes.append(
            {
                "cid": f"C{idx + 1}",
                "hypothesis": "LLM_REVIEW_REQUIRED",
                "source_claim_ids": source_ids,
                "contribution_type": "LLM_REVIEW_REQUIRED",
            }
        )
    return lanes


def _first_nonempty(values: list[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _citation_pool_for_alignment(evidence_plan: dict[str, Any], *, section: str) -> list[str]:
    keys: list[str] = []
    for slot in evidence_plan.get("claim_slots", []) if isinstance(evidence_plan, dict) else []:
        if not isinstance(slot, dict):
            continue
        if normalize_section_id(str(slot.get("section") or "")) != section:
            continue
        keys.extend(_unique_strings(slot.get("citation_pool", [])))
    return keys[:8]


def _design_choice_hint(cdr_tuple: dict[str, Any], claim: dict[str, Any], required_sections: list[str]) -> str:
    for key in ("design_rationale", "artifact", "design_principles"):
        value = cdr_tuple.get(key) if isinstance(cdr_tuple, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    if "methodology" in required_sections:
        task = str(claim.get("llm_task") or "").strip()
        if task:
            return task[:300]
    return "LLM_REVIEW_REQUIRED"


def _experiment_alignment_hint(
    evidence_plan: dict[str, Any],
    figure_plan: dict[str, Any],
    *,
    idx: int,
    evidence_refs: list[str],
) -> dict[str, Any]:
    metric_candidates: list[Any] = []
    for slot in evidence_plan.get("claim_slots", []) if isinstance(evidence_plan, dict) else []:
        if not isinstance(slot, dict):
            continue
        if normalize_section_id(str(slot.get("section") or "")) == "experiments":
            metric_candidates.extend(slot.get("result_metric_candidates", []) or [])
    visuals = figure_plan.get("planned_visuals", []) if isinstance(figure_plan, dict) else []
    table = ""
    ablation = ""
    for visual in visuals if isinstance(visuals, list) else []:
        if not isinstance(visual, dict):
            continue
        if not table and visual.get("table_id"):
            table = str(visual.get("table_id"))
        if not ablation and "ablation" in str(visual).lower():
            ablation = str(visual.get("figure_id") or visual.get("table_id") or "")
    return {
        "rq": f"RQ{idx}",
        "result_metric": metric_candidates[0] if metric_candidates else "LLM_REVIEW_REQUIRED",
        "table": table or "tab:main_results",
        "ablation": ablation or ("experiments/ablations.csv" if "experiments/ablations.csv" in evidence_refs else "LLM_REVIEW_REQUIRED"),
    }


def _responsible_cids_for_section(section_id: str, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    column_map = {
        "introduction": ["motivation", "contribution"],
        "related_work": ["related_gap"],
        "methodology": ["design_choice"],
        "experiments": ["experiment"],
        "analysis": ["analysis"],
        "conclusion": ["contribution", "analysis"],
        "abstract": ["motivation", "contribution", "experiment"],
    }
    columns = column_map.get(section_id, [])
    responsible: list[dict[str, str]] = []
    for row in rows:
        cid = str(row.get("cid") or "").strip()
        if not cid:
            continue
        for column in columns:
            value = row.get(column)
            responsible.append({"cid": cid, "column": column, "value": _shorten(value, 180)})
    return responsible


def _alignment_rows(alignment_matrix: dict[str, Any], paper_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = alignment_matrix.get("rows") if isinstance(alignment_matrix, dict) else None
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    shared = paper_state.get("shared_facts", {}) if isinstance(paper_state, dict) else {}
    rows = shared.get("alignment_matrix") if isinstance(shared, dict) else None
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    return []


def _read_section_texts(section_dir: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    for section_id in CORE_SECTIONS + ["limitations"]:
        for suffix in (".tex", ".md"):
            path = section_dir / f"{section_id}{suffix}"
            if path.exists():
                texts[section_id] = _strip_document_wrappers(path.read_text(encoding="utf-8", errors="replace"))
                break
    return texts


def _strip_abstract_section_markup(text: str) -> str:
    """Normalize abstract section files into prose for the LaTeX abstract environment."""

    cleaned = _strip_document_wrappers(text or "")
    cleaned = re.sub(
        r"^\s*\\section\*?\{\s*Abstract\s*\}\s*",
        "",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    )
    match = re.fullmatch(
        r"\s*\\begin\{abstract\}(.*?)\\end\{abstract\}\s*",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        cleaned = match.group(1)
    return cleaned.strip()


def _read_section_texts_from_paper(paper: str) -> dict[str, str]:
    texts: dict[str, str] = {}
    abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", paper, flags=re.DOTALL | re.IGNORECASE)
    if abstract_match:
        texts["abstract"] = abstract_match.group(1).strip()
    matches = list(re.finditer(r"\\section\*?\{([^}]+)\}", paper))
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        section_id = normalize_section_id(title.lower().replace(" ", "_"))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(paper)
        body = paper[start:end].strip()
        if section_id == "abstract" and "abstract" in texts:
            continue
        if section_id == "conclusion" and "\\subsection{Limitations}" in body:
            texts["conclusion"] = body
        elif section_id in CORE_SECTIONS:
            texts[section_id] = body
    return texts


def _count_intro_contributions(text: str) -> int:
    if not text.strip():
        return 0
    if re.search(r"\\begin\{(?:enumerate|itemize)\}", text):
        return len(re.findall(r"\\item\b", text))
    lines = text.splitlines()
    count = 0
    in_contrib = False
    for line in lines:
        clean = line.strip()
        if re.search(r"contributions?|贡献", clean, flags=re.IGNORECASE):
            in_contrib = True
            continue
        if in_contrib and (
            re.match(r"(?:[-*]|\d+[.)])\s+", clean)
            or re.match(r"\\item\b", clean)
        ):
            count += 1
        elif in_contrib and clean.startswith("\\section"):
            break
    if count:
        return count
    return 0


def _count_intro_gaps(text: str) -> int:
    markers = re.findall(r"\b(?:gap|motivation)\s*\d*\b|动机|缺口", text, flags=re.IGNORECASE)
    return min(len(markers), 20)


def _section_has_cid_anchor(text: str, cid: str) -> bool:
    pattern = r"%\s*\[[^\]]*\b" + re.escape(cid) + r"\b[^\]]*\]"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _internal_label_leakages(paper: str, cids: list[str]) -> list[str]:
    """Return short snippets where internal alignment IDs leak into final TeX.

    CIDs are intentionally still present in JSON artifacts. In `paper.tex`,
    however, they encourage mechanical prose such as "[C1]" or "C1:" and make
    the manuscript look like a planning document. We keep the detector narrow:
    it only checks the known alignment IDs and common CID surface forms.
    """

    if not paper.strip() or not cids:
        return []
    cid_values = [str(cid).upper() for cid in cids if re.fullmatch(r"C\d+", str(cid).upper())]
    if not cid_values:
        return []
    known = [re.escape(cid) for cid in cid_values]
    cid_alt = "|".join(known)
    cid_nums = sorted({str(int(cid[1:])) for cid in cid_values}, key=int)
    num_alt = "|".join(re.escape(num) for num in cid_nums)
    patterns = [
        rf"%\s*\[[^\]]*\b(?:{cid_alt})\b[^\]]*\]",
        rf"\[(?:{cid_alt})(?:\s*,\s*(?:{cid_alt}))*\]",
        rf"\bC0*(?:{num_alt})\s*[:：]",
        rf"\bC0*(?:{num_alt})\s*[\.)]",
        rf"\b(?:{cid_alt})\s*[:：]",
        rf"\b(?:{cid_alt})\s*[\.)]",
        rf"\b(?:{cid_alt})\b(?=\s+(?:contribution|claim|gap|motivation|rationale|experiment|analysis)\b)",
        rf"\b(?:{cid_alt})\b(?=\s+(?:is|are|shows?|supports?)\b)",
        rf"\b(?:contribution|claim|gap|motivation|rationale|experiment|analysis)\s+(?:{cid_alt})\b",
        rf"\bCID\s*(?:-|:|：)?\s*(?:{cid_alt}|0*(?:{num_alt}))\b",
        rf"\binternal alignment id\s*(?:-|:|：)?\s*(?:{cid_alt}|0*(?:{num_alt}))\b",
        rf"\binternal alignment (?:id|lane)\s*(?:-|:|：)?\s*(?:{cid_alt}|0*(?:{num_alt}))\b",
    ]
    hits: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, paper, flags=re.IGNORECASE):
            snippet = re.sub(r"\s+", " ", match.group(0)).strip()
            if snippet and snippet not in hits:
                hits.append(snippet[:120])
            if len(hits) >= 20:
                return hits
    return hits


def _placeholder_hits(paper: str) -> list[str]:
    hits: list[str] = []
    for pattern in [
        r"\bTODO\b",
        r"\bTBD\b",
        r"\bPLACEHOLDER\b",
        r"\bLLM_REVIEW_REQUIRED\b",
        r"\bLLM\s+review\s+required\b",
    ]:
        for match in re.finditer(pattern, paper or "", flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            if value and value not in hits:
                hits.append(value)
    return hits


def craft_audit_input_fingerprints(
    workspace: Path,
    *,
    paper_path: str = "drafts/paper.tex",
    sections_dir: str = "drafts/sections",
    related_work_bib_path: str = "literature/related_work.bib",
    citation_map_path: str = "literature/citation_map.json",
    paper_notes_dir: str = "literature/paper_notes",
    abstract_notes_dir: str = "literature/paper_notes_abstract",
    bridge_notes_dir: str = "literature/paper_notes_bridge",
    paper_state_path: str = "drafts/paper_state.json",
    alignment_matrix_path: str = "drafts/alignment_matrix.json",
    cdr_claim_ledger_path: str = "drafts/cdr_claim_ledger.json",
) -> dict[str, dict[str, Any]]:
    paths = {
        "paper": paper_path,
        "sections_dir": sections_dir,
        "related_work_bib": related_work_bib_path,
        "citation_map": citation_map_path,
        "paper_notes_dir": paper_notes_dir,
        "abstract_notes_dir": abstract_notes_dir,
        "bridge_notes_dir": bridge_notes_dir,
        "paper_state": paper_state_path,
        "alignment_matrix": alignment_matrix_path,
        "cdr_claim_ledger": cdr_claim_ledger_path,
    }
    fingerprints: dict[str, dict[str, Any]] = {}
    for label, rel_path in paths.items():
        path = workspace / rel_path
        item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
        if path.exists() and path.is_file():
            item["kind"] = "file"
            item["sha256"] = _sha256_path(path)
        elif path.exists() and path.is_dir():
            item["kind"] = "dir"
            item["file_count"] = len([child for child in path.rglob("*") if child.is_file()])
            item["sha256"] = _sha256_directory(path, workspace)
        fingerprints[label] = item
    return fingerprints


REVIEW_ROUND_INPUT_PATHS = {
    "paper": "drafts/paper.tex",
    "manuscript_audit": "drafts/manuscript_audit.md",
    "craft_audit": "drafts/craft_audit.json",
    "paper_claim_audit": "drafts/paper_claim_audit.json",
    "result_to_claim": "drafts/result_to_claim.json",
    "experiment_evidence_pack": "drafts/experiment_evidence_pack.json",
    "self_check": "drafts/self_check.md",
    "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
    "alignment_matrix": "drafts/alignment_matrix.json",
    "related_work_bib": "literature/related_work.bib",
    "results_summary": "experiments/results_summary.json",
}


def review_round_input_fingerprints(workspace: Path) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for label, rel_path in REVIEW_ROUND_INPUT_PATHS.items():
        path = workspace / rel_path
        item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
        if path.exists() and path.is_file():
            item["kind"] = "file"
            item["sha256"] = _sha256_path(path)
            item["size"] = path.stat().st_size
        elif path.exists() and path.is_dir():
            item["kind"] = "dir"
            item["file_count"] = len([child for child in path.rglob("*") if child.is_file()])
            item["sha256"] = _sha256_directory(path, workspace)
        fingerprints[label] = item
    return fingerprints


def validate_review_round_input_fingerprints(
    workspace: Path,
    fingerprints: object,
) -> tuple[bool, str | None]:
    if not isinstance(fingerprints, dict):
        return False, "review fingerprints 缺少 input_fingerprints"
    current = review_round_input_fingerprints(workspace)
    stale: list[str] = []
    for label, item in current.items():
        previous = fingerprints.get(label)
        if not isinstance(previous, dict):
            stale.append(label)
            continue
        if bool(previous.get("exists")) != bool(item.get("exists")):
            stale.append(label)
            continue
        if item.get("exists") and str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            stale.append(label)
    if stale:
        return False, "review 输入已变化，必须重新生成本轮 review: " + ", ".join(stale[:8])
    return True, None


def _sha256_directory(path: Path, workspace: Path) -> str:
    payload: list[dict[str, str]] = []
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        payload.append(
            {
                "path": _rel_path(workspace, child),
                "sha256": _sha256_path(child),
            }
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _row_experiment_refs_present(row: dict[str, Any], experiments_text: str) -> bool:
    experiment = row.get("experiment")
    if not isinstance(experiment, dict):
        return False
    refs: list[str] = []
    for key in ("table", "result_metric", "ablation", "rq"):
        value = experiment.get(key)
        if isinstance(value, dict):
            refs.extend(str(item) for item in value.values())
        elif isinstance(value, list):
            refs.extend(str(item) for item in value)
        elif value is not None:
            refs.append(str(value))
    concrete_refs = [
        ref.strip()
        for ref in refs
        if ref
        and ref.strip()
        and "LLM_REVIEW_REQUIRED" not in ref
        and not ref.strip().startswith("experiments/")
    ]
    if not concrete_refs:
        return False
    low = experiments_text.lower()
    return any(ref.lower() in low for ref in concrete_refs)


def _manuscript_section_citation_issues(section_texts: dict[str, str]) -> list[str]:
    issues: list[str] = []
    for section_id, minimum in MANUSCRIPT_SECTION_MIN_CITATIONS.items():
        if minimum <= 0:
            continue
        text = section_texts.get(section_id, "")
        if not text.strip():
            continue
        cited = _extract_latex_cites(text)
        if len(cited) < minimum:
            issues.append(f"{section_id} has {len(cited)} unique citations; minimum={minimum}")
    return issues


def _related_work_topics_are_substantive(text: str) -> bool:
    headings = list(re.finditer(r"\\subsection\*?\{[^}]+\}", text))
    if not headings:
        return True
    for idx, match in enumerate(headings):
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        chunk = text[match.start():end]
        body = re.sub(r"\\subsection\*?\{[^}]+\}", "", chunk)
        has_citation = bool(_extract_latex_cites(body))
        has_tension_word = bool(
            re.search(
                r"rationale|tension|gap|contrast|however|whereas|limitation|nearest prior|"
                r"张力|缺口|局限|差异|相比|然而",
                body,
                flags=re.IGNORECASE,
            )
        )
        word_count = len(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", body))
        if not (has_citation or has_tension_word or word_count >= 80):
            return False
    return True


def _related_work_consumes_pre_t5_signals(text: str, rows: list[dict[str, Any]]) -> bool:
    lowered = text.lower()
    candidates: list[str] = []
    for row in rows:
        related_gap = row.get("related_gap") if isinstance(row, dict) else {}
        nearest: Any = {}
        if isinstance(related_gap, dict):
            tension = str(related_gap.get("tension") or "").strip()
            if tension and "LLM_REVIEW_REQUIRED" not in tension:
                candidates.append(tension)
            nearest = related_gap.get("nearest_prior_work") or {}
        if not isinstance(nearest, dict) and isinstance(row, dict):
            nearest = row.get("nearest_prior_work") or {}
        if isinstance(nearest, dict):
            work = str(nearest.get("work") or "").strip()
            if work and work.lower() not in {"none", "none_found", "n/a"}:
                candidates.append(work)

    text_tokens = set(re.findall(r"[A-Za-z0-9_:-]{4,}", lowered))
    for candidate in candidates:
        candidate_tokens = set(re.findall(r"[A-Za-z0-9_:-]{4,}", candidate.lower()))
        if candidate_tokens and len(candidate_tokens & text_tokens) >= min(2, len(candidate_tokens)):
            return True
    return False


def _looks_like_laundry_list(text: str) -> bool:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    etal_sentences = sum(1 for sentence in sentences if re.search(r"\bet al\.", sentence))
    transition_hits = sum(1 for sentence in sentences if re.search(r"rationale|tension|gap|however|therefore|因此|张力|缺口", sentence, flags=re.IGNORECASE))
    return etal_sentences >= 4 and transition_hits < 2


def _sectioning_granularity_is_reasonable(text: str) -> bool:
    section_count = len(re.findall(r"(?<!sub)\\section\*?\{", text))
    subsection_count = len(re.findall(r"(?<!sub)\\subsection\*?\{", text))
    subsubsection_count = len(re.findall(r"\\subsubsection\*?\{", text))
    if subsubsection_count:
        return False
    if section_count <= 0:
        return subsection_count <= 12
    return subsection_count <= max(18, section_count * 4)


def _paragraphs_have_argument_functions(text: str) -> bool:
    paragraphs = [
        re.sub(
            r"\s+",
            " ",
            re.sub(r"\\(?:section|subsection|subsubsection|paragraph)\*?\{[^{}]*\}", " ", paragraph),
        ).strip()
        for paragraph in re.split(r"\n\s*\n", text or "")
    ]
    substantive = [
        paragraph
        for paragraph in paragraphs
        if len(paragraph) >= 120
        and "\\begin{" not in paragraph
        and "\\end{" not in paragraph
    ]
    function_words = re.compile(
        r"\b(because|therefore|however|whereas|while|although|suggests?|indicates?|"
        r"shows?|implies?|motivates?|contrasts?|differs?|supports?|limits?|boundary|"
        r"rationale|tension|gap|mechanism|evidence|we find|we show)\b|"
        r"因此|然而|相比|差异|机制|证据|边界|张力|缺口|说明|表明|支持|限制",
        flags=re.IGNORECASE,
    )
    weak = 0
    for paragraph in substantive:
        has_function = bool(function_words.search(paragraph))
        citation_like = len(re.findall(r"\\cite\{|\bet al\.", paragraph)) >= 2
        artifact_like = len(re.findall(r"\b(?:paper_state|alignment_matrix|claim_ledger|cdr_|drafts/|literature/)\b", paragraph)) >= 1
        if not has_function and (citation_like or artifact_like):
            weak += 1
    return weak <= max(0, len(substantive) // 4)


def _ai_boilerplate_hits(text: str) -> list[str]:
    patterns = [
        "delve into",
        "it is worth noting",
        "plays a crucial role",
        "plays a pivotal role",
        "plays a vital role",
        "in the realm of",
        "rich tapestry",
        "a testament to",
        "shed light on",
        "pave the way",
        "With the rapid development of",
        "With the rapid advancement of",
        "in today's fast-paced",
        "in today's ever-evolving",
    ]
    hits = []
    lowered = text.lower()
    for pattern in patterns:
        if pattern.lower() in lowered:
            hits.append(pattern)
    if len(re.findall(r"(?:^|\n)\s*Furthermore\b", text)) >= 3:
        hits.append("3+ paragraph-initial Furthermore")
    return hits


def _mechanical_punctuation_style_hits(text: str) -> list[str]:
    prose = re.sub(r"\\(?:section|subsection|subsubsection)\*?\{[^{}]*\}", " ", text or "")
    prose = re.sub(r"\\begin\{[^{}]+\}.*?\\end\{[^{}]+\}", " ", prose, flags=re.DOTALL)
    paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", prose)
        if len(re.sub(r"\s+", " ", paragraph).strip()) >= 80
    ]
    hits: list[str] = []
    colon_template = re.compile(
        r"(?m)^\s*(?:[A-Z][A-Za-z ]{2,32}|[一-龥]{2,12})\s*[:：]\s+\S"
    )
    colon_count = sum(1 for paragraph in paragraphs if colon_template.search(paragraph))
    if colon_count >= 4:
        hits.append(f"{colon_count} paragraph-like colon labels")
    dash_count = len(re.findall(r"\s(?:--|---|–|—)\s", prose))
    if dash_count >= 8:
        hits.append(f"{dash_count} spaced dash transitions")
    repeated_label_sentences = len(
        re.findall(
            r"\b(?:Problem|Gap|Insight|Mechanism|Implication|Challenge|Future direction|Contribution)\s*[:：]",
            prose,
            flags=re.IGNORECASE,
        )
    )
    if repeated_label_sentences >= 5:
        hits.append(f"{repeated_label_sentences} repeated English label-colon phrases")
    cjk_label_sentences = len(
        re.findall(r"(?:背景|问题|缺口|机制|挑战|启示|未来方向|贡献)\s*[:：]", prose)
    )
    if cjk_label_sentences >= 5:
        hits.append(f"{cjk_label_sentences} repeated Chinese label-colon phrases")
    return hits


def _improvement_overclaims(cdr_ledger: dict[str, Any], paper: str) -> bool:
    cdr_tuple = cdr_ledger.get("cdr_tuple", {}) if isinstance(cdr_ledger, dict) else {}
    contribution_type = str(cdr_tuple.get("contribution_type") or "").lower()
    if contribution_type != "improvement":
        return False
    return bool(re.search(r"\b(first|novel paradigm|paradigm-shifting|unprecedented)\b", paper, flags=re.IGNORECASE))


def _numbers_have_known_values(paper: str, paper_state: dict[str, Any]) -> bool:
    shared = paper_state.get("shared_facts", {}) if isinstance(paper_state, dict) else {}
    values = {
        str(item.get("value"))
        for item in shared.get("result_metrics", [])
        if isinstance(item, dict) and item.get("value") is not None
    }
    if not values:
        return True
    numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", paper)
    substantive = [num for num in numbers if _is_traceable_manuscript_number(num)]
    return all(_number_matches_known_value(num, values) for num in substantive[:30])


def _is_traceable_manuscript_number(num: str) -> bool:
    try:
        value = float(num)
    except Exception:
        return False
    if value in {0.0, 1.0}:
        return False
    if 1900 <= value <= 2100:
        return False
    if value.is_integer() and 1 <= value <= 20:
        return False
    return True


def _number_matches_known_value(num: str, values: set[str]) -> bool:
    try:
        numeric = float(num)
    except Exception:
        return False
    for value in values:
        try:
            known = float(value)
        except Exception:
            continue
        if numeric == known:
            return True
        tolerance = max(abs(known) * 0.001, 1e-9)
        if abs(numeric - known) <= tolerance:
            return True
    return False


def _conclusion_numbers_and_cites_seen_before(paper: str, conclusion: str) -> bool:
    if not conclusion.strip():
        return True
    before = paper.split(conclusion, 1)[0] if conclusion in paper else paper.replace(conclusion, "")
    conclusion_nums = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", conclusion))
    before_nums = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", before))
    conclusion_cites = _extract_latex_cites(conclusion)
    before_cites = _extract_latex_cites(before)
    return conclusion_nums.issubset(before_nums) and conclusion_cites.issubset(before_cites)


def _format_craft_audit_markdown(checks: list[dict[str, Any]], rows: list[dict[str, Any]], venue_style: str) -> str:
    fail_count = sum(1 for item in checks if item["level"] == "FAIL")
    warn_count = sum(1 for item in checks if item["level"] == "WARN")
    lines = [
        "# Writing Craft And Alignment Audit",
        "",
        "该报告是确定性写作范式审计 hint，不替代 Writer/Reviewer 的学术判断。",
        "",
        f"- Venue style: `{venue_style}`",
        f"- Alignment rows: {len(rows)}",
        f"- FAIL: {fail_count}",
        f"- WARN: {warn_count}",
        "",
        "## Checks",
    ]
    for item in checks:
        box = "x" if item["passed"] else " "
        lines.append(f"- [{box}] **{item['level']}** `{item['name']}`: {item['detail']}")
    lines.extend(["", "## Alignment Rows"])
    for row in rows:
        lines.append(
            "- `{}` hypothesis={} status={} contribution={}".format(
                row.get("cid"),
                row.get("hypothesis", ""),
                row.get("status", ""),
                _shorten(row.get("contribution"), 180),
            )
        )
    return "\n".join(lines) + "\n"


def _merge_legacy_limitations_into_conclusion(conclusion: str, limitations: str) -> str:
    if not limitations.strip():
        return conclusion
    if re.search(r"\\subsection\*?\{\s*Limitations\s*\}", conclusion, flags=re.IGNORECASE):
        return conclusion
    cleaned = re.sub(r"\\section\*?\{\s*Limitations\s*\}", "", limitations, flags=re.IGNORECASE).strip()
    if not cleaned:
        return conclusion
    return conclusion.rstrip() + "\n\n\\subsection{Limitations}\n" + cleaned + "\n"


def _shorten(value: Any, limit: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _artifact_entry(workspace: Path, path: Path, *, include_preview: bool) -> dict[str, Any]:
    rel = path.relative_to(workspace).as_posix()
    entry = {
        "path": rel,
        "kind": _artifact_kind(rel),
        "bytes": path.stat().st_size,
    }
    if include_preview and path.stat().st_size < 500_000:
        text = path.read_text(encoding="utf-8", errors="replace")
        entry["preview"] = text[:1200]
    return entry


def _read_optional_text(policy: WorkspaceAccessPolicy, rel_path: str) -> str:
    path = policy.workspace_dir / rel_path
    if not path.exists():
        return ""
    return policy.resolve_read(rel_path).read_text(encoding="utf-8", errors="replace")


def _read_optional_json(policy: WorkspaceAccessPolicy, rel_path: str) -> dict[str, Any]:
    text = _read_optional_text(policy, rel_path)
    if not text.strip():
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _artifact_kind(rel: str) -> str:
    if rel.endswith(".bib"):
        return "bibliography"
    if rel.endswith(".csv"):
        return "table_csv"
    if rel.endswith(".json"):
        return "json"
    if rel.endswith((".yaml", ".yml")):
        return "yaml"
    if rel.endswith(".md"):
        return "markdown"
    if rel.endswith(".py"):
        return "code"
    return "artifact"


def _glob_media(workspace: Path, *, kind: str) -> list[Path]:
    suffixes = {".png", ".jpg", ".jpeg", ".pdf", ".svg"} if kind == "figure" else {".csv", ".tsv", ".xlsx"}
    roots = [workspace / "experiments", workspace / "drafts", workspace / "evaluation"]
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes:
                paths.append(path)
    return sorted(paths)


def _media_entry(workspace: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "suffix": path.suffix.lower(),
        "bytes": path.stat().st_size,
    }


def _extract_bib_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return sorted(set(extract_bib_keys_from_text(text)))


def _extract_latex_cites(text: str) -> set[str]:
    keys: set[str] = set()
    for match in _LATEX_CITATION_COMMAND_RE.finditer(text or ""):
        command = match.group(0)
        brace_match = re.search(r"\{([^}]+)\}\s*$", command)
        if not brace_match:
            continue
        chunk = brace_match.group(1)
        keys.update(key.strip() for key in chunk.split(",") if key.strip())
    return keys


def _extract_result_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    metrics: list[dict[str, Any]] = []
    for exp in data.get("experiments", []) if isinstance(data, dict) else []:
        exp_id = exp.get("experiment_id") or exp.get("name") or "unknown"
        for key, value in (exp.get("metrics") or {}).items():
            if isinstance(value, (int, float, str)):
                metrics.append({"experiment_id": exp_id, "metric": key, "value": value})
    for path_key, value in _walk_numeric_values(data):
        if any(item["metric"] == path_key and item["value"] == value for item in metrics):
            continue
        metrics.append({"experiment_id": "results_summary", "metric": path_key, "value": value})
    return metrics


def _extract_evidence_pack_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict) or data.get("semantics") != "normalized_experiment_evidence_pack":
        return []
    metrics: list[dict[str, Any]] = []
    for metric in data.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        name = metric.get("name") or metric.get("metric") or metric.get("metric_id") or "metric"
        record = {
            "experiment_id": metric.get("experiment_id") or "external_executor",
            "metric": name,
            "value": value,
            "metric_id": metric.get("metric_id"),
            "source_artifact": metric.get("source_artifact"),
            "dataset": metric.get("dataset"),
            "seed": metric.get("seed"),
            "mock_only": bool(metric.get("mock_only") or data.get("mock_only")),
            "evidence_pack": "drafts/experiment_evidence_pack.json",
        }
        metrics.append({key: val for key, val in record.items() if val is not None})
    return metrics


def _dedupe_metric_records(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        key = (
            str(metric.get("metric_id") or ""),
            str(metric.get("experiment_id") or ""),
            str(metric.get("metric") or metric.get("name") or ""),
            str(metric.get("value") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(metric)
    return deduped


def _walk_numeric_values(value: Any, *, prefix: str = "") -> list[tuple[str, int | float]]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(prefix or "value", value)]
    if isinstance(value, dict):
        found: list[tuple[str, int | float]] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            found.extend(_walk_numeric_values(child, prefix=child_prefix))
        return found
    if isinstance(value, list):
        found = []
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            found.extend(_walk_numeric_values(child, prefix=child_prefix))
        return found
    return []


def _csv_columns(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            return next(reader, [])
    except Exception:
        return []


def _strip_document_wrappers(text: str) -> str:
    text = re.sub(r"\\documentclass(?:\[[^\]]+\])?\{[^}]+\}", "", text)
    text = re.sub(r"\\usepackage(?:\[[^\]]+\])?\{[^}]+\}", "", text)
    text = text.replace("\\begin{document}", "").replace("\\end{document}", "")
    return text.strip()


def _extract_title(outline_text: str) -> str:
    for line in outline_text.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean and "title" in clean.lower():
            parts = re.split(r"[:：]", clean, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return ""


def _extract_title_candidates(outline_text: str) -> list[str]:
    candidates: list[str] = []
    for line in outline_text.splitlines():
        stripped = line.strip().lstrip("-*0123456789. ").strip()
        if not stripped:
            continue
        if "title" in stripped.lower() or "标题" in stripped:
            parts = re.split(r"[:：]", stripped, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                candidates.append(parts[1].strip())
    return candidates[:5]


def _extract_outline_section(outline_text: str, section_id: str) -> str:
    if not outline_text.strip():
        return ""
    title = SECTION_TITLES.get(section_id, section_id).lower()
    aliases = {
        section_id.lower().replace("_", " "),
        title,
        "method" if section_id == "methodology" else "",
        "evaluation" if section_id == "experiments" else "",
        "literature review" if section_id == "related_work" else "",
        "discussion" if section_id == "analysis" else "",
    }
    aliases = {alias for alias in aliases if alias}
    lines = outline_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip().lower()
        if stripped and any(alias in stripped for alias in aliases):
            start = idx
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].lstrip().startswith("##"):
            end = idx
            break
    return "\n".join(lines[start:end]).strip()[:4000]


def _section_purpose(section_id: str) -> str:
    return {
        "methodology": "Explain the proposed mechanism, algorithm/procedure, inputs, outputs, and implementation choices grounded in hypotheses and exp_plan.",
        "experiments": "Report setup, datasets, baselines, metrics, main results, ablations, seeds, and compute using only experiment artifacts.",
        "related_work": "Position the paper against prior work with a taxonomy and real citation keys from the bibliography.",
        "analysis": "Interpret mechanism evidence, ablations, alternative explanations, failure cases, and sensitivity.",
        "introduction": "Motivate the problem, define the gap, state the insight, contributions, and result headline without overclaiming.",
        "conclusion": "Recap contributions, include a concrete Limitations subsection, and state future work without introducing new claims.",
        "abstract": "Compress the final paper into problem, method, evidence, result, and contribution after all sections exist.",
    }.get(section_id, "Write the section according to the global outline and evidence plan.")


def _section_cdr_responsibility(section_id: str) -> str:
    return {
        "abstract": "Compress problem_frame, design_rationale, artifact/evidence, and contribution_type without adding new claims.",
        "introduction": "Make the field-change argument explicit: what becomes different if this contribution works.",
        "related_work": "Compare competing design rationales and cross-paper tensions, not only method taxonomies.",
        "methodology": "Explain why the artifact is designed this way, including transferable design principles where justified.",
        "experiments": "Align datasets, metrics, baselines, and results with data_view and evaluation_mode.",
        "analysis": "Assess whether evidence supports or weakens the design_rationale and name alternative explanations.",
        "conclusion": "Return to contribution_type, transferable design knowledge, and boundary_conditions without introducing new evidence.",
    }.get(section_id, "Use CDR fields as a reasoning scaffold for this section.")


def _latex_section_title(name: str) -> str:
    return {
        "related_work": "Related Work",
        "methodology": "Method",
        "experiments": "Experiments",
        "analysis": "Analysis",
    }.get(name, name.replace("_", " ").title())


def _escape_latex_braces(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def rewrite_bibliography_to_references(tex: str, bib_stem: str = "references") -> str:
    """Rewrite LaTeX bibliography commands to the bundle-local bibliography basename."""

    target = bib_stem.strip() or "references"
    biblatex_target = f"{target}.bib"
    if re.search(r"\\addbibresource(?:\[[^\]]*\])?\{[^}]*\}", tex):
        tex = re.sub(
            r"\\addbibresource(?:\[[^\]]*\])?\{[^}]*\}",
            f"\\\\addbibresource{{{biblatex_target}}}",
            tex,
        )
        if "\\bibliography{" in tex:
            tex = re.sub(r"\\bibliography\{[^}]*\}", "", tex)
        return tex
    if re.search(r"\\bibliography\{[^}]*\}", tex):
        return re.sub(r"\\bibliography\{[^}]*\}", f"\\\\bibliography{{{target}}}", tex)
    insertion = f"\n\\bibliographystyle{{plain}}\n\\bibliography{{{target}}}\n"
    end_match = re.search(r"\\end\{document\}", tex)
    if end_match:
        return tex[: end_match.start()] + insertion + tex[end_match.start() :]
    return tex.rstrip() + insertion


def extract_bibliography_stems(tex: str) -> list[str]:
    stems: list[str] = []
    for chunk in re.findall(r"\\bibliography\{([^}]+)\}", tex):
        for item in chunk.split(","):
            stem = item.strip()
            if stem:
                stems.append(Path(stem).name)
    for chunk in re.findall(r"\\addbibresource(?:\[[^\]]*\])?\{([^}]+)\}", tex):
        stem = Path(chunk.strip()).name
        if stem.endswith(".bib"):
            stem = stem[:-4]
        if stem:
            stems.append(stem)
    return list(dict.fromkeys(stems))


def _copy_submission_figures(
    policy: WorkspaceAccessPolicy,
    *,
    tex: str,
    bundle_dir: str,
    enabled: bool,
) -> tuple[list[dict[str, str]], str]:
    if not enabled:
        return [], tex
    copied: list[dict[str, str]] = []
    copied_by_source: dict[str, str] = {}

    for rel_source in _extract_includegraphics_paths(tex):
        source_path = _resolve_graphics_source(policy, rel_source)
        if source_path is None:
            continue
        dst_rel = _copy_figure_to_bundle(policy, source_path, bundle_dir=bundle_dir)
        copied_by_source[source_path.relative_to(policy.workspace_dir).as_posix()] = dst_rel
        copied.append({"source_path": source_path.relative_to(policy.workspace_dir).as_posix(), "dest_path": dst_rel})
        tex = _rewrite_includegraphics_path(tex, rel_source, Path(dst_rel).relative_to(Path(bundle_dir)).as_posix())

    for rel_dir in ("drafts/figures", "figures"):
        src_dir = policy.workspace_dir / rel_dir
        if not src_dir.exists() or not src_dir.is_dir():
            continue
        for src in sorted(path for path in src_dir.rglob("*") if path.is_file()):
            source_rel = _rel_path(policy.workspace_dir, src)
            if source_rel in copied_by_source:
                continue
            dst_rel = _copy_figure_to_bundle(policy, src, bundle_dir=bundle_dir)
            copied_by_source[source_rel] = dst_rel
            copied.append({"source_path": source_rel, "dest_path": dst_rel})
    return copied, tex


def _extract_includegraphics_paths(tex: str) -> list[str]:
    paths: list[str] = []
    pattern = re.compile(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
    for match in pattern.finditer(tex or ""):
        value = match.group(1).strip()
        if value:
            paths.append(value)
    return list(dict.fromkeys(paths))


_ALLOWED_GRAPHICS_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}


def _resolve_graphics_source(policy: WorkspaceAccessPolicy, latex_path: str) -> Path | None:
    workspace = policy.workspace_dir
    value = latex_path.strip()
    if not value or value.startswith(("http://", "https://")):
        return None
    candidates: list[Path] = []
    raw = Path(value)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend(
            [
                workspace / value,
                workspace / "drafts" / value,
                workspace / "experiments" / value,
                workspace / "evaluation" / value,
                workspace / "figures" / value,
            ]
        )
    if raw.suffix:
        suffix_candidates = candidates
    else:
        suffix_candidates = []
        for candidate in candidates:
            suffix_candidates.append(candidate)
            for suffix in sorted(_ALLOWED_GRAPHICS_SUFFIXES):
                suffix_candidates.append(candidate.with_suffix(suffix))
    for candidate in suffix_candidates:
        try:
            resolved = candidate.resolve()
            source_rel = resolved.relative_to(workspace.resolve()).as_posix()
        except ValueError:
            continue
        if resolved.suffix.lower() not in _ALLOWED_GRAPHICS_SUFFIXES:
            continue
        try:
            checked = policy.resolve_read(source_rel)
        except ToolAccessDenied:
            continue
        if checked.exists() and checked.is_file():
            return resolved
    return None


def _copy_figure_to_bundle(policy: WorkspaceAccessPolicy, src: Path, *, bundle_dir: str) -> str:
    workspace = policy.workspace_dir
    source_rel = src.relative_to(workspace).as_posix()
    digest = _sha256_path(src)[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_rel).strip("_")
    suffix = src.suffix.lower()
    safe_stem = stem[:160].removesuffix(suffix) if suffix and stem.lower().endswith(suffix) else stem[:160]
    safe_name = f"{safe_stem}_{digest}{suffix}"
    if not safe_name:
        safe_name = f"figure_{digest}{suffix}"
    dst_rel = f"{bundle_dir}/figures/{safe_name}"
    dst = policy.resolve_write(dst_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return dst_rel


def _rewrite_includegraphics_path(tex: str, original: str, replacement: str) -> str:
    pattern = re.compile(
        r"(\\includegraphics(?:\s*\[[^\]]*\])?\s*\{)" + re.escape(original) + r"(\})"
    )
    return pattern.sub(r"\1" + replacement + r"\2", tex)


def build_submission_bundle_manifest(
    workspace: Path,
    *,
    paper_path: Path,
    bib_path: Path,
    main_path: Path,
    references_path: Path,
    copied_figures: list[str | dict[str, str]],
    copied_support_files: list[str] | None = None,
) -> dict[str, Any]:
    """Fingerprint the source artifacts used to prepare a T9 bundle.

    A compiled bundle can be internally self-consistent while still being
    stale relative to the current `drafts/paper.tex` or bibliography. The
    manifest gives T9 validators a mechanical freshness contract.
    """

    return {
        "version": "1.0",
        "semantics": "submission_bundle_source_fingerprint",
        "source": {
            "paper_path": _rel_path(workspace, paper_path),
            "paper_sha256": _sha256_path(paper_path),
            "bib_path": _rel_path(workspace, bib_path),
            "bib_sha256": _sha256_path(bib_path),
        },
        "bundle": {
            "main_tex_path": _rel_path(workspace, main_path),
            "main_tex_sha256": _sha256_path(main_path),
            "references_bib_path": _rel_path(workspace, references_path),
            "references_bib_sha256": _sha256_path(references_path),
            "copied_figures": _submission_figure_manifest_entries(workspace, copied_figures),
            "copied_support_files": _submission_support_file_manifest_entries(workspace, copied_support_files or []),
        },
    }


def _submission_figure_manifest_entries(
    workspace: Path,
    copied_figures: list[str | dict[str, str]],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in copied_figures:
        if isinstance(item, dict):
            source_rel = str(item.get("source_path") or "").strip()
            dest_rel = str(item.get("dest_path") or item.get("path") or "").strip()
        else:
            source_rel = ""
            dest_rel = str(item or "").strip()
        if not dest_rel:
            continue
        dest = workspace / dest_rel
        if not dest.exists():
            continue
        entry = {
            "path": dest_rel,  # backward-compatible bundle path
            "dest_path": dest_rel,
            "dest_sha256": _sha256_path(dest),
            "sha256": _sha256_path(dest),  # backward-compatible bundle hash
        }
        if source_rel:
            source = workspace / source_rel
            if source.exists():
                entry["source_path"] = source_rel
                entry["source_sha256"] = _sha256_path(source)
        entries.append(entry)
    return entries


def _submission_support_file_manifest_entries(workspace: Path, copied_support_files: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for rel in copied_support_files:
        path = workspace / rel
        if path.exists() and path.is_file():
            entries.append({"path": rel, "sha256": _sha256_path(path)})
        else:
            entries.append({"path": rel, "sha256": ""})
    return entries


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_path(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
