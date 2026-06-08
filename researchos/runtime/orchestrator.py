from __future__ import annotations

"""AgentRunner 主循环。"""

import asyncio
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from ..pydantic_compat import model_dump
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
from .t2_recovery import finalize_t2_outputs
from .abstract_sweep import run_abstract_sweep, run_abstract_sweep_with_reader
from .t2_config import load_t2_finalize_config
from .t3_recovery import prepare_t3_resume_artifacts
from .task_recovery import prepare_generic_resume_artifacts
from .run_logger import RunLogger
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

    @staticmethod
    def _default_policy_factory(
        ctx: ExecutionContext, eff: EffectiveConfig
    ) -> "WorkspaceAccessPolicy":
        from ..tools.workspace_policy import WorkspaceAccessPolicy

        return WorkspaceAccessPolicy(
            workspace_dir=ctx.workspace_dir,
            allowed_read_prefixes=eff.allowed_read_prefixes,
            allowed_write_prefixes=eff.allowed_write_prefixes,
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

    async def run(self, ctx: ExecutionContext) -> AgentResult:
        """执行一次完整 agent run。"""
        started = time.time()
        eff = resolve_effective_config(self.agent.spec, ctx)
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
        last_model_used: str | None = None
        last_endpoint_used: str | None = None
        stop_reason = AgentResult.STOP_ERROR
        error_msg: str | None = None

        policy = self.workspace_policy_factory(ctx, eff)
        build_ctx = ToolBuildContext(
            policy=policy,
            human=self.human,
            skill_dir=Path(ctx.extra["skill_dir"]) if "skill_dir" in ctx.extra else None,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
        )
        tool_map = self.tool_registry.build(eff.tool_names, build_ctx)
        tool_schemas = self.tool_registry.to_openai_schemas(tool_map)

        sys_msg = Message.system(self.agent.system_prompt(ctx), step=0)
        user_msg = Message.user(self.agent.initial_user_message(ctx), step=0)
        messages: list[Message] = [sys_msg, user_msg]
        trace.write_message(sys_msg)
        trace.write_message(user_msg)

        primary_binding = self.llm.resolve(
            profile=eff.llm_profile,
            tier=eff.llm_tier,
            model_override=eff.llm_model_override,
            endpoint_override=eff.llm_endpoint_override,
            max_context_override=eff.llm_max_context_override,
        )[0][0]

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
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized):
                t4_pre_finalized = await self._maybe_finalize_t4_before_llm(ctx)
            t45_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized or t4_pre_finalized):
                t45_pre_finalized = await self._maybe_finalize_t45_before_llm(ctx)
            external_wait_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized or t4_pre_finalized or t45_pre_finalized):
                external_wait_pre_finalized = await self._maybe_finalize_external_wait_before_llm(ctx)
            paper_claim_audit_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t4_pre_finalized
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
                or t4_pre_finalized
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
                or t4_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
                or t8_section_plan_pre_finalized
            ):
                t8_manuscript_pre_finalized = await self._maybe_finalize_t8_manuscript_before_llm(ctx)
            deterministic_pre_finalized = deterministic_pre_finalized or (
                    t2_pre_finalized
                    or t3_pre_finalized
                    or t4_pre_finalized
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
                run_logger.event(
                    "AGENT_STEP",
                    task=ctx.task_id,
                    step=budget.steps,
                    tokens=budget.tokens_in + budget.tokens_out,
                    cost_usd=f"{budget.cost_usd:.4f}",
                )

                # 每5步输出一次进度
                if budget.steps % 5 == 1 or budget.steps == 1:
                    step_limit = "unlimited" if budget.unlimited_budget else str(budget.max_steps)
                    self._emit(
                        f"[Agent] 步骤 {budget.steps}/{step_limit} | Token: {budget.tokens_in + budget.tokens_out} | 成本: ${budget.cost_usd:.4f}",
                        verbose_only=True,
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

                try:
                    run_logger.event(
                        "LLM_CALL",
                        task=ctx.task_id,
                        step=budget.steps,
                        tier=eff.llm_tier,
                        profile=eff.llm_profile,
                        tool_count=len(tool_schemas or []),
                    )
                    llm_resp = await self.llm.chat(
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
                    run_logger.event(
                        "ERROR",
                        task=ctx.task_id,
                        step=budget.steps,
                        kind="llm_provider",
                        message=str(exc)[:300],
                    )
                    if self._is_timeout_provider_error(exc):
                        cooldown_raw = self.retry_policy.get("llm_timeout_cooldown_seconds")
                        cooldown = 60.0 if cooldown_raw is None else float(cooldown_raw)
                        pause_after = int(self.retry_policy.get("llm_timeout_pause_after_cooldowns") or 0)
                        llm_timeout_cooldowns_used += 1
                        if pause_after > 0 and llm_timeout_cooldowns_used > pause_after:
                            raise RecoverableRuntimePause(
                                "LLM provider 连续超时，已暂停等待人工处理或稍后 resume；"
                                f"最近错误: {exc}"
                            ) from exc
                        self._emit(
                            "[Agent] LLM provider 连续超时，"
                            f"冷却 {cooldown:g}s 后继续尝试（第 {llm_timeout_cooldowns_used} 轮）",
                            important=True,
                        )
                        if cooldown > 0:
                            await asyncio.sleep(cooldown)
                        continue
                    stop_reason = AgentResult.STOP_ERROR
                    error_msg = f"LLM failed: {exc}"
                    break

                last_model_used = llm_resp.model_used
                last_endpoint_used = llm_resp.endpoint_used
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
                trace.write_message(assistant_msg)

                # 输出 Agent 的文本回复（如果有）。普通状态说明默认只在 verbose 显示；
                # 但同一轮如果要 ask_human，正文通常包含用户必须看到的草案、
                # 候选清单或决策上下文，不能被简洁模式吞掉。
                if assistant_msg.content and assistant_msg.content.strip():
                    self._emit(
                        f"\n[Agent 输出]\n{assistant_msg.content}\n",
                        verbose_only=not any(tc.name == "ask_human" for tc in assistant_msg.tool_calls),
                    )

                # 如果模型在文本里向用户提问/要求选择，但没有显式调用 ask_human，
                # runtime 必须先等待人类输入。即便同一轮还混有 read/write 等工具，
                # 也不能继续执行那些工具，否则会复现“模型问了但没有输入框仍继续跑”的问题。
                if self._looks_like_human_interaction_request(assistant_msg) and not any(
                    tc.name == "ask_human" for tc in assistant_msg.tool_calls
                ):
                    if "ask_human" not in tool_map:
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
                    bridge_note = Message.user(
                        "[Runtime] 检测到 Agent 向用户提问/要求选择但未调用 ask_human，"
                        "已自动转成 ask_human，并阻止本轮其它工具继续执行；如果输入不可用将暂停等待 resume。",
                        step=budget.steps,
                    )
                    messages.append(bridge_note)
                    trace.write_message(bridge_note)

                # 如果模型只说话不调用工具，runtime 会反复提醒它：
                # 要么继续推进，要么明确 finish_task。
                if not assistant_msg.tool_calls:
                    if not self._looks_like_human_interaction_request(assistant_msg):
                        nudge_count += 1
                        if nudge_count > self.runtime_settings.agent_behavior.max_nudge_finish:
                            stop_reason = AgentResult.STOP_ERROR
                            error_msg = "agent 多次只输出文本但未调用工具"
                            break
                        nudge = Message.user(
                            "你没有调用任何工具。如果任务已完成，请调用 finish_task；否则请继续调用适当工具。",
                            step=budget.steps,
                        )
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
                        human_barrier_note = Message.user(
                            "[Runtime] 本轮包含 ask_human，已先等待用户输入；"
                            f"延后执行同轮其它工具: {', '.join(blocked_tools)}。",
                            step=budget.steps,
                        )
                        messages.append(human_barrier_note)
                        trace.write_message(human_barrier_note)
                # 输出工具调用信息
                if len(assistant_msg.tool_calls) > 0:
                    tool_names = [tc.name for tc in assistant_msg.tool_calls]
                    self._emit(f"[Agent] 调用工具: {', '.join(tool_names)}")
                    for tc in assistant_msg.tool_calls:
                        run_logger.tool_call(tc.name, tc.arguments, step=budget.steps)

                # 同一轮 assistant 发出的多个 tool call 可以并行执行，但回填顺序保持原顺序。
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
                    if tool_call.name == "finish_task" and not tool_msg.metadata.get("is_error"):
                        finish_requested = True
                    if self._is_recoverable_tool_pause(tool_call.name, tool_msg):
                        pause_requested = True
                        pause_reason = tool_msg.content or "需要用户输入，但当前输入不可用。"

                if pause_requested:
                    stop_reason = AgentResult.STOP_INTERRUPTED
                    error_msg = pause_reason
                    self._emit(f"[Agent] 任务暂停：{pause_reason}", important=True)
                    break

                if finish_requested:
                    # finish_task 只是“请求结束”而不是直接结束。
                    # 真正能否成功结束，仍以 validate_outputs 为准。
                    self._emit("[Agent] Agent 请求完成任务，开始校验输出...")
                    run_logger.event("FINISH_REQUESTED", task=ctx.task_id, step=budget.steps)
                    if ctx.task_id == "T2":
                        run_logger.event("FINALIZE_STARTED", task=ctx.task_id, mode="t2_finish_finalize")
                        await self._finalize_t2_from_raw(
                            ctx,
                            mode="t2_finish_finalize",
                            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
                            start_message="[Agent] T2 收到 finish_task，先基于 papers_raw 执行确定性收尾...",
                            success_message="[Agent] T2 确定性收尾成功，继续校验输出",
                        )
                        run_logger.event("FINALIZE_DONE", task=ctx.task_id, mode="t2_finish_finalize")
                    ok, err = self.agent.validate_outputs(ctx)
                    if ok:
                        self._emit("[Agent] 输出校验通过，任务完成")
                        run_logger.event("VALIDATION_PASS", task=ctx.task_id, step=budget.steps)
                        stop_reason = AgentResult.STOP_FINISHED
                        break
                    validation_fails += 1
                    self._emit(
                        f"[Agent] 输出校验失败 ({validation_fails}/{validation_retry_limit}): {err}",
                        important=True,
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
                            run_logger.event(
                                "VALIDATION_RETRY",
                                task=ctx.task_id,
                                step=budget.steps,
                                failure=validation_fails,
                                new_limit=validation_retry_limit,
                            )
                            feedback = Message.user(
                                "用户选择继续修复输出校验问题。请只针对最后一次校验错误做最小修复，"
                                "优先调用确定性工具或读取现有 artifact，不要重写已合格的大文件。"
                                f"最后一次错误：{err}",
                                step=budget.steps,
                            )
                            messages.append(feedback)
                            trace.write_message(feedback)
                            continue
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = (
                            f"Validation failed {validation_fails} times. "
                            f"Paused for artifact repair/resume. Last reason: {err}"
                        )
                        break
                    feedback = Message.user(
                        f"你声称已完成，但输出校验失败：{err}。请修复后再次调用 finish_task。",
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
        step_limit = "unlimited" if eff.unlimited_budget else str(eff.max_steps)
        separator = self._centered_separator(f"{ctx.task_id} | {self.agent.spec.name}", width=80)
        self._emit(
            f"\n{separator}\n"
            "[Agent] 初始化完成 | "
            f"任务: {ctx.task_id} | Agent: {self.agent.spec.name} | 阶段: {phase} | "
            f"目标: {description} | 输出: {', '.join(expected) if expected else '未声明'} | "
            f"模型层级: {eff.llm_tier} | 最大步数: {step_limit}\n"
            f"{'=' * len(separator)}"
        )

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
        print(message, flush=True)

    @staticmethod
    def _infer_task_description(ctx: ExecutionContext) -> str:
        task_map = {
            "T1": "初始化项目配置和 workspace 状态",
            "T2": "检索、去重并验证候选论文",
            "T3": "精读论文并生成结构化 paper notes",
            "T3.5": "基于 notes 分阶段合成 literature synthesis",
            "T4": "生成候选研究假设、实验计划和风险分析",
            "T4.5": "做新颖性预审和 mechanism tuple 审计",
            "T5-HANDOFF": "编译外部实验协议、执行器选择和 handoff prompt",
            "T5-EXECUTOR-GATE": "由用户选择 mock、Claude Code、Codex CLI 或人工外部执行器",
            "T5-EXTERNAL-WAIT": "等待外部执行器写回 result_pack 并在 resume 时校验",
            "T5-DRY-RUN": "跑通 mock 外部执行器文件协议，不执行真实实验",
            "T7-INGEST": "摄取外部 result pack 并规范化结果证据",
            "T7-AUDIT": "审计实验 provenance、hash、mock 标记和指标来源",
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

        print("[Agent] T1 启动补充 gate：等待用户确认是否补充材料后再扫描 user_seeds/", flush=True)
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
            start_message="[Agent] T2 resume/recovery 检测到未完成输出，尝试基于 papers_raw 补齐...",
            success_message="[Agent] T2 resume/recovery 补齐成功，已恢复完整 T2 产物",
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
            mode_params = get_agent_mode_params("reader", "read")
            sweep_config = mode_params.get("abstract_sweep", {})
            if not sweep_config.get("enabled", False):
                return

            if stop_reason == AgentResult.STOP_FINISHED:
                print("[Agent] T3 deep read 完成，开始 abstract sweep...", flush=True)
            else:
                print(
                    f"[Agent] T3 以 {stop_reason} 退出，使用 deterministic abstract sweep 刷新浅层笔记覆盖...",
                    flush=True,
                )

            if stop_reason != AgentResult.STOP_FINISHED:
                result = run_abstract_sweep(ctx.workspace_dir, sweep_config)
                ctx.extra["abstract_sweep"] = result
                if result.get("notes_generated", 0) > 0:
                    print(
                        f"[Agent] Abstract sweep fallback 完成：筛选 {result['candidates_found']} 篇候选，"
                        f"生成 {result['notes_generated']} 篇 abstract note",
                        flush=True,
                    )
                return

            async def _reader_llm(_paper: dict[str, object], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are ResearchOS Reader. Produce cautious abstract-only "
                                "paper notes in the exact requested Markdown structure."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
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
                metadata_triage_reader=_metadata_triage_llm,
            )
            ctx.extra["abstract_sweep"] = result

            if result.get("notes_generated", 0) > 0 or result.get("metadata_triage_count", 0) > 0:
                print(
                    f"[Agent] Abstract sweep 完成：筛选 {result['candidates_found']} 篇候选，"
                    f"生成 {result['notes_generated']} 篇 abstract note "
                    f"（LLM {result.get('llm_notes_generated', 0)}，fallback {result.get('fallback_notes_generated', 0)}），"
                    f"metadata-only 批量 triage {result.get('metadata_triage_count', 0)} 篇",
                    flush=True,
                )
            else:
                print("[Agent] Abstract sweep 无候选论文", flush=True)
        except Exception:  # pragma: no cover - sweep failure should not fail a completed T3
            self.log.exception("t3_abstract_sweep_failed")

    async def _maybe_finalize_t2_before_llm(self, ctx: ExecutionContext) -> bool:
        """T2 续跑时，只有已足够完整的产物或显式恢复场景才跳过 LLM。

        冷启动后第一轮检索可能已经因为多源工具返回大量 raw，但这不等于
        Scout 的检索覆盖规划已经完成。因此这里不能只看 raw_count 自动结束。
        """

        if ctx.task_id != "T2":
            return False

        if ctx.outputs_expected and all(path.exists() for path in ctx.outputs_expected.values()):
            ok, _err = self.agent.validate_outputs(ctx)
            if ok:
                self._record_runtime_completion(
                    ctx,
                    "t2_existing_outputs_prefinalize",
                    {"raw_count": self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl")},
                )
                print("[Agent] T2 检测到已有完整产物且校验通过，跳过 LLM 续跑", flush=True)
                return True

        if not self._is_resume_run(ctx):
            return False

        return await self._finalize_t2_from_raw(
            ctx,
            mode="t2_resume_prefinalize",
            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
            start_message="[Agent] T2 resume 检测到已有 papers_raw，尝试确定性补齐缺失产物...",
            success_message="[Agent] T2 resume 确定性补齐成功，跳过 LLM 续跑",
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
            ctx.workspace_dir / "literature" / "paper_notes",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
            ctx.workspace_dir / "literature" / "related_work.bib",
        ]
        if any(not path.exists() for path in expected_paths):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t3_resume_prefinalize_skipped", reason=err)
            return False

        print("[Agent] T3 检测到已有 deep-read 产物且校验通过，跳过 LLM 续跑", flush=True)
        ctx.extra["skip_t3_abstract_sweep"] = True
        self._record_runtime_completion(
            ctx,
            "t3_resume_prefinalize",
            {
                "outputs": [
                    "literature/paper_notes",
                    "literature/comparison_table.csv",
                    "literature/related_work.bib",
                ],
            },
            action_type="t3_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t4_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4 续跑时，已有三件套可通过校验则直接完成。

        T4 的核心产物都是 workspace artifact。若它们已经存在并满足
        IdeationAgent.validate_outputs 的 schema、anchor、风险和预算约束，
        runtime 不再把“是否复用旧产物”交给 LLM 判断。
        """

        if ctx.task_id != "T4":
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

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t4_resume_prefinalize_skipped", reason=err)
            return False

        print("[Agent] T4 检测到已有 ideation 三件套且校验通过，跳过 LLM 续跑", flush=True)
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

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t45_resume_prefinalize_skipped", reason=err)
            return False

        print("[Agent] T4.5 检测到已有 novelty audit 且校验通过，跳过 LLM 续跑", flush=True)
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
        print("[Agent] T5-EXTERNAL-WAIT 检测到外部 result_pack 已就绪，跳过 LLM 并进入 T7-INGEST", flush=True)
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

        print("[Agent] T9 检测到已有投稿包且校验通过，跳过环境检查和 LLM 续跑", flush=True)
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

        print("[Agent] T8-PAPER-CLAIM-AUDIT 已用确定性工具完成，跳过 LLM", flush=True)
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
            print("[Agent] T8-SECTION-PLAN 检测到 paper_state/section_outlines 已合格，跳过 LLM 续跑", flush=True)
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

        print(
            "[Agent] T8-SECTION-PLAN 检测到已有计划文件但状态不合格，"
            "使用 initialize_manuscript_state 确定性修复...",
            flush=True,
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

        print("[Agent] T8-SECTION-PLAN 状态修复成功，跳过 LLM 续跑", flush=True)
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

        print("[Agent] T8 检测到已有章节草稿，先确定性重拼 manuscript 并刷新审计...", flush=True)
        ok, err = await refresh_t8_manuscript_outputs(ctx.workspace_dir)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_refresh_failed", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_validation_skipped", reason=err)
            return False

        print("[Agent] T8 manuscript 产物已合格，跳过重复 LLM 续跑", flush=True)
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
        notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
        bridge_notes_dir = ctx.workspace_dir / "literature" / "paper_notes_bridge"
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
                print("[Agent] T3.5 检测到现有 synthesis workbench 且未过期，跳过重复生成", flush=True)
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

        print("[Agent] T3.5 先执行分阶段 synthesis workbench 生成...", flush=True)
        tool = BuildSynthesisWorkbenchTool(policy)
        result = await tool.execute(write_final=False, render_draft=False)
        if not result.ok:
            self.log.warning("t35_workbench_failed", error=result.error, content=result.content)
            return False

        print("[Agent] T3.5 synthesis workbench 已生成；继续交给 LLM 审阅并写最终 synthesis.md", flush=True)
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
            if ok:
                self._record_runtime_completion(ctx, mode, {"raw_count": raw_count})
                return True
            needs_finalize = True

        if not needs_finalize:
            return False

        print(start_message, flush=True)
        recovery = await finalize_t2_outputs(ctx.workspace_dir)
        if not recovery.get("ok"):
            reason = recovery.get("reason") or "unknown"
            self.log.warning(f"{mode}_failed", reason=reason, recovery=recovery)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning(f"{mode}_validation_failed", error=err, recovery=recovery)
            return False

        print(success_message, flush=True)
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
        config_default = load_t2_finalize_config().finish_finalize_min_raw
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
        if ctx.task_id == "T2" and tc.name in T2_AUTO_PERSIST_SEARCH_TOOLS and not result.ok:
            t2_config = load_t2_finalize_config()
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

        t2_config = load_t2_finalize_config()
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
            ScoutProgressLogger(
                ctx.workspace_dir,
                str(getattr(t2_config, "progress_file", "") or "literature/temp/scout_progress.md"),
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
        except Exception:
            return

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
        elif ok and metadata.get("completion_mode") == "t4_resume_prefinalize":
            message = "Agent 成功完成（T4 resume 确定性收尾）"
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
