"""T4 Ideation Agent 单元测试。

测试覆盖：
1. AgentSpec配置
2. system_prompt生成
3. initial_user_message生成
4. validate_outputs - 成功场景
5. validate_outputs - 各种失败场景
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.ideation import (
    IdeationAgent,
    _validate_bridge_coverage_review,
    _validate_candidate_directions,
    validate_t4_gate1_ready,
)
from researchos.runtime.agent import ExecutionContext
from researchos.orchestration.task_io_contract import TASK_IO_CONTRACTS
from researchos.schemas.validator import validate_record


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_fingerprint(payload: dict) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _gate1_candidate_pool_fingerprints(workspace: Path) -> dict:
    paths = {
        "pass1_forward_candidates": "ideation/_pass1_forward_candidates.json",
        "pass2_grounding_review": "ideation/_pass2_grounding_review.json",
        "candidate_directions": "ideation/_candidate_directions.json",
        "gate1_selection_brief": "ideation/_gate1_selection_brief.md",
        "bridge_coverage_review": "ideation/bridge_coverage_review.json",
    }
    result = {}
    for label, rel in paths.items():
        path = workspace / rel
        item = {"path": rel, "exists": path.exists()}
        if path.exists() and path.is_file():
            item["sha256"] = _file_sha256(path)
            item["size"] = path.stat().st_size
        result[label] = item
    return result


def _write_gate1_selection(workspace: Path, *, selected_option: str = "select_direction") -> str:
    captured = {"selected_idea_ids": ["D1"], "user_feedback": "选择 D1"}
    pool = _gate1_candidate_pool_fingerprints(workspace)
    payload_for_hash = {
        "semantics": "t4_gate1_selection_fingerprint",
        "gate_id": "t4_gate1",
        "selected_option": selected_option,
        "captured": captured,
        "candidate_pool_fingerprints": pool,
    }
    fingerprint = _stable_json_fingerprint(payload_for_hash)
    payload = {
        "semantics": "t4_gate1_user_selection_for_candidate_pool",
        "task_id": "T4-GATE1",
        "gate_id": "t4_gate1",
        "selected_option": selected_option,
        "captured": captured,
        "candidate_pool_fingerprints": pool,
        "selection_fingerprint": fingerprint,
        "next_task": "T4",
        "decided_at": "2026-01-01T00:00:00+00:00",
    }
    path = workspace / "ideation" / "_gate1_user_selection.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fingerprint


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "ideation").mkdir()

    return workspace


@pytest.fixture
def ideation_agent():
    """创建Ideation Agent实例。"""
    return IdeationAgent()


def write_valid_idea_rationales(workspace: Path, refs: list[str] | None = None) -> None:
    """写入覆盖假设anchor的idea依据与决策链记录。"""
    refs = refs or ["H1"]
    pass1_candidates = [
        {
            "id": "D1",
            "title": "测试假设依据",
            "generation_stage": "mainline",
            "idea_origin": "free_reasoning",
            "constraint_status": "mainline",
            "pitch": "基于综述缺口提出一个可验证假设。",
            "core_claim": "目标机制可以改善可观测指标。",
            "mechanism": "通过正则化梯度范数改善稀疏用户嵌入质量",
            "prediction": "在稀疏用户子群上 Recall@20 提升 5%+",
            "counterfactual": "如果机制不成立，选择性噪声关闭后指标应无显著差异",
            "basis_summary": "LLM 综合 synthesis、comparison_table 和最终研究问题后提出的主线候选方向。",
        },
        {
            "id": "D1b",
            "title": "基于证据的替代候选",
            "generation_stage": "mainline",
            "idea_origin": "cross_domain_analogy",
            "constraint_status": "mainline",
            "pitch": "从 paper notes 的共同限制形成替代方向。",
            "core_claim": "证据驱动的机制干预能改善目标指标。",
            "mechanism": "针对共同失败模式调整训练信号可降低目标误差",
            "prediction": "目标失败子群上的 accuracy 相对 baseline 提升",
            "counterfactual": "若失败来自数据噪声而非训练信号，干预不会改善子群指标",
            "basis_summary": "从 paper notes 的共同限制和实验可行性出发形成的第二个主线候选。",
        },
        {
            "id": "D2",
            "title": "被淘汰方向",
            "generation_stage": "mainline",
            "idea_origin": "seed_refinement",
            "constraint_status": "mainline",
            "pitch": "把已有方法直接迁移到新场景。",
            "core_claim": "简单迁移也许可以提升指标。",
            "mechanism": "直接迁移在新场景中复用已有表示偏置影响目标指标",
            "prediction": "如果迁移偏置有用，新场景accuracy应相对baseline提升",
            "counterfactual": "如果迁移偏置无效，替换为简单baseline后指标不会下降",
            "basis_summary": "由用户 seed idea 细化而来，但因新颖性和评价链条不足被淘汰。",
        },
        {
            "id": "S1",
            "title": "反向操作补充候选",
            "generation_stage": "supplement",
            "idea_origin": "reverse_operation",
            "constraint_status": "supplement",
            "pitch": "检查移除关键机制时指标是否下降。",
            "core_claim": "反向操作可以检验机制是否必要。",
            "mechanism": "移除常规增强后若指标不降说明原增强并非关键机制",
            "prediction": "关闭增强后目标指标保持稳定或仅轻微下降",
            "counterfactual": "若增强确实必要，关闭后目标指标显著下降",
            "basis_summary": "作为 coverage supplement，检查移除关键机制时指标是否下降。",
        },
    ]
    pass2_reviews = [
        {
            "idea_id": "D1",
            "screening_recommendation": "proceed",
            "visible_to_gate": True,
            "counterfactual_check": "independent",
            "counterfactual_note": "抽掉最近论文后仍有独立机制论证。",
            "nearest_prior_work": {"work": "Smith2024", "distance": "moderate"},
            "novelty_signal": "adjacent_zone",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "medium"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": False,
                "why": "有明确机制和子群评价。",
            },
            "grounding_notes": ["可以进入 Gate1。"],
            "selection_warning": "none",
        },
        {
            "idea_id": "D1b",
            "screening_recommendation": "defer_recommended",
            "visible_to_gate": True,
            "counterfactual_check": "survives_weakened",
            "counterfactual_note": "抽掉最近工作后仍成立但证据会弱化。",
            "nearest_prior_work": {"work": "Nearby Paper", "distance": "distant"},
            "novelty_signal": "no_nearby_cluster",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "high_uncertainty"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": True,
                "why": "需要进一步收紧机制。",
            },
            "grounding_notes": ["可见但建议暂缓。"],
            "selection_warning": "若选择需要重构机制。",
        },
        {
            "idea_id": "D2",
            "screening_recommendation": "reject_recommended",
            "visible_to_gate": True,
            "counterfactual_check": "collapses",
            "counterfactual_note": "抽掉最相近工作后只剩应用迁移。",
            "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
            "novelty_signal": "marginal_zone",
            "novelty_check": {"prior_art": "closest_known", "closest_baselines": ["Nearby Paper"], "novelty_risk": "low"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "routine",
                "routine_risk": True,
                "reframe_needed": True,
                "why": "贡献更像应用迁移。",
            },
            "grounding_notes": ["Pass2 建议淘汰，但 Gate1 仍可见。"],
            "selection_warning": "若选择必须先重构 contribution character。",
        },
        {
            "idea_id": "S1",
            "screening_recommendation": "revise_before_selection",
            "visible_to_gate": True,
            "counterfactual_check": "survives_weakened",
            "counterfactual_note": "作为反向操作仍可服务机制检验。",
            "nearest_prior_work": {"work": "none", "distance": "none_found"},
            "novelty_signal": "no_nearby_cluster",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "medium"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": True,
                "why": "需要从 ablation 重构成机制检验。",
            },
            "grounding_notes": ["补充候选可见。"],
            "selection_warning": "选择前要说明为何不是普通消融。",
        },
    ]
    scorecard = {
        "version": "1.0",
        "ideas": [
            {
                "idea": {
                    "id": "D1",
                    "title": "测试假设依据",
                    "pitch": "基于综述缺口提出一个可验证假设。",
                    "core_claim": "目标机制可以改善可观测指标。",
                    "target_problem": "现有方法在目标场景下存在明确短板。",
                    "mechanism": "通过正则化梯度范数改善稀疏用户嵌入质量",
                    "prediction": "在稀疏用户子群上 Recall@20 提升 5%+",
                    "counterfactual": "如果机制不成立，选择性噪声关闭后指标应无显著差异",
                    "mechanism_family": "selective noise application",
                    "cdr_tuple": {
                        "problem_frame": "现有方法在目标场景下存在明确短板。",
                        "design_rationale": "按用户状态调节机制强度可以更直接地检验稀疏子群的表示质量。",
                        "artifact": "一个选择性机制调节模块。",
                        "design_principles": ["隔离机制", "保留简单基线"],
                        "data_view": "按子群切分的验证集。",
                        "evaluation_mode": "主指标加机制消融。",
                        "contribution_type": "improvement",
                        "boundary_conditions": ["目标场景具有可观测子群差异"],
                        "cross_paper_tension": ["统一机制假设与子群失败之间的张力"],
                    },
                    "contribution_strength": 4,
                },
                "hypothesis_refs": refs,
                "source": {
                    "idea_origin": "free_reasoning",
                    "constraint_status": "mainline",
                    "from_synthesis_section": "literature/synthesis.md: Q1",
                    "from_missing_area": "missing_areas.md: 需要机制验证",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [
                        {
                            "title": "Test Paper",
                            "claim_used": "现有方法在目标约束下存在缺口。",
                        }
                    ],
                    "trigger_observation": "对比表显示基线未覆盖该约束。",
                },
                "selection_rationale": {
                    "novelty_reason": "现有工作没有系统验证该机制。",
                    "feasibility_reason": "可以用小规模数据和基线先做验证。",
                    "impact_reason": "该问题影响后续系统可靠性。",
                    "evaluability_reason": "可以用accuracy和cost指标验证。",
                    "paper_story": "问题、方法和实验链路清楚。",
                    "contribution_character": "如果该假设成立，领域会从统一机制默认设置转向按子群状态调节机制强度的设计原则。",
                },
                "closest_baselines": [
                    {
                        "name": "baseline1",
                        "similarity": "都处理同一目标任务。",
                        "difference": "本idea强调机制验证和成本约束。",
                    }
                ],
                "counterfactual_check": "independent",
                "counterfactual_note": "抽掉单篇相关论文后，该方向仍可由综述张力和问题重构独立推出。",
                "nearest_prior_work": {"work": "baseline1", "distance": "moderate"},
                "novelty_signal": "adjacent_zone",
                "scores": {
                    "novelty": 4,
                    "feasibility": 4,
                    "impact": 4,
                    "evaluability": 5,
                    "differentiation": 3,
                    "cost": 5,
                    "contribution_strength": 4,
                },
                "decision": {
                    "status": "selected",
                    "selected_reason": [
                        "和研究问题最一致",
                        "有清晰baseline和指标",
                    ],
                    "selected_by": "user",
                    "user_feedback": "继续聚焦这个可验证方向。",
                },
                "risks": [
                    {
                        "risk": "机制收益不明显",
                        "early_signal": "pilot指标接近baseline",
                        "mitigation": "增加消融和错误分析",
                        "kill_criteria": "若不优于简单baseline则停止",
                    }
                ],
                "minimum_experiment": {
                    "dataset": "test validation set",
                    "baseline": "baseline1",
                    "metric": ["accuracy", "cost"],
                    "expected_signal": "同等成本下accuracy提升",
                    "estimated_cost_usd": 10.0,
                },
            },
            {
                "idea": {
                    "id": "D1b",
                    "title": "基于证据的替代候选",
                    "pitch": "从 paper notes 的共同限制形成替代方向。",
                    "core_claim": "证据驱动的机制干预能改善目标指标。",
                    "target_problem": "共同失败模式尚未被验证。",
                    "mechanism": "针对共同失败模式调整训练信号可降低目标误差",
                    "prediction": "目标失败子群上的 accuracy 相对 baseline 提升",
                    "counterfactual": "若失败来自数据噪声而非训练信号，干预不会改善子群指标",
                    "mechanism_family": "failure-mode intervention",
                    "cdr_tuple": {
                        "problem_frame": "共同失败模式尚未被验证。",
                        "design_rationale": "从失败模式出发能更直接定位机制。",
                        "artifact": "失败模式干预模块。",
                        "design_principles": ["机制定位"],
                        "data_view": "失败子群验证集。",
                        "evaluation_mode": "子群指标加消融。",
                        "contribution_type": "improvement",
                        "boundary_conditions": ["失败模式可观测"],
                    },
                    "contribution_strength": 2,
                },
                "hypothesis_refs": [],
                "source": {
                    "idea_origin": "cross_domain_analogy",
                    "constraint_status": "mainline",
                    "from_synthesis_section": "literature/synthesis.md: Q1",
                    "from_missing_area": "missing_areas.md: 失败子群",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [{"title": "Failure Paper", "claim_used": "存在共同失败模式。"}],
                    "trigger_observation": "paper notes 显示共同失败模式但机制未验证。",
                },
                "selection_rationale": {
                    "novelty_reason": "需要进一步收紧机制。",
                    "feasibility_reason": "可用小规模失败子群测试。",
                    "impact_reason": "有潜在价值但尚不如 D1 清楚。",
                    "evaluability_reason": "可评价但指标链较弱。",
                    "contribution_character": "如果成立，会把失败模式从现象描述推进到可干预机制。",
                },
                "closest_baselines": [],
                "counterfactual_check": "survives_weakened",
                "counterfactual_note": "抽掉失败模式论文后，该方向仍可成立但经验支撑会变弱。",
                "nearest_prior_work": {"work": "Failure Paper", "distance": "moderate"},
                "novelty_signal": "adjacent_zone",
                "scores": {
                    "novelty": 3,
                    "feasibility": 3,
                    "impact": 3,
                    "evaluability": 3,
                    "differentiation": 3,
                    "cost": 4,
                    "contribution_strength": 2,
                },
                "decision": {
                    "status": "deferred",
                    "rejection_reason": ["Pass2 建议暂缓，需要进一步收紧机制。"],
                    "can_revisit_if": "如果 D1 pilot 失败但失败子群信号强，可以重访。",
                },
                "risks": [
                    {
                        "risk": "机制过宽",
                        "early_signal": "多个失败解释都成立",
                        "mitigation": "缩小子群",
                        "kill_criteria": "无法形成单一反事实",
                    }
                ],
                "minimum_experiment": {
                    "dataset": "failure subset",
                    "baseline": "baseline1",
                    "metric": ["accuracy"],
                    "expected_signal": "失败子群指标提升",
                    "estimated_cost_usd": 6.0,
                },
            },
            {
                "idea": {
                    "id": "D2",
                    "title": "被淘汰方向",
                    "pitch": "把已有方法直接迁移到新场景。",
                    "core_claim": "简单迁移也许可以提升指标。",
                    "target_problem": "另一个较弱的问题设定。",
                    "mechanism": "直接迁移在新场景中复用已有表示偏置影响目标指标",
                    "prediction": "如果迁移偏置有用，新场景accuracy应相对baseline提升",
                    "counterfactual": "如果迁移偏置无效，替换为简单baseline后指标不会下降",
                    "mechanism_family": "direct transfer",
                    "cdr_tuple": {
                        "problem_frame": "另一个较弱的问题设定。",
                        "design_rationale": "直接迁移已有方法不改变核心设计逻辑。",
                        "artifact": "场景迁移 baseline。",
                        "data_view": "常规验证集。",
                        "evaluation_mode": "主指标比较。",
                        "contribution_type": "routine",
                        "boundary_conditions": ["仅作为 rejected idea 记录"],
                    },
                    "contribution_strength": 1,
                },
                "hypothesis_refs": [],
                "source": {
                    "idea_origin": "seed_refinement",
                    "constraint_status": "mainline",
                    "from_synthesis_section": "literature/synthesis.md: Q2",
                    "from_missing_area": "missing_areas.md: 评价指标不清楚",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [
                        {
                            "title": "Nearby Paper",
                            "claim_used": "已有方法已经覆盖主要机制。",
                        }
                    ],
                    "trigger_observation": "该方向来自对一个弱缺口的直接外推。",
                },
                "selection_rationale": {
                    "novelty_reason": "新颖性较弱。",
                    "feasibility_reason": "实现可行但贡献有限。",
                    "impact_reason": "影响范围较窄。",
                    "evaluability_reason": "缺少稳定评价指标。",
                    "paper_story": "论文故事更像工程迁移。",
                    "contribution_character": "如果成立也主要是应用迁移，不会改变领域对机制设计的理解。",
                },
                "closest_baselines": [
                    {
                        "name": "Nearby Paper",
                        "similarity": "机制和目标都很接近。",
                        "difference": "差异主要是场景变化。",
                    }
                ],
                "counterfactual_check": "collapses",
                "counterfactual_note": "抽掉 Nearby Paper 后，该方向基本只剩应用迁移，独立设计论证不足。",
                "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
                "novelty_signal": "marginal_zone",
                "scores": {
                    "novelty": 2,
                    "feasibility": 4,
                    "impact": 2,
                    "evaluability": 2,
                    "differentiation": 2,
                    "cost": 4,
                    "contribution_strength": 1,
                },
                "decision": {
                    "status": "rejected",
                    "rejection_reason": [
                        "和已有工作太接近",
                        "缺少清晰评价指标",
                    ],
                    "can_revisit_if": "如果找到更强的差异化机制和数据集，可以重访。",
                },
                "risks": [
                    {
                        "risk": "创新性不足",
                        "early_signal": "T4.5发现高重叠工作",
                        "mitigation": "重新寻找机制差异",
                        "kill_criteria": "若差异只剩应用场景变化则放弃",
                    }
                ],
                "minimum_experiment": {
                    "dataset": "small proxy set",
                    "baseline": "Nearby Paper",
                    "metric": ["accuracy"],
                    "expected_signal": "需要显著优于已有方法",
                    "estimated_cost_usd": 8.0,
                },
            },
            {
                "idea": {
                    "id": "S1",
                    "title": "反向操作补充候选",
                    "pitch": "检查移除关键机制时指标是否下降。",
                    "core_claim": "反向操作可以检验机制是否必要。",
                    "target_problem": "常规增强是否真是必要机制。",
                    "mechanism": "移除常规增强后若指标不降说明原增强并非关键机制",
                    "prediction": "关闭增强后目标指标保持稳定或仅轻微下降",
                    "counterfactual": "若增强确实必要，关闭后目标指标显著下降",
                    "mechanism_family": "reverse operation",
                    "cdr_tuple": {
                        "problem_frame": "常规增强是否真是必要机制。",
                        "design_rationale": "反向操作可区分机制必要性和表面增益。",
                        "artifact": "反向操作实验。",
                        "design_principles": ["必要性检验"],
                        "data_view": "主验证集。",
                        "evaluation_mode": "消融式机制检验。",
                        "contribution_type": "improvement",
                        "boundary_conditions": ["增强可被独立关闭"],
                    },
                    "contribution_strength": 2,
                },
                "hypothesis_refs": [],
                "source": {
                    "idea_origin": "reverse_operation",
                    "constraint_status": "supplement",
                    "from_synthesis_section": "literature/synthesis.md: mechanism cluster",
                    "from_missing_area": "none",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [{"title": "Ablation Paper", "claim_used": "增强常被默认开启。"}],
                    "trigger_observation": "多个方法默认添加增强但缺少必要性检验。",
                },
                "selection_rationale": {
                    "novelty_reason": "作为机制补充有价值。",
                    "feasibility_reason": "反向操作成本低。",
                    "impact_reason": "单独成文较弱。",
                    "evaluability_reason": "消融可测。",
                    "contribution_character": "如果成立，会削弱现有方法对默认增强必要性的解释。",
                },
                "closest_baselines": [],
                "counterfactual_check": "survives_weakened",
                "counterfactual_note": "抽掉具体消融论文后，反向操作仍可作为机制检验，但支撑较弱。",
                "nearest_prior_work": {"work": "Ablation Paper", "distance": "distant"},
                "novelty_signal": "no_nearby_cluster",
                "scores": {
                    "novelty": 3,
                    "feasibility": 5,
                    "impact": 2,
                    "evaluability": 5,
                    "differentiation": 3,
                    "cost": 5,
                    "contribution_strength": 2,
                },
                "decision": {
                    "status": "deferred",
                    "rejection_reason": ["适合作为 D1 的消融补充，不单独作为主线。"],
                    "can_revisit_if": "如果反向操作出现强信号，可以合并进主假设。",
                },
                "risks": [
                    {
                        "risk": "只是普通消融",
                        "early_signal": "没有机制解释",
                        "mitigation": "绑定反事实预测",
                        "kill_criteria": "无法区分机制必要性",
                    }
                ],
                "minimum_experiment": {
                    "dataset": "test validation set",
                    "baseline": "baseline1",
                    "metric": ["accuracy"],
                    "expected_signal": "关闭增强后指标变化可解释",
                    "estimated_cost_usd": 4.0,
                },
            },
        ],
    }
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(scorecard, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_family_distribution.md").write_text(
        """## Mechanism Family Distribution

