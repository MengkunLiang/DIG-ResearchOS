---
name: module_attribution
description: Attribute external experiment outcomes to method modules and ablations for ResearchOS T7 audit.
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

# Module Attribution

## Use for

Use after diagnosis to explain which modules are supported, unsupported, neutral, or harmful under ablation and diagnostic evidence.

## Do not use for

- Do not infer module causality without ablation, diagnostic, or clear raw evidence.
- Do not present whole-method success as module attribution.
- Do not ignore strong baseline modules.

## Reads

- `result_pack.realized_method_package`
- `result_pack.experiment_runs`
- `result_pack.result_diagnosis`
- `external_executor/raw_results/`
- `external_executor/skills/module_attribution/references/attribution_matrix.md`

## Writes

- `external_executor/logs/module_attribution.md`
- `result_pack.module_attribution`

## Workflow

- Map each realized module to planned mechanism, code path, and ablation or diagnostic run.
- Compare ours modules with effective baseline modules where possible.
- Record positive, neutral, negative, or inconclusive effects.
- Produce idea refinement: keep, modify, drop, or narrow boundary.

## Output contract

- Output baseline effective modules, ours effective modules, weak modules, supported mechanisms, unsupported mechanisms, and idea refinement.

## Evidence rules

- Attribute only what raw evidence supports.
- Mark unsupported module claims explicitly.

## Stop conditions

- Stop with `inconclusive` attribution when required ablations or diagnostics are unavailable.
