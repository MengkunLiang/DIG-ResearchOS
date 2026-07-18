# Run Record Contract

Every substantive baseline execution produces one immutable run record.

```json
{
  "schema_version": "baseline_run_record.v1",
  "run_id": "RUN-...",
  "reproduction_id": "REPRO-...",
  "baseline_id": "BASE-...",
  "candidate_id": "CAND-...",
  "attempt": 1,
  "status": "completed|failed|timed_out|cancelled|blocked",
  "started_at": "",
  "finished_at": "",
  "duration_seconds": 0,
  "argv": [],
  "command_display": "",
  "working_directory": "",
  "deployment_dir": "",
  "result_dir": "",
  "evidence_dir": "",
  "exit_code": null,
  "termination_signal": null,
  "timeout_seconds": 0,
  "resource_limits": {},
  "resource_usage": {},
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "source_manifest_sha256": "",
  "config_manifest_sha256": "",
  "dataset": {},
  "seeds": [],
  "repeats": 1,
  "stdout_path": "",
  "stderr_path": "",
  "environment_path": "",
  "expected_outputs": [],
  "output_checks": [],
  "produced_artifacts": [],
  "repository_content_executed": true,
  "notes": []
}
```

## Required provenance for comparability

- immutable source identity or package checksum;
- exact command vector and working directory;
- deployment directory under `external_executor/expr/baselines/`, result directory for raw baseline outputs/logs under `external_executor/raw_results/`, and evidence directory under `external_executor/report/baseline_reproduction/`;
- config file hashes and parameter snapshot;
- dataset identity, version, split, preprocessing, and checksum where available;
- seed/repeat policy and actual values;
- metric implementation/extractor and aggregation;
- stdout/stderr logs and original baseline outputs under raw results, plus machine-readable metric report and per-dataset/per-metric raw metric CSV files;
- environment and hardware record;
- start/end/status/exit information;
- protocol and fairness fingerprints;
- hashes of produced evidence files.

Missing fields may still support engineering diagnosis but cannot support formal comparison.

## Supersession

A corrected attempt links to the prior run ID and repair record. The older run remains present with its original status and checksums. Never rewrite a run record after review; append a correction note or create a new run.
