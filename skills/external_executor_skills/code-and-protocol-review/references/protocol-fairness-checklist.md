# Protocol Fairness Checklist

## Normalized comparison input

Use this shape with `compare_protocols.py`:

```json
{
  "comparison_id": "",
  "input_fingerprint": "",
  "baseline": {
    "variant_id": "",
    "dataset_id": "",
    "dataset_version": "",
    "split_fingerprint": "",
    "preprocessing_fingerprint": "",
    "metric_name": "",
    "metric_direction": "maximize | minimize",
    "evaluation_fingerprint": "",
    "seed_policy": "",
    "repeat_count": 0,
    "tuning_budget": {},
    "training_budget": {},
    "extra_data": [],
    "pretrained_source": "none | source",
    "checkpoint_selection": ""
  },
  "ours": {},
  "allowed_differences": [
    {"field": "training_budget", "rationale": "Explicit fairness rationale"}
  ]
}
```

`ours` uses the same fields as `baseline`. Dataset, split, preprocessing, metric, direction, and evaluation differences are never silently allowed. The comparison script reports candidates; the Reviewer decides whether a disclosed noncritical difference is scientifically acceptable.

## Dataset and split

- Same dataset identity/version and declared eligibility rules.
- Same train/validation/test split or an explicitly justified official protocol.
- Same deduplication, filtering, preprocessing, feature construction, and missing-value handling.
- No train/test overlap, target leakage, evaluation-time fit, or subset cherry-picking.
- Toy/synthetic/small-scale data cannot support formal benchmark claims.

## Metrics and evaluation

- Same metric definition, direction, averaging, units, thresholds, and tie handling.
- Same evaluation script/version and postprocessing.
- Primary versus secondary metrics are locked before formal results.
- No best-checkpoint selection on test data.
- Failed/missing examples are handled identically.

## Randomness and tuning

- Seed/repeat policy is declared and comparable.
- Hyperparameter search budget and early stopping are comparable or transparently constrained.
- No seed cherry-picking or selective failure removal.
- Validation data, selection criterion, and reporting aggregation are the same.

## Compute, data, and pretrained advantage

- Record parameter count, training steps, batch size, optimizer/scheduler, hardware, wall time, and memory when relevant.
- Extra data, augmentation, pretrained checkpoints, external tools, and retrieval sources are disclosed.
- A method-specific necessary component is separated from a generic engineering advantage.
- Baseline adapters do not silently weaken or alter the baseline mechanism.

## Method and ablation

- Each claimed module exists and is wired to the actual training/inference path.
- Ablation switches remove only the intended mechanism and preserve comparable capacity/compute where possible.
- “Without module” does not accidentally change preprocessing, data, metric, or training budget.
- Candidate or unsupported modules are not presented as validated contributions.

## Logging and provenance

- Command, config, seed/repeat, split, code/patch, resource version, environment, hardware, raw log, and metric output are capturable.
- Logs identify baseline/variant and experiment ID unambiguously.
- Errors, retries, excluded runs, and failed trials are retained.
- Formal outputs cannot be confused with smoke, dry-run, or diagnostic artifacts.

## Leakage red flags

Investigate, do not automatically convict:

- preprocessing fit on combined or test data;
- using test metric for early stopping, checkpoint selection, or hyperparameter tuning;
- label-derived features available only after prediction time;
- duplicates or near-duplicates across splits;
- caching keyed incorrectly across split/seed/variant;
- normalization/statistics computed from the full dataset;
- benchmark test annotations read by training or prompt construction.
