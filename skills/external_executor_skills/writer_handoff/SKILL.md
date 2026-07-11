---
name: writer_handoff
description: Produce the ResearchOS external executor writer handoff with realized method, figures, tables, limitations, and claim boundaries.
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

# Writer Handoff

## Use for

Use as the final external executor step. It assembles the result pack and handoff material for T7; it does not write the paper.

## Do not use for

- Do not write final manuscript prose.
- Do not mark ResearchOS acceptance.
- Do not summarize unsupported results as claims.

## Reads

- `result_pack.context_alignment`
- `result_pack.resources`
- `result_pack.baseline_reproduction`
- `result_pack.experiment_runs`
- `result_pack.result_diagnosis`
- `result_pack.module_attribution`
- `result_pack.realized_method_package`
- `result_pack.final_framework_figure`
- `result_pack.figure_table_inventory`
- `external_executor/skills/shared-references/result-pack-contract.md`
- `external_executor/skills/writer_handoff/references/handoff_schema.md`
- `external_executor/skills/writer_handoff/assets/executor_status_template.json`
- `external_executor/skills/writer_handoff/assets/run_manifest_template.json`

## Writes

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- `result_pack.writer_handoff`

## Workflow

- Follow `references/handoff_schema.md`.
- Assemble method summary, implementation summary, result summary, figure/table inventory, limitations, claim boundaries, must-not-claim list, and recommended storyline update.
- Ensure all required result pack fields are present.
- Set executor status to `completed`, `partial`, `blocked`, or `failed`; keep `accepted=false`.
- Write `run_manifest.json` and `executor_status.json`, then write `result_pack.json` last.

## Output contract

- `result_pack.json` must satisfy `expected_outputs_schema.json` and the shared result pack contract.
- Writer handoff must reference raw evidence rather than executor prose.

## Evidence rules

- Separate supported, weak, unsupported, and diagnostic-only findings.
- Missing required baselines, missing raw logs, and major drift must be visible to T7.

## Stop conditions

- Stop after writing complete, partial, blocked, or failed handoff files; do not continue into paper writing.
