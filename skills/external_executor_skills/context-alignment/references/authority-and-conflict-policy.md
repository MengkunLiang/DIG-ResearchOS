# Authority and Conflict Policy

Use authority by information domain. A file authoritative for paths is not automatically authoritative for research semantics.

## Authority matrix

| Domain | Primary control | Cross-check | Rule |
| --- | --- | --- | --- |
| Writable/forbidden paths | `AGENTS.md`, `allowed_paths.txt` | handoff path claims | Apply the stricter control; broader handoff permission is a blocker |
| Output shape/version | `expected_outputs_schema.json` | handoff schema claims | Unsupported major version blocks |
| Compiled execution context | `handoff_pack.json#context_reboost` | Pre-T5 sources | Use as executable summary only after cross-check |
| Initial method constraints | `handoff_pack.json#method_intent` | hypotheses, exp plan, novelty audit | Material disagreement blocks; do not silently choose |
| Required baselines/novelty boundary | novelty audit plus compiled handoff | exp plan, literature evidence | Omission or semantic replacement is material |
| Experiment protocol | exp plan plus compiled handoff | hypotheses, baseline matrix | Differences affecting fairness/claims are material |
| Central hypothesis | hypotheses plus compiled handoff | scorecard, synthesis | Core-mechanism disagreement blocks |
| Acquisition authority | compiled acquisition policy, ResearchOS default policy, and runtime controls | declared capabilities | Missing legacy policy uses the ResearchOS default; contradictory policy blocks |
| Budget/stop conditions | compiled handoff/root state | executor capabilities | Expansion requires root/human authority |

## Mismatch severity

```text
info       clarification with no execution or claim effect
warning    non-blocking ambiguity or omission with an authorized conservative resolution
material   affects baseline, benchmark, protocol, scope, claim, novelty, or minimum loop
blocking   affects authority, safety, unsupported schema, or irreconcilable core semantics
```

An unresolved `material` mismatch makes the alignment status `blocked`. `warning` is non-blocking only when its resolution and downstream constraint are explicit.

## Resolution status

```text
confirmed_same
accepted_compiled_value
accepted_stricter_control
recorded_constraint
requires_human_review
unresolved
```

- `accepted_compiled_value` is allowed only for non-material gaps where the compiled value is explicit and no source contradicts it.
- `accepted_stricter_control` applies to permissions/safety; it may still block the minimum loop.
- `recorded_constraint` preserves a conservative limitation downstream.
- `requires_human_review` and `unresolved` are blocking for material fields.

## Mandatory escalation fields

Escalate before continuing when conflict affects:

- central hypothesis or core mechanism;
- task or benchmark;
- contribution type or novelty boundary;
- required baseline identity or replacement;
- formal dataset/split/metric protocol;
- claim boundary or must-not-claim;
- network, credentials, restricted data, compute, license, or writable path;
- supported major Artifact schema;
- feasibility of the minimum experiment loop.

## Mismatch record

Each mismatch contains ID, axis, field, severity, values/claims compared, source refs, execution/claim impact, resolution status, resolution rationale, downstream constraints, and whether human review is required.

Do not collapse several fields into one vague “context differs” record.
