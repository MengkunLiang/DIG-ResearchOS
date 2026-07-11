# External Result Pack Contract

`external_executor/result_pack.json` is the only external-executor result source
that ResearchOS T7 may ingest. It must be written last, after raw artifacts,
configs, logs, figures, tables, and `run_manifest.json` exist.

Required top-level fields:

- `schema_version`
- `semantics`
- `run_id`
- `executor`
- `dry_run`
- `mock_only`
- `executor_status`
- `context_alignment`
- `resources`
- `baseline_reproduction`
- `experiment_runs`
- `metrics`
- `artifacts`
- `baseline_coverage`
- `result_diagnosis`
- `module_attribution`
- `realized_method_package`
- `final_framework_figure`
- `figure_table_inventory`
- `writer_handoff`
- `run_manifest`

Common enum values:

- `executor_status`: `completed`, `partial`, `blocked`, `failed`
- `review_status`: `pass`, `needs_fix`, `blocked`
- `run_type`: `smoke`, `small_scale`, `formal`, `ablation`, `robustness`, `diagnostic`
- `evidence_level`: `raw_result`, `audited_result`, `diagnostic_hint`, `method_definition`, `abstract_only`, `unsupported`
- `claim_strength`: `strong`, `moderate`, `weak`, `unsupported`
- `contribution_drift`: `none`, `minor`, `major`
- `required_action`: `none`, `update_method`, `rerun_experiment`, `rerun_novelty`, `human_review`, `narrow_claim`

Artifact references must include the relative path, role, kind, existence status,
and sha256 when the file exists. Every metric must point to a raw artifact and
must include run id, seed, config path, log path, and metric direction when known.
