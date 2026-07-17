# Resource Requirement Contract

## Purpose

The requirement matrix is the Phase B source of truth. Candidates are evaluated against requirements; requirements are not rewritten to fit candidates.

## Requirement types

Use one of:

```text
benchmark_definition
dataset
dataset_split
baseline_implementation
metric_implementation
evaluation_protocol
preprocessing
checkpoint
environment
reference_material
adapter_interface
other
```

Split resources when they have independently testable provenance or acceptance criteria. For example, dataset files, official split definitions, and evaluation code should usually be separate requirements.

## Required fields

Each item contains:

```json
{
  "requirement_id": "REQ-baseline-name-xxxxxxxx",
  "name": "",
  "resource_type": "baseline_implementation",
  "required": true,
  "minimum_loop_dependency": true,
  "purpose": "",
  "claim_ids": [],
  "baseline_id": null,
  "expected_identity": {},
  "expected_interface": {},
  "expected_protocol": {},
  "accepted_source_classes": [],
  "acceptance_criteria": [],
  "replacement": {
    "allowed": false,
    "requires_review": true,
    "equivalence_criteria": []
  },
  "missing_blocks_execution": true,
  "source_refs": [],
  "status": "open",
  "notes": []
}
```

## Identity expectations

For baseline implementations, capture as available:

- canonical paper/title/DOI;
- official baseline name and version;
- author/project organization;
- defining mechanism, objective, or algorithm;
- required configuration family;
- expected training and inference entry points;
- expected output semantics.

An executable baseline requires all of these to be independently evidenced before it can receive `baseline_reproduction` or `formal_comparison` approval:

- accessible code or model;
- immutable revision or version lock;
- clear license;
- environment or dependency information;
- dataset version and split;
- metric implementation;
- at least one traceable result record.

For datasets/benchmarks, capture:

- official name and version;
- source organization;
- task and population/domain;
- schema or record structure;
- split definition;
- label semantics;
- preprocessing assumptions;
- evaluation protocol;
- access and license conditions.

## Acceptance criteria

Criteria must be observable. Good examples:

- repository is linked by the paper or author project page;
- immutable commit is recorded;
- code exposes training and evaluation entry points for the target task;
- target dataset and official split are supported;
- metric implementation matches name, direction, averaging, and edge-case handling;
- license permits the intended research use;
- static review has no unresolved critical finding;
- adapter changes are documented and do not alter algorithmic meaning.

Bad examples:

- “looks correct”;
- “popular repo”;
- “should work”;
- “similar benchmark”;
- “probably official.”

## Requirement status

Use one of:

```text
open
satisfied
satisfied_with_constraints
blocked
unavailable
stale
```

Candidate deficiency uses a separate taxonomy:

```text
missing
incomplete
incompatible
not_runnable
protocol_nonequivalent
restricted
untrusted_source
license_unknown
security_blocked
identity_ambiguous
```

Do not encode a deficiency by changing `required=false`.

## Blocking semantics

A requirement blocks Phase B when all are true:

- it is `required=true`;
- it supports the minimum experiment loop or a mandatory comparison;
- no passing candidate has suitable `approved_for` authority;
- its absence is marked `missing_blocks_execution=true`, or the confirmed scope makes it blocking.

An optional requirement may still constrain claims. Record this under `material_gaps` and `claim_constraints` rather than blocking the minimum loop.

## Stable IDs

IDs must remain stable across resume when semantic identity has not changed. Generate IDs from normalized type/name/identity and a short hash. Do not renumber by array position.

## Source references

Each semantic field should point to one or more source records. A source reference should include a workspace-relative path or stable public identifier and, when available, checksum/commit/version. Do not claim line-level precision unless it was actually captured.
