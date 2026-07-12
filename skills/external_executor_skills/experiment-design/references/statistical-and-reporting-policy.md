# Statistical and Reporting Policy

## Primary and secondary metrics

Name the primary metric before formal execution. Record direction, unit, aggregation, and model-selection rule. Secondary metrics cannot replace the primary metric after results are observed.

When multiple primary metrics are genuinely required, predeclare how conflicting outcomes affect the claim.

## Seeds and repeats

Declare seed values or a deterministic seed-generation rule, count, repeats, and aggregation. Never choose the best seed for the main result. Preserve all planned completed runs and all failed-run records.

A run may be excluded only by a predeclared technical-validity rule, with the reason and raw evidence retained.

## Uncertainty

Select a method appropriate to the design and available repeats, such as standard deviation, standard error, confidence interval, bootstrap interval, or paired analysis. Do not demand significance testing by default; do not report significance without a valid design and assumptions.

Record:

- unit of replication;
- pairing or independence;
- aggregation level;
- interval/test method;
- multiple-comparison handling when relevant;
- practical effect-size policy.

## Hyperparameters and tuning fairness

Declare:

- tuning split;
- search space and algorithm;
- trial budget per method;
- whether published/default configurations are used;
- stopping and model-selection rules;
- whether baseline and ours receive comparable tuning opportunity.

A larger tuning budget for ours is a fairness risk unless the claim and protocol explicitly account for it.

## Missing and failed runs

Default rules:

- do not impute missing formal results;
- retain failed-run evidence;
- distinguish engineering failures from valid but poor outcomes;
- do not rerun only unfavorable seeds;
- reruns use the same predeclared policy or a new protocol version.

## Confirmatory interpretation

Before execution, define what each result means:

- positive: which claim and boundary it supports;
- negative: whether it weakens, refutes, or narrows the claim;
- inconclusive: which uncertainty, power, fidelity, or variance issue prevents a conclusion.

Avoid hard success thresholds when the project has not authorized them. Never invent expected effect sizes.
