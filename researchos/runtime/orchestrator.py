from __future__ import annotations

"""AgentRunner 主循环。"""

import asyncio
import inspect
import json
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Callable

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
from .abstract_sweep import run_abstract_sweep
from .t3_recovery import prepare_t3_resume_artifacts
from .task_recovery import prepare_generic_resume_artifacts
from .trace import NullTraceWriter, TraceWriter
from ..tools.base import Tool, ToolResult
from ..tools.human_gate import HumanInputUnavailable, HumanInterface
from ..tools.paper_save_tools import SavePapersRawTool
from ..tools.registry import ToolBuildContext, ToolRegistry
from .agent_params import get_agent_mode_params, get_budget_escalation_policy, get_global_timeout, get_retry_policy

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
T2_AUTO_FINALIZE_TRIGGER_TOOLS = T2_AUTO_PERSIST_SEARCH_TOOLS | frozenset(
    {
        "append_papers_raw",
        "process_papers_raw",
        "save_papers_raw",
    }
)
T2_AUTO_FINALIZE_MIN_RAW = 100
TOOL_FAILURE_CACHE_NAMES = frozenset({"fetch_paper_pdf"})
TOOL_CONTEXT_CONTENT_LIMITS = {
    # PDF 文本工具是 T3 上下文膨胀的主要来源。工具自身也有上限，这里再加
    # runtime 兜底，防止未来工具改动或异常 PDF 解析再次把长文本塞进模型。
    "extract_paper_sections": 12000,
    "extract_pdf_text": 50000,
}
T2_PROTECTED_SEARCH_BUCKET_ALIASES = {
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
    return T2_PROTECTED_SEARCH_BUCKET_ALIASES.get(value, value.replace(" ", "_"))


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
            # pre-hook 允许是同步或异步 callable；若返回 (ok, err) 且 ok=False，
            # 这里会统一转换成可读错误，而不是让 CLI 因 await 非协程直接崩溃。
            for hook in self.agent.spec.pre_hooks:
                await self._run_pre_hook(hook, ctx)

            t2_pre_finalized = await self._maybe_finalize_t2_before_llm(ctx)
            t3_pre_finalized = False
            if not t2_pre_finalized:
                t3_pre_finalized = await self._maybe_finalize_t3_before_llm(ctx)
            t4_pre_finalized = False
            t35_prepared = False
            if not (t2_pre_finalized or t3_pre_finalized):
                t35_prepared = await self._maybe_prepare_t35_before_llm(ctx, policy)
            if not (t2_pre_finalized or t3_pre_finalized):
                t4_pre_finalized = await self._maybe_finalize_t4_before_llm(ctx)
            t45_pre_finalized = False
            if not (t2_pre_finalized or t3_pre_finalized or t4_pre_finalized):
                t45_pre_finalized = await self._maybe_finalize_t45_before_llm(ctx)
            t8_section_plan_pre_finalized = False
            if not (t2_pre_finalized or t3_pre_finalized or t4_pre_finalized or t45_pre_finalized):
                t8_section_plan_pre_finalized = await self._maybe_finalize_t8_section_plan_before_llm(
                    ctx,
                    policy,
                )
            t8_manuscript_pre_finalized = False
            if not (
                t2_pre_finalized
                or t3_pre_finalized
                or t4_pre_finalized
                or t45_pre_finalized
                or t8_section_plan_pre_finalized
            ):
                t8_manuscript_pre_finalized = await self._maybe_finalize_t8_manuscript_before_llm(ctx)
            deterministic_pre_finalized = (
                t2_pre_finalized
                or t3_pre_finalized
                or t4_pre_finalized
                or t45_pre_finalized
                or t8_section_plan_pre_finalized
                or t8_manuscript_pre_finalized
            )
            if deterministic_pre_finalized:
                stop_reason = AgentResult.STOP_FINISHED
                error_msg = None

            while not deterministic_pre_finalized:
                # 每进入一轮 while，就代表一次“agent step”。
                budget.tick_step()

                # 每5步输出一次进度
                if budget.steps % 5 == 1 or budget.steps == 1:
                    step_limit = "unlimited" if budget.unlimited_budget else str(budget.max_steps)
                    print(f"[Agent] 步骤 {budget.steps}/{step_limit} | Token: {budget.tokens_in + budget.tokens_out} | 成本: ${budget.cost_usd:.4f}", flush=True)
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
                        print(
                            "[Agent] LLM provider 连续超时，"
                            f"冷却 {cooldown:g}s 后继续尝试（第 {llm_timeout_cooldowns_used} 轮）",
                            flush=True,
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

                # 输出 Agent 的文本回复（如果有）
                if assistant_msg.content and assistant_msg.content.strip():
                    print(f"\n[Agent 输出]\n{assistant_msg.content}\n", flush=True)

                # 如果模型只说话不调用工具，runtime 会反复提醒它：
                # 要么继续推进，要么明确 finish_task。
                if not assistant_msg.tool_calls:
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
                # 输出工具调用信息
                if len(assistant_msg.tool_calls) > 0:
                    tool_names = [tc.name for tc in assistant_msg.tool_calls]
                    print(f"[Agent] 调用工具: {', '.join(tool_names)}", flush=True)

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
                    print(f"[Agent] 任务暂停：{pause_reason}", flush=True)
                    break

                t2_finalized = await self._maybe_finalize_t2_after_tool_batch(
                    ctx=ctx,
                    tool_calls=assistant_msg.tool_calls,
                    tool_msgs=tool_msgs,
                )
                if t2_finalized:
                    note = Message.user(
                        "[Runtime] T2 已基于已落盘的 papers_raw.jsonl 完成确定性收尾，"
                        "后续去重、验证、精读队列和审计文件由 runtime 生成。",
                        step=budget.steps,
                    )
                    messages.append(note)
                    trace.write_message(note)
                    stop_reason = AgentResult.STOP_FINISHED
                    error_msg = None
                    break

                if finish_requested:
                    # finish_task 只是“请求结束”而不是直接结束。
                    # 真正能否成功结束，仍以 validate_outputs 为准。
                    print(f"[Agent] Agent 请求完成任务，开始校验输出...", flush=True)
                    ok, err = self.agent.validate_outputs(ctx)
                    if ok:
                        print(f"[Agent] 输出校验通过，任务完成", flush=True)
                        stop_reason = AgentResult.STOP_FINISHED
                        break
                    validation_fails += 1
                    print(f"[Agent] 输出校验失败 ({validation_fails}/{validation_retry_limit}): {err}", flush=True)
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
            raise
        except RecoverableRuntimePause as exc:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = str(exc)
        except HookExecutionError as exc:
            stop_reason = AgentResult.STOP_ERROR
            error_msg = str(exc)
        except Exception as exc:  # pragma: no cover - safety net
            stop_reason = AgentResult.STOP_ERROR
            error_msg = f"Unexpected: {exc!r}"
            self.log.exception("agent_runner_crashed")
        finally:
            stop_reason, error_msg = await self._maybe_finalize_t2_outputs(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
            )
            self._refresh_resume_artifacts(ctx)
            self._maybe_refresh_t3_resume_artifacts(ctx, stop_reason)
            self._maybe_run_t3_abstract_sweep(ctx, stop_reason)
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
        print(
            "[Agent] 初始化完成 | "
            f"任务: {ctx.task_id} | Agent: {self.agent.spec.name} | 阶段: {phase} | "
            f"目标: {description} | 输出: {', '.join(expected) if expected else '未声明'} | "
            f"模型层级: {eff.llm_tier} | 最大步数: {step_limit}",
            flush=True,
        )

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
            "T5-DRY-RUN": "跑通 mock 外部执行器文件协议，不执行真实实验",
            "T7-INGEST": "摄取外部 result pack 并规范化结果证据",
            "T7-AUDIT": "审计实验 provenance、hash、mock 标记和指标来源",
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

    async def _maybe_finalize_t2_outputs(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
    ) -> tuple[str, str | None]:
        """当 T2 中途失败但 raw 已落盘时，尝试代码化补齐其余输出。"""

        if ctx.task_id != "T2":
            return stop_reason, error_msg
        if stop_reason in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_HUMAN_REJECT}:
            return stop_reason, error_msg

        needs_recovery = stop_reason != AgentResult.STOP_FINISHED or any(
            not path.exists()
            for name, path in ctx.outputs_expected.items()
            if name != "papers_raw"
        )
        if not needs_recovery:
            return stop_reason, error_msg

        finalized = await self._finalize_t2_from_raw(
            ctx,
            mode="t2_recovery",
            min_raw_count=1,
            start_message="[Agent] T2 检测到未完成输出，尝试基于 papers_raw 自动补全...",
            success_message="[Agent] T2 自动补全成功，已恢复完整 T2 产物",
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

    def _maybe_run_t3_abstract_sweep(self, ctx: ExecutionContext, stop_reason: str) -> None:
        """T3 deep read 成功后，自动运行 abstract sweep 补读。"""

        if ctx.task_id != "T3" or stop_reason != AgentResult.STOP_FINISHED:
            return

        try:
            mode_params = get_agent_mode_params("reader", "read")
            sweep_config = mode_params.get("abstract_sweep", {})
            if not sweep_config.get("enabled", False):
                return

            print("[Agent] T3 deep read 完成，开始 abstract sweep...", flush=True)
            result = run_abstract_sweep(ctx.workspace_dir, sweep_config)
            ctx.extra["abstract_sweep"] = result

            if result.get("notes_generated", 0) > 0:
                print(
                    f"[Agent] Abstract sweep 完成：筛选 {result['candidates_found']} 篇候选，"
                    f"生成 {result['notes_generated']} 篇 abstract note",
                    flush=True,
                )
            else:
                print("[Agent] Abstract sweep 无候选论文", flush=True)
        except Exception:  # pragma: no cover - sweep failure should not fail a completed T3
            self.log.exception("t3_abstract_sweep_failed")

    async def _maybe_finalize_t2_before_llm(self, ctx: ExecutionContext) -> bool:
        """T2 续跑时，如果 raw 已存在，优先用确定性路径补齐产物。

        这避免模型在 `papers_raw.jsonl` 这种大文件和恢复状态之间反复读取，
        也让中断后的 T2 可以稳定从 raw 收敛到完整的 8 个 T2 输出。
        """

        if ctx.task_id != "T2":
            return False

        return await self._finalize_t2_from_raw(
            ctx,
            mode="t2_resume_prefinalize",
            min_raw_count=1,
            start_message="[Agent] T2 检测到已有 papers_raw，先执行确定性收尾...",
            success_message="[Agent] T2 确定性收尾成功，跳过 LLM 续跑",
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
        if not notes_dir.exists() or not any(notes_dir.glob("*.md")):
            return False
        staged_outputs = [
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "synthesis_outline.md",
            ctx.workspace_dir / "literature" / "synthesis_draft.md",
        ]
        note_files = [path for path in notes_dir.glob("*.md") if path.is_file()]
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

    async def _maybe_finalize_t2_after_tool_batch(
        self,
        *,
        ctx: ExecutionContext,
        tool_calls: list[ToolCall],
        tool_msgs: list[Message],
    ) -> bool:
        """T2 冷启动正常路径：raw 达标后直接由 runtime 收尾。

        这条路径避免 LLM 读取并手动解析 `papers_raw.jsonl`。检索工具负责拿到
        raw，runtime 负责 raw -> dedup -> verified -> queue -> audit 的确定性处理。
        """

        if ctx.task_id != "T2":
            return False

        triggered = any(
            tool_call.name in T2_AUTO_FINALIZE_TRIGGER_TOOLS
            and not tool_msg.metadata.get("is_error")
            for tool_call, tool_msg in zip(tool_calls, tool_msgs)
        )
        if not triggered:
            return False

        return await self._finalize_t2_from_raw(
            ctx,
            mode="t2_deterministic",
            min_raw_count=self._t2_auto_finalize_min_raw(ctx),
            start_message="[Agent] T2 raw 已达到收尾阈值，执行确定性收尾...",
            success_message="[Agent] T2 确定性收尾成功，任务完成",
        )

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
        self.log.info(f"{mode}_succeeded", recovery=recovery)
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
    def _t2_auto_finalize_min_raw(ctx: ExecutionContext) -> int:
        raw_value = ctx.extra.get("t2_auto_finalize_min_raw", T2_AUTO_FINALIZE_MIN_RAW)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return T2_AUTO_FINALIZE_MIN_RAW
        return max(10, value)

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
    ) -> Message:
        started = time.time()
        tool = tool_map.get(tc.name)
        if tool is None:
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"ERROR: unknown tool '{tc.name}'. Available: {sorted(tool_map)}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )

        if tool.requires_human_approval:
            # 高风险工具先经过 HumanInterface 审批。
            human_started = time.time()
            try:
                approved = await self.human.ask_approval(tool_name=tc.name, arguments=tc.arguments)
            except HumanInputUnavailable as exc:
                return Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval input unavailable: {exc}",
                    is_error=True,
                    step=step,
                    metadata={"data": {"input_unavailable": True}, "error": "human_input_unavailable"},
                )
            except Exception as exc:
                return Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval failed: {exc!r}",
                    is_error=True,
                    step=step,
                )
            finally:
                if budget is not None:
                    budget.exclude_wall_time(time.time() - human_started)
            if not approved:
                return Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="Rejected by human.",
                    is_error=True,
                    step=step,
                )

        try:
            # 先用 pydantic schema 做参数校验。
            parsed = tool.parameters_schema(**tc.arguments)
        except Exception as exc:
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Parameter validation error: {exc}",
                is_error=True,
                step=step,
            )

        failure_cache_key = self._tool_failure_cache_key(tc.name, model_dump(parsed))
        if failure_cache_key and tool_failure_cache is not None and failure_cache_key in tool_failure_cache:
            cached = tool_failure_cache[failure_cache_key]
            return Message.tool(
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
            return tool_msg
        except ToolAccessDenied as exc:
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Access denied: {exc}",
                is_error=True,
                step=step,
            )
        except ToolError as exc:
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool error: {exc}",
                is_error=True,
                step=step,
            )
        except Exception as exc:
            self.log.exception("tool_crashed", tool=tc.name)
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool crashed unexpectedly: {exc!r}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )

        auto_persist_metadata = await self._maybe_auto_persist_t2_search_result(
            ctx=ctx,
            policy=policy,
            tool_name=tc.name,
            tool_arguments=model_dump(parsed),
            result=result,
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
        return tool_msg

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

        papers = result.data.get("papers")
        edge_persist = self._persist_t2_citation_edges_if_present(
            ctx=ctx,
            policy=policy,
            tool_name=tool_name,
            result=result,
        )
        if not isinstance(papers, list) or not papers:
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
            return {
                "ok": False,
                "error": save_result.error,
                "content_suffix": f"[Runtime] 自动保存 papers_raw 失败: {save_result.content}",
            }

        persisted_count = save_result.data.get("count", len(papers))
        content_suffix = f"[Runtime] 已自动追加 {persisted_count} 篇到 literature/papers_raw.jsonl"
        if edge_persist and edge_persist.get("content_suffix"):
            content_suffix += "\n" + str(edge_persist["content_suffix"])
        return {
            "ok": True,
            "count": persisted_count,
            "mode": save_result.data.get("mode", "append"),
            "content_suffix": content_suffix,
        }

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
                key = tuple(sorted((left, right)))
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
        carries labels supplied by the LLM/tool metadata so deterministic queue
        builders can protect adjacent-field and theory-bridge material.
        """

        bucket = _normalize_t2_query_bucket(
            tool_arguments.get("search_bucket")
            or tool_arguments.get("query_bucket")
            or result.data.get("search_bucket")
            or result.data.get("query_bucket")
        )
        query = str(tool_arguments.get("query") or result.data.get("query") or "").strip()
        if not bucket and not query:
            return papers

        annotated: list[object] = []
        for paper in papers:
            if not isinstance(paper, dict):
                annotated.append(paper)
                continue
            record = dict(paper)
            if bucket and not record.get("search_bucket"):
                record["search_bucket"] = bucket
            if bucket and not record.get("source_bucket"):
                if bucket == "adjacent_field":
                    record["source_bucket"] = "adjacent"
                elif bucket == "theory_bridge":
                    record["source_bucket"] = "adjacent"
                elif bucket in {"core", "snowball", "seed"}:
                    record["source_bucket"] = bucket
            if bucket in {"adjacent_field", "theory_bridge"}:
                record.setdefault("adjacent_field", True)
            if query:
                record.setdefault("source_query", query)
            record.setdefault("source_tool", tool_name)
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
        if ok and metadata.get("completion_mode") == "t2_deterministic":
            message = "Agent 成功完成（T2 runtime 确定性收尾）"
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