### Family: selective noise application
- Candidates: D1
- Mechanism similarity notes: single candidate, no overlap concern

### Family: direct transfer
- Candidates: D2
- Mechanism similarity notes: single candidate, no overlap concern

## Summary

- Total candidates: 2
- Distinct families: 2
- Families with multiple candidates: 0

## Recommended for Gate1 review

Both families are distinct. D1 focuses on mechanism verification while D2 is a direct transfer approach.
""",
        encoding="utf-8",
    )
    pass1_payload = {
        "version": "1.0",
        "semantics": "raw_forward_generation_candidates_visible_to_gate",
        "candidates": pass1_candidates,
    }
    (workspace / "ideation" / "_pass1_forward_candidates.json").write_text(
        json.dumps(pass1_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_pass2_grounding_review.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "grounding_review_flags_not_deletion_or_final_quality_gate",
                "reviews": pass2_reviews,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    candidate_payload = {
        "version": "1.0",
        "semantics": "gate_visible_candidate_pool_after_grounding_review",
        "candidates": [
            {
                **candidate,
                "pass2_screening": {
                    "screening_recommendation": next(
                        review["screening_recommendation"]
                        for review in pass2_reviews
                        if review["idea_id"] == candidate["id"]
                    ),
                    "visible_to_gate": True,
                    "selection_warning": next(
                        review["selection_warning"]
                        for review in pass2_reviews
                        if review["idea_id"] == candidate["id"]
                    ),
                },
                "gate_visibility": "visible",
                "can_select_despite_risk": True,
            }
            for candidate in pass1_candidates
        ],
    }
    (workspace / "ideation" / "_candidate_directions.json").write_text(
        json.dumps(candidate_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_gate1_selection_brief.md").write_text(
        """# Gate1 Selection Brief

