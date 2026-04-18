from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .agent import ExecutionContext


def _prompt_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=1)
def get_prompt_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_prompt_dir())),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )


def render_prompt(template_name: str | None, ctx: ExecutionContext, **extra: object) -> str:
    env = get_prompt_env()
    template = env.get_template(template_name or f"{ctx.task_id.lower()}.j2")
    return template.render(
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        run_id=ctx.run_id,
        workspace_dir=str(ctx.workspace_dir),
        inputs={k: str(v) for k, v in ctx.inputs.items()},
        outputs_expected={k: str(v) for k, v in ctx.outputs_expected.items()},
        mode=ctx.mode,
        extra=ctx.extra,
        **extra,
    )

