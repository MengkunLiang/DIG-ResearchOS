# Evidence Packaging Output Contract

## Owned artifacts

```text
external_executor/evidence_packaging_preflight.json
external_executor/final_evidence_snapshot.json
external_executor/final_evidence_snapshot_validation.json
external_executor/evidence_package/realized_method_package.json
external_executor/evidence_package/framework_figure_spec.json
external_executor/evidence_package/framework_figure.mmd
external_executor/evidence_package/framework_figure.svg
external_executor/evidence_package/figure_table_inventory.json
external_executor/evidence_package/evidence_mapping.json
external_executor/evidence_package/evidence_package_manifest.json
external_executor/evidence_packaging_gate.json
external_executor/evidence_packaging_report.json
external_executor/evidence_packaging_report_validation.json
```

Root owns registration in `run_manifest.json` and final executor state.

## Report envelope

```json
{
  "schema_version": "evidence_packaging_report.v1",
  "child_skill": "evidence-packaging",
  "generated_at": "",
  "status": "complete|partial|blocked|failed",
  "packaging_readiness": "ready|partial|blocked",
  "snapshot_id": "",
  "snapshot_fingerprint": "",
  "realized_method_package": {},
  "framework_figure": {},
  "figure_table_inventory": {},
  "evidence_mapping": {},
  "package_manifest": {},
  "validation": {},
  "artifact_refs": [],
  "blocking_issues": [],
  "constraints": [],
  "recommended_next_action": "continue_to_writer_handoff|continue_to_writer_handoff_with_constraints|repair_package_or_return_to_root",
  "handoff_semantics": "pre_T7_audit_only",
  "claim_approval": "not_performed",
  "report_fingerprint": "",
  "notes": []
}
```

All component snapshot fingerprints must equal the report fingerprint.

## Result-pack mapping

The apply script writes only:

```text
realized_method_package <- report.realized_method_package
framework_figure <- report.framework_figure
figure_table_inventory <- report.figure_table_inventory
evidence_mapping <- report.evidence_mapping
evidence_packaging <- compact report metadata and package manifest
```

It must preserve context alignment, resources, plans, reproduction, implementation, reviews, runs, diagnoses, attribution, iterations, claims, risks, and sibling sections.

## Gate consistency

- `ready` requires child `complete`.
- `partial` normally maps to child `partial` and must list constraints.
- `blocked` maps to child `blocked` and must list blocking issues.
- `failed` is an unrecoverable execution failure, not missing evidence.
- A method package may be `partial` or `unavailable` even when the child itself completed best-effort work.
- Framework `missing` or `blocked` cannot be represented as ready.
- Ready result visuals require full numeric lineage.
- Claim approval is always `not_performed`.

## Child return

```text
child_skill=evidence-packaging
status=complete|partial|blocked|failed
packaging_readiness=ready|partial|blocked
snapshot_id=<id>
snapshot_fingerprint=<sha256>
report=external_executor/evidence_packaging_report.json
realized_method=external_executor/evidence_package/realized_method_package.json
framework_figure=external_executor/evidence_package/framework_figure_spec.json
figure_table_inventory=external_executor/evidence_package/figure_table_inventory.json
evidence_mapping=external_executor/evidence_package/evidence_mapping.json
blocking_issues=<ids>
constraints=<ids>
recommended_next_action=continue_to_writer_handoff|continue_to_writer_handoff_with_constraints|repair_package_or_return_to_root
```
