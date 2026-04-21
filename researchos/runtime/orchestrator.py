from __future__ import annotations

"""AgentRunner 主循环。"""

import asyncio
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Callable

from ..pydantic_compat import model_dump
from .agent import Agent, AgentResult, EffectiveConfig, ExecutionContext, resolve_effective_config
from .budget import BudgetTracker
from .config import RuntimeSettings
from .errors import BudgetExceeded, LLMProviderError, ToolAccessDenied, ToolError
from .llm_client import LLMClient, ModelBinding
from .logger import get_logger
from .message import Message, Role, ToolCall, is_empty_assistant
from .trace import TraceWriter
from ..tools.base import Tool, ToolResult
from ..tools.human_gate import HumanInterface
from ..tools.registry import ToolBuildContext, ToolRegistry

if TYPE_CHECKING:
    from ..tools.workspace_policy import WorkspaceAccessPolicy


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

    async def run(self, ctx: ExecutionContext) -> AgentResult:
        """执行一次完整 agent run。"""
        started = time.time()
        eff = resolve_effective_config(self.agent.spec, ctx)
        budget = BudgetTracker(
            max_steps=eff.max_steps,
            max_tokens=eff.max_tokens,
            max_wall_seconds=eff.max_wall_seconds,
        )
        trace_file = self.runtime_settings.traces_dir(ctx.workspace_dir) / f"{ctx.run_id}.jsonl"
        trace = TraceWriter(trace_file)
        trace.write_run_start(
            run_id=ctx.run_id,
            agent_name=self.agent.spec.name,
            project_id=ctx.project_id,
            task_id=ctx.task_id,
            workspace_dir=ctx.workspace_dir,
        )


        # 输出初始化信息
        print(f"[Agent] 初始化完成 (模型层级: {eff.llm_tier}, 最大步数: {eff.max_steps})", flush=True)
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
        )[0][0]

        empty_count = 0
        nudge_count = 0
        validation_fails = 0

        # Pre-hooks在try块之前执行，失败时直接抛异常阻止运行
        for hook in self.agent.spec.pre_hooks:
            await hook(ctx)

        try:
            while True:
                # 每进入一轮 while，就代表一次“agent step”。
                budget.tick_step()

                # 每5步输出一次进度
                if budget.steps % 5 == 1 or budget.steps == 1:
                    print(f"[Agent] 步骤 {budget.steps}/{eff.max_steps} | Token: {budget.tokens_in + budget.tokens_out} | 成本: ${budget.cost_usd:.4f}", flush=True)
                try:
                    budget.check()
                except BudgetExceeded as exc:
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
                    )
                except LLMProviderError as exc:
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
                        self._execute_one_tool_call(tc, tool_map, step=budget.steps)
                        for tc in assistant_msg.tool_calls
                    ]
                )

                finish_requested = False
                for tool_call, tool_msg in zip(assistant_msg.tool_calls, tool_msgs):
                    messages.append(tool_msg)
                    trace.write_message(tool_msg)
                    if tool_call.name == "finish_task" and not tool_msg.metadata.get("is_error"):
                        finish_requested = True

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
                    print(f"[Agent] 输出校验失败 ({validation_fails}/{self.agent.spec.max_validation_retries}): {err}", flush=True)
                    if validation_fails >= self.agent.spec.max_validation_retries:
                        stop_reason = AgentResult.STOP_ERROR
                        error_msg = f"Validation failed {validation_fails} times. Last reason: {err}"
                        break
                    feedback = Message.user(
                        f"你声称已完成，但输出校验失败：{err}。请修复后再次调用 finish_task。",
                        step=budget.steps,
                    )
                    messages.append(feedback)
                    trace.write_message(feedback)

                if budget.steps >= eff.max_steps:
                    stop_reason = AgentResult.STOP_MAX_STEPS
                    error_msg = "Reached maximum allowed steps"
                    break

        except asyncio.CancelledError:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = "Cancelled"
            raise
        except Exception as exc:  # pragma: no cover - safety net
            stop_reason = AgentResult.STOP_ERROR
            error_msg = f"Unexpected: {exc!r}"
            self.log.exception("agent_runner_crashed")
        finally:
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
                    await hook(ctx, result)
                except Exception:  # pragma: no cover - logging path
                    self.log.exception("post_hook_failed")
            trace.close(result)
        return result

    async def _execute_one_tool_call(
        self,
        tc: ToolCall,
        tool_map: dict[str, Tool],
        *,
        step: int,
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
            try:
                approved = await self.human.ask_approval(tool_name=tc.name, arguments=tc.arguments)
            except Exception as exc:
                return Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval failed: {exc!r}",
                    is_error=True,
                    step=step,
                )
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

        try:
            # 工具自身可有细粒度超时，但 runtime 仍统一包一层 wait_for。
            result: ToolResult = await asyncio.wait_for(
                tool.execute(**model_dump(parsed)),
                timeout=tool.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool timed out after {tool.timeout_seconds}s",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
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

        return Message.tool(
            tool_call_id=tc.id,
            name=tc.name,
            content=result.content,
            is_error=not result.ok,
            step=step,
            duration_ms=int((time.time() - started) * 1000),
            metadata={"data": result.data, "error": result.error},
        )

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
        return Message.assistant(content=content, tool_calls=tool_calls, step=step)

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
        message = {
            AgentResult.STOP_FINISHED: "Agent 成功完成",
            AgentResult.STOP_MAX_STEPS: "达到最大步数",
            AgentResult.STOP_BUDGET: "超出预算",
            AgentResult.STOP_ERROR: f"错误: {error_msg or 'unknown'}",
            AgentResult.STOP_INTERRUPTED: "被中断",
            AgentResult.STOP_HUMAN_REJECT: "被用户拒绝",
        }[stop_reason]
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
        )
