# baseline-reproduction file manifest

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Main child-skill workflow, boundaries, commands, gates, and return contract. |
| `references/reproduction-policy.md` | Phase D1 authority, lifecycle, fingerprint, and honest stop rules. |
| `references/reproduction-plan-contract.md` | Stable per-baseline execution plan schema. |
| `references/environment-and-execution-safety.md` | Command, process, environment, secret, network, and resource-limit policy. |
| `references/run-record-contract.md` | Immutable run/provenance record requirements. |
| `references/failure-and-repair-taxonomy.md` | Failure classes, allowed repairs, escalation, and repair record. |
| `references/sanity-and-comparability.md` | Outcome and comparability definitions and reference rules. |
| `references/output-contract.md` | Report and result-pack mapping, gate rules, and child return. |
| `scripts/_common.py` | Stdlib-only workspace, path, hash, environment, and JSON helpers. |
| `scripts/preflight_reproduction.py` | Verify prerequisites and active root authorization. |
| `scripts/build_reproduction_plan.py` | Build a deterministic plan scaffold from approved candidates and locked protocol. |
| `scripts/prepare_attempt.py` | Copy one candidate into an isolated immutable attempt workspace. |
| `scripts/capture_environment.py` | Capture non-secret runtime, packages, hardware, and source identity. |
| `scripts/run_reproduction.py` | Execute one authorized argv command with limits and durable run records. |
| `scripts/extract_metrics.py` | Normalize declared JSON/JSONL/CSV/regex metrics. |
| `scripts/classify_failure.py` | Produce a reviewed heuristic failure category and action. |
| `scripts/evaluate_reproduction.py` | Evaluate technical outcome and comparability under predeclared rules. |
| `scripts/initialize_reproduction_report.py` | Initialize/resume report while preserving history. |
| `scripts/compute_reproduction_gate.py` | Derive pass/partial/blocked from reviewed items. |
| `scripts/validate_reproduction_report.py` | Validate report structure and gate consistency. |
| `scripts/apply_reproduction_report.py` | Narrowly write only `result_pack.baseline_reproduction`. |
| `tests/test_baseline_reproduction_scripts.py` | Unit/integration-style tests for core helpers and narrow apply. |

No `assets/` directory is included because the Skill does not need static binary/template assets. Project-specific commands and baseline identities belong in the compiled `SKILL.md` and workspace artifacts, not reusable assets.
