---
name: method-refinement
description: Compile the approved ResearchOS method intent and current root-owned iteration decision into a versioned, implementation-ready method specification with stable module contracts, training/inference flows, config and ablation interfaces, failure modes, intent/spec fingerprints, an explicit delta ledger, and a pre-implementation scope-drift gate. Use when `research-execution` dispatches initial method engineering before implementation, when an approved minor refinement follows diagnosis or attribution, or when a stale method specification must be rebuilt after an authorized plan or protocol change. Do not write production code, run experiments, approve a major scope change, redesign the central hypothesis, alter the benchmark or contribution type, or overwrite sibling-owned result-pack sections.
---

# Method Refinement

Turn the approved research intent into a precise implementation contract before code is changed. Preserve the scientific mechanism, expose every implementation choice that may affect evidence, and stop before silent contribution drift.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before any write:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/handoff_pack.json#method_intent`;
   - `<workspace>/external_executor/result_pack.json#context_alignment`;
   - `<workspace>/external_executor/result_pack.json#experiment_plan`;
   - the active root-owned iteration plan and decision, when present;
   - `<skill-dir>/references/method-intent-contract.md`;
   - `<skill-dir>/references/implementation-spec-contract.md`;
   - `<skill-dir>/references/refinement-and-delta-policy.md`;
   - `<skill-dir>/references/scope-drift-and-escalation.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` when the method intent or confirmed scope is absent, the experiment protocol is not locked, an active iteration plan is missing, a requested refinement lacks root authorization, or the output boundary cannot be determined.

Write only:

- `external_executor/method_refinement_preflight.json`;
- `external_executor/method_intent_contract.json`;
- `external_executor/method_implementation_spec.json`;
- versioned snapshots under `external_executor/method_specs/`;
- `external_executor/method_spec_fingerprint.json`;
- `external_executor/method_delta.json`;
- `external_executor/method_scope_assessment.json`;
- `external_executor/method_refinement_review.json`;
- `external_executor/method_implementation_brief.md`;
- `external_executor/method_refinement_report.json` and its validation;
- `result_pack.json#method_refinements` and this skill's entries in `result_pack.json#scope_change_requests` through the narrow apply script.

Do not change executor status, run manifest, iteration plans or decisions, experiment plan, baseline reproduction, implementation records, code-review records, runs, diagnosis, attribution, or evidence packaging. Return control to `research-execution` after applying the report.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_method_refinement.py --workspace <workspace> \
  --output external_executor/method_refinement_preflight.json
```

The preflight confirms:

- context alignment is non-blocking;
- `method_intent` is explicitly marked as intent rather than final method truth;
- experiment plan and protocol fingerprint exist;
- an active root iteration plan identifies the trigger and approved change surface;
- any diagnosis/attribution-based refinement cites the responsible iteration decision;
- required baselines, claim boundaries, and must-preserve mechanism are traceable;
- all writes are inside allowed paths.

Warnings identify fields that the implementation specification must resolve. A blocker prevents implementation approval but should still result in an honest report when possible.

## Normalize the method intent

Read `references/method-intent-contract.md`, then run:

```bash
python <skill-dir>/scripts/normalize_method_intent.py --workspace <workspace> \
  --output external_executor/method_intent_contract.json
```

The normalized contract must separate:

- central hypothesis and contribution type;
- core mechanism and must-preserve components;
- candidate/supporting components;
- expected algorithm flow;
- mechanism-to-ablation commitments;
- allowed refinements;
- forbidden silent changes;
- claim boundary and must-not-claim constraints;
- unresolved implementation questions.

Do not strengthen, broaden, or reinterpret the original intent. Unknown implementation details remain explicit unknowns until resolved by authorized evidence or conservative engineering decisions.

## Build the implementation specification

Read `references/implementation-spec-contract.md` and `references/evidence-traceability.md`, then run:

```bash
python <skill-dir>/scripts/build_method_implementation_spec.py --workspace <workspace> \
  --intent external_executor/method_intent_contract.json \
  --output external_executor/method_implementation_spec.json
