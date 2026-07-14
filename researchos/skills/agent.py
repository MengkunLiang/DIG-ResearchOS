from __future__ import annotations

"""把外部 skill 包装成 ResearchOS Agent。"""

import json

from jinja2 import StrictUndefined, Template

from ..runtime.errors import ConfigurationError
from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from .loader import Skill
from .tool_aliases import translate_tool_names
from .workflow import parse_skill_workflow, workflow_prompt_block


class SkillAgent(Agent):
    """Skill 的运行时适配器。

    设计目标：
    - 尽量不改 skill 原始 prompt；
    - 只在 runtime 侧补最小上下文与工具翻译；
    - 让 skill 能像普通 Agent 一样被 AgentRunner 驱动。
    """

    def __init__(
        self,
        *,
        skill: Skill,
        available_tools: set[str],
        llm_profile: str | None = None,
    ):
        translated, warnings = translate_tool_names(skill.allowed_tools, available_tools=available_tools)
        if skill.metadata.get("strict_tools") and warnings:
            raise ConfigurationError(
                f"Skill '{skill.name}' declares strict_tools but has unavailable tools: "
                + "; ".join(warnings)
            )
        if "finish_task" in available_tools and "finish_task" not in translated:
            translated.append("finish_task")
        metadata = skill.metadata
        interaction = metadata.get("interaction") if isinstance(metadata.get("interaction"), dict) else {}
        guided = str(interaction.get("mode") or "guided") == "guided"
        workflow = parse_skill_workflow(metadata)
        # Public Skills share the workspace's single LLM connection. Metadata
        # tiers are accepted only as legacy input and no longer select a model.
        model_tier = "standard"
        if guided and "ask_human" in available_tools and "ask_human" not in translated:
            # Guided Skills need one safe channel for a semantic evidence gap
            # discovered after deterministic file checks have passed.
            translated.append("ask_human")
        if workflow and "update_skill_workflow" in available_tools and "update_skill_workflow" not in translated:
            translated.append("update_skill_workflow")
        allowed_write_prefixes = list(metadata.get("allowed_write_prefixes", [""]))
        if guided:
            intake_prefix = f"user_inputs/{skill.name}/"
            if intake_prefix not in allowed_write_prefixes:
                allowed_write_prefixes.append(intake_prefix)
        super().__init__(
            AgentSpec(
                name=f"skill_{skill.name}",
                model_tier=model_tier,
                tool_names=translated,
                # A guided Skill may need additional evidence checks or several
                # rounds of human follow-up.  Its lifecycle must never stop
                # because an arbitrary per-SKILL token/step ceiling was reached.
                # Provider/context failures, cancellation, human pauses, and
                # output validation still remain explicit, recoverable stops.
                max_steps=0,
                max_tokens_total=0,
                max_wall_seconds=int(metadata.get("max_wall_seconds", 1800)),
                unlimited_budget=True,
                temperature=float(metadata.get("temperature", 0.2)),
                llm_profile=None,
                prompt_template=None,
                allowed_read_prefixes=list(metadata.get("allowed_read_prefixes", [""])),
                allowed_write_prefixes=allowed_write_prefixes,
            )
        )
        self.skill = skill
        self.use_jinja = bool(metadata.get("use-jinja", False))
        self.translation_warnings = warnings
        self.workflow = workflow

    def system_prompt(self, ctx: ExecutionContext) -> str:
        body = self.skill.body
        if self.use_jinja:
            # 少数 skill 会显式声明 use-jinja，此时才对正文做模板渲染。
            body = Template(body, undefined=StrictUndefined).render(
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                workspace_dir=str(ctx.workspace_dir),
                inputs={k: str(v) for k, v in ctx.inputs.items()},
                outputs_expected={k: str(v) for k, v in ctx.outputs_expected.items()},
                mode=ctx.mode,
                extra=ctx.extra,
            )
        warning_block = ""
        if self.translation_warnings:
            warning_block = "\n".join(f"- {warning}" for warning in self.translation_warnings)
            warning_block = f"## Skill Translation Warnings\n{warning_block}\n\n"
        header = (
            "# Runtime Context\n"
            f"- workspace_dir: {ctx.workspace_dir}\n"
            f"- skill_dir: {self.skill.skill_dir}\n"
            f"- task_id: {ctx.task_id}\n"
            "- Edit is mapped to Write; provide full file content when editing.\n\n"
        )
        readable = ", ".join(self.spec.allowed_read_prefixes) or "(none)"
        writable = ", ".join(self.spec.allowed_write_prefixes) or "(none)"
        header += (
            "# Workspace Capability Boundary\n"
            f"- Read only these declared workspace areas: {readable}\n"
            f"- Write only these declared workspace areas: {writable}\n"
            f"- Enabled capability profiles: {', '.join(self.skill.capability_profiles) or 'workspace_navigation'}\n"
            f"- Available tools for this session: {', '.join(self.spec.tool_names)}\n"
            "- Start with the verified inputs listed below. Do not probe unrelated workspace paths just because they are conventional names. "
            "When a needed material is absent, request it through the guided follow-up protocol instead of attempting an unauthorized read.\n\n"
        )
        session_path = ctx.extra.get("skill_session_path")
        selected_inputs = ctx.extra.get("skill_selected_inputs")
        if session_path:
            header += (
                "# Guided Skill Session\n"
                f"- session state: {session_path}\n"
                "- The runtime checked the declared required inputs before this LLM turn.\n"
                "- Read the session state when prior-turn decisions or input provenance matter.\n"
            )
            if selected_inputs:
                header += "- verified inputs:\n" + "\n".join(
                    f"  - {key}: {value}" for key, value in selected_inputs.items()
                ) + "\n"
            header += "\n"
        workspace_mode = str(ctx.extra.get("skill_workspace_mode") or "standalone")
        intake_packet = str(ctx.extra.get("skill_intake_packet_path") or "").strip()
        if intake_packet:
            header += (
                "# Material Intake Protocol\n"
                f"- workspace mode: {workspace_mode}\n"
                f"- deterministic intake packet: {intake_packet}\n"
                "- Read the intake packet and the selected inputs before substantive work. Existing project files are candidates, not proof that their claims are sufficient.\n"
                "- If a source, result, citation, venue decision, or constraint is semantically missing, write "
                f"`user_inputs/{self.skill.name}/_followup_request.md` with the exact gap, why it matters, and a preferred answer/file path. Then call ask_human and wait for the response.\n"
                "- Do not create final deliverables by guessing missing material. Record the resolved answer in the follow-up file before continuing.\n\n"
            )
        header += (
            "# Experimental-Detail Integrity\n"
            "- A concrete dataset, benchmark, split, baseline, metric, seed, compute budget, implementation command, or performance number may be used only when an allowed input or audited workspace artifact explicitly identifies it. Record the source path and section/field whenever that detail affects a plan or claim.\n"
            "- This is a provenance rule, not a metric ban: AUUC, Qini, accuracy, F1, and any other metric are valid when the current project's allowed inputs or audited artifacts explicitly declare them.\n"
            "- If the user asks for a plan but the detail is not yet sourced, describe it to the user as “待验证提议” or “暂未确定”; reserve raw values such as `proposed_not_verified` and `unknown` for structured files. State what material would resolve it. Do not turn a plausible convention into an existing protocol.\n"
            "- Never infer experimental details from the project topic, a method name, an adjacent paper, a generic benchmark convention, or an earlier example. Missing protocol inputs require a focused human question, not a fabricated default.\n\n"
        )
        header += (
            "# 面向用户的沟通规则\n"
            "- 默认使用清楚、自然的中文。先说明已经检查了什么、当前能做什么、还需要什么，再给出下一步。\n"
            "- 不把 `schema`、`artifact`、`stage`、`section`、内部 Agent 名称或工具限制直接当作解释；只有文件路径、证据边界或用户需要采取的动作确有必要时才提及。\n"
            "- 用“材料准备”而不是 intake，用“论文阅读笔记”而不是 evidence card/note card，用“论文中的相关位置或段落”而不是 section anchor，用“输出文件”而不是 artifact。专业的学术术语（例如 taxonomy、baseline、ablation、claim、Related Work）可以保留英文。\n"
            "- 首次处理前先读材料清单和已验证输入。若材料不完整，准确指出缺少的内容以及可上传、粘贴或提供的标识符，不要泛泛地说“材料不足”。\n"
            "- 保留完整用户可读信息，不使用 `...` 截断字段，不用连续空行制造视觉间隔；长文本由终端按当前宽度自然换行。\n\n"
            "# Interaction And Output Style\n"
            "- Produce a compact, readable research interaction: state the decision, the evidence boundary, and the next action. Use a short paragraph, a table, or a flat list when it improves scanning; do not simulate a human-input panel in prose.\n"
            "- Ask a real question only through ask_human. If that tool is unavailable, persist the named blocker and finish or pause according to the Skill contract; never emit a faux question that the runtime must guess how to handle.\n"
            "- Keep Markdown structurally clean. Use one blank line between real blocks, no repeated empty spacer lines, no decorative separator walls, no character-level truncation, and no copy-pasted feature narration.\n"
            "- For candidate comparisons, use concise tables or labeled sections with complete values. Preserve source paths, uncertainty, and next actions; do not hide material text behind ellipses.\n\n"
        )
        workflow_block = workflow_prompt_block(self.workflow) if self.workflow else ""
        return header + workflow_block + warning_block + body

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        # CLI run-skill 时，用户请求会放在 ctx.extra["user_request"]。
        user_request = ctx.extra.get("user_request")
        if user_request:
            return str(user_request)
        return f"Execute the '{self.skill.name}' skill per your instructions."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """Require a durable evidence-aware manifest from integrated Skills."""

        ok, error = super().validate_outputs(ctx)
        if not ok or self.workflow is None:
            return ok, error
        interaction = self.skill.metadata.get("interaction")
        outputs = interaction.get("outputs") if isinstance(interaction, dict) else []
        manifest_path = ""
        for output in outputs if isinstance(outputs, list) else []:
            if not isinstance(output, dict):
                continue
            output_id = str(output.get("id") or "")
            path = str(output.get("path") or "")
            if output_id == "workflow_manifest" or path.endswith("_manifest.json"):
                manifest_path = path
                break
        if not manifest_path:
            return False, "integrated Skill must declare a JSON workflow_manifest output"
        path = ctx.workspace_dir / manifest_path
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"workflow manifest is not readable JSON: {manifest_path}: {exc}"
        if not isinstance(manifest, dict):
            return False, f"workflow manifest must be a JSON object: {manifest_path}"
        phases = manifest.get("phases")
        if not isinstance(phases, list):
            return False, f"workflow manifest must include a phases list: {manifest_path}"
        by_id = {
            str(item.get("id")): item
            for item in phases
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        missing = [phase.phase_id for phase in self.workflow.phases if phase.phase_id not in by_id]
        if missing:
            return False, "workflow manifest is missing declared phases: " + ", ".join(missing)
        unresolved: list[str] = []
        for phase in self.workflow.phases:
            item = by_id[phase.phase_id]
            status = str(item.get("status") or "").strip()
            if status not in {"completed", "skipped"}:
                unresolved.append(f"{phase.phase_id}={status or 'missing'}")
                continue
            if not str(item.get("summary") or "").strip():
                unresolved.append(f"{phase.phase_id}=missing_summary")
            if "evidence_boundary" not in item:
                unresolved.append(f"{phase.phase_id}=missing_evidence_boundary")
        if unresolved:
            return False, "workflow manifest has unresolved phases: " + ", ".join(unresolved)
        return True, None
