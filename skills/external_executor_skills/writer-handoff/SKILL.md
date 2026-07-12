---
name: writer-handoff
description: Compile and validate the final ResearchOS external-executor evidence snapshot into a machine-readable, pre-audit handoff for T7. Use when `research-execution` dispatches Phase F4-F5 after `evidence-packaging` has produced a stable realized method package, final framework figure, and figure/table inventory, including partial or blocked executions that still require an honest T7 handoff. Aggregate method, implementation, results, diagnoses, attributions, figures, tables, claim candidates, must-not-claim boundaries, limitations, risks, failed work, and recovery notes; verify artifact references and provenance; build a T7 ingest index; and write only `result_pack.writer_handoff`. Do not write manuscript prose, create final claims, mark evidence as audited, change upstream artifacts, repair experiments, approve scope changes, set executor completion status, or write T7/T8 outputs.
---

# Writer Handoff

Act as the final external-executor handoff compiler. Turn one pinned evidence-package snapshot into an honest, machine-readable package that T7 can ingest and audit without hidden conversation history. The output is always pre-audit: `ready_for_T7_audit` never means paper-ready, claim-approved, or T8-ready.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before writing:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/handoff_pack.json#context_reboost.writer_handoff_contract`;
   - `<workspace>/external_executor/expected_outputs_schema.json`;
   - `<workspace>/external_executor/result_pack.json`;
   - `<workspace>/external_executor/run_manifest.json`;
   - the root-owned final iteration decision and evidence-package checkpoint;
   - `<skill-dir>/references/handoff-policy.md`;
   - `<skill-dir>/references/t7-consumer-contract.md`;
   - `<skill-dir>/references/evidence-and-claim-boundary.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` only when the workspace/control boundary is indeterminate, the result pack cannot be parsed, an unsupported major schema prevents safe interpretation, or the evidence-package identity cannot be resolved. Missing scientific evidence normally produces an honest partial handoff rather than no handoff.

Write only:

- `external_executor/writer_handoff_preflight.json`;
- `external_executor/writer_handoff_snapshot.json`;
- `external_executor/writer_handoff_inventory.json`;
- `external_executor/writer_handoff_claim_map.json`;
- `external_executor/writer_handoff_t7_index.json`;
- `external_executor/writer_handoff_integrity.json`;
- `external_executor/writer_handoff_report.json`;
- versioned artifacts under `external_executor/workdir/writer_handoff/`;
- `result_pack.json#writer_handoff` through the narrow apply script.

Do not modify upstream evidence, runs, diagnoses, attributions, packages, figures, tables, claim boundaries, iteration decisions, executor status, manifest, budgets, drafts, or submission files. Return control to `research-execution` after applying the report.

## Run deterministic preflight

```bash
python <skill-dir>/scripts/preflight_handoff.py --workspace <workspace> \
  --output external_executor/writer_handoff_preflight.json
```

The preflight confirms:

- control files and supported schema majors;
- a final or currently selected evidence-package snapshot;
- realized method, framework figure, and figure/table inventory status;
- root iteration-stop or handoff authorization when represented;
- T7-required sections and the writer handoff contract;
- allowed write paths.

A partial, blocked, or failed execution still proceeds when a trustworthy snapshot can be built. Do not fabricate missing sections to obtain a pass.

## Pin one handoff snapshot

Read `references/artifact-reference-and-integrity.md`, then run:

```bash
python <skill-dir>/scripts/build_handoff_snapshot.py --workspace <workspace> \
  --output external_executor/writer_handoff_snapshot.json
```

The snapshot binds:

- result-pack and manifest versions;
- final iteration decision and claim boundary;
- resource and baseline readiness;
- experiment plan, reviews, runs, failures, diagnoses, and attributions;
- realized method package;
- final framework figure;
- figure/table inventory;
- open blockers, limitations, risks, approximations, replacements, and scope-change records;
- writer handoff contract and expected output requirements;
- one deterministic input fingerprint.

Do not mix artifacts produced from different evidence-package fingerprints. If upstream artifacts change, the handoff becomes stale and must be rebuilt.

## Inventory handoff materials

```bash
python <skill-dir>/scripts/inventory_handoff_materials.py \
  --snapshot <workspace>/external_executor/writer_handoff_snapshot.json \
  --output <workspace>/external_executor/writer_handoff_inventory.json
```

The inventory classifies material as:

