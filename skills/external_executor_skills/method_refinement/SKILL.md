---
name: method_refinement
description: Refine the ResearchOS method mechanism during external execution while tracking scope and contribution drift.
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

# Method Refinement

## Use for

Use before implementation to convert draft method intent into an implementable specification, and after diagnosis to update the realized method without silent drift.

## Do not use for

- Do not change the core hypothesis silently.
- Do not drop required baselines or change benchmarks here.
- Do not treat the initial method intent as final Method text.

## Reads

- `external_executor/handoff_pack.json#method_intent`
- `result_pack.result_diagnosis`
- `result_pack.module_attribution`
- `external_executor/skills/shared-references/scope-drift-policy.md`
- `external_executor/skills/method_refinement/references/realized_method_package_schema.md`

## Writes

- `external_executor/logs/method_refinement_notes.md`
- `result_pack.realized_method_package`
- `result_pack.scope_change_requests`

## Workflow

- Convert intent into input, output, modules, losses, training loop, inference procedure, config keys, ablation switches, and failure modes.
- Preserve must-keep mechanisms unless a scope change is recorded.
- After results, update realized modules, supported mechanisms, unsupported mechanisms, claim boundary, and deltas from intent.
- Mark contribution drift as `none`, `minor`, or `major`.

## Output contract

- `realized_method_package` must follow `references/realized_method_package_schema.md`.
- Major drift must create a scope change request and required action.

## Evidence rules

- Realized method facts must be tied to code paths, configs, ablations, or diagnostics.
- Unsupported mechanisms must stay out of final claims.

## Stop conditions

- Stop for human review if a necessary change alters the core contribution, task, benchmark, or required baselines.
