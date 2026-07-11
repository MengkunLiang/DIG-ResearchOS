---
name: experiment_iteration
description: Run controlled external experiment iterations for ResearchOS until budget, plateau, target, blocker, or claim narrowing.
allowed_tools:
  - "read_file"
  - "list_files"
  - "grep_search"
  - "glob_files"
  - "write_file"
  - "append_file"
  - "bash_run"
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
max_steps: 28
max_tokens_total: 140000
temperature: 0.2
---

# Experiment Iteration

## Use for

Use to execute smoke, small-scale, formal, ablation, robustness, and diagnostic runs under the reviewed protocol.

## Do not use for

- Do not run formal experiments before review passes.
- Do not keep tuning after plateau without recording why.
- Do not omit failed runs from the manifest.

## Reads

- `external_executor/configs/`
- `external_executor/workdir/`
- `external_executor/handoff_pack.json`
- `result_pack.protocol_review`
- `result_pack.claim_evidence_matrix`
- `external_executor/skills/experiment_iteration/references/run_levels.md`

## Writes

- `external_executor/raw_results/`
- `external_executor/logs/`
- `external_executor/run_manifest.json`
- `result_pack.experiment_runs`
- `result_pack.metrics`
- `result_pack.artifacts`

## Workflow

- Follow `references/run_levels.md`.
- Run smoke first, then small-scale validation, then formal runs.
- Record command, config, seed, split, raw result, metric output, log, and patch or commit id for every run.
- Update run manifest and artifact hashes after each round.

## Output contract

- Every metric must trace to raw result, config, log, run id, seed, and artifact hash.
- `experiment_runs` must include run type and status.

## Evidence rules

- Mock-only or dry-run records must be labeled and cannot support empirical claims.
- Failed and partial runs remain in the manifest.

## Stop conditions

- Stop on budget exhaustion, improvement plateau, required baseline unavailability, implementation blocker, audited target reached, or claim narrowing.
