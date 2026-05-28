"""Tests for ideation coverage analysis."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from researchos.tools.ideation_analysis import analyze_ideation_coverage


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sample_scorecard() -> dict:
    return {
        "ideas": [
            {
                "idea": {
                    "id": "D1",
                    "title": "Test idea 1",
                    "pitch": "pitch 1",
                    "core_claim": "claim 1",
                    "target_problem": "problem 1",
                    "mechanism": "noise regularization improves embeddings",
                    "prediction": "Recall improves",
                    "counterfactual": "if not, no improvement",
                    "mechanism_family": "noise regularization",
                },
                "source": {
                    "from_synthesis_section": "Q1 / [p3]",
                    "from_missing_area": "缺少噪声机制验证",
                    "from_seed_idea": True,
                    "supporting_papers": [{"title": "Paper A", "claim_used": "claim A"}],
                    "trigger_observation": "质疑 noise 的真正机制",
                    "seed_alignment": "direct",
                    "idea_origin": "free_reasoning",
                    "constraint_status": "mainline",
                },
                "selection_rationale": {
                    "novelty_reason": "novel",
                    "feasibility_reason": "feasible",
                    "impact_reason": "impactful",
                    "evaluability_reason": "evaluable",
                    "paper_story": "story",
                },
                "closest_baselines": [{"name": "BaselineA", "similarity": "similar", "difference": "different"}],
                "scores": {"novelty": 4, "feasibility": 3, "impact": 4, "evaluability": 5, "differentiation": 3, "cost": 4, "contribution_strength": 4},
                "decision": {"status": "selected"},
                "risks": [{"risk": "risk1", "early_signal": "signal1", "mitigation": "mit1", "kill_criteria": "kill1"}],
                "minimum_experiment": {"dataset": "ds", "baseline": "bl", "metric": ["m1"], "expected_signal": "sig", "estimated_cost_usd": 10},
            },
            {
                "idea": {
                    "id": "D2",
                    "title": "Test idea 2",
                    "pitch": "pitch 2",
                    "core_claim": "claim 2",
                    "target_problem": "problem 2",
                    "mechanism": "reverse augmentation works",
                    "prediction": "Accuracy improves",
                    "counterfactual": "if not, stays same",
                    "mechanism_family": "reverse augmentation",
                },
                "source": {
                    "from_synthesis_section": "Q2",
                    "from_missing_area": "",
                    "from_seed_idea": False,
                    "supporting_papers": [{"title": "Paper B", "claim_used": "claim B"}],
                    "trigger_observation": "反向操作可能有效",
                    "seed_alignment": "none",
                    "idea_origin": "reverse_operation",
                    "constraint_status": "supplement",
                },
                "selection_rationale": {
                    "novelty_reason": "novel",
                    "feasibility_reason": "feasible",
                    "impact_reason": "impactful",
                    "evaluability_reason": "evaluable",
                    "paper_story": "story",
                },
                "closest_baselines": [{"name": "BaselineB", "similarity": "similar", "difference": "different"}],
                "scores": {"novelty": 3, "feasibility": 4, "impact": 3, "evaluability": 4, "differentiation": 3, "cost": 5, "contribution_strength": 3},
                "decision": {"status": "rejected"},
                "risks": [{"risk": "risk2", "early_signal": "signal2", "mitigation": "mit2", "kill_criteria": "kill2"}],
                "minimum_experiment": {"dataset": "ds2", "baseline": "bl2", "metric": ["m2"], "expected_signal": "sig2", "estimated_cost_usd": 5},
            },
        ]
    }


def _sample_workbench() -> dict:
    return {
        "method_families": [
            {"name": "noise regularization", "paper_ids": ["p1", "p2"]},
            {"name": "contrastive learning", "paper_ids": ["p3"]},
        ],
        "mechanism_claim_clusters": [
            {
                "mechanism": "noise regularization improves embeddings",
                "paper_count": 3,
                "paper_ids": ["p1", "p2", "p3"],
                "evidence_strength_hint": "llm_review_required",
                "has_untested_claims": True,
                "challengeable_hint": True,
                "abstract_only_count": 0,
            },
        ],
        "research_question_candidates": [
            {"id": "Q1", "question": "How does noise work?"},
            {"id": "Q2", "question": "Is reverse augmentation effective?"},
        ],
        "shared_assumption_candidates": [
            {"assumption": "noise helps", "supporting_papers": ["p1"]},
        ],
    }


def _sample_missing_areas() -> str:
    return """# 文献缺口分析

## 可探索缺口

### 缺口 1: 噪声机制覆盖不足
- **覆盖缺口**: 仅 2 篇论文提及
- **为什么是缺口**: 缺乏验证
- **可探索方向**: 设计消融实验
- **难度**: Medium

### 缺口 2: 反向操作研究不足
- **覆盖缺口**: 无相关论文
- **为什么是缺口**: 未被探索
- **可探索方向**: 移除组件实验
- **难度**: Low
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyze_coverage_basic(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    assert result["ok"] is True
    coverage = result["coverage"]
    assert coverage["idea_count"] == 2


def test_analyze_coverage_source_stats(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    stats = result["coverage"]["source_stats"]
    assert stats["total_ideas"] == 2
    assert stats["from_synthesis"] == 2
    assert stats["from_missing_area"] == 1
    assert stats["from_seed_idea"] == 1
    assert stats["seed_alignment_direct"] == 1
    assert stats["seed_alignment_none"] == 1
    assert stats["origin_free_reasoning"] == 1
    assert stats["origin_supplement"] == 1
    assert result["coverage"]["origin_mix"]["mainline_total"] == 1
    assert result["coverage"]["origin_mix"]["supplement_only_risk"] is False


def test_analyze_coverage_family_coverage(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    fc = result["coverage"]["method_family_coverage"]
    assert fc["total_families"] == 2
    assert fc["covered_families"] >= 1


def test_analyze_coverage_question_coverage(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    qc = result["coverage"]["research_question_coverage"]
    assert qc["total_questions"] == 2
    assert qc["covered_questions"] >= 1


def test_analyze_coverage_mechanism_claim_clusters(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    dc = result["coverage"]["mechanism_claim_cluster_coverage"]
    assert dc["total_clusters"] == 1
    assert dc["challengeable_hint_count"] == 1
    assert dc["semantics"] == "mechanical_cluster_coverage_hint_only"


def test_analyze_coverage_missing_areas(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())
    _write_json(ws / "literature" / "synthesis_workbench.json", _sample_workbench())
    _write_text(ws / "literature" / "missing_areas.md", _sample_missing_areas())

    result = analyze_ideation_coverage(ws)
    ma = result["coverage"]["missing_area_coverage"]
    assert ma["total_gaps"] == 2
    assert ma["gaps_addressed"] == 1


def test_analyze_coverage_no_scorecard(tmp_path: Path):
    result = analyze_ideation_coverage(tmp_path / "nonexistent")
    assert "error" in result


def test_analyze_coverage_no_workbench(tmp_path: Path):
    ws = tmp_path / "workspace"
    _write_yaml(ws / "ideation" / "idea_scorecard.yaml", _sample_scorecard())

    result = analyze_ideation_coverage(ws)
    assert result["ok"] is True
    # Should still work with empty workbench
    assert result["coverage"]["idea_count"] == 2
