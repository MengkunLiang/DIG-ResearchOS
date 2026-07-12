# Metric and Statistics Policy

## Comparability key

Aggregate or compare only when all claim-relevant dimensions agree:

```text
protocol fingerprint
dataset and version
split
preprocessing/evaluation identity
metric name, direction and aggregation
setting/subset
run type and evidence class
fairness fingerprint when present
```

A diagnostic run may intentionally differ; keep it in a separate group.

## Descriptive summaries

For each comparable group report:

- `n`;
- values and seed/repeat IDs;
- mean and median;
- sample standard deviation when `n >= 2`;
- median absolute deviation;
- min/max/range;
- standard error and normal-approximation 95% interval when `n >= 2`, clearly labeled descriptive rather than guaranteed inferential coverage.

Do not hide the raw values behind an aggregate.

## Metric direction

Normalize ranking with:

```text
higher_is_better -> utility = value
lower_is_better  -> utility = -value
```

Store original values and direction. Do not rewrite a loss as a positive score in user-facing evidence without retaining the original metric.

## Paired comparisons

Prefer paired seeds/repeats when ours and a baseline share seed IDs under the same setting. Report:

- number and IDs of paired seeds;
- direction-adjusted per-pair differences;
- mean/median difference;
- win/tie/loss counts;
- sample SD of paired differences;
- paired standardized difference when defined.

An unpaired mean difference may be reported descriptively but must state that pairing is unavailable.

## Practical magnitude

When the experiment plan defines an absolute/relative minimum effect or equivalence margin, evaluate it exactly. Otherwise report the measured delta without inventing a “meaningful” threshold.

Separate:

- numerical direction;
- practical magnitude;
- repeat stability;
- inferential/statistical support.

## Statistical tests

This generic Skill uses the Python standard library and does not automatically choose a hypothesis test. A project-specific plan may authorize an existing statistics command or produce test artifacts elsewhere. Do not fabricate p-values.

Multiple datasets, repeated measures and multiple comparisons require a plan-aware procedure. Naive independent t-tests across all pairs are not an acceptable default.

## Small samples

- `n=1`: point observation only;
- `n=2`: minimal repeat information, very low inferential confidence;
- `n>=3`: descriptive variance becomes useful, but adequacy depends on the protocol;
- required seed/repeat count comes from the experiment plan, not this Skill.
