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

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .manuscript import _extract_latex_cites
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

OPTIONAL_SURVEY_SECTION_PREFIXES = ("theme_",)


class BuildSurveyStateParams(BaseModel):
    survey_plan_path: str = Field(default="drafts/survey/survey_plan.json")
    corpus_decision_path: str = Field(default="drafts/survey/corpus_decision.json")
    expansion_path: str = Field(default="drafts/survey/survey_expansion.json")
    state_output_path: str = Field(default="drafts/survey/survey_state.json")
    section_outline_dir: str = Field(default="drafts/survey/section_outlines")
    max_theme_sections: int = Field(default=4, ge=1, le=8)


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
            state_path = self.policy.resolve_write(params.state_output_path)
            outline_dir = self.policy.resolve_write(params.section_outline_dir)
        except (ToolAccessDenied, FileNotFoundError, ValueError) as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_input")

        outline = _coerce_outline(plan.get("outline"))
        overflow_count = _theme_entry_overflow_count(outline, max_theme_sections=params.max_theme_sections)
        if overflow_count > 0:
            return ToolResult(
                ok=False,
                content=(
                    f"survey_plan outline contains {overflow_count + params.max_theme_sections} theme sections, "
                    f"but current T3.6 state machine supports {params.max_theme_sections}. "
                    "Merge/prioritize themes or extend SURVEY_SECTION_SEQUENCE/state_machine before continuing."
                ),
                error="too_many_theme_sections",
            )
        theme_entries = _theme_entries(outline, max_theme_sections=params.max_theme_sections)
        theme_by_slot = {f"theme_{idx}": entry for idx, entry in enumerate(theme_entries, start=1)}

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
                "covers": list(plan_entry.get("covers") or []) if isinstance(plan_entry, dict) else [],
                "paper_ids": list(plan_entry.get("paper_ids") or []) if isinstance(plan_entry, dict) else [],
                "plan_section_id": str(plan_entry.get("section_id") or section_id) if isinstance(plan_entry, dict) else section_id,
            }

        state = {
            "semantics": "survey_state_for_taxonomy_driven_section_writing_not_final_claims",
            "survey_plan": params.survey_plan_path,
            "input_fingerprints": _input_fingerprints(
                self.policy.workspace_dir,
                {
                    "survey_plan": params.survey_plan_path,
                    "corpus_decision": params.corpus_decision_path,
                    "survey_expansion": params.expansion_path,
                },
            ),
            "corpus_scope": _corpus_scope(corpus_decision),
            "write_order": [sid for sid in SURVEY_SECTION_SEQUENCE if sections[sid]["status"] != "skipped"],
            "sections": sections,
            "shared_facts": {
                "taxonomy_dimension": ((plan.get("taxonomy") or {}).get("dimension") if isinstance(plan.get("taxonomy"), dict) else ""),
                "taxonomy_classes": _taxonomy_classes(plan),
                "evolution_narrative": str(plan.get("evolution_narrative") or ""),
                "coverage_selfcheck": plan.get("coverage_selfcheck") or {},
                "resource_upgrade_needs": _resource_upgrade_needs(plan),
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
        sections[section_id]["input_fingerprints"] = _input_fingerprints(
            self.policy.workspace_dir,
            {
                "section_outline": str(sections[section_id].get("outline_file") or f"drafts/survey/section_outlines/{section_id}.md"),
                "section_file": section_path,
            },
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
        pieces = [
            "\\documentclass[11pt]{article}",
            "\\usepackage[margin=1in]{geometry}",
            "\\usepackage{booktabs}",
            "\\usepackage{hyperref}",
            "\\usepackage{natbib}",
            "\\title{" + _escape_latex_title(title) + "}",
            "\\author{}",
            "\\date{}",
            "\\begin{document}",
            "\\maketitle",
        ]
        included: list[str] = []
        missing: list[str] = []
        for section_id in state.get("write_order") or SURVEY_SECTION_SEQUENCE:
            entry = (state.get("sections") or {}).get(section_id, {})
            if isinstance(entry, dict) and entry.get("status") == "skipped":
                continue
            file_rel = str(entry.get("file") or f"drafts/survey/sections/{section_id}.tex")
            try:
                file_path = self.policy.resolve_read(file_rel)
            except ToolAccessDenied:
                missing.append(file_rel)
                continue
            if not file_path.exists():
                missing.append(file_rel)
                continue
            text = file_path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                missing.append(file_rel)
                continue
            pieces.append(text)
            included.append(section_id)
        pieces.extend(["\\bibliographystyle{plainnat}", "\\bibliography{references}", "\\end{document}", ""])
        output_path.write_text("\n\n".join(pieces), encoding="utf-8")
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
        checks = []
        checks.append(_check("has_taxonomy_section", "Taxonomy" in tex or "taxonomy" in tex.lower(), "Survey should include a taxonomy section."))
        checks.append(_check("has_comparative_analysis", "Comparative" in tex or "comparison" in tex.lower(), "Survey should include cross-paper comparison."))
        checks.append(_check("has_open_challenges", "Challenge" in tex or "Open" in tex, "Survey should include open challenges."))
        checks.append(_check("has_future_directions", "Future" in tex or "direction" in tex.lower(), "Survey should include future directions."))
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
        missing_cites = sorted(cited - bib_keys) if bib_keys else []
        checks.append(_check("all_citations_in_bib", not missing_cites, f"Citation keys missing from bib: {missing_cites}"))
        checks.append(_check("has_multiple_citations", len(cited) >= 3, f"Only {len(cited)} unique citation keys found.", level_if_fail="WARN"))

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
    lines = [
        f"# {title}",
        "",
        f"- section_id: {section_id}",
        f"- plan_section_id: {entry.get('plan_section_id', section_id)}",
        f"- covers: {', '.join(str(item) for item in covers) if covers else 'LLM should map taxonomy classes here'}",
        f"- paper_ids: {', '.join(str(item) for item in paper_ids) if paper_ids else 'LLM should select from notes/bib'}",
        "",
        "## Writing Skill",
        "- Write one coherent survey section only; do not write adjacent sections.",
        "- Use taxonomy as the organizing axis, not the synthesis.md design-rationale fuel structure.",
        "- Synthesize evolution, comparison, tensions, and open problems using citations from related_work.bib.",
        "- Do not invent citations. If a needed citation key is missing, state the limitation in prose and avoid fake keys.",
        "- Avoid deterministic template filler; use LLM scholarly judgment for narrative, framing, and taxonomy critique.",
        "",
        "## Global Taxonomy Snapshot",
        json.dumps(plan.get("taxonomy") or {}, ensure_ascii=False, indent=2)[:3000],
        "",
    ]
    return "\n".join(lines)


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
    target_path.write_text(bib_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")


def _bib_keys_optional(policy: WorkspaceAccessPolicy, rel_path: str) -> set[str]:
    try:
        path = policy.resolve_read(rel_path)
        if not path.exists():
            return set()
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    return set(re.findall(r"@\w+\{([^,\s]+)", text))


def _cited_keys(text: str) -> set[str]:
    return _extract_latex_cites(text)


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
