---
name: resource_and_baseline_mining
description: Mine and document datasets, baseline repositories, model resources, and runnability risks for ResearchOS external execution.
allowed_tools:
  - "read_file"
  - "list_files"
  - "grep_search"
  - "glob_files"
  - "write_file"
  - "append_file"
  - "bash_run"
  - "clone_repo"
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

# Resource And Baseline Mining

## Use for

Use after context alignment to inspect human-provided materials and locate resources for required baselines, datasets, pretrained weights, and implementation references.

## Do not use for

- Do not replace a hard baseline just because it is difficult.
- Do not start baseline reproduction or implement the new method.
- Do not rely on a repository without recording license, compatibility, and runnability risk.

## Reads

- `external_executor/handoff_pack.json`
- `external_executor/expr/`
- `resources/`
- `user_seeds/`
- baseline hints under `literature/` and `novelty/`
- `external_executor/skills/resource_and_baseline_mining/references/baseline_inventory_schema.md`

## Writes

- `external_executor/logs/resource_mining_notes.md`
- optional cloned or copied resources under `external_executor/workdir/` or `external_executor/resources/`
- `result_pack.resources`
- `result_pack.baseline_candidates`

## Workflow

- Inspect `external_executor/expr/` before searching elsewhere.
- Use `baseline_matrix` as the required checklist.
- For each baseline, record source, repo, license, compatibility, dependency risk, compute cost, and runnability.
- If unavailable, record unavailable reason, replacement candidate, and claim risk.

## Output contract

- `resources` and `baseline_candidates` must be specific enough for `baseline_reproduction`.
- Missing resources must be explicit, not hidden by omission.

## Evidence rules

- Prefer official repositories and paper-linked artifacts.
- Unofficial replacements must be marked as replacements with risk.

## Stop conditions

- Stop as `blocked` only when required experiment materials are absent and no defensible reproduction path exists.
