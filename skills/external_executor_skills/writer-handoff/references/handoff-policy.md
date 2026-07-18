# Writer Handoff Policy

## Purpose

Translate final external-executor state into one factual research report that ResearchOS can audit and T8 can use without hidden conversation history.

## Ownership

The root owns execution routing, iteration decisions, budgets, manifest updates, and the terminal status decision. Before dispatching Writer Handoff, it records the same intended terminal outcome in `executor_status.json` and `result_pack.json`.

Writer Handoff owns only `executor_research_report.md` and its preflight, snapshot, facts, and validation files under `external_executor/report/`. It validates but never changes final status, result data, the manifest, figures, tables, code, configs, runs, diagnoses, attribution, or evidence packages.

ResearchOS runtime owns independent post-return ingestion. T8 owns final Claim wording, evidence adjudication, narrative planning, and manuscript writing.

## Source priority

Use this priority when records disagree:

1. existing files and checksums;
2. `report/run_manifest.json` registrations;
3. structured run records and final result tables;
4. realized method, diagnosis, attribution, and claim-boundary sections in `result_pack.json`;
5. narrative summaries only as navigation.

Never resolve a conflict by choosing the more favorable value.

## Staleness

The snapshot binds all core files and every final figure/table. Any change after snapshot invalidates the report. Rebuild from the changed authoritative input rather than editing the report around the mismatch.

## Partial outcomes

A partial, blocked, or failed execution still receives a report when its state is parseable. Missing work, failed runs, weak results, absent literature additions, and unsupported claims remain visible. Use blocking only when the package cannot be trusted or traced.
