# Failure and Repair Taxonomy

## Failure categories

Use one primary category and optional secondary categories.

| Category | Typical evidence | Default action |
| --- | --- | --- |
| `environment_issue` | missing runtime/library, incompatible ABI, unsupported device | repair or rerun |
| `obsolete_code` | removed API, old framework semantics, Python incompatibility | bounded compatibility repair |
| `dataset_unavailable` | missing/forbidden/inaccessible data or split | mark unavailable or block |
| `config_ambiguous` | required hyperparameter/default not recoverable | human review or conditional reproduction |
| `metric_protocol_mismatch` | wrong metric, direction, averaging, split, evaluator | repair if locked intent is clear; otherwise block |
| `baseline_not_applicable` | baseline cannot operate on confirmed task/interface without mechanism change | request replacement/scope review |
| `resource_incomplete` | missing checkpoint, preprocessing, label map, submodule, file | return resource gap |
| `security_license_block` | unsafe execution, prohibited license/access, required secret/network escalation | block execution |
| `out_of_memory` | OOM/killed allocation | bounded resource/config repair if fair |
| `timeout` | process exceeded authorized duration | rerun/scale only under budget/fairness approval |
| `numerical_instability` | NaN/Inf/divergence/overflow | inspect config/data; bounded repair |
| `runtime_error` | code exception not fitting a more specific category | repair |
| `metric_missing` | run completes but required metric/output absent | logging/metric extraction repair |
| `result_mismatch` | valid run but outside reference/sanity rule | preserve result; inspect fidelity/variance |
| `unknown` | evidence insufficient | needs review |

## Repair classes

Ordinary, when authorized:

- `environment_compatibility`: pin/adjust versions without changing algorithm semantics;
- `path_adapter`: point the baseline to the approved dataset/checkpoint/output path;
- `config_adapter`: translate locked protocol values into repository configuration syntax;
- `seed_plumbing`: expose and record the locked seed;
- `logging_repair`: persist already-computed outputs;
- `metric_extraction_repair`: parse the locked metric correctly;
- `device_compatibility`: CPU/GPU/backend compatibility without changing math materially;
- `interface_adapter`: convert input/output shape or naming without changing baseline mechanism.

Not ordinary repair; escalate:

- replacing algorithmic modules or losses;
- omitting, stubbing, weakening, or partially implementing required core modules or core design steps;
- using a different checkpoint/pretraining source;
- adding extra data or augmentation not allowed by protocol;
- changing dataset split, metric, aggregation, repeat count, or early stopping to favor the baseline;
- broad hyperparameter search beyond the locked budget;
- using a weaker/easier variant under the same baseline name;
- changing task or benchmark;
- bypassing security, license, or access controls.

## Repair record

```json
{
  "repair_id": "REPAIR-...",
  "reproduction_id": "REPRO-...",
  "from_attempt": 1,
  "to_attempt": 2,
  "failure_category": "",
  "repair_class": "",
  "changed_files": [],
  "patch_path": "",
  "rationale": "",
  "algorithm_impact": "none|minor|material|unknown",
  "protocol_impact": "none|minor|material|unknown",
  "fairness_impact": "none|minor|material|unknown",
  "approved_by_iteration_plan": true,
  "evidence_refs": []
}
```

Material or unknown algorithm/protocol/fairness impact prevents automatic rerun and requires root review.
