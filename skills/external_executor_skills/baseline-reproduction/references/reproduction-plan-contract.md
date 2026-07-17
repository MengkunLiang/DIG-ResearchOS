# Reproduction Plan Contract

## Envelope

```json
{
  "schema_version": "baseline_reproduction_plan.v1",
  "status": "complete|partial|blocked|stale",
  "generated_at": "",
  "input_fingerprint": "",
  "iteration_id": "",
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "items": []
}
```

## Plan item

```json
{
  "reproduction_id": "REPRO-...",
  "baseline_id": "BASE-...",
  "baseline_name": "",
  "required": true,
  "candidate_id": "CAND-...",
  "requirement_ids": [],
  "source": {
    "class": "official_author_repo|author_recognized|third_party_reproduction|executor_reimplementation|approximate_reproduction",
    "path": "resource/... or resources/...",
    "revision": "",
    "sha256": "",
    "resource_review_ids": []
  },
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "dataset": {
    "name": "",
    "version": "",
    "path": "",
    "split": "",
    "preprocessing": "",
    "checksum": ""
  },
  "metrics": [
    {
      "name": "",
      "primary": true,
      "direction": "higher|lower|target",
      "units": "",
      "aggregation": "mean|median|last|min|max|custom",
      "extractor": {
        "type": "json|jsonl|csv|regex",
        "path": "metrics.json",
        "selector": "metric.value",
        "pattern": null,
        "column": null
      },
      "reference": {
        "type": "absolute_tolerance|relative_tolerance|range|minimum|maximum|directional|none",
        "value": null,
        "lower": null,
        "upper": null,
        "tolerance": null,
        "source_refs": []
      }
    }
  ],
  "execution": {
    "authorized": false,
    "argv": [],
    "working_directory": ".",
    "allowed_executables": ["python", "python3"],
    "timeout_seconds": 3600,
    "memory_limit_mb": null,
    "cpu_time_limit_seconds": null,
    "expected_outputs": [],
    "allowed_env_names": [],
    "env_overrides": {},
    "network_required": false
  },
  "config": {
    "paths": [],
    "parameters": {},
    "seed_parameter": null,
    "repeat_parameter": null
  },
  "seeds": [],
  "repeats": 1,
  "repair_policy": {
    "max_attempts": 3,
    "allowed_classes": [
      "environment_compatibility",
      "path_adapter",
      "config_adapter",
      "seed_plumbing",
      "logging_repair",
      "metric_extraction_repair"
    ]
  },
  "claim_dependencies": [],
  "non_reproduction_consequence": "",
  "status": "planned|incomplete|blocked|stale",
  "blocking_issues": [],
  "notes": []
}
```

## Stable identity

`reproduction_id` derives from baseline ID, candidate ID, protocol fingerprint, fairness fingerprint, dataset/split, and config family. Changing one creates a new reproduction identity; changing only attempt number does not.

## Execution readiness

An item is executable only when:

- the candidate is approved for baseline reproduction;
- source path exists inside the approved resource pool: `resources/` for by-hand local material or `resource/` for acquired/reimplemented material;
- `external_executor/workdir/` and `external_executor/expr/` are not approved source pools;
- protocol/fairness fingerprints are non-empty;
- dataset/split and primary metric are explicit;
- argv is a non-empty array and `authorized=true`;
- the current root iteration plan authorizes the same baseline/action;
- no blocking issue remains.

A generated scaffold normally starts `incomplete` until project-specific command, config, metric extractor, and reference rules are completed.

## Execution and result locations

`working_directory` is resolved relative to the copied baseline deployment under:

```text
external_executor/expr/baseline_reproduction/<baseline-id>/<reproduction-id>/attempt-<N>/source/
```

All logs, run records, normalized metrics, environment records, and staged produced outputs must be written under the paired raw-result directory:

```text
external_executor/raw_results/baseline_reproduction/<baseline-id>/<reproduction-id>/attempt-<N>/
```
