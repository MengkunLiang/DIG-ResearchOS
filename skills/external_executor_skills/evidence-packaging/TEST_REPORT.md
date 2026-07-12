# Test Report

Validation date: 2026-07-11

## Static validation

```bash
python -m py_compile scripts/*.py
```

Result: passed.

## Regression tests

```text
test_end_to_end_ready_and_narrow_apply ........ passed
test_report_fingerprint_mismatch_is_blocked ... passed
test_snapshot_detects_live_mutation ........... passed
test_stale_run_is_not_active_evidence ......... passed
```

Coverage includes:

- Phase F preflight and pinned final evidence snapshot;
- live source mutation detection;
- stale formal-run exclusion;
- realized-method construction and intent delta;
- implementation fact versus controlled empirical support;
- framework figure specification and Mermaid/SVG rendering;
- result visual numeric lineage;
- evidence mappings and package manifest;
- deterministic packaging Gate;
- cross-snapshot mismatch rejection;
- narrow `result_pack.json` updates and sibling-section preservation;
- no writer-handoff or claim-approval side effects.
