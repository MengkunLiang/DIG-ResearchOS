# Implementation Output Contract

## Report envelope

```json
{
  "schema_version": "implementation_report.v1",
  "child_skill": "implementation",
  "status": "complete|partial|blocked|failed",
  "generated_at": "",
  "implementation_id": "",
  "iteration_id": "",
  "input_fingerprint": "",
  "contract_ref": "external_executor/report/phase_D/implementation_change_contract.json",
  "implementation_root": "external_executor/expr/implementation/...",
  "base_source": {},
  "approved_changes": {"status": "complete|partial|blocked|stale", "items": []},
  "implemented_changes": {"status": "complete|partial|blocked|stale", "items": []},
  "module_mapping": {"status": "complete|partial|blocked|stale", "items": [], "validation_ref": ""},
  "verification_records": {"status": "complete|partial|blocked|stale", "items": []},
  "tdd_cycles": {"status": "complete|partial|not_applicable|blocked|stale", "items": []},
  "patch_bundle": {"status": "complete|partial|blocked|stale", "path": "", "changed_files": []},
  "scope_scan": {"status": "pass|needs_review|blocked|stale", "path": "", "findings": []},
  "drift_assessment": {
    "contribution_drift": "none|minor|major",
    "protocol_impact": "none|nonmaterial|material",
    "fairness_impact": "none|controlled|uncertain|material",
    "baseline_reproduction_impact": "none|adapter_only|invalidates_selected|invalidates_all",
    "affected_reproduction_ids": [],
    "rationale": [],
    "evidence_refs": []
  },
  "implementation_risks": {"status": "complete|partial|blocked|stale", "items": []},
  "scope_change_proposals": {"status": "not_needed|complete|blocked|stale", "items": []},
  "implementation_gate": {
    "status": "ready_for_review|needs_fix|blocked",
    "required_verifications_complete": false,
    "mapping_complete": false,
    "patch_in_scope": false,
    "fresh_final_verification": false,
    "blocking_issues": [],
    "fixable_issues": [],
    "next_action": "continue_to_code_and_protocol_review|repair_implementation|human_review|stop_and_report"
  },
  "artifact_refs": [],
  "notes": []
}
```

Required sections remain present for blocked or failed work. Use honest status and empty items; do not omit a section or fabricate success content.

## Implemented change record

```json
{
  "change_id": "CHG-...",
  "status": "implemented|partial|not_implemented|blocked",
  "changed_paths": [],
  "summary": "",
  "spec_item_ids": [],
  "module_ids": [],
  "config_keys": [],
  "tests": [],
  "engineering_only": false,
  "known_limitations": [],
  "evidence_refs": []
}
```

## Module mapping record

```json
{
  "module_id": "M1",
  "implementation_status": "implemented|partial|not_implemented|blocked",
  "code_paths": [],
  "public_interfaces": [],
  "config_keys": [],
  "test_paths": [],
  "ablation_switch": {},
  "diagnostic_switches": [],
  "affected_experiment_ids": [],
  "limitations": [],
  "empirical_support_claimed": false
}
```

## Gate consistency

### `ready_for_review`

Requires:

- change contract status is `ready`;
- every mandatory approved change is implemented or an explicit authorized no-op;
- patch is non-empty unless no-op is authorized;
- scope scan is `pass`;
- module mapping validation passes;
- every mandatory verification has a passing green/final record;
- final verification records match the final worktree fingerprint;
- no unresolved blocking risk;
- contribution drift is not `major`;
- protocol impact is not `material`;
- fairness impact is not `material`;
- no unapproved dependency, secret, protected-path, or authority finding exists.

It does not mean Reviewer pass.

### `needs_fix`

Used when all remaining problems are repairable within the contract, such as incomplete implementation, failed bounded test, stale final verification, missing mapping, or reversible path-scope issue. It must include fixable issues.

### `blocked`

Used for missing authority/input, protected or escaping path, embedded secret, unapproved dependency/system change, major contribution drift, material protocol/fairness change, unresolved scope change, or another non-local blocker.

## Builder/Reviewer boundary

The report must not contain or assert:

```text
review_status=pass
approved_for=smoke
approved_for=small_scale
approved_for=formal
formal_run_ready=true
protocol_approved=true
fairness_approved=true
```

The validator rejects these claims when set by this child.

## Artifact references

Use workspace-relative paths and include when available:

```json
{
  "artifact_id": "",
  "path": "external_executor/...",
  "sha256": "",
  "size_bytes": 0,
  "producer": "implementation",
  "created_at": "",
  "evidence_level": "implementation_definition|verification|patch|mapping|unsupported"
}
```

## Result-pack mapping

The apply script updates only `result_pack.implementations`:

```json
{
  "status": "complete|partial|blocked|failed|stale",
  "active_implementation_id": "",
  "items": []
}
```

Each item is the full validated report or a lossless child-owned implementation record. Existing items with other IDs are preserved. Reapplying the same implementation ID replaces only that item.

## Child return

```text
child_skill=implementation
status=complete|partial|blocked|failed
implementation_gate=ready_for_review|needs_fix|blocked
implementation_id=<id>
iteration_id=<id>
report=external_executor/report/phase_D/implementation_report.json
implementation_root=<path>
patch_bundle=<path>
module_mapping=<path>
changed_paths=<paths-or-count>
contribution_drift=none|minor|major
protocol_impact=none|nonmaterial|material
fairness_impact=none|controlled|uncertain|material
baseline_reproduction_impact=none|adapter_only|invalidates_selected|invalidates_all
blocking_issues=<ids>
recommended_next_action=continue_to_code_and_protocol_review|repair_implementation|human_review|stop_and_report
```
