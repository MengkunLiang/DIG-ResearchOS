"""T4 Ideation Agent — 假设生成与实验计划

基于文献综述生成研究假设和实验计划，通过两轮Gate确认。
输入: synthesis.md, missing_areas.md, seed_ideas.md
输出: hypotheses.md, exp_plan.yaml, risks.md, idea_rationales.json,
      idea_scorecard.yaml, rejected_ideas.md, gate_decisions.json,
      _pass1_forward_candidates.json, _pass2_grounding_review.json,
      _gate1_selection_brief.md
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
from ..tools.ideation_analysis import analyze_ideation_coverage
from ._common import (
    cdr_schema_prompt_summary,
    load_cdr_schema,
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)
from .guidance import load_agent_guidance


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
                        "analyze_idea_concentration",
                        "compute_idea_novelty_signal",
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
                        "ideation/idea_scorecard.yaml": "idea_scorecard",
                        "ideation/gate_decisions.json": "gate_decisions",
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
        domain_map = read_text_file(ws / "literature" / "domain_map.json", default="")
        synthesis_workbench = read_text_file(ws / "literature" / "synthesis_workbench.json", default="")
        survey_insights = read_text_file(ws / "ideation" / "survey_insights.json", default="")

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            synthesis_preview=synthesis[:8000],
            missing_areas=missing_areas[:2000],
            seed_ideas=seed_ideas[:2000],
            comparison_table_preview=comparison_table[:1000],
            domain_map_preview=domain_map[:2500],
            synthesis_workbench_preview=synthesis_workbench[:3000],
            survey_insights_preview=survey_insights[:3000],
            has_domain_map=bool(domain_map.strip()),
            has_synthesis_workbench=bool(synthesis_workbench.strip()),
            has_survey_insights=bool(survey_insights.strip()),
            has_seed_ideas=bool(seed_ideas.strip()),
            temperature=self.spec.temperature,
            agent_guidance=load_agent_guidance("ideation"),
            cdr_schema_summary=cdr_schema_prompt_summary(),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T4 假设生成。基于 synthesis.md 和 seed_ideas.md，"
            "通过两轮 Gate 与用户确认，产出 hypotheses.md + exp_plan.yaml + "
            "risks.md + idea_rationales.json + idea_scorecard.yaml + "
            "rejected_ideas.md + gate_decisions.json + Pass1/Pass2可见候选文件。"
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
            forward_reasoning = (
                basis.get("forward_reasoning")
                or basis.get("problem_reframing")
                or basis.get("analogy_basis")
                or basis.get("grounding_checks")
            )
            if not observations and not forward_reasoning:
                return False, (
                    f"idea_rationales.json 第{i}条idea缺少生成依据："
                    "可用 literature_observations，也可用 forward_reasoning/problem_reframing/"
                    "analogy_basis/grounding_checks，不能为通过 gate 伪造文献来源"
                )
            reasoning = str(idea.get("reasoning") or "").strip()
            if len(reasoning) < 10:
                return False, f"idea_rationales.json 第{i}条idea的reasoning过短"

        missing_rationales = sorted(anchor_set - covered_refs)
        if missing_rationales:
            return False, (
                "idea_rationales.json 必须覆盖 hypotheses.md 中的所有假设anchor，"
                f"缺少: {missing_rationales}"
            )

        scorecard_path = ws / "ideation" / "idea_scorecard.yaml"
        if not scorecard_path.exists():
            return False, "缺少 ideation/idea_scorecard.yaml，无法追踪候选idea证据链"
        try:
            scorecard_data = yaml.safe_load(scorecard_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"idea_scorecard.yaml 解析失败: {e}"
        if not isinstance(scorecard_data, dict):
            return False, "idea_scorecard.yaml 必须是YAML对象"
        ok, err = validate_record(scorecard_data, "idea_scorecard")
        if not ok:
            return False, f"idea_scorecard.yaml 不符合schema: {err}"

        scorecard_ideas = scorecard_data.get("ideas", [])
        if not isinstance(scorecard_ideas, list) or len(scorecard_ideas) < 2:
            return False, "idea_scorecard.yaml 至少需要记录2个候选idea，包含选中和淘汰/暂缓项"

        ok, err = _validate_pass_stage_artifacts(ws)
        if not ok:
            return False, err
        ok, err = _validate_candidate_directions(ws)
        if not ok:
            return False, err

        # R1: mechanism / prediction / counterfactual / mechanism_family 必须存在
        _mechanism_fields = ("mechanism", "prediction", "counterfactual", "mechanism_family")
        placeholder_values = {
            "mechanism": {"see core_claim", "same as core_claim", "tbd", "todo", "n/a"},
            "prediction": {"qualitative: outperforms baseline", "outperforms baseline", "tbd", "todo", "n/a"},
            "counterfactual": {"no clear counterfactual", "tbd", "todo", "n/a"},
        }
        for i, item in enumerate(scorecard_ideas, start=1):
            if not isinstance(item, dict):
                continue
            idea = item.get("idea") or {}
            idea_id = str(idea.get("id") or f"#{i}")
            ok, err = _validate_soft_novelty_fields(item, idea_id)
            if not ok:
                return False, err
            for field in _mechanism_fields:
                val = str(idea.get(field) or "").strip()
                if not val:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少必要字段 mechanism/{field}，"
                        "每个 idea 必须包含 mechanism, prediction, counterfactual, mechanism_family"
                    )
            decision = item.get("decision") or {}
            status = str(decision.get("status") or "").strip().lower()
            has_hypothesis_refs = bool(item.get("hypothesis_refs"))
            if status == "selected" or has_hypothesis_refs:
                cdr_tuple = idea.get("cdr_tuple") if isinstance(idea, dict) else {}
                if not isinstance(cdr_tuple, dict):
                    cdr_tuple = {}
                design_rationale = str(
                    cdr_tuple.get("design_rationale")
                    or idea.get("design_rationale")
                    or ""
                ).strip()
                contribution_type = str(
                    cdr_tuple.get("contribution_type")
                    or idea.get("contribution_type")
                    or ""
                ).strip().lower()
                contribution_character = str(
                    item.get("selection_rationale", {}).get("contribution_character")
                    or idea.get("contribution_character")
                    or ""
                ).strip()
                contribution_strength = (
                    idea.get("contribution_strength")
                    or item.get("scores", {}).get("contribution_strength")
                )
                if not design_rationale:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少 CDR design_rationale；"
                        "选中或进入最终假设的 idea 必须说明为什么 artifact 应该这样设计"
                    )
                if contribution_type not in {"invention", "improvement", "exaptation"}:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 的 contribution_type 不能为 "
                        f"{contribution_type or '空'}；selected idea 不能是 routine"
                    )
                if len(contribution_character) < 20:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少 contribution_character："
                        "必须回答如果成立领域会怎样不同"
                    )
                try:
                    strength_value = float(contribution_strength)
                except (TypeError, ValueError):
                    return False, f"idea_scorecard.yaml idea {idea_id} 缺少 contribution_strength"
                if strength_value < 2:
                    return False, f"idea_scorecard.yaml idea {idea_id} contribution_strength 过低"
                for field, placeholders in placeholder_values.items():
                    val = str(idea.get(field) or "").strip().lower()
                    if val in placeholders:
                        return False, (
                            f"idea_scorecard.yaml idea {idea_id} 的 {field} 仍是占位语；"
                            "选中或进入最终假设的 idea 必须给出具体机制、预测和反事实"
                        )
            elif status in {"rejected", "deferred", "merged"}:
                has_placeholder = any(
                    str(idea.get(field) or "").strip().lower() in placeholders
                    for field, placeholders in placeholder_values.items()
                )
                if has_placeholder:
                    reasons = " ".join(str(v) for v in (decision.get("rejection_reason") or []))
                    if not re.search(r"机制未成形|反事实|mechanism|counterfactual", reasons, re.IGNORECASE):
                        return False, (
                            f"idea_scorecard.yaml {status} idea {idea_id} 使用机制占位语，"
                            "必须在 rejection_reason 中说明机制未成形或无法形成可检验反事实"
                        )

        # R2: _family_distribution.md 必须存在且长度 > 100
        family_dist_path = ws / "ideation" / "_family_distribution.md"
        if not family_dist_path.exists():
            return False, "缺少 ideation/_family_distribution.md，必须在生成 scorecard 前写入 family distribution"
        family_dist_text = read_text_file(family_dist_path)
        if len(family_dist_text.strip()) < 100:
            return False, (
                f"ideation/_family_distribution.md 过短({len(family_dist_text.strip())} 字符)，"
                "至少需要 100 字符的 family 分布描述"
            )

        known_idea_ids: set[str] = set()
        selected_idea_ids: set[str] = set()
        rejected_or_deferred_ids: set[str] = set()
        selected_scorecard_refs: set[str] = set()
        for i, item in enumerate(scorecard_ideas, start=1):
            if not isinstance(item, dict):
                return False, f"idea_scorecard.yaml 第{i}条idea必须是对象"
            idea = item.get("idea") or {}
            idea_id = str(idea.get("id") or "").strip()
            if not idea_id:
                return False, f"idea_scorecard.yaml 第{i}条idea缺少idea.id"
            known_idea_ids.add(idea_id)
            decision = item.get("decision") or {}
            status = str(decision.get("status") or "").strip().lower()
            if status == "selected":
                selected_idea_ids.add(idea_id)
                selected_reasons = decision.get("selected_reason") or []
                if not selected_reasons:
                    return False, f"idea_scorecard.yaml 选中idea {idea_id} 缺少selected_reason"
                for ref in item.get("hypothesis_refs") or []:
                    selected_scorecard_refs.add(str(ref).lstrip("#").strip().upper())
            elif status in {"rejected", "deferred", "merged"}:
                rejected_or_deferred_ids.add(idea_id)
                rejection_reasons = decision.get("rejection_reason") or []
                if not rejection_reasons:
                    return False, f"idea_scorecard.yaml {status} idea {idea_id} 缺少rejection_reason"
            else:
                return False, f"idea_scorecard.yaml idea {idea_id} 的decision.status无效: {status}"

        if not selected_idea_ids:
            return False, "idea_scorecard.yaml 必须至少有一个 decision.status=selected 的idea"
        if not rejected_or_deferred_ids:
            return False, "idea_scorecard.yaml 必须记录至少一个被淘汰/暂缓/合并的idea及原因"
        try:
            pass1_data_for_scorecard = json.loads(
                (ws / "ideation" / "_pass1_forward_candidates.json").read_text(encoding="utf-8")
            )
        except Exception:
            pass1_data_for_scorecard = {}
        pass1_ids_for_scorecard = {
            str(candidate.get("id") or candidate.get("idea_id") or "").strip()
            for candidate in pass1_data_for_scorecard.get("candidates", [])
            if isinstance(candidate, dict)
        }
        missing_scorecard_candidates = sorted(pass1_ids_for_scorecard - known_idea_ids)
        if missing_scorecard_candidates:
            return False, (
                "idea_scorecard.yaml 必须记录 Pass1 全部候选，不能删除被 Pass2 筛掉的候选: "
                f"{missing_scorecard_candidates}"
            )
        missing_selected_refs = sorted(anchor_set - selected_scorecard_refs)
        if missing_selected_refs:
            return False, (
                "idea_scorecard.yaml 中选中idea的hypothesis_refs必须覆盖所有最终假设anchor，"
                f"缺少: {missing_selected_refs}"
            )

        coverage_result = analyze_ideation_coverage(ws)
        coverage = coverage_result.get("coverage", {}) if isinstance(coverage_result, dict) else {}
        origin_mix = coverage.get("origin_mix", {}) if isinstance(coverage, dict) else {}
        mainline_total = int(origin_mix.get("mainline_total") or 0)
        if mainline_total < 1:
            schema = load_cdr_schema()
            mainline = ", ".join((schema.get("idea_origins") or {}).get("mainline") or [])
            return False, f"idea_scorecard.yaml 至少需要一个 CDR 主线idea（{mainline}）"
        if origin_mix.get("supplement_only_risk") is True:
            return False, "idea_scorecard.yaml 不能全部由四类补充候选构成，必须保留主线LLM推理idea"

        rejected_path = ws / "ideation" / "rejected_ideas.md"
        rejected_text = read_text_file(rejected_path)
        if not rejected_path.exists():
            return False, "缺少 ideation/rejected_ideas.md，无法记录淘汰idea原因"
        if len(rejected_text.strip()) < 100:
            return False, "rejected_ideas.md 过短，必须解释被淘汰/暂缓idea的原因"
        missing_rejected_mentions = [
            idea_id for idea_id in sorted(rejected_or_deferred_ids) if idea_id not in rejected_text
        ]
        if missing_rejected_mentions:
            return False, f"rejected_ideas.md 必须提到这些被淘汰/暂缓idea: {missing_rejected_mentions}"

        gate_path = ws / "ideation" / "gate_decisions.json"
        if not gate_path.exists():
            return False, "缺少 ideation/gate_decisions.json，无法追踪Gate决策链"
        try:
            gate_data = json.loads(gate_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"gate_decisions.json 解析失败: {e}"
        if not isinstance(gate_data, dict):
            return False, "gate_decisions.json 必须是JSON对象"
        ok, err = validate_record(gate_data, "gate_decisions")
        if not ok:
            return False, f"gate_decisions.json 不符合schema: {err}"
        decisions = gate_data.get("decisions", [])
        gate_ids = {str(item.get("gate_id") or "") for item in decisions if isinstance(item, dict)}
        required_gates = {"T4-DECIDE-1", "T4-DECIDE-2"}
        missing_gates = sorted(required_gates - gate_ids)
        if missing_gates:
            return False, f"gate_decisions.json 必须记录两轮Gate决策，缺少: {missing_gates}"
        gate_selected_ids: set[str] = set()
        gate_rejected_ids: set[str] = set()
        merged_sources_in_gate: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            gate_selected_ids.update(str(v).strip() for v in item.get("selected_idea_ids") or [] if str(v).strip())
            gate_rejected_ids.update(str(v).strip() for v in item.get("rejected_idea_ids") or [] if str(v).strip())
            gate_rejected_ids.update(str(v).strip() for v in item.get("deferred_idea_ids") or [] if str(v).strip())
            for merge in item.get("merged_idea_ids") or []:
                if isinstance(merge, (list, tuple)):
                    merged_sources_in_gate.update(str(v).strip() for v in merge if str(v).strip())
                elif isinstance(merge, dict):
                    merged_sources_in_gate.update(
                        str(v).strip()
                        for v in merge.get("from") or merge.get("source_idea_ids") or []
                        if str(v).strip()
                    )
        unknown_gate_ids = sorted((gate_selected_ids | gate_rejected_ids) - known_idea_ids)
        if unknown_gate_ids:
            return False, f"gate_decisions.json 引用了scorecard中不存在的idea_id: {unknown_gate_ids}"
        if not selected_idea_ids.issubset(gate_selected_ids):
            return False, "gate_decisions.json 必须记录scorecard中所有selected idea"
        if not rejected_or_deferred_ids.intersection(gate_rejected_ids):
            return False, "gate_decisions.json 必须记录至少一个被淘汰/暂缓idea"

        merged_scorecard_ids = {
            str((item.get("idea") or {}).get("id") or "").strip()
            for item in scorecard_ideas
            if isinstance(item, dict)
            and str((item.get("decision") or {}).get("status") or "").strip().lower() == "merged"
        }
        if merged_scorecard_ids and not merged_scorecard_ids.issubset(gate_rejected_ids | merged_sources_in_gate):
            return False, (
                "gate_decisions.json 必须记录被合并的原始idea，缺少: "
                f"{sorted(merged_scorecard_ids - (gate_rejected_ids | merged_sources_in_gate))}"
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


def _validate_candidate_directions(ws: Path) -> tuple[bool, str | None]:
    candidate_path = ws / "ideation" / "_candidate_directions.json"
    if not candidate_path.exists():
        return False, "缺少 ideation/_candidate_directions.json，必须记录主线与补充候选方向池"
    try:
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_candidate_directions.json 解析失败: {exc}"
    candidates = candidate_data.get("candidates") if isinstance(candidate_data, dict) else None
    if not isinstance(candidates, list) or len(candidates) < 4:
        return False, "_candidate_directions.json 必须包含至少4个候选方向"

    cdr_schema = load_cdr_schema()
    origins = cdr_schema.get("idea_origins") or {}
    mainline_origins = set(origins.get("mainline") or [
        "free_reasoning",
        "seed_refinement",
        "seed_derived",
        "evidence_driven",
    ])
    supplement_origins = set(origins.get("supplement") or [
        "mechanism_challenge",
        "reverse_operation",
        "subgroup_failure",
        "missing_area_exploration",
        "gap_exploration",
    ])
    mainline_count = 0
    supplement_count = 0
    ids: set[str] = set()
    for idx, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            return False, f"_candidate_directions.json 第{idx}条候选必须是对象"
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not idea_id:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 id/idea_id"
        if idea_id in ids:
            return False, f"_candidate_directions.json 候选ID重复: {idea_id}"
        ids.add(idea_id)
        origin = str(candidate.get("idea_origin") or candidate.get("origin") or "").strip()
        status = str(candidate.get("constraint_status") or "").strip()
        basis = str(candidate.get("basis_summary") or candidate.get("basis") or "").strip()
        if not origin:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 idea_origin"
        if not status:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 constraint_status"
        if len(basis) < 20:
            return False, f"_candidate_directions.json 第{idx}条候选 basis_summary 过短"
        if origin in mainline_origins or status == "mainline":
            mainline_count += 1
        if origin in supplement_origins or status == "supplement":
            supplement_count += 1
        pass2 = candidate.get("pass2_screening") or {}
        if pass2:
            visible = pass2.get("visible_to_gate")
            gate_visibility = str(candidate.get("gate_visibility") or "").strip().lower()
            if visible is False or gate_visibility == "hidden":
                return False, (
                    f"_candidate_directions.json 候选 {idea_id} 被 Pass2 隐藏；"
                    "Pass2 只能标风险，不能从 Gate1 删除候选"
                )
        if status == "not_supported_by_current_evidence" and origin not in supplement_origins:
            return False, (
                f"_candidate_directions.json 第{idx}条 unsupported 候选必须对应四类补充通道，"
                "不能把主线候选标成无证据"
            )

    if mainline_count < 2:
        return False, (
            "_candidate_directions.json 至少需要2个 CDR 主线候选，不能只靠四类补充；"
            f"合法主线 origins: {sorted(mainline_origins)}"
        )
    if supplement_count > mainline_count + 4:
        return False, "_candidate_directions.json 四类补充候选过多，主线推理被覆盖"
    return True, None


def _validate_pass_stage_artifacts(ws: Path) -> tuple[bool, str | None]:
    """Validate that T4 exposes both generation and grounding stages to Gate1."""

    ideation_dir = ws / "ideation"
    pass1_path = ideation_dir / "_pass1_forward_candidates.json"
    pass2_path = ideation_dir / "_pass2_grounding_review.json"
    candidate_path = ideation_dir / "_candidate_directions.json"
    gate_brief_path = ideation_dir / "_gate1_selection_brief.md"

    for path, label in [
        (pass1_path, "_pass1_forward_candidates.json"),
        (pass2_path, "_pass2_grounding_review.json"),
        (gate_brief_path, "_gate1_selection_brief.md"),
    ]:
        if not path.exists():
            return False, f"缺少 ideation/{label}，T4 必须暴露 Pass1/Pass2 和 Gate1 全量候选"

    try:
        pass1_data = json.loads(pass1_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_pass1_forward_candidates.json 解析失败: {exc}"
    try:
        pass2_data = json.loads(pass2_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_pass2_grounding_review.json 解析失败: {exc}"
    try:
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_candidate_directions.json 解析失败: {exc}"

    pass1_candidates = pass1_data.get("candidates") if isinstance(pass1_data, dict) else None
    if not isinstance(pass1_candidates, list) or len(pass1_candidates) < 4:
        return False, "_pass1_forward_candidates.json 必须包含至少4个 Pass1 原始候选"

    pass1_ids: set[str] = set()
    for idx, candidate in enumerate(pass1_candidates, start=1):
        if not isinstance(candidate, dict):
            return False, f"_pass1_forward_candidates.json 第{idx}条候选必须是对象"
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not idea_id:
            return False, f"_pass1_forward_candidates.json 第{idx}条候选缺少 id"
        if idea_id in pass1_ids:
            return False, f"_pass1_forward_candidates.json 候选ID重复: {idea_id}"
        pass1_ids.add(idea_id)
        if not str(candidate.get("idea_origin") or candidate.get("origin") or "").strip():
            return False, f"_pass1_forward_candidates.json 候选 {idea_id} 缺少 idea_origin"

    reviews = pass2_data.get("reviews") if isinstance(pass2_data, dict) else None
    if not isinstance(reviews, list):
        return False, "_pass2_grounding_review.json 必须包含 reviews 数组"
    pass2_ids: set[str] = set()
    for idx, review in enumerate(reviews, start=1):
        if not isinstance(review, dict):
            return False, f"_pass2_grounding_review.json 第{idx}条review必须是对象"
        idea_id = str(review.get("idea_id") or review.get("id") or "").strip()
        if not idea_id:
            return False, f"_pass2_grounding_review.json 第{idx}条review缺少 idea_id"
        pass2_ids.add(idea_id)
        ok, err = _validate_pass2_soft_diagnostics(review, idea_id)
        if not ok:
            return False, err
        if review.get("visible_to_gate") is False:
            return False, (
                f"_pass2_grounding_review.json review {idea_id} visible_to_gate=false；"
                "Pass2 不能隐藏候选"
            )
        recommendation = str(review.get("screening_recommendation") or "").strip()
        if recommendation not in {
            "proceed",
            "revise_before_selection",
            "defer_recommended",
            "reject_recommended",
        }:
            return False, (
                f"_pass2_grounding_review.json review {idea_id} screening_recommendation 无效: "
                f"{recommendation or '空'}"
            )

    missing_reviews = sorted(pass1_ids - pass2_ids)
    if missing_reviews:
        return False, f"_pass2_grounding_review.json 未覆盖这些 Pass1 候选: {missing_reviews}"

    candidates = candidate_data.get("candidates") if isinstance(candidate_data, dict) else None
    candidate_ids = {
        str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        for candidate in candidates or []
        if isinstance(candidate, dict)
    }
    missing_gate_candidates = sorted(pass1_ids - candidate_ids)
    if missing_gate_candidates:
        return False, (
            "_candidate_directions.json 必须保留 Pass1 全部候选，不能因 Pass2 筛选删除: "
            f"{missing_gate_candidates}"
        )

    brief_text = read_text_file(gate_brief_path)
    if len(brief_text.strip()) < 300:
        return False, "_gate1_selection_brief.md 过短，必须展示全量候选、Pass2风险和合并建议"
    missing_from_brief = [idea_id for idea_id in sorted(pass1_ids) if idea_id not in brief_text]
    if missing_from_brief:
        return False, f"_gate1_selection_brief.md 必须提到所有候选ID，缺少: {missing_from_brief}"
    if not re.search(r"合并|merge|D\d+\+D\d+", brief_text, re.IGNORECASE):
        return False, "_gate1_selection_brief.md 必须说明可合并多个候选，例如 合并 D1+D3"
    required_soft_sections = [
        ("集中度提示", r"集中度|concentration"),
        ("Origin 分布", r"Origin\s*分布|origin\s+distribution|origin mix"),
        ("Novelty-Utility 谱系排布", r"Novelty[-– ]Utility|新颖度.*可行|新颖.*效用"),
    ]
    missing_soft = [
        label for label, pattern in required_soft_sections
        if not re.search(pattern, brief_text, re.IGNORECASE)
    ]
    if missing_soft:
        return False, "_gate1_selection_brief.md 缺少软提示章节: " + ", ".join(missing_soft)

    return True, None


def _validate_pass2_soft_diagnostics(review: dict, idea_id: str) -> tuple[bool, str | None]:
    """Pass2 must expose soft diagnostics before Gate1, without using them as gates."""

    counterfactual_values = {"collapses", "survives_weakened", "independent", "insufficient_evidence"}
    distance_values = {"very_close", "moderate", "distant", "none_found", "not_computed"}
    novelty_values = {"marginal_zone", "adjacent_zone", "no_nearby_cluster", "not_computed", "domain_map_unavailable"}

    counterfactual_check = review.get("counterfactual_check")
    if counterfactual_check not in counterfactual_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} 缺少合法 counterfactual_check；"
            "Pass2 必须在 Gate1 前标注该软信号或说明 insufficient_evidence"
        )
    counterfactual_note = str(review.get("counterfactual_note") or "").strip()
    if len(counterfactual_note) < 8:
        return False, f"_pass2_grounding_review.json review {idea_id} counterfactual_note 过短"
    nearest = review.get("nearest_prior_work")
    if not isinstance(nearest, dict):
        return False, f"_pass2_grounding_review.json review {idea_id} 缺少 nearest_prior_work"
    distance = str(nearest.get("distance") or "").strip()
    if distance not in distance_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} nearest_prior_work.distance 无效: "
            f"{distance or '空'}"
        )
    if "work" not in nearest:
        return False, f"_pass2_grounding_review.json review {idea_id} nearest_prior_work 缺少 work"
    novelty_signal = review.get("novelty_signal")
    if novelty_signal not in novelty_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} 缺少合法 novelty_signal；"
            "该字段只是引用图近邻参考信号，不是 gate；图谱不可用时写 domain_map_unavailable/not_computed"
        )
    return True, None


def _validate_soft_novelty_fields(item: dict, idea_id: str) -> tuple[bool, str | None]:
    """Ensure soft diagnostic fields are present without turning them into gates."""

    counterfactual_values = {"collapses", "survives_weakened", "independent", "insufficient_evidence"}
    distance_values = {"very_close", "moderate", "distant", "none_found", "not_computed"}
    novelty_values = {"marginal_zone", "adjacent_zone", "no_nearby_cluster", "not_computed", "domain_map_unavailable"}

    idea = item.get("idea") if isinstance(item.get("idea"), dict) else {}
    counterfactual_check = item.get("counterfactual_check") or idea.get("counterfactual_check")
    if counterfactual_check not in counterfactual_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} 缺少合法 counterfactual_check；"
            "该字段是软提示，可取 collapses/survives_weakened/independent/insufficient_evidence"
        )
    counterfactual_note = str(item.get("counterfactual_note") or idea.get("counterfactual_note") or "").strip()
    if len(counterfactual_note) < 8:
        return False, f"idea_scorecard.yaml idea {idea_id} counterfactual_note 过短"
    nearest = item.get("nearest_prior_work") or idea.get("nearest_prior_work")
    if not isinstance(nearest, dict):
        return False, f"idea_scorecard.yaml idea {idea_id} 缺少 nearest_prior_work"
    distance = str(nearest.get("distance") or "").strip()
    if distance not in distance_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} nearest_prior_work.distance 无效: "
            f"{distance or '空'}"
        )
    if "work" not in nearest:
        return False, f"idea_scorecard.yaml idea {idea_id} nearest_prior_work 缺少 work"
    novelty_signal = item.get("novelty_signal") or idea.get("novelty_signal")
    if novelty_signal not in novelty_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} 缺少合法 novelty_signal；"
            "该字段只是引用图近邻参考信号，不是 gate；图谱不可用时写 domain_map_unavailable/not_computed"
        )
    return True, None
