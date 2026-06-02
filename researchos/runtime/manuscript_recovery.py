from __future__ import annotations

"""Deterministic recovery helpers for manuscript-writing runtime boundaries."""

import json
from pathlib import Path
from typing import Any

import yaml

from ..agents.writer import WriterAgent
from ..runtime.agent import ExecutionContext
from ..tools.manuscript import InitializeManuscriptStateTool
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
    return str(data.get("target_venue") or "")


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
