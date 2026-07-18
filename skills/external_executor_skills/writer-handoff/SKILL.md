---
name: writer-handoff
description: Compile the final external-executor state into `executor_research_report.md` and validate the complete ResearchOS writing handoff. Use after evidence-packaging and all experiment skills finish, when final terminal `executor_status.json`, `result_pack.json`, `report/run_manifest.json`, and assets under `figure/` and `table/` are ready. Build a source-bound research fact file, produce the eight-section academic report consumed by ResearchOS T8, and verify the four core files plus every final figure and table. Do not run or reinterpret experiments, modify final executor state or result data, update the manifest, write manuscript sections, invent citations or values, hide negative results, or make final paper-claim decisions.
---

# Writer Handoff

Convert what actually happened in the external execution environment into one ResearchOS-auditable research fact package. The primary downstream document is `external_executor/executor_research_report.md`. `result_pack.json`, the manifest, raw artifacts, figures, and tables remain authoritative sources that T8 can inspect.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish the final boundary

Locate the nearest directory containing `project.yaml` and `external_executor/`; call it `<workspace>`. Treat the directory containing this file as `<skill-dir>`.

Read before writing:

- `external_executor/AGENTS.md`;
- `external_executor/allowed_paths.txt`;
- `external_executor/handoff_pack.json`;
- `external_executor/expected_outputs_schema.json`;
- `external_executor/executor_status.json`;
- `external_executor/result_pack.json`;
- `external_executor/report/run_manifest.json`;
- every file under `external_executor/figure/` and `external_executor/table/`;
- `references/handoff-policy.md`;
- `references/research-report-contract.md`;
- `references/academic-writing-policy.md`;
- `references/final-validation-contract.md`;
- `references/output-contract.md`.

The root must record its intended terminal outcome in both status files before dispatch. Writer Handoff validates that outcome but does not change it.

Write only:

```text
external_executor/executor_research_report.md
external_executor/report/writer_handoff_preflight.json
external_executor/report/writer_handoff_snapshot.json
external_executor/report/writer_handoff_facts.json
external_executor/report/writer_handoff_validation.json
```

Do not write `result_pack.writer_handoff`, a T7 ingest index, a parallel claim-map file, manuscript drafts, or new experiment artifacts. The report is derived from the already-final result pack; it must not mutate that source after snapshotting.

## Check prerequisites

Run:

```bash
python <skill-dir>/scripts/preflight_handoff.py --workspace <workspace>
```

Block when a core control or required JSON file is missing, malformed, outside the allowed write boundary, or has an unsupported major schema. Missing figures, tables, or scientific evidence may produce a constrained report, but the omission must remain explicit.

## Pin the final inputs

Run:

```bash
python <skill-dir>/scripts/build_handoff_snapshot.py --workspace <workspace>
```

The snapshot binds the full values and checksums of:

- `executor_status.json`;
- `result_pack.json`;
- `report/run_manifest.json`;
- `handoff_pack.json` and `expected_outputs_schema.json`;
- every final file recursively found under `figure/` and `table/`.

Do not continue from a stale snapshot. A change to any bound input requires rebuilding facts and the report.

## Build source-bound report facts

Run:

```bash
python <skill-dir>/scripts/build_research_report_facts.py --workspace <workspace>
```

The facts file must organize, without inventing missing fields:

1. the research question, formal hypotheses, expected contributions, completed work, and explicit plan changes;
2. the realized method, modules, code entry points, configuration, environment, dependencies, data flow, design deltas, and incomplete work;
3. every planned or executed experiment and its actual runs;
4. every structured main, ablation, robustness, efficiency, and diagnostic result recovered from final result tables;
5. preliminary claim-to-experiment/file mappings and their limitations;
6. only executor-added literature records with a verifiable identifier and recorded support scope;
7. limitations, failures, open risks, confounds, compute/data restrictions, and must-not-claim boundaries;
8. a path and checksum index assembled from the manifest, result pack, figures, and tables.

Use `external_executor/table/main_comparison.csv`, `ablation_results.csv`, and `other_experiments.csv` as the preferred numeric summaries. Preserve their raw source-file references. Do not manually transcribe a number from a plot, log screenshot, prose summary, or favorable subset.

## Render the executor research report

Run:

```bash
python <skill-dir>/scripts/render_executor_research_report.py --workspace <workspace>
```

