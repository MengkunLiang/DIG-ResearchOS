---
name: context_alignment
description: Check ResearchOS handoff context against source artifacts before external experiment execution.
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
max_steps: 16
max_tokens_total: 90000
temperature: 0.2
---

# Context Alignment

## Use for

Use first, before resource mining, coding, or experiments, to verify that the external executor understands the project goal, central hypothesis, method intent, required baselines, experiment loop, allowed paths, and output schema.

## Do not use for

- Do not search for new baselines.
- Do not write code or run experiments.
- Do not resolve scientific conflicts silently.

## Reads

- `external_executor/AGENTS.md`
- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `project.yaml`
- source artifacts under `literature/`, `ideation/`, `novelty/`, `resources/`, and `user_seeds/`
- `external_executor/skills/context_alignment/references/checklist.md`

## Writes

- `external_executor/logs/context_alignment_notes.md`
- `result_pack.context_alignment`

## Workflow

- Follow `references/checklist.md`.
- Compare `context_reboost`, `method_intent`, `baseline_matrix`, and `claim_evidence_matrix` against source artifacts.
- If handoff content conflicts with source artifacts, record `context_mismatch`; prefer source artifacts and novelty audit for required baselines and claim boundaries.
- Classify status as `pass`, `mismatch`, or `blocked`.

## Output contract

- Populate `context_alignment.status`, `source_files_checked`, `mismatches`, and `resolution`.
- Do not advance if the task goal, required baselines, allowed paths, or result schema are unclear.

## Evidence rules

- Cite concrete source files for every mismatch.
- Do not infer missing baselines away because they are inconvenient.

## Stop conditions

- Stop as `blocked` when source artifacts are too incomplete or contradictory to define a runnable external experiment.
