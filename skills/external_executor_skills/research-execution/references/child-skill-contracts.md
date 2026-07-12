# Child Skill Contracts

Read this before dispatching a child. Child skills return control to `research-execution`; they do not call each other.

## Contract table

| Child | Required input | Owned output | Pass condition |
| --- | --- | --- | --- |
| `context-alignment` | Controls, handoff, source checks | `context_alignment` | Scope and authority are non-blocking |
| `resource-and-baseline-preparation` | Confirmed scope, acquisition policy | Requirements, inventories, reviews, readiness | Minimum-loop readiness decided |
| `experiment-design` | Readiness, claims, method intent, budget | Plan, protocol fingerprint, claim-evidence matrix | Required experiments are specified or unsupported |
| `baseline-reproduction` | Approved resource and protocol | Reproduction records, raw evidence, risks | Reproduction status and provenance are explicit |
| `method-refinement` | Method intent, plan, diagnosis | Implementation spec, delta, scope requests | Every change is classified and scoped |
| `implementation` | Approved spec and iteration plan | Code/config/patch/tests | Declared delta is implemented and testable |
| `code-and-protocol-review` | Code, config, tests, protocol | Review verdict and evidence | `pass`, `needs_fix`, or `blocked` is evidence-backed |
| `experiment-run` | Approved run level and budget | Runs, logs, metrics, checkpoints | Each run has complete status and provenance |
| `result-diagnosis` | New usable runs | Per-iteration diagnosis | Findings cite evidence and confidence |
| `module-attribution` | Diagnosis and controlled evidence | Per-iteration attribution | Attribution basis is explicit |
| `evidence-packaging` | Pinned final snapshot | Realized method, framework, figure/table inventory | Package maps to code and evidence |
| `writer-handoff` | Valid package and risks | Writer handoff, validation | Handoff is ready for T7 audit |

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

## Failure return

A blocked or failed child still returns a typed result, logs, partial outputs, and recovery guidance. Missing evidence is represented explicitly. The root then updates state and chooses stop, repair, or human review.