```

The specification must define, at minimum:

- objective and scientific contract;
- stable module IDs, purpose, inputs, outputs, invariants, and mechanism mapping;
- losses/objectives and their roles;
- training and inference flows;
- dataset, preprocessing, metric, baseline, and fairness interfaces inherited from the protocol;
- config keys, defaults, ablation switches, and diagnostic controls;
- logging, provenance, checkpoint, and raw-output requirements;
- non-contribution engineering choices and training tricks;
- expected failure modes and fallback behavior;
- implementation targets and tests;
- acceptance checks and experiment traceability;
- unresolved decisions and scope boundary.

The specification describes what must be implemented; it does not contain production implementation code. Keep engineering support modules distinct from claimed contribution modules.

Write a deterministic fingerprint and versioned snapshot:

```bash
python <skill-dir>/scripts/fingerprint_method_spec.py --workspace <workspace> \
  --spec external_executor/method_implementation_spec.json \
  --output external_executor/method_spec_fingerprint.json --write-back
```

## Compute the delta before implementation

Read `references/refinement-and-delta-policy.md`.

For an initial specification, compare the normalized intent to the specification. For a later refinement, compare the previous valid specification to the new specification:

```bash
python <skill-dir>/scripts/compare_method_specs.py --workspace <workspace> \
  --intent external_executor/method_intent_contract.json \
  --current external_executor/method_implementation_spec.json \
  --output external_executor/method_delta.json
```

When a previous specification exists, pass it explicitly:

```bash
python <skill-dir>/scripts/compare_method_specs.py --workspace <workspace> \
  --intent external_executor/method_intent_contract.json \
  --previous <previous-method-spec.json> \
  --current external_executor/method_implementation_spec.json \
  --output external_executor/method_delta.json
```

Every change must be classified as one of:

- `implementation_detail`;
- `contract_preserving_refinement`;
- `claim_affecting_minor`;
- `scope_change_major`.

Record added, removed, replaced, renamed, and modified modules; changed losses or flows; config and ablation changes; fairness effects; claim effects; and evidence/authorization references. Do not hide a change inside prose.

## Assess scope drift and escalate before code

Run:

```bash
python <skill-dir>/scripts/assess_scope_change.py --workspace <workspace> \
  --intent external_executor/method_intent_contract.json \
  --spec external_executor/method_implementation_spec.json \
  --delta external_executor/method_delta.json \
  --output external_executor/method_scope_assessment.json
```

A major scope change includes, at minimum:

- changing the central hypothesis, task, benchmark, contribution type, or core mechanism;
- removing or neutralizing a must-preserve component;
- broadening the claim boundary beyond the confirmed scope;
- introducing a new core contribution not authorized by the intent or root decision;
- turning the method into a required baseline or another known method variant;
- changing protocol/fairness assumptions to make the method appear stronger;
- implementing a previously unapproved major change and documenting it afterward.

For major drift, write a structured `scope_change_request`, set `requires_human_review=true`, and do not approve implementation. Only `research-execution` or the human gate may authorize a new scope.

## Validate and independently review the specification

Validate the contract:

```bash
python <skill-dir>/scripts/validate_method_implementation_spec.py --workspace <workspace> \
  --spec external_executor/method_implementation_spec.json \
  --output external_executor/method_implementation_spec_validation.json
```

Then read `references/method-review-checklist.md` and run:

```bash
python <skill-dir>/scripts/review_method_refinement.py --workspace <workspace> \
  --spec external_executor/method_implementation_spec.json \
  --delta external_executor/method_delta.json \
  --scope-assessment external_executor/method_scope_assessment.json \
  --spec-validation external_executor/method_implementation_spec_validation.json \
  --output external_executor/method_refinement_review.json
