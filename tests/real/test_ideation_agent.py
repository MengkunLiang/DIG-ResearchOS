"""Ideation Agent Integration Tests.

测试头脑风暴 Agent（T4）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.ideation import IdeationAgent


def _write_valid_idea_rationales(workspace: Path, refs: list[str] | None = None) -> None:
    refs = refs or ["H1"]
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea": {
                            "id": "D1",
                            "title": "Test rationale",
                            "pitch": "A traceable idea generated from synthesis gaps.",
                            "core_claim": "The proposed mechanism improves a measurable metric.",
                            "target_problem": "Prior methods leave a measurable gap.",
                        },
                        "hypothesis_refs": refs,
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q1",
                            "from_missing_area": "missing_areas.md: mechanism gap",
                            "from_seed_idea": False,
                            "supporting_papers": [
                                {
                                    "title": "Prior Paper",
                                    "claim_used": "Prior methods leave a measurable gap.",
                                }
                            ],
                            "trigger_observation": "The synthesis gap points to a pilotable mechanism.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "The mechanism is underexplored.",
                            "feasibility_reason": "A small pilot is enough.",
                            "impact_reason": "The problem matters.",
                            "evaluability_reason": "Metrics are clear.",
                            "paper_story": "Problem, method, and experiment align.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Baseline",
                                "similarity": "Same target problem.",
                                "difference": "Different mechanism.",
                            }
                        ],
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
                            "selected_reason": ["clear story", "cheap pilot"],
                            "selected_by": "user",
                            "user_feedback": "select D1",
                        },
                        "risks": [
                            {
                                "risk": "No gain",
                                "early_signal": "Pilot fails",
                                "mitigation": "Run ablation",
                                "kill_criteria": "No improvement",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Baseline",
                            "metric": ["accuracy"],
                            "expected_signal": "improvement",
                            "estimated_cost_usd": 5,
                        },
                    },
                    {
                        "idea": {
                            "id": "D2",
                            "title": "Rejected idea",
                            "pitch": "Too close to prior work.",
                            "core_claim": "A weak transfer may work.",
                            "target_problem": "Weak gap.",
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q2",
                            "from_missing_area": "missing_areas.md: weak gap",
                            "from_seed_idea": False,
                            "supporting_papers": [
                                {
                                    "title": "Nearby Paper",
                                    "claim_used": "Prior work already covers it.",
                                }
                            ],
                            "trigger_observation": "Direct transfer idea.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "Weak novelty.",
                            "feasibility_reason": "Feasible.",
                            "impact_reason": "Limited impact.",
                            "evaluability_reason": "Metrics unclear.",
                            "paper_story": "Story too thin.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Nearby Paper",
                                "similarity": "Very similar.",
                                "difference": "Mostly scenario change.",
                            }
                        ],
                        "scores": {
                            "novelty": 2,
                            "feasibility": 4,
                            "impact": 2,
                            "evaluability": 2,
                            "differentiation": 2,
                            "cost": 4,
                            "contribution_strength": 2,
                        },
                        "decision": {
                            "status": "rejected",
                            "rejection_reason": ["too close to prior work"],
                            "can_revisit_if": "Find a stronger mechanism.",
                        },
                        "risks": [
                            {
                                "risk": "Low novelty",
                                "early_signal": "High overlap",
                                "mitigation": "Find differentiation",
                                "kill_criteria": "Only scenario change",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Nearby Paper",
                            "metric": ["accuracy"],
                            "expected_signal": "large improvement",
                            "estimated_cost_usd": 5,
                        },
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "rejected_ideas.md").write_text(
        "# Rejected / Deferred Ideas\n\n"
        "## D2: Rejected idea\n\n"
        "- **Status**: rejected\n"
        "- **Why rejected**:\n"
        "  - too close to prior work\n"
        "- **Closest existing work**: Nearby Paper.\n"
        "- **Missing evidence / metric**: stronger mechanism.\n"
        "- **Can revisit if**: Find a stronger mechanism.\n"
        "- **Cheap pilot that was not chosen**: small data is not enough.\n",
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
                        "selected_by": "user",
                        "rationale": ["D1 clearer", "D2 too close"],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "selected_by": "user",
                        "rationale": ["plan ok"],
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
                        "title": "Test rationale",
                        "idea_summary": "A traceable idea generated from synthesis gaps.",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "Prior methods leave a measurable gap.",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                            "missing_area_links": ["missing_areas.md: mechanism gap"],
                            "comparison_table_signals": [],
                            "seed_idea_links": [],
                            "lens_insights": ["causal: the mechanism is experimentally separable"],
                        },
                        "reasoning": "The synthesis gap points to a measurable mechanism hypothesis.",
                        "confidence": "medium",
                        "limitations": ["Needs novelty audit."],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_current_ideation_artifacts(workspace, refs)


def _write_current_ideation_artifacts(workspace: Path, refs: list[str]) -> None:
    """Write the current T4 validator artifact shape for real integration tests."""

    selected = {
        "idea": {
            "id": "D1",
            "title": "Test rationale",
            "pitch": "A traceable idea generated from synthesis gaps.",
            "core_claim": "The proposed mechanism improves a measurable metric.",
            "target_problem": "Prior methods leave a measurable gap.",
            "mechanism": "A targeted calibration mechanism changes the measurable error pattern.",
            "prediction": "Accuracy improves on the target validation set.",
            "counterfactual": "If the mechanism is irrelevant, disabling calibration should not change accuracy.",
            "mechanism_family": "targeted calibration",
            "cdr_tuple": {
                "problem_frame": "Prior methods leave a measurable calibration gap.",
                "design_rationale": "A targeted calibration artifact should directly affect the observed error pattern.",
                "artifact": "calibration module",
                "design_principles": ["mechanism isolation", "ablation-ready comparison"],
                "data_view": "validation examples grouped by target condition",
                "evaluation_mode": "accuracy plus ablation",
                "contribution_type": "improvement",
                "boundary_conditions": ["works when target condition is observable"],
                "cross_paper_tension": ["prior work disagrees on whether calibration is general"],
            },
            "contribution_type": "improvement",
            "contribution_character": "If this works, the field gains a clearer mechanism-level explanation for when targeted calibration helps.",
            "contribution_strength": 4,
        },
        "hypothesis_refs": refs,
        "source": {
            "from_synthesis_section": "synthesis.md: Q1",
            "from_missing_area": "missing_areas.md: mechanism gap",
            "from_seed_idea": False,
            "idea_origin": "free_reasoning",
            "constraint_status": "mainline",
            "supporting_papers": [
                {"title": "Prior Paper", "claim_used": "Prior methods leave a measurable gap."}
            ],
            "trigger_observation": "The synthesis gap points to a pilotable mechanism.",
        },
        "selection_rationale": {
            "novelty_reason": "The mechanism is underexplored.",
            "feasibility_reason": "A small pilot is enough.",
            "impact_reason": "The problem matters.",
            "evaluability_reason": "Metrics are clear.",
            "paper_story": "Problem, method, and experiment align.",
            "contribution_character": "If this works, the field gains a clearer mechanism-level explanation for when targeted calibration helps.",
        },
        "closest_baselines": [
            {"name": "Baseline", "similarity": "Same target problem.", "difference": "Different mechanism."}
        ],
        "counterfactual_check": "independent",
        "counterfactual_note": "The idea still has a mechanism after weakening the nearest prior.",
        "nearest_prior_work": {"work": "Prior Paper", "distance": "moderate"},
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
            "selected_reason": ["clear story", "cheap pilot"],
            "selected_by": "user",
            "user_feedback": "select D1",
        },
        "risks": [
            {
                "risk": "No gain",
                "early_signal": "Pilot fails",
                "mitigation": "Run ablation",
                "kill_criteria": "No improvement",
            }
        ],
        "minimum_experiment": {
            "dataset": "small data",
            "baseline": "Baseline",
            "metric": ["accuracy"],
            "expected_signal": "improvement",
            "estimated_cost_usd": 5,
        },
    }
    rejected = {
        "idea": {
            "id": "D2",
            "title": "Rejected idea",
            "pitch": "Too close to prior work.",
            "core_claim": "A weak transfer may work.",
            "target_problem": "Weak gap.",
            "mechanism": "Direct transfer reuses a prior mechanism with little change.",
            "prediction": "Accuracy may improve slightly.",
            "counterfactual": "If the prior mechanism already explains the effect, no new signal appears.",
            "mechanism_family": "direct transfer",
            "contribution_type": "routine",
            "contribution_strength": 2,
        },
        "hypothesis_refs": [],
        "source": {
            "from_synthesis_section": "synthesis.md: Q2",
            "from_missing_area": "missing_areas.md: weak gap",
            "from_seed_idea": False,
            "idea_origin": "seed_refinement",
            "constraint_status": "mainline",
            "supporting_papers": [
                {"title": "Nearby Paper", "claim_used": "Prior work already covers it."}
            ],
            "trigger_observation": "Direct transfer idea.",
        },
        "selection_rationale": {
            "novelty_reason": "Weak novelty.",
            "feasibility_reason": "Feasible.",
            "impact_reason": "Limited impact.",
            "evaluability_reason": "Metrics unclear.",
            "paper_story": "Story too thin.",
        },
        "closest_baselines": [
            {"name": "Nearby Paper", "similarity": "Very similar.", "difference": "Mostly scenario change."}
        ],
        "counterfactual_check": "collapses",
        "counterfactual_note": "Removing the nearest prior leaves only a scenario transfer.",
        "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
        "novelty_signal": "marginal_zone",
        "scores": {
            "novelty": 2,
            "feasibility": 4,
            "impact": 2,
            "evaluability": 2,
            "differentiation": 2,
            "cost": 4,
            "contribution_strength": 2,
        },
        "decision": {
            "status": "rejected",
            "rejection_reason": ["too close to prior work"],
            "can_revisit_if": "Find a stronger mechanism.",
        },
        "risks": [
            {
                "risk": "Low novelty",
                "early_signal": "High overlap",
                "mitigation": "Find differentiation",
                "kill_criteria": "Only scenario change",
            }
        ],
        "minimum_experiment": {
            "dataset": "small data",
            "baseline": "Nearby Paper",
            "metric": ["accuracy"],
            "expected_signal": "large improvement",
            "estimated_cost_usd": 5,
        },
    }
    deferred_a = {
        **rejected,
        "idea": {
            **rejected["idea"],
            "id": "D3",
            "title": "Deferred evidence-driven idea",
            "mechanism_family": "evidence-driven adjustment",
        },
        "source": {
            **rejected["source"],
            "idea_origin": "cross_domain_analogy",
            "trigger_observation": "A weaker evidence pattern suggests a possible but underspecified mechanism.",
        },
        "decision": {
            "status": "deferred",
            "rejection_reason": ["needs stronger mechanism evidence"],
            "can_revisit_if": "D1 pilot exposes subgroup-specific failures.",
        },
        "counterfactual_check": "survives_weakened",
        "counterfactual_note": "The idea survives but becomes weak without stronger evidence.",
        "nearest_prior_work": {"work": "Related Evidence Paper", "distance": "distant"},
        "novelty_signal": "no_nearby_cluster",
    }
    deferred_b = {
        **rejected,
        "idea": {
            **rejected["idea"],
            "id": "D4",
            "title": "Deferred reverse-operation supplement",
            "mechanism_family": "reverse operation",
        },
        "source": {
            **rejected["source"],
            "idea_origin": "reverse_operation",
            "constraint_status": "supplement",
            "trigger_observation": "A reverse operation would be useful as a supplement but not as the mainline idea.",
        },
        "decision": {
            "status": "deferred",
            "rejection_reason": ["better as a supplement than standalone contribution"],
            "can_revisit_if": "Reverse-operation ablation becomes the strongest signal.",
        },
        "counterfactual_check": "survives_weakened",
        "counterfactual_note": "The reverse operation is a useful test but not a standalone story.",
        "nearest_prior_work": {"work": "none", "distance": "none_found"},
        "novelty_signal": "no_nearby_cluster",
    }
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(
            {"version": "1.0", "ideas": [selected, rejected, deferred_a, deferred_b]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_family_distribution.md").write_text(
        "## Mechanism Family Distribution\n\n"
        "### Family: targeted calibration\n- Candidates: D1\n- Distinct mainline mechanism.\n\n"
        "### Family: direct transfer\n- Candidates: D2\n- Rejected routine transfer.\n\n"
        "### Family: evidence-driven adjustment\n- Candidates: D3\n- Deferred but visible.\n\n"
        "### Family: reverse operation\n- Candidates: D4\n- Supplement only.\n\n"
        "## Summary\n\nTotal candidates: 4. Distinct families: 4. D1 is mainline free reasoning.\n",
        encoding="utf-8",
    )
    pass1 = [
        {
            **selected["idea"],
            "idea_origin": "free_reasoning",
            "constraint_status": "mainline",
            "basis_summary": "Generated from synthesis Q1 and a measurable mechanism gap.",
        },
        {
            **rejected["idea"],
            "idea_origin": "seed_refinement",
            "constraint_status": "mainline",
            "basis_summary": "Derived from a seed-like transfer option but too close to prior work.",
        },
        {
            **deferred_a["idea"],
            "idea_origin": "cross_domain_analogy",
            "constraint_status": "mainline",
            "basis_summary": "Derived as a cross-domain analogy candidate from weaker evidence patterns that need stronger mechanism support.",
        },
        {
            **deferred_b["idea"],
            "idea_origin": "reverse_operation",
            "constraint_status": "supplement",
            "basis_summary": "Generated as a reverse-operation supplement for testing the main mechanism.",
        },
    ]
    reviews = [
        {
            "idea_id": "D1",
            "screening_recommendation": "proceed",
            "visible_to_gate": True,
            "selection_warning": "none",
            "counterfactual_check": selected["counterfactual_check"],
            "counterfactual_note": selected["counterfactual_note"],
            "nearest_prior_work": selected["nearest_prior_work"],
            "novelty_signal": selected["novelty_signal"],
        },
        {
            "idea_id": "D2",
            "screening_recommendation": "reject_recommended",
            "visible_to_gate": True,
            "selection_warning": "too close to prior work",
            "counterfactual_check": rejected["counterfactual_check"],
            "counterfactual_note": rejected["counterfactual_note"],
            "nearest_prior_work": rejected["nearest_prior_work"],
            "novelty_signal": rejected["novelty_signal"],
        },
        {
            "idea_id": "D3",
            "screening_recommendation": "defer_recommended",
            "visible_to_gate": True,
            "selection_warning": "needs stronger mechanism evidence",
            "counterfactual_check": deferred_a["counterfactual_check"],
            "counterfactual_note": deferred_a["counterfactual_note"],
            "nearest_prior_work": deferred_a["nearest_prior_work"],
            "novelty_signal": deferred_a["novelty_signal"],
        },
        {
            "idea_id": "D4",
            "screening_recommendation": "revise_before_selection",
            "visible_to_gate": True,
            "selection_warning": "supplement only",
            "counterfactual_check": deferred_b["counterfactual_check"],
            "counterfactual_note": deferred_b["counterfactual_note"],
            "nearest_prior_work": deferred_b["nearest_prior_work"],
            "novelty_signal": deferred_b["novelty_signal"],
        },
    ]
    (workspace / "ideation" / "_pass1_forward_candidates.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "raw_forward_generation_candidates_visible_to_gate",
                "candidates": pass1,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_pass2_grounding_review.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "grounding_review_flags_not_deletion_or_final_quality_gate",
                "reviews": reviews,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_candidate_directions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "gate_visible_candidate_pool_after_grounding_review",
                "candidates": [
                    {
                        **candidate,
                        "pass2_screening": reviews[index],
                        "gate_visibility": "visible",
                        "can_select_despite_risk": True,
                    }
                    for index, candidate in enumerate(pass1)
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_gate1_candidate_cards.md").write_text(
        "# T4 Gate1 Candidate Cards\n\n"
        "## 排序 / 推荐动作\n"
        "- Rank 1: D1 select; Rank 2: D3 revise; Rank 3: D4 merge as ablation; Rank 4: D2 reject.\n\n"
        "## D1: mainline candidate\n"
        "- **Technical mechanism**: targeted mechanism changes sparse representations; prediction improves measurable target metrics; counterfactual no change if the mechanism is disabled.\n"
        "- **Practical / managerial / business implication**: decision makers can prioritize intervention budget for the subgroup where the mechanism matters most.\n"
        "- **Scores + score rationale**: novelty=4 feasibility=4 impact=4 evaluability=5 differentiation=3 cost=5 contribution_strength=4 because the mechanism and pilot are clear.\n"
        "- **Core paper dependencies**: synthesis.md Q1 and selected paper notes support the target constraint.\n"
        "- **Risk / kill criteria**: stop if the pilot is indistinguishable from baseline.\n\n"
        "## D2: rejected recommendation\n"
        "- **Technical mechanism**: direct transfer reuses an existing representation bias.\n"
        "- **Practical / managerial / business implication**: low practical research value if only the deployment setting changes.\n"
        "- **Scores + score rationale**: novelty=2 feasibility=4 impact=2 evaluability=2 differentiation=2 cost=4 contribution_strength=1 because it is close to prior work.\n"
        "- **Core paper dependencies**: Nearby Paper.\n"
        "- **Risk / kill criteria**: reject if novelty collapses into existing work.\n\n"
        "## D3: deferred evidence-driven option\n"
        "- **Technical mechanism**: failure-mode intervention changes subgroup behavior.\n"
        "- **Practical / managerial / business implication**: converts failure diagnosis into a targeted improvement workflow.\n"
        "- **Scores + score rationale**: novelty=3 feasibility=3 impact=3 evaluability=3 differentiation=3 cost=4 contribution_strength=2 because mechanism evidence is still weak.\n"
        "- **Core paper dependencies**: Failure Paper.\n"
        "- **Risk / kill criteria**: defer if no single counterfactual can be written.\n\n"
        "## D4: supplement reverse-operation option\n"
        "- **Technical mechanism**: removing a component tests whether it is necessary.\n"
        "- **Practical / managerial / business implication**: avoids spending engineering effort on unnecessary components.\n"
        "- **Scores + score rationale**: novelty=3 feasibility=5 impact=2 evaluability=5 differentiation=3 cost=5 contribution_strength=2 because it is best as a supporting ablation.\n"
        "- **Core paper dependencies**: Ablation Paper.\n"
        "- **Risk / kill criteria**: stop if it is only a routine ablation.\n\n"
        "Machine-readable artifacts: `ideation/_candidate_directions.json`, "
        "`ideation/_pass2_grounding_review.json`, `ideation/_pass1_forward_candidates.json`.\n",
        encoding="utf-8",
    )
    (workspace / "ideation" / "_gate1_selection_brief.md").write_text(
        "# Gate1 Selection Brief\n\n"
        "## Pass1 candidates\n\n"
        "- D1: mainline candidate, proceed.\n"
        "- D2: rejected recommendation, still visible.\n"
        "- D3: deferred evidence-driven option.\n"
        "- D4: supplement reverse-operation option.\n\n"
        "## Pass2 warnings\n\n"
        "- D1 has independent counterfactual support and moderate nearest prior distance.\n"
        "- D2 collapses into Nearby Paper and should not be selected without reframing.\n"
        "- D3 survives in weakened form but needs stronger mechanism evidence.\n"
        "- D4 is useful as a reverse-operation supplement, not a standalone paper story.\n\n"
        "## Merge options\n\n"
        "- 合并 D1+D3: use D1 as the main mechanism and D3 as an evidence-driven subgroup extension.\n"
        "- 合并 D1+D4: use D4 as a reverse-operation ablation for D1.\n\n"
        "## 集中度提示\n\n"
        "The candidates span four mechanism families, so the pool is not concentrated in a single prior paper.\n\n"
        "## Origin 分布\n\nfree_reasoning: 1; seed_refinement: 1; evidence_driven: 1; reverse_operation: 1.\n\n"
        "## Novelty-Utility 谱系排布\n\n"
        "High utility and medium novelty: D1. Low novelty routine transfer: D2. "
        "Higher uncertainty options: D3 and D4, both still visible for user choice.\n",
        encoding="utf-8",
    )
    (workspace / "ideation" / "selected_idea_brief.md").write_text(
        "# Selected Idea Brief\n\n"
        "## Gate1 用户选择\n"
        "- **Selected option**: select_or_reframe\n"
        "- **Captured feedback**: select D1\n"
        "- **Selection fingerprint**: fixture\n\n"
        "## Final selected idea\n"
        "- **Idea IDs**: D1\n"
        "- **One-line hypothesis**: A targeted calibration mechanism improves a measurable metric.\n"
        "- **Technical mechanism**: targeted calibration changes the measurable error pattern; prediction is validation accuracy improvement; counterfactual is no accuracy change when calibration is disabled.\n"
        "- **Practical / managerial / business implication**: decision makers can prioritize intervention budget for the subgroup where the mechanism matters most.\n"
        "- **Core paper dependencies**: Prior Paper / synthesis.md Q1; claim_used is that prior methods leave a measurable gap.\n"
        "- **Score rationale**: novelty=4, feasibility=4, impact=4, evaluability=5, differentiation=3, cost=5, contribution_strength=4.\n\n"
        "## Hypothesis scope\n"
        "- **H1**: validate the targeted calibration mechanism.\n\n"
        "## Rejected, deferred, or merged alternatives\n"
        "- D2 rejected, D3 deferred as cross-domain analogy, D4 deferred as reverse-operation supplement.\n",
        encoding="utf-8",
    )
    with (workspace / "ideation" / "rejected_ideas.md").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n## D3: Deferred evidence-driven idea\n\n"
            "- **Status**: deferred\n"
            "- **Why rejected**: needs stronger mechanism evidence.\n"
            "- **Can revisit if**: D1 pilot exposes subgroup-specific failures.\n\n"
            "## D4: Deferred reverse-operation supplement\n\n"
            "- **Status**: deferred\n"
            "- **Why rejected**: better as supplement than standalone contribution.\n"
            "- **Can revisit if**: reverse operation becomes the strongest signal.\n"
        )


class TestIdeationAgent:
    """Ideation Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = IdeationAgent()
        assert agent is not None
        assert agent.spec.name == "ideation"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = IdeationAgent()
        # ideation agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 ideation agent 没有 docker_exec 工具。"""
        agent = IdeationAgent()
        # ideation agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "## Method Families\n\n"
            "Family 1\n\n"
            "## Research Questions\n\n"
            "[p1] Question 1?\n",
            encoding="utf-8",
        )

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert len(msg) > 0


