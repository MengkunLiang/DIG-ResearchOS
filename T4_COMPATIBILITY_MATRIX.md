# T4 Evolution Compatibility Matrix

## Contract Boundary

This matrix is the implementation contract for the T4 evolutionary refactor. The refactor is additive behind the existing `T4 -> T4-GATE1 -> T4 -> T4.5` state-machine sequence. A compatibility projection may evolve internally, but it must not remove an existing path, gate identifier, external task name, or selected-output contract without a documented migration.

## State-Machine Compatibility

| Existing behavior | Required preserved behavior | Evolutionary implementation boundary | Verification |
|---|---|---|---|
| T4 starts through the `ideation` agent | `T4` continues to enter through `IdeationAgent` | Facade creates or resumes the internal `IdeaEvolutionController` | State-machine integration test |
| Pre-selection T4 writes a candidate pool | Candidate pool exists before `T4-GATE1` | P0/P1 projections write legacy pre-selection artifacts after controller checkpoints | Gate1 readiness test |
| T4-GATE1 is an immediate human gate | Gate ID `t4_gate1_selection_gate` and its options remain addressable | Gate reads a new `IdeaGateViewModel`; legacy artifact fallback remains available | Human-gate golden UI test |
| Valid Gate1 decision returns to T4 | T4 recognizes a valid selection and compiles selected output | Directive parser/validator preserves selection fingerprint and creates selected-idea artifacts | Resume test |
| Successful selected T4 proceeds to T4.5 | T4.5 receives compatible required inputs | Pre-novelty brief is additive; final legacy hypotheses and experiment plan remain valid T4.5 inputs | T4.5 handoff integration test |
| Existing T4.5 non-pass behavior uses human review | No automatic T4/T4.5 loop is introduced | T4 may be re-entered only through explicit user directive | Failure-injection test |

## Legacy Artifact Projection

| Legacy artifact | Must continue to exist | New authoritative source | Projection timing | Consumer compatibility |
|---|---:|---|---|---|
| `ideation/_pass1_forward_candidates.json` | Yes | Validated Round-0 route outputs | After P0 formation | Retains all route outputs, including unsupported records |
| `ideation/_pass2_grounding_review.json` | Yes | Initial independent Score Reports plus grounding/evidence checks | After P0 scoring | Covers every P0 candidate; no silent filtering |
| `ideation/_candidate_directions.json` | Yes | Active Population snapshot plus Candidate Dossiers | Before Gate1 and after targeted evolution | Every visible candidate remains attributable to an active or archived version |
| `ideation/_family_distribution.md` | Yes | Idea Family and Sibling Family artifacts | Before Gate1 | Human-readable family summary only; not a clustering source of truth |
| `ideation/_gate1_candidate_cards.md` | Yes | `IdeaGateViewModel` and model-authored candidate prose | Before Gate1 | Markdown fallback; normal terminal rendering uses Rich |
| `ideation/_gate1_selection_brief.md` | Yes | `IdeaGateViewModel` | Before Gate1 | Keeps selection guidance and artifact paths |
| `ideation/bridge_coverage_review.json` | Conditional as today | Bridge evidence route audit | Before Gate1 when bridge is declared or used | Escape hatch remains explicit and auditable |
| `ideation/_gate1_user_selection.json` | Yes | Validated human directive record | At Gate confirmation | Existing selection text, gate ID, and fingerprint fields remain usable |
| `ideation/selected_idea_brief.md` | Yes | Pre-novelty selected brief view | Immediately after full-candidate selection | No final novelty claim before T4.5 |
| `ideation/hypotheses.md` | Yes | Formal Hypothesis Bundle projection | Selected compilation boundary; finalized after T4.5 where required | Current T4.5/T5/T6/T7/T8 readers continue to work |
| `ideation/exp_plan.yaml` | Yes | Validation/Experiment Plan projection | Selected compilation boundary | Current experiment consumers continue to work |
| `ideation/idea_scorecard.yaml` | Yes | Candidate Dossiers and independent Score Reports | Selected compilation and Gate projection | Retains existing schema requirements; no longer owns population state |
| `ideation/idea_rationales.json` | Yes | Evidence/lineage provenance projection | Selected compilation | Keeps hypothesis coverage validation |
| `ideation/risks.md` | Yes | Selected Candidate risk/boundary data | Selected compilation | Retains at least the current selected-output semantics |
| `ideation/rejected_ideas.md` | Yes | Archive and survival decisions | Selected compilation | Does not hide rejected, deferred, merged, or unsupported candidates |
| `ideation/gate_decisions.json` | Yes | Human Directive and compilation decisions | After confirmed directive | Retains current gate decision fields and extends version references |

