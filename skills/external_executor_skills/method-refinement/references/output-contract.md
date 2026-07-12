# Output Contract

## Standalone artifacts

```text
external_executor/method_refinement_preflight.json
external_executor/method_intent_contract.json
external_executor/method_implementation_spec.json
external_executor/method_specs/method-spec-vNNN-<fingerprint>.json
external_executor/method_spec_fingerprint.json
external_executor/method_delta.json
external_executor/method_scope_assessment.json
external_executor/method_implementation_spec_validation.json
external_executor/method_refinement_review.json
external_executor/method_implementation_brief.md
external_executor/method_refinement_report.json
external_executor/method_refinement_report_validation.json
```

## Narrow result-pack ownership

This Skill may update only:

```text
result_pack.json#method_refinements
result_pack.json#scope_change_requests   # only entries created_by=method-refinement
```

Do not update context alignment, resources, experiment plan, root iteration plans/decisions, baseline reproduction, implementation, code review, runs, diagnosis, attribution, evidence packaging, executor status, or manifest.

## Method refinement record

```json
{
  "refinement_id": "",
  "iteration_id": "",
  "status": "ready | needs_fix | blocked",
  "spec_version": 1,
  "spec_ref": "external_executor/method_implementation_spec.json",
  "snapshot_ref": "external_executor/method_specs/...json",
  "intent_fingerprint": "",
  "spec_fingerprint": "",
  "protocol_fingerprint": "",
  "delta_level": "none | minor | major",
  "approved_for": "implementation | none",
  "scope_change_request_id": null,
  "blocking_issues": [],
  "constraints": [],
  "artifact_refs": []
}
```

`method_refinements` is append/update by `refinement_id`; prior records are preserved.

## Child versus readiness status

- child `complete`, refinement `ready`: safe to dispatch `implementation`.
- child `partial`, refinement `needs_fix`: report is valid but specification must be repaired.
- child `blocked`, refinement `blocked`: major drift or missing authority prevents implementation.
- child `failed`: report or scripts could not complete reliably.

## Root return

```text
child_skill=method-refinement
status=complete|partial|blocked|failed
refinement_status=ready|needs_fix|blocked
refinement_id=<id>
spec=<path>
spec_fingerprint=<sha256>
delta_level=none|minor|major
approved_for=implementation|none
scope_change_request=<path-or-none>
blocking_issues=<list>
recommended_next_action=continue_to_implementation|return_to_method_refinement|human_review|stop_and_report
```
