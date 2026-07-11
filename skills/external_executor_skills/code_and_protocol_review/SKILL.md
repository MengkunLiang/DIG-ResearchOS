---
name: code_and_protocol_review
description: Review external executor code and experiment protocol for method alignment, fairness, metrics, splits, and overclaim risk.
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

# Code And Protocol Review

## Use for

Use as the Reviewer side of the Builder-Reviewer loop before formal runs and after every implementation or protocol change.

## Do not use for

- Do not fix code in this phase unless explicitly returning to implementation.
- Do not approve formal runs with missing logs, unclear metrics, or unfair baselines.
- Do not downgrade risks silently.

## Reads

- `external_executor/workdir/`
- `external_executor/configs/`
- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `result_pack.realized_method_package`
- `external_executor/skills/shared-references/builder-reviewer-loop.md`
- `external_executor/skills/code_and_protocol_review/references/review_checklist.md`

## Writes

- `external_executor/logs/review_notes.md`
- `result_pack.code_review`
- `result_pack.protocol_review`

## Workflow

- Follow `references/review_checklist.md`.
- Check method alignment, baseline fairness, metric direction, splits, seeds, leakage, ablation switches, and artifact provenance.
- Classify `review_status` as `pass`, `needs_fix`, or `blocked`.
- Require fixes or claim narrowing before formal runs.

## Output contract

- Formal runs may proceed only when `review_status=pass`.
- Findings must identify concrete code/config/log paths when possible.

## Evidence rules

- Review must be grounded in files, configs, commands, and raw artifacts.
- A missing check is a risk, not a pass.

## Stop conditions

- Stop as `blocked` when fairness, leakage, or protocol defects cannot be repaired inside the external executor workspace.
