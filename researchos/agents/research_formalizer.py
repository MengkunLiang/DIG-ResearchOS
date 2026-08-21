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
    t45_structured_source_initialization_state,
    validate_orientation_review,
    validate_t45_structured_sources,
    validate_t45_formalization_core,
    write_post_novelty_formalization_manifest,
)
from ..ideation.proposal import repair_t45_proposal_manifest, validate_t45_research_proposal
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import load_project, prepend_resume_prefix


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
                        "query_research_evidence",
                        "targeted_literature_supplement",
                        "validate_t45_formalization_sources",
                        "validate_t45_research_package",
                        "finish_task",
                    ],
                    "max_steps": 45,
                    "max_tokens_total": 120_000,
                    "max_wall_seconds": 900,
                    "temperature": 0.35,
                    "allowed_read_prefixes": ["project.yaml", "ideation/", "literature/"],
                    "allowed_write_prefixes": [
                        "ideation/",
                        "literature/evidence_queries/",
                        "literature/targeted_supplements/",
                        "literature/shallow_read_notes/",
                        "literature/related_work.bib",
                        "literature/literature_manifest.json",
                    ],
                    # Some OpenAI-compatible providers choose `edit_file`
                    # after reading prose. Its implementation delegates to
                    # WriteFileTool, so schema-bound sources retain the same
                    # structural-write guard while prose edits no longer turn
                    # into unknown-tool retries.
                    "allow_edit_file_compatibility": True,
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
        # Formalization sees advisory quality cues while composing the package,
        # but repair-grade quality checks belong to the independent review
        # context.  Requiring the generator to pass those checks before the
        # reviewer sees the package creates a duplicate repair loop without
        # adding evidence.
        quality_diagnostics = collect_t45_quality_diagnostics(workspace)
        if phase != "review":
            quality_diagnostics = [
                item for item in quality_diagnostics if str(item.get("severity") or "") != "repair"
            ]
        return render_prompt(
            self.spec.prompt_template,
            ctx,
            phase=phase,
            project=project,
            orientation=orientation,
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
                "执行 T4.5 的 Orientation-Aware Review and Repair。第一个工具回合并行读取已保存的 blueprint、claim registry、"
                "hypotheses、experiment plan、proposal 和 ideation/orientation_config.yaml，并同时调用 validate_t45_research_package。"
                "不要探测 orientation_config.json。按当前 orientation 一次性审阅完整研究包；发现问题时只修复"
                "受影响的 source artifact，然后重新读取其内容确认一致。最后用 write_structured_file 写 "
                "ideation/orientation_review.json（schema_name='orientation_review', format='json'）。"
                "同一 prose 文件的多个独立修复用一次 edit_file(replacements=[...]) 原子完成，不要每项开启一个模型回合。"
                "在一个协调工具回合中提交所有已识别的 source 修复，每个 source 最多写一次。"
                "复核新增或改写的术语和缩写是否已在首次出现处自然定义且跨文件一致。"
                "第一个工具回合的 validate_t45_research_package(include_orientation_review=false) 结果就是写 review 前的权威检查；"
                "若此后没有修改 source，不要重复调用。只有完成 source 修复后才重新调用一次；"
                "写入 accepted review 后再次调用 validate_t45_research_package(include_orientation_review=true)。"
                "只有所有问题已修复、scores 达到规范且 status='accepted' 时才 finish_task；不要把 novelty audit 的内部标签写入 proposal，"
                "也不要写 runtime 负责生成的 manifest、map、dossier 或 receipt。"
            )
        else:
            initializing, _missing_sources = t45_structured_source_initialization_state(ctx.workspace_dir)
            initialization_instruction = (
                "这是一个全新 formalization：三个结构化来源尚未创建是正常起点。读取上游材料并收到首次 checkpoint 后，"
                "直接用 write_structured_file 一次协调创建 research_blueprint、claim_registry 与 exp_plan；"
                "不要对这三个尚不存在的路径调用 read_file，也不要把创建误当作失败修复。"
                if initializing
                else
                "这是已有 formalization 的恢复：只读取并修复当前 checkpoint 明确指向的既有来源，保留其它已通过内容。"
            )
            message = (
                "不要递归列出 workspace 根目录，也不要读取 state.yaml、_runtime、_DIR_GUIDE 或 user_seeds；"
                "先在一个并行工具回合中精确读取 selected_candidate、hypothesis_brief、novelty_audit 与 synthesis；"
                "不要再次读取这些不变来源。需要定位证据时先调用 "
                "query_research_evidence(stage='t45-formalize', purpose='proposal', max_results<=8)。"
                "只有一个会实质改变设计的外部事实缺口存在时，才调用一次 targeted_literature_supplement，"
                "且 target_record_count<=6。"
                "先调用 validate_t45_formalization_sources；它的最新 `valid` 结果是本轮唯一权威状态，"
                "会覆盖启动时的任何旧诊断。若 valid=false，只用 write_structured_file 修复它指出的 source 或最小同步集合，"
                "不要重写当前错误未指向且已通过自身 schema 的来源。"
                + initialization_instruction
                + "\n"
                "然后再次调用该工具。若 valid=true，不得再重写已通过的 research_blueprint、claim_registry 与 exp_plan；"
                "改为写缺失或不合格的 hypotheses.md 和 proposal/research_proposal.md，并在写后重新读取二者。"
                "正文必须遵守当前 formalization language 与术语/缩写首次定义规则，并以连贯论证覆盖 Proposal 的七项研究功能；"
                "可以在不牺牲可读性的前提下自然合并相邻小节。"
                "不要写 proposal_manifest、post_novelty_formalization、research_dossier、validation_map、"
                "contribution_hypothesis_map 或 kill_criteria；runtime 会从通过验证的 source artifacts 确定性编译它们。"
                "完成五项 source/prose artifact 后调用 finish_task，让 runtime 重新校验并编译派生产物。"
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
            return True, None
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
