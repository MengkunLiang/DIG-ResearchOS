---
name: context-re-boosting
description: Rewrite Pre-T5 ResearchOS artifacts into external_executor/handoff_pack.json#context_reboost before external execution.
allowed_tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - finish_task
allowed_read_prefixes:
  - ""
  - project.yaml
  - literature/
  - ideation/
  - novelty/
  - resources/
  - user_seeds/
  - external_executor/
allowed_write_prefixes:
  - external_executor/
max_steps: 18
max_tokens_total: 120000
temperature: 0.2
---

# Context Re-boosting

## Use for

Use this skill when ResearchOS has paused at the T5 re-boost gate and the user asks you to execute `external_executor/skills/context-re-boosting/SKILL.md`.

Your job is to transform Pre-T5 research artifacts into the execution context that an external experiment executor needs. Write the result into `external_executor/handoff_pack.json`, especially the `context_reboost` field.

## Do not use for

- Do not run experiments.
- Do not implement the method.
- Do not choose the external executor.
- Do not write `result_pack.json`, `executor_status.json`, `run_manifest.json`, paper drafts, final claims, or T7 audit artifacts.
- Do not modify ResearchOS runtime, `config/`, `researchos/`, `drafts/`, or `submission/`.
- Do not paste all source files into the handoff pack; re-organize them into execution context.

## Reads

Read these fixed Pre-T5 artifacts when present:

- `project.yaml`
- `literature/synthesis.md`
- `literature/synthesis_workbench.json`
- `literature/domain_map.json`
- `literature/comparison_table.csv`
- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/idea_scorecard.yaml`
- `ideation/risks.md`
- `ideation/novelty_audit.md`
- `novelty/novelty_audit.md`

Read these optional materials only when needed to resolve ambiguity:

- `literature/paper_notes/`
- `literature/paper_notes_abstract/`
- `resources/`
- `user_seeds/seed_external_resources.jsonl`
- `user_seeds/bridge_domains.yaml`
- existing `external_executor/handoff_pack.json`, if present

## Writes

Write or update only:

- `external_executor/handoff_pack.json`

If the file already exists, preserve useful existing top-level fields and replace only fields you can improve. At minimum, write a complete `context_reboost`. You may also write a draft-only `method_intent` if it directly follows from the re-boosted context.

## Workflow

1. Read the fixed Pre-T5 artifacts and note which files exist.
2. Identify the current research goal and central hypothesis.
3. Extract the method mechanism:
   - core mechanism
   - components that must be preserved
   - candidate or optional components
   - refinements the executor may make
   - scope changes the executor must not make silently
4. Extract required baselines from novelty audit and experiment plan. If they disagree, treat novelty audit as the stricter source and record the mismatch.
5. Build a baseline matrix that distinguishes required baselines, acceptable substitutes, why each baseline matters, source file, and claim risk if missing.
6. Build a claim-evidence matrix connecting hypotheses, reviewer questions, datasets or tasks, metrics, baselines, ablations, and the strength of claim each evidence item can support.
7. Define the minimum experiment loop the external executor must complete before writing results.
8. Define claim boundaries:
   - strong claims that need full evidence
   - weak claims that can survive partial evidence
   - claims that must not be made now
9. Define how experimental results should refine the idea or method after execution.
10. Define what the external executor must hand to Writer after experiments.
11. Write `external_executor/handoff_pack.json` with `schema_version: external_executor_handoff.v1` and a complete `context_reboost`.

## Output contract

`external_executor/handoff_pack.json` must be JSON and must contain:

```json
{
  "schema_version": "external_executor_handoff.v1",
  "context_reboost": {
    "project_goal": "",
    "central_hypothesis": "",
    "method_mechanism": {
      "core_mechanism": "",
      "must_preserve_components": [],
      "candidate_components": [],
      "allowed_refinements": [],
      "forbidden_scope_changes": []
    },
    "required_baselines": [],
    "baseline_matrix": [],
    "claim_evidence_matrix": [],
    "minimum_experiment_loop": [],
    "iteration_budget": {
      "max_rounds": 3,
      "stop_conditions": [
        "budget_exhausted",
        "improvement_plateau",
        "required_baseline_unavailable",
        "audited_target_reached",
        "implementation_blocked",
        "claim_must_be_narrowed"
      ]
    },
    "claim_boundaries": [],
    "writer_handoff_contract": [],
    "source_files_used": [],
    "known_context_mismatches": []
  }
}
```

Also mirror these two fields at top level for downstream compatibility:

- `baseline_matrix`
- `claim_evidence_matrix`

Recommended top-level fields:

- `semantics: external_experiment_handoff_contract`
- `status: context_reboost_completed`
- `reboosted_by: codex_cli`
- `reboost_notes`

## Evidence rules

- Every required baseline must cite the source that made it required.
- If `novelty_audit.md` requires a baseline but `exp_plan.yaml` omits it, record `known_context_mismatches`.
- If evidence is insufficient, narrow claims rather than invent support.
- If source files conflict, record the conflict and choose the stricter interpretation for experiment execution.
- Treat `method_intent` as draft-only; the final method source is the external executor's audited `realized_method_package`.

## Stop conditions

Stop after `external_executor/handoff_pack.json` contains a complete `context_reboost`.

If required source files are missing, still write the handoff pack with:

- `known_context_mismatches`
- narrowed `claim_boundaries`
- explicit `source_files_used`

Do not block on missing optional materials unless the core research goal, central hypothesis, or experiment plan cannot be recovered.
