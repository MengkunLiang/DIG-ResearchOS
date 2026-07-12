# Result Diagnosis Output Contract

## Child-owned artifacts

```text
external_executor/result_diagnosis_preflight.json
external_executor/diagnosis_evidence_snapshot.json
external_executor/diagnosis_statistics.json
external_executor/result_diagnosis_report.json
external_executor/workdir/result_diagnosis/**
```

The root owns manifest registration, executor status and iteration decisions.

## Report envelope

```json
{
  "schema_version": "result_diagnosis_report.v1",
  "child_skill": "result-diagnosis",
  "status": "complete|partial|blocked|failed",
  "generated_at": "",
  "diagnosis_id": "",
  "iteration_id": "",
  "input_fingerprint": "",
  "evidence_snapshot": {"status": "complete|partial|blocked|stale", "ref": "", "included_run_ids": [], "excluded_run_ids": []},
  "metric_summaries": {"status": "complete|partial|blocked|stale", "items": []},
  "method_comparisons": {"status": "complete|partial|blocked|stale", "items": []},
  "strongest_baselines": {"status": "complete|partial|blocked|stale", "items": []},
  "setting_diagnostics": {"status": "complete|partial|blocked|stale", "items": []},
  "anomalies": {"status": "complete|partial|blocked|stale", "items": []},
  "confound_assessments": {"status": "complete|partial|blocked|stale", "items": []},
  "claim_implications": {"status": "complete|partial|blocked|stale", "items": []},
  "evidence_requests": {"status": "complete|partial|blocked|stale", "items": []},
  "risks": {"status": "complete|partial|blocked|stale", "items": []},
  "diagnosis_gate": {
    "status": "ready_for_attribution|partial|blocked",
    "evidence_sufficiency": "sufficient_for_attribution|limited|insufficient",
    "material_anomaly_ids": [],
    "blocking_issue_ids": [],
    "claim_counts": {},
    "next_action": "continue_to_module_attribution|add_diagnostic_run|repair_or_rerun|human_review|stop_and_report"
  },
  "artifact_refs": [],
  "notes": []
}
```

Required sections remain present under blocked/failed states.

## Evidence reference vocabulary

A report item may cite:

```text
RUN-     run evidence
OBS-     metric observation
AGG-     aggregate
CMP-     comparison
BASE-    strongest-baseline fact
ANOM-    anomaly
CLAIM-   claim contract
EXP-     experiment plan item
REVIEW-  implementation/protocol review
BREP-    baseline reproduction record
```

The validator must be able to resolve every evidence reference from the snapshot/statistics or known result-pack input.

## Interpretation item rules

Every setting diagnosis, confound assessment and claim implication needs:

- non-empty evidence refs;
- confidence;
- bounded status vocabulary;
- no Builder/root authority fields;
- no final-paper or T7-audited status;
- no causal conclusion flag.

## Gate consistency

### `ready_for_attribution`

Requires:

- at least one usable comparable method/baseline surface or a valid controlled diagnostic surface;
- no blocking metric/protocol/fairness issue;
- strongest baseline facts where required;
- all substantive interpretations have evidence refs and confidence;
- material anomalies are propagated;
- evidence requests state remaining limitations.

### `partial`

Use when useful diagnosis exists but repeats, coverage, stability, provenance or confound control is limited. `next_action` is usually `add_diagnostic_run`, `repair_or_rerun`, or conditionally `continue_to_module_attribution` with explicit limitations.

### `blocked`

Use when no scientifically usable comparison exists, metric/protocol identity is unresolved, required baseline coverage is absent for the central question, or a blocking fairness/provenance issue remains.

## Result-pack mapping

The apply script writes only:

```text
result_pack.result_diagnoses = {
  status,
  items: append-or-replace by diagnosis_id,
  current_by_iteration: {iteration_id: diagnosis_id}
}
```

Do not write root decisions or `result_pack.module_attributions`.

## Child return

```text
child_skill=result-diagnosis
status=complete|partial|blocked|failed
diagnosis_gate=ready_for_attribution|partial|blocked
iteration_id=<id>
diagnosis_id=<id>
report=external_executor/result_diagnosis_report.json
strongest_baseline_ids=<ids>
material_anomaly_ids=<ids>
claim_implication_summary=<counts>
recommended_next_action=<enum>
```
