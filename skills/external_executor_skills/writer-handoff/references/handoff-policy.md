# Writer Handoff Policy

## Purpose

Compile one stable external-executor evidence snapshot for T7. This skill is a transfer and validation boundary, not a writer and not an auditor.

## Ownership

`writer-handoff` owns only its preflight, snapshot, inventory, claim map, T7 index, integrity report, handoff report, and the `result_pack.writer_handoff` section. The root owns final workflow validation, executor status, manifest registration, and routing. T7 owns evidence ingest/audit and claim closure. T8 owns manuscript writing from T7-approved resources.

## Preconditions

A root-selected evidence-package checkpoint should exist. `realized_method_package`, `final_framework_figure`, and `figure_table_inventory` may be complete, partial, missing, blocked, or unavailable, but their status must be explicit. A blocked or failed experiment process still receives a best-effort handoff when the result pack is interpretable.

## Pre-audit semantics

The strongest success label is `ready_for_T7_audit`. It means the package is internally consistent enough for T7 to inspect. It never means:

- evidence audited;
- claim accepted;
- paper ready;
- novelty rechecked;
- framework figure approved;
- T8 authorized.

## Partial and blocked behavior

Use `partial_for_T7_audit` when T7 can ingest useful evidence with restrictions. Use `blocked_for_T7_audit` only when core identity, schema, path, or integrity failure prevents safe ingestion. Do not use blocking merely because results are weak or negative.

## Resume and staleness

The snapshot fingerprint binds upstream package, runs, diagnosis, attribution, figures/tables, risk state, manifest, and handoff contract. Any relevant upstream change makes the handoff stale. Preserve old handoffs through root history/manifest behavior; the narrow apply section contains the current handoff only.
