---
name: research-execution
description: Orchestrate or resume the complete ResearchOS external-executor workflow from T5 handoff through resource readiness, claim-bound experiment planning, build-review-run iterations, diagnosis, attribution, evidence packaging, and Writer Handoff. Use when Codex or Claude Code is launched in a ResearchOS workspace to execute `external_executor/skills/research-execution/SKILL.md`, continue an interrupted external experiment, decide the next project-specific child skill, or enforce gates and budgets. Do not use for an isolated child-stage task when that child skill is explicitly requested.
---

# Research Execution

Act as the sole workflow owner for the external executor. Coordinate project-specific child skills through durable artifacts; do not implement their domain work inside this root skill.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish the execution root

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; treat it as `<workspace>`.
2. Treat the directory containing this file as `<root-skill>`.
3. Read, in order:
   - `<workspace>/external_executor/AGENTS.md`
   - `<workspace>/external_executor/handoff_pack.json`
   - `<workspace>/external_executor/expected_outputs_schema.json`
   - `<workspace>/external_executor/allowed_paths.txt`
   - `<root-skill>/references/security-and-path-policy.md`
   - `<root-skill>/references/artifact-and-resume-policy.md`
4. Stop with `blocked` if a required control file is missing, its major schema version is unsupported, or the allowed-path boundary cannot be determined.

Never broaden permissions, resource-acquisition policy, research scope, or writable paths by inference.

## Own only orchestration state

Write narrowly. This root skill owns:

- `external_executor/executor_status.json`;
- the global index and checkpoint metadata in `external_executor/report/run_manifest.json`;
- iteration plans and iteration decisions in `external_executor/result_pack.json`;
- budget accounting, blockers, human-review requests, and the intended terminal execution outcome.

Keep external process/report files grouped by phase under `external_executor/report/phase_A/` through `phase_F/` as defined in `references/artifact-and-resume-policy.md`. `external_executor/report/run_manifest.json` is the only cross-phase external file kept directly in `report/`; pre-execution ResearchOS receipts such as executor selection remain at their existing root-level paths.

Child skills own their domain sections and files. Read `<root-skill>/references/child-skill-contracts.md` before the first dispatch. Do not let one child invoke another child or overwrite a sibling's section.

## Initialize or resume

Run deterministic checks before choosing work:

```bash
python <root-skill>/scripts/fingerprint_inputs.py --workspace <workspace> \
  --output external_executor/report/phase_A/input_fingerprint.json

python <root-skill>/scripts/validate_executor_state.py --workspace <workspace> \
  --mode resume
```

If core state files do not exist, initialize minimal valid envelopes:

```bash
python <root-skill>/scripts/initialize_executor.py --workspace <workspace>
```

This sets `executor_status=running`, `current_phase=A`, empty checkpoint collections, and required result-pack sections to `not_started`. Do not use `--force` during resume.

If state exists:

1. Compare the current input fingerprint with the stored fingerprint.
2. Verify referenced artifacts and checksums.
3. Reuse only checkpoints whose schema, input fingerprint, dependencies, and evidence references remain valid.
4. Mark affected checkpoints and dependent runs `stale`; preserve unrelated valid work.
5. Resume from the earliest invalid prerequisite, not merely from the last recorded phase.

Never delete stale or failed evidence automatically. Preserve it as history and exclude it from current claims.

## Route through one owner

Read `<root-skill>/references/routing-and-gates.md`. Determine the next action from artifacts, not conversational memory.

Use the routing helper as a consistency check:

```bash
python <root-skill>/scripts/route_next_skill.py --workspace <workspace>
```

Dispatch exactly one child at a time unless `AGENTS.md` explicitly permits independent parallel work. To dispatch a child:

1. Resolve `<workspace>/external_executor/skills/<child>/SKILL.md`.
2. Confirm the child's prerequisites and input fingerprint.
3. Read that `SKILL.md` and only the references it explicitly requires.
4. Execute it in the same workspace, or in a fresh worker when the executor supports workers and project policy authorizes them.
5. Validate its declared outputs before recording the checkpoint.
6. Return control to this root skill for the next decision.

Child sequence and conditional use:

