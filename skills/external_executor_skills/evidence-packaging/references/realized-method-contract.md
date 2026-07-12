# Realized Method Package Contract

## Purpose

`method_intent` constrains the project before implementation. `realized_method_package` describes what actually exists after implementation, review, runs, diagnosis, attribution, and iteration decisions.

## Required envelope

```json
{
  "schema_version": "realized_method_package.v1",
  "snapshot_id": "",
  "snapshot_fingerprint": "",
  "status": "complete|partial|unavailable",
  "final_method_name": null,
  "one_sentence_method": null,
  "actual_core_mechanism": null,
  "implemented_modules": [],
  "dropped_modules": [],
  "added_modules": [],
  "unverified_modules": [],
  "actual_algorithm_flow": [],
  "actual_losses": [],
  "module_attribution": {},
  "claim_boundary": {},
  "delta_from_method_intent": {},
  "unresolved_fields": [],
  "source_refs": []
}
```

## Module record

Every implemented/added/modified module contains:

```json
{
  "module_id": "",
  "name": "",
  "status": "implemented|added|modified",
  "actual_role": "",
  "inputs": [],
  "outputs": [],
  "code_refs": [],
  "config_keys": [],
  "implementation_evidence_refs": [],
  "definition_status": "defined_in_implementation",
  "empirical_support": {
    "status": "supported|definition_or_hint_only|unsupported|unassessed",
    "evidence_types": [],
    "evidence_refs": [],
    "confidence": null,
    "limitations": []
  }
}
```

Code refs should identify a workspace-relative file and, when available, symbol/class/function. Config keys should identify the controlling key and config artifact. A README statement alone is insufficient when executable code/config exists.

## Actual algorithm flow

Record implemented order and data/control relationships, not aspirational prose. Each step should identify:

- step ID/order;
- input and output;
- module ID or code ref;
- operation;
- train/inference applicability;
- configuration switch;
- assumptions or failure conditions.

Losses/objectives should include formula/meaning, implementation ref, weighting/config, and where they enter the training flow.

## Delta from intent

Classify:

```text
implemented_as_intended
engineering_clarification
minor_allowed_refinement
added_module
modified_module
dropped_module
unverified_intent_component
approved_scope_change
unapproved_or_unresolved_drift
```

Do not conceal a changed central mechanism inside “engineering detail.” A major unresolved drift is a package blocker or human-review constraint.

## Claim boundary

Include:

- supported claim candidates;
- constrained/unresolved candidates;
- unsupported candidates;
- must-not-claim;
- setting/subset boundaries;
- attribution limitations;
- `audit_status=pre_T7_only`.

The package states what evidence exists. T7 decides what can enter the paper.

## Status

- `complete`: final identity, mechanism, flow, losses, modules, code/config mapping, attribution distinctions, intent delta, and boundary are present.
- `partial`: a reliable method definition exists but non-central fields or empirical assessments are unresolved.
- `unavailable`: actual method cannot be reconstructed without guessing, or code/config evidence is absent/incoherent.
