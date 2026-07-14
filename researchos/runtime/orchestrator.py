from __future__ import annotations

"""AgentRunner 主循环。"""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
from io import StringIO
import inspect
import json
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from ..pydantic_compat import model_dump
from ..orchestration.task_io_contract import get_task_io
from .agent import Agent, AgentResult, EffectiveConfig, ExecutionContext, resolve_effective_config
from .budget import BudgetTracker
from .config import RuntimeSettings
from .errors import BudgetExceeded, LLMProviderError, RecoverableRuntimePause, ToolAccessDenied, ToolError
from .llm_client import LLMClient, ModelBinding
from .logger import get_logger
from .manuscript_recovery import (
    can_refresh_t8_manuscript_outputs,
    can_repair_t8_section_plan,
    refresh_t8_manuscript_outputs,
    repair_t8_section_plan_outputs,
)
from .message import Message, Role, ToolCall, is_empty_assistant
from .progress import (
    CliProgressEmitter,
    build_tool_narrative,
    describe_task_artifacts,
    format_cli_message,
    next_step_for_task,
    safe_relative,
    summarize_reader_note_progress,
    summarize_progress_markdown,
    summarize_tool_result,
)
from .t2_recovery import finalize_t2_outputs, validate_t2_finalize_manifest
from .abstract_sweep import run_abstract_sweep, run_abstract_sweep_with_reader
from .t2_config import get_effective_reader_read_params, load_t2_finalize_config
from .t3_recovery import prepare_t3_resume_artifacts
from .t3_notes_manifest import validate_t3_input_fingerprints
from .artifact_fingerprints import validate_t45_fingerprint_report
from .task_recovery import prepare_generic_resume_artifacts
from .run_logger import RunLogger
from ..agents.ideation import (
    T4_GATE1_ARTIFACTS,
    ensure_t4_evidence_pool,
    prepare_t4_context_pack,
    refresh_t4_gate1_progress,
    validate_t4_gate1_ready,
)
from ..ideation.config import load_t4_evolution_settings
from ..ideation.directives import current_population_context
from ..ideation.evolution_controller import IdeaEvolutionController
from ..ideation.legacy_projection import project_gate1_population
from ..ideation.llm_roles import LLMJsonRoleInvoker, LLMIdeaEvolver, LLMIdeaGenerator, LLMIdeaScorer, T4RoleCallConfig
from ..ideation.models import EvolutionPhase, HumanCompositionCompatibility
from ..ideation.prerun import has_current_t4_prerun_confirmation
from ..ideation.selected_compilation import ensure_t45_pre_novelty_brief
from ..ideation.state import T4ArtifactStore
from ..ui.idea_evolution_renderer import render_t4_evolution_phase
from .trace import NullTraceWriter, TraceWriter
from ..tools.base import Tool, ToolResult
from ..tools.workspace_policy import WorkspaceAccessPolicy
from ..tools.external_experiment import validate_external_executor_ready
from ..tools.external_experiment import AuditPaperClaimsTool
from ..tools.human_gate import HumanInputUnavailable, HumanInterface
from ..tools.paper_save_tools import SavePapersRawTool
from ..tools.registry import ToolBuildContext, ToolRegistry
from .agent_params import get_agent_mode_params, get_budget_escalation_policy, get_global_timeout, get_retry_policy
from ..literature_identity import is_paper_note_file
from ..tools.scout_progress import ScoutProgressLogger
from rich.console import Console

if TYPE_CHECKING:
    from ..tools.workspace_policy import WorkspaceAccessPolicy


T2_AUTO_PERSIST_SEARCH_TOOLS = frozenset(
    {
        "multi_source_search",
        "search_papers",
        "semantic_scholar_search",
        "arxiv_search",
        "openalex_search",
        "crossref_search",
        "elsevier_scopus_search",
        "informs_search",
        "fetch_outgoing_citations",
    }
)
TOOL_FAILURE_CACHE_NAMES = frozenset({"fetch_paper_pdf"})
TOOL_CONTEXT_CONTENT_LIMITS = {
    # PDF 文本工具是 T3 上下文膨胀的主要来源。工具自身也有上限，这里再加
    # runtime 兜底，防止未来工具改动或异常 PDF 解析再次把长文本塞进模型。
    "extract_paper_sections": 12000,
    "extract_pdf_text": 50000,
}
T2_CROSS_DOMAIN_QUERY_BUCKET_ALIASES = {
    "adjacent": "adjacent_field",
    "adjacent-field": "adjacent_field",
    "adjacent_field": "adjacent_field",
    "cross-domain": "adjacent_field",
    "cross_domain": "adjacent_field",
    "nearby-field": "adjacent_field",
    "nearby_field": "adjacent_field",
    "theory": "theory_bridge",
    "theory-bridge": "theory_bridge",
    "theory_bridge": "theory_bridge",
    "theoretical": "theory_bridge",
}


