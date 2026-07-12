# Ablation and Intervention Analysis

## Comparison key

Pair reference and intervention only when these agree:

```text
method family
protocol fingerprint
dataset/version
split
preprocessing fingerprint
setting/subset
metric name/direction/aggregation
seed/repeat
fairness fingerprint
analysis evidence class
```

Do not average across incompatible surfaces.

## Direction-adjusted effect

Let `reference` be the module-enabled/full condition and `intervention` the module-disabled or altered condition.

For a higher-is-better metric:

```text
effect = reference - intervention
```

For a lower-is-better metric:

```text
effect = intervention - reference
```

Thus positive effect means preserving/enabling the module helped under the tested intervention.

## Required summaries

Per module, metric and setting:

- paired `n` and coverage;
- mean and median effect;
- sample standard deviation and MAD when defined;
- min/max;
- positive/neutral/negative pair counts;
- sign consistency;
- practical threshold status if predeclared;
- reference/intervention run and evidence IDs.

## Interpretation

Suggested empirical status:

- `beneficial`: direction-adjusted effect is consistently positive and practically meaningful under valid controls;
- `neutral`: effects are within a declared neutral band or consistently negligible;
- `harmful`: preserving the module consistently worsens the metric;
- `mixed`: sign or magnitude materially changes across seeds/settings;
- `unsupported`: insufficient or invalid evidence.

The generic script should not invent a neutral band or statistical test. Use plan-declared thresholds when available; otherwise report descriptive effects.

## Broken ablation check

An ablation may be invalid when it:

- breaks tensor shapes or interfaces;
- changes optimization length or convergence criteria;
- removes parameters without a capacity control;
- changes preprocessing, data access or metric implementation;
- disables several modules at once;
- causes NaN/Inf or unrelated training collapse.

Such evidence is an engineering failure or confounded intervention, not proof that the module is beneficial.
