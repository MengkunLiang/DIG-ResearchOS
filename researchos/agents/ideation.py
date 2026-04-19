"""T4 Ideation Agent — 假设生成与实验计划

基于文献综述生成研究假设和实验计划，通过两轮Gate确认。
输入: synthesis.md, missing_areas.md, seed_ideas.md
输出: hypotheses.md, exp_plan.yaml, risks.md
"""

from __future__ import annotations

import re
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


class IdeationAgent(Agent):
    """假设生成Agent。深度推理+两轮Gate确认。"""

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="ideation",
                model_tier="heavy",
                llm_profile="deep_reasoning",
                tool_names=[
                    "read_file",
                    "write_file",
                    "list_files",
                    "ask_human",
                    "finish_task",
                ],
                max_steps=40,
                max_tokens_total=500_000,
                max_wall_seconds=3600,
                temperature=0.75,
                allowed_read_prefixes=["", "literature/", "user_seeds/"],
                allowed_write_prefixes=["ideation/"],
                prompt_template="ideation.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息和文献综述。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")
        missing_areas = read_text_file(ws / "literature" / "missing_areas.md", default="")
        seed_ideas = read_text_file(ws / "user_seeds" / "seed_ideas.md", default="")
        comparison_table = read_text_file(ws / "literature" / "comparison_table.csv", default="")

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            synthesis_preview=synthesis[:8000],
            missing_areas=missing_areas[:2000],
            seed_ideas=seed_ideas[:2000],
            comparison_table_preview=comparison_table[:1000],
            has_seed_ideas=bool(seed_ideas.strip()),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return (
            "请执行 T4 假设生成。基于 synthesis.md 和 seed_ideas.md，"
            "通过两轮 Gate 与用户确认，产出 hypotheses.md + exp_plan.yaml + risks.md。"
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构 + schema + 引用一致性。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir
        hyp_text = read_text_file(ws / "ideation" / "hypotheses.md")
        if len(hyp_text) < 500:
            return False, f"hypotheses.md 过短({len(hyp_text)} 字符)"
        anchors = re.findall(r"^#+\s*(H\d+)", hyp_text, re.MULTILINE)
        if not anchors:
            return False, "hypotheses.md 必须包含假设anchor（## H1, ## H2等）"

        try:
            plan_data = yaml.safe_load(read_text_file(ws / "ideation" / "exp_plan.yaml"))
        except Exception as e:
            return False, f"exp_plan.yaml 解析失败: {e}"
        ok, err = validate_record(plan_data, "exp_plan")
        if not ok:
            return False, f"exp_plan.yaml 不符合schema: {err}"

        experiments = plan_data.get("experiments", [])
        if not experiments:
            return False, "exp_plan.yaml 必须包含至少一个实验"
        anchor_set = set(anchors)
        for i, exp in enumerate(experiments):
            if "hypothesis_ref" in exp:
                ref = exp["hypothesis_ref"].strip("#").strip().upper()
                if ref not in anchor_set:
                    return False, f"实验{i+1}的hypothesis_ref '{ref}' 不存在"

        risks_text = read_text_file(ws / "ideation" / "risks.md")
        risk_markers = risks_text.count("## 风险") + risks_text.count("## Risk")
        if risk_markers < 3:
            return False, f"risks.md 至少需要3条风险，当前{risk_markers}条"

        project = load_project(ctx)
        max_budget = project.get("constraints", {}).get("max_budget_usd", 100.0)
        for exp in experiments:
            gpu_hours = exp.get("compute_estimate", {}).get("gpu_hours", 0)
            if gpu_hours * 3.0 > max_budget * 0.85:
                return False, f"实验'{exp.get('name', '?')}'成本超预算85%"

        return True, None
