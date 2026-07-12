# Run Request Contract

Use this contract to materialize and validate one immutable execution attempt.

## Contents

- [Required shape](#required-shape)
- [Identity rules](#identity-rules)
- [Plan lookup](#plan-lookup)
- [Execution-level mapping](#execution-level-mapping)
- [Output rules](#output-rules)

## Required shape

```json
{
  "schema_version": "external_executor_run_request.v1",
  "run_id": "RUN-...",
  "experiment_id": "EXP-...",
  "iteration_id": "iter-...",
  "run_type": "smoke | small_scale | formal | ablation | robustness | diagnostic | efficiency",
  "execution_level": "smoke | small_scale | formal",
  "analysis_role": "confirmatory | diagnostic | exploratory",
  "command": ["python", "train.py", "--config", "..."],
  "cwd": "external_executor/workdir/...",
  "timeout_seconds": 3600,
  "experiment_plan_ref": "external_executor/.../experiment_plan.json",
  "iteration_plan_ref": "external_executor/.../iteration_plan.json",
  "review_ref": "external_executor/.../review_report.json",
  "review_id": "REV-...",
  "input_fingerprint": "sha256",
  "protocol_fingerprint": "...",
  "config_ref": "external_executor/configs/...json",
  "raw_log_path": "external_executor/logs/...log",
  "metric_output_path": "external_executor/raw_results/...metrics.json",
  "run_record_path": "external_executor/runs/...record.json",
  "checkpoint_path": "external_executor/runs/...checkpoint.json",
  "declared_outputs": ["external_executor/raw_results/..."],
  "dependencies": [
    {
      "kind": "code | config | dataset | resource | metric | evaluator | checkpoint",
      "path": "external_executor/...",
      "sha256": "..."
    }
  ],
  "dataset": {"id": "", "version": "", "split": ""},
  "seed": 0,
  "repeat_index": 0,
  "resources": {"gpu_count": 0, "cpu_count": 1, "memory_gb": 1},
  "budget": {
    "remaining": {"runs": 1, "wall_clock_seconds": 3600, "gpu_hours": 0, "cost": 0},
    "estimated": {"runs": 1, "wall_clock_seconds": 60, "gpu_hours": 0, "cost": 0}
  },
  "environment": {
    "allowed_env": [],
    "overrides": {},
    "network_required": false
  },
  "isolation": {
    "filesystem": "enforced | not_enforced | unknown",
    "network": "enforced | authorized | unknown",
    "evidence_ref": ""
  },
  "data_kind": "real | toy | synthetic | dry_run"
}
```

## Identity rules

- `run_id` identifies one immutable attempt, not a logical experiment across retries.
- `experiment_id` must resolve in the current experiment plan.
- `iteration_id` must match the root dispatch and current iteration plan.
- `review_id`, `input_fingerprint`, and `protocol_fingerprint` must match the current review.
- `command` is an argument vector. Shell interpreters with command-string flags, redirection, pipes, and implicit interpolation are forbidden.
- All paths are workspace-relative and must resolve under allowed paths.
- Dependency hashes are calculated before launch. A mismatch blocks execution.

## Plan lookup

The experiment plan may expose experiments under `experiments` or `items`. The matching entry must agree on any fields it declares among:

```text
experiment_id
run_type
analysis_role
protocol_fingerprint
dataset.id
dataset.version
dataset.split
seed
repeat_index
```

Missing optional fields do not create authority. A conflicting declared field blocks.

## Execution-level mapping

For the three level-named run types:

```text
run_type=smoke      -> execution_level=smoke
run_type=small_scale -> execution_level=small_scale
run_type=formal     -> execution_level=formal
```

`ablation`, `robustness`, `diagnostic`, and `efficiency` must declare an execution level independently. A formal-scale diagnostic still requires formal approval; a small-scale ablation remains small-scale evidence.

## Output rules

- The raw log, metric output, run record, and checkpoint must be distinct regular-file paths.
- The config must exist before launch.
- Declared outputs may be files or directories, but the run record fingerprints only regular files.
- Formal requests require at least one `code`, one `resource` or `dataset`, and one `evaluator` or `metric` dependency.
- A credential value must never appear in the request. Name authorized injected variables in `allowed_env`; values remain outside the artifact.