## New Internal Artifacts

| Artifact family | Owner | Status/fingerprint behavior | Legacy impact |
|---|---|---|---|
| `ideation/t4_run_config.json` | Pre-run configuration controller | Reused when run/input fingerprint is valid; UI-only changes do not invalidate population | New additive file |
| `ideation/evidence/*` | Evidence router | Regenerated only when relevant input fingerprint changes | Replaces implicit compact-pack evidence selection as source of truth |
| `ideation/genomes/*` | Genome encoder | Immutable by candidate version | New additive file |
| `ideation/families/*` | Family builder | Recomputable from immutable genomes and configuration | Drives legacy family summary |
| `ideation/candidates/*` | Candidate dossier writer | Each candidate version has a stable path and content fingerprint | Drives candidate directions, scorecard, cards |
| `ideation/scoring/*` | Independent scoring agent/controller | Batch and rubric versions stored; blind payload traceable | Drives Pass2 and scorecard projections |
| `ideation/evolution/*` | Evolution controller | Phase markers, round artifacts, active-population pointer, rollback-safe archive | New source of resume state |
| `ideation/populations/*` | Evolution controller | Immutable P0/P1/Pn snapshots | Drives active candidate projection |
| `ideation/archive/*` | Survival selection | Archived records remain readable and linked to reason | Drives rejected/deferred summaries |
| `ideation/human_directives/*` | Directive workflow | Every action is a new record; never mutates prior instruction | Extends Gate1 selection trace |
| `ideation/human_compositions/*` | Human composition workflow | Requires compatibility and rescoring before selection | Adds component-level selection safely |
| `ideation/selected/*` | Selected compilation | T4.5 pre-novelty search targets and lineage are explicit | Additive to legacy final artifacts |

## Reading Evidence Permissions

| Reading level/source | Recall and routing | Candidate mechanism language | Established mechanism or strong claim | Required behavior |
|---|---|---|---|---|
| `FULL_TEXT` | Allowed | Allowed within covered source section | Allowed within stated coverage | Preserve source path, section, and permission |
| `PARTIAL_TEXT` | Allowed | Allowed only for read sections | Conditional and bounded | Preserve read-section boundary |
| `ABSTRACT_ONLY` | Allowed | Candidate mechanism, inspiration, bridge hypothesis | Forbidden | Include reading-upgrade requirement when relied upon |
| `METADATA_ONLY` | Resource lead only | Forbidden except resource lead | Forbidden | Do not include as support atom |
| `SYNTHESIS_INFERENCE` | Opportunity and pattern | Conjectural only | Forbidden | Recheck underlying source for stronger use |
| `BRAINSTORM` | Idea inspiration | Conjectural only | Forbidden | Mark uncertainty and do not fabricate a source |

The compact T4 context pack and deferred evidence pool are migration inputs. Their previous prompt-selection behavior does not determine evidence permission. Both shallow and deep paper notes must be indexed and retrievable by later T4 phases.

## Agent Responsibility Separation

| Role | Can do | Cannot do | Required artifact boundary |
|---|---|---|---|
| `IdeaGeneratorAgent` | Plan Opportunities and generate route-scoped Idea Seeds | Score/rank/delete its own Seed; claim abstract evidence is established; create a final experiment plan | Route raw/validated Seed artifacts |
| `IdeaScoringAgent` | Blind score, diagnose, suggest preserve/modify genes, assess crossover compatibility | Generate/rewrite candidate body; see route or parent/child identity in blind scoring; delete candidates | Score reports and compatibility decisions |
| `IdeaEvolverAgent` | Generate one Mutation/Crossover Child from an explicit plan | Choose parent/pair; alter plan; score child; overwrite parent | Child dossier, lineage, gene delta, complexity record |
| Optional `IdeaArbiterAgent` | Compare a specified near-tie pair | Re-rank the population or create an idea | Pairwise decision only |
| `IdeaEvolutionController` | Schedule, fingerprint, validate, persist, select and render | Make field-specific research claims on its own | Population, round, archive and view artifacts |
| `IdeationAgent` facade | Preserve external T4 behavior and selected compilation compatibility | Become a monolithic generator/scorer/evolver prompt | Delegates to controller and projects legacy artifacts |

