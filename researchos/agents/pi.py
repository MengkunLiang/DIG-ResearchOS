"""T1/T7.5 PI Agent - 项目初始化与评估

T1 (init模式): 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
T7.5 (evaluate模式): 评估实验结果，决定后续路径

契约详见 ResearchOS_Agent_Dev_Spec.md §6
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.prompts import render_prompt
from ._common import (
    load_project,
    read_text_file,
    validate_files_exist,
)
from ..schemas.validator import validate_record


class PIAgent(Agent):
    """项目初始化与评估Agent。

    两种模式:
    - init (T1): 三轮对话产出project.yaml和seed文件
    - evaluate (T7.5): 评估实验结果，给出后续建议
    """

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="pi",
                model_tier="heavy",
                tool_names=["read_file", "write_file", "ask_human", "finish_task"],
                max_steps=30,
                max_tokens_total=100_000,
                max_wall_seconds=1800,
                temperature=0.3,  # init模式用0.3，evaluate模式会在prompt中说明
                allowed_read_prefixes=["", "user_seeds/", "experiments/", "ideation/", "evaluation/"],
                allowed_write_prefixes=["", "user_seeds/", "evaluation/"],
                prompt_template="pi.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """根据mode渲染不同的system prompt。"""
        mode = ctx.mode or "init"

        # 准备上下文变量（不包含mode，因为render_prompt已经传递了ctx.mode）
        context_vars = {}

        if mode == "init":
            # T1模式：用户topic从extra中获取
            context_vars["user_topic"] = ctx.extra.get("user_topic", "")
        elif mode == "evaluate":
            # T7.5模式：读取实验结果
            results_path = ctx.workspace_dir / "experiments" / "results_summary.json"
            iteration_log_path = ctx.workspace_dir / "experiments" / "iteration_log.md"
            exp_plan_path = ctx.workspace_dir / "ideation" / "exp_plan.yaml"

            context_vars["has_results"] = results_path.exists()
            context_vars["has_iteration_log"] = iteration_log_path.exists()
            context_vars["has_exp_plan"] = exp_plan_path.exists()

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        mode = ctx.mode or "init"

        if mode == "init":
            user_topic = ctx.extra.get("user_topic", "")
            return (
                f"请开始T1项目初始化流程。用户的研究方向是：{user_topic}\n\n"
                f"请按照system prompt中的三轮对话流程，引导用户明确研究方向，"
                f"最终产出project.yaml和seed文件。"
            )
        elif mode == "evaluate":
            return (
                "请开始T7.5实验评估流程。\n\n"
                "请读取experiments/results_summary.json、experiments/iteration_log.md "
                "和ideation/exp_plan.yaml，评估实验结果，判断Situation (A/B/C/D)，"
                "并给出后续Options建议。"
            )
        else:
            return f"未知模式: {mode}"

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出文件。"""
        # 先调用基类检查文件存在
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        mode = ctx.mode or "init"

        if mode == "init":
            return self._validate_init_outputs(ctx)
        elif mode == "evaluate":
            return self._validate_evaluate_outputs(ctx)
        else:
            return False, f"未知模式: {mode}"

    def _validate_init_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T1 init模式的输出。"""
        # 1. 检查project.yaml存在且符合schema
        project_path = ctx.workspace_dir / "project.yaml"
        if not project_path.exists():
            return False, "缺少project.yaml"

        try:
            project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"project.yaml解析失败: {e}"

        # 校验schema
        ok, err = validate_record(project_data, "project")
        if not ok:
            return False, f"project.yaml不符合schema: {err}"

        # 2. 检查三个seed文件存在（可以为空，但必须存在）
        seed_dir = ctx.workspace_dir / "user_seeds"
        required_seeds = ["seed_papers.jsonl", "seed_ideas.md", "seed_constraints.md"]

        for fname in required_seeds:
            seed_path = seed_dir / fname
            if not seed_path.exists():
                return False, f"缺少seed文件: user_seeds/{fname}"

        return True, None

    def _validate_evaluate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T7.5 evaluate模式的输出。"""
        # 检查evaluation_decision.md存在且包含必需内容
        decision_path = ctx.workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.exists():
            return False, "缺少evaluation/evaluation_decision.md"

        content = read_text_file(decision_path)

        # 必须包含Situation判定
        if "Situation" not in content:
            return False, "evaluation_decision.md必须包含'Situation'章节"

        # 必须包含后续建议
        if not any(keyword in content for keyword in ["Option", "next_task", "建议"]):
            return False, "evaluation_decision.md必须包含后续Options建议"

        return True, None
