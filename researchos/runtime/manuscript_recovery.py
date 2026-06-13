from __future__ import annotations

"""Deterministic recovery helpers for manuscript-writing runtime boundaries."""

import json
from pathlib import Path
from typing import Any

import yaml

from ..agents.writer import WriterAgent
from ..runtime.agent import ExecutionContext
from ..tools.manuscript import (
    AssembleManuscriptTool,
    AuditManuscriptClaimsTool,
    AuditWritingCraftTool,
    InitializeManuscriptStateTool,
)
from ..tools.external_experiment import AuditPaperClaimsTool
from ..tools.workspace_policy import WorkspaceAccessPolicy


T8_SECTION_PLAN_REQUIRED_INPUTS = [
    "drafts/outline.md",
    "drafts/manuscript_resource_index.json",
    "drafts/section_plan.json",
    "drafts/evidence_plan.json",
    "drafts/figure_table_plan.json",
    "drafts/alignment_matrix.json",
]


def can_repair_t8_section_plan(workspace_dir: Path) -> bool:
    """Return true when all deterministic inputs for T8 section-plan exist."""

    for rel_path in T8_SECTION_PLAN_REQUIRED_INPUTS:
        path = workspace_dir / rel_path
        if not path.exists() or path.stat().st_size <= 0:
            return False
    return True


async def repair_t8_section_plan_outputs(
    workspace_dir: Path,
    *,
    target_venue: str | None = None,
) -> tuple[bool, str | None]:
    """Rebuild `paper_state.json` and section outlines from approved T8 plans.

    This intentionally writes only mechanical state files. It does not generate
    manuscript prose or scientific claims.
    """

    workspace_dir = workspace_dir.resolve()
    if not can_repair_t8_section_plan(workspace_dir):
        return False, "T8-SECTION-PLAN 缺少 outline/resource/section/evidence/figure plan，无法确定性修复"

    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["", "drafts/"],
        allowed_write_prefixes=["drafts/"],
    )
    venue = target_venue
    if venue is None:
        venue = _load_target_venue(workspace_dir)

    tool = InitializeManuscriptStateTool(policy)
    result = await tool.execute(
        outline_path="drafts/outline.md",
        resource_index_path="drafts/manuscript_resource_index.json",
        section_plan_path="drafts/section_plan.json",
        evidence_plan_path="drafts/evidence_plan.json",
        figure_table_plan_path="drafts/figure_table_plan.json",
        alignment_matrix_path="drafts/alignment_matrix.json",
        state_output_path="drafts/paper_state.json",
        section_outline_dir="drafts/section_outlines",
        target_venue=str(venue or ""),
    )
    if not result.ok:
        return False, result.content or result.error or "T8-SECTION-PLAN 确定性修复失败"

    ctx = ExecutionContext(
        workspace_dir=workspace_dir,
        project_id="validator",
        task_id="T8-SECTION-PLAN",
        run_id="t8-section-plan-recovery",
        mode="section_plan",
        extra={"phase": "section_plan"},
    )
    ok, err = WriterAgent(mode="section_plan").validate_outputs(ctx)
    if not ok:
        return False, err
    return True, None


def _load_target_venue(workspace_dir: Path) -> str:
    project_path = workspace_dir / "project.yaml"
    if not project_path.exists():
        return ""
    try:
        data: Any = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    constraints = data.get("constraints") if isinstance(data.get("constraints"), dict) else {}
    return str(data.get("target_venue") or constraints.get("target_venue") or "")


def _load_venue_style(workspace_dir: Path) -> str:
    style_path = workspace_dir / "drafts" / "writing_style.json"
    if not style_path.exists():
        return "auto"
    try:
        data = json.loads(style_path.read_text(encoding="utf-8"))
    except Exception:
        return "auto"
    if not isinstance(data, dict):
        return "auto"
    style = str(data.get("venue_style") or "auto").strip()
    return style if style in {"is", "ccf_a", "both", "auto"} else "auto"


def _load_writing_template_selection(workspace_dir: Path) -> dict[str, str]:
    style_path = workspace_dir / "drafts" / "writing_style.json"
    try:
        data = json.loads(style_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "template_family": str(data.get("template_family") or data.get("template_type") or "").strip().lower(),
        "template_id": str(data.get("template_id") or "").strip().lower(),
        "writing_language": str(data.get("writing_language") or "auto").strip().lower() or "auto",
    }


