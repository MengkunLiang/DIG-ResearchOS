"""T6 Experimenter Agent — 实验执行与结果收集

业务需求：
- 读取ideation/exp_plan.yaml（T4的输出）
- 执行实验计划中的每个实验
- 收集实验结果和日志
- 生成results_summary.json和iteration_log.md

输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置

输出：
- experiments/results_summary.json: 实验结果汇总
- experiments/iteration_log.md: 实验迭代日志
- experiments/runs/{run_id}/: 每个实验的详细结果

契约详见 ResearchOS v4.0 完整实现设计文档 §T6/T7。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.prompts import render_prompt
from ..schemas.validator import validate_record
from ._common import (
    load_project,
    read_text_file,
    validate_files_exist,
)


class ExperimenterAgent(Agent):
    """实验执行Agent。执行实验计划，收集结果。"""

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="experimenter",
                model_tier="medium",
                tool_names=[
                    "read_file",
                    "write_file",
                    "list_files",
                    "bash_run",
                    "docker_exec",
                    "finish_task",
                ],
                max_steps=100,
                max_tokens_total=500_000,
                max_wall_seconds=14400,  # 4小时
                temperature=0.3,
                allowed_read_prefixes=["", "ideation/", "experiments/"],
                allowed_write_prefixes=["experiments/"],
                prompt_template="experimenter.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入实验计划和项目信息。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取实验计划
        exp_plan_path = ws / "ideation" / "exp_plan.yaml"
        exp_plan = {}
        if exp_plan_path.exists():
            try:
                exp_plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8"))
            except Exception:
                exp_plan = {}

        # 读取假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            exp_plan=exp_plan,
            hypotheses_preview=hypotheses[:2000],
            experiment_count=len(exp_plan.get("experiments", [])),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，简短指令。"""
        return (
            "请按 system prompt 执行 T6 实验执行任务。\n"
            "实验计划在 ideation/exp_plan.yaml 中。\n"
            "请执行所有实验，收集结果，生成 experiments/results_summary.json 和 "
            "experiments/iteration_log.md。"
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构 + 至少有1个实验结果。"""
        # 1. 先让基类检查文件存在
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir

        # 2. 校验results_summary.json存在且格式正确
        results_path = ws / "experiments" / "results_summary.json"
        if not results_path.exists():
            return False, "缺少 experiments/results_summary.json"

        try:
            results_data = json.loads(results_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"results_summary.json 解析失败: {e}"

        # 3. 校验必需字段
        if "experiments" not in results_data:
            return False, "results_summary.json 必须包含 'experiments' 字段"

        experiments = results_data.get("experiments", [])
        if len(experiments) == 0:
            return False, "results_summary.json 必须包含至少1个实验结果"

        # 4. 校验每个实验结果的必需字段
        required_fields = ["experiment_id", "status"]
        for i, exp in enumerate(experiments):
            for field in required_fields:
                if field not in exp:
                    return False, f"实验结果 {i+1} 缺少字段: {field}"

        # 5. 校验iteration_log.md存在
        log_path = ws / "experiments" / "iteration_log.md"
        if not log_path.exists():
            return False, "缺少 experiments/iteration_log.md"

        log_content = read_text_file(log_path)
        if len(log_content) < 100:
            return False, f"iteration_log.md 过短({len(log_content)} 字符)"

        return True, None
