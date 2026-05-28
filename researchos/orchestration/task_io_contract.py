from __future__ import annotations

"""每个 task 的输入/输出 artifact 契约。

说明：
- 该文件主要服务于 single-task 调试模式、前置输入校验和 artifact 拷贝；
- 契约以 Agent Dev Spec 的模式 B I/O 表为主，同时遵守当前 runtime 使用 `state.yaml`
  的实现现实；
- HELLO 不是正式 T-stage，而是当前仓库已经跑通的最小 runtime 调试任务，因此也保留一份
  专用契约方便测试与 smoke run。
"""

from pathlib import Path


TASK_IO_CONTRACTS: dict[str, dict[str, object]] = {
    "HELLO": {
        "inputs": {},
        "outputs": {"hello_file": "hello.txt"},
        "required_inputs": [],
        "schemas": {},
    },
    "T1": {
        "inputs": {},
        "outputs": {
            "project": "project.yaml",
            "state": "state.yaml",
            # seed 文件是可选的，如果用户没有提供则可以不创建或创建空文件
        },
        "required_inputs": [],
        "schemas": {
            "project": "project",
        },
    },
    "T2": {
        "inputs": {
            "project": "project.yaml",
            "seed_papers": "user_seeds/seed_papers.jsonl",
            "seed_constraints": "user_seeds/seed_constraints.md",
            "seed_ideas": "user_seeds/seed_ideas.md",
            "seed_external_resources": "user_seeds/seed_external_resources.jsonl",
        },
        "outputs": {
            "papers_raw": "literature/papers_raw.jsonl",
            "papers_dedup": "literature/papers_dedup.jsonl",
            "papers_verified": "literature/papers_verified.jsonl",
            "verification_failures": "literature/verification_failures.jsonl",
            "deep_read_queue": "literature/deep_read_queue.jsonl",
            "access_audit": "literature/access_audit.md",
            "search_log": "literature/search_log.md",
            "missing_areas": "literature/missing_areas.md",
        },
        "required_inputs": ["project"],
        "schemas": {
            "papers_raw": "papers_raw",
            "papers_dedup": "papers_dedup",
            "papers_verified": "papers_verified",
            "verification_failures": "verification_failure",
            "deep_read_queue": "deep_read_queue",
        },
    },
    "T3": {
        "inputs": {
            "project": "project.yaml",
            "papers_dedup": "literature/papers_dedup.jsonl",
            "papers_verified": "literature/papers_verified.jsonl",
            "deep_read_queue": "literature/deep_read_queue.jsonl",
            "deep_read_queue_pending": "literature/deep_read_queue_pending.jsonl",
            "access_audit": "literature/access_audit.md",
            "missing_areas": "literature/missing_areas.md",
        },
        "outputs": {
            "paper_notes_dir": "literature/paper_notes",
            "comparison_table": "literature/comparison_table.csv",
            "related_work_bib": "literature/related_work.bib",
        },
        "required_inputs": ["project", "papers_dedup"],
        "schemas": {},
    },
    "T3.5": {
        "inputs": {
            "project": "project.yaml",
            "paper_notes_dir": "literature/paper_notes",
            "comparison_table": "literature/comparison_table.csv",
            "missing_areas": "literature/missing_areas.md",
        },
        "outputs": {
            "synthesis": "literature/synthesis.md",
            "synthesis_workbench": "literature/synthesis_workbench.json",
            "synthesis_outline": "literature/synthesis_outline.md",
            "synthesis_draft": "literature/synthesis_draft.md",
        },
        "required_inputs": ["project", "paper_notes_dir", "comparison_table"],
        "schemas": {},
    },
    "T4": {
        "inputs": {
            "project": "project.yaml",
            "synthesis": "literature/synthesis.md",
            "comparison_table": "literature/comparison_table.csv",
            "missing_areas": "literature/missing_areas.md",
            "seed_ideas": "user_seeds/seed_ideas.md",
            "seed_constraints": "user_seeds/seed_constraints.md",
        },
        "outputs": {
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "risks": "ideation/risks.md",
            "idea_rationales": "ideation/idea_rationales.json",
            "idea_scorecard": "ideation/idea_scorecard.yaml",
            "rejected_ideas": "ideation/rejected_ideas.md",
            "gate_decisions": "ideation/gate_decisions.json",
            "family_distribution": "ideation/_family_distribution.md",
            "candidate_directions": "ideation/_candidate_directions.json",
        },
        "required_inputs": ["project", "synthesis"],
        "schemas": {
            "exp_plan": "exp_plan",
            "idea_rationales": "idea_rationales",
            "idea_scorecard": "idea_scorecard",
            "gate_decisions": "gate_decisions",
        },
    },
    "T4.5": {
        "inputs": {
            "project": "project.yaml",
            "hypotheses": "ideation/hypotheses.md",
            "synthesis": "literature/synthesis.md",
            "comparison_table": "literature/comparison_table.csv",
            "idea_scorecard": "ideation/idea_scorecard.yaml",
        },
        "outputs": {
            "novelty_audit": "ideation/novelty_audit.md",
            "mechanism_tuples_dir": "ideation/_mechanism_tuples",
            "design_rationale_tuples_dir": "ideation/_design_rationale_tuples",
            "collision_cases": "ideation/collision_cases.md",
        },
        "optional_outputs": ["collision_cases"],
        "required_inputs": ["project", "hypotheses", "synthesis"],
        "schemas": {},
    },
    "T5": {
        "inputs": {
            "project": "project.yaml",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "risks": "ideation/risks.md",
        },
        "outputs": {
            "pilot_plan": "pilot/pilot_plan.yaml",
            "pilot_code": "pilot/pilot_code",
            "pilot_results": "pilot/pilot_results.json",
            "motivation_validation": "pilot/motivation_validation.md",
        },
        "required_inputs": ["project", "hypotheses", "exp_plan"],
        "schemas": {
            "pilot_plan": "pilot_plan",
            "pilot_results": "pilot_results",
        },
    },
    "T6": {
        "inputs": {
            "project": "project.yaml",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "pilot_results": "pilot/pilot_results.json",
            "motivation_validation": "pilot/motivation_validation.md",
            "novelty_audit": "ideation/novelty_audit.md",
            "comparison_table": "literature/comparison_table.csv",
            "synthesis": "literature/synthesis.md",
        },
        "outputs": {
            "novelty_report": "novelty/novelty_report.md",
            "collision_cases": "novelty/collision_cases.md",
            "must_add_baselines": "novelty/must_add_baselines.md",
        },
        "required_inputs": [
            "project",
            "hypotheses",
            "exp_plan",
            "pilot_results",
            "motivation_validation",
            "novelty_audit",
            "synthesis",
        ],
        "schemas": {},
    },
    "T7": {
        "inputs": {
            "project": "project.yaml",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "risks": "ideation/risks.md",
            "novelty_audit": "ideation/novelty_audit.md",
            "idea_scorecard": "ideation/idea_scorecard.yaml",
            "synthesis": "literature/synthesis.md",
            "comparison_table": "literature/comparison_table.csv",
            "pilot_results": "pilot/pilot_results.json",
            "pilot_code": "pilot/pilot_code",
            "novelty_report": "novelty/novelty_report.md",
            "must_add_baselines": "novelty/must_add_baselines.md",
        },
        "outputs": {
            "results_summary": "experiments/results_summary.json",
            "runs_dir": "experiments/runs",
            "configs_dir": "experiments/configs",
            "iteration_log": "experiments/iteration_log.md",
            "ablations": "experiments/ablations.csv",
        },
        "required_inputs": [
            "project",
            "hypotheses",
            "exp_plan",
            "novelty_audit",
            "synthesis",
        ],
        "schemas": {
            "results_summary": "results_summary",
        },
    },
    "T7.5": {
        "inputs": {
            "results_summary": "experiments/results_summary.json",
            "iteration_log": "experiments/iteration_log.md",
            "exp_plan": "ideation/exp_plan.yaml",
        },
        "outputs": {
            "evaluation_decision": "evaluation/evaluation_decision.md",
        },
        "required_inputs": ["results_summary"],
        "schemas": {},
    },
    "T8": {
        "inputs": {
            "project": "project.yaml",
            "synthesis": "literature/synthesis.md",
            "results_summary": "experiments/results_summary.json",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "novelty_audit": "ideation/novelty_audit.md",
            "ablations": "experiments/ablations.csv",
        },
        "outputs": {
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "outline": "drafts/outline.md",
            "paper_state": "drafts/paper_state.json",
            "section_outlines_dir": "drafts/section_outlines",
            "sections_dir": "drafts/sections",
            "paper": "drafts/paper.tex",
            "manuscript_audit": "drafts/manuscript_audit.md",
            "self_check": "drafts/self_check.md",
        },
        "required_inputs": [
            "project",
            "synthesis",
            "related_work_bib",
            "hypotheses",
            "results_summary",
        ],
        "schemas": {},
    },
    "T8-RESOURCE": {
        "inputs": {
            "project": "project.yaml",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "novelty_audit": "ideation/novelty_audit.md",
            "comparison_table": "literature/comparison_table.csv",
            "ablations": "experiments/ablations.csv",
        },
        "outputs": {
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "required_inputs": [
            "project",
            "results_summary",
            "synthesis",
            "related_work_bib",
            "hypotheses",
        ],
        "schemas": {},
    },
    "T8-WRITE": {
        "inputs": {
            "project": "project.yaml",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {
            "outline": "drafts/outline.md",
        },
        "required_inputs": [
            "project",
            "results_summary",
            "synthesis",
            "related_work_bib",
            "hypotheses",
            "manuscript_resource_index",
            "section_plan",
            "evidence_plan",
            "figure_table_plan",
            "cdr_claim_ledger",
            "claim_ledger",
            "figure_registry",
        ],
        "schemas": {},
    },
    "T8-SECTION-PLAN": {
        "inputs": {
            "project": "project.yaml",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "outline": "drafts/outline.md",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
            "ablations": "experiments/ablations.csv",
        },
        "outputs": {
            "paper_state": "drafts/paper_state.json",
            "section_outlines_dir": "drafts/section_outlines",
        },
        "required_inputs": [
            "project",
            "results_summary",
            "synthesis",
            "related_work_bib",
            "hypotheses",
            "outline",
            "manuscript_resource_index",
            "section_plan",
            "evidence_plan",
            "figure_table_plan",
            "cdr_claim_ledger",
            "claim_ledger",
            "figure_registry",
        ],
        "schemas": {},
    },
    "T8-SEC-METHOD": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/methodology.md",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "results_summary": "experiments/results_summary.json",
            "outline": "drafts/outline.md",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/methodology.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "hypotheses"],
        "schemas": {},
    },
    "T8-SEC-EXPERIMENTS": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/experiments.md",
            "results_summary": "experiments/results_summary.json",
            "ablations": "experiments/ablations.csv",
            "exp_plan": "ideation/exp_plan.yaml",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/experiments.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "results_summary"],
        "schemas": {},
    },
    "T8-SEC-RELATED": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/related_work.md",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "comparison_table": "literature/comparison_table.csv",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/related_work.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "synthesis", "related_work_bib"],
        "schemas": {},
    },
    "T8-SEC-ANALYSIS": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/analysis.md",
            "results_summary": "experiments/results_summary.json",
            "ablations": "experiments/ablations.csv",
            "novelty_audit": "ideation/novelty_audit.md",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/analysis.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "results_summary"],
        "schemas": {},
    },
    "T8-SEC-INTRO": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/introduction.md",
            "synthesis": "literature/synthesis.md",
            "hypotheses": "ideation/hypotheses.md",
            "results_summary": "experiments/results_summary.json",
            "methodology_section": "drafts/sections/methodology.tex",
            "experiments_section": "drafts/sections/experiments.tex",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/introduction.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "methodology_section", "experiments_section"],
        "schemas": {},
    },
    "T8-SEC-LIMITATIONS": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/limitations.md",
            "risks": "ideation/risks.md",
            "novelty_audit": "ideation/novelty_audit.md",
            "results_summary": "experiments/results_summary.json",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/limitations.tex"},
        "required_inputs": ["project", "paper_state", "section_outline"],
        "schemas": {},
    },
    "T8-SEC-CONCLUSION": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/conclusion.md",
            "introduction_section": "drafts/sections/introduction.tex",
            "experiments_section": "drafts/sections/experiments.tex",
            "limitations_section": "drafts/sections/limitations.tex",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/conclusion.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "introduction_section", "experiments_section"],
        "schemas": {},
    },
    "T8-SEC-ABSTRACT": {
        "inputs": {
            "project": "project.yaml",
            "paper_state": "drafts/paper_state.json",
            "section_outline": "drafts/section_outlines/abstract.md",
            "introduction_section": "drafts/sections/introduction.tex",
            "methodology_section": "drafts/sections/methodology.tex",
            "experiments_section": "drafts/sections/experiments.tex",
            "analysis_section": "drafts/sections/analysis.tex",
            "limitations_section": "drafts/sections/limitations.tex",
            "conclusion_section": "drafts/sections/conclusion.tex",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {"section": "drafts/sections/abstract.tex"},
        "required_inputs": ["project", "paper_state", "section_outline", "introduction_section", "methodology_section", "experiments_section", "conclusion_section"],
        "schemas": {},
    },
    "T8-SECTIONS": {
        "inputs": {
            "project": "project.yaml",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "outline": "drafts/outline.md",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "ablations": "experiments/ablations.csv",
        },
        "outputs": {
            "sections_dir": "drafts/sections",
        },
        "required_inputs": [
            "project",
            "results_summary",
            "synthesis",
            "related_work_bib",
            "hypotheses",
            "outline",
            "manuscript_resource_index",
            "section_plan",
            "evidence_plan",
            "figure_table_plan",
        ],
        "schemas": {},
    },
    "T8-DRAFT": {
        "inputs": {
            "project": "project.yaml",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "novelty_audit": "ideation/novelty_audit.md",
            "ablations": "experiments/ablations.csv",
            "outline": "drafts/outline.md",
            "paper_state": "drafts/paper_state.json",
            "sections_dir": "drafts/sections",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
        },
        "outputs": {
            "paper": "drafts/paper.tex",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "required_inputs": ["project", "results_summary", "outline", "paper_state", "sections_dir"],
        "schemas": {},
    },
    "T8-SELF-CHECK": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "ablations": "experiments/ablations.csv",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "section_plan": "drafts/section_plan.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "outputs": {
            "self_check": "drafts/self_check.md",
        },
        "required_inputs": ["project", "paper", "results_summary", "related_work_bib"],
        "schemas": {},
    },
    "T8-REVIEW-1": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "results_summary": "experiments/results_summary.json",
            "related_work_bib": "literature/related_work.bib",
            "manuscript_audit": "drafts/manuscript_audit.md",
            "self_check": "drafts/self_check.md",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
        },
        "outputs": {
            "review_report": "drafts/review_rounds/round_1.md",
            "section_review_dir": "drafts/review_rounds/round_1_sections",
        },
        "required_inputs": ["project", "paper", "results_summary"],
        "schemas": {},
    },
    "T8-REVISE-1": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "paper_state": "drafts/paper_state.json",
            "review_report": "drafts/review_rounds/round_1.md",
            "section_review_dir": "drafts/review_rounds/round_1_sections",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "ablations": "experiments/ablations.csv",
            "sections_dir": "drafts/sections",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "outputs": {
            "revision_patches": "drafts/patches/round_1_patches.json",
            "revision_response": "drafts/revision_response_round_1.md",
            "paper": "drafts/paper.tex",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "required_inputs": ["project", "paper", "paper_state", "review_report", "section_review_dir"],
        "schemas": {},
    },
    "T8-REVIEW-2": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "results_summary": "experiments/results_summary.json",
            "related_work_bib": "literature/related_work.bib",
            "manuscript_audit": "drafts/manuscript_audit.md",
            "previous_review": "drafts/review_rounds/round_1.md",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
        },
        "outputs": {
            "review_report": "drafts/review_rounds/round_2.md",
            "section_review_dir": "drafts/review_rounds/round_2_sections",
        },
        "required_inputs": ["project", "paper"],
        "schemas": {},
    },
    "T8-REVISE-2": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "paper_state": "drafts/paper_state.json",
            "review_report": "drafts/review_rounds/round_2.md",
            "section_review_dir": "drafts/review_rounds/round_2_sections",
            "previous_review": "drafts/review_rounds/round_1.md",
            "results_summary": "experiments/results_summary.json",
            "synthesis": "literature/synthesis.md",
            "related_work_bib": "literature/related_work.bib",
            "ablations": "experiments/ablations.csv",
            "sections_dir": "drafts/sections",
            "manuscript_resource_index": "drafts/manuscript_resource_index.json",
            "evidence_plan": "drafts/evidence_plan.json",
            "figure_table_plan": "drafts/figure_table_plan.json",
            "cdr_claim_ledger": "drafts/cdr_claim_ledger.json",
            "claim_ledger": "drafts/claim_ledger.json",
            "figure_registry": "drafts/figure_registry.json",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "outputs": {
            "revision_patches": "drafts/patches/round_2_patches.json",
            "revision_response": "drafts/revision_response_round_2.md",
            "paper": "drafts/paper.tex",
            "manuscript_audit": "drafts/manuscript_audit.md",
        },
        "required_inputs": ["project", "paper", "paper_state", "review_report", "section_review_dir"],
        "schemas": {},
    },
    "T9": {
        "inputs": {
            "project": "project.yaml",
            "paper": "drafts/paper.tex",
            "related_work_bib": "literature/related_work.bib",
        },
        "outputs": {
            "bundle_dir": "submission/bundle",
            "migration_report": "submission/migration_report.md",
            "main_tex": "submission/bundle/main.tex",
            "references_bib": "submission/bundle/references.bib",
            "main_pdf": "submission/bundle/main.pdf",
            "compile_log": "submission/bundle/main.log",
            "compile_report": "submission/compile_report.json",
        },
        "required_inputs": ["project", "paper", "related_work_bib"],
        "optional_outputs": [],
    },
}


