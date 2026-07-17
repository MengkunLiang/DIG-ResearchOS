---
name: result-diagnosis
description: Diagnose one ResearchOS experiment iteration from durable run records and raw metric evidence. Use when `research-execution` dispatches Phase E1 because a new usable experiment-run set has not yet been diagnosed, or when a prior diagnosis became stale after run, protocol, metric, fairness, or claim-boundary changes. Pin an evidence snapshot, normalize metrics, aggregate repeats, compare ours with required baselines, identify strongest baselines, winning and failing settings, variance and protocol anomalies, plausible confounds, and pre-audit claim implications. Produce a per-iteration diagnosis with evidence references, confidence, risks, and an advisory next-action surface. Do not run experiments, modify code or protocol, perform module-level causal attribution, make the root iteration decision, approve claims, or convert smoke/small-scale evidence into formal support.
---

# Result Diagnosis

Act as the evidence-bound analyst for one completed or partially completed ResearchOS iteration. Describe what the run evidence shows, what it does not show, and what questions remain. `module-attribution` owns mechanism attribution; `research-execution` owns the iteration decision.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before writing:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/result_pack.json#context_alignment`;
   - `<workspace>/external_executor/result_pack.json#claim_evidence_matrix`;
   - `<workspace>/external_executor/result_pack.json#experiment_plan`;
   - `<workspace>/external_executor/result_pack.json#baseline_reproduction`;
   - `<workspace>/external_executor/result_pack.json#implementation_reviews`;
   - `<workspace>/external_executor/result_pack.json#experiment_runs`;
   - the root-owned active iteration plan;
   - `<skill-dir>/references/diagnosis-policy.md`;
   - `<skill-dir>/references/evidence-and-run-eligibility.md`;
   - `<skill-dir>/references/output-contract.md`.
   Raw logs, metric files, run records, checkpoints, and run-produced artifacts referenced by those records must resolve under `<workspace>/external_executor/raw_results/`.
4. Stop with `blocked` when no iteration can be identified, the run set is absent, protocol/metric direction is indeterminate, formal records are presented without minimum provenance, raw evidence is outside `external_executor/raw_results/`, or the writable boundary cannot be determined.

Write only:

- `external_executor/result_diagnosis_preflight.json`;
- `external_executor/diagnosis_evidence_snapshot.json`;
- `external_executor/diagnosis_statistics.json`;
- `external_executor/result_diagnosis_report.json`;
- versioned analysis artifacts under `external_executor/result_diagnosis/`;
- `result_pack.json#result_diagnoses` through the narrow apply script.

Do not change run records, raw results, configs, logs, protocol fingerprints, reviews, iteration plans/decisions, method specifications, module attributions, executor status, manifest, budget, or sibling-owned sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

```bash
python <skill-dir>/scripts/preflight_diagnosis.py --workspace <workspace> \
  --output external_executor/result_diagnosis_preflight.json
```

The preflight confirms:

- context alignment and the current experiment plan are non-blocking;
- an iteration and at least one terminal run record exist;
- metric names and directions are recoverable;
- required-baseline and claim mappings can be located;
- formal runs are distinguishable from smoke, small-scale, diagnostic, and failed runs;
- no unsupported major schema or path boundary prevents analysis.

Warnings become diagnosis limitations; blockers prevent positive claim implications.

## Pin the iteration evidence snapshot

Read `references/evidence-and-run-eligibility.md`, then run:

```bash
python <skill-dir>/scripts/build_evidence_snapshot.py --workspace <workspace> \
  --iteration-id <iteration-id> \
  --output external_executor/diagnosis_evidence_snapshot.json
```

The snapshot must:

- copy no large raw artifact unnecessarily;
- identify every included, excluded, stale, unusable, failed, and incomplete run;
- preserve run type, analysis role, experiment/claim IDs, method/baseline identity, setting, dataset/split, seed/repeat, protocol fingerprint, code/resource version, review approval, metric and artifact references;
- assign stable evidence IDs;
- record why each run is eligible only for `engineering`, `diagnostic`, `small_scale`, `formal_candidate`, or `excluded` use;
- compute an input fingerprint over the exact diagnosis evidence surface.

Never diagnose from a conversational summary when a run record or metric artifact exists.

## Normalize metric observations

```bash
python <skill-dir>/scripts/normalize_run_metrics.py \
  --snapshot <workspace>/external_executor/diagnosis_evidence_snapshot.json \
  --output <workspace>/external_executor/result_diagnosis/<iteration-id>/metric_observations.json
```

Each observation records:

- observation ID and run evidence ID;
- experiment, claim, method, role, setting, dataset, split, seed/repeat, run type and analysis role;
- metric name, numeric value, direction, unit, aggregation and source reference;
- protocol and fairness fingerprints;
- evidence eligibility class.