The report must contain exactly these major sections:

```text
1. Project Summary
2. Implementation Summary
3. Experiment Inventory
4. Comprehensive Results
5. Claim Support Table
6. Verified Literature Additions
7. Limitations and Open Issues
8. Artifact Index
```

The deterministic renderer supplies a complete source-bound draft. Inspect it against `writer_handoff_facts.json` and improve only prose clarity when needed. Do not add, remove, round, combine, or reinterpret values; do not replace paths; do not omit an unfavorable experiment; and do not add a citation that is absent from verified facts.

The Experiment Inventory must retain all required columns from `research-report-contract.md`. Comprehensive Results must cover every structured completed result, not only favorable rows. Each result must state the values, comparator, setting, statistical-test status, raw source, figure/table source, supported scope, and unsupported scope.

The Claim Support Table remains preliminary. Use `Supported candidate`, `Partially supported candidate`, or `Unsupported`; never write that the external executor finally accepted or proved a paper claim. T8 owns final claim adjudication.

## Enforce academic language

Follow `references/academic-writing-policy.md`.

Use connected academic paragraphs for motivation, method, setup, interpretation, and boundaries. Use tables where exact repeated fields improve auditability. Avoid fragmented micro-sections, em-dash rhetoric, colon-driven prose, slogans, unsupported significance language, promotional adjectives, and formulae that cannot be verified from the realized method and code.

Explain a technical term at first use. Define every symbol if a formula is necessary. Use concrete examples only to clarify a formal concept, never as evidence. Write `Not recorded` where a fact cannot be resolved.

## Validate the complete handoff

Run:

```bash
python <skill-dir>/scripts/validate_writer_handoff.py --workspace <workspace>
```

Writer Handoff itself must validate:

- `executor_status.json` is terminal and consistent with `result_pack.json`;
- `result_pack.json` is parseable and contains the final scientific sections appropriate to its outcome;
- every manifest path is workspace-contained, exists, and matches its declared hash and size;
- every final figure and table is nonempty, unchanged since snapshot, and registered in the manifest;
- `executor_research_report.md` is nonempty, contains all eight sections and required tables, includes every experiment/result/claim record, and preserves every source path;
- all quantitative result records have raw-result paths;
- every executor-added reference has a verifiable identifier and appears with its support/access boundary;
- forbidden authority or promotional phrasing is absent;
- all report paths resolve to real workspace artifacts;
- snapshot, facts, report, core files, and assets remain mutually consistent.

Validation statuses are:

- `ready` when the terminal execution is completed and no defect remains;
- `partial` when a non-completed terminal execution still yields a coherent, fully disclosed report, or only non-blocking coverage warnings remain;
- `blocked` when core identity, status, path, checksum, report coverage, or factual traceability is invalid.

Repair a blocked report from its authoritative source and rerun snapshot, fact compilation, rendering, and validation. Do not edit validation output to obtain a pass.

## Return to the root

Return:

```text
child_skill=writer-handoff
status=complete|partial|blocked|failed
handoff_readiness=ready|partial|blocked
report=external_executor/executor_research_report.md
validation=external_executor/report/writer_handoff_validation.json
input_fingerprint=<sha256>
blocking_issues=<ids>
recommended_next_action=handoff_complete|handoff_complete_with_constraints|repair_writer_handoff
```

The root records the child result and stops. It does not run a second final-output validator. ResearchOS runtime may still perform its independent ingestion gate after the external executor returns.

## Resource map

- `references/handoff-policy.md`: ownership, final-state semantics, staleness, and downstream authority.
- `references/research-report-contract.md`: required report content and experiment/result/claim fields.
- `references/academic-writing-policy.md`: language, structure, formula, terminology, and citation requirements.
- `references/final-validation-contract.md`: the six-surface final validation rules.
- `references/output-contract.md`: exact file ownership and schemas.
- `scripts/preflight_handoff.py`: check controls, final inputs, directories, and writes.
- `scripts/build_handoff_snapshot.py`: pin core files and all final figures/tables.
- `scripts/build_research_report_facts.py`: normalize source-bound report facts.
- `scripts/render_executor_research_report.py`: render the eight-section Markdown report.
- `scripts/validate_writer_handoff.py`: validate the four final files plus figure/table assets.
