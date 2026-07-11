---
name: baseline_reproduction
description: Reproduce required baselines before implementing the new method in ResearchOS external execution.
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
max_steps: 20
max_tokens_total: 100000
temperature: 0.2
---

# Baseline Reproduction

## Use for

Use after resource mining and before coding the new method. Required baselines must be attempted or explicitly marked unavailable with claim risk.

## Do not use for

- Do not implement the new method before baseline status is known.
- Do not silently substitute a weaker or easier baseline.
- Do not present dry-run output as reproduction.

## Reads

- `external_executor/handoff_pack.json`
- `external_executor/expr/`
- `external_executor/workdir/`
- `external_executor/configs/`
- `result_pack.resources`
- `result_pack.baseline_candidates`
- `external_executor/skills/baseline_reproduction/references/reproduction_record.md`

## Writes

- baseline configs under `external_executor/configs/`
- raw results under `external_executor/raw_results/`
- logs under `external_executor/logs/`
- `result_pack.baseline_reproduction`
- `result_pack.baseline_coverage`

## Workflow

- Create or reuse baseline configs with fixed dataset split, metric, seed, and command.
- Run smoke checks before formal reproduction.
- Record every attempt using `references/reproduction_record.md`.
- For failures, decide `reproduce`, `repair`, `replace`, or `mark_unavailable`, and record claim risk.

## Output contract

- Each baseline record needs command, config, split, seed, metric, raw log, result or failure reason, and status.
- Baseline coverage must identify missing required baselines.

## Evidence rules

- Raw logs and exact configs are required for reproduction claims.
- Failed attempts remain part of the evidence trail.

## Stop conditions

- Stop and report claim risk if a required baseline cannot be reproduced or defensibly marked unavailable.
