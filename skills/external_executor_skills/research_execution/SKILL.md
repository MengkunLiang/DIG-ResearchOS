---
name: research_execution
description: Root external executor skill that orchestrates the full ResearchOS Builder-Reviewer experiment loop.
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
max_steps: 36
max_tokens_total: 180000
temperature: 0.2
---

# Research Execution

## Use for

Use as the only user-facing entry point after reading `external_executor/AGENTS.md`. It coordinates the external executor loop and delegates to smaller skills. If your runtime cannot invoke skills directly, read each child `SKILL.md` and execute its phase.

## Do not use for

- Do not ask the user to invoke child skills one by one.
- Do not write paper prose or final claims.
- Do not modify ResearchOS runtime, config, drafts, submission, or Pre-T5 source artifacts.
- Do not treat `method_intent` as the final Method source.
- Do not fabricate datasets, baselines, metrics, logs, or missing raw results.

## Reads

- `external_executor/AGENTS.md`
- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/expr/`
- `external_executor/skills/shared-references/*.md`
- `external_executor/skills/research_execution/references/*.md`
- child skill files under `external_executor/skills/*/SKILL.md`

## Writes

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- external artifacts under `external_executor/raw_results/`, `configs/`, `logs/`, `patches/`, `figures/`, and `tables/`

## Workflow

- Read the shared references, especially `external-executor-protocol.md`, `result-pack-contract.md`, `evidence-rules.md`, `builder-reviewer-loop.md`, and `scope-drift-policy.md`.
- Follow `references/execution_loop.md` exactly: context alignment, resource mining, baseline reproduction, experiment design, method refinement, implementation, review, experiment iteration, diagnosis, attribution, packaging, and writer handoff.
- Dispatch child skills in this exact order: `context_alignment`, `resource_and_baseline_mining`, `baseline_reproduction`, `experiment_design`, `method_refinement`, `implementation`, `code_and_protocol_review`, `experiment_iteration`, `result_diagnosis`, `module_attribution`, optional second `method_refinement`, `figure_table_packaging`, `writer_handoff`.
- Enforce the Builder-Reviewer loop: every implementation or protocol change must pass `code_and_protocol_review` before formal runs.
- Run smoke, small-scale, formal, ablation, robustness, and diagnostic runs in order as budget permits.
- Write `result_pack.json` only after `writer_handoff` has assembled all required fields.

## Output contract

- `external_executor/result_pack.json` must satisfy `external_executor/expected_outputs_schema.json` and `external_executor/skills/shared-references/result-pack-contract.md`.
- Completion by this executor is not ResearchOS acceptance; keep `executor_status.accepted=false`.
- Partial or blocked runs must still write available status, manifest, logs, and claim risks.

## Evidence rules

- Raw artifacts, configs, logs, run ids, and hashes are stronger than prose summaries.
- Mock/dry-run output is protocol evidence only and cannot support empirical claims.
- Missing required baselines create claim risk; do not silently replace them.

## Stop conditions

- Use `references/stop_conditions.md`.
- Stop honestly on budget exhaustion, plateau, unavailable required baseline, blocker, audited target reached, claim narrowing, or scope change requiring human review.