def get_task_io(task_id: str) -> dict[str, object]:
    """读取 task 的 I/O 契约。"""
    if task_id not in TASK_IO_CONTRACTS:
        raise KeyError(f"Unknown task I/O contract: {task_id}")
    return TASK_IO_CONTRACTS[task_id]


def resolve_outputs(workspace: Path, task_id: str) -> dict[str, Path]:
    """把 output 相对路径解析成 workspace 内绝对路径。"""
    contract = get_task_io(task_id)
    optional = set(contract.get("optional_outputs", []))
    outputs = {
        name: rel
        for name, rel in contract["outputs"].items()  # type: ignore[union-attr]
        if name not in optional
    }
    return {name: workspace / rel for name, rel in outputs.items()}


def resolve_inputs(workspace: Path, task_id: str) -> dict[str, Path]:
    """把 input 相对路径解析成 workspace 内绝对路径。"""
    inputs = get_task_io(task_id)["inputs"]
    return {name: workspace / rel for name, rel in inputs.items()}  # type: ignore[union-attr]


def required_input_names(task_id: str) -> list[str]:
    """返回某个 task 的必需前置输入 key 列表。

    兼容策略：
    - 若契约显式声明了 `required_inputs`，则严格使用它；
    - 若未来某个调试 task 只定义了 `inputs` 没定义 `required_inputs`，则保守退回成
      “所有输入都必需”，避免 single-task 模式误判可运行。
    """

    contract = get_task_io(task_id)
    required = contract.get("required_inputs")
    if required is None:
        inputs = contract.get("inputs", {})
        return list(inputs.keys())  # type: ignore[union-attr]
    return list(required)  # type: ignore[arg-type]
