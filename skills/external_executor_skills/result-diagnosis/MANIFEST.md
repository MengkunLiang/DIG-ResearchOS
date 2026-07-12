# result-diagnosis Skill Manifest

## Purpose

`result-diagnosis` is the Phase E1 evidence-analysis Skill. It pins one iteration's run evidence, computes descriptive comparison facts, identifies anomalies/confounds, and produces evidence-bound setting and claim implications. It never runs experiments, performs module-level causal attribution, or makes the root iteration decision.

## Directory tree

```text
result-diagnosis/
├── SKILL.md
├── MANIFEST.md
├── references/
│   ├── diagnosis-policy.md
│   ├── evidence-and-run-eligibility.md
│   ├── metric-and-statistics.md
│   ├── anomaly-and-confound-taxonomy.md
│   ├── setting-and-claim-diagnosis.md
│   ├── confidence-and-causality.md
│   └── output-contract.md
├── scripts/
│   ├── _common.py
│   ├── preflight_diagnosis.py
│   ├── build_evidence_snapshot.py
│   ├── normalize_run_metrics.py
│   ├── aggregate_results.py
│   ├── compare_methods.py
│   ├── detect_anomalies.py
│   ├── build_diagnosis_facts.py
│   ├── initialize_diagnosis_report.py
│   ├── compute_diagnosis_gate.py
│   ├── validate_diagnosis_report.py
│   └── apply_diagnosis_report.py
└── tests/
    └── test_result_diagnosis_scripts.py
```

## Main file

### `SKILL.md`

Defines ownership, preflight, evidence snapshotting, metric normalization, repeat aggregation, paired comparison, strongest-baseline analysis, anomaly/confound review, evidence-bound interpretation, diagnosis Gate, narrow apply and root return.

## References

- `diagnosis-policy.md`: role, workflow, resume, no-op and history semantics.
- `evidence-and-run-eligibility.md`: evidence classes, minimum formal provenance and exclusion reasons.
- `metric-and-statistics.md`: comparability key, descriptive summaries, pairing, practical magnitude and small-sample limits.
- `anomaly-and-confound-taxonomy.md`: structured evidence, protocol, stability, coverage and fairness risks.
- `setting-and-claim-diagnosis.md`: strongest baseline, win/fail setting and pre-audit claim implication schema.
- `confidence-and-causality.md`: calibrated confidence and prohibition on mechanism-level causal conclusions.
- `output-contract.md`: report envelope, evidence IDs, Gate consistency, narrow result-pack mapping and child return.

## Scripts

- `_common.py`: workspace/path/JSON/hash/ID/section and evidence helpers.
- `preflight_diagnosis.py`: validates Phase E1 prerequisites and identifies the iteration.
- `build_evidence_snapshot.py`: pins all included/excluded run records and classifies evidence use.
- `normalize_run_metrics.py`: flattens machine-readable metric values into observations.
- `aggregate_results.py`: computes count, center, spread and descriptive interval by exact comparability key.
- `compare_methods.py`: computes direction-adjusted ours-vs-baseline deltas and paired seed facts.
- `detect_anomalies.py`: finds missing/ineligible evidence, low repeats, variance/outlier, pairing and coverage issues.
- `build_diagnosis_facts.py`: identifies strongest eligible baselines and setting-level numerical facts.
- `initialize_diagnosis_report.py`: creates the full report envelope and imports deterministic evidence.
- `compute_diagnosis_gate.py`: derives `ready_for_attribution`, `partial` or `blocked`.
- `validate_diagnosis_report.py`: validates schema, evidence refs, confidence, Gate consistency and causal boundary.
- `apply_diagnosis_report.py`: atomically updates only `result_pack.result_diagnoses` with append-preserving semantics.

## Tests

`tests/test_result_diagnosis_scripts.py` covers:

- preflight and evidence eligibility;
- metric normalization;
- repeat aggregation and paired comparisons;
- strongest-baseline selection;
- insufficient-repeat anomaly detection;
- evidence-bound interpretation and Gate;
- report validation and narrow apply;
- preservation of unrelated result-pack sections;
- rejection of causal claims and unknown evidence references.

## Dependencies

Runtime scripts use only the Python standard library. The generic implementation deliberately does not fabricate p-values or auto-select complex statistical tests. Project-specific statistical procedures should be declared by the experiment plan and introduced during T5 customization.
