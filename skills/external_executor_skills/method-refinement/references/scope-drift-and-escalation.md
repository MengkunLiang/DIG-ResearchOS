# Scope Drift and Escalation

## Drift levels

- `none`: specification implements the approved intent without semantic change.
- `minor`: the same intent is refined, but plan, claim constraint, or reviewer attention may be required.
- `major`: central scientific scope, contribution, mechanism, benchmark, or claim boundary changes.

## Mandatory major triggers

Treat as major when any of these is true:

1. central hypothesis differs materially;
2. contribution type changes;
3. core mechanism is replaced or its causal story changes;
4. a required/must-preserve component is removed, bypassed, or made non-functional;
5. task, benchmark, primary dataset role, or protocol meaning changes;
6. a required baseline or fairness constraint is bypassed through method design;
7. a new core module becomes the real contribution without prior approval;
8. the claim boundary is broadened;
9. the method converges to a known baseline/variant that changes novelty;
10. a root decision authorizes only a minor change but the spec contains a larger one.

## Scope-change request

```json
{
  "request_id": "scope-change-...",
  "status": "pending_human_review",
  "created_by": "method-refinement",
  "iteration_id": "",
  "trigger": "",
  "proposed_change": "",
  "current_scope": {},
  "proposed_scope": {},
  "why_needed": "",
  "evidence_refs": [],
  "affected_claims": [],
  "affected_baselines": [],
  "affected_experiments": [],
  "novelty_risk": "",
  "fairness_risk": "",
  "budget_impact": "",
  "alternatives_within_scope": [],
  "requested_decision": "approve | reject | narrow | return_to_T4_5",
  "implementation_must_pause": true
}
```

## Approval semantics

The child Skill never marks a scope request approved. `research-execution` records the human/root decision and creates a new iteration plan. Only then may this Skill create a new specification under the changed scope.

## Minor claim effects

A minor change may still require:

- a plan version update;
- a new ablation or diagnostic;
- claim narrowing;
- stale marking for affected prior results;
- post-novelty review if the realized contribution changes.

The root owns those routes.