## Pass1 candidates

- D1: 测试假设依据，Origin free_reasoning，Pass2 proceed。
- D1b: 基于证据的替代候选，Origin cross_domain_analogy，Pass2 defer_recommended，仍可选择但需要重构机制。
- D2: 被淘汰方向，Origin seed_refinement，Pass2 reject_recommended，仍可选择但必须重构 contribution character。
- S1: 反向操作补充候选，Origin reverse_operation，Pass2 revise_before_selection，适合作为补充。

## Pass2 warnings

- D1: none。
- D1b: 若选择需要重构机制。
- D2: 若选择必须先重构 contribution character。
- S1: 选择前要说明为何不是普通消融。

## Merge options

- 合并 D1+D1b：用 D1 的清晰机制吸收 D1b 的失败子群证据。
- 合并 D1+S1：把 S1 作为 D1 的反向操作消融。

## 集中度提示

多个候选没有过度依赖同一篇论文；这是软提示，不是质量结论。

## Origin 分布

- free_reasoning: 1
- cross_domain_analogy: 1
- seed_refinement: 1
- reverse_operation: 1

## Novelty-Utility 谱系排布

- 高新颖高风险: S1
- 中新颖高可行: D1, D1b
- 低新颖高可行: D2

用户可选择 D1、选择 D2 并重构、合并 D1+D1b、合并 D1+S1、新想法或重新分析。
""",
        encoding="utf-8",
    )
    (workspace / "ideation" / "rejected_ideas.md").write_text(
        """# Rejected / Deferred Ideas

