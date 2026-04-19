from __future__ import annotations

"""把外部 skill 包装成 ResearchOS Agent。"""

from jinja2 import StrictUndefined, Template

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
        if "finish_task" in available_tools and "finish_task" not in translated:
            translated.append("finish_task")
        metadata = skill.metadata
        model_tier = str(metadata.get("model_tier") or metadata.get("tier") or "medium")
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
                allowed_write_prefixes=list(metadata.get("allowed_write_prefixes", [""])),
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
        return header + warning_block + body

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        # CLI run-skill 时，用户请求会放在 ctx.extra["user_request"]。
        user_request = ctx.extra.get("user_request")
        if user_request:
            return str(user_request)
        return f"Execute the '{self.skill.name}' skill per your instructions."
