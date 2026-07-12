# Method Intent Contract

## Role

`handoff_pack.json#method_intent` is the approved starting constraint for implementation. It is not the final method and is not evidence that a mechanism works. This Skill normalizes it without strengthening or rewriting its scientific meaning.

## Authority by field

| Field | Primary authority | Required cross-check |
| --- | --- | --- |
| Central hypothesis | confirmed execution scope / hypotheses source | handoff context reboost |
| Contribution type | novelty boundary and confirmed scope | method intent |
| Core mechanism | method intent | confirmed scope and hypotheses |
| Must-preserve components | method intent | context alignment provenance |
| Candidate/support components | method intent | experiment plan and resource constraints |
| Allowed refinements | method intent / approved root iteration decision | diagnosis or attribution evidence when applicable |
| Forbidden changes | method intent and root policy | novelty and scope boundary |
| Claim boundary | confirmed scope / novelty audit | experiment plan |
| Mechanism tests | method intent and claim-evidence matrix | experiment plan |

No single global file priority applies to every field. A material disagreement is not resolved by convenience.

## Normalized envelope

```json
{
  "schema_version": "method_intent_contract.v1",
  "status": "complete | partial | blocked",
  "central_hypothesis": "",
  "contribution_type": "",
  "central_mechanism_hypothesis": "",
  "core_mechanism": "",
  "must_preserve_components": [],
  "candidate_components": [],
  "expected_algorithm_flow": [],
  "allowed_refinements": [],
  "forbidden_silent_changes": [],
  "mechanism_to_ablation_plan": [],
  "claim_boundary": [],
  "must_not_claim": [],
  "unknowns": [],
  "source_refs": [],
  "intent_fingerprint": ""
}
```

## Component contract

Each component should use a stable ID and include:

```text
component_id
name
role
mechanism_ref
intended_input
intended_output
invariants
required
source_refs
```

When the handoff lacks a stable ID, derive one deterministically from the name and preserve it in later versions.

## Unknown handling

Classify unknowns as:

- `implementation_detail`: can be resolved conservatively without changing the contribution;
- `evidence_sensitive`: may affect ablation, fairness, or claim interpretation and must be resolved before approval;
- `scope_sensitive`: may alter the central mechanism, task, benchmark, or contribution and must be escalated.

Never fill an evidence-sensitive or scope-sensitive unknown from generic domain knowledge alone.

## Non-final-method marker

The source method intent should contain or imply:

```text
status = draft_intent_only
not_final_method_source = true
```

Absence is a warning only when the surrounding T5 contract is unambiguous; contradiction is blocking.