## D2: 被淘汰方向

- **Status**: rejected
- **Why rejected**:
  - 和已有工作太接近，差异主要是应用场景变化。
  - 缺少清晰评价指标，难以形成完整论文故事。
- **Closest existing work**: Nearby Paper，机制和目标都很接近。
- **Missing evidence / metric**: 缺少稳定评价指标和强差异化机制。
- **Can revisit if**: 如果找到更强的差异化机制和数据集，可以重访。
- **Cheap pilot that was not chosen**: 小规模proxy set不足以证明新颖贡献。

## D1b: 基于证据的替代候选

- **Status**: deferred
- **Why deferred**:
  - Pass2 建议暂缓，需要进一步收紧机制。
  - 该方向仍保留在 Gate1 候选池，用户可以选择并要求重构。
- **Can revisit if**: 如果 D1 pilot 失败但失败子群信号强，可以重访。

## S1: 反向操作补充候选

- **Status**: deferred
- **Why deferred**:
  - 更适合作为 D1 的消融补充，不单独作为主线。
  - Gate1 仍可选择或合并，例如合并 D1+S1。
- **Can revisit if**: 如果反向操作出现强信号，可以合并进主假设。
""",
        encoding="utf-8",
    )
    (workspace / "ideation" / "gate_decisions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "decisions": [
                    {
                        "gate_id": "T4-DECIDE-1",
                        "action": "select_direction",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": ["D2"],
                        "deferred_idea_ids": [],
                        "selected_by": "user",
                        "user_feedback": "继续聚焦这个可验证方向。",
                        "rationale": ["D1更可评估", "D2和已有工作太接近"],
                        "resulting_artifacts": [
                            "ideation/idea_scorecard.yaml",
                            "ideation/rejected_ideas.md",
                        ],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "deferred_idea_ids": [],
                        "selected_by": "user",
                        "user_feedback": "确认计划。",
                        "rationale": ["实验预算可控", "指标和baseline清楚"],
                        "resulting_artifacts": [
                            "ideation/hypotheses.md",
                            "ideation/exp_plan.yaml",
                            "ideation/risks.md",
                        ],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "idea_rationales.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea_id": "D1",
                        "hypothesis_refs": refs,
                        "title": "测试假设依据",
                        "idea_summary": "基于综述缺口提出一个可验证假设。",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "现有方法在目标场景下存在明确短板。",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                            "missing_area_links": ["missing_areas.md: 需要机制验证"],
                            "comparison_table_signals": ["comparison_table.csv: 基线未覆盖该约束"],
                            "seed_idea_links": [],
                            "lens_insights": ["causal: 可用accuracy指标验证机制差异"],
                            "forward_reasoning": "从问题机制出发先提出可检验设计，再回到文献做 grounding。",
                            "grounding_checks": ["确认预算内可评估", "确认已有基线不足以覆盖该机制"],
                        },
                        "reasoning": "文献缺口和对比表共同指向该机制，因此形成可实验验证的假设。",
                        "confidence": "medium",
                        "limitations": ["仍需T4.5进行新颖性审计"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_ideation_agent_spec(ideation_agent):
    """测试Ideation Agent的AgentSpec配置。"""
    spec = ideation_agent.spec
    assert spec.name == "ideation"
    assert spec.model_tier == "heavy"
    assert spec.llm_profile == "ideation_deep"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "ask_human" in spec.tool_names
    assert "finish_task" in spec.tool_names
    assert spec.temperature == 0.75
    assert "literature/" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_read_prefixes
    assert "_runtime/resume/" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_write_prefixes
    assert spec.structured_outputs["ideation/idea_rationales.json"] == "idea_rationales"
    assert spec.structured_outputs["ideation/idea_scorecard.yaml"] == "idea_scorecard"
    assert spec.structured_outputs["ideation/gate_decisions.json"] == "gate_decisions"


def test_ideation_system_prompt(ideation_agent, temp_workspace):
    """测试system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test research direction",
        "keywords": ["test", "research"],
        "constraints": {
            "max_budget_usd": 500,
            "compute_resources": {"allow_gpu": True},
        },
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建synthesis.md
    syn_path = temp_workspace / "literature" / "synthesis.md"
    syn_path.write_text("# Test Synthesis\n\n" + "x" * 1000)

    # 创建missing_areas.md
    missing_path = temp_workspace / "literature" / "missing_areas.md"
    missing_path.write_text("# Missing Areas\n\nSome gaps...")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    prompt = ideation_agent.system_prompt(ctx)
    assert "Ideation Agent" in prompt or "假设生成" in prompt
    assert "Test research direction" in prompt
    assert "Gate" in prompt or "两轮" in prompt