def can_refresh_t8_manuscript_outputs(workspace_dir: Path) -> bool:
    """Return true when section drafts exist and manuscript audits can be refreshed."""

    sections_dir = workspace_dir / "drafts" / "sections"
    if not sections_dir.exists():
        return False
    required = [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "conclusion",
    ]
    for section_id in required:
        if not any((sections_dir / f"{section_id}{suffix}").exists() for suffix in (".tex", ".md")):
            return False
    return True


async def refresh_t8_manuscript_outputs(
    workspace_dir: Path,
    *,
    target_venue: str | None = None,
    venue_style: str | None = None,
    refresh_style_variant_audits: bool = True,
) -> tuple[bool, str | None]:
    """Reassemble paper.tex and refresh mechanical audits from section files.

    This is a deterministic recovery boundary for T8-DRAFT/T8-REVISE resume.
    It does not rewrite section prose, review responses, or patch lists. If
    `venue_style=both`, the main paper is assembled without overwriting existing
    style-variant manuscripts; variant craft audits are refreshed only when the
    variant files already exist.
    """

    workspace_dir = workspace_dir.resolve()
    if not can_refresh_t8_manuscript_outputs(workspace_dir):
        return False, "缺少 drafts/sections 下的核心章节，无法刷新 T8 manuscript 输出"

    style = venue_style or _load_venue_style(workspace_dir) or "auto"
    template_selection = _load_writing_template_selection(workspace_dir)
    venue = target_venue if target_venue is not None else _load_target_venue(workspace_dir)
    assembly_style = "auto" if style == "both" else style
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["", "drafts/", "literature/", "experiments/", "ideation/"],
        allowed_write_prefixes=["drafts/"],
    )

    assembled = await AssembleManuscriptTool(policy).execute(
        section_dir="drafts/sections",
        output_path="drafts/paper.tex",
        outline_path="drafts/outline.md",
        target_venue=str(venue or ""),
        venue_style=assembly_style,
        template_family=template_selection.get("template_family", ""),
        template_id=template_selection.get("template_id", ""),
        writing_language=template_selection.get("writing_language", "auto"),
    )
    if not assembled.ok:
        return False, assembled.content or assembled.error or "T8 manuscript 拼装失败"

    claim_audit = await AuditManuscriptClaimsTool(policy).execute(
        paper_path="drafts/paper.tex",
        output_path="drafts/manuscript_audit.md",
        resource_index_path="drafts/manuscript_resource_index.json",
    )
    if not claim_audit.ok:
        return False, claim_audit.content or claim_audit.error or "T8 claim audit 刷新失败"

    craft_audit = await AuditWritingCraftTool(policy).execute(
        paper_path="drafts/paper.tex",
        sections_dir="drafts/sections",
        paper_state_path="drafts/paper_state.json",
        alignment_matrix_path="drafts/alignment_matrix.json",
        cdr_claim_ledger_path="drafts/cdr_claim_ledger.json",
        venue_style=style,
        output_path="drafts/craft_audit.md",
        also_audit_style_variants=bool(style == "both" and refresh_style_variant_audits),
    )
    if not craft_audit.ok:
        return False, craft_audit.content or craft_audit.error or "T8 craft audit 刷新失败"

    if (
        (workspace_dir / "drafts" / "experiment_evidence_pack.json").exists()
        and (workspace_dir / "drafts" / "result_to_claim.json").exists()
    ):
        paper_claim_audit = await AuditPaperClaimsTool(policy).execute(
            paper_path="drafts/paper.tex",
            evidence_pack_path="drafts/experiment_evidence_pack.json",
            result_to_claim_path="drafts/result_to_claim.json",
            output_path="drafts/paper_claim_audit.md",
        )
        if not paper_claim_audit.ok:
            return False, paper_claim_audit.content or paper_claim_audit.error or "T8 paper claim audit 刷新失败"

    return True, None


def paper_state_semantics(workspace_dir: Path) -> str:
    """Small diagnostic helper used by tests and CLI status checks."""

    path = workspace_dir / "drafts" / "paper_state.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("semantics") or "")
