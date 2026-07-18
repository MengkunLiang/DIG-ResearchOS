---
name: baseline-reproduction
description: Reproduce, repair, and validate required ResearchOS baselines against the locked experiment protocol using Phase B approved resources and a root-owned iteration plan. Use when `research-execution` dispatches D1 because a required baseline has no valid reproduction, a reproduction fingerprint became stale, or a bounded repair/rerun is authorized. Capture command, config, dataset/split, seed/repeat, metric output, raw logs, code/resource/environment provenance, failure classification, repair attempts, sanity comparison, comparability status, reviewer verdict, and claim risk. Do not search for new resources, silently replace a required baseline, redesign the experiment protocol, approve formal comparison, modify the proposed method, or run unreviewed third-party setup/install scripts.
---

# Baseline Reproduction

Turn an approved baseline resource into auditable execution evidence. A runnable repository is not a reproduced baseline, and a successful exit code is not a comparable result.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before any write:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/result_pack.json#context_alignment`;
   - `<workspace>/external_executor/result_pack.json#resource_readiness`;
   - `<workspace>/external_executor/result_pack.json#baseline_candidates`;
   - `<workspace>/external_executor/result_pack.json#experiment_plan`;
   - the current root-owned iteration plan;
   - `<skill-dir>/references/reproduction-policy.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` when the locked protocol, approved baseline candidate, active iteration authorization, or safe writable run boundary cannot be identified.

Write only:

- `external_executor/report/phase_D/baseline_reproduction_preflight.json`;
- `external_executor/report/phase_D/baseline_reproduction_plan.json`;
- `external_executor/report/phase_D/baseline_reproduction_report.json`;
- controlled baseline deployments, copied source, configs, and repair patches under `external_executor/expr/baselines/<baseline-id>/`;
- environment records, run records, normalized metrics, failure classifications, evaluations, and other process reports under `external_executor/report/phase_D/baseline_reproduction/`;
- baseline stdout/stderr logs, baseline-produced output files, per-dataset/per-metric raw-result CSV files, and other original experiment outputs under `external_executor/raw_results/baseline_reproduction/`;
- `result_pack.json#baseline_reproduction` through the narrow apply script.

Do not change resources, experiment design, iteration decisions, executor status, run manifest, proposed-method code, or sibling-owned result-pack sections. The root registers produced artifacts and chooses the next child.

## Read the operating contracts

Read these references when their step is reached:

- `references/reproduction-plan-contract.md` before planning;
- `references/environment-and-execution-safety.md` before any command;
- `references/run-record-contract.md` before recording a run;
- `references/failure-and-repair-taxonomy.md` after a failed or unusable run;
- `references/sanity-and-comparability.md` before judging reproduction;
- `references/output-contract.md` before assembling the final report.

Project-specific compilation should inject baseline identity, approved candidate IDs, target protocol, expected entry points, expected metrics or sanity ranges, known risks, and explicit repair limits. Do not inject private credentials or mutable URLs.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_reproduction.py --workspace <workspace> \
  --output external_executor/report/phase_D/baseline_reproduction_preflight.json
```

Preflight must establish:

- context alignment is non-blocking;
- the minimum loop is feasible under resource readiness;
- at least one Phase B baseline candidate exists and approved candidates have `approved_for` containing `baseline_reproduction` or `formal_comparison`;
- experiment plan status and protocol fingerprint are present;
- a root-owned active iteration plan exists; ambiguous baseline authorization is reported as a warning;
- Phase D1 write targets are allowed by `allowed_paths.txt`.

Detailed source path, dataset split, metric, command, config, and output completeness is checked when building the reproduction plan; unresolved fields make plan items `incomplete` rather than being fully decided by preflight.

Warnings require targeted review. Blockers prevent execution.

## Build the reproduction plan

Run:

```bash
python <skill-dir>/scripts/build_reproduction_plan.py --workspace <workspace> \
  --output external_executor/report/phase_D/baseline_reproduction_plan.json