```text
method_definition
implementation_evidence
formal_result_candidate
ablation_result_candidate
diagnostic_result
framework_figure
result_figure
result_table
failed_or_unusable_run
limitation
open_risk
must_not_claim
recovery_note
```

Every item must expose source IDs and artifact paths where applicable. A natural-language summary without source references is navigation, not evidence.

## Verify artifact references and provenance

```bash
python <skill-dir>/scripts/validate_artifact_refs.py --workspace <workspace> \
  --snapshot external_executor/writer_handoff_snapshot.json \
  --inventory external_executor/writer_handoff_inventory.json \
  --output external_executor/writer_handoff_integrity.json
```

Verify:

- workspace-relative canonical paths;
- no path escape or symlink escape;
- referenced file existence;
- checksum and size when declared;
- formal-result links to config, raw log, metric output, split, seed/repeat, code/resource version, environment, and protocol fingerprint;
- method-module links to code/config;
- figure/table links to generating scripts or structured sources;
- manifest consistency when entries exist.

A missing optional presentation file may be partial. A broken formal-result, realized-method, or source-data reference is blocking for the affected item and must lower readiness.

## Build the claim–evidence handoff map

Read `references/evidence-and-claim-boundary.md`, then run:

```bash
python <skill-dir>/scripts/build_claim_evidence_map.py \
  --snapshot <workspace>/external_executor/writer_handoff_snapshot.json \
  --inventory <workspace>/external_executor/writer_handoff_inventory.json \
  --output <workspace>/external_executor/writer_handoff_claim_map.json
```

For each claim ID, preserve:

- the upstream claim or reviewer question;
- supporting formal candidates, diagnostics, method definitions, and figures/tables;
- counterevidence and failed/unstable runs;
- required baseline coverage;
- evidence ceiling before T7 audit;
- limitations, risks, and unresolved evidence requests;
- explicit must-not-claim consequences.

Allowed evidence ceilings are:

```text
formal_candidate
diagnostic_only
method_definition_only
unsupported
```

Never mark a claim `audited`, `accepted`, `proven`, `paper_ready`, or final. T7 owns audit and claim closure.

## Build the T7 ingest index

```bash
python <skill-dir>/scripts/build_t7_ingest_index.py \
  --snapshot <workspace>/external_executor/writer_handoff_snapshot.json \
  --inventory <workspace>/external_executor/writer_handoff_inventory.json \
  --claim-map <workspace>/external_executor/writer_handoff_claim_map.json \
  --integrity <workspace>/external_executor/writer_handoff_integrity.json \
  --output <workspace>/external_executor/writer_handoff_t7_index.json
```

The index tells T7 where to find:

- realized method and code/config mapping;
- run records, raw logs, configs, metrics, environments, and failed trials;
- diagnosis and attribution records;
- framework and result figures/tables with source lineage;
- claim candidates, counterevidence, limitations, risks, and must-not-claim boundaries;
- schema versions and fingerprints.

The index is a navigation layer, not a replacement for source artifacts.

## Compose the pre-audit handoff

Read:

- `references/limitations-risks-and-negative-results.md`;
- `references/storyline-and-summary-policy.md`;
- `references/output-contract.md`.

Initialize:

```bash
python <skill-dir>/scripts/initialize_writer_handoff.py --workspace <workspace> \
  --snapshot external_executor/writer_handoff_snapshot.json \
  --inventory external_executor/writer_handoff_inventory.json \
  --claim-map external_executor/writer_handoff_claim_map.json \
  --t7-index external_executor/writer_handoff_t7_index.json \
  --integrity external_executor/writer_handoff_integrity.json \
  --output external_executor/writer_handoff_report.json
```

Complete only evidence-bound summaries:

- method and implementation summaries;
- main, ablation, diagnostic, robustness, efficiency, failure, and negative-result summaries;
- figure/table navigation;
- claim candidates with support and counterevidence;
- must-not-claim boundaries;
- limitations, open risks, approximations, replacements, unavailable resources, and recovery notes;
- a `recommended_storyline_update` labeled `pre_audit_suggestion`.

Do not write paper sections, title/abstract claims, polished contribution statements, or final captions that erase uncertainty. Preserve negative and contradictory evidence.

## Compute handoff readiness

```bash
python <skill-dir>/scripts/compute_handoff_gate.py \
  --report <workspace>/external_executor/writer_handoff_report.json --write-back
```

Gate outcomes:

