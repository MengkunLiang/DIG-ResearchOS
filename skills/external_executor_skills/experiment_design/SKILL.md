---
name: experiment_design
description: Convert ResearchOS claim-evidence requirements into fair external experiment protocols.
allowed_tools:
  - "read_file"
  - "list_files"
  - "grep_search"
  - "glob_files"
  - "write_file"
  - "finish_task"
allowed_read_prefixes:
  - ""
  - "external_executor/"
  - "experiments/"
  - "ideation/"
  - "literature/"
  - "novelty/"
  - "resources/"
  - "user_seeds/"
allowed_write_prefixes:
  - "external_executor/"
max_steps: 16
max_tokens_total: 90000
temperature: 0.2
---

# Experiment Design

## Use for

Use to design experiments from claims and reviewer questions after baseline reproduction status is known.

## Do not use for

- Do not invent table slots without claim questions.
- Do not weaken fairness constraints to make ours look better.
- Do not run experiments in this phase.

## Reads

- `external_executor/handoff_pack.json`
- `result_pack.context_alignment`
- `result_pack.baseline_reproduction`
- `result_pack.baseline_coverage`
- `external_executor/skills/experiment_design/references/claim_to_experiment_matrix.md`
- `external_executor/skills/experiment_design/assets/claim_evidence_matrix_template.json`

## Writes

- experiment configs or protocol notes under `external_executor/configs/`
- `result_pack.claim_evidence_matrix`

## Workflow

- For each candidate claim, write reviewer question, evidence needed, experiment, metric, split, seed, baseline, ablation, and fairness constraint.
- Separate smoke, small-scale, formal, ablation, robustness, and diagnostic runs.
- Record experiments that cannot run and the claim boundary they affect.

## Output contract

- Every experiment must answer one reviewer question.
- Every claim must have evidence status or an explicit unsupported boundary.

## Evidence rules

- Ablations must test method mechanisms, not arbitrary removals.
- Baseline and ours protocols must remain comparable.

## Stop conditions

- Stop as `blocked` if no fair experiment can test the central claim with available materials.
