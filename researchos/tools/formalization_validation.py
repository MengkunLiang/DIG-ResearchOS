"""Deterministic T4.5 source-contract diagnostics for the Research Formalizer."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..ideation.formalization import (
    collect_t45_structured_source_errors,
    collect_t45_semantic_errors,
    t45_structured_source_initialization_state,
    validate_orientation_review,
    validate_t45_structured_sources,
)
from ..ideation.t45_semantic_adjudication import semantic_adjudication_scope
from ..ideation.proposal import validate_t45_proposal_source
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class ValidateT45FormalizationSourcesParams(BaseModel):
    """The active workspace and task policy supply all validation inputs."""


class ValidateT45FormalizationSourcesTool(Tool):
    """Report whether blueprint, claim registry, and experiment plan agree."""

    name = "validate_t45_formalization_sources"
    description = (
        "只读校验 T4.5 的 research_blueprint、claim_registry 与 exp_plan 是否构成可写正文的共同研究契约；"
        "返回全部彼此独立的确定性失败项及最小修复集合，不改写文件。"
    )
    parameters_schema = ValidateT45FormalizationSourcesParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    @staticmethod
    def _repair_target(error: str) -> tuple[list[str], str]:
        """Map a deterministic source error to its smallest safe write set."""

        detail = str(error or "")
        if detail.startswith("Missing required structured artifact:"):
            normalized = detail.replace("\\", "/")
            for path in (
                "ideation/research_blueprint.yaml",
                "ideation/claim_registry.yaml",
                "ideation/exp_plan.yaml",
            ):
                if normalized.endswith(path):
                    return (
                        [path],
                        "This source does not exist yet. Create it directly with write_structured_file; do not read it before its first write.",
                    )
        if "UTD formalization must include" in detail or "CCF-A formalization must include" in detail:
            return (
                [
                    "ideation/research_blueprint.yaml",
                    "ideation/claim_registry.yaml",
                    "ideation/exp_plan.yaml",
                ],
                "This is one minimal synchronized change set: add one substantive technical claim to the blueprint and registry, then map it in exp_plan.",
            )
        if "evaluation.ablations or evaluation.mechanism_tests" in detail:
            return (
                ["ideation/research_blueprint.yaml"],
                "Repair research_blueprint.yaml only: each listed component needs component_id/component_ref and a substantive planned_test under evaluation.ablations or evaluation.mechanism_tests. An exp_plan-only edit does not satisfy this check.",
            )
        if "Experiment plan has no experiment mapped" in detail:
            return (
                ["ideation/exp_plan.yaml"],
                "Map every listed active claim to an existing or new exp_plan.experiments entry through claim_ref or claim_refs.",
            )
        if "claim_registry.yaml" in detail and "research_blueprint.yaml" not in detail:
            return (["ideation/claim_registry.yaml"], "Repair the named claim-registry field without changing unrelated sources.")
        if "research_blueprint.yaml" in detail and "claim_registry.yaml" not in detail:
            return (["ideation/research_blueprint.yaml"], "Repair the named blueprint field without changing unrelated sources.")
        if "exp_plan.yaml" in detail:
            return (["ideation/exp_plan.yaml"], "Repair the named experiment-plan field without changing unrelated sources.")
        return (
            [
                "ideation/research_blueprint.yaml",
                "ideation/claim_registry.yaml",
                "ideation/exp_plan.yaml",
            ],
            "The three structured sources form one research contract; repair only the source directly implicated by this diagnostic or the minimal synchronized set.",
        )

    async def execute(self, **kwargs) -> ToolResult:
        errors = collect_t45_structured_source_errors(self.policy.workspace_dir)
        valid = not errors
        error = errors[0] if errors else None
        if valid:
            return ToolResult(
                ok=True,
                content=(
                    "T4.5 structured-source contract passed. You may now write "
                    "hypotheses.md and proposal/research_proposal.md."
                ),
                data={"valid": True, "sources": ["research_blueprint", "claim_registry", "exp_plan"]},
            )
        initialization_required, missing_sources = t45_structured_source_initialization_state(
            self.policy.workspace_dir
        )
        detail = str(error or "unknown error")
        repair_plan = []
        repair_targets: list[str] = []
        for finding in errors:
            targets, action = self._repair_target(finding)
            repair_targets.extend(targets)
            repair_plan.append({"error": finding, "repair_targets": targets, "action": action})
        repair_targets = list(dict.fromkeys(repair_targets))
        findings = "\n".join(
            f"- {item['error']}\n  Minimal repair: {item['action']}"
            for item in repair_plan
        )
        if initialization_required:
            content = (
                "T4.5 formalization is initializing a new research contract. The three structured sources do not "
                "exist yet; this is expected immediately after Candidate selection. Create the complete blueprint, "
                "claim registry, and experiment plan directly with write_structured_file, then call this checkpoint again."
            )
            disposition = "initialization_required"
        else:
            content = (
                "T4.5 structured-source contract has not passed. The following independent deterministic "
                "findings are current; repair their listed minimal source sets together before writing prose:\n"
                + findings
            )
            disposition = "validation_failed"
        return ToolResult(
            ok=True,
            content=content,
            data={
                "valid": False,
                "validation_error": detail,
                "validation_errors": errors,
                "sources": ["research_blueprint", "claim_registry", "exp_plan"],
                "repair_targets": repair_targets,
                "repair_plan": repair_plan,
                "initialization_required": initialization_required,
                "missing_sources": missing_sources,
                # The call itself succeeded and must remain model-readable,
                # while the CLI distinguishes a normal blank-slate creation
                # from an actual repair failure.
                "display_disposition": disposition,
            },
        )


class ValidateT45ResearchPackageParams(BaseModel):
    """Request a researcher-facing package preflight during T4.5 review."""

    include_orientation_review: bool = Field(
        False,
        description="Whether orientation_review.json must also be present and accepted.",
    )


class ValidateT45ResearchPackageTool(Tool):
    """Preflight claims and Proposal prose before ``finish_task``.

    The tool is read-only. It checks researcher-facing sources rather than
    runtime-owned manifests, so a reviewer can see a semantic failure directly
    after editing instead of spending one final-validation loop discovering it.
    """

    name = "validate_t45_research_package"
    description = (
        "只读预检 T4.5 的正式假设与 Proposal 研究论证；可选核验 orientation review。"
        "不写入 manifest 或其它派生产物。"
    )
    parameters_schema = ValidateT45ResearchPackageParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        include_review = bool(kwargs.get("include_orientation_review", False))
        valid, error = validate_t45_proposal_source(self.policy.workspace_dir)
        checks = [{"artifact": "hypotheses_and_proposal", "valid": valid, "error": error or ""}]
        if valid and include_review:
            valid, error = validate_orientation_review(self.policy.workspace_dir)
            checks.append({"artifact": "orientation_review", "valid": valid, "error": error or ""})
        if valid:
            return ToolResult(
                ok=True,
                content=(
                    "T4.5 researcher-facing package preflight passed"
                    + (" including orientation review." if include_review else ".")
                ),
                data={"valid": True, "checks": checks, "include_orientation_review": include_review},
            )
        detail = str(error or "unknown research-package error")
        # Give the Formalizer all currently visible prose concerns in one
        # read-only checkpoint.  The runtime will independently adjudicate
        # only these allowlisted items after a finish request; hard failures
        # stay deterministic and cannot be waived by this list.
        semantic_candidates = [
            item
            for item in collect_t45_semantic_errors(self.policy.workspace_dir)
            if semantic_adjudication_scope(item) is not None
        ]
        semantic_only_failure = bool(semantic_candidates) and detail in semantic_candidates
        semantic_note = ""
        if semantic_candidates:
            semantic_note = (
                " The following prose-only concerns are also visible in the current package; "
                "repair them together when they are genuinely absent, or preserve their explicit current argument "
                "for the independent quoted semantic review at final validation: "
                + " | ".join(semantic_candidates)
            )
        if semantic_only_failure:
            semantic_note += (
                " This checkpoint found no deterministic hard-contract failure behind the reported prose concern; "
                "after a complete self-review, finish_task may request the independent semantic adjudication."
            )
        return ToolResult(
            ok=True,
            content=(
                "T4.5 researcher-facing package preflight has not passed. Repair only the source artifact "
                f"named by this deterministic error: {detail}"
                + semantic_note
            ),
            data={
                "valid": False,
                "validation_error": detail,
                "checks": checks,
                "include_orientation_review": include_review,
                "semantic_review_candidates": semantic_candidates,
                "semantic_only_failure": semantic_only_failure,
                "display_disposition": "validation_failed",
            },
        )
