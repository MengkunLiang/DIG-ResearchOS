"""T4 Ideation Agent — 假设生成与实验计划

基于文献综述生成研究假设和实验计划，通过两轮Gate确认。
输入: synthesis.md, missing_areas.md, seed_ideas.md
输出: hypotheses.md, exp_plan.yaml, risks.md, idea_rationales.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..schemas.validator import validate_record
from ._common import (
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)


class IdeationAgent(Agent):
    """假设生成Agent。深度推理+两轮Gate确认。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "ideation",
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "list_files",
                        "ask_human",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 200_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.75,
                    "allowed_read_prefixes": [
                        "",
                        "literature/",
                        "user_seeds/",
                        "ideation/",
                        "_runtime/resume/",
                    ],
                    "allowed_write_prefixes": ["ideation/"],
                    "prompt_template": "ideation.j2",
                    "structured_outputs": {
                        "ideation/exp_plan.yaml": "exp_plan",
                        "ideation/idea_rationales.json": "idea_rationales",
                    },
                },
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
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T4 假设生成。基于 synthesis.md 和 seed_ideas.md，"
            "通过两轮 Gate 与用户确认，产出 hypotheses.md + exp_plan.yaml + "
            "risks.md + idea_rationales.json。"
            ),
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

        # 提取假设anchors（支持 ## H1, ## H2 等格式）
        anchors = re.findall(r"^#+\s*(H\d+)", hyp_text, re.MULTILINE)
        if not anchors:
            return False, "hypotheses.md 必须包含假设anchor（## H1, ## H2等）"

        # 规范化anchors为大写
        anchor_set = set(a.upper() for a in anchors)

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

        # 检查hypothesis_ref引用
        for i, exp in enumerate(experiments):
            if "hypothesis_ref" in exp:
                raw_ref = exp["hypothesis_ref"]
                if isinstance(raw_ref, (list, tuple)):
                    refs = [str(ref).strip() for ref in raw_ref if str(ref).strip()]
                else:
                    refs = [
                        ref.strip()
                        for ref in re.split(r"[,;，、\s]+", str(raw_ref))
                        if ref.strip()
                    ]
                if not refs:
                    return False, f"实验{i+1}的hypothesis_ref 为空"
                for ref in refs:
                    # 移除可能的 # 前缀，并转为大写
                    ref_normalized = ref.lstrip("#").strip().upper()
                    if ref_normalized not in anchor_set:
                        return False, f"实验{i+1}的hypothesis_ref '{ref}' 不存在于hypotheses.md中（可用: {anchor_set}）"

        risks_text = read_text_file(ws / "ideation" / "risks.md")
        risk_markers = risks_text.count("## 风险") + risks_text.count("## Risk")
        if risk_markers < 3:
            return False, f"risks.md 至少需要3条风险，当前{risk_markers}条"

        rationales_path = ws / "ideation" / "idea_rationales.json"
        if not rationales_path.exists():
            return False, "缺少 ideation/idea_rationales.json，无法追踪每个idea的生成依据"
        try:
            rationale_data = json.loads(rationales_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"idea_rationales.json 解析失败: {e}"
        if not isinstance(rationale_data, dict):
            return False, "idea_rationales.json 必须是JSON对象"
        ok, err = validate_record(rationale_data, "idea_rationales")
        if not ok:
            return False, f"idea_rationales.json 不符合schema: {err}"

        ideas = rationale_data.get("ideas", [])
        if not isinstance(ideas, list) or not ideas:
            return False, "idea_rationales.json 必须包含至少一条idea依据记录"
        covered_refs: set[str] = set()
        for i, idea in enumerate(ideas, start=1):
            if not isinstance(idea, dict):
                return False, f"idea_rationales.json 第{i}条idea必须是对象"
            refs = idea.get("hypothesis_refs") or []
            for ref in refs:
                covered_refs.add(str(ref).lstrip("#").strip().upper())

            basis = idea.get("basis") or {}
            observations = basis.get("literature_observations") or []
            if not observations:
                return False, f"idea_rationales.json 第{i}条idea缺少literature_observations依据"
            reasoning = str(idea.get("reasoning") or "").strip()
            if len(reasoning) < 10:
                return False, f"idea_rationales.json 第{i}条idea的reasoning过短"

        missing_rationales = sorted(anchor_set - covered_refs)
        if missing_rationales:
            return False, (
                "idea_rationales.json 必须覆盖 hypotheses.md 中的所有假设anchor，"
                f"缺少: {missing_rationales}"
            )

        project = load_project(ctx)
        max_budget = project.get("constraints", {}).get("max_budget_usd", 100.0)
        total_estimated_cost = 0.0
        for exp in experiments:
            estimate = exp.get("compute_estimate", {}) or {}
            gpu_hours = float(estimate.get("gpu_hours", 0) or 0)
            estimated_cost = estimate.get("estimated_cost_usd")
            exp_cost = float(estimated_cost) if estimated_cost is not None else gpu_hours * 3.0
            total_estimated_cost += exp_cost
            if exp_cost > max_budget * 0.85:
                return False, f"实验'{exp.get('name', '?')}'成本超预算85%"

        declared_total = plan_data.get("total_estimated_cost_usd")
        if declared_total is not None and float(declared_total) > max_budget:
            return False, (
                f"exp_plan.yaml 声明总成本 ${float(declared_total):.2f} "
                f"超过项目预算 ${float(max_budget):.2f}"
            )

        if total_estimated_cost > max_budget:
            return False, (
                f"实验总成本 ${total_estimated_cost:.2f} "
                f"超过项目预算 ${float(max_budget):.2f}"
            )

        budget_check = plan_data.get("budget_check") or {}
        if isinstance(budget_check, dict) and budget_check.get("over_budget") is True:
            return False, "exp_plan.yaml budget_check.over_budget=true，不能判定为完成"

        return True, None