def test_ideation_initial_user_message(ideation_agent, temp_workspace):
    """测试初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    msg = ideation_agent.initial_user_message(ctx)
    assert "T4 Gate1 前半段" in msg
    assert "_pass1_forward_candidates.json" in msg
    assert "_gate1_selection_brief.md" in msg
    assert "不要在本轮调用 ask_human" in msg
    assert "不要写hypotheses.md" in msg


def test_ideation_initial_user_message_after_gate1_selection(ideation_agent, temp_workspace):
    """Gate1 selection 存在后，T4 才进入最终假设/实验计划写作。"""
    (temp_workspace / "ideation" / "_gate1_user_selection.json").write_text(
        json.dumps(
            {
                "semantics": "t4_gate1_user_selection_for_candidate_pool",
                "selection_fingerprint": "abc123",
            }
        ),
        encoding="utf-8",
    )
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    msg = ideation_agent.initial_user_message(ctx)

    assert "T4 Gate1 后半段" in msg
    assert "_gate1_user_selection.json" in msg
    assert "hypotheses.md" in msg
    assert "selection_fingerprint" in msg


def _write_valid_t4_outputs(workspace: Path) -> None:
    # 创建project.yaml
    project_path = workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md（带anchor）
    hyp_path = workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

### 背景
这是背景，需要足够长的内容来通过500字符的验证。我们基于文献综述发现了一个重要的研究缺口，
现有方法在处理大规模数据时存在效率问题，而我们提出的方法可以显著提升性能。
这个假设基于多篇论文的观察，包括Smith et al. 2023和Jones et al. 2024的工作。
我们相信这个方向具有重要的理论和实践价值。

### 核心假设
我们假设通过引入新的注意力机制，可以在保持准确率的同时将推理速度提升2倍以上。
这个假设是可验证的，我们将通过在ImageNet数据集上的实验来验证。
具体来说，我们将实现一个新的高效注意力模块。

### 预期结果
如果假设成立，我们预期在ImageNet-1k验证集上达到80%以上的top-1准确率，
同时推理时间不超过100ms每张图片。这将比现有最好的方法快2倍。

### 风险
主要风险是新机制可能导致训练不稳定。如果出现这种情况，我们将尝试调整学习率和优化器。

## H2: 第二个假设

### 背景
第二个假设关注模型的泛化能力，我们观察到现有方法在分布外数据上表现不佳。
"""
    hyp_path.write_text(hyp_content)

    # 创建exp_plan.yaml（符合schema）
    plan_path = workspace / "ideation" / "exp_plan.yaml"
    plan_data = {
        "goal": "验证假设H1",
        "experiments": [
            {
                "id": "exp1",
                "name": "Baseline",
                "title": "基线实验",
                "hypothesis_ref": "#H1,#H2",
                "datasets": [{"name": "test", "split": "val", "size": 1000}],
                "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                "our_method": {
                    "name": "OurMethod",
                    "description": "Our approach",
                    "key_difference": "Different from baseline",
                },
                "metrics": [
                    {"name": "accuracy", "primary": True, "target": 0.8}
                ],
                "success_criteria": [
                    {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                ],
                "steps": [
                    {"step": 1, "action": "Prepare data", "details": "Download dataset"}
                ],
                "compute_estimate": {
                    "gpu_hours": 50,
                    "gpu_type": "A100",
                    "estimated_cost_usd": 150,
                },
                "expected_duration_days": 3,
            }
        ],
    }
    plan_path.write_text(yaml.dump(plan_data))

    # 创建risks.md（至少3条风险）
    risks_path = workspace / "ideation" / "risks.md"
    risks_content = """# Top 3 风险

## 风险1: 数据不足
- **描述**: 数据集可能太小
- **Early Signal**: 训练loss不下降
- **Mitigation**: 使用数据增强
- **Kill Criteria**: 如果3天内无进展

## 风险2: 计算资源不足
- **描述**: GPU时间可能不够
- **Early Signal**: 训练速度慢
- **Mitigation**: 优化代码
- **Kill Criteria**: 如果超出预算

## 风险3: 基线难以复现
- **描述**: 基线结果可能无法复现
- **Early Signal**: 结果偏差大
- **Mitigation**: 联系原作者
- **Kill Criteria**: 如果1周内无法复现
"""
    risks_path.write_text(risks_content)
    write_valid_idea_rationales(workspace, refs=["H1", "H2"])


