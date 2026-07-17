# Phase B Output Contract

## Child-owned artifacts

```text
external_executor/resource_preflight.json
external_executor/resource_requirement_matrix.json
external_executor/resource_local_inventory.json
external_executor/resource_search_records.json
external_executor/resource_source_report.json
external_executor/resource_source_report.md
external_executor/resource_preparation_report.json
resources/**
resource/**
```

The root owns manifest registration and executor status.

## Report envelope

```json
{
  "schema_version": "resource_preparation_report.v1",
  "child_skill": "resource-and-baseline-preparation",
  "status": "complete|partial|blocked|failed",
  "generated_at": "",
  "input_fingerprint": "",
  "policy_snapshot": {},
  "resource_requirement_matrix": {
    "schema_version": "resource_requirement_matrix.v1",
    "status": "complete|partial|blocked|stale",
    "items": []
  },
  "local_inventory": {"status": "complete|partial|blocked|stale", "items": []},
  "remote_search_records": {"status": "not_needed|complete|partial|blocked|stale", "items": []},
  "staged_resources": {"status": "not_started|complete|partial|blocked|stale", "items": []},
  "acquired_resources": {"status": "not_needed|complete|partial|blocked|stale", "items": []},
  "baseline_candidates": {"status": "complete|partial|blocked|stale", "items": []},
  "dataset_inventory": {"status": "complete|partial|blocked|stale", "items": []},
  "reimplementations": {"status": "not_needed|complete|partial|blocked|stale", "items": []},
  "resource_source_report": {
    "status": "not_started|complete|partial|blocked|stale",
    "json_path": "external_executor/resource_source_report.json",
    "markdown_path": "external_executor/resource_source_report.md",
    "source_roots": ["resources", "resource"],
    "counts": {"byhand": 0, "Remote_acquisition": 0, "reproduction": 0},
    "categories": {"byhand": [], "Remote_acquisition": [], "reproduction": []}
  },
  "resource_reviews": {"status": "complete|partial|blocked|stale", "items": []},
  "material_gaps": {"status": "complete|partial|blocked|stale", "items": []},
  "resource_risks": {"status": "complete|partial|blocked|stale", "items": []},
  "resource_readiness": {
    "status": "ready|partial|blocked",
    "minimum_loop_feasible": false,
    "approved_requirement_ids": [],
    "constrained_requirement_ids": [],
    "blocking_requirement_ids": [],
    "claim_constraints": [],
    "blocking_issues": [],
    "next_action": "continue_to_experiment_design|continue_with_constraints|human_review|stop_and_report"
  },
  "artifact_refs": [],
  "notes": []
}
```

Required sections remain present under blocked/failed states; use honest status, empty items, and blocking issues.

## Result-pack mapping

The apply script writes only:

```text
resource_requirement_matrix <- report.resource_requirement_matrix
resources <- {
  status,
  policy_snapshot,
  local_inventory,
  remote_search_records,
  staged_resources,
  acquired_resources,
  reimplementations,
  resource_source_report,
  resource_reviews,
  artifact_refs
}
baseline_candidates <- report.baseline_candidates
dataset_inventory <- report.dataset_inventory
material_gaps <- report.material_gaps
resource_risks <- report.resource_risks
resource_readiness <- report.resource_readiness
```

## Review and readiness consistency

- Every requirement ID is unique.
- Every candidate references known requirement IDs.
- Every review references a known candidate and known requirement IDs.
- No candidate may use `external_executor/expr/` as its source path; that directory is the formal execution area, not the Phase B resource pool.
- Staged local products must be under `resources/byhand/`, remote acquisitions under `resource/Remote_acquisition/`, and baseline reimplementations under `resource/reproduction/`.
- The final resource source report must classify by-hand products under `resources/` and acquired/reimplemented products under `resource/` as `byhand`, `Remote_acquisition`, or `reproduction`.
- Every required baseline requirement is represented by a candidate, a material gap, or a blocker.
- `ready` requires all minimum-loop blocking requirements to be satisfied by passing reviews with suitable approvals.
- `partial` requires `minimum_loop_feasible=true` and at least one documented constraint/gap/risk.
- `blocked` requires `minimum_loop_feasible=false` or one or more blocking requirement IDs/issues.
- `complete` child status can accompany `ready`, `partial`, or `blocked` when Phase B analysis itself completed honestly.
- `failed` is reserved for an unrecoverable execution error in the skill, not resource unavailability.

## Candidate and artifact references

Use workspace-relative paths. An artifact reference should contain, when available:

```json
{
  "artifact_id": "",
  "path": "external_executor/...",
  "sha256": "",
  "size_bytes": 0,
  "producer": "resource-and-baseline-preparation",
  "created_at": "",
  "evidence_level": "resource_definition|provenance|static_review|method_definition|unsupported"
}
```

## Child return

```text
child_skill=resource-and-baseline-preparation
status=complete|partial|blocked|failed
resource_readiness=ready|partial|blocked
report=external_executor/resource_preparation_report.json
matrix=external_executor/resource_requirement_matrix.json
approved_requirement_ids=<ids>
constrained_requirement_ids=<ids>
blocking_requirement_ids=<ids>
claim_constraints=<ids-or-summary>
recommended_next_action=continue_to_experiment_design|continue_with_constraints|human_review|stop_and_report
```
