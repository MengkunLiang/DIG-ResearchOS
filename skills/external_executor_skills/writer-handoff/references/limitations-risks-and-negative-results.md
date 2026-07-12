# Limitations, Risks, and Negative Results

## Propagate, do not summarize away

The handoff must preserve:

- required baseline unavailable or approximate;
- replacement/reimplementation status;
- incomplete repeats or seed coverage;
- protocol/fairness uncertainty;
- unstable, failed, stale, cancelled, and unusable runs;
- settings where ours loses or fails;
- unsupported mechanisms and interaction questions;
- missing or blocked figures/tables;
- contribution drift, scope review, security/license constraints;
- recovery actions still possible.

## Coverage rule

Every upstream risk or blocker with an ID must appear in `open_risks`, `limitations`, `must_not_claim`, or an explicit `resolved_or_not_applicable` record with justification and evidence.

## Negative result roles

Negative evidence may narrow a claim, contradict a mechanism, identify a failure mode, or justify stopping. It must not be excluded merely because it weakens the story.

## Recovery notes

Recovery notes identify the earliest invalid/missing prerequisite and affected downstream artifacts. They are operational guidance, not promises of success.
