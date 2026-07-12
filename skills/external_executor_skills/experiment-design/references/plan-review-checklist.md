# Experiment Plan Review Checklist

Review the plan independently from the planner's prose summary. Inspect the actual claim matrix, protocol snapshot, approved resources, experiment records, DAG, and budget.

## Claim coverage

- Every required claim is planned or explicitly unsupported.
- Every confirmatory experiment maps to a claim and reviewer question.
- No experiment exists only to create a conventional table.
- Claim boundaries and must-not-claim rules are propagated.

## Protocol completeness

- Dataset identity/version/split and preprocessing are fixed.
- Primary metric, direction, aggregation, and selection rule are fixed.
- Baseline identity/config/resource and fairness constraints are fixed.
- Seed/repeat, evaluation, statistics, failure handling, and tuning fairness are fixed.
- Formal experiments use one protocol fingerprint.

## Baseline and resource binding

- Every required baseline plan binds an approved Phase B candidate.
- Approximate or reimplemented baselines retain their labels and claim risks.
- Dataset, license, access, security, and material gaps are propagated.
- No unavailable baseline is silently omitted or replaced.

## Experiment validity

- Confirmatory, diagnostic, and exploratory roles are correct.
- Smoke/small-scale work is not treated as formal evidence.
- Main comparisons are fair and current enough for the claim.
- Ablations name a mechanism, intervention, controls, and confounds.
- Robustness/failure/efficiency experiments answer claim-linked questions.
- Positive, negative, and inconclusive interpretations are predeclared.

## Anti-post-hoc safeguards

- No outcome-dependent metric, seed, subset, threshold, or baseline selection.
- Failed and unfavorable runs remain in the record.
- Exploratory results require a new versioned confirmatory test before claim promotion.
- Material protocol changes produce a new fingerprint and evidence impact record.

## DAG and budget

- Experiment IDs and dependencies are valid and acyclic.
- Parallel groups have no internal dependency or resource conflict.
- Required evidence is prioritized before optional evidence.
- Estimated plan cost fits declared limits, including rerun reserve.
- Early-stop rules are predeclared and cannot selectively favor outcomes.

## Verdicts

- `pass`: protocol, claims, resources, DAG, and budget satisfy the minimum loop.
- `needs_fix`: correctable omissions or inconsistencies prevent approval.
- `blocked`: missing authority/resource, infeasible budget, unsupported required claim, or material scope conflict requires root/human action.
