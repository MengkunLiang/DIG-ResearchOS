"""Deterministic T4.5 source-contract diagnostics for the Research Formalizer."""

from __future__ import annotations

from pydantic import BaseModel

from ..ideation.formalization import validate_t45_structured_sources
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class ValidateT45FormalizationSourcesParams(BaseModel):
    """The active workspace and task policy supply all validation inputs."""


class ValidateT45FormalizationSourcesTool(Tool):
    """Report whether blueprint, claim registry, and experiment plan agree."""

    name = "validate_t45_formalization_sources"
    description = (
        "只读校验 T4.5 的 research_blueprint、claim_registry 与 exp_plan 是否构成可写正文的共同研究契约；"
        "返回唯一的确定性失败原因，不改写文件。"
    )
    parameters_schema = ValidateT45FormalizationSourcesParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        valid, error = validate_t45_structured_sources(self.policy.workspace_dir)
        if valid:
            return ToolResult(
                ok=True,
                content=(
                    "T4.5 structured-source contract passed. You may now write "
                    "hypotheses.md and proposal/research_proposal.md."
                ),
                data={"valid": True, "sources": ["research_blueprint", "claim_registry", "exp_plan"]},
            )
        detail = str(error or "unknown error")
        if "UTD formalization must include" in detail or "CCF-A formalization must include" in detail:
            repair_hint = (
                " This is one minimal synchronized change set: add the substantive technical claim to "
                "research_blueprint.active_claim_ids and claim_registry.claims, then map it in exp_plan."
            )
        else:
            repair_hint = ""
        return ToolResult(
            ok=True,
            content=(
                "T4.5 structured-source contract has not passed. Repair only the "
                f"source or minimal synchronized source set implicated by this deterministic error: {detail}"
                + repair_hint
            ),
            data={
                "valid": False,
                "validation_error": detail,
                "sources": ["research_blueprint", "claim_registry", "exp_plan"],
            },
        )