## Required Default-Mode Semantics

`rounds=1` is a complete evolutionary transition, not a single generation pass:

```text
Preflight -> Evidence Index -> Opportunity Map -> Multi-route P0 Formation
-> Genome + Family -> independent P0 scoring -> Parent Selection
-> 2-4 Mutation Children + 0-2 compatibility-gated Crossover Children
-> blind union rescoring -> Idea Contract -> Family Survival
-> Population Update -> Portfolio Selection -> P1 Gate
```

The configured route budget is non-symmetric: Literature formation produces three seeds; Informed Brainstorm produces two or three; each of Mechanism Challenge, Reverse Operation, Subgroup Failure, and Gap Exploration has one route; Cross-domain/Bridge has one or two. Supplement and Bridge routes may return `unsupported` with an explicit reason. The active population normally retains six to eight candidates, while the default visible portfolio shows one to three. These are configurable defaults, not hardcoded project facts.

## Selection, Composition, and Confirmation Rules

| User intent | System action | Confirmation | Reversibility |
|---|---|---|---|
| Select one full Candidate | Pin selected version and create a Pre-Novelty Hypothesis Brief | Required before selected compilation | Population and all versions remain; rollback possible |
| Select several full Candidates | Ask whether to keep parallel, inspect differences, or compose | Required when selection implies combination | No default merge |
| Continue one round | Create a new Round artifact from current active population | No extra confirmation within configured budget | Prior generations remain archived/readable |
| Focus a Candidate | Compile a targeted evolution plan | Confirmation when it changes preserved genes or exceeds budget | Parent remains immutable |
| Merge/Crossover full candidates | Run compatibility check and independent scoring | Required | Parent versions remain |
| Select individual hypothesis/contribution/gene across candidates | Create a Human-composed Candidate, validate compatibility, independently score, ask again | Two confirmations: compose and select | Original candidates remain untouched |
| Regenerate a Route | Create a new route run/version | Confirmation if it clears active population | Previous output remains archived |
| Rollback | Change active-population pointer | Required | Later generations are retained |
| Pause | Persist pending state | No extra confirmation | Resume must return to the same gate or phase |

## Migration Matrix

| Workspace state | Migration action | What must not happen | Test |
|---|---|---|---|
| Legacy Pass1/Pass2/candidate directions only | Create `P0` and `legacy_partial` genomes with source pointers | Do not invent an evolution round, hidden scores, or missing evidence | Legacy migration integration |
| Legacy valid Gate1 selection | Validate legacy pool fingerprints and create selected compilation state | Do not force T2/T3 rerun | Resume selection integration |
| Legacy final artifacts valid and current | Reuse existing selected compilation compatibility path | Do not call an LLM only to reformat artifacts | Idempotent resume test |
| Upstream literature/project/seed changes | Archive stale population, invalidate selection, request pre-run confirmation | Do not overwrite prior generation or silently reuse stale Gate | Fingerprint failure injection |
| UI-only setting changes | Re-render from existing artifacts | Do not invalidate population | UI setting test |
| Interrupted phase after durable artifact | Resume from next incomplete phase | Do not regenerate a completed LLM phase | Phase marker failure injection |

## Test and Documentation Obligations

The implementation must add and run tests for typed-model round trips, invalid evidence permissions, input fingerprint change, idempotent phase resume, P0/P1 population transition, anonymous scoring, crossover rejection, complexity inflation, unsupported Bridge escape hatch, human-composed candidate confirmation, legacy migration, state-machine compatibility, T4.5 handoff, normal-mode raw JSON exclusion, and Golden Rich UI views. Required documentation updates include the mirrored Chinese and English T4 workflow sections, user commands, Skill contracts, and `T4_SKILL_AUDIT_REPORT.md`.
