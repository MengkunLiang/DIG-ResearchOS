# Resumable External Runs

External runs must be resumable from artifacts:

- `executor_status.json` records current executor-side status.
- `heartbeat.json` records recent progress.
- `run_manifest.json` records raw results, configs, logs, and artifact hashes.
- ResearchOS ingest/audit decides acceptance.

If a run stops mid-chain, resume should inspect files on disk rather than regenerate completed artifacts.

