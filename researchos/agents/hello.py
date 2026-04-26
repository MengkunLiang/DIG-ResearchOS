from __future__ import annotations

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import prepend_resume_prefix


class HelloAgent(Agent):
    def __init__(self):
        super().__init__(
            build_agent_spec(
                "hello",
                defaults={
                    "model_tier": "medium",
                    "tool_names": ["echo", "write_file", "read_file", "finish_task"],
                    "max_steps": 10,
                    "max_tokens_total": 20_000,
                    "max_wall_seconds": 300,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": [""],
                    "allowed_write_prefixes": [""],
                    "prompt_template": "hello.j2",
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return render_prompt(self.spec.prompt_template, ctx)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return prepend_resume_prefix(ctx, "请按 system prompt 的要求完成任务。")

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err
        hello_path = ctx.outputs_expected.get("hello_file")
        if hello_path is None:
            return False, "缺少 hello_file 输出声明"
        if hello_path.read_text(encoding="utf-8").strip() != "Hello, Runtime!":
            return False, "hello_file 内容必须是 'Hello, Runtime!'"
        return True, None
