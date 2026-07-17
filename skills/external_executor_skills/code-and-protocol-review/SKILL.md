---
name: code-and-protocol-review
description: Independently review ResearchOS external-executor implementation changes and experiment protocols before smoke, small-scale, or formal runs. Use when `research-execution` dispatches Phase D3 after code, config, adapter, dataset split, metric, evaluation, ablation, logging, or protocol changes; when a prior review is stale; or when deciding `review_status`, `approved_for`, required fixes, repair owner, fairness risk, data-leakage risk, reproducibility, security/path compliance, and contribution drift. Do not use to implement fixes, run formal experiments, reinterpret results, approve major scope changes, or review static resource acquisition fidelity owned by the resource-preparation skill.
---

# Code and Protocol Review

Act as an independent gate between Builder output and experiment execution. Verify the actual diff/snapshot and fresh evidence; never approve from the Builder's summary alone.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish scope and ownership

1. Locate the nearest directory containing `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read:
   - `external_executor/AGENTS.md` and `allowed_paths.txt`;
   - `handoff_pack.json#method_intent`;
   - `result_pack.json#context_alignment` and confirmed execution scope;
   - the current iteration plan, implementation specification/delta, experiment plan, protocol fingerprint, baseline reproduction status, and run manifest;
   - the Builder's changed paths, tests, configs, adapters, and claimed verification evidence.
   Deployed baseline and ours code/config snapshots under review must come from `external_executor/expr/`; raw run evidence referenced by the review must come from `external_executor/raw_results/`.
4. Write only review snapshots/reports under `external_executor/` and append one review record to `result_pack.json#implementation_reviews` through the apply script.

Do not edit code, config, protocol, raw results, handoff, executor status, or another result-pack section. Return fixes to the owning Builder skill.

## Load review policy

Read:

- `references/scope-and-dispatch-policy.md` before fixing the review boundary;
- `references/review-axes-and-severity.md` before recording findings;
- `references/protocol-fairness-checklist.md` for dataset, metric, baseline, ablation, and leakage review;
- `references/evidence-and-verification-policy.md` before accepting tests or checks;
- `references/output-contract.md` before issuing a verdict.

## Pin a review baseline

Review a fixed input snapshot, not a moving worktree. The root or Builder must identify the iteration, requested approval level, changed paths, implementation spec, affected experiments, protocol fingerprint, and verification commands.

Create a deterministic snapshot:

```bash
python <skill-dir>/scripts/snapshot_review_inputs.py --workspace <workspace> \
  --iteration-id <iteration-id> \
  --path external_executor/expr/<changed-code-or-config-path> [--path external_executor/expr/<another-path>] \
  --output external_executor/reviews/<iteration-id>/input_snapshot.json
```

When a prior snapshot exists, pass it with `--baseline-snapshot` to classify added, modified, removed, and unchanged files. Stop with `blocked` if the changed scope cannot be pinned, required files are missing, or paths escape the workspace/allowed boundary.

Per-iteration review is task-scoped, but expand the scope when a change crosses a protocol seam: shared data loader, split generator, metric/evaluation code, baseline adapter, global config, seed utility, checkpoint loader, or common training loop. Record why the expansion is necessary.

## Gather deterministic review signals

Run static candidate scanning on the pinned snapshot:

```bash
python <skill-dir>/scripts/scan_code_risks.py --workspace <workspace> \
  --snapshot external_executor/reviews/<iteration-id>/input_snapshot.json \
  --output external_executor/reviews/<iteration-id>/static_candidates.json
```

Treat scanner results as candidates, never automatic findings. Inspect the code and surrounding logic before keeping or dismissing each candidate.

For formal-comparison review, normalize baseline and ours into the protocol comparison contract and run:

```bash
python <skill-dir>/scripts/compare_protocols.py --workspace <workspace> \
  --input external_executor/reviews/<iteration-id>/protocol_comparison.json \
  --output external_executor/reviews/<iteration-id>/protocol_comparison_report.json
```

Any allowed difference must be explicitly listed with a rationale. Do not mark a difference allowed merely because it benefits ours or makes a baseline easier to run.

## Review independent axes

Review these axes separately so one pass cannot hide another failure:

1. **Spec and method alignment** — code implements the approved method intent/spec; required modules are present; no unapproved behavior or scope creep.
2. **Code correctness** — shapes, objectives, gradients, state, error handling, checkpointing, tests, and configuration wiring are credible.
3. **Protocol fairness** — dataset/split/preprocessing/metric/evaluation/seed/tuning/extra-data/pretraining/compute treatment is comparable.
4. **Data integrity** — no leakage, train/test contamination, target leakage, duplicate contamination, or evaluation-time fitting.
5. **Reproducibility and observability** — commands, configs, seeds, versions, environment, logging, raw outputs, patch/code identity, and ablation switches are recoverable.
6. **Security and path compliance** — no unauthorized paths, secret exposure, unsafe third-party execution, or destructive behavior.
7. **Contribution drift** — implementation does not replace the core mechanism, change task/benchmark/contribution type, silently drop baselines, or promote an engineering trick into a paper contribution.

