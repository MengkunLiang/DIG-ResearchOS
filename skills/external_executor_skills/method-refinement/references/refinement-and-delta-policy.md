# Refinement and Delta Policy

## Principle

Refinement is allowed only inside the approved scientific contract. Every change is explicit, typed, versioned, and traceable to an authorized trigger.

## Change classes

### `implementation_detail`

Examples:

- file or class layout;
- tensor plumbing that preserves semantics;
- logging and checkpoint format;
- numerical guardrails;
- deterministic configuration parsing;
- runtime optimization without claim impact.

Usually no new scientific approval is required, but fairness and reproducibility effects must be recorded.

### `contract_preserving_refinement`

Examples:

- converting an abstract component into a concrete architecture consistent with its intended role;
- adding an ablation switch or diagnostic hook;
- selecting an implementation alternative explicitly listed in `allowed_refinements`;
- adding a supporting module necessary to realize the approved mechanism without changing the contribution.

Requires a rationale and traceability to intent or root decision.

### `claim_affecting_minor`

Examples:

- changing module weighting or aggregation while keeping the same mechanism;
- dropping an optional candidate component;
- adding a non-core module that narrows interpretation;
- changing a failure-handling path that affects a subset or limitation.

Requires root awareness, an updated experiment plan when evidence changes, and explicit claim constraints. It cannot silently broaden claims.

### `scope_change_major`

Examples:

- changing the central hypothesis or contribution type;
- replacing the core mechanism;
- deleting a must-preserve component;
- changing task, benchmark, dataset role, or primary comparison meaning;
- introducing a new core contribution outside the intent;
- turning the method into a known baseline variant;
- broadening the claim boundary;
- changing fairness/protocol to favor the method.

Requires a `scope_change_request` and human/root approval before implementation.

## Delta record

```json
{
  "change_id": "",
  "path": "modules/M1/config_keys",
  "operation": "add | remove | replace | modify | rename",
  "before": null,
  "after": null,
  "classification": "implementation_detail | contract_preserving_refinement | claim_affecting_minor | scope_change_major",
  "rationale": "",
  "authorization_refs": [],
  "affected_modules": [],
  "affected_claims": [],
  "affected_experiments": [],
  "fairness_effect": "none | review_required | material",
  "novelty_effect": "none | possible | material",
  "requires_plan_update": false,
  "requires_human_review": false
}
```

## Versioning

Increment `spec_version` whenever any semantic field changes. Pure timestamp, formatting, or artifact-reference updates do not create a semantic version.

Preserve:

- previous spec fingerprint;
- current spec fingerprint;
- trigger and iteration decision;
- delta list;
- compatibility status;
- required downstream invalidation.

Do not overwrite history without a versioned snapshot.
