# Anomaly and Confound Taxonomy

## Anomaly record

```json
{
  "anomaly_id": "ANOM-...",
  "category": "high_variance",
  "severity": "info|warning|material|blocking",
  "scope": {"run_ids": [], "experiment_ids": [], "claim_ids": []},
  "description": "",
  "evidence_refs": [],
  "automatic": true,
  "status": "open|explained|controlled|resolved",
  "claim_risk": "none|low|medium|high|blocking"
}
```

## Categories

### Evidence integrity

```text
missing_metric
nonfinite_metric
missing_raw_log
missing_metric_output
missing_config
missing_code_version
missing_environment
missing_seed
stale_or_unusable_run
artifact_reference_missing
```

### Protocol and fairness

```text
mixed_protocol_fingerprint
mixed_split
mixed_metric_direction
mixed_metric_aggregation
mixed_preprocessing
mixed_run_type
fairness_fingerprint_mismatch
review_approval_mismatch
extra_data_or_pretraining
compute_budget_imbalance
tuning_budget_imbalance
early_stopping_imbalance
possible_leakage
possible_test_set_tuning
```

### Statistical/stability

```text
insufficient_repeats
seed_imbalance
high_variance
extreme_outlier
unstable_ranking
suspicious_identical_values
large_missingness
selected_seed_risk
```

### Coverage

```text
missing_required_baseline
missing_required_experiment
missing_confirmatory_run
incomplete_setting_coverage
claim_without_experiment
```

### Execution

```text
failed_run_cluster
timeout_cluster
oom_cluster
metric_extraction_failure
planned_executed_role_mismatch
```

## Confound families

Review each material comparison for:

```text
capacity
parameter_count
training_compute
inference_compute
extra_data
pretraining
hyperparameter_search
early_stopping
optimizer_or_schedule
data_preprocessing
augmentation
split_or_sampling
metric_implementation
implementation_quality
baseline_reproduction_fidelity
hardware_or_precision
random_seed
missingness_or_cherry_picking
```

Confound status:

```text
ruled_out
controlled
plausible
likely
blocking
unknown
not_applicable
```

A confound is not “ruled out” merely because it was not mentioned in logs.

## Severity guidance

- `info`: useful observation with no current claim effect;
- `warning`: interpretation should mention it;
- `material`: limits attribution or claim strength;
- `blocking`: invalidates the central comparison or metric identity.