def test_validate_outputs_success(ideation_agent, temp_workspace):
    """测试输出校验（成功场景）。"""
    _write_valid_t4_outputs(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_t4_contract_declares_bridge_domain_plan_input():
    """T4 prompt consumes bridge_domain_plan, so the IO contract must track it."""

    assert (
        TASK_IO_CONTRACTS["T4"]["inputs"]["bridge_domain_plan"]
        == "literature/bridge_domain_plan.json"
    )


def test_validate_outputs_rejects_gate1_selection_when_candidate_pool_changed(ideation_agent, temp_workspace):
    """Gate1 用户选择必须绑定当时展示的候选池，而不只是绑定 selected id。"""
    _write_valid_t4_outputs(temp_workspace)
    fingerprint = _write_gate1_selection(temp_workspace)
    gate_path = temp_workspace / "ideation" / "gate_decisions.json"
    gate_data = json.loads(gate_path.read_text(encoding="utf-8"))
    gate_data["gate1_selection_fingerprint"] = fingerprint
    gate_data["decisions"][0]["source_selection_fingerprint"] = fingerprint
    gate_path.write_text(json.dumps(gate_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert ok, f"Validation failed before candidate mutation: {err}"

    candidate_path = temp_workspace / "ideation" / "_candidate_directions.json"
    candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_data["candidates"][0]["pitch"] = "Changed after human selection"
    candidate_path.write_text(json.dumps(candidate_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "候选池已变化" in (err or "")


def test_validate_outputs_missing_idea_rationales(ideation_agent, temp_workspace):
    """测试输出校验（缺少idea依据记录）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}}))

    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-missing-rationales",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "idea_rationales.json" in err


def test_validate_outputs_missing_idea_scorecard(ideation_agent, temp_workspace):
    """测试输出校验（缺少候选idea scorecard）。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)
    (temp_workspace / "ideation" / "idea_scorecard.yaml").unlink()

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-missing-scorecard",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "idea_scorecard.yaml" in err


def test_validate_outputs_missing_hypothesis_anchor(ideation_agent, temp_workspace):
    """测试输出校验（缺少假设anchor）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md（没有anchor）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = "# 研究假设\n\n这是一些假设内容，但没有H1/H2等anchor。" + "x" * 500
    hyp_path.write_text(hyp_content)

    # 创建其他必需文件
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_path.write_text("goal: test\nexperiments: []\n")

    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "anchor" in err or "假设" in err


def test_validate_outputs_invalid_exp_plan(ideation_agent, temp_workspace):
    """测试输出校验（exp_plan不符合schema）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = "# 研究假设\n\n## H1: 假设1\n\n内容..." + "x" * 500
    hyp_path.write_text(hyp_content)

    # 创建exp_plan.yaml（缺少必需字段）
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_path.write_text("goal: test\n")  # 缺少experiments字段

    # 创建risks.md
    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "schema" in err or "实验" in err


def test_validate_outputs_budget_exceeded(ideation_agent, temp_workspace):
    """测试输出校验（预算超限）。"""
    # 创建project.yaml（预算很小）
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 100},  # 只有$100预算
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容..." + "x" * 500)

    # 创建exp_plan.yaml（计算成本超出预算）
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_data = {
        "goal": "验证假设H1",
        "experiments": [
            {
                "id": "exp1",
                "name": "Expensive Experiment",
                "title": "昂贵的实验",
                "hypothesis_ref": "#H1",
                "datasets": [{"name": "test", "split": "val", "size": 1000}],
                "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                "our_method": {
                    "name": "OurMethod",
                    "description": "Our approach",
                    "key_difference": "Different",
                },
                "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                "success_criteria": [
                    {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                ],
                "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                "compute_estimate": {
                    "gpu_hours": 100,  # 100h * $3 = $300，超过$100预算的85%
                    "gpu_type": "A100",
                    "estimated_cost_usd": 300,
                },
                "expected_duration_days": 7,
            }
        ],
    }
    plan_path.write_text(yaml.dump(plan_data))

    # 创建risks.md
    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "预算" in err or "成本" in err or "budget" in err.lower()


def test_validate_outputs_total_budget_exceeded_even_when_single_experiments_fit(
    ideation_agent,
    temp_workspace,
):
    """总预算必须校验，不能只看单个实验是否低于85%。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 100}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    plan_data = {
        "goal": "验证假设H1",
        "total_estimated_cost_usd": 108.0,
        "budget_check": {"over_budget": True},
        "experiments": [
            {
                "id": f"exp{i}",
                "name": f"Experiment {i}",
                "title": f"实验{i}",
                "hypothesis_ref": "#H1",
                "datasets": [{"name": "test", "split": "val", "size": 1000}],
                "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                "our_method": {
                    "name": "OurMethod",
                    "description": "Our approach",
                    "key_difference": "Different",
                },
                "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                "success_criteria": [
                    {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                ],
                "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                "compute_estimate": {
                    "gpu_hours": 10,
                    "gpu_type": "A100",
                    "estimated_cost_usd": cost,
                },
                "expected_duration_days": 2,
            }
            for i, cost in enumerate([30, 24, 18, 36], start=1)
        ],
    }
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(yaml.dump(plan_data))
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-total-budget",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "总成本" in err or "over_budget" in err


def test_validate_outputs_rejects_missing_pass2_review(
    ideation_agent,
    temp_workspace,
):
    """Pass2 必须覆盖 Pass1 全部候选，不能筛掉后不留记录。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)
    pass2_path = temp_workspace / "ideation" / "_pass2_grounding_review.json"
    data = json.loads(pass2_path.read_text(encoding="utf-8"))
    data["reviews"] = [review for review in data["reviews"] if review["idea_id"] != "S1"]
    pass2_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-missing-pass2",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "未覆盖" in err and "S1" in err


