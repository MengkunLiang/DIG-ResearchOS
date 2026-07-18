# Realized Method Package Contract

## Purpose

`method_intent` constrains the project before implementation. `realized_method_package` describes what actually exists after implementation, review, runs, diagnosis, attribution, and iteration decisions.

Select the attribution report for the final iteration through `result_pack.module_attributions.current_by_iteration` and flatten its nested `module_attributions.items`. Preserve mechanism-level records separately; never interpret the report envelope itself as a module attribution.

## Required envelope

```json
{
  "schema_version": "realized_method_package.v1",
  "snapshot_id": "",
  "snapshot_fingerprint": "",
  "status": "complete|partial|unavailable",
  "final_version": {
    "iteration_id": "",
    "implementation_id": "",
    "implementation_root": "",
    "final_worktree_fingerprint": "",
    "method_spec": {"spec_id": "", "spec_version": 0, "spec_fingerprint": "", "spec_ref": "", "source_sha256": ""},
    "review_id": "",
    "review_status": "pass|other",
    "approved_for": "",
    "protocol_fingerprint": "",
    "bound_experiment_run_ids": []
  },
  "final_method_name": null,
  "one_sentence_method": null,
  "actual_core_mechanism": null,
  "implemented_modules": [],
  "dropped_modules": [],
  "added_modules": [],
  "unverified_modules": [],
  "actual_algorithm_flow": [],
  "training_flow": [],
  "inference_flow": [],
  "actual_losses": [],
  "system_boundary": {},
  "symbol_table": [],
  "pseudocode": [],
  "data_and_protocol_interfaces": {},
  "configuration_contract": {},
  "ablation_and_diagnostic_controls": {},
  "implementation_change_history": [],
  "method_evolution": {},
  "execution_binding": {},
  "reproducibility_requirements": {},
  "evidence_traceability": [],
  "module_attribution": {},
  "claim_boundary": {},
  "delta_from_method_intent": {},
  "source_validation": {},
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

Keep specified and realized semantics distinct. A method-spec flow may be retained with `realization_status=unverified_from_spec`, but `complete` requires all participating modules and each loss implementation to resolve against the selected final implementation.

## Final-version selection

Select exactly one implementation through `implementations.active_implementation_id`. Match its `method_spec_fingerprint` to one `method_refinements` record and follow that record's immutable `snapshot_ref`. Select review, diagnosis, attribution, iteration decision, and experiment runs for the same implementation/iteration. Never merge historical implementation module mappings into the final package.

All selected result-pack values and the parsed method-spec value must come from `external_executor/report/final_evidence_snapshot.json`. Live source files are checked for mutation, not reread as an alternative package input.

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

- `complete`: final identity and fingerprint, passed review, one protocol, training/inference flow, verified loss implementations, modules, code/config mapping, full attribution distinctions, evidence traceability, intent delta, and boundary are present; `source_validation.status=pass` and no unresolved field remains.
- `partial`: a reliable method definition exists but non-central fields or empirical assessments are unresolved.
- `unavailable`: actual method cannot be reconstructed without guessing, or code/config evidence is absent/incoherent.
