# Child Skill Contracts

Read this before dispatching a child. Child skills return control to `research-execution`; they do not call each other.

## Contract table

| Child | Required input | Owned output | Pass condition |
| --- | --- | --- | --- |
| `context-alignment` | Controls, handoff, source checks | `context_alignment` | Scope and authority are non-blocking |
| `resource-and-baseline-preparation` | Confirmed scope, acquisition policy | Requirements, inventories, reviews, readiness | Minimum-loop readiness decided |
| `experiment-design` | Readiness, claims, method intent, budget | Plan, protocol fingerprint, claim-evidence matrix | Required experiments are specified or unsupported |
| `baseline-reproduction` | Approved resource and protocol | Baseline deployment in `external_executor/expr/`, raw evidence in `external_executor/raw_results/`, risks | Reproduction status and provenance are explicit |
| `method-refinement` | Method intent, plan, diagnosis | Implementation spec, delta, scope requests | Every change is classified and scoped |
| `implementation` | Approved spec and iteration plan | Code/config/patch/tests deployed under `external_executor/expr/` | Declared delta is implemented and testable |
| `code-and-protocol-review` | Code, config, tests, protocol | Review verdict and evidence | `pass`, `needs_fix`, or `blocked` is evidence-backed |
| `experiment-run` | Approved run level and budget | Runs from `external_executor/expr/`; logs, metrics, records, checkpoints in `external_executor/raw_results/` | Each run has complete status and provenance |
| `result-diagnosis` | New terminal runs, including failed/unusable attempts, plus reviewed baseline reproductions | Per-iteration diagnosis, baseline performance summary, method-change assessment | Findings cite evidence/confidence and give a concrete repair/refinement surface when needed |
| `module-attribution` | Diagnosis and controlled evidence | Per-iteration attribution | Attribution basis is explicit |
| `evidence-packaging` | Pinned final snapshot | Realized method, framework, figure/table inventory | Package maps to code and evidence |
| `writer-handoff` | Terminal status/result pack, frozen manifest, valid evidence package, final figures/tables | `executor_research_report.md`, fact snapshot, complete handoff validation | Four core files and all final figures/tables are coherent; control returns to the root for `launch-t8` |

## Dispatch envelope

Before execution, the root records:

```json
{
  "dispatch_id": "",
  "child_skill": "",
  "input_fingerprint": "",
  "owned_sections": [],
  "required_outputs": [],
  "budget": {},
  "started_at": ""
}
```

After execution, require:

```json
{
  "dispatch_id": "",
  "child_skill": "",
  "status": "complete | partial | blocked | failed",
  "outputs": [],
  "evidence_refs": [],
  "blocking_issues": [],
  "recommended_next_action": "",
  "finished_at": ""
}
```

The recommended action is advisory. The root validates artifacts and decides the route.

## Narrow-write rule

- Children may append records to their owned arrays and update their owned summary.
- Children preserve unknown fields and sibling sections.
- A reviewer writes findings; it does not silently repair Builder output.
- A runner writes raw execution records; it does not reinterpret claims.
- A diagnosis/attribution skill reads raw evidence but does not alter it.
- Packaging creates derived artifacts from a pinned snapshot and records the source fingerprint.
- Writer Handoff validates final core state and presentation assets but does not modify them or write a result-pack section.

## Failure return

A blocked or failed child still returns a typed result, logs, partial outputs, and recovery guidance. Missing evidence is represented explicitly. The root then updates state and chooses stop, repair, or human review.
