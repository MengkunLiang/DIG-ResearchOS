---
name: implementation
description: Implement external experiment code only inside ResearchOS allowed external executor paths.
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
max_steps: 24
max_tokens_total: 120000
temperature: 0.2
---

# Implementation

## Use for

Use to write or adapt experiment code after baseline reproduction, experiment design, and method specification are clear.

## Do not use for

- Do not modify ResearchOS runtime, configs, drafts, submission, or Pre-T5 artifacts.
- Do not weaken baseline code or configs.
- Do not run formal experiments before review passes.

## Reads

- `external_executor/handoff_pack.json`
- `external_executor/allowed_paths.txt`
- `external_executor/expr/`
- `external_executor/workdir/`
- `external_executor/configs/`
- `result_pack.realized_method_package`
- `external_executor/skills/implementation/references/allowed_path_policy.md`

## Writes

- `external_executor/workdir/`
- `external_executor/patches/`
- `external_executor/configs/`
- `external_executor/logs/`

## Workflow

- Follow `references/allowed_path_policy.md`.
- Implement modules, losses, training loop, evaluation, and ablation switches required by the refined method.
- Keep baseline and ours configs comparable.
- Record patch notes with code paths, config keys, and expected reviewer checks.

## Output contract

- Implementation output is not valid until `code_and_protocol_review` returns `pass`.
- Patch notes must let T7 trace realized method modules to code paths.

## Evidence rules

- Prefer small, traceable changes over broad rewrites.
- Every claimed mechanism needs a code path and an ablation or diagnostic plan.

## Stop conditions

- Stop as `blocked` when implementation cannot proceed inside allowed paths or required materials are missing.
