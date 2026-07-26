"""Reusable valid T4.5 fixture for contract and T5 handoff tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from researchos.ideation.formalization import (
    compile_t45_derived_artifacts,
    write_post_novelty_formalization_manifest,
)
from researchos.ideation.proposal import repair_t45_proposal_manifest


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _paragraph(topic: str, count: int = 14) -> str:
    return " ".join(
        f"{topic} statement {index} specifies the proposed scope, evidentiary boundary, operational consequence, and decision rationale without claiming an observed result."
        for index in range(1, count + 1)
    )


def _claims_document() -> str:
    blocks = []
    for claim_id, label in (("TC1", "representation-aware estimator"), ("MC1", "diagnostic feedback mechanism")):
        blocks.append(
            "\n".join(
                [
                    f"### {claim_id}: {label}",
                    "Rationale: " + _paragraph(f"{claim_id} rationale", 10),
                    "Mechanism: " + _paragraph(f"{claim_id} mechanism", 10),
                    "Expected Observation: " + _paragraph(f"{claim_id} observation", 8),
                    "Evaluation: " + _paragraph(f"{claim_id} evaluation", 8),
                    "Competing Explanation: " + _paragraph(f"{claim_id} alternative", 8),
                    "Falsification Condition: " + _paragraph(f"{claim_id} falsification", 8),
                ]
            )
        )
    return "# Research Claims and Hypotheses\n\n" + "\n\n".join(blocks) + "\n"


def _proposal() -> str:
    return "\n\n".join(
        [
            "# Research Proposal",
            "## Research Motivation and Core Problem\n"
            + _paragraph("A platform decision team faces an unreliable treatment-effect estimate when agent-mediated decisions diverge from human response patterns", 15),
            "## Prior Research, Gap and Key Challenges\n"
            + "C1 assumes stable representations, but the assumption fails when decision traces are context dependent; the observable consequence is transport error and the required capability is representation-aware estimation. "
            + "C2 assumes a fixed feedback channel, but the assumption fails when explanations alter future decisions; the observable consequence is mechanism confounding and the required capability is diagnostic feedback modeling. "
            + "C3 assumes a stationary comparison set, but the assumption fails under changing policies; the observable consequence is brittle ranking and the required capability is robust policy-aware evaluation. "
            + _paragraph("Prior work comparison grounds each challenge in a bounded gap rather than a generic literature claim", 12),
            "## Proposed Approach and Design Rationale\n"
            + "Central Insight: a representation-aware estimator and a diagnostic feedback component should jointly expose the source of transport error before an intervention is ranked. "
            + "COMP1 learns a task-conditioned representation for C1 and its expected effect is to isolate response-relevant variation. COMP2 records and tests feedback pathways for C2 and its expected effect is to distinguish feedback from direct treatment response. "
            + "The design rationale links each component to a challenge. A simpler alternative that applies a static model without these components cannot distinguish contextual shift from a genuine treatment effect. "
            + _paragraph("The algorithmic model uses an explicit representation objective, an inference procedure, and a system-level diagnostic interface", 14),
            "## Research Questions, Claims and Hypotheses\n"
            + "TC1 states that COMP1 can reduce proposed transport error against the declared counterfactual baseline within its stated boundary conditions. MC1 states that COMP2 can reveal whether diagnostic feedback explains the observed shift rather than a spurious alternative. "
            + _paragraph("Each research claim remains falsifiable because its mechanism, expected observation, competing explanation, and evaluation path are explicit", 14),
            "## Research Design and Evaluation\n"
            + "Main effectiveness compares the proposed system with declared baseline estimators on the planned setting and primary metrics. Mechanism validation tests the COMP2 pathway; an ablation removes COMP1 and a separate ablation removes COMP2. Robustness evaluates distribution shift and policy variation. Efficiency records training and inference cost only when the setting permits measurement. Real-world relevance is assessed through deployment feasibility for platform analysts and decision owners rather than fabricated business outcomes. "
            + _paragraph("The evaluation protocol maps TC1 and MC1 to their experiment records, baselines, metrics, and error analysis", 14),
            "## Expected Contributions and Implications\n"
            + "Technical Contribution: the project proposes a representation-aware algorithm and diagnostic system artifact that make transport failure testable. Theoretical or design contribution: it explains when feedback changes the meaning of observed response traces. Practical implication: platform analysts and product decision owners can decide when automation requires additional diagnostic controls. "
            + "For the Hybrid orientation, the cross-level mechanism links the technical diagnostic property to platform decision oversight and user-facing deployment consequences. "
            + _paragraph("Each contribution is conditional on the linked claim and planned evaluation rather than stated as an already achieved result", 14),
            "## Risks, Limitations and Execution Plan\n"
            + "Novelty risk is mitigated by retaining the required baseline family and narrowing claims when overlap is confirmed. Technical risk is mitigated by a fallback design that evaluates COMP1 without COMP2. Data and measurement risk are recorded as unknown until resources are verified. Kill criteria stop a component-level claim when its ablation or falsification condition fails. The execution plan stages resource verification, implementation, reproducible evaluation, and result diagnosis. "
            + _paragraph("Each milestone records a mitigation, fallback, or stopping condition instead of hiding feasibility uncertainty", 14),
        ]
    ) + "\n"


def populate_valid_t45_workspace(tmp_path: Path, *, orientation: str = "ccf_a") -> None:
    """Create a rich, internally consistent formalization without fake results."""

    profile_type = {"ccf_a": "ccf_cs", "utd": "utd_is", "hybrid": "hybrid"}[orientation]
    write(tmp_path / "project.yaml", "project_id: unified-t45\nresearch_question: How can treatment-effect estimation remain reliable under agent-mediated decisions?\n")
    write(tmp_path / "literature/synthesis.md", "# Synthesis\nThe available corpus motivates a bounded proposed study.\n")
    write(tmp_path / "literature/synthesis_workbench.json", "{}\n")
    write(tmp_path / "literature/domain_map.json", "{}\n")
    write(tmp_path / "literature/comparison_table.csv", "method,role\nCausal forest,baseline\n")
    write_json = lambda path, payload: write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    write_json(tmp_path / "ideation/t4_run_config.json", {"mode": "standard", "rounds": 1, "allow_crossover": False, "final_top_k": 1, "target_profile": {"profile_type": profile_type, "target_venues": [], "user_instruction": ""}})
    write_json(
        tmp_path / "ideation/selected/selected_candidate.json",
        {"candidate_id": "D1", "selection_fingerprint": "unified-fingerprint", "candidate": {"id": "D1", "target_problem": "Agent-mediated decision traces can invalidate transport assumptions."}},
    )
    write_yaml(tmp_path / "ideation/hypothesis_brief.yaml", {"status": "draft_for_novelty_review", "draft_hypotheses": [{"id": "H1", "statement": "A representation-aware design can diagnose transport error."}]})
    write(tmp_path / "ideation/novelty_audit.md", "# Novelty Audit\n\n## H1\nLevel 2\nCollision Axis\nAmbition Axis\nContribution Distance\n\nFinal Gate Verdict: pass_to_experiment\n")

    blueprint = {
        "semantics": "t45_research_blueprint",
        "schema_version": "1.0.0",
        "orientation": {"profile_type": orientation, "target_venues": [], "user_instruction": "", "proposal_weights": {"methodology": 0.25}, "guidance": "Use one unified, evidence-bounded template with technical and practical reasoning."},
        "core_problem": {"real_world_context": "Platform teams increasingly delegate treatment and ranking decisions to agent-mediated workflows with changing response traces.", "affected_actors": ["platform analysts", "product decision owners"], "decision_or_task": "Estimate and rank interventions when observed decisions may not transport across agent contexts.", "observed_failure": "Static response assumptions can confound contextual shift, feedback, and genuine treatment effects in operational decisions.", "scientific_significance": "The problem asks when a computational estimator remains identifiable and useful after the decision-maker changes."},
        "technical_problem": {"computational_abstraction": "Learn a treatment-effect estimator that separates task-conditioned representation shift from feedback-mediated response variation.", "current_assumptions": ["Existing estimators treat historical representations as stable across decision contexts."], "failure_mechanisms": ["Contextual shift and feedback alter the relation between traces and treatment response."], "key_challenges": [{"id": "C1", "existing_assumption": "Representations are stable across contexts.", "why_it_fails": "Agent-mediated context changes the signals used for decisions.", "observable_consequence": "Treatment effects can appear to transport when they do not.", "required_new_capability": "Learn task-conditioned response representations."}, {"id": "C2", "existing_assumption": "Feedback does not alter future response traces.", "why_it_fails": "Explanations and diagnostics can change later decisions.", "observable_consequence": "Observed improvement may be mechanism confounding.", "required_new_capability": "Model and test feedback pathways explicitly."}, {"id": "C3", "existing_assumption": "Comparison policies remain stationary.", "why_it_fails": "Platforms revise policies while models are evaluated.", "observable_consequence": "Ranking can be brittle under policy shift.", "required_new_capability": "Evaluate policy-aware robustness."}]},
        "proposed_approach": {"artifact_type": "algorithm", "central_insight": "Jointly modeling representation shift and diagnostic feedback makes it possible to distinguish transport failure from a real intervention effect.", "components": [{"id": "COMP1", "name": "Task-conditioned representation estimator", "challenge_refs": ["C1", "C3"], "technical_role": "Learns response-relevant context features before treatment-effect estimation.", "expected_effect": "Reduces proposed transport error under contextual variation."}, {"id": "COMP2", "name": "Diagnostic feedback module", "challenge_refs": ["C2"], "technical_role": "Measures whether explanations and diagnostics change future response traces.", "expected_effect": "Separates feedback mechanisms from direct treatment response."}], "design_rationales": [{"component_id": "COMP1", "rationale": "The estimator is needed because static features cannot isolate contextual shift."}, {"component_id": "COMP2", "rationale": "The diagnostic module is needed because feedback is a competing explanation."}], "alternatives_considered": [{"alternative": "Static estimator", "reason_not_sufficient": "It cannot distinguish feedback and representation shift."}]},
        "research_claims": {"active_claim_ids": ["TC1", "MC1"], "cross_level_links": ([{"technical_design": "COMP2", "system_property": "diagnostic transparency", "outcome": "better platform decision oversight"}] if orientation == "hybrid" else [])},
        "evaluation": {"datasets_or_setting": [{"setting": "proposed controlled agent-mediated decision setting", "status": "unknown_until_resource_verification"}], "baselines": [{"name": "Causal forest", "status": "required comparison"}], "primary_metrics": [{"name": "planned treatment-effect error metric"}], "main_tests": [{"claim_ref": "TC1", "description": "Compare the proposed estimator with declared baselines under the shared protocol."}], "ablations": [{"component_id": "COMP1", "planned_test": "Remove task-conditioned representations while holding the remaining protocol fixed."}, {"component_id": "COMP2", "planned_test": "Remove diagnostic feedback while holding the estimator fixed."}], "mechanism_tests": [{"component_id": "COMP1", "planned_test": "Measure whether contextual representations account for transport variation."}, {"component_id": "COMP2", "planned_test": "Measure whether feedback explains a subsequent response shift."}], "robustness_tests": [{"condition": "context and policy shift"}], "efficiency_tests": [{"condition": "record cost when resources permit"}], "real_world_validation": [{"actor": "platform analyst", "decision": "deployment feasibility"}]},
        "contributions": {"technical": [{"statement": "A representation-aware estimator and diagnostic module for agent-mediated treatment-effect transport."}], "theoretical_or_design": [{"statement": "A design explanation linking feedback and representation shift to transport failure."}], "practical_or_managerial": [{"statement": "A diagnostic basis for platform analysts deciding when automated intervention ranking needs oversight."}]},
        "risks": {"novelty_risks": [{"risk": "Nearest work may narrow the novelty boundary.", "mitigation": "Retain required baselines and narrow claims."}], "technical_risks": [{"risk": "Feedback may be difficult to measure.", "mitigation": "Use an estimator-only fallback."}], "data_or_experimental_risks": [{"risk": "Setting availability is unknown.", "mitigation": "Verify resources before implementation."}], "fallback_designs": [{"design": "Evaluate COMP1 without COMP2 when diagnostic signals are unavailable."}], "kill_criteria": [{"condition": "A component ablation or falsification test fails.", "action": "Stop the component-level claim and report the narrowed result."}]},
    }
    claims = {
        "semantics": "t45_claim_registry",
        "schema_version": "1.0.0",
        "orientation": orientation,
        "claims": [
            {"id": "TC1", "claim_type": "technical", "statement": "COMP1 can reduce proposed transport error relative to the declared baseline under bounded contextual variation.", "rationale": "Task-conditioned features address the representation instability stated in C1.", "related_components": ["COMP1"], "mechanism": "Context-sensitive representation learning isolates response variation that static features conflate.", "scope_or_boundary_conditions": "The claim is limited to settings where relevant context signals can be observed.", "expected_observation": "The proposed estimator has lower error than the declared static comparison.", "baseline_or_counterfactual": "Compare with a static causal-forest baseline under the same protocol.", "evaluation_methods": ["main effectiveness comparison", "COMP1 ablation"], "competing_explanation": "Any change may be due to feedback rather than representation shift.", "falsification_condition": "The claim fails if the baseline matches performance or COMP1 ablation shows no attributable effect."},
            {"id": "MC1", "claim_type": "mechanism", "statement": "COMP2 can diagnose whether feedback rather than direct treatment response explains a proposed outcome shift.", "rationale": "Feedback is the competing mechanism identified in C2.", "related_components": ["COMP2"], "mechanism": "The diagnostic module measures changes in subsequent traces after explanation exposure.", "scope_or_boundary_conditions": "The claim applies only when a feedback channel can be recorded or simulated transparently.", "expected_observation": "Mechanism tests distinguish feedback-associated change from direct treatment response.", "baseline_or_counterfactual": "Compare with a no-diagnostic or feedback-blind condition.", "evaluation_methods": ["mechanism validation", "COMP2 ablation"], "competing_explanation": "Context drift may explain the observed shift without feedback.", "falsification_condition": "The claim fails if feedback-blind conditions explain the shift equally well."},
        ],
        "dropped_claims": [],
    }
    write_yaml(tmp_path / "ideation/research_blueprint.yaml", blueprint)
    write_yaml(tmp_path / "ideation/claim_registry.yaml", claims)
    write_yaml(tmp_path / "ideation/exp_plan.yaml", {"goal": "Evaluate the two pre-experiment research claims without fabricating results.", "experiments": [{"id": "EXP-TC1", "name": "Main effectiveness and COMP1 ablation", "claim_refs": ["TC1"], "datasets": ["unknown_until_resource_verification"], "required_baselines": ["Causal forest"], "metrics": ["planned treatment-effect error metric"], "steps": ["compare declared baselines", "ablate COMP1"]}, {"id": "EXP-MC1", "name": "Feedback mechanism and COMP2 ablation", "claim_refs": ["MC1"], "datasets": ["unknown_until_resource_verification"], "required_baselines": ["Causal forest"], "metrics": ["planned feedback diagnostic"], "steps": ["run mechanism test", "ablate COMP2"]}]})
    write(tmp_path / "ideation/hypotheses.md", _claims_document())
    write(tmp_path / "ideation/proposal/research_proposal.md", _proposal())
    compiled, error = compile_t45_derived_artifacts(tmp_path, tmp_path / "ideation/novelty_audit.md")
    assert compiled, error
    write_json(tmp_path / "ideation/orientation_review.json", {"semantics": "t45_orientation_aware_review", "orientation": orientation, "status": "accepted", "scores": {"problem_significance": 3.5, "technical_novelty": 3.5, "technical_completeness": 3.5, "design_rationale": 3.5, "claim_clarity": 3.5, "evaluation_rigor": 3.5, "mechanism_validation": 3.5, "theoretical_or_design_value": 3.5, "practical_significance": 3.5, "cross_artifact_consistency": 3.5, "writing_quality": 3.5, "cross_level_integration": 3.5}, "issues": [], "repair_summary": ["Initial fixture is internally consistent."]})
    repaired, error = repair_t45_proposal_manifest(tmp_path, tmp_path / "ideation/novelty_audit.md")
    assert repaired or error is None, error
    write_post_novelty_formalization_manifest(tmp_path)
