from __future__ import annotations

"""Agent 抽象、执行上下文与运行结果模型。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


PreHook = Callable[["ExecutionContext"], Awaitable[None]]
PostHook = Callable[["ExecutionContext", "AgentResult"], Awaitable[None]]


@dataclass
class AgentSpec:
    """Agent 的静态声明。

    这里存放“写死在 agent 类上的默认行为”：
    - 用哪个 tier/profile；
    - 允许哪些工具；
    - 默认预算；
    - 默认可读写路径；
    - 以及 pre/post hooks。
    """

    name: str
    model_tier: str
    tool_names: list[str]
    max_steps: int = 30
    max_tokens_total: int = 200_000
    max_wall_seconds: int = 1800
    unlimited_budget: bool = False
    temperature: float = 0.7
    model_override: str | None = None
    llm_profile: str | None = None
    llm_endpoint: str | None = None
    llm_max_context: int | None = None
    allowed_read_prefixes: list[str] = field(
        default_factory=lambda: ["", "user_seeds/", "papers/", "hypotheses/", "exp_plans/"]
    )
    allowed_write_prefixes: list[str] = field(default_factory=list)
    max_validation_retries: int = 3
    pre_hooks: list[PreHook] = field(default_factory=list)
    post_hooks: list[PostHook] = field(default_factory=list)
    prompt_template: str | None = None
    output_schemas: dict[str, str] | None = None  # 输出名称到schema名称的映射
    structured_outputs: dict[str, str] | None = None  # 文件路径到schema名称的映射（Phase 1）


@dataclass
class LLMConfigOverride:
    """状态机或上层 runner 对 LLM 行为的临时覆盖。"""

    profile: str | None = None
    tier: str | None = None
    model: str | None = None
    endpoint: str | None = None
    max_context: int | None = None
    temperature: float | None = None


@dataclass
class BudgetOverride:
    """对 budget 的临时覆盖。"""

    max_steps: int | None = None
    max_tokens: int | None = None
    max_wall_seconds: int | None = None
    unlimited_budget: bool | None = None


@dataclass
class ToolPolicyOverride:
    """对工具权限和工具集的临时覆盖。"""

    allowed_read_prefixes: list[str] | None = None
    allowed_write_prefixes: list[str] | None = None
    extra_tool_names: list[str] = field(default_factory=list)


@dataclass
class ExecutionContext:
    """一次 task run 的完整上下文。

    这是 runtime 在“静态 AgentSpec”和“当前 FSM 状态”之间的桥梁：
    - AgentSpec 决定默认配置；
    - ExecutionContext 决定这一次 run 的具体输入、输出、override 与动态 extra。
    """

    workspace_dir: Path
    project_id: str
    task_id: str
    run_id: str
    inputs: dict[str, Path] = field(default_factory=dict)
    outputs_expected: dict[str, Path] = field(default_factory=dict)
    llm_override: LLMConfigOverride = field(default_factory=LLMConfigOverride)
    budget_override: BudgetOverride = field(default_factory=BudgetOverride)
    tool_policy_override: ToolPolicyOverride = field(default_factory=ToolPolicyOverride)
    mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def input_path(self, key: str) -> Path:
        value = self.inputs.get(key)
        if value is None:
            raise KeyError(f"Input {key} not declared")
        return value

    def output_path(self, key: str) -> Path:
        value = self.outputs_expected.get(key)
        if value is None:
            raise KeyError(f"Output {key} not declared")
        return value


@dataclass
class EffectiveConfig:
    """把 AgentSpec 默认值和 ctx override 合并后的最终配置。"""

    llm_profile: str | None
    llm_tier: str
    llm_model_override: str | None
    llm_endpoint_override: str | None
    llm_max_context_override: int | None
    llm_temperature: float
    max_steps: int
    max_tokens: int
    max_wall_seconds: int
    unlimited_budget: bool
    allowed_read_prefixes: list[str]
    allowed_write_prefixes: list[str]
    tool_names: list[str]


def resolve_effective_config(spec: AgentSpec, ctx: ExecutionContext) -> EffectiveConfig:
    """统一合并静态 spec 与动态 override。"""
    lo = ctx.llm_override
    bo = ctx.budget_override
    to = ctx.tool_policy_override
    return EffectiveConfig(
        llm_profile=lo.profile if lo.profile is not None else spec.llm_profile,
        llm_tier=lo.tier if lo.tier is not None else spec.model_tier,
        llm_model_override=lo.model if lo.model is not None else spec.model_override,
        llm_endpoint_override=lo.endpoint if lo.endpoint is not None else spec.llm_endpoint,
        llm_max_context_override=(
            lo.max_context if lo.max_context is not None else spec.llm_max_context
        ),
        llm_temperature=lo.temperature if lo.temperature is not None else spec.temperature,
        max_steps=bo.max_steps if bo.max_steps is not None else spec.max_steps,
        max_tokens=bo.max_tokens if bo.max_tokens is not None else spec.max_tokens_total,
        max_wall_seconds=(
            bo.max_wall_seconds if bo.max_wall_seconds is not None else spec.max_wall_seconds
        ),
        unlimited_budget=(
            bo.unlimited_budget if bo.unlimited_budget is not None else spec.unlimited_budget
        ),
        allowed_read_prefixes=(
            to.allowed_read_prefixes
            if to.allowed_read_prefixes is not None
            else spec.allowed_read_prefixes
        ),
        allowed_write_prefixes=(
            to.allowed_write_prefixes
            if to.allowed_write_prefixes is not None
            else spec.allowed_write_prefixes
        ),
        tool_names=list(spec.tool_names) + list(to.extra_tool_names),
    )


@dataclass
class AgentResult:
    """一次 run 的最终结果。"""

    ok: bool
    message: str
    outputs_produced: dict[str, Path]
    steps_used: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_seconds: float
    stop_reason: str
    error: str | None = None
    trace_file: Path | None = None
    llm_profile: str | None = None
    llm_tier: str | None = None
    llm_model_used: str | None = None
    llm_endpoint_used: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    STOP_FINISHED = "finished"
    STOP_MAX_STEPS = "max_steps"
    STOP_BUDGET = "budget"
    STOP_ERROR = "error"
    STOP_INTERRUPTED = "interrupted"
    STOP_HUMAN_REJECT = "human_reject"


class Agent(ABC):
    """所有正式 agent / skill agent 的共同基类。"""

    spec: AgentSpec

    def __init__(self, spec: AgentSpec):
        self.spec = spec

    @abstractmethod
    def system_prompt(self, ctx: ExecutionContext) -> str:
        ...

    @abstractmethod
    def initial_user_message(self, ctx: ExecutionContext) -> str:
        ...

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """
        默认校验：
        1. 检查 outputs_expected 里的路径是否存在
        2. 如果 agent spec 声明了 output_schemas，调用 task artifact 校验器
        3. 如果 agent spec 声明了 structured_outputs，逐个校验对应文件 schema

        子类可以覆盖此方法添加自定义校验，但应先调用 super().validate_outputs(ctx)
        """
        # 1. 检查文件存在
        missing: list[str] = []
        for name, path in ctx.outputs_expected.items():
            if not path.exists():
                missing.append(f"{name} -> {path.relative_to(ctx.workspace_dir)}")
        if missing:
            return False, f"缺少以下预期输出: {', '.join(missing)}"

        # 2. 调用 schema 校验器（如果 agent 声明了 output_schemas）
        # P0-1 修复: 添加 schema 级别的校验
        if hasattr(self.spec, 'output_schemas') and self.spec.output_schemas:
            try:
                # 延迟导入避免循环依赖
                from ..schemas.validator import validate_task_artifacts
                ok, err = validate_task_artifacts(ctx.task_id, ctx.workspace_dir)
                if not ok:
                    return False, f"Schema 校验失败: {err}"
            except ImportError:
                # schemas.validator 尚未实现时，跳过 schema 校验
                pass

        # 3. 直接按 structured_outputs 校验文件。它和 task_io_contract 不同：
        #    structured_outputs 是 agent 自身声明的“这个相对路径应符合这个 schema”。
        structured_outputs = self._applicable_structured_outputs(ctx)
        if structured_outputs:
            try:
                from ..schemas.validator import validate_structured_outputs
            except ImportError:
                return True, None
            ok, err = validate_structured_outputs(ctx.workspace_dir, structured_outputs)
            if not ok:
                return False, f"Structured output schema 校验失败: {err}"

        return True, None

    def _applicable_structured_outputs(self, ctx: ExecutionContext) -> dict[str, str]:
        """Return structured outputs that apply to this task/mode run.

        `AgentSpec.structured_outputs` is agent-level config, while several
        agents are reused for multiple tasks or modes.  Filter it through the
        current task's declared outputs so T5 does not require T7 schemas, T7
        does not require T5 schemas, and T7.5 does not require T1 project.yaml.
        """

        if not self.spec.structured_outputs:
            return {}

        if not ctx.outputs_expected:
            return {}

        expected_paths = {path.resolve() for path in ctx.outputs_expected.values()}
        return {
            rel_path: schema_name
            for rel_path, schema_name in self.spec.structured_outputs.items()
            if (ctx.workspace_dir / rel_path).resolve() in expected_paths
        }
