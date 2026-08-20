"""T6 Novelty Agent — 新颖性验证与基线补充

业务需求：
- 在 T5 Pilot 实验完成后增量复核新颖性边界与必要 baseline
- 检查实验结果是否支撑假设的创新性
- 识别潜在撞车案例
- 补充必须的基线方法

输入：
- ideation/hypotheses.md: T4.5 formalization 通过后生成的研究假设
- ideation/exp_plan.yaml: T4.5 formalization 通过后生成的实验计划
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

from ..time_utils import recent_year_from
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.logger import get_logger
from ..runtime.prompts import render_prompt
from ._common import (
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)

logger = get_logger(__name__)


class NoveltyAgent(Agent):
    """T6 Novelty Agent。新颖性验证与基线补充。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "novelty",
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "query_research_evidence",
                        "targeted_literature_supplement",
                        "ask_human",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 150_000,
                    "max_wall_seconds": 600,
                    "temperature": 0.3,
                    # T6 在恢复运行时需要读取 novelty/ 下已有草稿，否则只能“会写不会读”。
                    "allowed_read_prefixes": ["", "ideation/", "literature/", "pilot/", "novelty/"],
                    "allowed_write_prefixes": [
                        "novelty/",
                        "literature/evidence_queries/",
                        "literature/targeted_supplements/",
                        "literature/shallow_read_notes/",
                        "literature/related_work.bib",
                        "literature/literature_manifest.json",
                    ],
                    "prompt_template": "novelty.j2",
                },
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

        # 读取 T4.5 审计结果。T6 的职责不是从零重跑一遍 novelty audit，
        # 而是在已有审计基础上，结合 Pilot 证据做增量复核和补充 baseline。
        novelty_audit = read_text_file(ws / "ideation" / "novelty_audit.md", default="")

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
            novelty_audit_preview=novelty_audit[:2500],
            comparison_table_preview=comparison_table[:1000],
            synthesis_preview=synthesis[:2000],
            hypothesis_count=len(anchors),
            hypothesis_anchors=anchors,
            recent_year_from=recent_year_from(1),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T6 新颖性验证任务。\n"
            "先以 T4.5 的 novelty_audit.md 为主参考，再用 T5 Pilot 结果明确发生变化的机制与边界；"
            "先复用本地 evidence，只在 verdict 会改变时做一次可归档补检，识别潜在撞车风险并补充必须的基线方法。\n"
            "产出 novelty/novelty_report.md、novelty/collision_cases.md（如有）和 "
            "novelty/must_add_baselines.md。"
            ),
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
