---
name: experiment-run
description: Execute and checkpoint approved ResearchOS T5 external-executor runs with complete provenance. Use when `research-execution` dispatches a reviewed iteration run, or when an approved smoke, small-scale, formal, ablation, robustness, diagnostic, or efficiency experiment must be launched, observed, recorded, validated, or safely reused. Enforce review level, protocol fingerprint, budget, allowed paths, environment isolation, immutable logs, metrics, checksums, and failure preservation. Do not use to design experiments, modify code, approve a protocol, diagnose scientific meaning, choose the next iteration, or promote smoke/toy evidence to formal evidence.
---

# Experiment Run

Execute exactly the run authorized by the current iteration plan and review. Treat the run request and durable artifacts as the source of truth; do not reconstruct authority from chat memory.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Own only run execution

Own:

- preflight validation for one run request;
- environment and hardware capture;
- process launch, timeout/cancellation handling, and raw-log capture;
- metric/output collection and checksums;
- one immutable run record and one run checkpoint;
- narrow upsert into `result_pack.experiment_runs` when the root asks for it.

Do not own experiment design, code repair, review approval, scientific diagnosis, iteration decisions, global budget decisions, or global executor status. Return control to `research-execution` after every run or checkpoint.

## Read controls before execution

Read these workspace artifacts:

```text
external_executor/AGENTS.md
external_executor/allowed_paths.txt
external_executor/executor_status.json
external_executor/result_pack.json
external_executor/run_manifest.json
```

Then read the dispatched iteration plan, experiment plan entry, latest implementation review, run request, config, and declared dependency files. Use the more restrictive path/security rule when controls disagree. A material authority, protocol, or scope conflict is `blocked`; do not guess.

Read these references when their decision surface applies:

- `references/run-request-contract.md` before creating or validating a request.
- `references/execution-level-and-evidence-policy.md` before deciding whether a run level is allowed or how its evidence may be used.
- `references/safety-and-environment-policy.md` before launching any process.
- `references/failure-and-recovery-policy.md` after interruption, timeout, nonzero exit, missing output, or resume.
- `references/run-record-and-checkpoint-contract.md` before finalization or result-pack writeback.
- `references/budget-and-stop-policy.md` before reserving resources or reporting actual consumption.

## Preconditions

Require all of the following:

1. `research-execution` supplied a current iteration ID and run request.
2. The experiment exists in the current experiment plan and its dependencies are ready.
3. The latest review has `review_status=pass` and `approved_for` covers the requested execution level.
4. Formal execution has explicit `approved_for=formal` and matching input/protocol fingerprints.
5. Estimated run cost fits within the budget snapshot supplied by the root.
6. Command, working directory, logs, metrics, record, checkpoint, config, and declared outputs resolve inside allowed paths.
7. Required code, data, split, resource, config, and protocol identities are pinned.
8. The execution environment provides the isolation required by project policy. Formal execution requires enforced filesystem isolation and either enforced network isolation or explicitly authorized network access.

Missing approval, budget, fingerprint, dependency, path authority, or isolation is not a runnable warning. Return a blocked preflight result.

## Prepare a run request

Use an existing request if the root supplied one. Otherwise materialize the root's dispatch into the schema in `references/run-request-contract.md`; do not invent missing scientific fields.

Validate it before launch:

```bash
python <skill>/scripts/validate_run_request.py \
  --workspace <workspace> \
  --request <workspace-relative-run-request.json>
```

The validator compares the request with the plan entry and review, verifies dependency checksums, checks the approval lattice, and confirms budget and path constraints.

## Capture the execution environment

Capture the environment before launching:

```bash
python <skill>/scripts/capture_environment.py \
  --workspace <workspace> \
  --output <workspace-relative-environment.json>
```

This records platform, Python, packages, CPU/memory, selected accelerator metadata, and a deterministic environment fingerprint. It does not dump credentials or the complete process environment. Add project-specific framework/runtime versions to the request dependencies when they matter.

## Execute one immutable attempt

Launch through the deterministic runner:

```bash
python <skill>/scripts/execute_run.py \
  --workspace <workspace> \
  --request <workspace-relative-run-request.json>
```

The runner:

1. repeats preflight validation immediately before launch;
2. writes a `running` record before starting the child process;
3. executes the argument vector without a shell;
4. captures combined stdout/stderr in the declared raw log;
5. enforces the declared timeout and records cancellation or signals;
6. loads the declared JSON metric output without rewriting its values;
7. fingerprints logs, metrics, config, dependencies, and declared outputs;
8. writes the terminal record atomically.

Never silently retry. Every retry is a new run ID or attempt record authorized by the root. Never overwrite a failed, cancelled, unusable, stale, or completed record. `--reuse-valid` may only reuse a completed record with the same request fingerprint and valid artifact checksums.

Do not pass an unreviewed shell string. Do not add flags, seeds, metrics, splits, checkpoints, adapters, or environment variables at launch time. Any such change invalidates preflight and returns to the owning planning/review step.

## Classify the terminal state

Use only:

```text
completed | failed | cancelled | unusable
```

The root may later mark a record `stale` when a dependency fingerprint changes.

- `completed`: process exited zero and all required outputs/provenance exist.
- `failed`: process exited nonzero, timed out, or could not launch; preserve logs and failure category.
- `cancelled`: an authorized external cancellation or operator interrupt occurred.
- `unusable`: process exited zero but mandatory log, metric, config, provenance, or declared output is missing/invalid.

Do not delete failed outputs or convert a zero-exit run with missing formal provenance into `completed`.

## Validate and checkpoint

Validate the terminal record independently:

```bash
python <skill>/scripts/validate_run_record.py \
  --workspace <workspace> \
  --record <workspace-relative-run-record.json>
```

Build an atomic run-scoped checkpoint:

```bash
python <skill>/scripts/build_run_checkpoint.py \
  --workspace <workspace> \
  --record <workspace-relative-run-record.json> \
  --output <workspace-relative-run-checkpoint.json>
```

The checkpoint contains the run record, artifact references, actual budget consumption, input fingerprint, recovery state, and root update recommendations. It is not a Git commit and does not claim global phase completion.

When explicitly requested by the root, apply only the owned result-pack section:

```bash
python <skill>/scripts/apply_run_checkpoint.py \
  --workspace <workspace> \
  --checkpoint <workspace-relative-run-checkpoint.json> \
  --result-pack external_executor/result_pack.json
```

The root remains responsible for registering manifest entries, updating global budget/status, choosing the next skill, and marking dependent artifacts stale.

## Evidence rules

- Preserve command arguments, config, protocol fingerprint, seed/repeat, dataset split, code/resource identities, environment, hardware, timestamps, exit information, raw log, metric output, and checksums.
- Keep `run_type`, `execution_level`, and `analysis_role` independent.
- Smoke and small-scale outputs remain engineering or diagnostic evidence even when successful.
- Toy/synthetic/dry-run output is never a pre-audit claim candidate.
- A formal process exit is not formal evidence unless review approval, protocol identity, provenance, and required outputs all validate.
- Do not remove failed trials, select favorable seeds, rewrite metric files, or upgrade evidence after observing results.
- Metric interpretation belongs to `result-diagnosis`; record values without deciding whether the idea succeeded.

## Return contract

Return a compact dispatch result containing:

```json
{
  "child_skill": "experiment-run",
  "iteration_id": "",
  "run_id": "",
  "run_status": "completed | failed | cancelled | unusable | blocked",
  "execution_level": "smoke | small_scale | formal",
  "run_record_ref": "",
  "checkpoint_ref": "",
  "actual_budget": {},
  "blocking_issues": [],
  "recovery": {},
  "recommended_next_action": "result-diagnosis | research-execution"
}
```

Use `result-diagnosis` only for a newly completed, validated, usable run. Otherwise return to `research-execution`; never invoke another child skill directly.

## Non-negotiable rules

- Run only the reviewed command at or below the approved execution level.
- Never turn smoke, small-scale, toy, synthetic, or dry-run output into formal evidence.
- Never claim network or filesystem isolation that the executor did not enforce.
- Never expose secrets in requests, logs, environment snapshots, or records.
- Never exceed a budget snapshot or continue after a root-owned stop condition.
- Never overwrite or discard historical attempts.
- Never interpret metrics, choose the next experiment, or modify research scope.