Reject NaN/Inf as usable metric evidence. Do not reverse metric direction by intuition; use the locked protocol or explicit metric record.

## Aggregate comparable repeats

Read `references/metric-and-statistics.md`, then run:

```bash
python <skill-dir>/scripts/aggregate_results.py \
  --observations <metric-observations.json> \
  --output <workspace>/external_executor/result_diagnosis/<iteration-id>/metric_aggregates.json

python <skill-dir>/scripts/compare_methods.py \
  --aggregates <metric-aggregates.json> \
  --observations <metric-observations.json> \
  --output <workspace>/external_executor/result_diagnosis/<iteration-id>/method_comparisons.json
```

Only aggregate observations that agree on the declared comparability key:

```text
protocol fingerprint + dataset/version + split + preprocessing + metric/direction/aggregation
+ setting/subset + run type/evidence class + relevant fairness fingerprint
```

Report at least count, mean, median, standard deviation, median absolute deviation, range and a clearly labeled descriptive interval when repeats permit. Comparisons report direction-adjusted absolute/relative delta, paired seed coverage, win/tie/loss counts, paired standardized effect when defined, and practical-threshold status when the plan declares one.

Do not infer statistical significance when the planned test, sample structure, or required dependency is unavailable. `n=1` is a point observation, not stable evidence.

## Detect anomalies and confounds before interpretation

Read `references/anomaly-and-confound-taxonomy.md`, then run:

```bash
python <skill-dir>/scripts/detect_anomalies.py \
  --snapshot <snapshot.json> \
  --observations <metric-observations.json> \
  --aggregates <metric-aggregates.json> \
  --comparisons <method-comparisons.json> \
  --output <workspace>/external_executor/result_diagnosis/<iteration-id>/anomalies.json
```

Inspect at least:

- missing or contradictory metric direction;
- NaN/Inf, failed extraction, missing artifacts, stale/unusable runs;
- mixed protocol, split, preprocessing, metric aggregation or run type;
- seed/repeat imbalance and incomplete required baseline coverage;
- high variance, extreme outliers, instability and suspiciously identical values;
- baseline reproduction limitations and review constraints;
- extra data, stronger pretraining, capacity, compute, tuning or early-stopping imbalance;
- possible leakage, test-set tuning, favorable missingness or cherry-picking;
- divergence between planned and executed experiment roles.

A detected anomaly is a finding to inspect, not an automatic causal explanation.

## Build the deterministic diagnosis facts

```bash
python <skill-dir>/scripts/build_diagnosis_facts.py \
  --snapshot <snapshot.json> \
  --aggregates <metric-aggregates.json> \
  --comparisons <method-comparisons.json> \
  --anomalies <anomalies.json> \
  --output external_executor/diagnosis_statistics.json
```

This facts file identifies, per comparable setting:

- strongest eligible baseline;
- ours-versus-baseline ranking and deltas;
- settings where ours wins, loses, ties, is mixed, or is incomparable;
- repeat sufficiency and stability;
- missing mandatory comparisons;
- anomalies and unresolved confound surfaces.

Facts are descriptive. They are not final claims or mechanism explanations.

## Produce evidence-bound interpretation

Read:

- `references/setting-and-claim-diagnosis.md`;
- `references/confidence-and-causality.md`;
- `references/output-contract.md`.

Initialize the report:

```bash
python <skill-dir>/scripts/initialize_diagnosis_report.py --workspace <workspace> \
  --snapshot external_executor/diagnosis_evidence_snapshot.json \
  --statistics external_executor/diagnosis_statistics.json \
  --output external_executor/result_diagnosis_report.json
```

Then complete the interpretation sections. Every substantive item must include `evidence_refs`, `confidence`, and an interpretation level.

Diagnose:

1. strongest baseline and what the observed performance pattern shows;
2. settings/subsets where ours wins, loses, ties, is unstable, or remains incomparable;
3. metric, variance, stability, fairness and protocol anomalies;
4. plausible confounds and whether they are controlled, ruled out, unresolved or blocking;
5. claim implications: `supported`, `weakened`, `contradicted`, `unresolved`, or `not_tested`;
6. concrete missing evidence or diagnostic experiments;
7. risks to the next module-attribution step.

Use interpretation levels:

```text
observed_fact
descriptive_inference
plausible_hypothesis
unsupported
```

This skill must not output a causal mechanism conclusion. It may say a pattern is *consistent with* a hypothesis and request controlled evidence. Module-level causal reasoning belongs to `module-attribution`.

## Keep evidence roles separate