class TestIdeationAgentValidateOutputs:
    """Ideation Agent 输出验证测试。"""

    def test_validate_outputs_no_hypotheses(self, standard_workspace: Path, project_yaml: Path):
        """测试无假设文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "hypotheses.md" in err

    def test_validate_outputs_hypotheses_too_short(self, standard_workspace: Path, project_yaml: Path):
        """测试假设文件过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建过短的 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1", encoding="utf-8")

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err

    def test_validate_outputs_missing_exp_plan(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少实验计划时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Title\n\n"
            "### Hypothesis\n"
            "This is a test hypothesis with sufficient content.\n\n"
            "### Evidence\n"
            "Evidence supporting this hypothesis.\n\n"
            "This is a longer hypothesis document.\n" * 10,
            encoding="utf-8",
        )

        # 缺少 exp_plan.yaml
        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "exp_plan.yaml" in err

    def test_validate_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Test Hypothesis\n\n"
            "### Background\n"
            "This is a test hypothesis with sufficient content. The synthesis gap points to a pilotable mechanism and the closest prior work leaves a measurable gap.\n\n"
            "### Evidence and rationale\n"
            "- **Literature observation**: Prior Paper reports a measurable gap.\n"
            "- **Forward reasoning / problem reframing**: targeted calibration should directly affect the observed error pattern.\n"
            "- **Score rationale**: novelty=4, feasibility=4, impact=4, evaluability=5, differentiation=3, cost=5, contribution_strength=4.\n"
            "- **Core paper dependencies**: Prior Paper / synthesis.md Q1; claim_used is that prior methods leave a measurable gap.\n"
            "- **Confidence**: Medium.\n\n"
            "### Technical mechanism\n"
            "A targeted calibration mechanism changes the measurable error pattern. Prediction: accuracy improves on the target validation set. Counterfactual: disabling calibration should not change accuracy if the mechanism is irrelevant.\n\n"
            "### Practical / managerial / business implication\n"
            "Decision makers can prioritize intervention budget for the subgroup where the mechanism matters most.\n\n"
            "### Hypothesis\n"
            "The proposed targeted calibration mechanism improves a measurable metric.\n\n"
            "### Expected result\n"
            "Accuracy improves on the target validation set without exceeding budget.\n\n"
            "### Risk / falsification / kill criteria\n"
            "If the pilot fails or disabling calibration does not change accuracy, the hypothesis is falsified and should stop.\n",
            encoding="utf-8",
        )

        # 创建 exp_plan.yaml
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test Hypothesis\n"
            "    priority: high\n"
            "datasets:\n"
            "  - name: dataset1\n"
            "experiments:\n"
            "  - name: exp1\n"
            "    hypothesis_ref: H1\n"
            "    compute_estimate:\n"
            "      gpu_hours: 10\n",
            encoding="utf-8",
        )

        # 创建 risks.md（至少需要3条风险）
        risks = standard_workspace / "ideation" / "risks.md"
        risks.write_text(
            "# Risks\n\n"
            "## 风险 1: 数据质量风险\n\n"
            "数据可能存在噪声。\n\n"
            "## 风险 2: 计算资源风险\n\n"
            "可能超出预算。\n\n"
            "## 风险 3: 时间风险\n\n"
            "进度可能延迟。\n",
            encoding="utf-8",
        )
        _write_valid_idea_rationales(standard_workspace)

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err


class TestIdeationAgentHypothesisStructure:
    """Ideation Agent 假设结构测试。"""

    def test_hypothesis_has_required_fields(self, standard_workspace: Path, project_yaml: Path):
        """测试假设是否包含必需字段。"""
        from researchos.runtime.agent import ExecutionContext

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Efficient Attention\n\n"
            "### Hypothesis\n"
            "We propose a new attention mechanism that reduces complexity.\n\n"
            "### Mechanism\n"
            "The mechanism uses X to achieve O(n) complexity.\n\n"
            "### Evidence\n"
            "Prior work shows X is effective.\n\n"
            "### Risk Level\n"
            "Medium\n\n"
            "This is a test hypothesis.\n" * 20,
            encoding="utf-8",
        )

        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Efficient Attention\n"
            "    priority: high\n",
            encoding="utf-8",
        )

        # 验证假设内容
        content = hypotheses.read_text(encoding="utf-8")
        assert "Hypothesis" in content
        assert "Mechanism" in content
        assert "Evidence" in content
        assert "H1" in content
