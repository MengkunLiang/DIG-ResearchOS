---
name: skills_customization
description: Customize the copied ResearchOS external executor skill templates into a project-specific skill suite after context re-boost.
allowed_tools:
  - "read_file"
  - "list_files"
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
  - "external_executor/skills/"
max_steps: 24
max_tokens_total: 140000
temperature: 0.2
---

# Skills Customization

## Use for

Use by the ResearchOS `T5-SKILL-CUSTOMIZATION-GATE` agent mode. ResearchOS calls the configured LLM provider directly; users do not need to launch Codex CLI for this step.

This guide tells the LLM how to rewrite the 13 copied external executor templates under `external_executor/skills/` into project-specific skills using `external_executor/handoff_pack.json`.

## Do not use for

- Do not run baseline reproduction, experiments, ablations, or diagnostics.
- Do not write `external_executor/result_pack.json`, `executor_status.json`, or `run_manifest.json`.
- Do not modify ResearchOS runtime, config, drafts, submission, or source Pre-T5 artifacts.
- Do not modify this `skills_customization` skill except for writing a customization report outside this directory if needed.
- Do not rename the 13 target skill directories or change their frontmatter `name`.

## Reads

- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/AGENTS.md`
- `external_executor/skills/template_manifest.json`
- `external_executor/skills/shared-references/*.md`
- the 13 target `external_executor/skills/*/SKILL.md` files listed in `template_manifest.json#copied_skills`
- optional source context referenced by the handoff pack, such as `ideation/`, `literature/`, `novelty/`, `resources/`, and `user_seeds/`
- `external_executor/skills/skills_customization/references/customization_checklist.md`

## Writes

- project-specific versions of the 13 target skill files under `external_executor/skills/<target_skill>/SKILL.md`
- optional project-specific reference notes under each target skill's `references/`
- `external_executor/skills/customization_report.json`

## Workflow

- Read `handoff_pack.json`, `template_manifest.json`, and `references/customization_checklist.md` first.
- Identify the 13 target skills from `template_manifest.json#copied_skills`; exclude `skills_customization` and `shared-references`.
- Extract project-specific context: project goal, central hypothesis, `context_reboost`, draft-only `method_intent`, `baseline_matrix`, `claim_evidence_matrix`, required baselines, allowed paths, stop conditions, and writer handoff contract.
- Use the per-skill specialization map in `references/customization_checklist.md` to decide which project facts each target skill needs.
- Customize each target skill while preserving its concise structure: `Use for`, `Do not use for`, `Reads`, `Writes`, `Workflow`, `Output contract`, `Evidence rules`, and `Stop conditions`.
- Add project-specific details where they change execution: concrete baseline names, metrics, datasets/material locations, claim ids, expected evidence, method modules, audit risks, and forbidden drift.
- Keep complex project detail in `references/project_context.md` or other references under the target skill instead of turning `SKILL.md` into a long prompt.
- Preserve the root flow: users later start external execution with `请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research_execution/SKILL.md。`
- Write `customization_report.json` last.

## Output contract

`external_executor/skills/customization_report.json` must include:

- `semantics: external_executor_skill_customization_report`
- `handoff_pack: external_executor/handoff_pack.json`
- `customized_skills`: one entry per target skill
- `unchanged_or_skipped`: empty unless there is a documented blocker
- `project_specific_fields_used`: key handoff fields used for customization
- `next_instruction`: `python -m researchos.cli run-task T5-EXPR-MATERIAL-GATE --workspace <workspace>` for single-task debugging, or `python -m researchos.cli resume --workspace <workspace>` in the full state machine

## Evidence rules

- Every project-specific detail must come from `handoff_pack.json` or cited source files.
- If source files conflict with `context_reboost`, record the conflict in the target skill or report rather than silently choosing.
- Do not invent baseline availability, datasets, metrics, or experimental results.
- Keep `method_intent` marked as draft-only; final Method facts come from later `realized_method_package`.

## Stop conditions

- Stop with a partial `customization_report.json` if `handoff_pack.json`, `template_manifest.json`, or any required target skill is missing.
- Stop if customizing a target skill would require changing ResearchOS runtime behavior.
- Stop after writing the customized target skills and report; do not proceed to experiment execution.