```

The review checks:

- intent and confirmed-scope alignment;
- coverage of every must-preserve component;
- module/interface/loss/flow completeness;
- protocol and experiment-plan compatibility;
- mechanism-to-ablation and claim-to-module traceability;
- fairness and non-contribution engineering separation;
- config, logging, reproducibility, and testability;
- unresolved design decisions;
- scope drift and required approval.

Use:

- `pass`, `approved_for=implementation` only when the specification is complete enough to implement and no major drift exists;
- `needs_fix`, `approved_for=none` for repairable incompleteness or ambiguity;
- `blocked`, `approved_for=none` for major scope drift, missing authority, or incompatible protocol.

This review approves only the specification for implementation. The later `code-and-protocol-review` Skill must still review actual code and protocol before any run.

## Render the implementer brief

After a passing or constrained specification review, run:

```bash
python <skill-dir>/scripts/render_implementation_brief.py --workspace <workspace> \
  --spec external_executor/method_implementation_spec.json \
  --review external_executor/method_refinement_review.json \
  --output external_executor/method_implementation_brief.md
```

The brief is a navigation artifact for `implementation`; the JSON specification remains the source of truth.

## Assemble, validate, and apply narrowly

Run:

```bash
python <skill-dir>/scripts/assemble_method_refinement_report.py --workspace <workspace> \
  --output external_executor/method_refinement_report.json

python <skill-dir>/scripts/validate_method_refinement_report.py --workspace <workspace> \
  --report external_executor/method_refinement_report.json

python <skill-dir>/scripts/apply_method_refinement_report.py --workspace <workspace> \
  --report external_executor/method_refinement_report.json
```

The apply script appends or replaces only the matching refinement record and scope request owned by this skill. It must preserve every sibling-owned result-pack section.

## Return to the root

Return a compact child result:

```text
child_skill=method-refinement
status=complete|partial|blocked|failed
refinement_status=ready|needs_fix|blocked
refinement_id=<id>
spec=external_executor/method_implementation_spec.json
spec_fingerprint=<sha256>
delta_level=none|minor|major
approved_for=implementation|none
scope_change_request=<path-or-none>
blocking_issues=<ids>
recommended_next_action=continue_to_implementation|return_to_method_refinement|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns checkpointing, manifest updates, executor status, human gates, and the next dispatch.

## Evidence and safety rules

- Treat `method_intent` as an initial constraint, never as realized method truth.
- Preserve stable module IDs across refinements; record renames and replacements explicitly.
- Every claimed mechanism must map to modules, config/ablation controls, and planned evidence.
- Every non-contribution engineering trick must be labeled and applied fairly where relevant.
- Do not write code to resolve a design ambiguity that changes the contribution.
- Do not use diagnosis or attribution as authority unless the root iteration decision approves the refinement direction.
- Do not broaden claims because a new implementation seems promising.
- Do not delete failed or superseded specification history; snapshot and reference it.
- Do not approve implementation when the protocol fingerprint is stale or incompatible.
- Never self-approve a major scope change.

## Resource map

- `references/method-intent-contract.md`: normalized intent fields, authority, and unknown handling.
- `references/implementation-spec-contract.md`: module, flow, config, test, and acceptance specification.
- `references/refinement-and-delta-policy.md`: change taxonomy, versioning, and delta records.
- `references/scope-drift-and-escalation.md`: major drift triggers and scope-change request contract.
- `references/evidence-traceability.md`: claim–mechanism–module–config–ablation mapping.
- `references/method-review-checklist.md`: pre-implementation independent review.
- `references/output-contract.md`: standalone artifacts, result-pack ownership, and return shape.
- `scripts/preflight_method_refinement.py`: validate Phase D method-design prerequisites.
- `scripts/normalize_method_intent.py`: create a stable intent contract and fingerprint.
- `scripts/build_method_implementation_spec.py`: generate a versioned implementation-spec scaffold.
- `scripts/fingerprint_method_spec.py`: fingerprint and snapshot the current specification.
- `scripts/compare_method_specs.py`: produce a structured intent/spec or spec/spec delta.
- `scripts/assess_scope_change.py`: classify drift and create a scope-change request when required.
- `scripts/validate_method_implementation_spec.py`: validate structural and traceability contracts.
- `scripts/review_method_refinement.py`: issue the pre-implementation review verdict.
- `scripts/render_implementation_brief.py`: render a human/agent navigation brief.
- `scripts/assemble_method_refinement_report.py`: assemble the durable child report.
- `scripts/validate_method_refinement_report.py`: validate report and verdict consistency.
- `scripts/apply_method_refinement_report.py`: update only owned result-pack sections.