If independent workers are available and authorized, use two isolated reviewers:

- Spec Reviewer: axes 1 and 7 against the fixed method/spec baseline.
- Code/Protocol Reviewer: axes 2-6 against code, configs, protocol, and fresh evidence.

Give both the same snapshot ID and do not leak the other's conclusions. If workers are unavailable, perform the two reviews sequentially and record each axis before aggregation.

## Require fresh verification

Identify which command or deterministic check proves each approval claim. Execute only authorized, review-relevant checks. Save full output and exit code; do not accept “tests passed” without logs tied to the current snapshot fingerprint.

Validate the evidence bundle:

```bash
python <skill-dir>/scripts/validate_verification_evidence.py --workspace <workspace> \
  --snapshot external_executor/reviews/<iteration-id>/input_snapshot.json \
  --evidence external_executor/reviews/<iteration-id>/verification_evidence.json
```

Formal approval requires evidence for relevant tests, protocol comparison, config/logging validation, and any changed metric/split/ablation path. A linter does not prove scientific correctness; one smoke run does not prove formal fairness.

## Record findings precisely

Each finding must name:

- axis and category;
- severity: `info`, `warning`, `major`, or `blocking`;
- exact affected file/config/protocol field;
- evidence references;
- why it matters for execution, fairness, reproducibility, claims, or scope;
- the smallest sufficient required fix;
- repair owner: `baseline-reproduction`, `method-refinement`, `implementation`, `experiment-design`, or `research-execution`;
- which run levels it blocks.

Separate `unknown` from `failed`. Missing evidence is not proof of correctness or incorrectness; it is a gate limitation.

For path compliance, check that executable deployments and code dependencies live under `external_executor/expr/`, prepared resources are read from `resources/` for by-hand local material or `resource/` for acquired/reimplemented material, and logs, metrics, run records, checkpoints, and run-produced artifacts are under `external_executor/raw_results/`.

## Issue the gate verdict

Use `references/output-contract.md` and create:

```text
external_executor/reviews/<iteration-id>/review_report.json
```

Verdict rules:

- `pass`: no unresolved major/blocking finding; required axes and fresh verification support the requested approval level.
- `needs_fix`: one or more fixable findings prevent the requested approval, with concrete fixes and repair owners.
- `blocked`: authority, material scope drift, security/license/path violation, unresolvable fairness problem, missing fixed baseline, or missing evidence prevents a safe review.

`approved_for` is the highest supported level: `smoke`, `small_scale`, `formal`, or `none`. Never approve above the requested level. `formal` requires every mandatory axis to pass and a complete current-snapshot evidence bundle.

Validate and apply:

```bash
python <skill-dir>/scripts/validate_review_report.py --workspace <workspace> \
  --report external_executor/reviews/<iteration-id>/review_report.json

python <skill-dir>/scripts/apply_review_report.py --workspace <workspace> \
  --report external_executor/reviews/<iteration-id>/review_report.json
```

The apply script only appends/upserts the review by `review_id` in `result_pack.implementation_reviews`.

## Return control to the root

Return:

```text
child_skill=code-and-protocol-review
status=complete|partial|blocked|failed
review_status=pass|needs_fix|blocked
approved_for=smoke|small_scale|formal|none
review_id=<id>
report=<path>
required_fixes=<ids>
repair_owners=<skills>
recommended_next_action=experiment-run|baseline-reproduction|method-refinement|implementation|experiment-design|human_review
```

`research-execution` decides the next route. This Skill does not implement fixes or launch the approved experiment.

## Non-negotiable rules

- Do not trust Builder completion claims without fresh evidence.
- Do not review an unpinned or changing snapshot.
- Do not let tests passing substitute for line-by-line spec/protocol review.
- Do not let protocol review substitute for code correctness review.
- Do not rerun broad expensive suites without a change-specific reason; final whole-project audit is a separate scope.
- Do not pre-judge a suspected finding for an independent reviewer.
- Do not approve formal runs with missing baseline fairness, split/metric provenance, ablation correctness, or logging/config recovery.
- Do not silently repair findings, approve scope drift, or interpret experimental results.

## Resource map

- `references/scope-and-dispatch-policy.md`: fixed snapshot, review scope, and independent reviewer dispatch.
- `references/review-axes-and-severity.md`: axes, finding severity, repair ownership, and gate impact.
- `references/protocol-fairness-checklist.md`: scientific protocol, leakage, baseline, metric, and ablation checks.
- `references/evidence-and-verification-policy.md`: fresh verification and evidence sufficiency.
- `references/output-contract.md`: review report and result-pack contract.
- `scripts/snapshot_review_inputs.py`: hash and diff review inputs.
- `scripts/scan_code_risks.py`: emit static risk candidates for human/LLM adjudication.
- `scripts/compare_protocols.py`: compare normalized baseline and ours protocols.
- `scripts/validate_verification_evidence.py`: validate current-snapshot check evidence.
- `scripts/validate_review_report.py`: enforce verdict and approval invariants.
- `scripts/apply_review_report.py`: atomically upsert only `implementation_reviews`.
