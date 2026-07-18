---
name: evidence-packaging
description: Compile one pinned final ResearchOS external-executor evidence snapshot into a realized method package, an evidence-bound final framework figure, a reproducible result figure/table inventory, and bidirectional method/code/config/result/visual mappings. Use when `research-execution` has stopped the build-review-run loop, when Phase F1-F3 artifacts are missing or stale, or when valid partial evidence must be packaged after completion, budget exhaustion, blocking, or failure. Do not approve paper claims, write the paper, perform T7 audit, replace missing visuals with fabricated placeholders, mix evidence from different protocol or iteration snapshots, or change code, experiments, attribution, scope, budgets, executor status, or global manifest state.
---

# Evidence Packaging

Compile the experiment loop's final factual state into one internally consistent pre-audit package. Method definition, framework figure, result visuals, and evidence mappings must all derive from the same pinned snapshot.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before any write:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/handoff_pack.json`;
   - `<workspace>/external_executor/expected_outputs_schema.json`;
   - `<workspace>/external_executor/executor_status.json`;
   - `<workspace>/external_executor/report/run_manifest.json`;
   - `<workspace>/external_executor/result_pack.json`;
   - `<skill-dir>/references/snapshot-and-staleness-policy.md`;
   - `<skill-dir>/references/evidence-level-policy.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Return `blocked` when control files are missing or malformed, path ownership cannot be determined, an unsupported major schema is required, or no coherent snapshot can be pinned.

Write only:

```text
external_executor/evidence_package/realized_method_package.json
external_executor/figure/*.svg
external_executor/figure/*.png
external_executor/table/*.csv
external_executor/table/*.tsv
external_executor/report/evidence_packaging_*
external_executor/report/final_evidence_snapshot*.json
external_executor/report/framework_figure_spec.json
external_executor/report/framework_figure.mmd
external_executor/report/result_table_build_report.json
external_executor/report/result_figure_build_report.json
external_executor/report/figure_table_inventory.json
external_executor/report/evidence_mapping.json
external_executor/report/evidence_package_manifest.json
result_pack.json#realized_method_package
result_pack.json#framework_figure
result_pack.json#figure_table_inventory
result_pack.json#evidence_mapping
result_pack.json#evidence_packaging
```

Use the narrow apply script for `result_pack.json`. Do not update executor status, global manifest entries, iteration decisions, claim approval, writer handoff, T7/T8 artifacts, source code, config, raw results, or sibling-owned sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_evidence_packaging.py --workspace <workspace> \
  --output external_executor/report/evidence_packaging_preflight.json
```

Packaging is allowed after the root stops the loop, routes to Phase F, or records an honest terminal/stop state. Phase F must make the best possible package under `completed`, `partial`, `blocked`, or `failed`; those states do not authorize invented method facts, results, figures, paths, or evidence.

Inspect warnings as package constraints. A missing valid formal result can produce a partial or unavailable method/visual package, but does not excuse omitting failure state, stale evidence, risks, or open gaps.

## Pin one final evidence snapshot

Read `references/snapshot-and-staleness-policy.md`, then run:

```bash
python <skill-dir>/scripts/build_evidence_snapshot.py --workspace <workspace> \
  --output external_executor/report/final_evidence_snapshot.json

python <skill-dir>/scripts/validate_evidence_snapshot.py --workspace <workspace> \
  --snapshot external_executor/report/final_evidence_snapshot.json
```

The snapshot must bind:

- selected `result_pack` sections and their canonical hashes;
- active formal records;
- stale, failed, superseded, smoke, small-scale, or unusable history;
- active protocol fingerprint;
- run-manifest artifact paths and checksums;
- executor state and final iteration decision context.
- the one `implementations.active_implementation_id` record;
- the method refinement whose `spec_fingerprint` matches that implementation;
- the immutable method-spec snapshot file, content hash, and parsed value;
- the review, diagnosis, attribution, and experiment runs selected for the same final iteration and implementation.

Do not continue if the snapshot source changes while packaging. Rebuild the snapshot instead. Every F1-F3 output must carry exactly the same `snapshot_id` and `snapshot_fingerprint`.

## Build the realized method package

Read `references/realized-method-contract.md`, then run:

```bash
python <skill-dir>/scripts/build_realized_method_package.py --workspace <workspace> \
  --snapshot external_executor/report/final_evidence_snapshot.json \
  --output external_executor/evidence_package/realized_method_package.json
```

The generated file is a deterministic, final-version scaffold. It reads selected result-pack values and the selected method specification from `final_evidence_snapshot.json`; it must not merge historical implementations or reread live result-pack sections. Semantic inspection may clarify summaries, but it must not change `final_version`, source validation, fingerprints, or evidence status without rebuilding the snapshot.

Resolve final lineage in this order:

1. select `result_pack.implementations.active_implementation_id`;
2. select the `method_refinements` record matching its `method_spec_fingerprint`;
3. follow the refinement `snapshot_ref` and verify the spec fingerprint and file hash;
4. select review, diagnosis, attribution, decision, and experiment runs for that implementation/iteration;
5. reject ambiguous selection instead of merging multiple implementation records.

Read the final attribution report selected by `result_pack.module_attributions.current_by_iteration`, then expand its nested `module_attributions.items`. Do not treat the report envelope as one module record, and do not merge an earlier iteration's attribution into the final realized method.

The package must distinguish:

```text
method definition
empirical support
causal/mechanism support
unsupported or unresolved mechanism
```

For each implemented, added, or modified module record:

- stable module ID and actual role;
- actual input and output semantics;
- code path or symbol reference;
- config key or config path;
- implementation evidence;
- empirical-support status and evidence type;
- attribution confidence and limitations.

Use only `direct_ablation` or `controlled_diagnostic` as controlled mechanism support. `implementation_fact` proves existence, not contribution. `correlational_hint` must not be rewritten as causal support.

Record final method name, one-sentence method, actual core mechanism, actual algorithm flow, losses, implemented/dropped/added modules, supported/unsupported mechanisms, claim boundary, and `delta_from_method_intent`. A module that exists in code but lacks experimental support remains part of the method definition and must not become an empirically supported contribution by wording.

Also preserve `final_version`, separate `training_flow` and `inference_flow`, system/data interfaces, symbol table/pseudocode when available, configuration contract, ablation controls, method evolution across iterations, final-run binding, reproducibility requirements, evidence traceability, and the complete selected attribution sections. A loss is realized only when its implementation reference exists in the final implementation worktree.

Set realized method status honestly:

- `complete`: final version identity, reviewed implementation, protocol, training/inference flow, realized losses, modules, code/config mappings, attribution, evidence traceability, and claim boundaries are complete and source validation passes;
- `partial`: a usable method definition exists but some mappings or support assessments are unresolved;
- `unavailable`: actual implemented method cannot be reconstructed reliably.

## Design and render the final framework figure

Read `references/framework-figure-contract.md` and `references/visual-traceability-policy.md`, then run:

```bash
python <skill-dir>/scripts/build_framework_figure_spec.py --workspace <workspace>
```

Review the spec before rendering. It must contain:

- panel purpose;
- implemented nodes only;
- code/config/evidence mapping per node;
- actual edges from the realized algorithm flow;
- neutral visual treatment for implementation-only modules;
- controlled emphasis only for supported mechanisms;
- caption draft;
- `must_not_show` entries for dropped, unimplemented, unsupported, stale, or scope-excluded content.

Use these states:

```text
ready_for_T7_audit
missing
blocked
```

Never create placeholder paths or an illustrative architecture that is not grounded in actual code. When the spec is `ready_for_T7_audit`, render editable and viewable assets:

```bash
python <skill-dir>/scripts/render_framework_figure.py --workspace <workspace> \
  --write-back
```

The renderer writes the final SVG to `external_executor/figure/framework_figure.svg` and the editable Mermaid source to `external_executor/report/framework_figure.mmd`. The SVG must reflect the final code/module structure, attribution status, and claim boundary. Project-specific redesign is allowed only if the same node, edge, evidence, and `must_not_show` contract remains intact.

## Generate result tables and figures

Build deterministic tables directly from snapshot-pinned structured files in `external_executor/raw_results/`. Skip unmanifested files and files whose current checksum differs from `final_evidence_snapshot.json`; record the reason instead of incorporating post-snapshot values:

```bash
python <skill-dir>/scripts/build_result_tables.py --workspace <workspace>
python <skill-dir>/scripts/render_result_figures.py --workspace <workspace>
```

The table builder accepts CSV, TSV, JSON, and JSONL raw results, enriches them with pinned run metadata, and writes when data exists:

```text
external_executor/table/all_results.csv
external_executor/table/main_comparison.csv
external_executor/table/ablation_results.csv
external_executor/table/other_experiments.csv
```

`main_comparison.csv` aggregates ours and baselines by protocol, dataset, split, metric, method, and repeat. `ablation_results.csv` preserves variant identity. `other_experiments.csv` covers robustness, efficiency, diagnostic, small-scale, and other non-main runs. Unknown metric direction remains `unknown`; never infer it from observed values.

The figure renderer reads only generated aggregate tables and creates one SVG per dataset/split/metric/direction/protocol in `external_executor/figure/`. Never mix different metrics, directions, or protocol fingerprints on one axis. Main-result plots require both ours and baseline records under the same protocol; ablation plots require at least two variants. On rerun, remove only the renderer-owned `main_*.svg`, `ablation_*.svg`, and `other_*.svg` outputs before regeneration so stale plots cannot survive. Missing comparable data produces a report warning, not a fabricated plot.

## Build the result figure/table inventory

Read `references/figure-table-inventory-contract.md` and `references/visual-traceability-policy.md`, then run:

```bash
python <skill-dir>/scripts/build_figure_table_inventory.py --workspace <workspace>
```

After table and figure generation, build the inventory. For each result figure or table record:

- artifact ID, kind, status, evidence layer, and claim candidate IDs;
- source result and structured source-data references;
- config, raw log, metric-output, and protocol references;
- plot/render script;
- editable source and rendered files;
- caption draft and `must_not_imply` boundary;
- numeric traceability status.

A result visual is `ready_for_T7_audit` only when it can be regenerated from structured results or a source table and all required provenance is present. A rendered image alone is not traceable evidence. Preserve stale visuals as history, but exclude them from active claim support.

Required visuals that are absent remain explicit `missing` inventory entries. Do not fabricate numbers, charts, tables, captions, source files, or plot scripts merely to make the package look complete.

## Build bidirectional evidence mappings

Read `references/evidence-mapping-contract.md`, then run:

```bash
python <skill-dir>/scripts/build_evidence_mapping.py --workspace <workspace>
```

Map both directions:

```text
realized module
  ↔ code path / config key
  ↔ implementation and attribution evidence
  ↔ framework node
  ↔ result visual
  ↔ pre-T7 claim candidate
```

and:

```text
figure/table
  ↔ source result / source data / metric output
  ↔ config / log / plot script / rendered file
  ↔ protocol fingerprint
  ↔ claim candidate
```

Claim mappings are navigation aids for T7. Do not set paper-level `supported`, `approved`, or `publishable` verdicts here.

## Build the package manifest

Run:

```bash
python <skill-dir>/scripts/build_package_manifest.py --workspace <workspace>
```

The manifest aggregates package files with identity, checksums, roles, and explicit relationships. It follows lightweight Research Object principles but does not copy large raw artifacts or remote resources automatically. Root manifest registration remains the responsibility of `research-execution`.

## Review the package independently

Read `references/packaging-review-checklist.md`. When independent workers are supported, use a reviewer that reads the pinned snapshot, implementation code, configs, raw run records, attribution, package files, and rendered visuals directly. Otherwise perform a separate sequential review after generation.

Review at least:

- all F1-F3 files share one snapshot fingerprint;
- stale/failed/smoke evidence is not promoted;
- actual method does not silently revert to `method_intent`;
- implemented modules have code and config mappings;
- empirical support is not inferred from implementation;
- framework nodes and edges match realized code/flow;
- `must_not_show` is enforced;
- every ready result visual has numeric provenance;
- captions do not overstate causal or claim support;
- missing work is represented as missing, partial, blocked, or unavailable;
- claim approval remains deferred to T7.

## Compute the gate and assemble the report

Run:

```bash
python <skill-dir>/scripts/compute_packaging_gate.py --workspace <workspace>

python <skill-dir>/scripts/assemble_evidence_packaging_report.py --workspace <workspace>

python <skill-dir>/scripts/validate_evidence_packaging_report.py --workspace <workspace>
```

Gate outcomes:

- `ready`: one valid snapshot; complete realized method; traceable framework source/render; complete mappings; no required main visual gap or blocking provenance defect;
- `partial`: a coherent useful package exists, but method details, framework figure, optional/main visuals, or non-blocking provenance remain incomplete;
- `blocked`: snapshot mismatch, source mutation, checksum failure, unavailable realized method, invalid mapping, fabricated/forbidden visual content, or incoherent provenance prevents safe handoff.

The child can return `status=complete` only when packaging readiness is `ready`. A successfully executed best-effort package may return `status=partial` with readiness `partial`. Reserve `failed` for unrecoverable Skill execution errors, not ordinary missing research evidence.

## Apply narrowly and return to the root

Run:

```bash
python <skill-dir>/scripts/apply_evidence_packaging_report.py --workspace <workspace>
```

The apply script updates only the five owned `result_pack` sections. Then return:

```text
child_skill=evidence-packaging
status=complete|partial|blocked|failed
packaging_readiness=ready|partial|blocked
snapshot_id=<id>
snapshot_fingerprint=<sha256>
report=external_executor/report/evidence_packaging_report.json
realized_method=external_executor/evidence_package/realized_method_package.json
framework_figure=external_executor/figure/framework_figure.svg
figure_table_inventory=external_executor/report/figure_table_inventory.json
evidence_mapping=external_executor/report/evidence_mapping.json
blocking_issues=<ids>
constraints=<ids>
recommended_next_action=continue_to_writer_handoff|continue_to_writer_handoff_with_constraints|repair_package_or_return_to_root
```

The recommendation is advisory. `research-execution` owns checkpointing, global manifest registration, final status, and the next dispatch.

## Evidence and safety rules

- Artifact files, code, config, run records, metric outputs, and checksums are facts; summaries are navigation aids.
- Keep method definition, implementation fact, empirical support, causal support, claim candidate, and T7-approved claim separate.
- Keep formal, diagnostic, exploratory, smoke, small-scale, stale, failed, and unusable evidence distinct.
- Never manually alter a plotted number without updating its structured source and regeneration path.
- Never create a fake editable source, rendered path, checksum, module, edge, run, plot script, result, or caption.
- Never include dropped or unimplemented modules in the final architecture.
- Preserve negative, null, failed, and limited evidence when it affects interpretation.
- Do not write into `drafts/`, `experiments/`, `submission/`, `researchos/`, or `config/`.
- Do not perform T7 audit or T8 writing. Every output remains `pre_T7_audit_only`.

## Resource map

- `references/snapshot-and-staleness-policy.md`: final evidence selection, fingerprinting, stale evidence, and source mutation rules.
- `references/evidence-level-policy.md`: definition, empirical, causal, diagnostic, exploratory, and claim-candidate semantics.
- `references/realized-method-contract.md`: realized method fields, module mappings, intent delta, and status rules.
- `references/framework-figure-contract.md`: panel/node/edge/caption/must-not-show contract and figure status.
- `references/figure-table-inventory-contract.md`: result visual schema, status, evidence layers, and missing-item behavior.
- `references/visual-traceability-policy.md`: source-result-to-number-to-render lineage and no-manual-edit rules.
- `references/evidence-mapping-contract.md`: bidirectional method/code/config/result/visual/claim mapping.
- `references/packaging-review-checklist.md`: independent review and gate checklist.
- `references/output-contract.md`: report envelope, owned result-pack sections, and child return.
- `scripts/preflight_evidence_packaging.py`: validate Phase F dispatch, controls, and write boundaries.
- `scripts/build_evidence_snapshot.py`: freeze selected sections, formal records, history, artifacts, and protocol.
- `scripts/validate_evidence_snapshot.py`: detect changed sections, bad checksums, and cross-protocol evidence.
- `scripts/build_realized_method_package.py`: scaffold the final implemented method and intent delta.
- `scripts/build_framework_figure_spec.py`: create an evidence-bound final architecture specification.
- `scripts/render_framework_figure.py`: render conservative Mermaid and SVG assets when the spec is ready.
- `scripts/build_result_tables.py`: normalize raw results and generate main, ablation, and other experiment tables.
- `scripts/render_result_figures.py`: render per-dataset/per-metric SVGs from generated tables.
- `scripts/build_figure_table_inventory.py`: inventory existing and required result visuals with provenance.
- `scripts/build_evidence_mapping.py`: create bidirectional mappings.
- `scripts/build_package_manifest.py`: aggregate package identities, checksums, roles, and relations.
- `scripts/compute_packaging_gate.py`: derive ready/partial/blocked status.
- `scripts/assemble_evidence_packaging_report.py`: assemble the durable child report.
- `scripts/validate_evidence_packaging_report.py`: enforce snapshot, method, visual, mapping, and claim-boundary rules.
- `scripts/apply_evidence_packaging_report.py`: atomically update only evidence-packaging-owned result-pack sections.
