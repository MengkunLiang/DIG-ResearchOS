"""T4.5 research formalization and orientation-aware review agent.

This agent intentionally starts after the novelty auditor has completed.  It
therefore receives a new model context and cannot inherit an ever-growing
search-and-audit conversation when it has to reason about method design,
claims, and experiments.
"""

from __future__ import annotations

from ..ideation.formalization import (
    CLAIM_REGISTRY_REL_PATH,
    ORIENTATION_REVIEW_REL_PATH,
    BLUEPRINT_REL_PATH,
    canonicalize_research_blueprint_file,
    collect_t45_quality_diagnostics,
    compile_t45_derived_artifacts,
    ensure_current_t45_selection_isolation,
    format_t45_repairable_quality_warnings,
    persist_orientation_configuration,
    validate_orientation_review,
    validate_t45_structured_sources,
    validate_t45_formalization_core,
    write_post_novelty_formalization_manifest,
)
from ..ideation.proposal import repair_t45_proposal_manifest, validate_t45_research_proposal
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import load_project, prepend_resume_prefix, read_text_file


class ResearchFormalizerAgent(Agent):
    """Produce and review one unified T4.5 research formalization."""

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "research_formalizer",
                mode=mode,
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "validate_t45_formalization_sources",
                        "list_files",
                        "finish_task",
                    ],
                    "max_steps": 45,
                    "max_tokens_total": 120_000,
                    "max_wall_seconds": 900,
                    "temperature": 0.35,
                    "allowed_read_prefixes": ["", "ideation/", "literature/"],
                    "allowed_write_prefixes": ["ideation/"],
                    "prompt_template": "research_formalizer.j2",
                    "structured_outputs": {
                        BLUEPRINT_REL_PATH: "research_blueprint",
                        CLAIM_REGISTRY_REL_PATH: "claim_registry",
                        "ideation/exp_plan.yaml": "exp_plan",
                    },
                },
            )
        )
        self._mode = mode or "formalize"

    def _phase(self, ctx: ExecutionContext) -> str:
        return str(ctx.mode or ctx.extra.get("phase") or self._mode or "formalize")

    def system_prompt(self, ctx: ExecutionContext) -> str:
        workspace = ctx.workspace_dir
        project = load_project(ctx)
        phase = self._phase(ctx)
        # A workspace may have been paused after selecting a new Candidate
        # under an older runtime. Isolate any old formalization package before
        # it can appear in this fresh Formalizer context as reusable source.
        ensure_current_t45_selection_isolation(workspace)
        canonicalize_research_blueprint_file(workspace)
        orientation = persist_orientation_configuration(workspace)
        structured_sources_ok, structured_sources_error = validate_t45_structured_sources(workspace)
        formalization_ok, formalization_error = validate_t45_formalization_core(workspace)
        quality_diagnostics = collect_t45_quality_diagnostics(workspace)
        artifact_preview = {
            "selected_candidate": read_text_file(workspace / "ideation" / "selected" / "selected_candidate.json", default="")[:6000],
            "hypothesis_brief": read_text_file(workspace / "ideation" / "hypothesis_brief.yaml", default="")[:4000],
            "novelty_audit": read_text_file(workspace / "ideation" / "novelty_audit.md", default="")[:6000],
            "synthesis": read_text_file(workspace / "literature" / "synthesis.md", default="")[:5000],
            "blueprint": read_text_file(workspace / BLUEPRINT_REL_PATH, default="")[:9000],
            "claim_registry": read_text_file(workspace / CLAIM_REGISTRY_REL_PATH, default="")[:9000],
            "hypotheses": read_text_file(workspace / "ideation" / "hypotheses.md", default="")[:7000],
            "exp_plan": read_text_file(workspace / "ideation" / "exp_plan.yaml", default="")[:7000],
            "proposal": read_text_file(workspace / "ideation" / "proposal" / "research_proposal.md", default="")[:9000],
        }
        return render_prompt(
            self.spec.prompt_template,
            ctx,
            phase=phase,
            project=project,
            orientation=orientation,
            artifact_preview=artifact_preview,
            structured_sources_ok=structured_sources_ok,
            structured_sources_error=structured_sources_error or "",
            formalization_ok=formalization_ok,
            formalization_error=formalization_error or "",
            quality_diagnostics=quality_diagnostics,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        phase = self._phase(ctx)
        if phase == "review":
            message = (
                "执行 T4.5 的 Orientation-Aware Review and Repair。先读取已保存的 blueprint、claim registry、"
                "hypotheses、experiment plan、proposal 和 orientation config。按当前 orientation 审阅；发现问题时只修复"
                "受影响的 source artifact，然后重新读取其内容确认一致。最后用 write_structured_file 写 "
                "ideation/orientation_review.json（schema_name='orientation_review', format='json'）。"
                "只有所有问题已修复、scores 达到规范且 status='accepted' 时才 finish_task；不要把 novelty audit 的内部标签写入 proposal。"
            )
        else:
            structured_ok, structured_error = validate_t45_structured_sources(ctx.workspace_dir)
            if structured_ok:
                message = (
                    "T4.5 的 research_blueprint、claim_registry 与 exp_plan 已共同通过确定性验证。"
                    "不要重新生成或覆盖这三份结构化来源。现在只写缺失或不合格的 hypotheses.md 和 "
                    "proposal/research_proposal.md；正文必须遵守当前 formalization language。"
                    "写完后调用 finish_task，让 runtime 校验正文并确定性编译兼容产物。"
                )
            else:
                repair_prefix = (
                    "当前选择的三份 T4.5 结构化来源尚未通过共同研究契约。"
                    f"唯一确定性失败点：{structured_error}。先读取并只修复该错误涉及的 source artifact 或最小一致性集合；"
                    "不要重写无关的 Candidate、novelty audit 或已经一致的 structured source。"
                )
                message = (
                    repair_prefix
                    + "先调用 validate_t45_formalization_sources 获取当前唯一失败点。"
                    "使用 write_structured_file 修复后，再次调用该校验工具；只有它返回 valid=true 才可写 hypotheses.md 或 proposal/research_proposal.md。"
                    "不要写 proposal_manifest、post_novelty_formalization、research_dossier、validation_map 或 kill_criteria；"
                    "运行时会从验证后的 source artifacts 确定性编译这些兼容产物。"
                )
        return prepend_resume_prefix(ctx, message)

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        ok, error = super().validate_outputs(ctx)
        if not ok:
            return ok, error
        workspace = ctx.workspace_dir
        formal_ok, formal_error = validate_t45_formalization_core(workspace)
        if not formal_ok:
            return False, formal_error
        compiled, compile_error = compile_t45_derived_artifacts(workspace, workspace / "ideation" / "novelty_audit.md")
        if not compiled:
            return False, compile_error
        if self._phase(ctx) != "review":
            # The independent reviewer and its acceptance record are required
            # before a Proposal can become a T5-authoritative artifact.
            quality_warning = format_t45_repairable_quality_warnings(collect_t45_quality_diagnostics(workspace))
            return (False, quality_warning) if quality_warning else (True, None)
        repaired, repair_error = repair_t45_proposal_manifest(workspace, workspace / "ideation" / "novelty_audit.md")
        if repair_error and not repaired:
            return False, repair_error
        proposal_ok, proposal_error = validate_t45_research_proposal(workspace, workspace / "ideation" / "novelty_audit.md")
        if not proposal_ok:
            return False, proposal_error
        review_ok, review_error = validate_orientation_review(workspace)
        if not review_ok:
            return False, review_error
        quality_warning = format_t45_repairable_quality_warnings(collect_t45_quality_diagnostics(workspace))
        if quality_warning:
            return False, quality_warning
        write_post_novelty_formalization_manifest(workspace)
        return True, None
