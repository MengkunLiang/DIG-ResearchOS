---
name: external-executor-bridge
description: Build and verify a ResearchOS external experiment executor handoff without running real experiments.
allowed_tools:
  - read_file
  - build_experiment_handoff_pack
  - mock_external_dry_run
  - ingest_external_results
  - audit_experiment_integrity
  - map_results_to_claims
  - build_experiment_evidence_pack
  - finish_task
allowed_read_prefixes:
  - ""
  - ideation/
  - literature/
  - external_executor/
  - experiments/
  - drafts/
allowed_write_prefixes:
  - external_executor/
  - experiments/
  - drafts/
max_steps: 24
max_tokens_total: 160000
temperature: 0.2
---

# External Executor Bridge

Use this skill when ResearchOS should prepare an experiment for Codex CLI, Claude Code, a manual executor, or a mock dry-run.

## Protocol

1. Compile handoff with `build_experiment_handoff_pack`.
2. For tests, call `mock_external_dry_run`; do not run real experiments.
3. Ingest the result pack with `ingest_external_results`.
4. Run `audit_experiment_integrity`.
5. Run `map_results_to_claims` and `build_experiment_evidence_pack`.

## Invariants

- Executor `done` is not ResearchOS `accepted`.
- Dry-run results must remain `mock_only`.
- Paper writing must consume `result_to_claim` and `experiment_evidence_pack`, not executor prose.