```

Complete each plan item using `references/reproduction-plan-contract.md`. Before execution, every item must specify:

- stable baseline, candidate, requirement, iteration, and reproduction IDs;
- immutable source/resource identity and local controlled path;
- protocol and fairness fingerprints;
- dataset/version/split/preprocessing;
- metric name, direction, aggregation, extraction rule, and reference/sanity rule;
- command as an argument vector, working directory, timeout, resource limits, and expected outputs;
- config files or explicit parameter map;
- seed/repeat policy;
- execution authorization and executable allowlist;
- repair budget and allowed repair classes;
- claim dependencies and consequences of non-reproduction.

Do not infer a missing primary metric or split from repository defaults. Do not edit the locked protocol to make the baseline pass.

## Prepare a deployed baseline workspace

For each authorized item, create a controlled copy:

```bash
python <skill-dir>/scripts/prepare_attempt.py --workspace <workspace> \
  --plan external_executor/report/phase_D/baseline_reproduction_plan.json \
  --reproduction-id <reproduction-id> --attempt <N>
```

Attempt 1 copies the approved source from `resources/`. For attempt `N>1`, the preparer automatically copies the immediately preceding deployed attempt's `source/`, `configs/`, and `patches/` when it exists, so authorized compatibility/debug repairs are cumulative. Use `--from-attempt <M>` only when the root explicitly selects a different earlier attempt. The prior deployment and its run evidence remain immutable; continue editing only the new attempt.

The deployment directory is:

```text
external_executor/expr/baselines/<baseline-id>/<reproduction-id>/attempt-<N>/
```

The paired evidence/report directory is:

```text
external_executor/report/phase_D/baseline_reproduction/<baseline-id>/<reproduction-id>/attempt-<N>/
```

The paired raw-result directory is only for original outputs produced by running the baseline, including stdout/stderr logs and metric/result files:

```text
external_executor/raw_results/baseline_reproduction/<baseline-id>/<reproduction-id>/attempt-<N>/
```

The deployment directory contains the copied baseline source, plan fragment, patch/config directories, and attempt provenance with initial source/config manifest hashes and, for a repaired attempt, its parent-attempt identity. If execution fails because of implementation, environment, path, logging, or metric-extraction problems, debug and modify the deployed copy under `external_executor/expr/baselines/<baseline-id>/...` until the authorized baseline command can run or a bounded stop condition is reached. After an attempt has produced an immutable run record, create a new inherited attempt before further repair or rerun. Never patch the Phase B source in `resources/` in place. Reject path-escaping symlinks.

When modifying/debugging the deployed baseline, stay inside the baseline's original core idea, core modules, and core design. You may fix compatibility, paths, configuration plumbing, deterministic seed handling, logging, and metric extraction. You may not replace the algorithm, omit a required core module, substitute a different model or objective, use extra data/pretraining/tuning budget, change the locked dataset/split/metric, or otherwise cross the baseline's conceptual boundary merely to make the command succeed. Record material edits and risks in the report.

## Capture the execution environment

Run before the first command and after a material environment repair:

```bash
python <skill-dir>/scripts/capture_environment.py \
  --path <evidence-dir>/environment.json \
  --source <deployment-dir>/source
```

Capture OS, architecture, Python/runtime, installed packages, selected hardware facts, Git identity, and declared non-secret environment names. Do not store secret values. Treat a material environment change as a new attempt or new fingerprint.

## Execute only the authorized command

Read `references/environment-and-execution-safety.md`, then run:

```bash
python <skill-dir>/scripts/run_reproduction.py --workspace <workspace> \
  --plan external_executor/report/phase_D/baseline_reproduction_plan.json \
  --reproduction-id <reproduction-id> --attempt <N>
```

The runner:

- uses an argument vector and never `shell=True`;
- checks the executable against the plan allowlist;
- sanitizes inherited environment variables and withholds credentials by default;
- executes from the deployment directory under `external_executor/expr/`;
- exposes `RESEARCHOS_OUTPUT_DIR` and `RESEARCHOS_RAW_RESULTS_DIR` under the paired raw-result directory;
- writes stdout/stderr continuously to the raw-result directory;
- records start/end time, exit status, timeout, resource usage, expected-output checks, and checksums;
- keeps process records, environment captures, normalized metrics, failure classifications, and evaluations in the report/evidence directory rather than `raw_results`;
- preserves failed and superseded attempts;
- never upgrades evidence level based on exit code alone.

Do not run setup, install, download, container, Makefile, notebook, or arbitrary shell commands unless the current iteration plan and security review explicitly authorize that exact operation. Resource acquisition belongs to Phase B.

## Extract and normalize metrics

Use a declared extractor; never copy a number from the console by memory:

```bash
python <skill-dir>/scripts/extract_metrics.py \
  --attempt-dir <deployment-dir> \
  --spec <deployment-dir>/plan_fragment.json \
  --output <evidence-dir>/metrics.json
