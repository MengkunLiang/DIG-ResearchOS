# Output Contract

## Snapshot

`writer_handoff_snapshot.v1` contains `handoff_id`, `input_fingerprint`, `source_versions`, `writer_handoff_contract`, `expected_requirements`, `sections`, `manifest`, and source references.

## Inventory

`writer_handoff_inventory.v1` contains categorized `items`, each with `item_id`, `category`, `status`, `source_ids`, `artifact_refs`, `evidence_level`, `t7_consumers`, and limitations.

## Claim map

`writer_handoff_claim_map.v1` contains `claims`, each with `claim_id`, question/summary, support refs by class, counterevidence refs, required-baseline status, evidence ceiling, limitations, risks, must-not-claim consequences, and `audit_status=pending_T7`.

## T7 index

`writer_handoff_t7_index.v1` maps consumer stages to artifacts and source IDs. It includes schema/fingerprint information and integrity status.

## Report

Required fields:

```text
schema_version
handoff_id
status
pre_audit
input_fingerprint
source_snapshot
method_summary
implementation_summary
results
figures
tables
claim_candidates
must_not_claim
limitations
open_risks
failed_and_unusable_work
recovery_notes
recommended_storyline_update
t7_ingest_index
integrity_validation
blocking_issues
warnings
handoff_gate
```

`status`: `complete | partial | blocked | failed`.

`handoff_gate.status`:

```text
ready_for_T7_audit
partial_for_T7_audit
blocked_for_T7_audit
```

`recommended_next_action`:

```text
return_to_root_for_final_validation
return_to_root_with_partial_handoff
repair_handoff
stop_and_report
```

## Narrow apply

Only `result_pack.writer_handoff` may be replaced. Sibling sections remain byte-equivalent after JSON normalization.
