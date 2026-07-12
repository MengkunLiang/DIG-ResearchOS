from __future__ import annotations

"""把外部 skill 包装成 ResearchOS Agent。"""

from jinja2 import StrictUndefined, Template

from ..runtime.errors import ConfigurationError
from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from .loader import Skill
from .tool_aliases import translate_tool_names


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
        model_tier = str(metadata.get("model_tier") or metadata.get("tier") or "medium")
        if guided and "ask_human" in available_tools and "ask_human" not in translated:
            # Guided Skills need one safe channel for a semantic evidence gap
            # discovered after deterministic file checks have passed.
            translated.append("ask_human")
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
                max_steps=int(metadata.get("max_steps", 20)),
                max_tokens_total=int(metadata.get("max_tokens_total", 100_000)),
                max_wall_seconds=int(metadata.get("max_wall_seconds", 1800)),
                temperature=float(metadata.get("temperature", 0.2)),
                llm_profile=llm_profile or metadata.get("llm_profile"),
                prompt_template=None,
                allowed_read_prefixes=list(metadata.get("allowed_read_prefixes", [""])),
                allowed_write_prefixes=allowed_write_prefixes,
            )
        )
        self.skill = skill
        self.use_jinja = bool(metadata.get("use-jinja", False))
        self.translation_warnings = warnings

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
        return header + warning_block + body

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        # CLI run-skill 时，用户请求会放在 ctx.extra["user_request"]。
        user_request = ctx.extra.get("user_request")
        if user_request:
            return str(user_request)
        return f"Execute the '{self.skill.name}' skill per your instructions."
