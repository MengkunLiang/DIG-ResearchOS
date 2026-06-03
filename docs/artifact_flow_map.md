# Artifact Flow Map

本文档描述 Pre-T5、外部实验链、T8/T9 之间的文件流转。

## Pre-T5

```text
T2
  literature/papers_raw.jsonl
  literature/papers_verified.jsonl
  literature/citation_edges.json
  literature/domain_map.json
  literature/deep_read_queue.jsonl

T3
  literature/paper_notes/*.md
  literature/paper_notes_abstract/*.md
  literature/comparison_table.csv
  literature/related_work.bib

T3.5
  literature/synthesis_workbench.json
  literature/synthesis_outline.md
  literature/synthesis_draft.md
  literature/synthesis.md

T4
  ideation/hypotheses.md
  ideation/exp_plan.yaml
  ideation/risks.md
  ideation/idea_scorecard.yaml
  ideation/idea_rationales.json
  ideation/gate_decisions.json

T4.5
  ideation/novelty_audit.md
  ideation/_mechanism_tuples/
  ideation/_design_rationale_tuples/
```

## Experiment

```text
T5-HANDOFF
  external_executor/handoff_pack.json
  external_executor/expected_outputs_schema.json
  external_executor/allowed_paths.txt
  external_executor/AGENTS.md
  external_executor/CLAUDE.md
  external_executor/job_state.json
  external_executor/codex_prompt.md
  external_executor/claude_code_prompt.md

T5-EXECUTOR-GATE
  external_executor/executor_selection.json

T5-EXTERNAL-WAIT
  external_executor/wait_acceptance_report.json

external executor / T5-DRY-RUN
  external_executor/result_pack.json
  external_executor/executor_status.json
  external_executor/run_manifest.json
  external_executor/raw_results/*
  external_executor/configs/*
  external_executor/logs/*

T7-INGEST
  experiments/results_summary.json
  experiments/run_records.jsonl
  experiments/evidence_index.json
  experiments/ingest_report.json

T7-AUDIT
  experiments/integrity_audit.json
  experiments/experiment_fairness_review.md

T7-POST-NOVELTY
  novelty/post_experiment_novelty_check.json
  novelty/post_experiment_collision_cases.md

T7-CLAIMS
  experiments/experimental_claims.json
  drafts/result_to_claim.json
  drafts/must_not_claim.md
  drafts/claim_support_matrix.csv
  drafts/experiment_evidence_pack.json
  experiments/iteration_log.md
```

## Writing

```text
T8-RESOURCE
  drafts/manuscript_resource_index.json
  drafts/section_plan.json
  drafts/evidence_plan.json
  drafts/figure_table_plan.json
  drafts/cdr_claim_ledger.json
  drafts/claim_ledger.json
  drafts/figure_registry.json
  drafts/alignment_matrix.json

T8-WRITE
  drafts/outline.md

T8-SECTION-PLAN
  drafts/paper_state.json
  drafts/section_outlines/*.md

T8-SEC-*
  drafts/sections/*.tex

T8-DRAFT / REVISE
  drafts/paper.tex
  drafts/manuscript_audit.md
  drafts/craft_audit.md
  drafts/paper_claim_audit.md
  drafts/paper_claim_audit.json

T9
  submission/bundle/
  submission/compile_report.json
  submission/migration_report.md
  submission/main.pdf
```

## Claim 进入论文的条件

任何实验 claim 进入正文前必须满足：

1. 数字存在于 `drafts/experiment_evidence_pack.json` 或 indexed result artifact。
2. `drafts/result_to_claim.json` 没有把它标为 `unsupported_mock_only`。
3. Writer 使用 `allowed_wording` 或更保守的表述。
4. `drafts/paper_claim_audit.json` 中没有未处理 FAIL。
5. T9 migration report 记录 evidence audit 文件链。