- `formal_candidate` evidence may support a moderate or strong **pre-audit** implication only when provenance, required comparisons and repeat/stability requirements are complete.
- `small_scale` evidence supports only provisional direction and feasibility.
- `diagnostic` evidence supports anomaly or hypothesis formation, not the main confirmatory claim unless the plan explicitly defined it as confirmatory.
- `smoke`, toy, synthetic and engineering evidence support no scientific performance claim.
- failed, stale, cancelled and unusable runs remain visible but cannot support positive findings.

Do not select the best seed, discard an unfavorable valid run, combine incompatible protocols, or compare metrics with different direction/aggregation.

## Compute the diagnosis gate

```bash
python <skill-dir>/scripts/compute_diagnosis_gate.py \
  --report <workspace>/external_executor/result_diagnosis_report.json \
  --write-back
```

Allowed gate outcomes:

- `ready_for_attribution`: the current evidence set is valid enough for module-level analysis; all material limitations remain explicit.
- `partial`: useful descriptive diagnosis exists, but missing repeats, baseline coverage, unstable metrics, unresolved confounds or incomplete formal provenance limits attribution or claims.
- `blocked`: no scientifically usable comparison exists, protocol/metric identity is unresolved, or a blocking fairness/provenance issue prevents diagnosis.

The gate provides an advisory next-action surface:

```text
continue_to_module_attribution
add_diagnostic_run
repair_or_rerun
human_review
stop_and_report
```

It does not make the root iteration decision.

## Validate and apply narrowly

```bash
python <skill-dir>/scripts/validate_diagnosis_report.py --workspace <workspace> \
  --report external_executor/result_diagnosis_report.json

python <skill-dir>/scripts/apply_diagnosis_report.py --workspace <workspace> \
  --report external_executor/result_diagnosis_report.json
```

The apply script updates only `result_pack.json#result_diagnoses`, preserving earlier iteration diagnoses and every sibling section. If validation fails, fix the report; do not bypass the validator.

## Return to the root

Return:

```text
child_skill=result-diagnosis
status=complete|partial|blocked|failed
diagnosis_gate=ready_for_attribution|partial|blocked
iteration_id=<id>
diagnosis_id=<id>
report=external_executor/result_diagnosis_report.json
strongest_baseline_ids=<ids>
material_anomaly_ids=<ids>
claim_implication_summary=<supported/weakened/contradicted/unresolved counts>
recommended_next_action=continue_to_module_attribution|add_diagnostic_run|repair_or_rerun|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns routing, budget, checkpointing, iteration decision and claim-boundary updates.

## Evidence and safety rules

- Artifact records and metric files are facts; model narratives are interpretations.
- Preserve negative, null, failed, stale and contradictory evidence.
- Every diagnosis item needs resolvable evidence references and confidence.
- Report practical magnitude separately from uncertainty and repeat sufficiency.
- Never treat missing baseline or missing seed as a win.
- Never convert `n=1` or a selected seed into a stable trend.
- Never promote smoke/small-scale/toy/synthetic evidence to formal support.
- Never infer causality from correlation, ranking or one uncontrolled comparison.
- Never modify run evidence to make the diagnosis cleaner.
- Keep per-iteration records; later diagnoses may supersede but must not overwrite history.

## Resource map

- `references/diagnosis-policy.md`: role, boundaries, workflow, resume and completion semantics.
- `references/evidence-and-run-eligibility.md`: evidence classes, minimum provenance and exclusion rules.
- `references/metric-and-statistics.md`: comparability keys, aggregation, uncertainty, pairing and practical magnitude.
- `references/anomaly-and-confound-taxonomy.md`: anomaly classes, severity and confound review.
- `references/setting-and-claim-diagnosis.md`: strongest baseline, win/fail setting and claim implication contracts.
- `references/confidence-and-causality.md`: confidence calibration, language and causal boundaries.
- `references/output-contract.md`: report schema, gate consistency, narrow apply and child return.
- `scripts/preflight_diagnosis.py`: validate prerequisites and identify the diagnosis iteration.
- `scripts/build_evidence_snapshot.py`: pin and classify the exact run evidence set.
- `scripts/normalize_run_metrics.py`: convert run metrics into comparable observations.
- `scripts/aggregate_results.py`: compute descriptive repeat summaries.
- `scripts/compare_methods.py`: compute baseline rankings and paired comparisons.
- `scripts/detect_anomalies.py`: detect provenance, variance, protocol and fairness anomalies.
- `scripts/build_diagnosis_facts.py`: compile deterministic strongest-baseline and setting facts.
- `scripts/initialize_diagnosis_report.py`: create a complete interpretation envelope.
- `scripts/compute_diagnosis_gate.py`: derive the diagnosis readiness gate.
- `scripts/validate_diagnosis_report.py`: enforce schema, evidence and authority boundaries.
- `scripts/apply_diagnosis_report.py`: atomically update only `result_pack.result_diagnoses`.
