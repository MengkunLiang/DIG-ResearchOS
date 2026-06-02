# Result Schema

External result packs should include:

- `semantics: external_executor_result_pack`
- `run_id`
- `executor`
- `dry_run`
- `mock_only`
- `metrics[]`
- `artifacts[]`
- `run_manifest`
- `logs[]`

Each metric needs `metric_id`, `name`, `value`, and `source_artifact`.