- `ready_for_T7_audit`: required handoff sections exist, core references validate, risks and boundaries propagate, and the package is internally consistent;
- `partial_for_T7_audit`: useful ingestible evidence exists but required baseline coverage, formal provenance, package completeness, figures/tables, repeats, or risk closure is incomplete;
- `blocked_for_T7_audit`: T7 cannot safely identify the evidence snapshot or core artifacts due to schema, path, integrity, or authority failure.

The gate does not set executor completion status and does not authorize T8.

## Validate and apply narrowly

```bash
python <skill-dir>/scripts/validate_writer_handoff.py --workspace <workspace> \
  --report external_executor/writer_handoff_report.json

python <skill-dir>/scripts/apply_writer_handoff.py --workspace <workspace> \
  --report external_executor/writer_handoff_report.json
```

Validation enforces:

- report schema and status consistency;
- pending/pre-audit semantics;
- valid source IDs and artifact references;
- upstream risk, limitation, failed-run, and must-not-claim propagation;
- claim-support and counterevidence linkage;
- method/code/config and figure/table/source linkage;
- forbidden authority fields;
- T7 index consistency;
- narrow ownership.

The apply script updates only `result_pack.writer_handoff`. Root scripts remain responsible for manifest registration, final result-pack validation, executor status, and T7 routing.

## Return to the root

Return a compact child result:

```text
child_skill=writer-handoff
status=complete|partial|blocked|failed
audit_readiness=ready_for_T7_audit|partial_for_T7_audit|blocked_for_T7_audit
handoff_id=<id>
report=external_executor/writer_handoff_report.json
t7_index=external_executor/writer_handoff_t7_index.json
input_fingerprint=<sha256>
blocking_issues=<ids>
recommended_next_action=return_to_root_for_final_validation|return_to_root_with_partial_handoff|repair_handoff|stop_and_report
```

The recommendation is advisory. `research-execution` owns final validation, executor status, manifest updates, and transition to T7.

## Evidence and safety rules

- Artifact files are authoritative; summaries are navigation aids.
- Keep method definition, formal candidate, diagnostic, exploratory, failed, stale, and unsupported evidence distinct.
- Preserve counterevidence, failed trials, approximations, unavailable baselines, and claim risks.
- Every quantitative or mechanism-facing handoff item must point to source evidence.
- Never write `audited=true`, `final_claim`, `paper_ready`, `T8_ready`, `accepted`, or equivalent authority claims.
- Never create `drafts/` or `experiments/` T7/T8 outputs.
- Never relax allowed paths, schema requirements, checksum failures, claim boundaries, or must-not-claim rules.
- A complete-looking summary cannot compensate for missing provenance.

## Resource map

- `references/handoff-policy.md`: ownership, pre-audit semantics, partial/blocked behavior, and resume.
- `references/t7-consumer-contract.md`: what T7 needs to ingest, audit, method-check, novelty-check, and close claims.
- `references/evidence-and-claim-boundary.md`: claim candidates, evidence ceilings, counterevidence, and forbidden promotion.
- `references/artifact-reference-and-integrity.md`: reference form, path/checksum validation, manifest and fingerprint rules.
- `references/limitations-risks-and-negative-results.md`: propagation of limitations, risks, failures, approximations, and recovery notes.
- `references/storyline-and-summary-policy.md`: navigation summaries and pre-audit storyline suggestions.
- `references/final-validation-checklist.md`: F5 handoff-level checks before root final validation.
- `references/output-contract.md`: report, inventory, claim map, index, gate, and child-result schema.
- `scripts/preflight_handoff.py`: verify controls and prerequisites.
- `scripts/build_handoff_snapshot.py`: pin one final evidence snapshot.
- `scripts/inventory_handoff_materials.py`: classify handoff materials.
- `scripts/validate_artifact_refs.py`: verify paths, checksums, provenance, and manifest consistency.
- `scripts/build_claim_evidence_map.py`: bind claim candidates to support, counterevidence, limits, and ceilings.
- `scripts/build_t7_ingest_index.py`: produce machine-readable T7 navigation.
- `scripts/initialize_writer_handoff.py`: create the evidence-bound report envelope.
- `scripts/compute_handoff_gate.py`: derive audit readiness.
- `scripts/validate_writer_handoff.py`: enforce structure, evidence, risk propagation, and authority boundaries.
- `scripts/apply_writer_handoff.py`: atomically update only `result_pack.writer_handoff`.
