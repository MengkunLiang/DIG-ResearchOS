"""T6 Novelty Agent — 新颖性验证与基线补充

业务需求：
- 在 T5 Pilot 实验完成后进行新颖性最终验证
- 检查实验结果是否支撑假设的创新性
- 识别潜在撞车案例
- 补充必须的基线方法

输入：
- ideation/hypotheses.md: T4 产出的研究假设
- ideation/exp_plan.yaml: T4 产出的实验计划
- pilot/pilot_results.json: T5 Pilot 实验结果
- pilot/motivation_validation.md: T5 Pilot 动机验证
- literature/comparison_table.csv: 已有方法对比表
- literature/synthesis.md: T3.5 文献综述

输出：
- novelty/novelty_report.md: 新颖性报告
- novelty/collision_cases.md: 潜在撞车案例（如有）
- novelty/must_add_baselines.md: 必须补充的基线方法
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.prompts import render_prompt
from ._common import (
    load_project,
    read_text_file,
    validate_files_exist,
)

logger = structlog.get_logger(__name__)


class NoveltyAgent(Agent):
    """T6 Novelty Agent。新颖性验证与基线补充。"""

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="novelty",
                model_tier="medium",
                tool_names=[
                    "read_file",
                    "write_file",
                    "list_files",
                    "search_papers",
                    "ask_human",
                    "finish_task",
                ],
                max_steps=50,
                max_tokens_total=300_000,
                max_wall_seconds=3600,
                temperature=0.3,
                allowed_read_prefixes=["", "ideation/", "literature/", "pilot/"],
                allowed_write_prefixes=["novelty/"],
                prompt_template="novelty.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染 system prompt。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")

        # 读取实验计划
        exp_plan = read_text_file(ws / "ideation" / "exp_plan.yaml", default="")

        # 读取 Pilot 结果（如果有）
        pilot_results = read_text_file(ws / "pilot" / "pilot_results.json", default="")

        # 读取 Motivation Validation
        motivation = read_text_file(ws / "pilot" / "motivation_validation.md", default="")

        # 读取对比表
        comparison_table = read_text_file(ws / "literature" / "comparison_table.csv", default="")

        # 读取文献综述
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")

        # 提取假设 anchor
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            hypotheses_preview=hypotheses[:5000],
            exp_plan_preview=exp_plan[:2000],
            pilot_results_preview=pilot_results[:2000],
            motivation_preview=motivation[:1500],
            comparison_table_preview=comparison_table[:1000],
            synthesis_preview=synthesis[:2000],
            hypothesis_count=len(anchors),
            hypothesis_anchors=anchors,
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return (
            "请执行 T6 新颖性验证任务。\n"
            "基于 T5 Pilot 实验结果和 T4 假设，检查每个假设的创新性，"
            "搜索近期相关工作，识别潜在撞车风险，补充必须的基线方法。\n"
            "产出 novelty/novelty_report.md、novelty/collision_cases.md（如有）和 "
            "novelty/must_add_baselines.md。"
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """验证 T6 输出。"""
        ws = ctx.workspace_dir

        # 1. 必需文件检查
        required_files = [
            "novelty/novelty_report.md",
            "novelty/must_add_baselines.md",
        ]
        ok, err = validate_files_exist(ctx, required_files)
        if not ok:
            return False, err

        # 2. novelty_report.md 内容检查
        report_path = ws / "novelty" / "novelty_report.md"
        report_text = read_text_file(report_path)

        if len(report_text) < 500:
            return False, f"novelty/novelty_report.md 过短({len(report_text)} 字符)"

        # 检查是否包含新颖性等级标记
        level_markers = ["Level 0", "Level 1", "Level 2", "Level 3"]
        has_level = any(marker in report_text for marker in level_markers)
        if not has_level:
            return False, "novelty/novelty_report.md 必须包含新颖性等级（Level 0-3）"

        # 3. must_add_baselines.md 内容检查
        baselines_path = ws / "novelty" / "must_add_baselines.md"
        baselines_text = read_text_file(baselines_path)

        if len(baselines_text) < 100:
            return False, f"novelty/must_add_baselines.md 过短({len(baselines_text)} 字符)"

        # 4. 检查是否审计了所有假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)

        for anchor in anchors:
            if anchor not in report_text:
                return False, f"novelty/novelty_report.md 缺少对假设 {anchor} 的审计"

        # 5. collision_cases.md 检查（如果有 High Overlap 则必须存在）
        collision_path = ws / "novelty" / "collision_cases.md"
        if collision_path.exists():
            collision_text = read_text_file(collision_path)
            # 检查是否标记了高风险撞车
            has_high_risk = "高风险" in collision_text or "High" in collision_text
            if has_high_risk and "Level 0" in report_text:
                logger.warning(
                    "发现 Level 0 假设但 novelty_report 未明确标记撞车风险"
                )

        return True, None
