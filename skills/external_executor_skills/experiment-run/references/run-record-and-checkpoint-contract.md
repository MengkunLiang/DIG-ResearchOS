# Run Record and Checkpoint Contract

## Contents

- [Run record](#run-record)
- [Artifact reference](#artifact-reference)
- [Checkpoint](#checkpoint)
- [Result-pack write](#result-pack-write)
- [Validation invariants](#validation-invariants)

## Run record

```json
{
  "schema_version": "external_executor_experiment_run.v1",
  "run_id": "",
  "experiment_id": "",
  "iteration_id": "",
  "request_ref": "",
  "request_fingerprint": "",
  "run_type": "",
  "execution_level": "",
  "analysis_role": "",
  "run_status": "completed | failed | cancelled | unusable | stale",
  "evidence_level": "raw_result | diagnostic_hint | unsupported",
  "evidence_use": "engineering_only | diagnostic_only | pre_audit_candidate | none",
  "review": {
    "review_ref": "",
    "review_id": "",
    "approved_for": "",
    "input_fingerprint": ""
  },
  "protocol_fingerprint": "",
  "command": [],
  "cwd": "",
  "config_ref": {},
  "dataset": {"id": "", "version": "", "split": ""},
  "seed": 0,
  "repeat_index": 0,
  "dependencies": [],
  "environment": {},
  "hardware": {},
  "started_at": "",
  "finished_at": "",
  "duration_seconds": 0,
  "exit": {"exit_code": 0, "signal": null, "timed_out": false},
  "raw_log_ref": {},
  "metric_output_ref": {},
  "metrics": {},
  "artifacts": [],
  "actual_budget": {},
  "failure": null,
  "recovery": {},
  "created_at": ""
}
```

The record describes one attempt. It is immutable after terminal finalization except that the root may later copy it into a new record with `run_status=stale` and an explicit invalidation reason.

Run records are read from `external_executor/raw_results/` through their request/checkpoint references. `cwd` identifies the executed deployment under `external_executor/expr/`; raw-result refs identify the durable evidence under `external_executor/raw_results/`.

## Artifact reference

```json
{
  "artifact_id": "",
  "path": "workspace-relative/path",
  "sha256": "",
  "size_bytes": 0,
  "producer": "experiment-run",
  "created_at": "",
  "evidence_level": "raw_result"
}
```

Directory outputs are expanded into regular-file references. Symbolic links resolving outside the workspace are rejected.

## Checkpoint

```json
{
  "schema_version": "external_executor_run_checkpoint.v1",
  "checkpoint_id": "RUN-...:terminal",
  "iteration_id": "",
  "run_id": "",
  "input_fingerprint": "",
  "status": "complete | partial | blocked",
  "run_record": {},
  "artifact_refs": [],
  "manifest_entries": [],
  "actual_budget": {},
  "blocking_issues": [],
  "recovery": {},
  "root_updates": {
    "register_manifest_entries": true,
    "account_budget": true,
    "recommended_next_action": "result-diagnosis | research-execution"
  },
  "created_at": ""
}
```

`status=complete` means the run record is valid and completed, not that the iteration or external executor is complete.

## Result-pack write

`apply_run_checkpoint.py` upserts the record by `run_id` into:

```text
result_pack.experiment_runs
```

It accepts either an array or a section envelope with `items`. It preserves all unrelated result-pack keys and never writes executor status, global budget, diagnosis, or claims.

## Validation invariants

All terminal records require:

- known schema and enums;
- matching request fingerprint;
- exact reviewed command and protocol;
- start and finish times in order;
- raw log reference with valid checksum;
- dependency references with valid checksums;
- actual budget with one consumed attempt;
- explicit recovery data for non-complete status.

Completed records additionally require:

- exit code zero and no timeout;
- config and metric-output references with valid checksums;
- structured metrics object;
- every declared output that the request requires.

Formal pre-audit candidates additionally require:

- real data;
- confirmatory role;
- formal review approval;
- code, dataset/resource, and evaluator/metric dependency kinds;
- dataset ID/version/split;
- seed/repeat;
- environment and hardware fingerprints;
- complete formal provenance.

Smoke, small-scale, toy, synthetic, dry-run, diagnostic, failed, cancelled, unusable, and stale records must never use `evidence_use=pre_audit_candidate`.
