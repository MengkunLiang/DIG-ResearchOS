# Evidence and Claim Boundary

## Claim candidate, not claim

A handoff claim candidate is a structured question plus evidence map. It is not manuscript text and has `audit_status=pending_T7`.

## Evidence ceilings

- `formal_candidate`: at least one formal candidate exists with required provenance, but audit is pending.
- `diagnostic_only`: only diagnostic/small-scale/ablation evidence is available.
- `method_definition_only`: the statement concerns implemented method structure, not empirical effectiveness.
- `unsupported`: no usable evidence supports the candidate.

The ceiling is a maximum pre-audit status, not a promise that T7 will accept it.

## Counterevidence

Include failed or contrary runs, instability, subgroup failures, weaker settings, unavailable baselines, fairness/protocol concerns, and diagnosis/attribution counterevidence. Do not select only favorable runs.

## Must-not-claim propagation

Collect boundaries from the T5 handoff, novelty audit-derived context, iteration decisions, diagnosis, attribution, evidence package, unavailable baselines, approximations, replacements, and integrity failures. Each item should contain a reason and source references.

## Forbidden promotions

The report must not contain authority fields or equivalent prose that asserts `audited`, `accepted`, `proven`, `final_claim`, `paper_ready`, `T8_ready`, `SOTA`, or causal mechanism proof without T7 closure.
