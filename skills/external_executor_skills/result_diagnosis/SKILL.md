---
name: result_diagnosis
description: Diagnose external experiment results, failures, baseline strengths, and claim risks for ResearchOS.
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
max_steps: 18
max_tokens_total: 90000
temperature: 0.2
---

# Result Diagnosis

## Use for

Use after experiment runs to interpret evidence conservatively before module attribution, method updates, and writer handoff.

## Do not use for

- Do not convert weak or diagnostic-only evidence into strong claims.
- Do not hide failures.
- Do not revise method scope silently.

## Reads

- `external_executor/raw_results/`
- `external_executor/configs/`
- `external_executor/logs/`
- `external_executor/run_manifest.json`
- `result_pack.experiment_runs`
- `result_pack.metrics`
- `external_executor/skills/result_diagnosis/references/diagnosis_questions.md`

## Writes

- `external_executor/logs/result_diagnosis.md`
- `result_pack.result_diagnosis`

## Workflow

- Follow `references/diagnosis_questions.md`.
- Trace every metric to raw result, config, log, run id, and seed.
- Identify strongest baseline, failure modes, metric anomalies, active mechanisms, inactive modules, and claim implications.
- Separate audited evidence from diagnostic hints.

## Output contract

- Populate strongest baseline, baseline strength analysis, where ours wins/fails, mechanism hypotheses, metric anomalies, claim implications, and next iteration recommendations.

## Evidence rules

- Each diagnosis item must cite evidence level.
- Unsupported interpretations go to limitations or must-not-claim.

## Stop conditions

- Stop with honest limitations when results are insufficient to support the planned claims.
