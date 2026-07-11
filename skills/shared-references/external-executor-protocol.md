# External Executor Protocol

ResearchOS owns protocol generation, executor selection, ingest, integrity
audit, method audit, result-to-claim mapping, and T8 handoff. The external
executor owns implementation and experiment execution in an isolated workspace.

Required executor outputs:

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- raw results under `external_executor/raw_results/`
- configs under `external_executor/configs/`
- logs under `external_executor/logs/`
- optional patches under `external_executor/patches/`
- optional figures and tables under `external_executor/figures/` and `external_executor/tables/`

Hard boundaries:

- Do not modify ResearchOS runtime, `config/`, `drafts/`, `submission/`, or Pre-T5 source artifacts.
- Do not write final paper prose or final scientific claims.
- Do not treat executor completion as ResearchOS acceptance.
- Keep `executor_status.accepted=false`; T7 decides acceptance.

Expected launch pattern:

`Read external_executor/AGENTS.md, then execute external_executor/skills/research_execution/SKILL.md.`
