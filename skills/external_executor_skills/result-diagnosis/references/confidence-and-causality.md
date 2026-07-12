# Confidence and Causality

## Confidence is not gate status

Confidence describes support for one finding. Gate status describes whether the overall diagnosis can proceed downstream.

Use:

- `high`: direct, complete, stable and provenance-rich evidence with no material unresolved anomaly;
- `medium`: consistent evidence with limited repeats or non-blocking constraints;
- `low`: weak, noisy, incomplete or partially confounded evidence;
- `insufficient`: no defensible interpretation beyond noting missing evidence.

## Interpretation levels

### `observed_fact`

Directly represented in deterministic artifacts, such as values, ranks, missing runs or variance.

### `descriptive_inference`

A bounded summary of multiple facts, such as “ours is consistently lower on the two recorded sparse settings.”

### `plausible_hypothesis`

A proposed explanation requiring controlled evidence, such as “the pattern may be associated with model capacity.”

### `unsupported`

A proposition not supported by current evidence. Preserve it only as a rejected or open idea.

## Causal boundary

Result diagnosis must not conclude:

- a module caused an improvement;
- a mechanism is validated;
- a training trick explains the gain;
- a baseline component is the reason for its strength;
- a data property causes the observed effect.

It may say:

- the pattern is consistent with a hypothesis;
- a confound is plausible;
- a controlled ablation/diagnostic is needed;
- the evidence does not distinguish between explanations.

`module-attribution` may later use direct ablation and controlled diagnostics, but it must still label evidence type.

## Language controls

Prefer:

```text
the recorded runs show
within this setting
under the current protocol
the result is consistent with
the evidence is insufficient to distinguish
pre-audit implication
```

Avoid:

```text
proves
demonstrates the mechanism
causes
always
universally
SOTA
statistically significant
```

unless an authorized, valid artifact directly supports that exact wording and the owning downstream audit permits it.