```

Supported generic extraction modes are JSON file/path, JSON Lines, CSV column, and regex against a named log. Record the exact source file, selector, units, direction, aggregation, and raw matched value in the normalized report file. The extractor must also write per-dataset/per-metric metric values as CSV under `<raw-result-dir>/raw_metrics/<dataset>/<metric>.csv` and reference those files from `metrics.json`. If multiple seeds/repeats are planned, keep per-run values and aggregate only by the locked rule. Do not write run records, environment captures, normalized JSON reports, or diagnostics under `external_executor/raw_results/`; raw baseline logs and original baseline outputs may stay there.

## Classify failures before repair

When execution or evidence validation fails, run:

```bash
python <skill-dir>/scripts/classify_failure.py \
  --run-record <evidence-dir>/run_record.json \
  --stdout <raw-result-dir>/stdout.log \
  --stderr <raw-result-dir>/stderr.log \
  --output <evidence-dir>/failure_classification.json
```

Use the taxonomy in `references/failure-and-repair-taxonomy.md`. The script provides a heuristic proposal; the Builder and Reviewer must inspect the direct evidence before accepting the category.

Allowed actions are:

```text
repair
rerun
mark_unavailable
request_replacement_review
block_execution
```

A repair must be classified and bounded. Record changed files, patch, rationale, algorithm/protocol impact, fairness impact, and new fingerprint. Environment compatibility fixes, path/config adapters, deterministic seed plumbing, and logging/metric extraction fixes may be repaired directly in the deployed baseline under `external_executor/expr/baselines/` when authorized. Any change that alters or omits the baseline's core idea, core module, core objective, or core design is forbidden for this Skill. Algorithm substitutions, stronger pretraining, extra data, changed split, changed metric, favorable hyperparameter expansion, or partial implementations that skip required core behavior require root/human review and are not ordinary repairs.

## Evaluate reproduction and comparability

Run after each candidate evidence bundle:

```bash
python <skill-dir>/scripts/evaluate_reproduction.py \
  --plan-fragment <deployment-dir>/plan_fragment.json \
  --run-record <evidence-dir>/run_record.json \
  --metrics <evidence-dir>/metrics.json \
  --environment <evidence-dir>/environment.json \
  --output <evidence-dir>/reproduction_evaluation.json
```

Use `references/sanity-and-comparability.md`. Separate:

- executability;
- method/config fidelity;
- protocol fidelity;
- metric completeness;
- paper/reference agreement;
- statistical/repeat sufficiency;
- formal comparability.

Allowed technical outcomes:

```text
reproduced_within_tolerance
reproduced_directionally
partially_reproduced
executable_only
failed
unavailable
blocked
```

Allowed comparability states:

```text
formal_review_candidate
conditional_comparison_only
smoke_only
not_comparable
```

`formal_review_candidate` means the baseline evidence is complete enough for the separate `code-and-protocol-review` Skill. It is not formal approval.

## Perform an independent reproduction review

When independent workers are available, use a fresh reviewer that reads the plan, source identity, patch, config, environment, raw logs, metric extraction, reference rule, and all attempts directly. Otherwise perform a separate sequential review after Builder work stops.

The reviewer records:

- `verdict = pass | needs_fix | blocked`;
- identity/mechanism fidelity;
- protocol and fairness fidelity;
- environment and dependency adequacy;
- run and metric provenance completeness;
- sanity/reference comparison validity;
- repair impact and approximation level;
- unresolved findings and required fixes;
- `approved_for = formal_review_candidate | conditional_comparison_only | smoke_only | none`;
- claim risks.

Do not let the Builder's summary substitute for raw evidence. Do not label a result “paper reproduced” merely because one scalar is close.

## Assemble, validate, and apply the report

Initialize or resume the report envelope:

```bash
python <skill-dir>/scripts/initialize_reproduction_report.py --workspace <workspace> \
  --plan external_executor/report/phase_D/baseline_reproduction_plan.json \
  --output external_executor/report/phase_D/baseline_reproduction_report.json