def test_validate_outputs_rejects_hidden_pass2_candidate(
    ideation_agent,
    temp_workspace,
):
    """Pass2 只能标风险，不能把候选隐藏出 Gate1。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)
    pass2_path = temp_workspace / "ideation" / "_pass2_grounding_review.json"
    data = json.loads(pass2_path.read_text(encoding="utf-8"))
    data["reviews"][0]["visible_to_gate"] = False
    pass2_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-hidden-pass2",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "不能隐藏候选" in err or "visible_to_gate=false" in err


def test_validate_outputs_rejects_candidate_pool_deleting_pass1(
    ideation_agent,
    temp_workspace,
):
    """Gate1 候选池必须保留 Pass1 全部候选。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)
    candidate_path = temp_workspace / "ideation" / "_candidate_directions.json"
    data = json.loads(candidate_path.read_text(encoding="utf-8"))
    data["candidates"] = [candidate for candidate in data["candidates"] if candidate["id"] != "D2"]
    candidate_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-deleted-pass1",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "不能因 Pass2 筛选删除" in err and "D2" in err


def test_candidate_directions_rejects_zero_bridge_candidate_when_plan_confirmed(temp_workspace):
    """T1 已确认 bridge 清单时，T4 不能完全漏掉 bridge_synthesis 候选。"""
    (temp_workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "user",
                "bridge_domains": [
                    {
                        "bridge_id": "b1",
                        "name": "Mechanism transfer",
                        "why": "User confirmed this bridge.",
                        "priority": "must_explore",
                        "queries": ["mechanism transfer"],
                        "source": "user",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_valid_idea_rationales(temp_workspace)

    ok, err = _validate_candidate_directions(temp_workspace)

    assert not ok
    assert "零 bridge_synthesis 候选" in err


def test_candidate_directions_require_cross_domain_candidate(temp_workspace):
    """四类补充之外必须有一个领域交叉/跨域类比候选。"""
    write_valid_idea_rationales(temp_workspace)
    for rel in [
        "ideation/_candidate_directions.json",
        "ideation/_pass1_forward_candidates.json",
    ]:
        path = temp_workspace / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        for candidate in data["candidates"]:
            if str(candidate.get("id") or candidate.get("idea_id")) == "D1b":
                candidate["idea_origin"] = "evidence_driven"
                candidate.pop("cross_domain_sources", None)
                candidate.pop("cross_domain_source", None)
                candidate.pop("cross_domain_relation", None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, err = _validate_candidate_directions(temp_workspace)

    assert not ok
    assert "领域交叉候选" in err


def test_bridge_missing_must_can_use_escape_hatch_warning(temp_workspace):
    """must_explore 覆盖不足是 WARN/逃生舱语义，不应迫使 LLM 硬编 idea。"""
    (temp_workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "mixed",
                "bridge_domains": [
                    {
                        "bridge_id": "b1",
                        "name": "Mechanism transfer",
                        "why": "User confirmed this bridge.",
                        "priority": "must_explore",
                        "queries": ["mechanism transfer"],
                        "source": "user",
                    },
                    {
                        "bridge_id": "b2",
                        "name": "Evaluation transfer",
                        "why": "User confirmed this as optional bridge.",
                        "priority": "should_explore",
                        "queries": ["evaluation transfer"],
                        "source": "user",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_valid_idea_rationales(temp_workspace)
    candidate_path = temp_workspace / "ideation" / "_candidate_directions.json"
    candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    bridge_candidate = {
        "id": "B2",
        "title": "Evaluation bridge candidate",
        "generation_stage": "bridge",
        "idea_origin": "bridge_synthesis",
        "constraint_status": "bridge",
        "pitch": "Use an evaluation protocol from the confirmed optional bridge.",
        "core_claim": "Evaluation transfer can expose a hidden failure mode.",
        "target_problem": "Current evaluation misses a transfer-sensitive failure mode.",
        "mechanism": "Changing the evaluation protocol reveals failures hidden by aggregate metrics.",
        "prediction": "Under the transferred protocol, the target system will show larger subgroup gaps.",
        "counterfactual": "If the failure is not protocol-sensitive, subgroup gaps should remain stable.",
        "mechanism_family": "evaluation transfer",
        "basis_summary": "Bridge notes suggest an evaluation protocol that can transfer as a diagnostic, while b1 lacks enough completed notes.",
        "cross_domain_sources": ["b2"],
        "cross_domain_relation": "evaluation_or_metric_bridge",
        "pass2_screening": {
            "screening_recommendation": "proceed",
            "visible_to_gate": True,
            "selection_warning": "Bridge candidate is visible but optional.",
        },
        "gate_visibility": "visible",
        "can_select_despite_risk": True,
    }
    candidate_data["candidates"].append(bridge_candidate)
    candidate_path.write_text(json.dumps(candidate_data, ensure_ascii=False, indent=2), encoding="utf-8")
    pass1_path = temp_workspace / "ideation" / "_pass1_forward_candidates.json"
    pass1_data = json.loads(pass1_path.read_text(encoding="utf-8"))
    pass1_data["candidates"].append(bridge_candidate)
    pass1_path.write_text(json.dumps(pass1_data, ensure_ascii=False, indent=2), encoding="utf-8")
    pass2_path = temp_workspace / "ideation" / "_pass2_grounding_review.json"
    pass2_data = json.loads(pass2_path.read_text(encoding="utf-8"))
    pass2_data["reviews"].append(
        {
            "idea_id": "B2",
            "screening_recommendation": "proceed",
            "visible_to_gate": True,
            "counterfactual_check": "survives_weakened",
            "counterfactual_note": "Bridge protocol transfer has some independent rationale but needs stronger evidence.",
            "nearest_prior_work": {"work": "Bridge Evaluation Paper", "distance": "distant"},
            "novelty_signal": "adjacent_zone",
            "selection_warning": "Optional bridge candidate.",
        }
    )
    pass2_path.write_text(json.dumps(pass2_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_workspace / "ideation" / "bridge_coverage_review.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "bridge_candidate_visibility_and_escape_hatch_review",
                "source_bridge_plan": "literature/bridge_domain_plan.json",
                "bridge_reviews": [
                    {
                        "bridge_id": "b1",
                        "priority": "must_explore",
                        "candidate_ids": [],
                        "visible_to_gate": False,
                        "forced_surfaced": False,
                        "selected_into_hypotheses": False,
                        "decision_summary": "No bridge_synthesis candidate was generated because completed notes lacked transferable mechanisms.",
                        "escape_hatch": {
                            "status": "no_candidate_available",
                            "reason": "No completed bridge note gives enough mechanism evidence for b1.",
                            "falsification_or_kill_criteria": "If later notes still lack a transferable mechanism, keep b1 out of hypotheses.",
                            "can_revisit_if": "Revisit after T2/T3 adds at least one complete b1 bridge note.",
                        },
                    },
                    {
                        "bridge_id": "b2",
                        "priority": "should_explore",
                        "candidate_ids": ["B2"],
                        "visible_to_gate": True,
                        "forced_surfaced": False,
                        "selected_into_hypotheses": False,
                        "decision_summary": "B2 is visible to Gate1 but not selected by default.",
                        "escape_hatch": {
                            "status": "deferred",
                            "reason": "Optional bridge candidate awaits user selection.",
                            "falsification_or_kill_criteria": "Drop if pilot cannot instantiate the transferred protocol.",
                            "can_revisit_if": "Revisit if user selects bridge evaluation framing.",
                        },
                    },
                ],
                "warnings": ["must_explore bridge b1 did not have enough material for a visible candidate"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok, err = _validate_candidate_directions(temp_workspace)
    assert ok, err
    ok, err = _validate_bridge_coverage_review(temp_workspace)
    assert ok, err


def test_legacy_bridge_coverage_review_is_normalized_for_resume(temp_workspace):
    """旧 T4 partial artifact 用 bridge_domains/low_evidence 时，resume 应自动迁移。"""
    (temp_workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "user",
                "bridge_domains": [
                    {
                        "bridge_id": "b1",
                        "name": "Mechanism transfer",
                        "why": "User confirmed this bridge.",
                        "priority": "must_explore",
                        "queries": ["mechanism transfer"],
                        "source": "user",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    review_path = temp_workspace / "ideation" / "bridge_coverage_review.json"
    review_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "bridge_coverage_review_for_gate1_visibility",
                "bridge_domains": [
                    {
                        "bridge_id": "b1",
                        "summary": "No completed bridge note provides a transferable mechanism.",
                        "candidates_generated": [],
                        "visible_to_gate": False,
                        "escape_hatch": {
                            "status": "low_evidence",
                            "reason": "Bridge-specific evidence is too weak for a candidate.",
                        },
                    }
                ],
                "warnings": ["must_explore bridge b1 lacks enough evidence for a visible candidate"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok, err = _validate_bridge_coverage_review(temp_workspace)

    assert ok, err
    normalized = json.loads(review_path.read_text(encoding="utf-8"))
    assert normalized["semantics"] == "bridge_candidate_visibility_and_escape_hatch_review"
    assert "bridge_domains" not in normalized
    assert normalized["bridge_reviews"][0]["bridge_id"] == "b1"
    assert normalized["bridge_reviews"][0]["escape_hatch"]["status"] == "no_candidate_available"


def test_validate_t4_gate1_ready_accepts_candidate_pool_without_final_outputs(temp_workspace):
    write_valid_idea_rationales(temp_workspace)

    ok, err = validate_t4_gate1_ready(temp_workspace)

    assert ok, err

    assert not (temp_workspace / "ideation" / "hypotheses.md").exists()
    assert not (temp_workspace / "ideation" / "exp_plan.yaml").exists()


def test_idea_scorecard_schema_accepts_survey_driven_origin(temp_workspace):
    write_valid_idea_rationales(temp_workspace)
    data = yaml.safe_load((temp_workspace / "ideation" / "idea_scorecard.yaml").read_text(encoding="utf-8"))
    data["ideas"][1]["source"]["idea_origin"] = "survey_driven"
    data["ideas"][1]["source"]["trigger_observation"] = "T3.6 taxonomy exposed a mechanism-level design split."

    ok, err = validate_record(data, "idea_scorecard")

    assert ok, err


def test_unsupported_candidate_can_be_visible_but_not_pass2_proceed(temp_workspace):
    write_valid_idea_rationales(temp_workspace)
    candidate_path = temp_workspace / "ideation" / "_candidate_directions.json"
    data = json.loads(candidate_path.read_text(encoding="utf-8"))
    data["candidates"].append(
        {
            "id": "U1",
            "title": "Weak evidence candidate",
            "generation_stage": "mainline",
            "idea_origin": "survey_driven",
            "constraint_status": "not_supported_by_current_evidence",
            "pitch": "A weak survey hint that needs resource upgrade.",
            "core_claim": "Weak hint may become useful after full-text acquisition.",
            "target_problem": "The current evidence is abstract-only.",
            "mechanism": "Not yet supported by current evidence.",
            "prediction": "No strong prediction until resources are upgraded.",
            "counterfactual": "No strong counterfactual until resources are upgraded.",
            "mechanism_family": "weak evidence",
            "basis_summary": "Only weak_evidence_and_resource_upgrade and metadata triage currently support this direction.",
            "pass2_screening": {
                "screening_recommendation": "defer_recommended",
                "visible_to_gate": True,
                "selection_warning": "Resource upgrade required.",
            },
            "gate_visibility": "visible",
            "can_select_despite_risk": False,
        }
    )
    candidate_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, err = _validate_candidate_directions(temp_workspace)

    assert ok, err

    data["candidates"][-1]["pass2_screening"]["screening_recommendation"] = "proceed"
    candidate_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, err = _validate_candidate_directions(temp_workspace)

    assert not ok
    assert "unsupported 候选不能在 Pass2 标为 proceed" in err


def test_validate_outputs_rejects_selected_unsupported_scorecard_idea(ideation_agent, temp_workspace):
    _write_valid_t4_outputs(temp_workspace)
    scorecard_path = temp_workspace / "ideation" / "idea_scorecard.yaml"
    scorecard = yaml.safe_load(scorecard_path.read_text(encoding="utf-8"))
    scorecard["ideas"][0]["source"]["constraint_status"] = "not_supported_by_current_evidence"
    scorecard_path.write_text(yaml.safe_dump(scorecard, allow_unicode=True, sort_keys=False), encoding="utf-8")
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-unsupported-selected",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)

    assert not ok
    assert "仅有弱证据" in err
