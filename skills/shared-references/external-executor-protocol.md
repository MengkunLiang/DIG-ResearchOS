# External Executor Protocol

ResearchOS owns the experiment protocol, evidence contract, executor selection, ingest, integrity audit, result-to-claim, and writing handoff.

External executors own implementation and execution in an isolated workspace. They must write:

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- raw results under `external_executor/raw_results/`
- configs under `external_executor/configs/`
- logs under `external_executor/logs/`

Executor `done` never means ResearchOS `accepted`.

