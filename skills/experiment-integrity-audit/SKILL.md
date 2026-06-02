---
name: experiment-integrity-audit
description: Audit external experiment evidence for traceability, mock contamination, missing metrics, and artifact integrity.
allowed_tools:
  - read_file
  - audit_experiment_integrity
  - finish_task
allowed_read_prefixes:
  - experiments/
  - external_executor/
allowed_write_prefixes:
  - experiments/
max_steps: 10
max_tokens_total: 80000
temperature: 0.2
---

# Experiment Integrity Audit

Run `audit_experiment_integrity` over ingested external results.

The audit checks mechanical evidence integrity: metric presence, source artifacts, file existence, hash mismatch, dry-run contamination, and run manifest semantics. It does not decide whether a research idea is scientifically strong.

