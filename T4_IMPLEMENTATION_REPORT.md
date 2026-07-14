# T4 Evolutionary Ideation Implementation Report

## Objective

Implement the T4 evolutionary research-idea workflow described in `ResearchOS_T4_Evolutionary_Ideation_Development_Guide.md` while preserving the public state path `T4 -> T4-GATE1 -> T4 -> T4.5`, legacy Pass1/Pass2 projections, Gate1, Bridge escape hatch, Human selection, formal `hypotheses.md`/`exp_plan.yaml`, and T4.5/T5 compatibility.

## Delivered Phases

| Phase | Delivered capability | Compatibility impact |
| --- | --- | --- |
| Inventory and contracts | Inventory, compatibility matrix, typed models, schemas, versioned configuration, fingerprints, atomic artifact store, migration hooks | Legacy Gate1 files remain readable and can migrate as `legacy_partial`. |
| Pre-run and evidence | Rich pre-run confirmation, readiness inspection, user-confirmed run config, Evidence Index, Evidence Permission, Opportunity Map, Route-specific bundles | No new public state-machine node. Resume reuses matching fingerprints. |
| P0 and scoring | Asymmetric Literature/Brainstorm/Supplement/Bridge formation, unsupported Route records, Idea Genome/Family/Sibling Family, independent blinded Scorer | Generator and Evolver cannot self-score; abstract-only evidence remains recall/inspiration only. |
| Evolution | Parent slots, Mutation/Crossover plans, Gene Donor Map, union rescoring, Idea Contract, Gene Delta, Complexity Inflation, Survival Selection, Archive, Population and Portfolio | Standard mode completes P0->P1; all Parent/Child artifacts and Population snapshots are retained. |
| Gate1 directives | LLM-first natural-language parsing followed by deterministic validation, confirmation, inspection, continue/focus/crossover/parallel/route-regeneration/rollback/pause actions | Multiple complete Candidates never merge implicitly. Read-only actions do not mutate Population. |
| Human composition | Compatibility report, Gene Donor Map, second confirmation, Human-composed Candidate, independent rescoring and updated Portfolio | Source Candidates and lineage remain immutable; no direct hypothesis-string concatenation. |
| Selected lifecycle | Pre-Novelty brief and targeted T4.5 search scope after selection; formal post-novelty compilation after a T4.5 pass | T5 remains blocked until formal hypotheses, plan, and accepted novelty audit exist. |
| Rich UX and docs | Pre-run, eight runtime phases, Gate1 cards/actions/results, user-facing language, no normal-mode raw JSON; paired English/Chinese documentation | Renderers consume validated ViewModels and workspace-derived scientific prose only. |

## Public Artifact Lifecycle

```text
Evidence Index + Opportunity Map
  -> P0 + Genome/Family/Score artifacts
  -> Evolution round + P1/P2/... + Archive + Portfolio
  -> Gate1 directive or selection
  -> hypothesis_brief.yaml + selected lineage + T4.5 targets
  -> T4.5 novelty/collision audit
  -> formal hypotheses + maps + kill criteria + exp_plan.yaml
  -> T5 handoff
```

The legacy `_pass1_forward_candidates.json`, `_pass2_grounding_review.json`, `_candidate_directions.json`, `_family_distribution.md`, `_gate1_candidate_cards.md`, `_gate1_selection_brief.md`, `idea_scorecard.yaml`, and related decision artifacts are retained as projections or compatibility ledgers. Native T4 state is held in `ideation/evolution/state.json` and immutable Population/Round/Candidate artifacts.

## Test Coverage

- Unit: Evidence Permission, route formation, role separation, family/sibling behavior, scoring, contracts, Pre-Novelty compilation, T4.5 formalization, directives, migration, and legacy projection.
- Integration: Standard P0->P1, Deep P0->P2, same-fingerprint reuse, Continue, Focus, Human composition check -> confirmation -> scoring -> Gate1 re-entry, selection -> T4.5, and legacy T4.5 migration.
- Failure injection: unknown Route, unsupported Route, duplicate regenerated Candidate IDs, rollback followed by a new snapshot number, invalid component composition, stale fingerprints, malformed output, and schema/permission failures.
- Golden Rich UI: pre-run, all eight visible T4 phases, operation confirmation, compatibility result, and no raw JSON leakage.

## Development Verification

Verification was executed after the final lifecycle and compatibility changes:

- Targeted T4, Gate1, T4.5, recovery, failure-injection, and Golden Rich UI suite: `157 passed`.
- Full repository regression suite: `1412 passed in 77.08s`.
- Static compilation: `python -m compileall -q researchos` completed without errors.
- State-machine and I/O-contract validation: `python -m researchos.cli validate-config --no-banner --no-color --quiet` returned `ok: true` with no errors.
- External executor inventory checks ran in a temporary Workspace. Both context-alignment and research-reboost classified `hypothesis_brief.yaml` and `t45_search_targets.json` as `pre_novelty_context`, never as an executable protocol.
- Real Workspace compatibility checks ran without changing research content. `project-cross-domain` migrated five legacy Candidates and `uplift_model` migrated eight into in-memory `legacy_partial` P0 projections. For both, T4.5 migration created a Pre-Novelty brief in a temporary copy while preserving the source `hypotheses.md` byte-for-byte.

The real-workspace check also found that `project-cross-domain` has a stale historical Gate1 selection because its bound candidate files changed afterwards. This is intentionally rejected rather than applied to a different candidate pool. It does not affect that Workspace's current `T5-EXTERNAL-WAIT` state.

## Compatibility Guarantees

- The external transition remains exactly `T4 -> T4-GATE1 -> T4 -> T4.5`.
- Existing workspaces are not silently rewritten. Legacy artifacts can produce a marked partial migration, and existing formal hypotheses remain intact.
- Rollback changes only the active Population pointer. Later snapshots, Candidates, diagnostics, and failed/unsupported Route artifacts remain on disk.
- Every model-backed operation is artifact-first and fingerprint-bound. Matching completed phases are reused; changed input invalidates only the affected continuation.
- T4 has no hardcoded research topic, method, dataset, metric, or candidate text. Scientific content is workspace-derived or LLM-authored under explicit validation boundaries.

## Related Audit

See [T4_SKILL_AUDIT_REPORT.md](T4_SKILL_AUDIT_REPORT.md) for the complete Skill, prompt, renderer, validator, and downstream-consumer review.
