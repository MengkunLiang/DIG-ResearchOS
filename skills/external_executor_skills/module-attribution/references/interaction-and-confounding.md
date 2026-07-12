# Interaction and Confounding

## Pairwise interaction

For two modules A and B, a valid factorial design contains:

```text
full:          A=1, B=1
without A:     A=0, B=1
without B:     A=1, B=0
without A+B:   A=0, B=0
```

Using direction-normalized metric values, the interaction is:

```text
interaction = full - without_A - without_B + without_A_B
```

Compute it per matched seed/setting, then summarize. Positive values indicate synergy under this coding; negative values indicate redundancy or antagonism. Interpretation remains local to the tested design.

Two single-module ablations without the joint condition cannot identify interaction.

## Confound families

```text
capacity
compute
memory
pretraining
extra_data
preprocessing
optimization
hyperparameter_budget
early_stopping
checkpoint_selection
metric_or_split
seed_imbalance
intervention_integrity
multi_function_switch
subset_selection
leakage
multiple_comparisons
baseline_nonequivalence
other
```

## Severity

- `blocking`: invalidates the claimed effect or intervention identity.
- `material`: may explain a substantial part of the observed effect.
- `moderate`: limits confidence or transfer.
- `minor`: documented but unlikely to change the conclusion.

## Controls

Examples:

- parameter-matched replacement;
- compute-matched training;
- shared data/preprocessing/evaluation;
- paired seeds;
- sham switch or adapter control;
- restored module diagnostic;
- factorial interaction run;
- subset defined before seeing results.

Absence of a control should be recorded, not retroactively assumed harmless.
