# Drift and Escalation

## Purpose

Implementation may reveal that the approved design is incomplete or infeasible. Discovery does not grant authority. Classify the impact and stop before material change.

## Contribution drift

```text
none
  Direct realization of the approved specification; no semantic contribution change.
minor
  Non-central clarification or engineering realization that should be documented but does not replace the core mechanism or contribution type.
major
  Core mechanism, hypothesis, task, benchmark, contribution type, or novelty-relevant method identity changes.
```

`major` is blocking and requires root/human review.

## Protocol impact

```text
none
  No protocol-facing behavior changes.
nonmaterial
  Interface or logging change that preserves dataset, split, preprocessing, metric, seed/repeat, budget, and evaluation semantics.
material
  Changes any claim-critical protocol dimension or makes prior results non-comparable.
```

`material` is blocking until the experiment plan is versioned and reviewed.

## Fairness impact

```text
none
  No comparison-facing difference.
controlled
  Shared or explicitly balanced change whose effect is visible and reviewable.
uncertain
  Potentially asymmetric effect requiring Reviewer analysis.
material
  Extra data/pretraining, asymmetric capacity/budget/tuning, changed baseline semantics, or other direct fairness violation.
```

`material` is blocking. `uncertain` cannot become `ready_for_review` unless explicitly represented as a review risk and all implementation work remains within scope.

## Baseline reproduction impact

```text
none
  Baseline code/resource/protocol dependency unchanged.
adapter_only
  Adapter/logging/interface change that should be reviewed but is not expected to alter baseline semantics.
invalidates_selected
  One or more existing reproduction fingerprints are no longer valid.
invalidates_all
  Shared data, metric, split, environment, or protocol change invalidates all relevant baseline reproductions.
```

The Builder records affected reproduction IDs. The root decides staleness and rerouting.

## Escalation triggers

Stop and report when implementation requires:

- a new research module not in the spec;
- replacement or substantial redesign of a must-preserve module;
- changing objective/loss semantics beyond the spec;
- changing task, benchmark, dataset, split, preprocessing, metric, aggregation, or success rule;
- dropping/replacing a required baseline;
- extra data, pretraining, capacity, compute, or tuning budget;
- a new dependency with network/system/install implications;
- editing a protected path;
- bypassing a license/security/access restriction;
- weakening a test or acceptance criterion;
- converting ours into an existing baseline variant;
- changing contribution type or novelty identity.

## Scope-change record

The implementation report may propose, but not approve:

```json
{
  "request_id": "SCOPE-...",
  "status": "proposed",
  "trigger": "",
  "current_contract_refs": [],
  "proposed_change": "",
  "why_required": "",
  "alternatives_considered": [],
  "contribution_drift": "major",
  "protocol_impact": "material",
  "fairness_impact": "uncertain|material",
  "affected_claims": [],
  "affected_experiments": [],
  "affected_baseline_reproductions": [],
  "required_action": "human_review|replan|rerun_novelty",
  "evidence_refs": []
}
```

The root owns the official `scope_change_requests` section.
