# Confidence and Causality

## Confidence

Use:

```text
high
medium
low
insufficient
```

Confidence considers:

- evidence class;
- intervention integrity;
- paired coverage and repeats;
- effect consistency and magnitude;
- protocol/fairness comparability;
- counterevidence;
- unresolved confounds;
- setting breadth.

Confidence is not the same as effect size.

## Causal status

Use one of:

```text
local_intervention_effect
mechanism_consistent
correlational_only
implementation_only
unsupported
```

### `local_intervention_effect`

Allowed only for valid direct ablation or controlled intervention. Wording must remain bounded to the tested intervention, metric and setting.

### `mechanism_consistent`

The outcome agrees with the proposed mechanism, but alternative pathways remain. Do not write “proves,” “causes,” or “is responsible for.”

### `correlational_only`

Association without controlled manipulation.

### `implementation_only`

The module is present/wired but has no empirical attribution.

### `unsupported`

No valid inference.

## Mechanism support

A module's local intervention effect does not automatically establish its intended mechanism. Stronger mechanism attribution generally needs:

- a discriminating intervention or mediator manipulation;
- predicted pattern across settings/subsets;
- negative control or alternative-explanation control;
- repeat stability;
- counterevidence assessment.

## Forbidden upgrades

Do not upgrade:

- one seed to stable effect;
- small-scale to formal support;
- subset correlation to causal mechanism;
- code presence to contribution;
- a multi-function switch to one mechanism;
- a joint package effect to unique module effects;
- pre-audit attribution to paper-approved claim.