| Order | Child skill | Dispatch condition |
| --- | --- | --- |
| 1 | `context-alignment` | First run, changed handoff/control inputs, or invalid alignment checkpoint |
| 2 | `resource-and-baseline-preparation` | Alignment is non-blocking and resource readiness is missing/stale |
| 3 | `experiment-design` | Minimum loop is feasible and experiment plan/protocol is missing/stale |
| 4 | `baseline-reproduction` | A required baseline lacks a valid reproduction or its fairness fingerprint changed |
| 5 | `method-refinement` | Initial method specification or an approved refinement is required |
| 6 | `implementation` | An approved implementation/repair delta is pending |
| 7 | `code-and-protocol-review` | Code, config, adapter, split, metric, or protocol changed |
| 8 | `experiment-run` | Review approved the requested run level and budget remains |
| 9 | `result-diagnosis` | A new usable run set has not been diagnosed |
| 10 | `module-attribution` | Evidence is sufficient for mechanism/module analysis |
| 11 | `evidence-packaging` | Iteration stops and current evidence snapshot is stable |
| 12 | `writer-handoff` | Evidence package exists and the final status/result/manifest/assets are ready to compile and validate for T8 |

## Control the build-review-run loop

Before the first method build, create iteration `ITER-01` with `iteration_number=1`, `max_method_iterations=10`, the trigger, approved changes, affected experiments, reusable runs, runs to execute, budget before execution, and expected decision surface. Materialize the same plan at `external_executor/report/phase_D/iteration_plans/ITER-01.json` and store that path as `plan_ref`; experiment run requests use it as `iteration_plan_ref`. Ten is a fixed total method implementation/debug-attempt limit, not a default that a child may raise.

Enforce this loop:

```text
plan
  -> baseline reproduction or repair when needed
  -> method refinement when needed
  -> implementation
  -> code and protocol review
       -> needs_fix: route to the owning repair step
       -> blocked: stop or request human review
       -> pass: run only the approved level
  -> experiment run and checkpoint
  -> result diagnosis
  -> deterministic root iteration decision
       -> failed/incomplete run: copy the last method and debug in a new iteration
       -> not better than every required baseline: method refinement, then copy/implement in a new iteration
       -> better than every required baseline but final ablation contracts missing: return to experiment design
       -> better than every required baseline but reference/intervention pairs incomplete: run the missing final ablations
       -> target reached or a stop condition fires: leave the optimization loop
  -> module attribution only after every required final ablation has complete comparable variants for every planned seed/repeat
       -> partial/add evidence: return to experiment design
       -> blocked: human review
       -> ready: evidence packaging
```

Every experiment attempt, including a failed launch or unusable result, must be applied to `result_pack.experiment_runs` and submitted to `result-diagnosis`. Do not bypass diagnosis by debugging directly from console output.

After applying each diagnosis, run:

```bash
python <root-skill>/scripts/decide_iteration.py --workspace <workspace> \
  --diagnosis external_executor/result_diagnosis_report.json
```

The helper records one root decision and the next durable route. It creates a new iteration only for a method modification/debug, materializes the plan under `external_executor/report/phase_D/iteration_plans/`, binds its `base_source` to the immediately preceding implementation `worktree/`, carries all prior diagnosis lessons, and refuses an eleventh method iteration. For final ablations it verifies complete plan-declared variant sets, shared pair identity, exact module states, fingerprints, and seed/repeat coverage; a single completed ablation run is insufficient. Never edit an earlier method worktree in place.

Do not run formal experiments without `review_status=pass` and `approved_for=formal`. Baseline work and method smoke tests may proceed independently only when the plan and fairness constraints allow it; do not make a superiority claim before required comparisons are valid.

The deterministic decision records one primary value:

- `continue_same_idea`
- `minor_method_fix`
- `module_reweight`
- `baseline_repair`
- `add_diagnostic_run`
- `claim_narrowing`
- `scope_change_request`
- `stop_and_report`

Record rationale, evidence references, affected claims, planned changes, remaining budget, human-review requirement, and next action. Use `references/routing-and-gates.md` for route effects.

The optimization target is reached only when completed formal our-method runs beat every required baseline on every comparable surface and every required final-method ablation has a complete, plan-matching reference/intervention pair for every declared seed/repeat surface. Missing, tied, mixed, or incomparable baseline evidence, or a lone completed ablation variant, is not success. Budget exhaustion, active authority/security blockers, and the fixed ten-iteration limit stop the loop honestly and package the best auditable evidence available.

## Enforce gates

Pause instead of guessing when any of these occurs:

- material conflict in required baselines, benchmark, core mechanism, contribution type, or claim boundary;
- permission, network, dataset-access, security, or license escalation;
- replacement of a required baseline not already authorized;
- change of task, benchmark, central hypothesis, core mechanism, or contribution type;
- unsupported major schema version;
- a major contribution drift or post-novelty review requirement.

Write the blocker or `scope_change_request` before pausing. Do not implement a major change and document it afterward.

## Checkpoint every transition

After every child return and every iteration decision:

1. Update the producing section of `result_pack.json` without replacing unrelated sections.
2. Register new or changed artifacts:

```bash
python <root-skill>/scripts/update_manifest.py --workspace <workspace> \
  --producer <child-or-root> --phase <phase> --artifact <workspace-relative-path>
```

3. Validate state:

```bash
python <root-skill>/scripts/validate_executor_state.py --workspace <workspace> \
  --mode checkpoint
```

4. Atomically update `executor_status.json` only after the output validation passes.

Use workspace-relative artifact paths. Bind formal results to config, raw log, metric output, split, seed/repeat, code version, resource version, environment, and protocol fingerprint. Executed code/config must be under `external_executor/expr/`; approved resources, public remote acquisitions, and baseline reimplementations must be under `resources/`; raw logs, metric outputs, records, checkpoints, and run-produced artifacts must be under `external_executor/raw_results/`.

## Stop honestly

Stop or package partial evidence when any configured condition is met, including budget exhaustion, improvement plateau, required-baseline unavailability, audited target reached, implementation block, mandatory claim narrowing, human review, no valid formal result, minimum-loop block, or security/license block.

`completed` means mandatory scientific work and provenance are complete and Writer Handoff validation can pass. Use `partial`, `blocked`, or `failed` according to `references/status-and-enums.md`; never use `completed` as a courtesy status.

Even after failure or blocking, preserve logs, failure records, valid partial evidence, open risks, and recovery instructions.

## Package and hand off

When iteration stops:

1. Dispatch `evidence-packaging` against one pinned final evidence snapshot.
2. Derive the intended terminal outcome from actual work and write the same `completed`, `partial`, `blocked`, or `failed` value to `executor_status.json` and `result_pack.json`. Register all evidence-packaging outputs before freezing the manifest.
3. Dispatch `writer-handoff`. It creates `external_executor/executor_research_report.md` and validates the terminal status, result pack, run manifest, research report, and every final figure/table.
4. Accept only `external_executor/report/phase_F/writer_handoff_validation.json` with `status=ready|partial`. A blocked result routes back to `writer-handoff` repair or the authoritative producer identified by the validation error.
5. Record the child checkpoint, rerun `scripts/route_next_skill.py`, and require the root action `launch-t8`. Execute the returned command exactly once. The command performs an independent ResearchOS acceptance pass, creates T8 evidence inputs, safely enters or resumes the existing T8 state, and delegates writing to the normal ResearchOS pipeline runner.
6. Do not exit the external executor merely to ask the user to run `resume`, and do not directly write `drafts/` or manuscript content. The `run-task T8` ResearchOS subprocess owns those writes and any T8 human Gates.
7. If routing later returns `stop` because T8 was already delegated, do not invoke `run-task T8` again. Leave the external executor outputs frozen and let the active or resumable ResearchOS T8 state remain authoritative.

The external handoff is a validated downstream input package, never “paper-approved.” `executor_research_report.md` is the primary T8 input; the validated result pack, manifest, facts, raw results, realized method package, figures, and tables remain supporting provenance.

## Evidence rules

- Treat Artifact files as the source of truth; summaries are navigation aids.
- Keep confirmatory, diagnostic, exploratory, smoke, small-scale, and formal evidence distinct.
- Preserve failed trials and stale runs; exclude them from active claim support.
- Require evidence references for diagnosis, attribution, iteration decisions, realized modules, figures, tables, and claim candidates.
- Do not promote correlation to causal attribution.
- Do not fabricate a required key; represent unavailable work with explicit status, empty items, and blocking issues.

## Resource map

- `references/routing-and-gates.md`: phase transitions, loop routes, and human gates.
- `references/artifact-and-resume-policy.md`: ownership, fingerprints, staleness, atomic updates, and recovery.
- `references/status-and-enums.md`: shared status model, required envelopes, and completion semantics.
- `references/security-and-path-policy.md`: path, acquisition, third-party code, secret, and scope guardrails.
- `references/child-skill-contracts.md`: child inputs, outputs, boundaries, and checkpoint contracts.
- `scripts/fingerprint_inputs.py`: calculate deterministic input fingerprints.
- `scripts/initialize_executor.py`: create minimal state, manifest, and result-pack envelopes.
- `scripts/update_manifest.py`: register artifacts with checksums and provenance.
- `scripts/validate_executor_state.py`: validate controls, state, paths, and manifest integrity.
- `scripts/validate_result_pack.py`: validate checkpoint result-pack contracts before the terminal Writer Handoff snapshot.
- `scripts/route_next_skill.py`: derive the next safe child action from durable state.
- `scripts/decide_iteration.py`: enforce the diagnosis-driven method loop, versioning, baseline target, final ablations, budget stops, and fixed ten-iteration cap.
