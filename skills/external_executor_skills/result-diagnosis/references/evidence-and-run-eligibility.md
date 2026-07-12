# Evidence and Run Eligibility

## Evidence classes

| Class | Meaning | Permitted use |
| --- | --- | --- |
| `formal_candidate` | completed formal/ablation/robustness/efficiency run with minimum provenance and approval | descriptive formal comparison and pre-audit claim implication |
| `small_scale` | completed small-scale run | provisional direction, stability and feasibility only |
| `diagnostic` | completed diagnostic/exploratory run | anomaly investigation and hypothesis formation |
| `engineering` | smoke, toy, synthetic or engineering-only run | execution health only |
| `excluded` | failed, stale, cancelled, unusable, malformed or materially incomparable | negative history and blocker evidence only |

`formal_candidate` is not `audited_result`. T7 still owns audit.

## Minimum run identity

Every included run needs:

```text
run_id
iteration_id
experiment_id
method_id or baseline_id
method_role = ours | baseline | ablation | diagnostic | other
run_type
status
metric record or metric output reference
protocol fingerprint or explicit diagnostic exemption
setting/dataset/split identity
```

## Minimum formal provenance

A formal candidate additionally needs:

- code/patch version;
- config reference;
- dataset/version and split;
- seed or repeat ID;
- metric name, direction and aggregation;
- raw log reference;
- machine-readable metric output reference;
- environment/hardware reference or record;
- review approval compatible with formal execution;
- resource/baseline version when relevant.

Missing minimum formal provenance downgrades or excludes the run. It never becomes a positive formal result by narrative explanation.

## Terminal status

Terminal statuses:

```text
completed
failed
cancelled
stale
unusable
```

A `running` or `planned` record is not diagnosis evidence except as a missing-work note.

## Exclusion reasons

Use structured reasons:

```text
non_terminal
failed_run
cancelled_run
stale_run
unusable_run
missing_metric
nonfinite_metric
missing_metric_direction
missing_method_identity
missing_protocol_fingerprint
missing_formal_provenance
review_not_approved
protocol_mismatch
fairness_mismatch
unknown_run_type
```

## Negative evidence

Failed and unusable runs remain in the snapshot. They may support statements about engineering instability, budget loss or evidence incompleteness, but not claims about comparative method quality unless the failure itself is a planned scientific outcome and the protocol explicitly defines it.

## Required baseline coverage

A missing required baseline creates:

- `missing_required_baseline` anomaly;
- an unresolved or weakened relevant claim;
- at least a partial gate;
- a blocker when the central comparison cannot be made.

Absence is never scored as a loss by the baseline or a win by ours.