```

Populate attempts, evaluations, reviews, repairs, risks, and Artifact references. Preserve prior attempts and stale history. Then run:

```bash
python <skill-dir>/scripts/compute_reproduction_gate.py \
  --report <workspace>/external_executor/report/phase_D/baseline_reproduction_report.json \
  --write-back

python <skill-dir>/scripts/validate_reproduction_report.py --workspace <workspace> \
  --report external_executor/report/phase_D/baseline_reproduction_report.json

python <skill-dir>/scripts/apply_reproduction_report.py --workspace <workspace> \
  --report external_executor/report/phase_D/baseline_reproduction_report.json
```

The apply script updates only `result_pack.json#baseline_reproduction`.

Gate meaning:

- `pass`: all required baseline items in the active iteration have passing reviews and are `formal_review_candidate` under the current protocol/fairness fingerprint.
- `partial`: useful baseline evidence exists or ours smoke work may continue, but one or more required comparisons remain conditional, incomplete, stale, or unavailable; superiority claims remain blocked.
- `blocked`: a required baseline cannot proceed because of protocol, resource, access, security/license, budget authorization, or unresolved material fidelity failure.

## Return to the root

Return:

```text
child_skill=baseline-reproduction
status=complete|partial|blocked|failed
reproduction_gate=pass|partial|blocked
report=external_executor/report/phase_D/baseline_reproduction_report.json
plan=external_executor/report/phase_D/baseline_reproduction_plan.json
reproduced_baseline_ids=<ids>
conditional_baseline_ids=<ids>
blocking_baseline_ids=<ids>
stale_baseline_ids=<ids>
claim_risks=<ids-or-summary>
recommended_next_action=continue_to_method_refinement|continue_to_implementation|baseline_repair|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns manifest registration, global status, budget accounting, scope-change gates, iteration decisions, and dispatch.

## Evidence and boundary rules

- Preserve every attempt; never overwrite a failed or superseded run.
- A result without command, config, split, seed/repeat, raw log, per-dataset/per-metric raw metric CSV, metric output, source version, environment, and protocol fingerprint is not comparable evidence.
- Keep paper/author reference values separate from reproduced measurements.
- Record the acceptance rule before judging the result; never loosen tolerance after seeing failure without a versioned, reviewed protocol change.
- `smoke`, toy, synthetic, shortened training, reduced data, or fewer repeats cannot become formal baseline evidence.
- A repaired baseline must remain the same baseline. Mechanism changes create a replacement/scope request.
- Do not hide negative, unstable, or lower-than-paper results.
- Do not search for or download new resources; return a resource gap to the root.
- Do not approve formal experiments; hand `formal_review_candidate` evidence to `code-and-protocol-review`.
- Do not make superiority, SOTA, or paper-level reproducibility claims.

## Resource map

- `references/reproduction-policy.md`: scope, authority, attempt lifecycle, and hard boundaries.
- `references/reproduction-plan-contract.md`: per-baseline execution plan and stable IDs.
- `references/environment-and-execution-safety.md`: sandbox, command, secret, network, and process rules.
- `references/run-record-contract.md`: command/config/data/seed/log/metric/environment provenance.
- `references/failure-and-repair-taxonomy.md`: failure categories, permitted repair classes, and escalation.
- `references/sanity-and-comparability.md`: outcome levels, reference rules, repeats, and fairness gate.
- `references/output-contract.md`: report schema, result-pack mapping, gate consistency, and child return.
- `scripts/preflight_reproduction.py`: validate prerequisites and active authorization.
- `scripts/build_reproduction_plan.py`: create a deterministic plan scaffold from approved artifacts.
- `scripts/prepare_attempt.py`: create an isolated source/config attempt workspace.
- `scripts/capture_environment.py`: record non-secret environment and source identity.
- `scripts/run_reproduction.py`: bounded argv-based execution with durable logs and run record.
- `scripts/extract_metrics.py`: normalize metrics from declared files/logs.
- `scripts/classify_failure.py`: propose a structured failure category from direct evidence.
- `scripts/evaluate_reproduction.py`: compute technical reproduction and comparability assessment.
- `scripts/initialize_reproduction_report.py`: create/resume the report envelope.
- `scripts/compute_reproduction_gate.py`: derive pass/partial/blocked from reviewed items.
- `scripts/validate_reproduction_report.py`: enforce schema, references, and gate consistency.
- `scripts/apply_reproduction_report.py`: atomically update only `result_pack.baseline_reproduction`.