class _T4OperationEnvelope:
    """Small typed-read adapter for a durable T4 operation envelope."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(cls, value: object) -> "_T4OperationEnvelope":
        if not isinstance(value, dict):
            raise ValueError("T4 operation artifact must be a JSON object")
        return cls(value)


def _t4_recap_title(candidate: dict[str, object], *, limit: int = 72) -> str:
    """Use the declared display label for CLI telemetry, never a long pitch."""

    text = str(
        candidate.get("display_title")
        or candidate.get("title_short_zh")
        or candidate.get("short_title")
        or candidate.get("title")
        or "未命名方向"
    )
    compact = " ".join(text.split())
    effective_limit = min(limit, 32) if re.search(r"[\u4e00-\u9fff]", compact) else limit
    return compact if len(compact) <= effective_limit else compact[: max(0, effective_limit - 3)] + "..."


def _normalize_t2_query_bucket(raw: object) -> str:
    value = str(raw or "").strip().casefold()
    if not value:
        return ""
    return T2_CROSS_DOMAIN_QUERY_BUCKET_ALIASES.get(value, value.replace(" ", "_"))


class HookExecutionError(RuntimeError):
    """hook 执行失败时使用的统一异常。"""


class AgentRunner:
    """驱动单个 agent 完成一次完整 run。

    这里集中处理：
    - budget 与步数限制；
    - LLM 调用与消息拼装；
    - tool 调用、异常兜底与结果回填；
    - finish_task 触发后的输出校验；
    - trace 写入。
    """

    def __init__(
        self,
        agent: Agent,
        tool_registry: ToolRegistry,
        llm_client: LLMClient,
        human_interface: HumanInterface,
        runtime_settings: RuntimeSettings | None = None,
        workspace_policy_factory: Callable[[ExecutionContext, EffectiveConfig], "WorkspaceAccessPolicy"]
        | None = None,
    ):
        self.agent = agent
        self.tool_registry = tool_registry
        self.llm = llm_client
        self.human = human_interface
        # runner 默认使用共享 runtime 配置；测试里若不传，则安全回退到默认值。
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.workspace_policy_factory = workspace_policy_factory or self._default_policy_factory
        self.log = get_logger(f"runner.{agent.spec.name}")
        self.global_timeout = get_global_timeout()
        self.retry_policy = get_retry_policy()
        self.budget_escalation_policy = get_budget_escalation_policy()
        self.progress = CliProgressEmitter(
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
            verbosity=self.runtime_settings.ui.verbosity,
            no_color=self.runtime_settings.ui.no_color,
            json_events=self.runtime_settings.ui.json_events,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )
        self._t4_durable_recap_keys: set[str] = set()

    @staticmethod
    def _default_policy_factory(
        ctx: ExecutionContext, eff: EffectiveConfig
    ) -> "WorkspaceAccessPolicy":
        from ..tools.workspace_policy import WorkspaceAccessPolicy

        allowed_write_prefixes = list(eff.allowed_write_prefixes)
        allowed_survey_section_ids: frozenset[str] | None = None

        # A T3.6 section worker has historically inherited drafts/survey/ and
        # could therefore rewrite Abstract, Conclusion, outlines, or trigger
        # assembly while writing Introduction.  The task I/O contract already
        # declares exactly one section output; make it an enforced capability
        # boundary instead of a prompt-only convention.
        if ctx.task_id.startswith("T3.6-SEC-"):
            section_id = str(ctx.extra.get("section_id") or "").strip()
            if not section_id:
                section_id = ctx.task_id.removeprefix("T3.6-SEC-").lower().replace("-", "_")
            section_path = ctx.outputs_expected.get("section")
            if section_path is None:
                section_path = ctx.workspace_dir / "drafts" / "survey" / "sections" / f"{section_id}.tex"
            try:
                section_rel = section_path.relative_to(ctx.workspace_dir).as_posix()
            except ValueError:
                section_rel = f"drafts/survey/sections/{section_id}.tex"
            scoped_writes = [
                section_rel,
                "drafts/survey/survey_state.json",
            ]
            allowed_write_prefixes = [
                path
                for path in scoped_writes
                if WorkspaceAccessPolicy.path_allowed(path, allowed_write_prefixes)
            ]
            allowed_survey_section_ids = frozenset({section_id})

        return WorkspaceAccessPolicy(
            workspace_dir=ctx.workspace_dir,
            allowed_read_prefixes=eff.allowed_read_prefixes,
            allowed_write_prefixes=allowed_write_prefixes,
            task_id=ctx.task_id,
            allowed_survey_section_ids=allowed_survey_section_ids,
        )

    @staticmethod
    def _is_timeout_provider_error(exc: LLMProviderError) -> bool:
        text = str(exc).lower()
        if not text:
            return False
        timeout_markers = (
            "timeouterror",
            "timeout error",
            "timed out",
            "timeout",
            "readtimeout",
            "connecttimeout",
            "超时",
        )
        fatal_markers = (
            "authentication",
            "permissiondenied",
            "permission denied",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "rate limit",
            "ratelimit",
            "context_length",
            "context window",
            "badrequest",
            "bad request",
        )
        return any(marker in text for marker in timeout_markers) and not any(
            marker in text for marker in fatal_markers
        )

    @classmethod
    def _is_recoverable_provider_error(cls, exc: LLMProviderError) -> bool:
        text = str(exc).lower()
        if not text:
            return False
        timeout_or_connection_markers = (
            "timeouterror",
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "connectionerror",
            "connection error",
            "server disconnected",
            "超时",
        )
        if any(marker in text for marker in timeout_or_connection_markers):
            return True
        fatal_markers = (
            "authentication",
            "permissiondenied",
            "permission denied",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "context_length",
            "context window",
            "badrequest",
            "bad request",
        )
        if any(marker in text for marker in fatal_markers):
            return False
        transient_markers = (
            "temporarily unavailable",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
            "overloaded",
            "超时",
        )
        return cls._is_timeout_provider_error(exc) or any(marker in text for marker in transient_markers)

    def _llm_provider_recovery_policy(self) -> tuple[int, float, float]:
        """Return bounded, user-facing recovery settings for provider outages.

        ``LLMClient.chat`` already tries the primary/fallback chain once.  A
        recovery *batch* below deliberately starts that chain over after a
        short cooldown, which is what lets a briefly overloaded provider
        recover without forcing the researcher to resume the whole project.
        The two legacy keys remain accepted so existing user settings do not
        silently change behaviour.
        """

        raw_batches = self.retry_policy.get("llm_provider_retry_batches")
        if raw_batches is None:
            raw_batches = self.retry_policy.get("llm_timeout_pause_after_cooldowns")
        try:
            batches = int(raw_batches)
        except (TypeError, ValueError):
            batches = 10
        # Historically ``0`` meant "do not auto-pause".  Bound it now rather
        # than allowing an unattended infinite retry loop.
        if batches <= 0:
            batches = 10
        batches = max(1, min(batches, 50))

        raw_cooldown = self.retry_policy.get("llm_provider_initial_cooldown_seconds")
        if raw_cooldown is None:
            raw_cooldown = self.retry_policy.get("llm_timeout_cooldown_seconds")
        try:
            cooldown = float(raw_cooldown)
        except (TypeError, ValueError):
            cooldown = 10.0
        # A legacy zero is useful in tests and explicit local development, but
        # the checked-in user-facing default is ten seconds.
        cooldown = max(0.0, min(cooldown, 300.0))

        try:
            long_cooldown = float(self.retry_policy.get("llm_provider_long_cooldown_seconds", 20))
        except (TypeError, ValueError):
            long_cooldown = 20.0
        return batches, cooldown, max(0.0, min(long_cooldown, 900.0))

    @staticmethod
    def _public_provider_error_message(exc: LLMProviderError) -> str:
        """Return a safe CLI message without endpoint, key, or SDK details."""

        text = str(exc).casefold()
        if any(marker in text for marker in ("authentication", "invalid_api_key", "unauthorized", "permissiondenied")):
            return "模型服务配置未通过验证；请检查已选择服务的凭据和模型名称后 resume。"
        if any(marker in text for marker in ("context_length", "context window", "badrequest", "bad request")):
            return "模型请求未被接受；请检查模型上下文设置或项目输入后 resume。"
        return "模型服务暂时不可用；已保留当前进度，可稍后 resume。"

    async def _choose_llm_provider_recovery(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        failed_batches: int,
        retry_batches: int,
        cooldown_seconds: float,
        long_cooldown_seconds: float,
    ) -> tuple[str, float]:
        """Choose a safe next action after a recoverable provider failure.

        Returns ``("retry", seconds)`` or ``("pause", 0)``.  The human gate
        is only opened after the bounded automatic retries are exhausted.
        """

        if failed_batches < retry_batches:
            return "retry", cooldown_seconds

        self.progress.stage_human_action_required(
            task_id=ctx.task_id,
            gate_id="runtime_llm_provider_recovery",
            reason="模型服务连续不可用，需要确认是否继续等待。",
        )
        human_started = time.time()
        try:
            selection = await self.human.present_gate(
                gate_id="runtime_llm_provider_recovery",
                presentation={
                    "_title": "模型服务暂时不可用",
                    "_description": (
                        f"系统已自动重试 {failed_batches} 次，但当前请求仍未完成。"
                        "项目进度已经安全保留，请选择下一步。"
                    ),
                    "task_id": ctx.task_id,
                    "retry_count": failed_batches,
                },
                options=[
                    {"id": "retry_now", "label": "立即再试", "description": "重新从首选模型服务链开始请求。"},
                    {
                        "id": "wait_20_seconds",
                        "label": "等待 20 秒后重试",
                        "description": "适合服务刚刚超时或负载较高的情况。",
                    },
                    {"id": "pause", "label": "暂停项目", "description": "保留进度，稍后使用 resume 继续。"},
                ],
            )
        except HumanInputUnavailable:
            return "pause", 0.0
        finally:
            budget.exclude_wall_time(time.time() - human_started)

        option_id = str((selection or {}).get("option_id") or "pause")
        self.progress.stage_gate_resolved(
            task_id=ctx.task_id,
            gate_id="runtime_llm_provider_recovery",
            decision=option_id,
        )
        if option_id == "retry_now":
            return "retry", 0.0
        if option_id == "wait_20_seconds":
            return "retry", long_cooldown_seconds
        return "pause", 0.0

    async def _wait_before_llm_provider_retry(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        seconds: float,
        attempt: int,
        retry_batches: int,
    ) -> None:
        """Wait without consuming the active agent wall-clock budget."""

        wait_seconds = max(0.0, seconds)
        if wait_seconds:
            self.progress.emit(
                f"[Runtime] 模型服务暂时不可用，{wait_seconds:g} 秒后重试（{attempt}/{retry_batches}）。",
                important=True,
            )
            self._record_skill_progress(
                ctx,
                step=budget.steps,
                step_limit="unlimited" if budget.unlimited_budget else budget.max_steps,
                phase="waiting_runtime",
                detail=f"模型服务暂时不可用，等待 {wait_seconds:g} 秒后重新请求。",
            )
            started = time.time()
            await asyncio.sleep(wait_seconds)
            budget.exclude_wall_time(time.time() - started)
        else:
            self.progress.emit(
                f"[Runtime] 模型服务暂时不可用，正在立即重试（{attempt}/{retry_batches}）。",
                important=True,
            )

    async def run(self, ctx: ExecutionContext) -> AgentResult:
        """执行一次完整 agent run。"""
        self.progress.configure_observability(workspace=ctx.workspace_dir)
        started = time.time()
        eff = resolve_effective_config(self.agent.spec, ctx)
        dynamic_tool_names = self.tool_registry.dynamic_tool_names_for(self.agent.spec.name)
        if dynamic_tool_names:
            # MCP tools are configured by the workspace owner at startup. They
            # augment, rather than replace, the capability contract declared by
            # the Agent or Skill.
            eff.tool_names = list(dict.fromkeys([*eff.tool_names, *dynamic_tool_names]))
        max_agent_runtime = int(self.global_timeout.get("max_agent_runtime") or 0)
        effective_wall_seconds = eff.max_wall_seconds
        if max_agent_runtime > 0:
            effective_wall_seconds = min(effective_wall_seconds, max_agent_runtime)
        budget = BudgetTracker(
            max_steps=eff.max_steps,
            max_tokens=eff.max_tokens,
            max_wall_seconds=effective_wall_seconds,
            unlimited_budget=eff.unlimited_budget,
        )
        trace_file: Path | None = None
        if self.runtime_settings.debug.enable_trace:
            trace_file = self.runtime_settings.traces_dir(ctx.workspace_dir) / f"{ctx.run_id}.jsonl"
            trace = TraceWriter(trace_file)
            trace.write_run_start(
                run_id=ctx.run_id,
                agent_name=self.agent.spec.name,
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                workspace_dir=ctx.workspace_dir,
            )
        else:
            trace = NullTraceWriter()

        run_logger = RunLogger(
            ctx.workspace_dir,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
        )
        run_logger.event(
            "RUN_START",
            run_id=ctx.run_id,
            task=ctx.task_id,
            agent=self.agent.spec.name,
            project_id=ctx.project_id,
        )
        run_logger.event(
            "TASK_START",
            task=ctx.task_id,
            agent=self.agent.spec.name,
            mode=ctx.mode or ctx.extra.get("phase"),
        )

        self._print_task_start_summary(ctx, eff)
        try:
            task_io = get_task_io(ctx.task_id)
        except KeyError:
            # Programmatic callers and unit tests may use a lightweight Agent
            # without a state-machine contract. Keep their established CLI
            # behaviour instead of inventing a formal research-stage panel.
            task_io = None
        # Standalone Skills are not state-machine nodes, but they still have
        # declared inputs, outputs and durable sessions.  Route them through
        # the same observable stage protocol so their Markdown and Tool events
        # receive the Rich rendering instead of falling back to raw `[Tool]`
        # lines.  Ad-hoc programmatic agents keep the historical lightweight
        # behaviour below.
        if task_io is not None or ctx.task_id.startswith("SKILL_"):
            required_input_keys = {
                str(key)
                for key in (task_io.get("required_inputs") or [])
                if isinstance(key, str)
            } if task_io is not None else set(ctx.inputs)
            self.progress.stage_started(
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                inputs=ctx.inputs,
                outputs=ctx.outputs_expected,
                required_input_keys=required_input_keys,
                agent=self.agent.spec.name,
                mode=str(ctx.mode or ctx.extra.get("phase") or "-"),
                is_resume=self._is_resume_run(ctx),
            )
        self.progress.agent_start(
            task_id=ctx.task_id,
            agent=self.agent.spec.name,
            phase=ctx.mode or ctx.extra.get("phase") or "-",
            objective=str(ctx.extra.get("task_description") or self._infer_task_description(ctx)),
            inputs=[
                safe_relative(path, ctx.workspace_dir) or str(path)
                for path in list(ctx.inputs.values())
            ],
            expected_outputs=[
                safe_relative(path, ctx.workspace_dir) or str(path)
                for path in list(ctx.outputs_expected.values())
            ],
            expected_artifacts=describe_task_artifacts(ctx.task_id),
            llm_tier=eff.llm_tier,
            step_limit="unlimited" if eff.unlimited_budget else str(eff.max_steps),
        )
        if (
            ctx.task_id == "T4"
            and not self._t4_gate1_user_selection_exists(ctx)
            and not has_current_t4_prerun_confirmation(ctx.workspace_dir)
        ):
            # Write and render the first durable checkpoint before provider work
            # begins. This lets the CLI distinguish "preparing evidence" from a
            # silent provider wait without exposing private reasoning.
            self._refresh_t4_gate1_progress(ctx, active_path=None)
        self._record_skill_progress(
            ctx,
            step=0,
            step_limit="unlimited" if eff.unlimited_budget else eff.max_steps,
            phase="starting",
            detail="已建立运行上下文，正在准备第一组可执行动作。",
        )
        last_model_used: str | None = None
        last_endpoint_used: str | None = None
        stop_reason = AgentResult.STOP_ERROR
        error_msg: str | None = None

        primary_binding, primary_endpoint = self.llm.resolve(
            profile=eff.llm_profile,
            tier=eff.llm_tier,
            model_override=eff.llm_model_override,
            endpoint_override=eff.llm_endpoint_override,
            max_context_override=eff.llm_max_context_override,
        )[0]
        # ``max_context`` in routing is an auditable fallback rather than a
        # claim about a provider's live deployment.  Discover once before
        # building context-sensitive tools, then resolve again so read_file,
        # history truncation, and the first model call agree on one capacity.
        discover_context = getattr(self.llm, "discover_context_window", None)
        if eff.llm_max_context_override is None and callable(discover_context):
            discovery = discover_context(primary_binding, primary_endpoint)
            if inspect.isawaitable(discovery):
                await discovery
            primary_binding, primary_endpoint = self.llm.resolve(
                profile=eff.llm_profile,
                tier=eff.llm_tier,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=None,
            )[0]

        context_source: str | None = None
        context_info_getter = getattr(self.llm, "get_context_window_info", None)
        if callable(context_info_getter):
            context_info = context_info_getter(
                primary_binding,
                primary_endpoint,
                explicit_override=eff.llm_max_context_override is not None,
            )
            source_labels = {
                "provider_metadata": "服务端元数据",
                "configured_fallback": "配置回退",
                "explicit_override": "显式上限",
            }
            context_source = str(context_info.source)
            self.progress.emit(
                f"[Runtime] 模型上下文：{context_info.max_context:,} tokens "
                f"（{source_labels.get(context_info.source, context_info.source)}）",
                verbose_only=True,
            )

        policy = self.workspace_policy_factory(ctx, eff)
        if ctx.task_id == "T4":
            self._maybe_prepare_t4_context_pack_before_prompt(ctx)
        build_ctx = ToolBuildContext(
            policy=policy,
            human=self.human,
            skill_dir=Path(ctx.extra["skill_dir"]) if "skill_dir" in ctx.extra else None,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            llm_model=primary_binding.model,
            llm_tier=eff.llm_tier,
            llm_max_context=primary_binding.max_context,
            llm_context_source=context_source,
            skill_session_id=str(ctx.extra.get("skill_session_id") or "") or None,
        )
        tool_map = self.tool_registry.build(eff.tool_names, build_ctx)
        tool_schemas = self.tool_registry.to_openai_schemas(tool_map)

        # Agents that prepare large, source-grounded prompts can use the same
        # discovered context capacity as the provider-bound tool layer.  This
        # avoids a stale per-agent character cap while keeping a smaller model
        # on a safe, explicit file-reading path.
        ctx.extra["runtime_context_window"] = primary_binding.max_context
        sys_msg = Message.system(self.agent.system_prompt(ctx), step=0)
        user_msg = Message.user(self.agent.initial_user_message(ctx), step=0)
        messages: list[Message] = [sys_msg, user_msg]
        trace.write_message(sys_msg)
        trace.write_message(user_msg)

        empty_count = 0
        nudge_count = 0
        validation_fails = 0
        validation_retry_limit = int(self.agent.spec.max_validation_retries)
        budget_extensions_used = 0
        validation_extensions_used = 0
        llm_timeout_cooldowns_used = 0
        tool_failure_cache: dict[tuple[str, str], Message] = {}

        try:
            await self._maybe_run_t1_startup_gate(ctx, tool_map, messages, trace)

            t9_pre_finalized = await self._maybe_finalize_t9_submission_before_hooks(ctx)
            if t9_pre_finalized:
                deterministic_pre_finalized = True
                stop_reason = AgentResult.STOP_FINISHED
                error_msg = None
            else:
                deterministic_pre_finalized = False

            # pre-hook 允许是同步或异步 callable；若返回 (ok, err) 且 ok=False，
            # 这里会统一转换成可读错误，而不是让 CLI 因 await 非协程直接崩溃。
            if not deterministic_pre_finalized:
                for hook in self.agent.spec.pre_hooks:
                    await self._run_pre_hook(hook, ctx)

            t2_pre_finalized = False
            if not deterministic_pre_finalized:
                t2_pre_finalized = await self._maybe_finalize_t2_before_llm(ctx)
            t3_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized):
                t3_pre_finalized = await self._maybe_finalize_t3_before_llm(ctx)
            t4_pre_finalized = False
            t35_prepared = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized):
                t35_prepared = await self._maybe_prepare_t35_before_llm(ctx, policy)
            t36_section_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized):
                t36_section_pre_finalized = await self._maybe_finalize_t36_section_before_llm(ctx)
            t36_visuals_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
            ):
                t36_visuals_pre_finalized = await self._maybe_finalize_t36_visuals_before_llm(ctx)
            t36_compile_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
            ):
                t36_compile_pre_finalized = await self._maybe_finalize_t36_compile_before_llm(ctx)
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
            ):
                t4_pre_finalized = await self._maybe_finalize_t4_before_llm(ctx)
            t4_pre_novelty_selected = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
            ):
                t4_pre_novelty_selected = await self._maybe_advance_t4_pre_novelty_selection(ctx)
            t4_gate1_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_pre_novelty_selected
            ):
                t4_gate1_pre_finalized = await self._maybe_finalize_t4_gate1_before_llm(ctx)
            t4_evolution_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_pre_novelty_selected
                or t4_gate1_pre_finalized
            ):
                t4_evolution_pre_finalized = await self._maybe_run_t4_evolution_before_llm(
                    ctx=ctx,
                    eff=eff,
                    budget=budget,
                )
            if t4_evolution_pre_finalized:
                deterministic_pre_finalized = True
            if t4_pre_novelty_selected:
                deterministic_pre_finalized = True
            t45_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
            ):
                if ctx.task_id == "T4.5":
                    ensure_t45_pre_novelty_brief(ctx.workspace_dir)
                t45_pre_finalized = await self._maybe_finalize_t45_before_llm(ctx)
            external_wait_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
            ):
                external_wait_pre_finalized = await self._maybe_finalize_external_wait_before_llm(ctx)
            paper_claim_audit_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
            ):
                paper_claim_audit_pre_finalized = await self._maybe_finalize_paper_claim_audit_before_llm(ctx, policy)
            t8_section_plan_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or
                t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
            ):
                t8_section_plan_pre_finalized = await self._maybe_finalize_t8_section_plan_before_llm(
                    ctx,
                    policy,
                )
            t8_manuscript_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or
                t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
                or t8_section_plan_pre_finalized
            ):
                t8_manuscript_pre_finalized = await self._maybe_finalize_t8_manuscript_before_llm(ctx)
            deterministic_pre_finalized = deterministic_pre_finalized or (
                    t2_pre_finalized
                    or t3_pre_finalized
                    or t36_section_pre_finalized
                    or t36_visuals_pre_finalized
                    or t36_compile_pre_finalized
                    or t4_pre_finalized
                    or t4_gate1_pre_finalized
                    or t45_pre_finalized
                    or external_wait_pre_finalized
                    or paper_claim_audit_pre_finalized
                    or t8_section_plan_pre_finalized
                    or t8_manuscript_pre_finalized
                )
            if deterministic_pre_finalized:
                stop_reason = AgentResult.STOP_FINISHED
                error_msg = None

            while not deterministic_pre_finalized:
                # 每进入一轮 while，就代表一次“agent step”。
                budget.tick_step()
                step_limit = "unlimited" if budget.unlimited_budget else str(budget.max_steps)
                self._record_skill_progress(
                    ctx,
                    step=budget.steps,
                    step_limit=step_limit,
                    phase="preparing_step",
                    detail="正在整理当前 workspace 产物并请求下一组可执行动作。",
                )
                run_logger.event(
                    "AGENT_STEP",
                    task=ctx.task_id,
                    step=budget.steps,
                    tokens=budget.tokens_in + budget.tokens_out,
                    cost_usd=f"{budget.cost_usd:.4f}",
                )

                # 每5步输出一次进度
                if budget.steps % 5 == 1 or budget.steps == 1:
                    self.progress.agent_step(
                        agent=self.agent.spec.name,
                        step=budget.steps,
                        step_limit=step_limit,
                        tokens=budget.tokens_in + budget.tokens_out,
                        cost_usd=budget.cost_usd,
                    )
                try:
                    budget.check()
                except BudgetExceeded as exc:
                    extended, budget_extensions_used = await self._maybe_offer_budget_extension(
                        ctx=ctx,
                        budget=budget,
                        exc=exc,
                        used_extensions=budget_extensions_used,
                    )
                    if extended:
                        continue
                    stop_reason = AgentResult.STOP_BUDGET
                    error_msg = str(exc)
                    break

                # 如果上下文太长，这里会按“完整 tool call group”为单位裁掉旧消息，
                # 同时插入一条 runtime note，提醒模型去读 artifact 而不是假装记得历史。
                messages = self._maybe_truncate(messages, primary_binding)
                messages = self._repair_openai_tool_message_sequence(messages)

                provider_retry_batches, provider_cooldown, provider_long_cooldown = self._llm_provider_recovery_policy()
                provider_failures_this_request = 0
                provider_pause_requested = False
                while True:
                    try:
                        run_logger.event(
                            "LLM_CALL",
                            task=ctx.task_id,
                            step=budget.steps,
                            tier=eff.llm_tier,
                            profile=eff.llm_profile,
                            tool_count=len(tool_schemas or []),
                            provider_recovery_attempt=provider_failures_this_request,
                        )
                        llm_resp = await self._await_llm_with_progress(
                            ctx=ctx,
                            step=budget.steps,
                            progress_step_limit=step_limit,
                            messages=[item.to_openai_dict() for item in messages],
                            tools=tool_schemas or None,
                            temperature=eff.llm_temperature,
                            tier=eff.llm_tier,
                            profile=eff.llm_profile,
                            model_override=eff.llm_model_override,
                            endpoint_override=eff.llm_endpoint_override,
                            max_context_override=eff.llm_max_context_override,
                            timeout=int(self.global_timeout.get("llm_call") or 120),
                            max_retries_per_model=int(self.retry_policy.get("llm_retries") or 2),
                            retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
                        )
                    except LLMProviderError as exc:
                        # Keep complete provider diagnostics in the durable run
                        # log.  The terminal must not expose endpoint URLs,
                        # model chains, SDK internals, or credential hints.
                        run_logger.event(
                            "ERROR",
                            task=ctx.task_id,
                            step=budget.steps,
                            kind="llm_provider",
                            message=str(exc)[:300],
                        )
                        if not self._is_recoverable_provider_error(exc):
                            stop_reason = AgentResult.STOP_ERROR
                            error_msg = self._public_provider_error_message(exc)
                            provider_pause_requested = True
                            break

                        llm_timeout_cooldowns_used += 1
                        provider_failures_this_request += 1
                        action, wait_seconds = await self._choose_llm_provider_recovery(
                            ctx=ctx,
                            budget=budget,
                            failed_batches=provider_failures_this_request,
                            retry_batches=provider_retry_batches,
                            cooldown_seconds=provider_cooldown,
                            long_cooldown_seconds=provider_long_cooldown,
                        )
                        if action == "retry":
                            await self._wait_before_llm_provider_retry(
                                ctx=ctx,
                                budget=budget,
                                seconds=wait_seconds,
                                attempt=provider_failures_this_request,
                                retry_batches=provider_retry_batches,
                            )
                            # A human-confirmed retry starts a fresh bounded
                            # batch.  Automatic retries retain their count.
                            if provider_failures_this_request >= provider_retry_batches:
                                provider_failures_this_request = 0
                            continue

                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = "模型服务持续不可用；当前进度已保留，可在服务恢复后 resume。"
                        self.progress.emit(
                            "[Runtime] 模型服务持续不可用，项目已暂停并保留当前进度。",
                            important=True,
                        )
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="waiting_runtime",
                            detail=error_msg,
                        )
                        self._refresh_t4_gate1_progress(ctx, active_path=None, paused_reason=error_msg)
                        provider_pause_requested = True
                        break
                    else:
                        break

                if provider_pause_requested:
                    break

                last_model_used = llm_resp.model_used
                last_endpoint_used = llm_resp.endpoint_used
                self._record_skill_progress(
                    ctx,
                    step=budget.steps,
                    step_limit=step_limit,
                    phase="llm_response_received",
                    detail="模型已返回；正在校验并执行声明的工具调用。",
                )
                budget.add_tokens(llm_resp.tokens_in, llm_resp.tokens_out, llm_resp.cost_usd)
                run_logger.event(
                    "LLM_RESULT",
                    task=ctx.task_id,
                    step=budget.steps,
                    model=llm_resp.model_used,
                    endpoint=llm_resp.endpoint_used,
                    tokens_in=llm_resp.tokens_in,
                    tokens_out=llm_resp.tokens_out,
                    duration_ms=llm_resp.duration_ms,
                )
                assistant_msg = self._parse_llm_response(llm_resp, step=budget.steps)
                trace.write_llm_response(llm_resp, assistant_msg)

                # 空回复不是立刻判死刑，而是先给模型一次 nudged retry 的机会。
                if is_empty_assistant(assistant_msg):
                    empty_count += 1
                    if empty_count > self.runtime_settings.agent_behavior.max_empty_reply:
                        stop_reason = AgentResult.STOP_ERROR
                        error_msg = f"{self.runtime_settings.agent_behavior.max_empty_reply} consecutive empty replies"
                        break
                    nudge = Message.user(
                        "你刚才没有输出任何内容也没有调用工具。请继续推进任务，或在确认完成后调用 finish_task。",
                        step=budget.steps,
                    )
                    messages.append(nudge)
                    trace.write_message(nudge)
                    continue

                empty_count = 0
                messages.append(assistant_msg)

                # 输出 Agent 的文本回复（如果有）。普通状态说明默认只在 verbose 显示；
                # 但同一轮如果要 ask_human，正文通常包含用户必须看到的草案、
                # 候选清单或决策上下文，不能被简洁模式吞掉。
                if assistant_msg.content and assistant_msg.content.strip():
                    self.progress.agent_markdown(
                        task_id=ctx.task_id,
                        agent=self.agent.spec.name,
                        content=assistant_msg.content,
                        human_action_context=any(tc.name == "ask_human" for tc in assistant_msg.tool_calls),
                        verbose_only=not any(tc.name == "ask_human" for tc in assistant_msg.tool_calls),
                    )

                post_tool_runtime_notes: list[Message] = []
                # 如果模型在文本里向用户提问/要求选择，但没有显式调用 ask_human，
                # runtime 必须先等待人类输入。即便同一轮还混有 read/write 等工具，
                # 也不能继续执行那些工具，否则会复现“模型问了但没有输入框仍继续跑”的问题。
                if self._looks_like_human_interaction_request(assistant_msg) and not any(
                    tc.name == "ask_human" for tc in assistant_msg.tool_calls
                ):
                    if "ask_human" not in tool_map:
                        trace.write_message(assistant_msg)
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = (
                            "Agent asked for human input but ask_human is not available in this task. "
                            "Paused so the user can answer or the task tool policy can be fixed."
                        )
                        break
                    tool_call = ToolCall.create(
                        "ask_human",
                        {
                            "question": self._build_autobridged_human_question(
                                assistant_msg.content or "请补充必要的人类输入。"
                            ),
                            "suggestions": [],
                        },
                    )
                    assistant_msg.tool_calls = [tool_call]
                    post_tool_runtime_notes.append(Message.user(
                        "[Runtime] 检测到 Agent 向用户提问/要求选择但未调用 ask_human，"
                        "已自动转成 ask_human，并阻止本轮其它工具继续执行；如果输入不可用将暂停等待 resume。",
                        step=budget.steps,
                    ))

                # 如果模型只说话不调用工具，runtime 会反复提醒它：
                # 要么继续推进，要么明确 finish_task。
                if not assistant_msg.tool_calls:
                    if not self._looks_like_human_interaction_request(assistant_msg):
                        nudge_count += 1
                        if nudge_count > self.runtime_settings.agent_behavior.max_nudge_finish:
                            trace.write_message(assistant_msg)
                            stop_reason = AgentResult.STOP_ERROR
                            error_msg = "agent 多次只输出文本但未调用工具"
                            break
                        nudge = Message.user(
                            "你没有调用任何工具。如果任务已完成，请调用 finish_task；否则请继续调用适当工具。",
                            step=budget.steps,
                        )
                        trace.write_message(assistant_msg)
                        messages.append(nudge)
                        trace.write_message(nudge)
                        continue

                nudge_count = 0
                self._ensure_ask_human_questions_are_self_contained(assistant_msg)
                if any(tc.name == "ask_human" for tc in assistant_msg.tool_calls):
                    ask_call = next(tc for tc in assistant_msg.tool_calls if tc.name == "ask_human")
                    blocked_tools = [tc.name for tc in assistant_msg.tool_calls if tc.name != "ask_human"]
                    if blocked_tools:
                        assistant_msg.tool_calls = [ask_call]
                        post_tool_runtime_notes.append(Message.user(
                            "[Runtime] 本轮包含 ask_human，已先等待用户输入；"
                            f"延后执行同轮其它工具: {', '.join(blocked_tools)}。",
                            step=budget.steps,
                        ))
                trace.write_message(assistant_msg)
                # 输出工具调用信息
                if len(assistant_msg.tool_calls) > 0:
                    tool_names = [tc.name for tc in assistant_msg.tool_calls]
                    if len(tool_names) > 1:
                        self._emit(
                            f"[{self.agent.spec.name} Agent] 本轮将按顺序处理 {len(tool_names)} 个工具调用："
                            f"{', '.join(tool_names)}",
                            verbose_only=True,
                        )
                    for tc in assistant_msg.tool_calls:
                        run_logger.tool_call(tc.name, tc.arguments, step=budget.steps)
                        self.progress.stage_tool_call(
                            task_id=ctx.task_id,
                            run_id=ctx.run_id,
                            tool_name=tc.name,
                            arguments=tc.arguments,
                        )
                        narrative = build_tool_narrative(
                            task_id=ctx.task_id,
                            agent=self.agent.spec.name,
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            workspace_dir=ctx.workspace_dir,
                            verbose=self.runtime_settings.ui.verbose,
                        )
                        self.progress.tool_call(
                            agent=self.agent.spec.name,
                            tool_name=tc.name,
                            narrative=narrative,
                        )
                    if len(tool_names) == 1:
                        self._emit(
                            f"[{self.agent.spec.name} Agent] 正在调用工具：{tool_names[0]}",
                            verbose_only=True,
                        )

                # T4 Gate1 has ordered durable artifacts.  Executing its calls
                # one by one makes both artifact dependencies and the CLI
                # progress truthful.  Other tasks retain parallel tool calls.
                if self._requires_sequential_tool_execution(ctx, assistant_msg.tool_calls):
                    tool_msgs = []
                    for tc in assistant_msg.tool_calls:
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="tool_running",
                            tool_name=tc.name,
                            detail=f"正在执行工具 {tc.name}。",
                        )
                        tool_msgs.append(
                            await self._execute_one_tool_call(
                                tc,
                                tool_map,
                                ctx=ctx,
                                policy=policy,
                                budget=budget,
                                step=budget.steps,
                                tool_failure_cache=tool_failure_cache,
                                run_logger=run_logger,
                            )
                        )
                else:
                    for tc in assistant_msg.tool_calls:
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="tool_running",
                            tool_name=tc.name,
                            detail=f"正在调度工具 {tc.name}。",
                        )
                    tool_msgs = await asyncio.gather(
                        *[
                            self._execute_one_tool_call(
                                tc,
                                tool_map,
                                ctx=ctx,
                                policy=policy,
                                budget=budget,
                                step=budget.steps,
                                tool_failure_cache=tool_failure_cache,
                                run_logger=run_logger,
                            )
                            for tc in assistant_msg.tool_calls
                        ]
                    )

                finish_requested = False
                pause_requested = False
                pause_reason: str | None = None
                for tool_call, tool_msg in zip(assistant_msg.tool_calls, tool_msgs):
                    messages.append(tool_msg)
                    trace.write_message(tool_msg)
                    tool_ok = not bool(tool_msg.metadata.get("is_error"))
                    tool_summary, output_path = summarize_tool_result(
                        tool_name=tool_call.name,
                        ok=tool_ok,
                        content=tool_msg.content,
                        data=tool_msg.metadata.get("data") if isinstance(tool_msg.metadata, dict) else {},
                        error=tool_msg.metadata.get("error") if isinstance(tool_msg.metadata, dict) else None,
                        metadata=tool_msg.metadata if isinstance(tool_msg.metadata, dict) else {},
                        verbose=self.runtime_settings.ui.verbose,
                    )
                    tool_data = (
                        tool_msg.metadata.get("data")
                        if isinstance(tool_msg.metadata, dict)
                        and isinstance(tool_msg.metadata.get("data"), dict)
                        else {}
                    )
                    tool_error = (
                        tool_msg.metadata.get("error")
                        if isinstance(tool_msg.metadata, dict)
                        else None
                    )
                    self.progress.stage_tool_result(
                        task_id=ctx.task_id,
                        run_id=ctx.run_id,
                        tool_name=tool_call.name,
                        ok=tool_ok,
                        data=tool_data,
                        error=str(tool_error) if tool_error else None,
                    )
                    self.progress.tool_result(
                        agent=self.agent.spec.name,
                        tool_name=tool_call.name,
                        ok=tool_ok,
                        result_summary=tool_summary,
                        output_path=safe_relative(output_path, ctx.workspace_dir) or output_path,
                        next_step=next_step_for_task(ctx.task_id, ok=tool_ok) if not tool_ok else None,
                        duration_ms=tool_msg.duration_ms,
                        data=tool_data,
                    )
                    self._record_skill_progress(
                        ctx,
                        step=budget.steps,
                        step_limit=step_limit,
                        phase="tool_completed" if tool_ok else "tool_failed",
                        tool_name=tool_call.name,
                        detail=("工具完成：" if tool_ok else "工具失败：") + tool_summary,
                    )
                    if ctx.task_id == "T4" and tool_call.name in {"write_file", "write_structured_file", "append_file"}:
                        # The tool result itself already announces the durable
                        # write. Refresh the on-disk checkpoint silently so a
                        # second, misleading "0/6" line is never printed.
                        self._refresh_t4_gate1_progress(
                            ctx,
                            active_path=output_path if tool_ok else None,
                            announce=False,
                        )
                        if tool_ok:
                            self._emit_t4_durable_candidate_recap(ctx, output_path)
                    if ctx.task_id == "T4" and tool_call.name == "log_t4_ideation_progress" and tool_ok:
                        self._update_t4_public_activity_from_event(ctx, tool_data)
                        # Candidate milestones and Gate1 artifact checkpoints
                        # measure different things. The candidate card above is
                        # sufficient here; retain only the durable state for
                        # the next heartbeat.
                        self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                    if (
                        ctx.task_id == "T2"
                        and tool_ok
                        and self._is_t2_raw_pool_read(tool_call, tool_data)
                    ):
                        checkpoint = self._t2_raw_pool_checkpoint_message(
                            ctx=ctx,
                            tool_data=tool_data,
                            step=budget.steps,
                        )
                        if checkpoint is not None:
                            post_tool_runtime_notes.append(checkpoint)
                    if tool_call.name == "finish_task" and not tool_msg.metadata.get("is_error"):
                        finish_requested = True
                    if self._is_recoverable_tool_pause(tool_call.name, tool_msg):
                        pause_requested = True
                        pause_reason = tool_msg.content or "需要用户输入，但当前输入不可用。"
                for note in post_tool_runtime_notes:
                    messages.append(note)
                    trace.write_message(note)

                if pause_requested:
                    stop_reason = AgentResult.STOP_INTERRUPTED
                    error_msg = pause_reason
                    self.progress.emit(f"[Runtime] 当前任务暂停：{pause_reason}", important=True)
                    break

                if finish_requested:
                    # finish_task 只是“请求结束”而不是直接结束。
                    # 真正能否成功结束，仍以 validate_outputs 为准。
                    self.progress.validation_start(task_id=ctx.task_id)
                    run_logger.event("FINISH_REQUESTED", task=ctx.task_id, step=budget.steps)
                    if ctx.task_id == "T2":
                        run_logger.event("FINALIZE_STARTED", task=ctx.task_id, mode="t2_finish_finalize")
                        await self._finalize_t2_from_raw(
                            ctx,
                            mode="t2_finish_finalize",
                            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
                            start_message="[Scout Agent] T2 收到 finish_task，先基于 papers_raw 执行确定性收尾...",
                            success_message="[Scout Agent] T2 确定性收尾成功，继续校验输出",
                        )
                        run_logger.event("FINALIZE_DONE", task=ctx.task_id, mode="t2_finish_finalize")
                    ok, err = self.agent.validate_outputs(ctx)
                    if ok:
                        self.progress.validation_result(task_id=ctx.task_id, ok=True)
                        run_logger.event("VALIDATION_PASS", task=ctx.task_id, step=budget.steps)
                        stop_reason = AgentResult.STOP_FINISHED
                        break
                    validation_fails += 1
                    repeated_validation_failures = self._record_validation_failure(
                        ctx,
                        str(err or "unknown validation error"),
                    )
                    self.progress.validation_result(
                        task_id=ctx.task_id,
                        ok=False,
                        error=str(err or "unknown validation error"),
                        failure_count=validation_fails,
                        retry_limit=validation_retry_limit,
                    )
                    run_logger.event(
                        "VALIDATION_FAILED",
                        task=ctx.task_id,
                        step=budget.steps,
                        failure=validation_fails,
                        limit=validation_retry_limit,
                        reason=err,
                    )
                    if validation_fails >= validation_retry_limit:
                        (
                            extended,
                            validation_retry_limit,
                            validation_extensions_used,
                        ) = await self._maybe_offer_validation_retry_extension(
                            ctx=ctx,
                            budget=budget,
                            last_error=str(err or "unknown validation error"),
                            failures=validation_fails,
                            retry_limit=validation_retry_limit,
                            used_extensions=validation_extensions_used,
                        )
                        if extended:
                            # A user explicitly approved another repair window.
                            # It is a new decision, so previous identical-error
                            # counts must not suppress the newly granted attempt.
                            ctx.extra.pop("last_validation_error", None)
                            ctx.extra.pop("same_validation_error_count", None)
                            run_logger.event(
                                "VALIDATION_RETRY",
                                task=ctx.task_id,
                                step=budget.steps,
                                failure=validation_fails,
                                new_limit=validation_retry_limit,
                            )
                            feedback = Message.user(
                                self._validation_repair_feedback(
                                    ctx=ctx,
                                    error=str(err or "unknown validation error"),
                                    resumed_after_extension=True,
                                ),
                                step=budget.steps,
                            )
                            messages.append(feedback)
                            trace.write_message(feedback)
                            continue
                        validation_circuit_limit = 3 if ctx.task_id == "T4" else 2
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        if repeated_validation_failures >= validation_circuit_limit:
                            error_msg = (
                                f"同一输出校验问题连续出现 {validation_circuit_limit} 次，已停止重复修复并保留当前产物。"
                                f"最后原因：{err}。请按该原因修复对应文件后再恢复运行。"
                            )
                            self.progress.emit(
                                "[Validation] 同一问题再次出现，已暂停并保留当前结果；"
                                "不会继续重复执行相同修复。",
                                important=True,
                            )
                        else:
                            error_msg = (
                                f"Validation failed {validation_fails} times. "
                                f"Paused for artifact repair/resume. Last reason: {err}"
                            )
                        break
                    validation_circuit_limit = 3 if ctx.task_id == "T4" else 2
                    if repeated_validation_failures >= validation_circuit_limit:
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = (
                            f"同一输出校验问题连续出现 {validation_circuit_limit} 次，已停止重复修复并保留当前产物。"
                            f"最后原因：{err}。请按该原因修复对应文件后再恢复运行。"
                        )
                        self.progress.emit(
                            "[Validation] 同一问题再次出现，已暂停并保留当前结果；"
                            "不会继续重复执行相同修复。",
                            important=True,
                        )
                        break
                    feedback = Message.user(
                        self._validation_repair_feedback(
                            ctx=ctx,
                            error=str(err or "unknown validation error"),
                        ),
                        step=budget.steps,
                    )
                    messages.append(feedback)
                    trace.write_message(feedback)

                if not budget.unlimited_budget and budget.steps >= budget.max_steps:
                    extended, budget_extensions_used = await self._maybe_offer_budget_extension(
                        ctx=ctx,
                        budget=budget,
                        exc=BudgetExceeded("steps", budget.max_steps, budget.steps),
                        used_extensions=budget_extensions_used,
                    )
                    if extended:
                        continue
                    stop_reason = AgentResult.STOP_MAX_STEPS
                    error_msg = "Reached maximum allowed steps; paused so you can resume or raise the step budget."
                    break

        except asyncio.CancelledError:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = "Cancelled"
            run_logger.event("PAUSED", task=ctx.task_id, reason=error_msg)
        except RecoverableRuntimePause as exc:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = str(exc)
            run_logger.event("PAUSED", task=ctx.task_id, reason=error_msg)
        except HookExecutionError as exc:
            stop_reason = AgentResult.STOP_ERROR
            error_msg = str(exc)
            run_logger.event("ERROR", task=ctx.task_id, kind="hook", message=error_msg)
        except Exception as exc:  # pragma: no cover - safety net
            stop_reason = AgentResult.STOP_ERROR
            error_msg = f"Unexpected: {exc!r}"
            self.log.exception("agent_runner_crashed")
            run_logger.event("ERROR", task=ctx.task_id, kind="runner_crash", message=error_msg)
        finally:
            stop_reason, error_msg = await self._maybe_finalize_t2_outputs(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
            )
            stop_reason, error_msg = self._maybe_finalize_t4_gate1_outputs(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
            )
            self._refresh_resume_artifacts(ctx)
            self._maybe_refresh_t3_resume_artifacts(ctx, stop_reason)
            await self._maybe_run_t3_abstract_sweep(ctx, stop_reason, eff)
            result = self._build_result(
                ctx=ctx,
                budget=budget,
                stop_reason=stop_reason,
                error_msg=error_msg,
                started=started,
                trace_file=trace_file,
                eff=eff,
                last_model_used=last_model_used,
                last_endpoint_used=last_endpoint_used,
            )
            for hook in self.agent.spec.post_hooks:
                try:
                    await self._run_post_hook(hook, ctx, result)
                except Exception:  # pragma: no cover - logging path
                    self.log.exception("post_hook_failed")
            trace.close(result)
            self.progress.agent_done(
                task_id=ctx.task_id,
                agent=self.agent.spec.name,
                ok=result.ok,
                stop_reason=result.stop_reason,
                summary=result.message,
                artifacts=[
                    safe_relative(path, ctx.workspace_dir) or str(path)
                    for path in list(result.outputs_produced.values())
                ],
                next_step=next_step_for_task(ctx.task_id, ok=result.ok) if not result.ok else None,
                trace_file=str(result.trace_file.relative_to(ctx.workspace_dir))
                if result.trace_file is not None
                else None,
                error=result.error,
                outputs_expected=ctx.outputs_expected,
                run_id=ctx.run_id,
            )
            run_logger.event(
                "TASK_END",
                task=ctx.task_id,
                ok=result.ok,
                stop_reason=result.stop_reason,
                error=result.error,
                steps=result.steps_used,
                tokens=result.tokens_in + result.tokens_out,
            )
            run_logger.event(
                "RUN_END",
                run_id=ctx.run_id,
                task=ctx.task_id,
                ok=result.ok,
                stop_reason=result.stop_reason,
            )
        return result

    async def _run_pre_hook(self, hook, ctx: ExecutionContext) -> None:
        """兼容同步/异步 pre-hook，并解释常见返回值。"""
        result = hook(ctx)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, tuple) and len(result) == 2:
            ok, message = result
            if not ok:
                text = str(message or f"Pre-hook failed: {hook.__name__}")
                if "WAITING_ENVIRONMENT" in text or "环境不可用" in text:
                    raise RecoverableRuntimePause(text)
                raise HookExecutionError(text)
            return

        if result is False:
            raise HookExecutionError(f"Pre-hook failed: {hook.__name__}")

    def _print_task_start_summary(self, ctx: ExecutionContext, eff: EffectiveConfig) -> None:
        """Print a human-readable one-line task summary before LLM work."""

        phase = ctx.mode or ctx.extra.get("phase") or "-"
        description = str(ctx.extra.get("task_description") or self._infer_task_description(ctx))
        expected = [
            str(path.relative_to(ctx.workspace_dir))
            for path in list(ctx.outputs_expected.values())[:5]
        ]
        if len(ctx.outputs_expected) > 5:
            expected.append(f"...(+{len(ctx.outputs_expected) - 5})")
        separator = self._centered_separator(f"{ctx.task_id} | {self.agent.spec.name}", width=80)
        self._emit(
            f"\n{separator}\n"
            f"[{self.agent.spec.name} Agent] 初始化完成 | "
            f"任务: {ctx.task_id} | 阶段: {phase} | "
            f"目标: {description} | 输出: {', '.join(expected) if expected else '未声明'} | "
            "LLM: 当前全局配置\n"
            f"{'=' * len(separator)}",
            verbose_only=True,
        )

    @staticmethod
    def _requires_sequential_tool_execution(ctx: ExecutionContext, tool_calls: list[ToolCall]) -> bool:
        """Return whether this response has order-sensitive durable writes."""

        # Multiple compiler invocations compete for LaTeX auxiliary files and
        # make a failed child process difficult to diagnose in the CLI.  Keep
        # them serial even outside T4; other independent tool calls can remain
        # concurrent.
        if sum(call.name == "latex_compile" for call in tool_calls) > 1:
            return True
        return ctx.task_id == "T4" and bool(tool_calls)

    @staticmethod
    def _t4_artifact_write_order_error(ctx: ExecutionContext, tc: ToolCall) -> str | None:
        """Reject a Gate1 artifact write that skips a durable predecessor."""

        if ctx.task_id != "T4" or tc.name not in {"write_file", "write_structured_file", "append_file"}:
            return None
        path = str(tc.arguments.get("path") or "").replace("\\", "/").lstrip("./")
        ordered_paths = [item[0] for item in T4_GATE1_ARTIFACTS]
        if path not in ordered_paths:
            return None
        index = ordered_paths.index(path)
        missing = [candidate for candidate in ordered_paths[:index] if not (ctx.workspace_dir / candidate).exists()]
        if not missing:
            return None
        return (
            f"T4 Gate1 artifact order violation: cannot write {path} before "
            f"{', '.join(missing)}. Write the missing predecessor(s) first."
        )

    def _record_skill_progress(
        self,
        ctx: ExecutionContext,
        *,
        step: int | None,
        step_limit: int | str | None,
        phase: str,
        detail: str,
        tool_name: str | None = None,
    ) -> None:
        """Persist observable runtime events for standalone Skill sessions."""

        session_id = str(ctx.extra.get("skill_session_id") or "").strip()
        if not session_id or not ctx.task_id.startswith("SKILL_"):
            return
        try:
            from ..skills.session import record_run_progress

            record_run_progress(
                workspace=ctx.workspace_dir,
                session_id=session_id,
                step=step,
                step_limit=step_limit,
                phase=phase,
                detail=detail,
                tool_name=tool_name,
            )
        except Exception as exc:  # pragma: no cover - progress must not break a run
            self.log.warning("skill_session_progress_write_failed", error=str(exc))

    def _refresh_t4_gate1_progress(
        self,
        ctx: ExecutionContext,
        *,
        active_path: str | Path | None,
        paused_reason: str | None = None,
        announce: bool = True,
    ) -> None:
        """Refresh the durable T4 checkpoint without conflating it with candidates.

        Gate1's ``n/6`` measures only required *persisted artifacts*. It is
        intentionally independent from a model-authored D1/D2/... candidate
        count. Store the exact checkpoint in ``ctx.extra`` for heartbeats and
        print it only when a user needs a new artifact-level milestone.
        """

        if ctx.task_id != "T4":
            return
        try:
            refreshed = refresh_t4_gate1_progress(
                ctx.workspace_dir,
                active_path=str(active_path) if active_path else None,
                paused_reason=paused_reason,
            )
            current = str(refreshed.get("current_label") or "正在更新 Gate1 进度")
            completed = int(refreshed.get("completed_count") or 0)
            total = int(refreshed.get("total_count") or 0)
            next_artifact = str(refreshed.get("next_artifact_label") or current)
            ctx.extra["t4_artifact_progress"] = {"completed": completed, "total": total}
            ctx.extra["t4_public_activity"] = current
            ctx.extra["t4_next_artifact"] = next_artifact
            if not announce:
                return
            signature = (completed, total, current, paused_reason or "")
            if ctx.extra.get("t4_last_announced_artifact_progress") == signature:
                return
            ctx.extra["t4_last_announced_artifact_progress"] = signature
            self.progress.emit(
                f"[T4 Gate1 artifacts] {completed}/{total} · {current}",
                important=True,
            )
        except Exception as exc:  # pragma: no cover - progress is observational
            self.log.warning("t4_progress_refresh_failed", error=str(exc))

    @staticmethod
    def _update_t4_public_activity_from_event(ctx: ExecutionContext, data: dict[str, object]) -> None:
        """Keep the last public candidate milestone available to LLM heartbeats.

        This accepts only the bounded event emitted by
        ``log_t4_ideation_progress``. It never records model rationale or
        unpersisted research content.
        """

        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        if not isinstance(event, dict):
            return
        phase = str(event.get("phase") or "T4").replace("_", " ")
        status = str(event.get("status") or "updated").replace("_", " ")
        subject_parts = [
            str(event.get("candidate_id") or "").strip(),
            str(event.get("candidate_title") or event.get("channel") or "").strip(),
        ]
        subject = " · ".join(part for part in subject_parts if part)
        label = " · ".join(part for part in (phase, subject, status) if part)
        if label:
            ctx.extra["t4_candidate_activity"] = label

    @staticmethod
    def _t4_heartbeat_context(ctx: ExecutionContext) -> dict[str, object]:
        """Return only public, durable T4 status for a provider heartbeat."""

        if ctx.task_id != "T4":
            return {}
        if ctx.extra.get("t4_evolution_active"):
            return {
                "activity": str(ctx.extra.get("t4_evolution_activity") or "T4 Evolution"),
                "next_artifact": str(ctx.extra.get("t4_evolution_next_artifact") or "T4 artifact"),
                "artifact_completed": None,
                "artifact_total": None,
            }
        progress = ctx.extra.get("t4_artifact_progress")
        completed = progress.get("completed") if isinstance(progress, dict) else None
        total = progress.get("total") if isinstance(progress, dict) else None
        activity = str(
            ctx.extra.get("t4_candidate_activity")
            or ctx.extra.get("t4_public_activity")
            or "正在准备下一项可执行动作"
        )
        next_artifact = str(ctx.extra.get("t4_next_artifact") or "等待下一项持久化产物")
        return {
            "activity": activity,
            "next_artifact": next_artifact,
            "artifact_completed": int(completed) if isinstance(completed, int) else None,
            "artifact_total": int(total) if isinstance(total, int) else None,
        }

    def _emit_t4_durable_candidate_recap(self, ctx: ExecutionContext, output_path: str | Path | None) -> None:
        """Backstop candidate-level CLI facts when the model omits progress events.

        The output is parsed only after a durable artifact was reported as
        written. It therefore describes persisted candidates/reviews/scores,
        never intermediate reasoning.
        """

        if ctx.task_id != "T4" or not output_path:
            return
        relative = safe_relative(output_path, ctx.workspace_dir) or str(output_path)
        if relative not in {
            "ideation/_pass1_forward_candidates.json",
            "ideation/_pass2_grounding_review.json",
            "ideation/_candidate_directions.json",
            "ideation/_gate1_candidate_cards.md",
        }:
            return
        path = ctx.workspace_dir / relative
        try:
            stat = path.stat()
        except OSError:
            return
        recap_key = f"{relative}:{stat.st_mtime_ns}:{stat.st_size}"
        if recap_key in self._t4_durable_recap_keys:
            return
        self._t4_durable_recap_keys.add(recap_key)
        try:
            if relative.endswith(".json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = None
        except (OSError, json.JSONDecodeError) as exc:
            self.log.warning("t4_durable_recap_parse_failed", path=relative, error=str(exc))
            return

        if relative == "ideation/_pass1_forward_candidates.json":
            candidates = payload.get("candidates") if isinstance(payload, dict) else []
            candidates = [item for item in candidates if isinstance(item, dict)]
            mainline = sum(1 for item in candidates if str(item.get("constraint_status") or "") == "mainline")
            supplements = sum(1 for item in candidates if str(item.get("constraint_status") or "") == "supplement")
            self.progress.emit(
                f"[T4 Pass1] 已保存候选池：{len(candidates)} 个方向（主线 {mainline}，补充 {supplements}）。",
                important=True,
            )
            for index, candidate in enumerate(candidates, start=1):
                candidate_id = str(candidate.get("id") or f"#{index}")
                title = _t4_recap_title(candidate)
                origin = str(candidate.get("idea_origin") or candidate.get("origin") or "未标注")
                lane = str(candidate.get("constraint_status") or "未标注")
                self.progress.emit(
                    f"[T4 Pass1] {index}/{len(candidates)} · {candidate_id} · {title} | {lane}/{origin} | 已写入候选记录。",
                    important=True,
                )
            return

        if relative == "ideation/_pass2_grounding_review.json":
            reviews = payload.get("reviews") if isinstance(payload, dict) else []
            reviews = [item for item in reviews if isinstance(item, dict)]
            self.progress.emit(f"[T4 Pass2] 已保存接地复核：{len(reviews)} 个候选。", important=True)
            for index, review in enumerate(reviews, start=1):
                candidate_id = str(review.get("idea_id") or review.get("id") or f"#{index}")
                recommendation = str(review.get("screening_recommendation") or "需复核")
                novelty = str(review.get("novelty_signal") or "未计算")
                self.progress.emit(
                    f"[T4 Pass2] {index}/{len(reviews)} · {candidate_id} | 建议={recommendation} | 新颖性信号={novelty}。",
                    important=True,
                )
            return

        if relative == "ideation/_candidate_directions.json":
            candidates = payload.get("candidates") if isinstance(payload, dict) else []
            candidates = [item for item in candidates if isinstance(item, dict)]
            self.progress.emit(f"[T4 评分] 已保存结构化候选：{len(candidates)} 个方向。", important=True)
            for index, candidate in enumerate(candidates, start=1):
                scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
                score_text = ", ".join(f"{key}={value}/5" for key, value in scores.items()) or "未评分"
                self.progress.emit(
                    f"[T4 评分] {index}/{len(candidates)} · {candidate.get('id') or '#'} · {_t4_recap_title(candidate)} | {score_text}",
                    important=True,
                )
            return

        if relative == "ideation/_gate1_candidate_cards.md":
            self.progress.emit(
                "[T4 Gate1] 完整候选卡片已写入：短标题、创新、候选 H1/H2/H3、可组合关系、评分依据和证据边界均在卡片中展示。",
                important=True,
            )


    async def _await_llm_with_progress(
        self,
        *,
        ctx: ExecutionContext,
        step: int,
        progress_step_limit: int | str | None,
        **kwargs,
    ):
        """Await one LLM call while emitting a bounded observable heartbeat."""

        self._record_skill_progress(
            ctx,
            step=step,
            step_limit=progress_step_limit,
            phase="awaiting_llm",
            detail="已提交模型请求，正在等待下一组可执行动作。",
        )
        if (
            ctx.task_id == "T4"
            and not self._t4_gate1_user_selection_exists(ctx)
            and not ctx.extra.get("t4_evolution_active")
        ):
            self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
        heartbeat = self._t4_heartbeat_context(ctx)
        self.progress.llm_request_started(task_id=ctx.task_id, step=step, **heartbeat)
        task = asyncio.create_task(self.llm.chat(**kwargs))
        started = time.monotonic()
        timeout = 12.0
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=timeout)
                if done:
                    return task.result()
                elapsed = int(time.monotonic() - started)
                if (
                    ctx.task_id == "T4"
                    and not self._t4_gate1_user_selection_exists(ctx)
                    and not ctx.extra.get("t4_evolution_active")
                ):
                    self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                heartbeat = self._t4_heartbeat_context(ctx)
                self.progress.llm_waiting(
                    task_id=ctx.task_id,
                    agent=self.agent.spec.name,
                    step=step,
                    elapsed_seconds=elapsed,
                    **heartbeat,
                )
                self._record_skill_progress(
                    ctx,
                    step=step,
                    step_limit=progress_step_limit,
                    phase="awaiting_llm",
                    detail=f"模型调用仍在等待，已持续 {elapsed}s。",
                )
                # T4 is intentionally visible but should not flood a long
                # terminal. First heartbeat remains at 12s; later pulses are
                # every 30s and only report public execution state.
                timeout = 30.0 if ctx.task_id == "T4" else 20.0
        except BaseException:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise

    @staticmethod
    def _centered_separator(title: str, *, width: int = 80, fill: str = "=") -> str:
        label = f" {title.strip()} "
        if len(label) >= width:
            return label
        left = (width - len(label)) // 2
        right = width - len(label) - left
        return f"{fill * left}{label}{fill * right}"

    def _emit(self, message: str, *, important: bool = False, verbose_only: bool = False) -> None:
        """Print according to CLI verbosity while RunLogger keeps full timeline."""

        if verbose_only and not self.runtime_settings.ui.verbose:
            return
        if self.runtime_settings.ui.quiet and not important:
            return
        print(format_cli_message(message), flush=True)

    @staticmethod
    def _infer_task_description(ctx: ExecutionContext) -> str:
        task_map = {
            "T1": "初始化项目配置和 workspace 状态",
            "T2": "检索、去重并验证候选论文",
            "T3": "精读论文并生成结构化 paper notes",
            "T3.5": "基于 notes 分阶段合成 literature synthesis",
            "T4": "生成候选研究假设、实验计划和风险分析",
            "T4.5": "做新颖性预审和 mechanism tuple 审计",
            "T5-REBOOST-GATE": "调用 LLM API 对 Pre-T5 材料做 context re-boost",
            "T5-HANDOFF": "编译外部实验协议、生成项目专属 skills 和 handoff prompt",
            "T5-SKILL-CUSTOMIZATION-GATE": "检查 external_executor skill specialization report",
            "T5-EXPR-MATERIAL-GATE": "等待用户放置外部实验材料并确认继续",
            "T5-EXECUTOR-GATE": "由用户选择 mock、Claude Code、Codex CLI 或人工外部执行器",
            "T5-EXTERNAL-WAIT": "等待外部执行器写回 result_pack 并在 resume 时校验",
            "T5-DRY-RUN": "跑通 mock 外部执行器文件协议，不执行真实实验",
            "T7-INGEST": "摄取外部 result pack 并规范化结果证据",
            "T7-AUDIT": "审计实验 provenance、hash、mock 标记、指标来源、method drift 和 framework figure",
            "T7-POST-NOVELTY": "基于实现/结果状态复核 novelty 和 claim 降级边界",
            "T7-CLAIMS": "生成 result-to-claim 和写作 evidence pack",
            "T5": "legacy pilot 实验兼容节点",
            "T6": "legacy pilot 后新颖性复核兼容节点",
            "T7": "legacy 内部完整实验兼容节点",
            "T7.5": "评估外部实验证据是否足够进入写作",
            "T8-RESOURCE": "构建写作资源索引、证据计划和图表计划",
            "T8-WRITE": "生成资源驱动的论文总大纲",
            "T8-SECTION-PLAN": "初始化 paper_state 和每章局部大纲",
            "T8-DRAFT": "拼装章节、审计 claim 并生成 paper.tex",
            "T8-SELF-CHECK": "作者自查整篇论文",
            "T8-REVIEW-1": "第一轮逐章节审稿",
            "T8-REVIEW-2": "第二轮逐章节审稿",
            "T8-REVISE-1": "按第一轮 patch list 修订论文",
            "T8-REVISE-2": "按第二轮 patch list 修订论文",
            "T8-PAPER-CLAIM-AUDIT": "进入 T9 前最终审计 paper claim 与 evidence pack 一致性",
            "T9": "构建投稿包、编译 PDF 并修复 TeX 问题",
        }
        if ctx.task_id.startswith("T8-SEC-"):
            section_id = ctx.extra.get("section_id") or ctx.extra.get("section") or "section"
            return f"只写单个论文 section: {section_id}"
        return task_map.get(ctx.task_id, "执行当前状态机节点声明的任务")

    async def _run_post_hook(self, hook, ctx: ExecutionContext, result: AgentResult) -> None:
        """兼容同步/异步 post-hook。"""
        outcome = hook(ctx, result)
        if inspect.isawaitable(outcome):
            await outcome

    async def _maybe_run_t1_startup_gate(
        self,
        ctx: ExecutionContext,
        tool_map: dict[str, Tool],
        messages: list[Message],
        trace: TraceWriter,
    ) -> None:
        """T1 必须先给用户一次补充材料/确认窗口，再让 PI 扫描 seeds。

        这是一个 runtime 级前置 gate，不依赖 LLM 是否记得调用 ask_human。
        首次运行会写 `_runtime/t1_startup_gate.json`；resume 或重跑时复用该
        artifact，把用户回答注入上下文，但不重复弹输入框。
        """

        if ctx.task_id != "T1" or self.agent.spec.name != "pi":
            return
        if (ctx.mode or "init") != "init":
            return

        gate_path = ctx.workspace_dir / "_runtime" / "t1_startup_gate.json"
        existing = self._load_t1_startup_gate(gate_path)
        if existing:
            answer = str(existing.get("answer") or "").strip()
            if answer:
                ctx.extra["t1_startup_gate_answer"] = answer
                ctx.extra["t1_startup_gate_path"] = str(gate_path)
                note = Message.user(
                    "【T1 启动补充 gate 已完成】\n"
                    "下面是用户在扫描 user_seeds/ 之前补充或确认的信息。"
                    "请先结合这段信息，再调用 list_files/read_file 扫描 user_seeds/。\n\n"
                    f"{answer}",
                    step=0,
                )
                messages.append(note)
                trace.write_message(note)
                return

        if "ask_human" not in tool_map:
            raise RecoverableRuntimePause(
                "T1 启动补充 gate 需要 ask_human 工具，但当前 Agent 工具策略没有开放 ask_human。"
            )

        question = (
            "【T1 启动补充 gate】\n"
            "在 ResearchOS 扫描 user_seeds/ 之前，请先补充或确认初始化信息。\n\n"
            "为什么需要回答：T1 会把你的研究边界、已有论文/想法/约束和外部资源写成 "
            "project.yaml、user_seeds/* 与 literature/bridge_domain_plan.json；"
            "这些 artifact 会直接影响后续 T2 检索、T3 阅读、T4 idea 生成和实验计划。"
            "先确认一次可以避免系统用过期或缺失材料启动。\n\n"
            "你可以回答：\n"
            "1. 已经放入 user_seeds/ 的材料有哪些，是否可以直接扫描；\n"
            "2. 还想补充的种子论文、arXiv/DOI、初步想法、硬约束、目标 venue、预算/GPU；\n"
            "3. 外部资源，如数据集、benchmark、代码仓库、预训练模型；\n"
            "4. 如果没有补充，直接回答“继续，扫描现有 user_seeds”。"
        )
        suggestions = [
            "继续，扫描现有 user_seeds",
            "我已补充 seed PDFs/seed_ideas/seed_constraints，请先读取这些文件",
            "我要补充研究问题、目标 venue、预算/GPU 或外部资源",
        ]

        self.progress.emit(
            "[PI Agent] T1 启动补充 gate：先确认种子材料和研究边界，再扫描 user_seeds/",
            important=True,
        )
        result = await tool_map["ask_human"].execute(question=question, suggestions=suggestions)
        if not result.ok:
            reason = result.content or result.error or "T1 启动补充 gate 未获得用户输入"
            raise RecoverableRuntimePause(str(reason))

        data = result.data if isinstance(result.data, dict) else {}
        answer = str(data.get("answer") or "").strip()
        if not answer:
            raise RecoverableRuntimePause("T1 启动补充 gate 收到空回答，已暂停等待明确输入。")

        gate_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "version": "1.0",
            "semantics": "t1_startup_material_supplement_gate",
            "interaction_id": data.get("interaction_id") or f"t1_startup_{uuid4().hex[:12]}",
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "suggestions": suggestions,
            "answer": answer,
            "next_action": "scan_user_seeds_after_gate",
        }
        gate_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ctx.extra["t1_startup_gate_answer"] = answer
        ctx.extra["t1_startup_gate_path"] = str(gate_path)

        note = Message.user(
            "【T1 启动补充 gate 用户回答】\n"
            "必须先结合这段回答，再扫描 user_seeds/ 并继续后续分轮访谈：\n\n"
            f"{answer}",
            step=0,
        )
        messages.append(note)
        trace.write_message(note)

    @staticmethod
    def _load_t1_startup_gate(path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("semantics") != "t1_startup_material_supplement_gate":
            return None
        return data

    async def _maybe_finalize_t2_outputs(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
    ) -> tuple[str, str | None]:
        """T2 退出后的窄口恢复。

        不能把普通冷启动中的 LLM/step 失败当成“raw 已足够，可以完成 T2”。
        Scout 是否已经完成覆盖判断，必须由 finish_task 或真实 resume/retry 语义
        触发；否则第一轮多源搜索返回大量 raw 时会伪装成 T2 已成功。
        """

        if ctx.task_id != "T2":
            return stop_reason, error_msg
        if stop_reason in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_HUMAN_REJECT}:
            return stop_reason, error_msg
        if stop_reason == AgentResult.STOP_FINISHED:
            return stop_reason, error_msg
        if not self._allow_t2_exit_recovery(ctx):
            return stop_reason, error_msg

        needs_recovery = any(
            not path.exists()
            for name, path in ctx.outputs_expected.items()
            if name != "papers_raw"
        )
        if not needs_recovery:
            return stop_reason, error_msg

        finalized = await self._finalize_t2_from_raw(
            ctx,
            mode="t2_recovery",
            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
            start_message="[Scout Agent] T2 resume/recovery 检测到未完成输出，尝试基于 papers_raw 补齐...",
            success_message="[Scout Agent] T2 resume/recovery 补齐成功，已恢复完整 T2 产物",
        )
        if finalized:
            return AgentResult.STOP_FINISHED, None

        return stop_reason, error_msg

    def _refresh_resume_artifacts(self, ctx: ExecutionContext) -> None:
        """在任意退出路径刷新通用恢复快照，避免失败/暂停后仍看到旧进度。"""

        try:
            recovery = prepare_generic_resume_artifacts(
                ctx.workspace_dir,
                task_id=ctx.task_id,
                outputs_expected=ctx.outputs_expected,
            )
            ctx.extra.update(
                {
                    "resume_state_path": recovery.get("resume_state_path"),
                    "resume_existing_outputs": recovery.get("resume_existing_outputs"),
                    "resume_missing_outputs": recovery.get("resume_missing_outputs"),
                    "resume_output_summaries": recovery.get("resume_output_summaries"),
                    "resume_existing_artifacts": recovery.get("resume_existing_artifacts"),
                }
            )
        except Exception:  # pragma: no cover - refresh failure should not hide the real result
            self.log.exception("resume_artifact_refresh_failed")

    def _maybe_refresh_t3_resume_artifacts(self, ctx: ExecutionContext, stop_reason: str) -> None:
        """T3 退出时刷新 pending queue 快照，避免暂停/失败后仍显示旧进度。"""

        if ctx.task_id != "T3":
            return
        try:
            recovery = prepare_t3_resume_artifacts(
                ctx.workspace_dir,
                refresh_reason=f"runner_exit:{stop_reason}",
            )
            ctx.extra.update(
                {
                    "resume_queue_path": recovery.get("resume_queue_path"),
                    "resume_queue_count": recovery.get("resume_queue_count"),
                    "existing_note_count": recovery.get("existing_note_count"),
                }
            )
        except Exception:  # pragma: no cover - refresh failure should not fail a completed T3
            self.log.exception("t3_resume_artifact_refresh_failed")

    async def _maybe_run_t3_abstract_sweep(
        self,
        ctx: ExecutionContext,
        stop_reason: str,
        eff: EffectiveConfig,
    ) -> None:
        """T3 退出后自动运行/恢复 abstract sweep 补读。

        finished 路径使用 Reader LLM 生成轻量笔记；max_steps/budget/interrupt
        路径只用确定性 fallback，避免中断后又发起长 LLM 补读，但仍保证
        shallow/backlog 论文不会因为任务被取消而永远没有 abstract note。
        """

        if ctx.task_id != "T3":
            return
        if ctx.extra.get("skip_t3_abstract_sweep"):
            return
        allowed_stop_reasons = {
            AgentResult.STOP_FINISHED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
            AgentResult.STOP_INTERRUPTED,
        }
        if stop_reason not in allowed_stop_reasons:
            return

        try:
            mode_params = get_effective_reader_read_params(ctx.workspace_dir)
            sweep_config = mode_params.get("abstract_sweep", {})
            if not sweep_config.get("enabled", False):
                return

            if stop_reason == AgentResult.STOP_FINISHED:
                self.progress.emit(
                    "[Reader Agent] T3 精读阶段已完成，开始 abstract sweep 补齐摘要级覆盖",
                    important=True,
                )
            else:
                self.progress.emit(
                    f"[Reader Agent] T3 以 {stop_reason} 退出，使用 deterministic abstract sweep 刷新浅层笔记覆盖...",
                    important=True,
                )

            if stop_reason != AgentResult.STOP_FINISHED:
                result = run_abstract_sweep(ctx.workspace_dir, sweep_config)
                ctx.extra["abstract_sweep"] = result
                if result.get("notes_generated", 0) > 0:
                    self.progress.emit(
                        f"[Reader Agent] Abstract sweep fallback 完成：筛选 {result['candidates_found']} 篇候选，"
                        f"生成 {result['notes_generated']} 篇 abstract note",
                        important=True,
                    )
                return

            abstract_reader_binding = self.llm.resolve(
                profile=eff.llm_profile,
                tier=eff.llm_tier,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
            )[0][0]

            def _abstract_reader_messages(prompt: str) -> list[dict[str, str]]:
                return [
                    {
                        "role": "system",
                        "content": (
                            "You are ResearchOS Reader. Produce cautious abstract-only "
                            "paper notes in the exact requested Markdown or JSON structure."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]

            async def _reader_llm(_paper: dict[str, object], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=_abstract_reader_messages(prompt),
                    tools=None,
                    temperature=0.2,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=int(self.global_timeout.get("llm_call") or 120),
                    max_retries_per_model=max(1, int(self.retry_policy.get("llm_retries") or 2)),
                    retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            async def _abstract_batch_llm(_papers: list[dict[str, object]], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=_abstract_reader_messages(prompt),
                    tools=None,
                    temperature=0.15,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=int(self.global_timeout.get("llm_call") or 120),
                    max_retries_per_model=max(1, int(self.retry_policy.get("llm_retries") or 2)),
                    retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            def _count_abstract_batch_prompt(prompt: str) -> int:
                return self.llm.count_tokens(_abstract_reader_messages(prompt), abstract_reader_binding)

            async def _metadata_triage_llm(_papers: list[dict[str, object]], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are ResearchOS Reader. Triage metadata-only literature candidates as a batch. "
                                "Never claim to have read abstracts or full text, and never produce evidence claims."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    tools=None,
                    temperature=0.1,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=int(self.global_timeout.get("llm_call") or 120),
                    max_retries_per_model=max(1, int(self.retry_policy.get("llm_retries") or 2)),
                    retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            result = await run_abstract_sweep_with_reader(
                ctx.workspace_dir,
                sweep_config,
                abstract_reader=_reader_llm,
                abstract_batch_reader=_abstract_batch_llm,
                metadata_triage_reader=_metadata_triage_llm,
                provider_context_window=abstract_reader_binding.max_context,
                prompt_token_counter=_count_abstract_batch_prompt,
            )
            ctx.extra["abstract_sweep"] = result

            if result.get("notes_generated", 0) > 0 or result.get("metadata_triage_count", 0) > 0:
                self.progress.emit(
                    f"[Reader Agent] Abstract sweep 完成：筛选 {result['candidates_found']} 篇候选，"
                    f"生成 {result['notes_generated']} 篇 abstract note "
                    f"（LLM {result.get('llm_notes_generated', 0)}，fallback {result.get('fallback_notes_generated', 0)}），"
                    f"provider-context 批次 {result.get('llm_batch_calls', 0)}，"
                    f"metadata-only 批量 triage {result.get('metadata_triage_count', 0)} 篇",
                    important=True,
                )
            else:
                self.progress.emit("[Reader Agent] Abstract sweep 无候选论文", important=True)
        except Exception:  # pragma: no cover - sweep failure should not fail a completed T3
            self.log.exception("t3_abstract_sweep_failed")

    async def _maybe_finalize_t2_before_llm(self, ctx: ExecutionContext) -> bool:
        """T2 续跑时，只有已足够完整的产物或显式恢复场景才跳过 LLM。

        冷启动后第一轮检索可能已经因为多源工具返回大量 raw，但这不等于
        Scout 的检索覆盖规划已经完成。因此这里不能只看 raw_count 自动结束。
        """

        if ctx.task_id != "T2":
            return False

        if bool(ctx.extra.get("t2_user_requested_expansion")):
            # The user explicitly chose "expand / adjust query" at the T2
            # coverage gate. Existing outputs are valuable evidence to retain,
            # but they are not a substitute for the newly requested Scout
            # round.  If that round already changed the persisted corpus before
            # an interruption, resume must finalize those results rather than
            # launch another full search loop.
            if self._t2_expansion_has_persisted_progress(ctx):
                return await self._finalize_t2_from_raw(
                    ctx,
                    mode="t2_expansion_resume_prefinalize",
                    min_raw_count=self._t2_finish_finalize_min_raw(ctx),
                    start_message="[T2] 检测到已保存的补检结果，正在整理本轮新增文献...",
                    success_message="[T2] 补检结果已整理完成，跳过重复检索。",
                )
            self.progress.emit(
                "[T2] 已按你的选择进入补检：保留现有论文池，并补充尚未覆盖的检索角度。",
                important=True,
            )
            return False

        if ctx.outputs_expected and all(path.exists() for path in ctx.outputs_expected.values()):
            ok, _err = self.agent.validate_outputs(ctx)
            manifest_ok, manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
            if ok and manifest_ok:
                self._record_runtime_completion(
                    ctx,
                    "t2_existing_outputs_prefinalize",
                    {"raw_count": self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl")},
                )
                self.progress.emit(
                    "[Scout Agent] T2 检测到已有完整产物且校验通过，跳过重复 LLM 续跑",
                    important=True,
                )
                return True
            if ok and not manifest_ok:
                self.log.info("t2_existing_outputs_prefinalize_skipped", reason=manifest_err)

        if not self._is_resume_run(ctx):
            return False

        manifest_ok, manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
        if not manifest_ok and (ctx.workspace_dir / "literature" / "papers_raw.jsonl").exists():
            if not self._raw_t2_cache_newer_than_inputs(ctx):
                self.log.info("t2_resume_prefinalize_skipped", reason=manifest_err)
                return False

        return await self._finalize_t2_from_raw(
            ctx,
            mode="t2_resume_prefinalize",
            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
            start_message="[Scout Agent] T2 resume 检测到已有 papers_raw，尝试确定性补齐缺失产物...",
            success_message="[Scout Agent] T2 resume 确定性补齐成功，跳过 LLM 续跑",
        )

    def _t2_expansion_has_persisted_progress(self, ctx: ExecutionContext) -> bool:
        """Detect an interrupted T2 supplement without trusting volatile state.

        ``coverage_decision.json`` records the corpus fingerprints presented to
        the user before selecting "expand".  Any changed tracked artifact means
        the requested round has already produced durable work and can safely be
        finalized from ``papers_raw``.  Older decisions did not fingerprint the
        raw file, so search-log and downstream-artifact changes remain valid
        compatibility signals.
        """

        decision_path = ctx.workspace_dir / "literature" / "coverage_decision.json"
        if not decision_path.is_file():
            return False
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(decision, dict) or decision.get("selected_option") != "rerun_t2_expand":
            return False
        fingerprints = decision.get("input_fingerprints")
        if not isinstance(fingerprints, dict):
            return False

        tracked_labels = (
            "papers_raw",
            "search_log",
            "missing_areas",
            "papers_dedup",
            "papers_verified",
            "deep_read_queue",
        )
        for label in tracked_labels:
            expected = fingerprints.get(label)
            if not isinstance(expected, dict):
                continue
            rel_path = str(expected.get("path") or "").strip()
            expected_hash = str(expected.get("sha256") or "").strip()
            if not rel_path or not expected_hash:
                continue
            path = ctx.workspace_dir / rel_path
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                continue
            if digest.hexdigest() != expected_hash:
                return True
        return False

    def _raw_t2_cache_newer_than_inputs(self, ctx: ExecutionContext) -> bool:
        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists() or raw_path.stat().st_size <= 0:
            return False
        return self._outputs_newer_than_inputs(
            ctx,
            outputs=[raw_path],
            inputs=[
                ctx.workspace_dir / "project.yaml",
                ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
                ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl",
                ctx.workspace_dir / "user_seeds" / "seed_outline_profile.json",
                ctx.workspace_dir / "user_seeds" / "seed_external_resources.jsonl",
            ],
            event="t2_resume_prefinalize_skipped",
            reason="papers_raw_older_than_t2_inputs",
        )

    async def _maybe_finalize_t3_before_llm(self, ctx: ExecutionContext) -> bool:
        """T3 续跑时，已有 deep-read 产物通过校验则直接完成。

        T3 的成功条件是“足够且结构合格的深读证据”，不是必须把
        `deep_read_queue_pending.jsonl` 中所有低优先级或 overflow 条目全部读完。
        若当前 artifact 已满足 Reader validator，继续让 LLM 补 alias/stub 会浪费预算。
        """

        if ctx.task_id != "T3":
            return False

        expected_paths = [
            ctx.workspace_dir / "literature" / "deep_read_notes",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
            ctx.workspace_dir / "literature" / "related_work.bib",
        ]
        if any(not path.exists() for path in expected_paths):
            return False

        manifest_path = ctx.workspace_dir / "literature" / "notes_manifest.json"
        if not manifest_path.exists() or manifest_path.stat().st_size <= 0:
            self.log.info("t3_resume_prefinalize_skipped", reason="notes_manifest_missing")
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log.info("t3_resume_prefinalize_skipped", reason=f"notes_manifest_invalid:{exc}")
            return False
        if not isinstance(manifest, dict):
            self.log.info("t3_resume_prefinalize_skipped", reason="notes_manifest_not_object")
            return False
        ok, err = validate_t3_input_fingerprints(ctx.workspace_dir, manifest)
        if not ok:
            self.log.info("t3_resume_prefinalize_skipped", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t3_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Reader Agent] T3 检测到已有 deep-read 产物且校验通过，跳过重复 deep-read LLM",
            important=True,
        )
        # Do not suppress abstract sweep here. Resume may have valid deep-read
        # notes while shallow/metadata notes are missing or stale; the post-run
        # sweep is the cheap deterministic/Reader path that repairs that gap.
        self._record_runtime_completion(
            ctx,
            "t3_resume_prefinalize",
            {
                "outputs": [
                    "literature/deep_read_notes",
                    "literature/comparison_table.csv",
                    "literature/related_work.bib",
                ],
            },
            action_type="t3_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t36_section_before_llm(self, ctx: ExecutionContext) -> bool:
        """Advance a validated survey section after a pause without rewriting it.

        Section writing is the only T3.6 phase where an interrupted provider
        run can leave a complete, valid single-file artifact while the global
        survey remains unfinished.  Replaying the model call is harmful: it
        needlessly changes a reviewed section and used to combine with broad
        write privileges to disturb later sections.  A resumed section task
        therefore validates its declared output/state pair first and advances
        directly when both remain current.
        """

        if not ctx.task_id.startswith("T3.6-SEC-") or not self._is_resume_run(ctx):
            return False
        section_path = ctx.outputs_expected.get("section")
        if section_path is None or not section_path.exists() or section_path.stat().st_size <= 0:
            return False
        state_path = ctx.workspace_dir / "drafts" / "survey" / "survey_state.json"
        if not state_path.exists() or state_path.stat().st_size <= 0:
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t36_section_resume_prefinalize_skipped", task=ctx.task_id, reason=err)
            return False
        relative_section = safe_relative(section_path, ctx.workspace_dir) or str(section_path)
        self.progress.emit(
            f"[Survey Writer Agent] {ctx.task_id} 的章节、状态与证据校验已通过；恢复时不重写 {relative_section}。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t36_section_resume_prefinalize",
            {"outputs": [relative_section, "drafts/survey/survey_state.json"]},
            action_type="t36_section_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t36_visuals_before_llm(self, ctx: ExecutionContext) -> bool:
        """Reuse a valid deterministic taxonomy visual after a recoverable pause.

        T3.6-VISUALS has no scholarly generation step: the restricted tool
        renders the taxonomy map and the validator checks its factual policy.
        Once that manifest/PDF pair is valid, an LLM retry would add cost and
        could not improve the artifact.  Resume should therefore continue from
        the durable result without asking a provider to repeat the tool call.
        """

        if ctx.task_id != "T3.6-VISUALS" or not self._is_resume_run(ctx):
            return False
        manifest_path = ctx.workspace_dir / "drafts" / "survey" / "figures" / "survey_visual_manifest.json"
        pdf_path = ctx.workspace_dir / "drafts" / "survey" / "figures" / "fig_taxonomy_overview.pdf"
        if not manifest_path.exists() or manifest_path.stat().st_size <= 0:
            return False
        if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t36_visuals_resume_prefinalize_skipped", reason=err)
            return False
        self.progress.emit(
            "[Survey Writer Agent] T3.6-VISUALS 检测到已通过 taxonomy-only 契约的 PDF/manifest，跳过重复 LLM 与图生成",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t36_visuals_resume_prefinalize",
            {
                "outputs": [
                    "drafts/survey/figures/fig_taxonomy_overview.pdf",
                    "drafts/survey/figures/survey_visual_manifest.json",
                ],
            },
            action_type="t36_visuals_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t36_compile_before_llm(self, ctx: ExecutionContext) -> bool:
        """T3.6-COMPILE resume: reuse an already valid survey PDF/report.

        Compile validation is deterministic and already checks survey.tex,
        survey.pdf, survey.log, survey_compile_report.json, current audit, and
        unresolved LaTeX warnings. If those artifacts validate after manual
        repair, resume should advance without asking an LLM to recompile the
        same PDF.
        """

        if ctx.task_id != "T3.6-COMPILE":
            return False
        if not self._is_resume_run(ctx):
            return False
        expected_paths = [
            ctx.workspace_dir / "drafts" / "survey" / "survey.pdf",
            ctx.workspace_dir / "drafts" / "survey" / "survey.log",
            ctx.workspace_dir / "drafts" / "survey" / "survey_compile_report.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in expected_paths):
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t36_compile_resume_prefinalize_skipped", reason=err)
            return False
        self.progress.emit(
            "[Survey Writer Agent] T3.6-COMPILE 检测到已有 PDF、log 和 compile report 且校验通过，跳过重复编译",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t36_compile_resume_prefinalize",
            {
                "outputs": [
                    "drafts/survey/survey.pdf",
                    "drafts/survey/survey.log",
                    "drafts/survey/survey_compile_report.json",
                ],
            },
            action_type="t36_compile_resume_prefinalize",
        )
        return True

    def _maybe_prepare_t4_context_pack_before_prompt(self, ctx: ExecutionContext) -> bool:
        """Prepare compact T4 inputs before rendering the ideation prompt."""

        if ctx.task_id != "T4":
            return False
        if has_current_t4_prerun_confirmation(ctx.workspace_dir):
            # The evolutionary controller builds its own Evidence Index and
            # route-scoped bundles. Retaining the legacy compact pack here
            # would duplicate work and produce an unrelated six-artifact
            # progress counter before the new eight-phase run begins.
            return False
        if not self._t4_gate1_user_selection_exists(ctx):
            gate1_ready, _gate1_err = validate_t4_gate1_ready(ctx.workspace_dir)
            if gate1_ready:
                backfill = ensure_t4_evidence_pool(ctx.workspace_dir)
                if backfill.get("changed"):
                    self.progress.emit(
                        "[T4 Evidence Pool] 已补齐历史 workspace 的可回查笔记索引："
                        f"首轮 {backfill.get('selected_count', 0)} 张，延后可回查 {backfill.get('deferred_count', 0)} 张。",
                        important=True,
                    )
                self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                return False
        try:
            pack = prepare_t4_context_pack(ctx.workspace_dir)
        except Exception as exc:
            self.log.warning("t4_context_pack_prepare_failed", error=str(exc))
            self.progress.emit(
                "[Ideation Agent] T4 compact context pack 准备失败；将回退到原始材料读取",
                important=True,
            )
            return False

        summary = pack.get("note_card_summary") if isinstance(pack.get("note_card_summary"), dict) else {}
        outputs = pack.get("outputs") if isinstance(pack.get("outputs"), list) else []
        selected = summary.get("selected_card_count", 0)
        usable = summary.get("usable_card_count", 0)
        raw = summary.get("raw_card_count", 0)
        self.progress.emit(
            "[Ideation Agent] T4 已准备 compact context pack\n"
            f"- 笔记卡: 已选 {selected} 张；可用 {usable} 张；原始 {raw} 张\n"
            f"- 写入: {'；'.join(str(item) for item in outputs[:3])}\n"
            "- 用途: 让 T4 先基于压缩证据生成 Gate1 候选，减少无目标分页读取",
            important=True,
        )
        self._refresh_t4_gate1_progress(ctx, active_path=None)
        self.progress.progress_file_update(
            label="Ideation/T4 进度",
            path="ideation/t4_progress.md",
            bullets=summarize_progress_markdown(ctx.workspace_dir / "ideation" / "t4_progress.md", max_items=4),
        )
        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": "t4_context_pack_prepared",
                    "mode": "t4_context_pack_prepared",
                    "outputs": outputs,
                    "selected_note_cards": selected,
                    "usable_note_cards": usable,
                }
            )
        ctx.extra["t4_context_pack_prepared"] = True
        return True

    async def _maybe_run_t4_evolution_before_llm(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
    ) -> bool:
        """Run the confirmed evolutionary T4 path before the legacy tool loop.

        This is intentionally an internal T4 facade, not a new external state
        machine node. A successful run writes the retained Gate1 artifacts and
        returns the established ``t4_gate1_ready`` completion mode, preserving
        the public ``T4 -> T4-GATE1 -> T4 -> T4.5`` transition.
        """

        if ctx.task_id != "T4" or self._t4_gate1_user_selection_exists(ctx):
            return False
        if not has_current_t4_prerun_confirmation(ctx.workspace_dir):
            return False
        store = T4ArtifactStore(ctx.workspace_dir)
        try:
            run_config = store.read_run_config()
        except ValueError:
            return False
        async def role_call(system_contract: str, user_prompt: str) -> str:
            return await self._call_t4_evolution_role(
                ctx=ctx,
                eff=eff,
                budget=budget,
                system_contract=system_contract,
                user_prompt=user_prompt,
            )

        async def progress_callback(phase: EvolutionPhase, status: str, payload: dict[str, object]) -> None:
            self._record_t4_evolution_activity(ctx, phase=phase, status=status)
            self._render_t4_evolution_phase(phase=phase, status=status, payload=payload)

        invoker = LLMJsonRoleInvoker(
            config=T4RoleCallConfig(
                tier=eff.llm_tier,
                profile=eff.llm_profile,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
                timeout=int(self.global_timeout.get("llm_call") or 120),
                max_retries_per_model=int(self.retry_policy.get("llm_retries") or 2),
                retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
            ),
            call=role_call,
        )
        generator = LLMIdeaGenerator(invoker)
        scorer = LLMIdeaScorer(invoker)
        evolver = LLMIdeaEvolver(invoker)
        controller = IdeaEvolutionController(
            workspace_dir=ctx.workspace_dir,
            settings=load_t4_evolution_settings(),
            generator=generator,
            scorer=scorer,
            evolver=evolver,
            progress_callback=progress_callback,
        )
        ctx.extra["t4_evolution_active"] = True
        try:
            operation = ctx.extra.get("t4_operation_request")
            operation_action = str(operation.get("action") or "") if isinstance(operation, dict) else ""
            directive = operation.get("directive") if isinstance(operation, dict) and isinstance(operation.get("directive"), dict) else {}
            if operation_action == "continue_evolution":
                result = await controller.continue_from_active_population(run_config)
            elif operation_action == "focus_candidate":
                targets = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
                if len(targets) != 1:
                    raise ValueError("Focus Evolution requires exactly one selected Candidate")
                result = await controller.focus_active_candidate(run_config, candidate_id=str(targets[0]))
            elif operation_action == "merge_candidates":
                targets = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
                if len(targets) != 2:
                    raise ValueError("Create a Crossover requires exactly two selected Candidates")
                try:
                    result = await controller.create_crossover_from_active_candidates(
                        run_config,
                        parent_ids=[str(targets[0]), str(targets[1])],
                    )
                except ValueError as exc:
                    if "Compatibility Check" not in str(exc):
                        raise
                    self._write_t4_operation_outcome(
                        ctx,
                        operation=operation,
                        status="compatibility_rejected",
                        summary="The requested Crossover was not generated because the independent Compatibility Check did not approve one coherent Gene Donor Map.",
                        details={"plan_artifact": f"ideation/evolution/plans/round_{T4ArtifactStore(ctx.workspace_dir).read_state().generation + 1}.json"},
                    )
                    ready, error = validate_t4_gate1_ready(ctx.workspace_dir)
                    if not ready:
                        raise RecoverableRuntimePause(error or "T4 Gate1 artifacts are unavailable after the Compatibility Check")
                    self._record_runtime_completion(
                        ctx,
                        "t4_gate1_ready",
                        {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                        action_type="t4_crossover_compatibility_rejected",
                    )
                    return True
            elif operation_action == "compose_from_components":
                await self._run_t4_human_composition_check(
                    ctx=ctx,
                    scorer=scorer,
                    operation=operation,
                )
                self._record_runtime_completion(
                    ctx,
                    "t4_gate1_ready",
                    {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                    action_type="t4_human_composition_checked",
                )
                return True
            elif operation_action == "execute_human_composition":
                result = await self._run_t4_human_composition_generation(
                    ctx=ctx,
                    run_config=run_config,
                    controller=controller,
                    evolver=evolver,
                    operation=operation,
                )
            elif operation_action:
                raise RecoverableRuntimePause(
                    "This T4 directive was recorded safely, but its model-backed operation is not available in this runtime build yet. "
                    "The current Population was not changed; return to Gate1 to choose another action."
                )
            else:
                result = await controller.run(run_config)
            projection = project_gate1_population(
                ctx.workspace_dir,
                population=result.population,
                dossiers=result.active_dossiers,
                scores=result.active_scores,
                route_results=result.route_results,
            )
        except RecoverableRuntimePause:
            raise
        except LLMProviderError:
            raise
        except Exception as exc:
            raise RecoverableRuntimePause(
                "T4 Evolution 结果没有通过结构与证据边界校验，当前 Population 已保留；"
                f"resume 会从最后一个完整 artifact 继续。原因：{str(exc)[:500]}"
            ) from exc
        finally:
            ctx.extra["t4_evolution_active"] = False

        ready, error = validate_t4_gate1_ready(ctx.workspace_dir)
        if not ready:
            raise RecoverableRuntimePause(
                "T4 Evolution 已完成，但 Gate1 兼容投影尚未通过校验；"
                f"已保留 P0/P1 和评分结果，resume 可继续。原因：{error}"
            )
        self.progress.emit(
            "T4 已完成一轮 Idea Evolution。P1、评分、谱系和完整 Archive 已保存；接下来请选择一个完整 Candidate，或保留多个并行推进。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/populations/P0.json",
                    f"ideation/populations/{result.population.population_id}.json",
                    "ideation/portfolio.json",
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                ],
                "candidate_count": projection["candidate_count"],
            },
            action_type="t4_evolution_controller",
        )
        return True

    @staticmethod
    def _write_t4_operation_outcome(
        ctx: ExecutionContext,
        *,
        operation: object,
        status: str,
        summary: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Persist a compact, user-safe outcome for a requested Gate1 operation."""

        payload = operation if isinstance(operation, dict) else {}
        T4ArtifactStore(ctx.workspace_dir).write_json(
            "ideation/evolution/latest_operation_result.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_native_operation_result",
                "directive_path": str(payload.get("directive_path") or ""),
                "action": str(payload.get("action") or ""),
                "status": status,
                "summary": summary,
                "details": details or {},
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _run_t4_human_composition_check(
        self,
        *,
        ctx: ExecutionContext,
        scorer: LLMIdeaScorer,
        operation: object,
    ) -> None:
        """Write a compatibility-gated composition plan without creating a Child."""

        request = operation if isinstance(operation, dict) else {}
        directive = request.get("directive") if isinstance(request.get("directive"), dict) else {}
        component_refs = [str(item) for item in directive.get("component_refs", []) if str(item).strip()] if isinstance(directive.get("component_refs"), list) else []
        source_ids = [str(item) for item in directive.get("target_candidate_ids", []) if str(item).strip()] if isinstance(directive.get("target_candidate_ids"), list) else []
        if len(set(source_ids)) < 2 or len(component_refs) < 2:
            raise ValueError("Human composition requires selected components from at least two Candidates")
        population, dossiers = current_population_context(ctx.workspace_dir)
        if not set(source_ids).issubset(dossiers):
            raise ValueError("Human composition references a Candidate outside the active Population")
        directive_id = str(directive.get("directive_id") or "")
        if not directive_id:
            raise ValueError("Human composition request is missing a stable Directive ID")
        composition_id = f"HC-{directive_id.removeprefix('DIR-')}"
        compatibility = await scorer.review_human_composition(
            composition_id=composition_id,
            candidates=[dossiers[candidate_id] for candidate_id in source_ids],
            component_refs=component_refs,
            preserve_genes=[str(item) for item in directive.get("preserve_genes", []) if str(item).strip()] if isinstance(directive.get("preserve_genes"), list) else [],
            donor_genes={str(key): str(value) for key, value in (directive.get("donor_genes") or {}).items()} if isinstance(directive.get("donor_genes"), dict) else {},
            constraints=[str(item) for item in directive.get("constraints", []) if str(item).strip()] if isinstance(directive.get("constraints"), list) else [],
        )
        if set(compatibility.source_candidate_ids) != set(source_ids):
            raise ValueError("Composition reviewer changed the source Candidate set")
        store = T4ArtifactStore(ctx.workspace_dir)
        root = f"ideation/human_compositions/{composition_id}"
        report_path = f"{root}/compatibility_report.json"
        store.write_json(report_path, model_dump(compatibility, mode="json"))
        composable = compatibility.recommended_action == "compose" and compatibility.gene_donor_map is not None
        plan_path = f"{root}/composition_plan.json"
        store.write_json(
            plan_path,
            {
                "schema_version": "1.0.0",
                "semantics": "t4_human_composition_plan",
                "composition_id": composition_id,
                "status": "awaiting_human_confirmation" if composable else "not_composable",
                "directive_path": str(request.get("directive_path") or ""),
                "population_id": population.population_id,
                "population_generation": population.generation,
                "input_fingerprint": population.input_fingerprint,
                "run_config_fingerprint": population.run_config_fingerprint,
                "compatibility_report": report_path,
                "compatibility": model_dump(compatibility, mode="json"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if composable:
            summary = (
                f"Compatibility Check found a potentially coherent Human-composed Candidate from {', '.join(source_ids)}. "
                f"Review the Gene Donor Map, then explicitly confirm composition {composition_id} to generate and independently score a new Candidate."
            )
            status = "awaiting_composition_confirmation"
        else:
            summary = (
                f"Compatibility Check recommends {compatibility.recommended_action}. No new Candidate was created; "
                "the source Candidates remain unchanged and can be kept in parallel or revised."
            )
            status = "not_composable"
        self._write_t4_operation_outcome(
            ctx,
            operation=request,
            status=status,
            summary=summary,
            details={"composition_id": composition_id, "compatibility_report": report_path, "composition_plan": plan_path},
        )

    async def _run_t4_human_composition_generation(
        self,
        *,
        ctx: ExecutionContext,
        run_config,
        controller: IdeaEvolutionController,
        evolver: LLMIdeaEvolver,
        operation: object,
    ):
        """Generate, validate, independently score, and integrate a confirmed Child."""

        request = operation if isinstance(operation, dict) else {}
        plan_path = str(request.get("composition_plan_path") or "")
        if not plan_path:
            raise ValueError("Human composition generation is missing its confirmed Composition Plan")
        store = T4ArtifactStore(ctx.workspace_dir)
        payload = store.read_model(plan_path, _T4OperationEnvelope).payload
        if payload.get("semantics") != "t4_human_composition_plan" or payload.get("status") != "awaiting_human_confirmation":
            raise ValueError("Human composition plan is not awaiting a valid final confirmation")
        population, dossiers = current_population_context(ctx.workspace_dir)
        if payload.get("population_id") != population.population_id:
            raise ValueError("Human composition plan is stale because the active Population changed")
        if payload.get("input_fingerprint") != population.input_fingerprint or payload.get("run_config_fingerprint") != population.run_config_fingerprint:
            raise ValueError("Human composition plan fingerprints are stale")
        compatibility = HumanCompositionCompatibility.model_validate(payload.get("compatibility"))
        source_ids = list(compatibility.source_candidate_ids)
        if not set(source_ids).issubset(dossiers):
            raise ValueError("Human composition source Candidate is no longer active")
        target_candidate_id = f"HC{population.generation + 1}-{compatibility.composition_id.removeprefix('HC-')}"
        child = await evolver.generate_human_composition(
            composition_id=compatibility.composition_id,
            target_candidate_id=target_candidate_id,
            compatibility=compatibility,
            parents=[dossiers[candidate_id] for candidate_id in source_ids],
        )
        result = await controller.integrate_human_composed_candidate(
            run_config,
            composition=compatibility,
            child=child,
        )
        payload.update(
            {
                "status": "generated_and_independently_scored",
                "generated_candidate_id": child.candidate_id,
                "output_population_id": result.population.population_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.write_json(plan_path, payload)
        self._write_t4_operation_outcome(
            ctx,
            operation=request,
            status="composition_scored",
            summary=(
                f"Human-composed Candidate {child.candidate_id} was created from the confirmed Gene Donor Map and independently rescored with its source Candidates. "
                "The source versions remain preserved; review the updated Portfolio before proceeding to T4.5."
            ),
            details={"composition_plan": plan_path, "candidate_id": child.candidate_id, "population_id": result.population.population_id},
        )
        return result

    async def _call_t4_evolution_role(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
        system_contract: str,
        user_prompt: str,
    ) -> str:
        """Use the normal provider recovery policy for one typed T4 role call."""

        retry_batches, cooldown, long_cooldown = self._llm_provider_recovery_policy()
        failed_batches = 0
        while True:
            budget.tick_step()
            budget.check()
            try:
                response = await self._await_llm_with_progress(
                    ctx=ctx,
                    step=budget.steps,
                    progress_step_limit="unlimited" if budget.unlimited_budget else str(budget.max_steps),
                    messages=[
                        {"role": "system", "content": system_contract},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=None,
                    temperature=0.2,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=int(self.global_timeout.get("llm_call") or 120),
                    max_retries_per_model=int(self.retry_policy.get("llm_retries") or 2),
                    retry_base_delay=float(self.retry_policy.get("llm_retry_delay") or 2),
                )
            except LLMProviderError as exc:
                if not self._is_recoverable_provider_error(exc):
                    raise RecoverableRuntimePause(self._public_provider_error_message(exc)) from exc
                failed_batches += 1
                action, delay = await self._choose_llm_provider_recovery(
                    ctx=ctx,
                    budget=budget,
                    failed_batches=failed_batches,
                    retry_batches=retry_batches,
                    cooldown_seconds=cooldown,
                    long_cooldown_seconds=long_cooldown,
                )
                if action != "retry":
                    raise RecoverableRuntimePause(self._public_provider_error_message(exc)) from exc
                await self._wait_before_llm_provider_retry(
                    ctx=ctx,
                    budget=budget,
                    seconds=delay,
                    attempt=failed_batches,
                    retry_batches=retry_batches,
                )
                continue
            budget.add_tokens(response.tokens_in, response.tokens_out, response.cost_usd)
            ctx.extra["t4_evolution_last_model"] = response.model_used
            ctx.extra["t4_evolution_last_endpoint"] = response.endpoint_used
            content = str(getattr(response.raw.choices[0].message, "content", "") or "")
            if not content:
                raise RecoverableRuntimePause("T4 role returned an empty response; progress is saved and resume can retry the role.")
            return content

    def _record_t4_evolution_activity(
        self,
        ctx: ExecutionContext,
        *,
        phase: EvolutionPhase,
        status: str,
    ) -> None:
        labels = {
            EvolutionPhase.EVIDENCE_ROUTING: "Evidence Routing",
            EvolutionPhase.OPPORTUNITY_MAP: "Opportunity Map",
            EvolutionPhase.FORMATION: "Multi-route Generation",
            EvolutionPhase.GENOME_FAMILY: "Idea Genome & Family",
            EvolutionPhase.SCORING: "Independent Scoring",
            EvolutionPhase.EVOLUTION_PLANNING: "Evolution Planning",
            EvolutionPhase.OFFSPRING: "Offspring & Rescoring",
            EvolutionPhase.SURVIVAL: "Survival & Portfolio",
        }
        label = labels.get(phase, phase.value.replace("_", " ").title())
        ctx.extra["t4_evolution_activity"] = f"{label} · {status.replace('_', ' ')}"
        ctx.extra["t4_evolution_next_artifact"] = {
            EvolutionPhase.EVIDENCE_ROUTING: "Evidence Index",
            EvolutionPhase.OPPORTUNITY_MAP: "Opportunity Map",
            EvolutionPhase.FORMATION: "Population P0",
            EvolutionPhase.GENOME_FAMILY: "Idea Family map",
            EvolutionPhase.SCORING: "Independent scores",
            EvolutionPhase.EVOLUTION_PLANNING: "Evolution plans",
            EvolutionPhase.OFFSPRING: "Union score batch",
            EvolutionPhase.SURVIVAL: "Population P1 and Portfolio",
        }.get(phase, "T4 artifact")

    def _render_t4_evolution_phase(
        self,
        *,
        phase: EvolutionPhase,
        status: str,
        payload: dict[str, object],
    ) -> None:
        """Render a compact Rich phase panel while preserving progress settings."""

        buffer = StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self.runtime_settings.ui.no_color,
            color_system=None if self.runtime_settings.ui.no_color else "truecolor",
            no_color=self.runtime_settings.ui.no_color,
            width=120,
            highlight=False,
        )
        render_t4_evolution_phase(phase, status, payload, console=console)
        rendered = buffer.getvalue().rstrip()
        if rendered:
            self.progress.emit(rendered, important=True)

    async def _maybe_finalize_t4_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4 续跑时，已有三件套可通过校验则直接完成。

        T4 的核心产物都是 workspace artifact。若它们已经存在并满足
        IdeationAgent.validate_outputs 的 schema、anchor、风险和预算约束，
        runtime 不再把“是否复用旧产物”交给 LLM 判断。
        """

        if ctx.task_id != "T4":
            return False

        # A complete Gate1 selection now advances through the Pre-Novelty
        # handoff below.  Reusing legacy formal artifacts here would place
        # final hypotheses and an experiment plan before T4.5 has audited the
        # selected Candidate.
        if self._t4_gate1_user_selection_exists(ctx):
            brief = ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml"
            selected = ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json"
            if brief.exists() and brief.stat().st_size > 0 and selected.exists() and selected.stat().st_size > 0:
                return False

        expected_paths = [
            ctx.workspace_dir / "ideation" / "hypotheses.md",
            ctx.workspace_dir / "ideation" / "exp_plan.yaml",
            ctx.workspace_dir / "ideation" / "risks.md",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "ideation" / "rejected_ideas.md",
            ctx.workspace_dir / "ideation" / "_family_distribution.md",
            ctx.workspace_dir / "ideation" / "_candidate_directions.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in expected_paths):
            return False
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=expected_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_resume_prefinalize_skipped",
            reason="final_outputs_older_than_t4_inputs",
        ):
            return False
        if not self._t4_final_outputs_follow_gate1(ctx):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t4_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Ideation Agent] T4 检测到已有 ideation 产物且校验通过，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_resume_prefinalize",
            {
                "outputs": [
                    str(path.relative_to(ctx.workspace_dir))
                    for path in expected_paths
                ],
            },
            action_type="t4_resume_prefinalize",
        )
        return True

    async def _maybe_advance_t4_pre_novelty_selection(self, ctx: ExecutionContext) -> bool:
        """Advance a confirmed complete Candidate to T4.5 without re-running T4.

        Gate1 already produced the LLM-authored Candidate and the deterministic
        Pre-Novelty compiler organized its draft hypotheses and provenance.  A
        second legacy T4 pass must not replace that bundle with formal
        hypotheses before novelty/collision review.
        """

        if ctx.task_id != "T4" or not self._t4_gate1_user_selection_exists(ctx):
            return False
        required = [
            ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
            ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
            ctx.workspace_dir / "ideation" / "selected" / "hypothesis_lineage.json",
            ctx.workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False
        self.progress.emit(
            "Selected Candidate 已整理为 Pre-Novelty brief。ResearchOS 将保留当前 Population，并把 novelty/collision audit 交给 T4.5；正式 Hypothesis Bundle 和 Experiment Plan 只会在 T4.5 明确通过后生成。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_pre_novelty_ready",
            {"outputs": [str(path.relative_to(ctx.workspace_dir)) for path in required]},
            action_type="t4_pre_novelty_handoff",
        )
        return True

    async def _maybe_finalize_t4_gate1_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4 resume: if Gate1 artifacts are ready, stop before another long LLM run."""

        if ctx.task_id != "T4":
            return False
        if self._t4_gate1_user_selection_exists(ctx):
            return False
        ok, err = validate_t4_gate1_ready(ctx.workspace_dir)
        # Candidate research content is model-authored. Do not silently turn a
        # provider failure into a template-derived Gate1 deck: users need the
        # model's actual mechanism, H1/H2/H3, and research judgement.
        if not ok:
            self.log.info("t4_gate1_prefinalize_skipped", reason=err)
            return False
        gate1_paths = self._t4_gate1_artifact_paths(ctx)
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=gate1_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_gate1_prefinalize_skipped",
            reason="gate1_artifacts_older_than_t4_inputs",
        ):
            return False
        self.progress.emit(
            "[轨迹] T4 Gate1 候选池已就绪：Pass1、Pass2、候选卡片和选择简报均已落盘，转入人工选择。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                    "ideation/bridge_coverage_review.json",
                ],
            },
            action_type="t4_gate1_ready",
        )
        return True

    def _maybe_finalize_t4_gate1_outputs(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
    ) -> tuple[str, str | None]:
        """Convert a partial/failed T4 run into a Gate1-ready success when possible."""

        if ctx.task_id != "T4" or self._t4_gate1_user_selection_exists(ctx):
            return stop_reason, error_msg
        if ctx.extra.get("completion_mode") in {"t4_resume_prefinalize", "t4_gate1_ready"}:
            return stop_reason, error_msg
        ok, err = validate_t4_gate1_ready(ctx.workspace_dir)
        # Keep provider failures resumable rather than manufacturing a
        # deterministic candidate deck. See the matching preflight path above.
        if not ok:
            self.log.info("t4_gate1_finalize_skipped", reason=err)
            return stop_reason, error_msg
        gate1_paths = self._t4_gate1_artifact_paths(ctx)
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=gate1_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_gate1_finalize_skipped",
            reason="gate1_artifacts_older_than_t4_inputs",
        ):
            return stop_reason, error_msg
        self.progress.emit(
            "[轨迹] T4 Gate1 候选池已就绪：Pass1、Pass2、候选卡片和选择简报均已落盘，暂停进入人工选择。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                    "ideation/bridge_coverage_review.json",
                ],
            },
            action_type="t4_gate1_ready",
        )
        return AgentResult.STOP_FINISHED, None

    @staticmethod
    def _t4_gate1_user_selection_exists(ctx: ExecutionContext) -> bool:
        from ..orchestration.state_machine import validate_t4_gate1_selection_file

        ok, _ = validate_t4_gate1_selection_file(ctx.workspace_dir)
        return ok

    def _t4_final_outputs_follow_gate1(self, ctx: ExecutionContext) -> bool:
        """Require final T4 artifacts to be produced after the formal Gate1 choice.

        A previous or interrupted T4 run may already have written final hypotheses
        before the user made a Gate1 decision. Reusing those files on resume would
        make the new formal gate cosmetic only, so final artifacts are considered
        reusable only when either Gate1 is not ready yet, or a recorded selection
        exists and the downstream final artifacts are newer than that selection.
        """

        selection_path = ctx.workspace_dir / "ideation" / "_gate1_user_selection.json"
        if not selection_path.exists() or selection_path.stat().st_size <= 0:
            ok, _ = validate_t4_gate1_ready(ctx.workspace_dir)
            if ok:
                self.log.info("t4_resume_prefinalize_skipped", reason="gate1_selection_missing")
                return False
            return True

        selection_mtime = selection_path.stat().st_mtime
        final_paths = [
            ctx.workspace_dir / "ideation" / "hypotheses.md",
            ctx.workspace_dir / "ideation" / "exp_plan.yaml",
            ctx.workspace_dir / "ideation" / "risks.md",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "ideation" / "rejected_ideas.md",
            ctx.workspace_dir / "ideation" / "selected_idea_brief.md",
        ]
        stale_paths = [
            str(path.relative_to(ctx.workspace_dir))
            for path in final_paths
            if path.exists() and path.stat().st_mtime <= selection_mtime
        ]
        if stale_paths:
            self.log.info(
                "t4_resume_prefinalize_skipped",
                reason="final_outputs_older_than_gate1_selection",
                stale_paths=stale_paths,
            )
            return False
        return True

    def _t4_gate1_artifact_paths(self, ctx: ExecutionContext) -> list[Path]:
        paths = [
            ctx.workspace_dir / "ideation" / "_pass1_forward_candidates.json",
            ctx.workspace_dir / "ideation" / "_pass2_grounding_review.json",
            ctx.workspace_dir / "ideation" / "_candidate_directions.json",
            ctx.workspace_dir / "ideation" / "_gate1_candidate_cards.md",
            ctx.workspace_dir / "ideation" / "_gate1_selection_brief.md",
        ]
        bridge_review = ctx.workspace_dir / "ideation" / "bridge_coverage_review.json"
        if bridge_review.exists():
            paths.append(bridge_review)
        return paths

    @staticmethod
    def _t4_stop_reason_allows_gate1_recovery(stop_reason: str, error_msg: str | None) -> bool:
        if stop_reason not in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_ERROR, AgentResult.STOP_MAX_STEPS}:
            return False
        text = str(error_msg or "").casefold()
        return any(
            marker in text
            for marker in (
                "llm provider",
                "provider",
                "timeout",
                "temporarily unavailable",
                "暂时不可用",
                "连续超时",
                "all candidates failed",
            )
        )

    def _t4_last_error_allows_gate1_recovery(self, ctx: ExecutionContext) -> bool:
        resume_path = ctx.workspace_dir / "_runtime" / "resume" / "t4_resume_state.json"
        text = ""
        if resume_path.exists():
            try:
                text += resume_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        state_path = ctx.workspace_dir / "state.yaml"
        if state_path.exists():
            try:
                text += "\n" + state_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            except OSError:
                pass
        return self._t4_stop_reason_allows_gate1_recovery(AgentResult.STOP_INTERRUPTED, text)

    def _t4_upstream_input_paths(self, ctx: ExecutionContext) -> list[Path]:
        return [
            ctx.workspace_dir / "project.yaml",
            ctx.workspace_dir / "literature" / "synthesis.md",
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "domain_map.json",
            ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
            ctx.workspace_dir / "literature" / "missing_areas.md",
            ctx.workspace_dir / "ideation" / "survey_insights.json",
            ctx.workspace_dir / "user_seeds" / "seed_ideas.md",
            ctx.workspace_dir / "user_seeds" / "seed_constraints.md",
        ]

    def _t45_output_paths(self, ctx: ExecutionContext) -> list[Path]:
        paths = [
            ctx.workspace_dir / "ideation" / "novelty_audit.md",
        ]
        tuples_dir = ctx.workspace_dir / "ideation" / "_mechanism_tuples"
        if tuples_dir.exists():
            paths.extend(path for path in tuples_dir.rglob("*") if path.is_file())
        design_tuples_dir = ctx.workspace_dir / "ideation" / "_design_rationale_tuples"
        if design_tuples_dir.exists():
            paths.extend(path for path in design_tuples_dir.rglob("*") if path.is_file())
        collision_path = ctx.workspace_dir / "ideation" / "collision_cases.md"
        if collision_path.exists():
            paths.append(collision_path)
        for rel in (
            "ideation/hypotheses.md",
            "ideation/exp_plan.yaml",
            "ideation/contribution_hypothesis_map.yaml",
            "ideation/validation_map.yaml",
            "ideation/kill_criteria.yaml",
            "ideation/post_novelty_formalization.json",
        ):
            path = ctx.workspace_dir / rel
            if path.exists():
                paths.append(path)
        return paths

    def _t45_upstream_input_paths(self, ctx: ExecutionContext) -> list[Path]:
        return [
            ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
            ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
            ctx.workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "literature" / "synthesis.md",
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
        ]

    def _outputs_newer_than_inputs(
        self,
        ctx: ExecutionContext,
        *,
        outputs: list[Path],
        inputs: list[Path],
        event: str,
        reason: str,
    ) -> bool:
        existing_outputs = [path for path in outputs if path.exists() and path.stat().st_size > 0]
        if not existing_outputs:
            self.log.info(event, reason=f"{reason}:missing_outputs")
            return False
        existing_inputs = [path for path in inputs if path.exists() and path.stat().st_size > 0]
        if not existing_inputs:
            return True

        oldest_output_mtime = min(path.stat().st_mtime for path in existing_outputs)
        newer_inputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in existing_inputs
            if path.stat().st_mtime > oldest_output_mtime
        ]
        if newer_inputs:
            oldest_outputs = [
                str(path.relative_to(ctx.workspace_dir))
                for path in existing_outputs
                if path.stat().st_mtime == oldest_output_mtime
            ]
            self.log.info(
                event,
                reason=reason,
                newer_inputs=newer_inputs,
                oldest_outputs=oldest_outputs,
            )
            return False
        return True

    async def _maybe_finalize_t45_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4.5 续跑时，已有审计和 mechanism tuples 合格则直接完成。"""

        if ctx.task_id != "T4.5":
            return False

        required_paths = [
            ctx.workspace_dir / "ideation" / "novelty_audit.md",
            ctx.workspace_dir / "ideation" / "_mechanism_tuples",
        ]
        if any(not path.exists() for path in required_paths):
            return False
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=self._t45_output_paths(ctx),
            inputs=self._t45_upstream_input_paths(ctx),
            event="t45_resume_prefinalize_skipped",
            reason="novelty_outputs_older_than_t45_inputs",
        ):
            return False
        ok, err = validate_t45_fingerprint_report(ctx.workspace_dir)
        if not ok:
            self.log.info("t45_resume_prefinalize_skipped", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t45_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Novelty Auditor Agent] T4.5 检测到已有 novelty audit 且校验通过，跳过重复 LLM",
            important=True,
        )
        outputs = [
            "ideation/novelty_audit.md",
            "ideation/_mechanism_tuples",
        ]
        collision_path = ctx.workspace_dir / "ideation" / "collision_cases.md"
        if collision_path.exists():
            outputs.append("ideation/collision_cases.md")
        self._record_runtime_completion(
            ctx,
            "t45_resume_prefinalize",
            {"outputs": outputs},
            action_type="t45_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_external_wait_before_llm(self, ctx: ExecutionContext) -> bool:
        """T5-EXTERNAL-WAIT is a deterministic external handoff wait boundary."""

        if ctx.task_id != "T5-EXTERNAL-WAIT":
            return False

        report = validate_external_executor_ready(
            ctx.workspace_dir,
            "external_executor/result_pack.json",
            "external_executor/executor_status.json",
        )
        if not report.get("ok"):
            raise RecoverableRuntimePause(str(report.get("message") or "WAITING_EXTERNAL: result pack not ready"))

        output_path = ctx.workspace_dir / "external_executor" / "wait_acceptance_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.progress.emit(
            "[Experimenter Agent] T5-EXTERNAL-WAIT 检测到外部 result_pack 已就绪，跳过 LLM 并进入 T7-INGEST",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "external_wait_prefinalize",
            {"outputs": ["external_executor/wait_acceptance_report.json"]},
            action_type="external_wait_prefinalize",
        )
        return True

    async def _maybe_finalize_t9_submission_before_hooks(self, ctx: ExecutionContext) -> bool:
        """Finish T9 from an already valid submission bundle before hooks/LLM.

        T9's compile-environment pre-hook is necessary when the bundle still
        needs work, but it should not block resume if the current workspace
        already contains a validator-clean `submission/bundle`. This also
        avoids launching the SubmissionAgent LLM merely to rediscover that the
        existing PDF/report are already valid.
        """

        if ctx.task_id != "T9" or self.agent.spec.name != "submission":
            return False

        bundle_dir = ctx.workspace_dir / "submission" / "bundle"
        required = [
            bundle_dir / "main.tex",
            bundle_dir / "references.bib",
            bundle_dir / "main.pdf",
            bundle_dir / "main.log",
            ctx.workspace_dir / "submission" / "compile_report.json",
            ctx.workspace_dir / "submission" / "migration_report.md",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t9_submission_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Submission Agent] T9 检测到已有投稿包且校验通过，跳过环境检查和重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t9_submission_prefinalize",
            {
                "outputs": [
                    "submission/bundle/main.tex",
                    "submission/bundle/main.pdf",
                    "submission/bundle/main.log",
                    "submission/compile_report.json",
                    "submission/migration_report.md",
                ],
            },
            action_type="t9_submission_prefinalize",
        )
        return True

    async def _maybe_finalize_paper_claim_audit_before_llm(
        self,
        ctx: ExecutionContext,
        policy: WorkspaceAccessPolicy,
    ) -> bool:
        """Run the final T8 paper-claim audit as a deterministic tool boundary."""

        if ctx.task_id != "T8-PAPER-CLAIM-AUDIT":
            return False

        required = [
            ctx.workspace_dir / "drafts" / "paper.tex",
            ctx.workspace_dir / "drafts" / "experiment_evidence_pack.json",
            ctx.workspace_dir / "drafts" / "result_to_claim.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False

        tool = AuditPaperClaimsTool(policy)
        result = await tool.execute(
            paper_path="drafts/paper.tex",
            evidence_pack_path="drafts/experiment_evidence_pack.json",
            result_to_claim_path="drafts/result_to_claim.json",
            output_path="drafts/paper_claim_audit.md",
        )
        if not result.ok:
            self.log.warning("paper_claim_audit_prefinalize_failed", error=result.error, content=result.content)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning("paper_claim_audit_prefinalize_validation_failed", error=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8-PAPER-CLAIM-AUDIT 已用确定性工具完成，跳过 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "paper_claim_audit_prefinalize",
            {"outputs": ["drafts/paper_claim_audit.md", "drafts/paper_claim_audit.json"]},
            action_type="paper_claim_audit_prefinalize",
        )
        return True

    async def _maybe_finalize_t8_section_plan_before_llm(
        self,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
    ) -> bool:
        """Repair/initialize T8 section state deterministically before LLM work.

        `T8-SECTION-PLAN` is a mechanical boundary: it should call
        initialize_manuscript_state and stop. If a previous run let the LLM
        hand-write an incompatible paper_state.json, resume should repair it
        from the already-approved outline/plans instead of spending another
        LLM run on the same deterministic job.
        """

        if ctx.task_id != "T8-SECTION-PLAN":
            return False
        if ctx.mode not in {None, "section_plan"} and ctx.extra.get("phase") != "section_plan":
            return False

        if not can_repair_t8_section_plan(ctx.workspace_dir):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if ok:
            self.progress.emit(
                "[Writer Agent] T8-SECTION-PLAN 检测到 paper_state/section_outlines 已合格，跳过重复 LLM",
                important=True,
            )
            self._record_runtime_completion(
                ctx,
                "t8_section_plan_prefinalize",
                {
                    "outputs": [
                        "drafts/paper_state.json",
                        "drafts/section_outlines",
                    ],
                },
                action_type="t8_section_plan_prefinalize",
            )
            return True

        self.progress.emit(
            "[Writer Agent] T8-SECTION-PLAN 检测到已有计划文件但状态不合格，"
            "使用 initialize_manuscript_state 确定性修复...",
            important=True,
        )
        project = {}
        project_path = ctx.workspace_dir / "project.yaml"
        if project_path.exists():
            try:
                import yaml

                loaded = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    project = loaded
            except Exception:
                project = {}
        ok, err = await repair_t8_section_plan_outputs(
            ctx.workspace_dir,
            target_venue=str(project.get("target_venue") or ""),
        )
        if not ok:
            self.log.warning(
                "t8_section_plan_prefinalize_failed",
                error=err,
            )
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning("t8_section_plan_prefinalize_validation_failed", error=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8-SECTION-PLAN 状态修复成功，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_section_plan_prefinalize",
            {
                "outputs": [
                    "drafts/paper_state.json",
                    "drafts/section_outlines",
                ],
            },
            action_type="t8_section_plan_prefinalize",
        )
        return True

    async def _maybe_finalize_t8_manuscript_before_llm(self, ctx: ExecutionContext) -> bool:
        """Refresh T8 assembled manuscript/audits before spending another LLM run.

        T8-DRAFT and T8-REVISE are artifact-first boundaries. If section files,
        patch lists, revision responses, and audits are already present, resume
        should first rebuild deterministic outputs from section files and then
        validate. This prevents stale craft audits from sending Writer into
        repeated section rewrites for old or soft checks.
        """

        if ctx.task_id not in {"T8-DRAFT", "T8-REVISE-1", "T8-REVISE-2"}:
            return False
        if ctx.mode not in {None, "draft", "revise"} and ctx.extra.get("phase") not in {"draft", "revise"}:
            return False
        if not can_refresh_t8_manuscript_outputs(ctx.workspace_dir):
            return False

        self.progress.emit(
            "[Writer Agent] T8 检测到已有章节草稿，先确定性重拼 manuscript 并刷新审计",
            important=True,
        )
        ok, err = await refresh_t8_manuscript_outputs(ctx.workspace_dir)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_refresh_failed", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_validation_skipped", reason=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8 manuscript 产物已合格，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_manuscript_prefinalize",
            {
                "outputs": [
                    "drafts/paper.tex",
                    "drafts/manuscript_audit.md",
                    "drafts/craft_audit.md",
                    "drafts/craft_audit.json",
                ],
            },
            action_type="t8_manuscript_prefinalize",
        )
        return True

    async def _maybe_prepare_t35_before_llm(
        self,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
    ) -> bool:
        """T3.5 may prebuild evidence scaffolding, but must not finish.

        Synthesis is a knowledge-heavy task. The tool can organize notes into a
        workbench and outline, yet final section claims must come from the
        Reader LLM after inspecting those artifacts.
        """

        if ctx.task_id != "T3.5":
            return False
        mode_params = get_agent_mode_params("reader", "synthesize")
        if not bool(mode_params.get("prebuild_workbench_before_llm", False)):
            return False
        notes_dir = ctx.workspace_dir / "literature" / "deep_read_notes"
        bridge_notes_dir = ctx.workspace_dir / "literature" / "bridge_notes"
        note_files = [path for path in notes_dir.glob("*.md") if is_paper_note_file(path)] if notes_dir.exists() else []
        if bridge_notes_dir.exists():
            note_files.extend(path for path in bridge_notes_dir.glob("**/*.md") if is_paper_note_file(path))
        if not note_files:
            return False
        staged_outputs = [
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "synthesis_outline.md",
            ctx.workspace_dir / "literature" / "synthesis_draft.md",
        ]
        if all(path.exists() and path.stat().st_size > 0 for path in staged_outputs):
            newest_note_mtime = max((path.stat().st_mtime for path in note_files), default=0)
            oldest_staged_mtime = min(path.stat().st_mtime for path in staged_outputs)
            if oldest_staged_mtime >= newest_note_mtime:
                self.progress.emit(
                    "[Synthesizer Agent] T3.5 使用已有结构化综合材料\n"
                    f"- 输入: 检测到 {len(note_files)} 份 paper notes，现有 workbench 未过期\n"
                    "- 输出: literature/synthesis_workbench.json；literature/synthesis_outline.md；literature/synthesis_draft.md\n"
                    "- 后续: LLM 将复核这些材料并写最终 synthesis.md",
                    important=True,
                )
                actions = ctx.extra.setdefault("runtime_actions", [])
                if isinstance(actions, list):
                    actions.append(
                        {
                            "type": "t35_synthesis_workbench_reused",
                            "mode": "t35_workbench_reused",
                            "outputs": [
                                str(path.relative_to(ctx.workspace_dir))
                                for path in staged_outputs
                            ],
                        }
                    )
                ctx.extra["t35_workbench_prepared"] = True
                ctx.extra["t35_workbench_reused"] = True
                return True

        from ..tools.literature_synthesis import BuildSynthesisWorkbenchTool

        self.progress.emit(
            "[Synthesizer Agent] T3.5 先执行分阶段 synthesis workbench 生成，用于把 paper notes 组织成可审计综述材料",
            important=True,
        )
        tool = BuildSynthesisWorkbenchTool(policy)
        result = await tool.execute(write_final=False, render_draft=False)
        if not result.ok:
            self.log.warning("t35_workbench_failed", error=result.error, content=result.content)
            return False

        data = result.data if isinstance(result.data, dict) else {}
        outputs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
        output_bits = [
            str(path)
            for path in (
                outputs.get("workbench"),
                outputs.get("outline"),
                outputs.get("draft"),
            )
            if path
        ]
        summary_bits = [
            f"深读笔记 {data.get('note_count', 0)}",
            f"摘要轻读 {data.get('abstract_note_count', 0)}",
            f"方法家族 {data.get('family_count', 0)}",
        ]
        citation_target = data.get("citation_coverage_target")
        if citation_target not in (None, ""):
            summary_bits.append(f"建议覆盖引用 {citation_target}")
        self.progress.emit(
            "[Synthesizer Agent] T3.5 结构化综合摘要\n"
            f"- 输入: {'；'.join(summary_bits)}\n"
            f"- 输出: {'；'.join(output_bits) if output_bits else 'literature/synthesis_workbench.json / synthesis_outline.md / synthesis_draft.md'}\n"
            "- 后续: LLM 将复核 workbench 并写最终 synthesis.md",
            important=True,
        )

        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": "t35_synthesis_workbench_prepared",
                    "mode": "t35_workbench_prepared",
                    "outputs": list((result.data.get("outputs") or {}).values())
                    if isinstance(result.data.get("outputs"), dict)
                    else [],
                }
            )
        ctx.extra["t35_workbench_prepared"] = True
        return True

    async def _finalize_t2_from_raw(
        self,
        ctx: ExecutionContext,
        *,
        mode: str,
        min_raw_count: int,
        start_message: str,
        success_message: str,
    ) -> bool:
        if ctx.task_id != "T2":
            return False

        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        raw_count = self._count_jsonl_records(raw_path)
        if raw_count < min_raw_count:
            return False

        needs_finalize = any(
            not path.exists()
            for name, path in ctx.outputs_expected.items()
            if name != "papers_raw"
        )
        if not needs_finalize:
            ok, _err = self.agent.validate_outputs(ctx)
            manifest_ok, _manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
            if ok and manifest_ok:
                self._record_runtime_completion(ctx, mode, {"raw_count": raw_count})
                return True
            # A raw-pool enrichment can occur after an interrupted finalize.
            # Existing files are then structurally valid but describe a stale
            # raw snapshot. Rebuild once so every downstream artifact and the
            # manifest agree on the same durable candidate pool.
            needs_finalize = True

        if not needs_finalize:
            return False

        self.progress.emit(start_message, important=True)
        recovery = await finalize_t2_outputs(ctx.workspace_dir)
        if not recovery.get("ok"):
            reason = recovery.get("reason") or "unknown"
            self.log.warning(f"{mode}_failed", reason=reason, recovery=recovery)
            self.progress.error_context(
                stage="T2 确定性收尾",
                agent=self.agent.spec.name,
                message=str(reason),
                log_path=str(ctx.workspace_dir / "_runtime" / "logs" / "researchos.log"),
            )
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning(f"{mode}_validation_failed", error=err, recovery=recovery)
            self.progress.error_context(
                stage="T2 确定性收尾后校验",
                agent=self.agent.spec.name,
                message=str(err or "unknown"),
                log_path=str(ctx.workspace_dir / "_runtime" / "logs" / "researchos.log"),
            )
            return False

        self.progress.emit(success_message, important=True)
        self.progress.emit(
            "[Scout Agent] T2 确定性收尾完成，papers_raw 已被整理为可继续阅读的候选池",
            important=True,
        )
        t2_config = load_t2_finalize_config(ctx.workspace_dir)
        progress_rel = str(getattr(t2_config, "progress_file", "") or "literature/temp/scout_progress.md")
        self.progress.progress_file_update(
            label="Scout/T2 收尾进度",
            path=progress_rel,
            bullets=summarize_progress_markdown(ctx.workspace_dir / progress_rel, max_items=4),
        )
        self._record_runtime_completion(ctx, mode, recovery)
        self.log.debug(f"{mode}_succeeded", recovery=recovery)
        return True

    def _record_runtime_completion(
        self,
        ctx: ExecutionContext,
        mode: str,
        details: dict[str, object],
        *,
        action_type: str = "t2_finalize_from_raw",
    ) -> None:
        ctx.extra["completion_mode"] = mode
        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": action_type,
                    "mode": mode,
                    "raw_count": details.get("raw_count"),
                    "dedup_count": details.get("dedup_count"),
                    "trace_count": details.get("trace_count"),
                    "outputs": details.get("outputs"),
                }
            )

    @staticmethod
    def _count_jsonl_records(path: Path) -> int:
        if not path.exists() or path.stat().st_size <= 0:
            return 0
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        count += 1
        except OSError:
            return 0
        return count

    @staticmethod
    def _t2_finish_finalize_min_raw(ctx: ExecutionContext) -> int:
        config_default = load_t2_finalize_config(ctx.workspace_dir).finish_finalize_min_raw
        raw_value = ctx.extra.get("t2_finish_finalize_min_raw", config_default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return config_default
        return max(10, value)

    @staticmethod
    def _is_resume_run(ctx: ExecutionContext) -> bool:
        if ctx.extra.get("resume_reason") == "retry_after_failure" and not ctx.extra.get(
            "allow_t2_failure_recovery"
        ):
            return False
        return bool(
            ctx.extra.get("is_resume")
            or ctx.extra.get("resumed_from_run_id")
            or ctx.extra.get("resumed_from")
            or ctx.extra.get("resume_reason") in {"interrupted", "iteration"}
        )

    @staticmethod
    def _allow_t2_exit_recovery(ctx: ExecutionContext) -> bool:
        if ctx.extra.get("allow_t2_failure_recovery"):
            return True
        if ctx.extra.get("resume_reason") == "retry_after_failure":
            return False
        return bool(
            ctx.extra.get("is_resume")
            or ctx.extra.get("resumed_from_run_id")
            or ctx.extra.get("resumed_from")
            or ctx.extra.get("resume_reason") in {"interrupted", "iteration"}
        )

    async def _execute_one_tool_call(
        self,
        tc: ToolCall,
        tool_map: dict[str, Tool],
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        step: int,
        budget: BudgetTracker | None = None,
        tool_failure_cache: dict[tuple[str, str], Message] | None = None,
        run_logger: RunLogger | None = None,
    ) -> Message:
        started = time.time()
        tool = tool_map.get(tc.name)
        if tool is None:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"ERROR: unknown tool '{tc.name}'. Available: {sorted(tool_map)}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="unknown_tool",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        t4_order_error = self._t4_artifact_write_order_error(ctx, tc)
        if t4_order_error:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"ERROR: {t4_order_error}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="t4_artifact_order_violation",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        if tool.requires_human_approval:
            # 高风险工具先经过 HumanInterface 审批。
            human_started = time.time()
            try:
                approved = await self.human.ask_approval(tool_name=tc.name, arguments=tc.arguments)
            except HumanInputUnavailable as exc:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval input unavailable: {exc}",
                    is_error=True,
                    step=step,
                    metadata={"data": {"input_unavailable": True}, "error": "human_input_unavailable"},
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data=tool_msg.metadata.get("data") or {},
                        error="human_input_unavailable",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg
            except Exception as exc:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval failed: {exc!r}",
                    is_error=True,
                    step=step,
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data={},
                        error="approval_failed",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg
            finally:
                if budget is not None:
                    budget.exclude_wall_time(time.time() - human_started)
            if not approved:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="Rejected by human.",
                    is_error=True,
                    step=step,
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data={},
                        error="human_rejected",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg

        try:
            # 先用 pydantic schema 做参数校验。
            parsed = tool.parameters_schema(**tc.arguments)
        except Exception as exc:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Parameter validation error: {exc}",
                is_error=True,
                step=step,
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="parameter_validation",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        failure_cache_key = self._tool_failure_cache_key(tc.name, model_dump(parsed))
        if failure_cache_key and tool_failure_cache is not None and failure_cache_key in tool_failure_cache:
            cached = tool_failure_cache[failure_cache_key]
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=(
                    "Skipped tool call because the same request already failed in this run.\n\n"
                    + (cached.content or "")
                ),
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
                metadata={
                    "data": {
                        "cached_failure": True,
                        "cache_key": failure_cache_key[1],
                        "original_step": cached.step,
                    },
                    "error": "cached_failure",
                },
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data=tool_msg.metadata.get("data") or {},
                    error="cached_failure",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        try:
            max_tool_timeout = self._timeout_for_tool(tc.name, tool)
            tool_timeout = min(tool.timeout_seconds, max_tool_timeout)
            # 工具自身可有细粒度超时，但 runtime 仍统一包一层 wait_for。
            tool_execute_started = time.time()
            try:
                result: ToolResult = await asyncio.wait_for(
                    tool.execute(**model_dump(parsed)),
                    timeout=tool_timeout,
                )
            finally:
                if budget is not None and tc.name == "ask_human":
                    budget.exclude_wall_time(time.time() - tool_execute_started)
        except asyncio.TimeoutError:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool timed out after {tool_timeout}s",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            self._remember_tool_failure(failure_cache_key, tool_msg, tool_failure_cache)
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="timeout",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except ToolAccessDenied as exc:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Access denied: {exc}",
                is_error=True,
                step=step,
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="access_denied",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except ToolError as exc:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool error: {exc}",
                is_error=True,
                step=step,
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="tool_error",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except Exception as exc:
            self.log.exception("tool_crashed", tool=tc.name)
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool crashed unexpectedly: {exc!r}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="tool_crashed",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        auto_persist_metadata = await self._maybe_auto_persist_t2_search_result(
            ctx=ctx,
            policy=policy,
            tool_name=tc.name,
            tool_arguments=model_dump(parsed),
            result=result,
        )
        self._record_t2_search_ledger(
            ctx=ctx,
            tool_name=tc.name,
            tool_arguments=model_dump(parsed),
            result=result,
            auto_persist_metadata=auto_persist_metadata,
        )
        try:
            task_io = get_task_io(ctx.task_id)
        except KeyError:
            task_io = None
        self._annotate_optional_input_absence(
            ctx=ctx,
            task_io=task_io,
            tool_name=tc.name,
            arguments=model_dump(parsed),
            result=result,
        )
        if ctx.task_id == "T2" and tc.name in T2_AUTO_PERSIST_SEARCH_TOOLS and not result.ok:
            t2_config = load_t2_finalize_config(ctx.workspace_dir)
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tc.name,
                tool_arguments=model_dump(parsed),
                result=result,
                paper_count=0,
                persisted_delta=0,
                merged_count=0,
                raw_count_after=self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl"),
                append_status=str(result.error or "failed"),
            )
        content = result.content
        metadata = {"data": result.data, "error": result.error}
        content, cap_metadata = self._cap_tool_content_for_context(tc.name, content)
        if cap_metadata:
            metadata["context_cap"] = cap_metadata
        if auto_persist_metadata:
            metadata["auto_persist_raw"] = auto_persist_metadata
            suffix = auto_persist_metadata.get("content_suffix")
            if suffix:
                content = f"{content}\n\n{suffix}" if content else suffix

        tool_msg = Message.tool(
            tool_call_id=tc.id,
            name=tc.name,
            content=content,
            is_error=not result.ok,
            step=step,
            duration_ms=int((time.time() - started) * 1000),
            metadata=metadata,
        )
        if not result.ok:
            self._remember_tool_failure(failure_cache_key, tool_msg, tool_failure_cache)
        self._record_tool_side_effect_metadata(ctx, tc.name, model_dump(parsed), result)
        self._emit_tool_progress(tc.name, result)
        if run_logger is not None:
            run_logger.tool_result(
                tc.name,
                model_dump(parsed),
                ok=result.ok,
                content=content,
                data=result.data,
                error=result.error,
                duration_ms=tool_msg.duration_ms,
                metadata=metadata,
                step=step,
            )
        return tool_msg

    @staticmethod
    def _annotate_optional_input_absence(
        *,
        ctx: ExecutionContext,
        task_io: dict[str, object] | None,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> None:
        """Mark only declared optional reads as a non-blocking public skip."""

        if result.ok or tool_name != "read_file" or str(result.error or "") not in {"not_found", "file_not_found"}:
            return
        if not isinstance(task_io, dict):
            return
        requested = str(arguments.get("path") or "").strip().lstrip("./")
        inputs = task_io.get("inputs")
        if not requested or not isinstance(inputs, dict):
            return
        required = {str(key) for key in task_io.get("required_inputs") or []}
        for key, declared in inputs.items():
            if str(declared).lstrip("./") != requested:
                continue
            if str(key) in required:
                return
            result.data = {
                **(result.data if isinstance(result.data, dict) else {}),
                "optional_input": True,
                "optional_input_label": str(key),
                "path": requested,
                "display_disposition": "skipped",
            }
            return

    def _emit_tool_progress(self, tool_name: str, result: ToolResult) -> None:
        """Print deterministic progress summaries that users need during long runs."""

        if self.runtime_settings.ui.quiet:
            return
        data = result.data if isinstance(result.data, dict) else {}
        progress = str(data.get("progress") or "").strip()
        if tool_name == "save_paper_note" and progress:
            self.progress.emit(f"[Reader Agent] {summarize_reader_note_progress(data, progress=progress)}")

    @staticmethod
    def _looks_like_human_interaction_request(message: Message) -> bool:
        """Detect text-only assistant turns that are actually waiting on a user.

        This is a runtime safety net. Prompts should still require explicit
        ask_human/gate usage, but if a model prints a question or choice menu
        without a tool call, continuing to the next LLM turn would silently
        skip the user interaction.
        """

        content = (message.content or "").strip()
        if not content:
            return False
        normalized = content.lower()

        # Plain status narration such as "我来检查已有材料" must not open an
        # input box. This safety net only catches explicit user-facing
        # requests to choose, confirm, answer, or provide missing information.
        strong_markers = (
            "请选择",
            "请输入",
            "请回答",
            "请确认",
            "请你确认",
            "请补充",
            "请提供",
            "请明确",
            "等待用户",
            "需要用户",
            "需要你回答",
            "需要你确认",
            "需要你选择",
            "请告诉我",
            "告诉我你的",
            "please choose",
            "please answer",
            "please confirm",
            "please provide",
            "provide your",
            "tell me your",
            "do you want me to",
            "waiting for user",
        )
        if any(marker in normalized for marker in strong_markers):
            return True

        question_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip().endswith(("?", "？"))
        ]
        if question_lines:
            explicit_question_prefixes = (
                "请",
                "你是否",
                "是否",
                "要不要",
                "能否",
                "可否",
                "do you",
                "would you",
                "which",
                "what would you like",
            )
            for line in question_lines:
                lowered = line.lower()
                if lowered.startswith(explicit_question_prefixes):
                    return True

        return bool(
            re.search(r"(?m)^\s*(?:\[\d+\]|\d+[.)、])\s+.+", content)
            and re.search(r"(?i)(请选择|please choose|选择|option|继续|停止|confirm|确认)", content)
        )

    @staticmethod
    def _build_autobridged_human_question(content: str) -> str:
        """Explain why runtime is asking before forwarding model text."""

        return (
            "Runtime 检测到 Agent 正在请求人工选择/确认，但这一轮没有显式调用 ask_human。"
            "为避免跳过你的决策，ResearchOS 已暂停在这里。\n\n"
            "请根据下面 Agent 原始请求作答；如果这是误触发，可以回答“继续”，runtime 会把回答记录为人工输入。\n\n"
            f"--- Agent 原始请求 ---\n{content}"
        )

    @staticmethod
    def _ensure_ask_human_questions_are_self_contained(message: Message) -> None:
        """Make ask_human questions visible even when the model relies on prior text.

        Models often print a long draft/choice list in assistant content, then call
        ask_human with a short question like "请确认以上草案". In normal CLI mode
        assistant content is not always shown, so the user would see an input box
        without the actual draft. This keeps the human gate self-contained.
        """

        content = (message.content or "").strip()
        if not content:
            return
        for tool_call in message.tool_calls:
            if tool_call.name != "ask_human":
                continue
            raw_question = str(tool_call.arguments.get("question") or "").strip()
            if not raw_question:
                tool_call.arguments["question"] = content
                continue
            if AgentRunner._ask_human_question_depends_on_hidden_context(raw_question):
                tool_call.arguments["question"] = (
                    "下面是 Agent 本轮生成的完整上下文，请先阅读，再回答后面的人工输入问题。\n\n"
                    f"{content}\n\n"
                    "----- 需要你回答的问题 -----\n"
                    f"{raw_question}"
                )

    @staticmethod
    def _ask_human_question_depends_on_hidden_context(question: str) -> bool:
        normalized = re.sub(r"\s+", "", question.strip().lower())
        if not normalized:
            return True
        context_dependent_markers = (
            "以上",
            "上述",
            "上面",
            "前面",
            "如上",
            "以上草案",
            "上述草案",
            "以上project",
            "以上`project.yaml`",
            "以上5个",
            "以上五个",
            "这些方向",
            "这些候选",
            "请确认以上",
            "请确认上述",
            "请确认草案",
            "请确认以上`project.yaml`草案",
            "above",
            "aforementioned",
            "theabove",
            "confirmtheabove",
            "confirmthedraftabove",
        )
        return any(marker in normalized for marker in context_dependent_markers)

    @staticmethod
    def _record_tool_side_effect_metadata(
        ctx: ExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> None:
        """记录 validator 需要的运行期证据，例如 Docker 使用和代码重写次数。"""

        if tool_name == "docker_exec":
            ctx.extra["docker_exec_call_count"] = int(ctx.extra.get("docker_exec_call_count", 0) or 0) + 1
            if result.ok:
                ctx.extra["docker_exec_success_count"] = int(ctx.extra.get("docker_exec_success_count", 0) or 0) + 1
            return

        if tool_name == "latex_compile":
            ctx.extra["latex_compile_call_count"] = int(ctx.extra.get("latex_compile_call_count", 0) or 0) + 1
            if result.ok:
                ctx.extra["latex_compile_success_count"] = int(ctx.extra.get("latex_compile_success_count", 0) or 0) + 1
            return

        if tool_name not in {"write_file", "write_structured_file"} or not result.ok:
            return

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str):
            return
        normalized_path = raw_path.strip().lstrip("./")
        counts = ctx.extra.setdefault("artifact_write_counts", {})
        if isinstance(counts, dict):
            counts[normalized_path] = int(counts.get(normalized_path, 0) or 0) + 1
        if ctx.task_id == "T5" and normalized_path == "pilot/pilot_code/run_pilot.py":
            ctx.extra["pilot_code_write_count"] = int(ctx.extra.get("pilot_code_write_count", 0) or 0) + 1

    @staticmethod
    def _is_recoverable_tool_pause(tool_name: str, tool_msg: Message) -> bool:
        """Return true for tool failures that should pause instead of burning retries."""

        if tool_name not in {"ask_human", "docker_exec", "latex_compile"}:
            return False
        if not tool_msg.metadata.get("is_error"):
            return False
        error = tool_msg.metadata.get("error")
        data = tool_msg.metadata.get("data")
        if isinstance(data, dict) and not error:
            error = data.get("error")
        if error == "human_input_unavailable":
            return True
        content = tool_msg.content or ""
        if isinstance(error, str) and error.startswith("waiting_environment"):
            return True
        return "WAITING_ENVIRONMENT" in content

    @staticmethod
    def _tool_failure_cache_key(tool_name: str, arguments: dict[str, object]) -> tuple[str, str] | None:
        if tool_name not in TOOL_FAILURE_CACHE_NAMES:
            return None
        if tool_name == "fetch_paper_pdf":
            paper_id = str(arguments.get("paper_id") or "").strip().casefold()
            save_path = str(arguments.get("save_path") or "").strip().casefold()
            if paper_id:
                return (tool_name, f"paper_id:{paper_id}")
            if save_path:
                return (tool_name, f"save_path:{save_path}")
        return None

    def _timeout_for_tool(self, tool_name: str, tool: Tool) -> float:
        """Return the runtime timeout cap for a tool.

        Long-running experiment and LaTeX tools need their dedicated timeout
        budget; otherwise the global small-tool cap kills valid T7/T9 work.
        """

        if tool_name == "docker_exec":
            return float(
                self.global_timeout.get("docker_operation")
                or self.global_timeout.get("max_tool_call")
                or tool.timeout_seconds
            )
        if tool_name == "latex_compile":
            return float(
                self.global_timeout.get("latex_compile")
                or self.global_timeout.get("max_compile")
                or self.global_timeout.get("docker_operation")
                or self.global_timeout.get("max_tool_call")
                or tool.timeout_seconds
            )
        return float(self.global_timeout.get("max_tool_call") or tool.timeout_seconds)

    @staticmethod
    def _remember_tool_failure(
        key: tuple[str, str] | None,
        message: Message,
        cache: dict[tuple[str, str], Message] | None,
    ) -> None:
        if key is not None and cache is not None:
            cache[key] = message

    @staticmethod
    def _cap_tool_content_for_context(
        tool_name: str,
        content: str,
    ) -> tuple[str, dict[str, object] | None]:
        """限制高风险工具返回给下一轮 LLM 的文本量。"""

        limit = TOOL_CONTEXT_CONTENT_LIMITS.get(tool_name)
        if limit is None or len(content) <= limit:
            return content, None
        capped = content[:limit]
        if tool_name == "extract_pdf_text":
            capped = AgentRunner._rewrite_pdf_metadata_after_runtime_cap(
                capped,
                original_chars=len(content),
                limit=limit,
            )
        capped += (
            f"\n\n[Runtime] Tool output truncated before LLM context: "
            f"{limit}/{len(content)} chars shown. Use narrower parameters if more detail is needed."
        )
        return capped, {
            "original_chars": len(content),
            "shown_chars": limit,
            "reason": "tool_context_content_limit",
        }

    @staticmethod
    def _rewrite_pdf_metadata_after_runtime_cap(
        content: str,
        *,
        original_chars: int,
        limit: int,
    ) -> str:
        """Prevent capped PDF previews from still advertising complete reads."""

        if "[PDF extraction metadata]" not in content:
            return content
        replacements = {
            r"(?m)^- preview_truncated_by_max_chars: false$": "- preview_truncated_by_max_chars: true",
            r"(?m)^- complete_pdf_read: true$": "- complete_pdf_read: false",
            r"(?m)^- covers_full_pdf: true$": "- covers_full_pdf: false",
            r"(?m)^- next_start_page: none$": "- next_start_page: unknown_due_to_runtime_truncation",
        }
        rewritten = content
        for pattern, replacement in replacements.items():
            rewritten = re.sub(pattern, replacement, rewritten)
        rewritten = re.sub(
            r"(?m)^- note: .*$",
            (
                "- note: Runtime truncated this PDF preview before the LLM saw the full tool output; "
                "do not mark the note FULL-TEXT from this call. Re-read narrower page ranges until "
                "every chunk is visible and final Reading Coverage says truncation is resolved."
            ),
            rewritten,
        )
        return (
            rewritten
            + f"\n- runtime_context_truncated: true ({limit}/{original_chars} chars shown)"
        )

    async def _maybe_auto_persist_t2_search_result(
        self,
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
    ) -> dict[str, object] | None:
        """T2 中的检索结果自动落盘到 papers_raw.jsonl。"""
        if ctx.task_id != "T2" or tool_name not in T2_AUTO_PERSIST_SEARCH_TOOLS or not result.ok:
            return None

        t2_config = load_t2_finalize_config(ctx.workspace_dir)
        papers = result.data.get("papers")
        edge_persist = self._persist_t2_citation_edges_if_present(
            ctx=ctx,
            policy=policy,
            tool_name=tool_name,
            result=result,
        )
        if not isinstance(papers, list) or not papers:
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                result=result,
                paper_count=0,
                persisted_delta=0,
                merged_count=0,
                raw_count_after=self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl"),
                append_status="no_papers" if result.ok else str(result.error or "failed"),
            )
            return edge_persist

        papers = self._annotate_t2_search_bucket(
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            result=result,
            papers=papers,
        )

        save_tool = SavePapersRawTool(policy)
        save_result = await save_tool.execute(papers=papers, append=True)
        if not save_result.ok:
            raw_count_after = self._count_jsonl_records(
                ctx.workspace_dir / "literature" / "papers_raw.jsonl"
            )
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                result=result,
                paper_count=len(papers),
                persisted_delta=0,
                merged_count=0,
                raw_count_after=raw_count_after,
                append_status="raw_append_failed",
            )
            return {
                "ok": False,
                "error": save_result.error,
                "raw_count_after": raw_count_after,
                "content_suffix": f"[Runtime] 自动保存 papers_raw 失败: {save_result.content}",
            }

        raw_delta = int(save_result.data.get("count", 0) or 0)
        merged_count = int(save_result.data.get("merged_count", 0) or 0)
        retained_count = raw_delta + merged_count
        raw_count_after = self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl")
        content_suffix = (
            f"[Runtime] 已自动保留 {retained_count} 篇到 literature/papers_raw.jsonl"
            f"（新增 {raw_delta}，合并重复 {merged_count}）"
        )
        if edge_persist and edge_persist.get("content_suffix"):
            content_suffix += "\n" + str(edge_persist["content_suffix"])
        self._log_t2_search_progress(
            ctx,
            t2_config,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            result=result,
            paper_count=len(papers),
            persisted_delta=raw_delta,
            merged_count=merged_count,
            raw_count_after=raw_count_after,
            append_status="ok",
        )
        return {
            "ok": True,
            "count": raw_delta,
            "merged_count": merged_count,
            "retained_count": retained_count,
            "raw_count_after": raw_count_after,
            "mode": save_result.data.get("mode", "append"),
            "content_suffix": content_suffix,
        }

    @staticmethod
    def _log_t2_search_progress(
        ctx: ExecutionContext,
        t2_config: object,
        *,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        paper_count: int,
        persisted_delta: int,
        merged_count: int,
        raw_count_after: int | None,
        append_status: str,
    ) -> None:
        if not getattr(t2_config, "progress_enabled", True) or not getattr(
            t2_config,
            "progress_update_on_tool_results",
            True,
        ):
            return
        query = str(
            result.data.get("query")
            or tool_arguments.get("query")
            or tool_arguments.get("search_query")
            or ""
        ).strip()
        if not query:
            query = "[query unavailable]"
        try:
            progress_rel = str(getattr(t2_config, "progress_file", "") or "literature/temp/scout_progress.md")
            ScoutProgressLogger(
                ctx.workspace_dir,
                progress_rel,
            ).log_runtime_event(
                "search_result",
                query=query,
                source=tool_name,
                bucket=tool_arguments.get("query_bucket")
                or tool_arguments.get("search_bucket")
                or result.data.get("query_bucket"),
                bridge=tool_arguments.get("bridge_id") or result.data.get("bridge_id"),
                reported_paper_count=paper_count,
                persisted_raw_delta=persisted_delta,
                merged_raw_count=merged_count,
                raw_count_after=raw_count_after,
                append_status=append_status,
            )
            self.progress.progress_file_update(
                label="Scout/T2 检索进度",
                path=progress_rel,
                bullets=summarize_progress_markdown(ctx.workspace_dir / progress_rel, max_items=4),
            )
        except Exception:
            return

    @staticmethod
    def _normalized_t2_query(value: object) -> str:
        return " ".join(str(value or "").casefold().split())

    def _record_t2_search_ledger(
        self,
        *,
        ctx: ExecutionContext,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        auto_persist_metadata: dict[str, object] | None,
    ) -> None:
        """Keep compact, factual T2 search state across history truncation.

        The ledger is not a relevance judgment and never replaces
        ``papers_raw.jsonl``. It only records already completed retrieval
        operations so Scout does not restart its query plan after reading a
        large raw page.
        """

        if ctx.task_id != "T2" or tool_name not in T2_AUTO_PERSIST_SEARCH_TOOLS:
            return
        query = str(
            result.data.get("query")
            or tool_arguments.get("query")
            or tool_arguments.get("search_query")
            or ""
        ).strip()
        if not query:
            return
        ledger = ctx.extra.setdefault("t2_search_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            ctx.extra["t2_search_ledger"] = ledger
        key = (tool_name, self._normalized_t2_query(query))
        for item in ledger:
            if not isinstance(item, dict):
                continue
            if (str(item.get("tool") or ""), str(item.get("query_key") or "")) == key:
                return
        papers = result.data.get("papers")
        ledger.append(
            {
                "tool": tool_name,
                "query": query,
                "query_key": key[1],
                "bucket": str(
                    tool_arguments.get("query_bucket")
                    or tool_arguments.get("search_bucket")
                    or result.data.get("query_bucket")
                    or ""
                ).strip(),
                "bridge_id": str(tool_arguments.get("bridge_id") or result.data.get("bridge_id") or "").strip(),
                "returned": len(papers) if isinstance(papers, list) else 0,
                "persisted": int((auto_persist_metadata or {}).get("retained_count") or 0),
                "ok": bool(result.ok),
            }
        )

    @staticmethod
    def _is_t2_raw_pool_read(tool_call: ToolCall, tool_data: dict[str, object]) -> bool:
        if tool_call.name != "read_file":
            return False
        path = str(tool_data.get("path") or tool_call.arguments.get("path") or "").replace("\\", "/")
        return path.lstrip("./") == "literature/papers_raw.jsonl"

    def _hydrate_t2_search_ledger_from_raw(self, ctx: ExecutionContext) -> None:
        """Restore compact retrieval facts after a T2 resume.

        The raw JSONL is the durable source of retrieval provenance. This reads
        only structured provenance fields and never scores relevance, filters
        papers, or creates scholarly content. It lets a resumed Scout retain
        the completed query/source coverage after prior chat history has gone.
        """

        if ctx.task_id != "T2" or ctx.extra.get("t2_search_ledger_hydrated"):
            return
        ctx.extra["t2_search_ledger_hydrated"] = True
        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists():
            return
        ledger = ctx.extra.setdefault("t2_search_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            ctx.extra["t2_search_ledger"] = ledger
        known = {
            (str(item.get("tool") or ""), str(item.get("query_key") or ""))
            for item in ledger
            if isinstance(item, dict)
        }
        try:
            with raw_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
                    queries = [record.get("source_query"), provenance.get("source_query")]
                    if isinstance(record.get("source_queries"), list):
                        queries.extend(record["source_queries"])
                    tools = [record.get("source_tool"), provenance.get("source_tool")]
                    if isinstance(record.get("source_tools"), list):
                        tools.extend(record["source_tools"])
                    normalized_queries = [
                        str(query).strip()
                        for query in queries
                        if self._normalized_t2_query(query)
                    ]
                    normalized_tools = [str(tool).strip() for tool in tools if str(tool or "").strip()]
                    if not normalized_queries or not normalized_tools:
                        continue
                    bucket = str(
                        record.get("query_bucket")
                        or record.get("search_bucket")
                        or provenance.get("query_bucket")
                        or provenance.get("search_bucket")
                        or ""
                    ).strip()
                    bridge_id = str(record.get("bridge_id") or provenance.get("bridge_id") or "").strip()
                    for tool in dict.fromkeys(normalized_tools):
                        for query in dict.fromkeys(normalized_queries):
                            key = (tool, self._normalized_t2_query(query))
                            if key in known:
                                continue
                            known.add(key)
                            ledger.append(
                                {
                                    "tool": tool,
                                    "query": query,
                                    "query_key": key[1],
                                    "bucket": bucket,
                                    "bridge_id": bridge_id,
                                    "returned": 0,
                                    "persisted": 0,
                                    "ok": True,
                                    "recovered_from_raw": True,
                                }
                            )
        except OSError:
            return

    def _t2_raw_pool_checkpoint_message(
        self,
        *,
        ctx: ExecutionContext,
        tool_data: dict[str, object],
        step: int,
    ) -> Message | None:
        """Return a durable, compact resume instruction after a raw-pool page."""

        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists():
            return None
        self._hydrate_t2_search_ledger_from_raw(ctx)
        offset = int(tool_data.get("offset") or 0)
        size = int(tool_data.get("size") or raw_path.stat().st_size)
        next_offset = min(
            size,
            int(tool_data.get("next_offset") or offset + int(tool_data.get("max_chars") or 0)),
        )
        truncated = bool(tool_data.get("truncated"))
        raw_count = self._count_jsonl_records(raw_path)
        ledger = ctx.extra.get("t2_search_ledger")
        entries = [entry for entry in ledger if isinstance(entry, dict)] if isinstance(ledger, list) else []
        unique_queries = {
            str(entry.get("query_key") or "")
            for entry in entries
            if str(entry.get("query_key") or "")
        }
        sources = sorted({str(entry.get("tool") or "") for entry in entries if entry.get("tool")})
        buckets = sorted({str(entry.get("bucket") or "") for entry in entries if entry.get("bucket")})
        ctx.extra["t2_last_raw_page"] = {
            "offset": offset,
            "next_offset": next_offset,
            "size": size,
            "raw_count": raw_count,
        }
        page_note = (
            f"本页为 {offset}:{next_offset}/{size}；下一页 offset={next_offset}。"
            if truncated
            else "当前 raw 文件已完整展示。"
        )
        return Message.user(
            "[Runtime T2 检索检查点] 已完成的检索必须保留，不要重新初始化、expand_queries 或重跑已完成的来源/query。"
            f"当前 papers_raw={raw_count} 条；已完成 {len(unique_queries)} 条不同 query、"
            f"{len(entries)} 次来源检索；来源={', '.join(sources) or '未记录'}；"
            f"覆盖桶={', '.join(buckets) or '未记录'}。{page_note}"
            "请基于刚读到的 title/abstract/source_query 继续 semantic_screen；需要更多记录时只读取下一页。"
            "完成必要筛选后调用 finish_task，让 runtime 做去重、核验和队列收尾。"
            "此检查点是运行事实，不是论文相关性或最终筛选结论。",
            step=step,
        )

    def _persist_t2_citation_edges_if_present(
        self,
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        tool_name: str,
        result: ToolResult,
    ) -> dict[str, object] | None:
        """Persist raw one-hop citation edges independently of neighbor paper resolution."""

        if ctx.task_id != "T2" or tool_name != "fetch_outgoing_citations" or not result.ok:
            return None
        source_id = str(result.data.get("source_id") or "").strip()
        if not source_id:
            return None
        edges: list[list[str]] = []
        for key in ("referenced_works", "related_works"):
            values = result.data.get(key)
            if not isinstance(values, list):
                continue
            for target in values:
                target_id = str(target or "").strip()
                if target_id and target_id != source_id:
                    edges.append([source_id, target_id])
        if not edges:
            return None

        try:
            path = policy.resolve_write("literature/citation_edges.json")
            existing: list[object] = []
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        existing = loaded
                except Exception:
                    existing = []
            seen: set[tuple[str, str]] = set()
            merged: list[object] = []
            for item in [*existing, *edges]:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    merged.append(item)
                    continue
                left, right = str(item[0] or ""), str(item[1] or "")
                if not left or not right or left == right:
                    continue
                key = (left, right)
                if key in seen:
                    continue
                seen.add(key)
                merged.append([left, right])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "content_suffix": f"[Runtime] 自动保存 citation_edges 失败: {exc}",
            }
        return {
            "ok": True,
            "edge_count": len(edges),
            "content_suffix": f"[Runtime] 已自动追加 {len(edges)} 条到 literature/citation_edges.json",
        }

    @staticmethod
    def _annotate_t2_search_bucket(
        *,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        papers: list[object],
    ) -> list[object]:
        """Preserve explicit Scout query-bucket labels in raw paper records.

        The runtime does not infer academic relevance from keywords. It only
        carries labels supplied by the LLM/tool metadata as retrieval
        provenance. Domain-map and deep-read admission still require Scout
        LLM's semantic_screen.
        """

        bucket = _normalize_t2_query_bucket(
            tool_arguments.get("search_bucket")
            or tool_arguments.get("query_bucket")
            or result.data.get("search_bucket")
            or result.data.get("query_bucket")
        )
        bridge_id = str(
            tool_arguments.get("bridge_id")
            or result.data.get("bridge_id")
            or ""
        ).strip()
        query = str(tool_arguments.get("query") or result.data.get("query") or "").strip()
        if not bucket and not query and not bridge_id:
            return papers

        annotated: list[object] = []
        for paper in papers:
            if not isinstance(paper, dict):
                annotated.append(paper)
                continue
            record = dict(paper)
            if bucket and not record.get("search_bucket"):
                record["search_bucket"] = bucket
            if bucket and not record.get("query_bucket"):
                record["query_bucket"] = bucket
            if bucket and not record.get("source_bucket"):
                if bucket == "adjacent_field":
                    record["source_bucket"] = "adjacent"
                elif bucket == "theory_bridge":
                    record["source_bucket"] = "adjacent"
                elif bucket in {"core", "snowball", "seed"}:
                    record["source_bucket"] = bucket
            if bucket in {"adjacent_field", "theory_bridge"}:
                record.setdefault("cross_domain_retrieval_candidate", True)
                record.setdefault("adjacent_field", True)  # deprecated provenance alias
                record.setdefault("retrieval_intent", "cross_domain_bridge")
            elif bucket:
                record.setdefault("retrieval_intent", "primary")
            if bridge_id:
                record.setdefault("bridge_id", bridge_id)
                record.setdefault("retrieval_intent", "cross_domain_bridge")
            if query:
                record.setdefault("source_query", query)
            record.setdefault("source_tool", tool_name)
            provenance = record.get("provenance")
            if not isinstance(provenance, dict):
                provenance = {}
            provenance.setdefault("source_tool", tool_name)
            if query:
                provenance.setdefault("source_query", query)
            if bucket:
                provenance.setdefault("query_bucket", bucket)
                provenance.setdefault("search_bucket", bucket)
            if bridge_id:
                provenance.setdefault("bridge_id", bridge_id)
            record["provenance"] = provenance
            annotated.append(record)
        return annotated

    def _parse_llm_response(self, resp: object, *, step: int) -> Message:
        choice = resp.raw.choices[0].message
        content = getattr(choice, "content", None) or None
        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(choice, "tool_calls", None) or []
        for tool_call in raw_tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {
                    "__raw__": tool_call.function.arguments,
                    "__parse_error__": True,
                }
            tool_calls.append(
                ToolCall(id=tool_call.id, name=tool_call.function.name, arguments=arguments)
            )
        # 某些 OpenAI-compatible provider 会把工具调用吐成文本片段而不是原生 tool_calls。
        # 这里做一次兜底解析，尽量把 DSML 风格的伪调用恢复成真实 ToolCall。
        if not tool_calls and content:
            recovered_content, recovered_calls = self._recover_textual_tool_calls(content)
            if recovered_calls:
                content = recovered_content
                tool_calls = recovered_calls
        return Message.assistant(content=content, tool_calls=tool_calls, step=step)

    def _recover_textual_tool_calls(self, content: str) -> tuple[str | None, list[ToolCall]]:
        """从文本中恢复 DSML 风格的伪工具调用。"""
        invoke_re = re.compile(
            r"<[^>\n]*invoke\s+name=\"(?P<name>[^\"]+)\"[^>]*>(?P<body>.*?)</[^>\n]*invoke>",
            re.DOTALL,
        )
        param_re = re.compile(
            r"<[^>\n]*parameter\s+name=\"(?P<name>[^\"]+)\"[^>]*>(?P<value>.*?)</[^>\n]*parameter>",
            re.DOTALL,
        )

        tool_calls: list[ToolCall] = []
        for match in invoke_re.finditer(content):
            arguments: dict[str, object] = {}
            for param_match in param_re.finditer(match.group("body")):
                key = param_match.group("name").strip()
                value = self._coerce_textual_tool_value(param_match.group("value"))
                arguments[key] = value
            tool_calls.append(ToolCall.create(match.group("name").strip(), arguments))

        if not tool_calls:
            return content, []

        cleaned = invoke_re.sub("", content)
        cleaned = re.sub(r"<[^>\n]*tool_calls[^>]*>|</[^>\n]*tool_calls>", "", cleaned)
        cleaned = re.sub(r"<[^>\n]*minimax:tool_call[^>]*>|</[^>\n]*minimax:tool_call>", "", cleaned)
        cleaned = cleaned.strip() or None
        return cleaned, tool_calls

    def _coerce_textual_tool_value(self, raw_value: str) -> object:
        """尽量把文本参数恢复成工具 schema 更容易接受的类型。"""
        value = raw_value.strip()
        if not value:
            return ""
        if value.startswith("{") or value.startswith("["):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value

    def _maybe_truncate(self, messages: list[Message], binding: ModelBinding) -> list[Message]:
        """按 message group 粒度做上下文裁剪。"""
        config = self.llm.get_truncation_config()
        limit = self.llm.get_context_window(binding)
        trigger = int(limit * config.get("trigger_ratio", 0.8))
        target = int(limit * config.get("target_ratio", 0.6))
        current = self.llm.count_tokens([m.to_openai_dict() for m in messages], binding)
        if current <= trigger:
            return messages

        groups = self._split_into_groups(messages)
        kept = [groups[0]]
        total = self._count_group_tokens(groups[0], binding)
        for group in reversed(groups[1:]):
            group_tokens = self._count_group_tokens(group, binding)
            if total + group_tokens > target:
                break
            kept.insert(1, group)
            total += group_tokens

        omitted = len(groups) - len(kept)
        if omitted <= 0:
            return messages

        note = Message.user(
            f"[Runtime] 由于上下文过长，已省略较早的 {omitted} 轮交互。如需回忆先前信息，请读取相关 artifact。",
            step=messages[-1].step,
        )
        flattened: list[Message] = []
        flattened.extend(kept[0])
        flattened.append(note)
        for group in kept[1:]:
            flattened.extend(group)
        return flattened

    def _repair_openai_tool_message_sequence(self, messages: list[Message]) -> list[Message]:
        """Ensure assistant tool_calls are immediately followed by tool messages.

        OpenAI-compatible providers reject histories where an assistant message
        declares tool_calls but any corresponding tool result is missing. This
        can happen after cancellation, gate auto-bridging, manual trace repair,
        or future truncation changes. We repair at the provider boundary instead
        of letting one malformed history make every fallback model fail.
        """

        repaired: list[Message] = []
        changed = False
        idx = 0
        while idx < len(messages):
            message = messages[idx]
            if message.role == Role.TOOL:
                changed = True
                preview = (message.content or "").strip()
                if len(preview) > 500:
                    preview = preview[:497] + "..."
                repaired.append(
                    Message.user(
                        "[Runtime] Omitted an orphan tool result from the provider message history "
                        "because it was not immediately attached to an assistant tool_call. "
                        f"tool={message.name or 'unknown_tool'} tool_call_id={message.tool_call_id or ''}. "
                        f"Preview: {preview}",
                        step=message.step,
                    )
                )
                idx += 1
                continue
            repaired.append(message)
            idx += 1
            if message.role != Role.ASSISTANT or not message.tool_calls:
                continue

            expected_ids = [tool_call.id for tool_call in message.tool_calls]
            seen_ids: set[str] = set()
            while idx < len(messages) and messages[idx].role == Role.TOOL:
                tool_message = messages[idx]
                if tool_message.tool_call_id:
                    seen_ids.add(tool_message.tool_call_id)
                repaired.append(tool_message)
                idx += 1

            missing_ids = [tool_call_id for tool_call_id in expected_ids if tool_call_id not in seen_ids]
            if not missing_ids:
                continue
            changed = True
            name_by_id = {tool_call.id: tool_call.name for tool_call in message.tool_calls}
            for tool_call_id in missing_ids:
                repaired.append(
                    Message.tool(
                        tool_call_id=tool_call_id,
                        name=name_by_id.get(tool_call_id, "unknown_tool"),
                        content=(
                            "ERROR: tool result was unavailable because the previous ResearchOS "
                            "turn was interrupted or repaired before the tool response was recorded. "
                            "Do not assume this tool succeeded; inspect persisted artifacts or call "
                            "the tool again if needed."
                        ),
                        is_error=True,
                        step=message.step,
                        metadata={
                            "error": "missing_tool_result_repaired",
                            "data": {"runtime_repaired": True},
                        },
                    )
                )

        if changed:
            self.log.warning("repaired_missing_tool_messages_before_llm")
        return repaired

    def _split_into_groups(self, messages: list[Message]) -> list[list[Message]]:
        """把消息拆成“assistant + tool results”为一组的逻辑轮次。"""
        if not messages:
            return []
        groups: list[list[Message]] = []
        first = [messages[0]]
        idx = 1
        if idx < len(messages) and messages[idx].role == Role.USER:
            first.append(messages[idx])
            idx += 1
        groups.append(first)

        while idx < len(messages):
            message = messages[idx]
            if message.role == Role.ASSISTANT:
                group = [message]
                idx += 1
                while idx < len(messages) and messages[idx].role == Role.TOOL:
                    group.append(messages[idx])
                    idx += 1
                groups.append(group)
                continue
            groups.append([message])
            idx += 1
        return groups

    def _count_group_tokens(self, group: list[Message], binding: ModelBinding) -> int:
        return self.llm.count_tokens([message.to_openai_dict() for message in group], binding)

    def _build_result(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        stop_reason: str,
        error_msg: str | None,
        started: float,
        trace_file: Path,
        eff: EffectiveConfig,
        last_model_used: str | None,
        last_endpoint_used: str | None,
    ) -> AgentResult:
        outputs = {name: path for name, path in ctx.outputs_expected.items() if path.exists()}
        ok = stop_reason == AgentResult.STOP_FINISHED
        if last_model_used is None:
            last_model_used = str(ctx.extra.get("t4_evolution_last_model") or "") or None
        if last_endpoint_used is None:
            last_endpoint_used = str(ctx.extra.get("t4_evolution_last_endpoint") or "") or None
        metadata: dict[str, object] = {}
        if ctx.extra.get("completion_mode"):
            metadata["completion_mode"] = ctx.extra.get("completion_mode")
        if isinstance(ctx.extra.get("runtime_actions"), list):
            metadata["runtime_actions"] = ctx.extra.get("runtime_actions")
        message = {
            AgentResult.STOP_FINISHED: "Agent 成功完成",
            AgentResult.STOP_MAX_STEPS: "达到最大步数",
            AgentResult.STOP_BUDGET: "超出预算",
            AgentResult.STOP_ERROR: f"错误: {error_msg or 'unknown'}",
            AgentResult.STOP_INTERRUPTED: "被中断",
            AgentResult.STOP_HUMAN_REJECT: "被用户拒绝",
        }[stop_reason]
        if ok and metadata.get("completion_mode") == "t2_finish_finalize":
            message = "Agent 成功完成（T2 finish_task 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t2_recovery":
            message = "Agent 成功完成（T2 recovery 自动补全）"
        elif ok and metadata.get("completion_mode") == "t2_resume_prefinalize":
            message = "Agent 成功完成（T2 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t3_resume_prefinalize":
            message = "Agent 成功完成（T3 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t36_visuals_resume_prefinalize":
            message = "Agent 成功完成（T3.6 taxonomy visual 已验证，跳过重复生成）"
        elif ok and metadata.get("completion_mode") == "t36_compile_resume_prefinalize":
            message = "Agent 成功完成（T3.6 survey PDF 已验证，跳过重复编译）"
        elif ok and metadata.get("completion_mode") == "t4_resume_prefinalize":
            message = "Agent 成功完成（T4 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t4_gate1_ready":
            message = "Agent 成功完成（T4 Gate1 候选池已就绪）"
        elif ok and metadata.get("completion_mode") == "t4_pre_novelty_ready":
            message = "Agent 成功完成（已生成 Pre-Novelty brief，进入 T4.5）"
        elif ok and metadata.get("completion_mode") == "t45_resume_prefinalize":
            message = "Agent 成功完成（T4.5 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t8_section_plan_prefinalize":
            message = "Agent 成功完成（T8 section-plan 确定性修复/收尾）"
        elif ok and metadata.get("completion_mode") == "t9_submission_prefinalize":
            message = "Agent 成功完成（T9 已有投稿包确定性收尾）"
        return AgentResult(
            ok=ok,
            message=message,
            outputs_produced=outputs,
            steps_used=budget.steps,
            tokens_in=budget.tokens_in,
            tokens_out=budget.tokens_out,
            cost_usd=budget.cost_usd,
            duration_seconds=time.time() - started,
            stop_reason=stop_reason,
            error=error_msg,
            trace_file=trace_file,
            llm_profile=eff.llm_profile,
            llm_tier=eff.llm_tier,
            llm_model_used=last_model_used,
            llm_endpoint_used=last_endpoint_used,
            metadata=metadata,
        )

    async def _maybe_offer_budget_extension(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        exc: BudgetExceeded,
        used_extensions: int,
    ) -> tuple[bool, int]:
        """在预算触顶时给长任务一个人工扩限机会。"""

        policy = self.budget_escalation_policy or {}
        if not policy.get("enabled", False):
            return False, used_extensions

        enabled_tasks = set(policy.get("tasks") or [])
        if enabled_tasks and ctx.task_id not in enabled_tasks:
            return False, used_extensions

        raw_max_extensions = policy.get("max_extensions_per_run")
        # `null` / 缺省 / 负数 表示“不设上限”，但每次都仍然要经过人工 gate 确认。
        if raw_max_extensions is None:
            max_extensions = None
        else:
            max_extensions = int(raw_max_extensions)
            if max_extensions < 0:
                max_extensions = None
        if max_extensions is not None and used_extensions >= max_extensions:
            return False, used_extensions

        steps_ratio = float(policy.get("steps_increase_ratio", 0.25) or 0.25)
        token_ratio = float(policy.get("token_increase_ratio", 0.5) or 0.5)
        wall_ratio = float(policy.get("wall_seconds_increase_ratio", 0.5) or 0.5)

        if exc.dimension == "steps":
            delta = max(20, int(budget.max_steps * steps_ratio))
        elif exc.dimension == "tokens":
            delta = max(100000, int(budget.max_tokens * token_ratio))
        elif exc.dimension == "wall_seconds":
            delta = max(600, int(budget.max_wall_seconds * wall_ratio))
        else:
            return False, used_extensions

        unit = {
            "steps": "steps",
            "tokens": "tokens",
            "wall_seconds": "seconds",
        }[exc.dimension]
        snapshot = budget.snapshot()
        # 把当前已落盘的关键输出一起展示出来，方便用户判断“现在停会损失什么”。
        existing_outputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in ctx.outputs_expected.values()
            if path.exists()
        ]
        human_started = time.time()
        try:
            result = await self.human.present_gate(
                gate_id="runtime_budget_extension",
                presentation={
                    "_title": "预算上限已触发",
                    "_description": "当前任务已达到预算上限。你可以选择扩限后继续，或停止本次运行。",
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "extensions_used": used_extensions,
                    "dimension": exc.dimension,
                    "used": exc.used,
                    "limit": exc.limit,
                    "current_budget": snapshot,
                    "existing_outputs": existing_outputs,
                    "suggested_extension": {
                        "dimension": exc.dimension,
                        "delta": delta,
                        "new_limit": int(exc.limit + delta),
                        "unit": unit,
                    },
                },
                options=[
                    {
                        "id": "extend",
                        "label": f"继续，并增加 {delta} {unit}",
                    },
                    {
                        "id": "stop",
                        "label": "停止本次运行",
                    },
                ],
            )
        except HumanInputUnavailable as exc:
            raise RecoverableRuntimePause(str(exc)) from exc
        finally:
            budget.exclude_wall_time(time.time() - human_started)
        if (result or {}).get("option_id") != "extend":
            return False, used_extensions

        # 只扩当前触顶维度，避免一次确认后无节制地放大全部预算。
        budget.extend_limit(exc.dimension, delta)
        self.log.info(
            "budget_extended",
            task_id=ctx.task_id,
            dimension=exc.dimension,
            delta=delta,
            old_limit=exc.limit,
            new_limit=exc.limit + delta,
        )
        return True, used_extensions + 1

    @staticmethod
    def _record_validation_failure(ctx: ExecutionContext, error: str) -> int:
        """Track the consecutive identical validator error for circuit breaking."""

        normalized = " ".join(str(error or "unknown validation error").split()).casefold()
        previous = str(ctx.extra.get("last_validation_error") or "").casefold()
        count = int(ctx.extra.get("same_validation_error_count") or 0)
        count = count + 1 if normalized == previous else 1
        ctx.extra["last_validation_error"] = normalized
        ctx.extra["same_validation_error_count"] = count
        return count

    @staticmethod
    def _validation_repair_feedback(
        *,
        ctx: ExecutionContext,
        error: str,
        resumed_after_extension: bool = False,
    ) -> str:
        """Give the LLM an artifact-specific repair contract, not vague retry prose."""

        prefix = (
            "已获准继续校验修复。"
            if resumed_after_extension
            else "输出校验未通过。"
        )
        base = (
            f"{prefix} 最后错误：{error}\n"
            "只修复该错误涉及的最小 artifact，保留其它已合格字段；修复后再次调用 finish_task。"
        )
        if ctx.task_id != "T4":
            return base

        idea_match = re.search(r"idea\s+([A-Za-z][A-Za-z0-9_-]*)", error)
        idea_id = idea_match.group(1) if idea_match else "对应 idea"
        if "idea_scorecard.yaml" in error:
            details = (
                "读取 `ideation/idea_scorecard.yaml`，定位 `ideas[]` 中 "
                f"`idea.id == \"{idea_id}\"` 的记录。"
            )
            if "design_rationale" in error:
                details += (
                    "在 `idea.cdr_tuple.design_rationale` 写出模型根据该候选问题、机制、"
                    "跨论文观察所得的设计理由：解释 artifact 为什么必须采取当前结构，"
                    "不是复述实现步骤。"
                )
            elif "contribution_type" in error or "contribution_character" in error or "contribution_strength" in error:
                details += (
                    "补全该选中 idea 的 `cdr_tuple.contribution_type`、"
                    "`selection_rationale.contribution_character`（或 `idea.contribution_character`）"
                    "及 `idea.contribution_strength`，并让三者与当前 mechanism 和 design_rationale 一致。"
                )
            else:
                details += "按报错字段补全该 idea，同时保留完整 CDR、评分和 decision 记录。"
            return (
                base
                + details
                + "使用 `write_structured_file(path=\"ideation/idea_scorecard.yaml\", "
                "schema_name=\"idea_scorecard\", format=\"yaml\", data=...)` 重写通过 schema 的完整对象。"
                "研究性文字必须由你依据已落盘证据归纳；不要让确定性工具代写假设、机制、设计理由或评分依据。"
            )
        structured_targets = {
            "idea_rationales.json": ("ideation/idea_rationales.json", "idea_rationales", "json"),
            "exp_plan.yaml": ("ideation/exp_plan.yaml", "exp_plan", "yaml"),
            "gate_decisions.json": ("ideation/gate_decisions.json", "gate_decisions", "json"),
        }
        for marker, (path, schema, fmt) in structured_targets.items():
            if marker in error:
                return (
                    base
                    + f"读取 `{path}`，只补报错指出的字段，然后使用 "
                    f"`write_structured_file(path=\"{path}\", schema_name=\"{schema}\", format=\"{fmt}\", data=...)`。"
                    "不要改写无关的已合格 artifact。"
                )
        if "_candidate_directions.json" in error:
            return (
                base
                + "读取 `ideation/_candidate_directions.json` 并修复报错候选的模型归纳字段。"
                "每个 Gate1 candidate 必须保留 2-3 条不同的可证伪假设、`basis_sources` 的 claim/implication、"
                "CDR 设计理由和互不重复的七维评分理由；使用 `write_file` 写回完整 JSON 对象。"
                "不要让 runtime 展示层补写研究内容。"
            )
        return (
            base
            + "T4 的研究字段必须由你根据已有候选、论文笔记和 scorecard 归纳修复；"
            "先读取报错文件，确认 schema 和字段路径，再写回最小完整修复。"
        )

    async def _maybe_offer_validation_retry_extension(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        last_error: str,
        failures: int,
        retry_limit: int,
        used_extensions: int,
    ) -> tuple[bool, int, int]:
        """Offer a recoverable gate before pausing on validation retry exhaustion."""

        policy = self.budget_escalation_policy or {}
        if not policy.get("enabled", False):
            return False, retry_limit, used_extensions

        enabled_tasks = set(policy.get("tasks") or [])
        if enabled_tasks and ctx.task_id not in enabled_tasks:
            return False, retry_limit, used_extensions

        raw_max_extensions = policy.get("max_validation_extensions_per_run")
        if raw_max_extensions is None:
            raw_max_extensions = policy.get("max_extensions_per_run")
        if raw_max_extensions is None:
            max_extensions = None
        else:
            max_extensions = int(raw_max_extensions)
            if max_extensions < 0:
                max_extensions = None
        if max_extensions is not None and used_extensions >= max_extensions:
            return False, retry_limit, used_extensions

        delta = max(1, int(policy.get("validation_retry_increase", 2) or 2))
        existing_outputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in ctx.outputs_expected.values()
            if path.exists()
        ]
        human_started = time.time()
        try:
            result = await self.human.present_gate(
                gate_id="runtime_validation_retry_extension",
                presentation={
                    "_title": "输出校验仍未通过",
                    "_description": (
                        "当前任务已耗尽自动修复轮次。你可以增加少量校验修复轮次继续，"
                        "或暂停后人工检查 artifact 再 resume。"
                    ),
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "failures": failures,
                    "retry_limit": retry_limit,
                    "last_error": last_error,
                    "existing_outputs": existing_outputs,
                    "suggested_extension": {
                        "validation_retry_delta": delta,
                        "new_retry_limit": retry_limit + delta,
                    },
                },
                options=[
                    {
                        "id": "extend",
                        "label": f"继续修复，并增加 {delta} 次校验机会",
                    },
                    {
                        "id": "stop",
                        "label": "暂停，稍后 resume",
                    },
                ],
            )
        except HumanInputUnavailable:
            return False, retry_limit, used_extensions
        finally:
            budget.exclude_wall_time(time.time() - human_started)

        if (result or {}).get("option_id") != "extend":
            return False, retry_limit, used_extensions

        new_limit = retry_limit + delta
        self.log.info(
            "validation_retry_limit_extended",
            task_id=ctx.task_id,
            failures=failures,
            old_limit=retry_limit,
            new_limit=new_limit,
        )
        return True, new_limit, used_extensions + 1
