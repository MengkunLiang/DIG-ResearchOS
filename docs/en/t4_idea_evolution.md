# T4 Multi-Agent Idea Evolution: Native Architecture, Prompts, Validation, and Recovery

> [English](../en/t4_idea_evolution.md) | [中文](../cn/t4_idea_evolution.md)

> This document describes the current native T4 implementation. The authoritative implementation is in `researchos/ideation/`, `researchos/runtime/orchestrator.py`, `researchos/orchestration/state_machine.py`, `researchos/cli_runners/`, `researchos/ui/idea_evolution_renderer.py`, `researchos/prompts/idea_*.j2`, and `config/system_config/t4_evolution.yaml`. Older workspaces can contain legacy projection artifacts; those are compatibility exports, not the native T4 control plane.

T4 is not a form that asks one model to produce a fully specified paper proposal in a single call. It is a recoverable, population-based idea-evolution system. LLMs propose, explain, compare, challenge, and refine research ideas. Deterministic code owns identity, lineage, artifacts, paths, fingerprints, and evidence permissions. Researchers retain authority over research direction, exploration cost, and final selection.

```text
Strict about: factual boundaries, evidence permissions, lineage, state, and artifacts.
Tolerant about: output formatting, incomplete enrichment, route underfill,
                 local model failure, and population-size variation.
Order of response: lossless normalization -> bounded repair -> object-level
                   degradation -> human decision when a research or cost choice is required.
```

The central boundary is simple: **evidence constrains certification; it does not close the space of imagination.** An LLM may use project context, general scholarly knowledge, counterfactual reasoning, and structural cross-domain analogy to propose a bold idea. Such material remains conjectural and verification-required until it is supported. It must never be rewritten as a verified mechanism, a real experiment result, an available dataset, a valid citation, or an external novelty conclusion.

---

## 1. System Model and Invariants

### 1.1 Researcher-facing flow

```text
T3 / T3.5 / T3.6 materials, user seeds, and cross-domain catalogs
                              |
                              v
                        T4 Pre-run Gate
                              |
                              v
Evidence Index + Opportunity Map + multi-route Idea Seed formation
                              |
                              v
Candidate Enricher -> independent three-dimension scoring -> Family / Interaction Graph
                              |
                              v
Mutation plans + Crossover compatibility review + Child / explicit deferral
                              |
                              v
Union scoring + contract / delta / complexity diagnostics + family-aware survival
                              |
                              v
Portfolio + LLM Final Card Compiler + Gate1 (D1, D2, D3, ...)
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
     select for T4.5    evolve / focus         inspect / compare / rollback / keep parallel
```

P0, every population generation, candidate version, score, plan, child, deferral, interaction graph, final-card translation, and Gate directive is persisted. `resume` should reuse checkpoints whose input and run-config fingerprints still match. It should not repeat successful model calls or discard valid research work merely because another object failed.

### 1.2 Division of responsibility

| Responsibility | Authority | Reason |
| --- | --- | --- |
| Problem reframing, conceptual leaps, mechanism explanations, competing explanations, surprising predictions, research-program potential | LLM | These are the creative and scientific core of idea evolution. |
| Opportunities, Idea Seeds, candidate enrichment, score rationales, interaction explanations, compatibility reviews, children, final cards | LLM | These are workspace-specific scientific interpretations, not generic templates. |
| Candidate IDs, versions, parent IDs, population IDs, plan IDs, paths, fingerprints, resume order | Controller / State Machine | They are reproducible operational state, not scientific judgement. |
| JSON/YAML/fence parsing, known aliases, enum synonyms, list normalization | Recovery layer | Surface-form differences should not consume researcher attention or reject usable science. |
| Reading level, SourceRef, Evidence Permission, artifact write boundary | Source artifacts plus deterministic layer | A model cannot promote an abstract or conjecture to verified evidence. |
| Quotas, concurrency, batch size, population targets, family soft caps | Configuration and Controller | These control cost and scheduling, not whether an idea is legitimate. |
| Publication orientation, extra rounds, recovery budget, T4.5 selection | Researcher | These are research strategy and cost choices. |

### 1.3 Hard invariants

Only violations that would cause scientific deception, state corruption, or unrecoverable misbinding are hard blocks. They should block the affected object or operation and leave a precise diagnostic.

1. No fabricated paper, citation key, SourceRef, file path, dataset, metric, empirical result, cost, or external-novelty conclusion.
2. No promotion of `abstract_only`, `metadata_only`, `synthesis_inference`, or LLM conjecture into verified mechanism support, strong evidence, or final claim.
3. Candidate, Genome, and Lineage IDs must agree. A child may not overwrite a parent or forge parent/plan lineage.
4. An approved crossover child must match its parent set and Gene Donor Map. A mutation child must respect its preserve/modify constraints.
5. Active and archived population sets may not overlap. Input, run-config, and selection fingerprints may not be mixed.
6. Writes must remain inside the current workspace. Path traversal, unsafe overwrite, or a legacy artifact replacing native output is not recoverable by blind retry.
7. A T4.5 selection must bind to a current, traceable, selection-ready candidate.

Formatting differences, optional diagnostic gaps, route underfill, a failed card enrichment, a failed score batch, or a rejected crossover are not hard invariants.

### 1.4 Non-hard conditions

Markdown fences, YAML instead of JSON, known field aliases, a one-item list/object mismatch, an underfilled route, a failed optional enrichment, an unscored but traceable candidate, a rejected/parallel crossover, and a portfolio with fewer than three genuine directions are handled through repair, visible degradation, or researcher choice. They are not evidence that the entire population is unsafe.

---

## 2. Entry Points, Modes, and State

### 2.1 Common commands

| Command | Use | Execution model |
| --- | --- | --- |
| `python -m researchos.cli run --workspace <ws> --from-task T4` | Run or enter T4 through the full pipeline | Complete Pipeline Runner and State Machine |
| `python -m researchos.cli resume --workspace <ws>` | Resume a `PAUSED` or `WAITING_HUMAN` workspace | Re-present a persisted Gate or continue from checkpoint |
| `python -m researchos.cli run-task T4 --workspace <ws>` | Isolated T4 debugging | SingleTask Runner, including pre-run and recoverable Gate handling |

Only one writer may operate on a workspace at a time. Do not run `run`, `resume`, `run-task`, or a writing Skill concurrently against the same `ideation/` or `literature/` artifacts.

### 2.2 T4 Pre-run Gate

On a new run, or when the input fingerprint changes, `inspect_t4_inputs()` produces a read-only inspection. The `t4_prerun_gate` then asks the researcher to confirm mode, crossover permission, final portfolio size, and publication orientation before a Generator call is made.

The actual required inputs are deliberately small:

- `project.yaml`;
- `literature/synthesis.md`;
- `literature/synthesis_workbench.json`;
- `literature/domain_map.json`.

`comparison_table.csv`, reading notes, T3.6 survey insights, user seeds, and cross-domain catalogs are enrichment material. Their absence may reduce grounding or produce more explicitly conjectural ideas, but it does not make the idea space unavailable.

### 2.3 Current mode semantics

| Mode | New-workspace default rounds | Flow | Intended use |
| --- | ---: | --- | --- |
| `quick` | 0 | P0, independent scoring, Family/Interaction Graph, Portfolio, Gate1 | Inspect the initial candidate space without child generation. |
| `standard` | 2 | P0 -> P1 -> P2 | Default exploration: formation followed by two rounds of mechanism, counterfactual, and validation refinement. |
| `deep` | 3 | P0 -> P1 -> P2 -> P3 | Larger researcher-approved exploration budget. |
| `auto` | 2 by default, explicitly configurable 0-3 | Uses the confirmed round budget | Currently a configurable budget mode, not an autonomous research-policy agent. |

Historic workspaces can retain confirmed `standard=1` or `deep=2` configurations and remain resumable. A round count is an exploration-cost upper bound, never a requirement to manufacture a child in every round.

### 2.4 Publication orientation

`utd_is`, `management_is`, `ccf_cs`, `technical_cs`, `hybrid`, and `custom` profiles can change three-dimension summary weights, prompt/card emphasis, and the separately displayed qualitative Profile Fit. They do not change sources, Evidence Permission, the core Genome, or scientific facts. Profile Fit is not silently multiplied into survival or portfolio selection.

### 2.5 Durable state and artifacts

| Purpose | Path | Authority |
| --- | --- | --- |
| Pipeline state, pending gate, history, error | `state.yaml` | State Machine |
| Native T4 state | `ideation/evolution/state.json` | Current population, phase, fingerprints, display candidate IDs |
| Confirmed run config | `ideation/evolution/t4_run_config.json` | Mode, rounds, quotas, orientation |
| Population snapshots | `ideation/populations/P<n>.json` | Active and archived pointers by generation |
| Candidate dossier | `ideation/candidates/<id>.v<version>.json` | Genome, lineage, hypotheses, creative context |
| Score receipts/checkpoints | `ideation/evolution/scoring/`, `ideation/evolution/scores/` | Three-dimension scores, repair/isolation/unscored outcomes |
| P0 route checkpoints | `ideation/evolution/routes/round_0/<route>.json` | Supported, partial, or unsupported route result |
| Interaction Graph | `ideation/evolution/interactions/P<n>.json` | Bounded pair shortlist and optional LLM review |
| Evolution plans | `ideation/evolution/plans/round_<n>.json` | Parents, mutation/crossover plan, and compatibility decision |
| Children and deferrals | `ideation/evolution/offspring/` | A child for each admitted plan, or an explicit reason not to manufacture one |
| Portfolio card translations | `ideation/final_cards/portfolio_cards.json` | Optional LLM presentation enhancement |
| Gate1 compatibility projection | `ideation/_candidate_directions.json` and related files | Human-facing read model, not the sole native truth |

Checkpoint reuse is guarded by input and run-config fingerprints. A changed input or confirmed config cannot silently be presented as the result of an older population.

---

## 3. Evidence and Cross-domain Context

### 3.1 Evidence Index is a calibration substrate

`researchos/ideation/evidence.py` turns readable materials into `EvidenceAtom` records with stable identity, source path, section locator, reading level, domain role, bridge relation, allowed uses, and forbidden uses. It preserves provenance and prompt compactness; it does not determine the scientific value of an opportunity.

| Reading level | May support | Must not support |
| --- | --- | --- |
| `full_text` | Recall, problem anchor, bounded mechanism support, conditional/final claim within read scope | Claims beyond the actual section or SourceRef scope |
| `partial_text` | Bounded mechanism support, conditional claim, inspiration | Unconditional final claim |
| `abstract_only` | Topic, trend, discovery, analogy, reading-upgrade lead | Verified mechanism, strong support, final claim |
| `metadata_only` | Resource lead and reading priority | Problem, mechanism, or result support |
| `synthesis_inference` | Synthesis-based reframing and inspiration | Independent mechanism evidence or strong claim |
| `brainstorm` | Creative fuel | Existing fact, paper, dataset, or empirical result |

Not having deeply read a paper does not prohibit a new idea. It requires that the relevant proposition be marked conjectural/verification-required and include a falsifiable test or reading upgrade.

### 3.2 Cross-domain catalog and paper notes are separate roots

Cross-domain retrieval is not synonymous with `bridge_notes`:

```text
literature/
  bridge_domain_plan.json                   # researcher-confirmed B1/B2/... intent, rationale, queries
  cross_domain_catalogs/                    # canonical retrieval/analogy catalog root
    index.json
    B1/
      bridge_context.json
      _bridge_context.md
      paper_catalog.json
  bridge_notes/                              # actual read Bridge paper notes only
    B1/<paper-id>.md
  deep_read_notes/
  shallow_read_notes/
```

`bridge_context.json` holds the bridge name, researcher rationale, priority, queries, and usage boundary. `paper_catalog.json` holds retrieved metadata, abstracts, read status, canonical-note links, and bridge association. These B1/B2/B3 tracks remain meaningful even when `bridge_notes/` has no deep-read Markdown note.

Catalog-only records become `ABSTRACT_ONLY` or `METADATA_ONLY` cross-domain idea fuel. They can inform structural analogy, counterexamples, validation questions, historical or taxonomy framing, baseline/dataset leads, and reading priority. They are never direct evidence for a mechanism, result, citation claim, or method equivalence. A linked canonical reading note controls what claim use is permitted.

### 3.3 Legacy migration and no duplicate injection

Historic workspaces placed catalog JSON beside Bridge notes. Workspace initialization and T2/T3 refresh use `researchos/runtime/bridge_catalog.py` to migrate those records non-destructively:

1. Copy `bridge_context.json`, `paper_catalog.json`, and `_bridge_context.md` into canonical `cross_domain_catalogs/`.
2. Never move or delete actual Markdown notes.
3. Never overwrite a differing canonical file; record a conflict instead.
4. Retain `bridge_notes/<id>/paper_catalog.json` only as a read fallback if the canonical catalog for the same bridge ID is absent.
5. Select one catalog per bridge ID so old and canonical copies cannot be injected twice into a T4 prompt.

When a Cross-domain track appears empty, inspect in this order:

```text
literature/bridge_domain_plan.json
literature/cross_domain_catalogs/index.json
literature/cross_domain_catalogs/<B#>/bridge_context.json
literature/cross_domain_catalogs/<B#>/paper_catalog.json
```

Inspect the legacy `bridge_notes/<B#>/paper_catalog.json` only for a historic workspace, and inspect `bridge_notes/<B#>/` when the question is specifically whether a paper has been read. An empty note directory does not mean the cross-domain track was lost.

### 3.4 T4 use of cross-domain context

T4 passes both evidence-backed Bridge atoms and bounded catalog summaries to the Opportunity Planner and Route Generator. It preserves representative material from separate bridges so a large core-note collection does not crowd all adjacent-domain context out of the prompt.

The `cross_domain_bridge` route can use a bridge name, rationale, and query as conjectural structural-transfer scaffolding even before a dedicated paper note exists. A defensible candidate explains the source-to-target mapping, the transferable mechanism/method/evaluation/baseline perspective, transfer risk, conjectural status, and a verification or reading upgrade. Keyword overlap alone is insufficient.

If no sufficiently grounded structural mapping can be proposed, `unsupported` or `deferred` is a normal result. It does not fail the mainline T4 run or erase the bridge catalog.

### 3.5 Cross-domain context beyond T4

Catalog context and actual Bridge notes are not only T4 inputs. They can also support T3/T3.5/T3.6 taxonomy and supplemental retrieval, T4.5 differentiation searches, T8 related-work positioning, and relevant internal or external Skills. The same boundary applies everywhere: catalog context broadens discovery and framing, while an actual canonical note controls evidence permission.

---

## 4. Native T4 Lifecycle

### 4.1 Lifecycle overview

```text
Pre-run -> Evidence Index -> Opportunity Map -> P0 multi-route formation
  -> candidate enrichment -> independent scoring -> Family / Interaction Graph
  -> mutation/crossover plans -> child or explicit deferral -> survival
  -> portfolio/card projection -> Gate1
```

Each stage is checkpointed. A successful artifact is reused only when its input and run-config fingerprints still match; a local failed object does not invalidate completed sibling work.

### 4.2 Evidence Routing and workspace context

The controller writes:

```text
ideation/evidence/evidence_index.jsonl
ideation/evidence/evidence_index_summary.json
```

It then builds a bounded `workspace_research_context` from the project, synthesis, user seeds, selected evidence atoms, and catalog tracks. This is retrieval/truncation, not a deterministic research-gap classifier. The complete index remains addressable for targeted inspection.

### 4.3 Opportunity Map

`idea_opportunity_planner.j2` emits distinct `OpportunityQuery` objects rather than candidates or rankings. It can use tensions, hidden assumptions, failure boundaries, evaluation blind spots, user constraints, and structural bridge opportunities.

If the planner is unavailable or structurally irreparable, T4 records `ideation/evolution/diagnostics/opportunity_planner_recovery.json` and continues with an explicitly provisional fallback opportunity set. The fallback is an operational receipt, not a domain claim. Planner failure must not pause all route generation.

### 4.4 P0 multi-route formation

Current route ranges are exploration-cost guidance, not output obligations:

| Route | Default range | Scientific role |
| --- | ---: | --- |
| `evidence_routed_literature` | 3 | Mechanisms, tensions, counterexamples, and gaps in mainline material |
| `informed_brainstorm` | 2-3 | Project-grounded, explicitly conjectural leaps using LLM scholarly knowledge |
| `mechanism_challenge` | 0-1 | Challenge default mechanism assumptions |
| `reverse_operation` | 0-1 | Reverse objective, causal direction, or operation logic |
| `subgroup_failure` | 0-1 | Reframe around heterogeneity, failed groups, or boundaries |
| `gap_exploration` | 0-1 | Explore a structural explanatory or measurement gap |
| `cross_domain_bridge` | 0-2 | Propose a verification-required structural transfer |

Each route has an independent checkpoint at `ideation/evolution/routes/round_0/<route>.json` and may be `supported`, `partial`, or `unsupported`. One bounded repair/re-divergence attempt is available. A route that remains underfilled records why; it does not loop until it produces duplicate filler or block other routes.

### 4.5 Minimal IdeaSeed contract

The Generator is intentionally allowed to return a small, exploratory seed:

- project-relevant problem;
- one-line thesis;
- candidate mechanism;
- one contribution sketch;
- one falsifiable prediction;
- one major uncertainty;
- route origin and any supplied evidence references.

A complete `CandidateDossier` is also accepted when available. A minimal seed is projected into a traceable, explicitly `seed`-maturity dossier without inventing citations, scores, experiments, or scientific prose. A structurally usable candidate with incomplete presentation is downgraded to a seed rather than causing its entire route to fail.

### 4.6 Candidate Enricher

`LLMCandidateEnricher` gives each admitted seed an independent opportunity to deepen its scientific expression: mechanism chain, competing explanations, 2-4 hypotheses, 2-4 contributions, validation logic, boundaries, risks, kill criteria, and researcher-readable Chinese presentation.

It may not change the candidate ID, route, parent lineage, problem reframing, core thesis, existing conceptual leap, SourceRef, or Evidence Permission. It cannot score, select, reject, merge, or replace the seed.

Artifacts:

```text
ideation/evolution/enrichment/<candidate-id>.json
ideation/evolution/diagnostics/enrichment_<candidate-id>_attempt_<n>.json
```

After one normal attempt and one structural repair attempt, an unavailable enrichment leaves the original seed active with an `enrichment_degraded` warning. Later focused evolution, a reading upgrade, or human-directed refinement can deepen it. Enrichment failure never removes the seed or stops P0.

### 4.7 Genome, Creative Context, and Family

`IdeaGenome` is the Candidate's evolvable, auditable scientific backbone. It is not a Gate1 display card or a collection of freely composable text fragments. In addition to identity, version, generation, maturity, Route, and Parent metadata, it has exactly these 11 scientific genes: `problem`, `opportunity`, `challenged_assumption`, `core_thesis`, `mechanism`, `design_or_artifact`, `contribution_package`, `hypothesis_bundle`, `validation_logic`, `boundary_conditions`, and `risks`.

Each gene is an `IdeaGene(value, provenance)`. Its provenance records source routes, traceable source references, reading levels, evidence role, confidence, and whether a reading or validation upgrade is required. The current schema therefore does **not** have a twelfth standalone `evidence_map` gene. Evidence mapping is distributed across per-gene provenance and the Dossier-level `evidence_composition`. Abstract-only, metadata-only, synthesis-inference, and model-derived material may motivate an opportunity, a conjecture, or an upgrade request, but cannot become a mechanism anchor or direct support.

`CandidateDossier` additionally normalizes the Genome's scientific package into `contributions` and `hypotheses` for downstream use. An evolved Candidate has two to four contributions and two to four provisional hypotheses, each hypothesis carrying its mechanism, observable prediction, and discriminating test. `CreativeContext` preserves the LLM-authored conceptual leap, competing explanations, surprising prediction, research-program potential, knowledge origin, evidence status, and required reading/validation upgrades.

Family is a comparison and organization mechanism. The deterministic interaction shortlist uses only `problem`, `mechanism`, `contribution_package`, `hypothesis_bundle`, and `validation_logic` to surface candidates worth review. It is neither a scientific equivalence verdict nor a deletion rule. Mechanism relationships, dependencies, and whether candidates should remain parallel or be combined require LLM/researcher interpretation.

### 4.8 Independent scoring with exactly three numerical dimensions

The formal `ScoreDimensions` object contains exactly:

| Dimension | Question | Not a proxy for |
| --- | --- | --- |
| `research_value` | If the conjecture survives testing, how important is the problem and what could it change? | Reading volume, dataset availability, or publication maturity |
| `mechanism_integrity` | Is the mechanism coherent, falsifiable, and distinguishable from a competing explanation? | Completing all reading before scoring |
| `contribution_distinctiveness` | Does the idea differ substantively from the current T4 population? | Proof of literature-wide external novelty |

`overall_readiness` is runtime-derived from those three dimensions only. It is not a fourth model score or a hard gate. Its public weighting can reflect confirmed publication orientation, but it never includes evidence calibration, validation feasibility, uncertainty, upside, or Profile Fit.

Evidence calibration, validation feasibility, scientific upside, evolution potential, score uncertainty, wildcard recommendation, dominant strength/bottleneck, Profile Fit, and historical compatibility grids are non-blocking diagnostics. The blind Scorer cannot create, rewrite, merge, select, or archive candidates.

Failed batches are repaired once, then isolated into smaller or per-candidate scoring calls. An ultimately unscored candidate remains visible and unranked; no synthetic fallback score is invented and other candidates are not discarded.

### 4.9 Interaction Graph

Every P0/P<n> can write `ideation/evolution/interactions/P<n>.json`. The graph has two layers:

1. A transparent deterministic shortlist from problem, mechanism, contribution, hypothesis, and validation token overlap/distance. It identifies pairs worth inspection as possible competitors, complements, or distant transfers.
2. The optional LLM Interaction Reviewer, which explains shared core, key difference, peer challenge, transferable element, differentiation need, crossover potential/risk, and one of `competitor`, `complement`, `distant_transfer`, or `parallel`.

The graph is not a second scorer or a hidden survival rule. Deterministic similarity only selects review attention. The reviewer cannot score, select, delete, merge, or rewrite candidates. If it fails, the graph is persisted as `deterministic_degraded`, a local diagnostic is written, and the round continues.

Compact peer context may be attached to a mutation plan as advisory material. The Evolver is free to reject an unhelpful transfer and return a deferral.

### 4.10 Parent, Mutation Plan, and Crossover Compatibility

| Model expression | Normalized result | Research meaning |
| --- | --- | --- |
| `parallel`, `keep parallel`, or an equivalent Chinese expression | `parallel` | Do not create a crossover child; preserve both parents as independently comparable directions |
| `incompatible` | `rejected` | The pair must not be merged automatically; neither Candidate is deleted |
| `needs clarification`, `defer`, or an equivalent Chinese expression | `uncertain` | More explanation or evidence is needed; do not force a child |

Mutation plans make preserve/modify genes, expected improvement, and failure conditions auditable. The scientific transformation remains LLM work. A substantive mutation should sharpen causal explanation, distinguish an alternative, add a discriminating counterfactual, narrow a risky thesis into an informative test, or turn a structural analogy into a falsifiable mechanism.

Crossover follows this sequence:

```text
Interaction-graph pair suggestion (or bounded fallback)
  -> LLM Crossover Compatibility Review
  -> only approved pairs receive a Gene Donor Map and Crossover Plan
  -> Evolver may generate a child
```

The durable compatibility enum is `approved`, `rejected`, `uncertain`, or `parallel`. Only `approved` can authorize a Gene Donor Map and Crossover Child. `parallel` is a first-class no-child finding: it preserves both Parents as independently comparable directions rather than collapsing that scientific conclusion into a generic rejection. `parallel`, `keep parallel`, and recognized localized aliases normalize to `parallel`. A persisted `parallel_crossover` or `no_approved_crossover` plan batch is reusable on resume, so an intentionally childless decision does not call the Compatibility Reviewer or Evolver again. A single string in `conflicts` is normalized to a one-item list.

### 4.11 Offspring, Explicit Deferral, and Parent Preservation

For a plan that cannot produce a substantive, plan-consistent child, `EvolutionPlanDeferral` records `no_improvement`, `incompatible`, or `deferred` with a concrete rationale and revisit condition. A plan-local provider/schema failure is repaired locally and then deferred/archived if necessary. Parents and unrelated plans remain intact.

### 4.12 Union Scoring, Contract, Delta, Complexity, and Survival

Admitted children trigger independent parent-plus-child union scoring. When no child is admitted, the controller reuses the completed parent scores with an explicit receipt rather than making another unnecessary model call.

The controller then records structural contract checks, Gene Delta, and Complexity diagnostics. Survival uses three-dimension Pareto layers first, then parent-child gain, structural diversity, a soft family cap, and explicit wildcard preservation. Evidence calibration, validation feasibility, Profile Fit, uncertainty, and upside do not become hidden numerical survival dimensions. Structurally viable unscored candidates can remain visible for human review.

Portfolio selection aims for a quality-diverse Lead/Alternative/High-upside view. It can show one or two genuine directions when the population does not support three distinct ones.

### 4.13 Final Card Compilation and Gate1 Projection

`LLMFinalIdeaCardCompiler` translates portfolio candidates into Chinese final cards. A malformed or unavailable card compiler is a card-level degradation: candidate dossiers, scores, and Gate1 projection remain usable. The renderer must not invent scientific explanations to fill a missing card.

---

## 5. Prompt Roles and Their Boundaries

### 5.1 Prompt Catalog

| Template | Role/output | May do | Must not do | Default failure handling |
| --- | --- | --- | --- | --- |
| `idea_opportunity_planner.j2` | Opportunity Planner, `opportunities` | Propose diverse research questions from tensions, assumptions, failures, seeds, and bridges | Score or select candidates; turn retrieval absence into a factual gap | Planner diagnostic and provisional fallback |
| `idea_opportunity_semantic_repair.j2` | Opportunity repair | Normalize existing opportunity structure/boundaries | Create scores, sources, or novelty claims | Bounded repair; fallback remains possible |
| `idea_generator.j2` | Route Generator, `seeds` / dossier / `unsupported` | Form bold but falsifiable minimal seeds | Require final-paper completeness or fabricate support | Route-local repair/partial/unsupported receipt |
| `idea_route_semantic_repair.j2` | Generator repair | Reorganize supplied material and safely downgrade provenance | Score, select, delete candidates, or invent citations | Local route degradation only |
| `idea_candidate_enricher.j2` | Candidate Enricher, `candidate` | Deepen a retained seed | Change identity, route, parents, core problem/thesis, source permissions, or selection | Candidate-local degraded seed |
| `idea_scorer.j2` | Blind Scorer, `scores` | Assess exactly three formal dimensions and optional diagnostics | Rewrite, rank, merge, or archive candidates | Repair, isolation, then unscored receipt |
| `idea_score_semantic_repair.j2` | Score schema repair | Normalize a parseable score structure | Invent scientific rationale or alter candidate | Bounded repair then isolate batch |
| `idea_score_rationale_repair.j2` | Rationale repair | Clarify existing score explanation | Turn diagnostics into hard gates | Missing diagnostic stays visible |
| `idea_interaction_reviewer.j2` | Interaction Reviewer, `reviews` | Explain shortlisted pair relationships | Score, select, merge, or convert lexical overlap into evidence | Deterministic-degraded graph |
| `idea_crossover_reviewer.j2` | Compatibility Check, `decisions` | Assess one-thesis coherence and donor-map safety | Generate a child or force merging | Parent preservation for rejected/uncertain/parallel |
| `idea_evolver.j2` | Evolver, `children` / `deferred_plans` | Produce a substantive plan-bounded child | Change a plan/parent, score, or select survival | Plan-local repair and deferral |
| `idea_offspring_semantic_repair.j2` | Child repair | Repair structure/lineage/plan alignment | Forge parent, bypass donor map, elevate evidence | Only the one plan is affected |
| `idea_final_card_compiler.j2` | Final Card Compiler, `cards` | Produce profile-aware candidate explanations | Alter Genome/scores/sources/lineage or add claims absent from candidate | Card-local repair/deferred card |
| `idea_final_card_semantic_repair.j2` | Card repair | Map supplied candidate/score content into card schema | Invent research claims, hypotheses, citations, or recommendations | Explicit missing-field degradation |
| `idea_human_composer.j2` | Human-composed candidate | Build one candidate from a confirmed compatibility record | Concatenate text, bypass confirmation, choose winner | Preserve source candidates |
| `idea_composition_reviewer.j2` | Component compatibility | Judge whether researcher-selected components can form one coherent idea | Directly generate/force a candidate | Keep parallel/request choice/reject are valid |

The Opportunity Planner and Generator are explicitly allowed to use general scholarly knowledge and structural cross-domain reasoning. Non-workspace material must retain `knowledge_origin`, conjectural status, and a verification/reading-upgrade need. The Candidate Enricher deepens expression but cannot convert that knowledge into verified facts. The Scorer outputs three formal dimensions; evidence, validation, profile, and upside are diagnostics, not vetoes.

### 5.2 Opportunity Planner creativity contract

The planner may propose non-obvious questions through general scholarly knowledge, counterfactual reframing, and structural bridge analogy. It must mark non-workspace content as verification-required and must not turn a missing retrieval area into a factual gap or novelty finding.

### 5.3 Generator minimal-seed contract

The generator is intentionally asked for a compact problem, thesis, mechanism, contribution sketch, falsifiable prediction, uncertainty, and route origin. It should return fewer directions or `unsupported` rather than manufacture duplicated filler. Cross-domain names and rationales are creative scaffolding, not certified evidence.

### 5.4 Candidate Enricher protection contract

The enricher expands a retained seed without changing its identity, route, parent lineage, problem, core thesis, or source boundary. It can deepen explanation but must preserve uncertainty instead of inventing a citation, dataset, metric, result, or external-novelty conclusion.

### 5.5 Three-dimension Scorer contract

The scorer evaluates only research value, mechanism integrity, and contribution distinctiveness. Its candidate-specific rationales and optional diagnostics guide evolution and human comparison; they do not author a maturity veto or an external novelty claim.

### 5.6 Interaction and Crossover separation

Interaction review explains candidate relationships. Compatibility review decides only whether a pair can support one coherent thesis and a safe donor map. Neither role may directly merge, rank, or delete candidates.

### 5.7 Evolver scientific-improvement contract

The Evolver should seek a substantive causal, counterfactual, or validation improvement rather than a wording edit or module stack. When no defensible improvement exists, an explicit deferral preserves the parent.

### 5.8 Final Card and Human Composition contract

Final cards translate existing structured research content for a researcher; they do not mutate the candidate. Human composition requires a researcher-selected component set, a compatibility review, and confirmation before creating a separately scored candidate.

---

## 6. Typed Contracts and Legacy Isolation

### 6.1 Candidate Models

| Model | Purpose | Key contract |
| --- | --- | --- |
| `IdeaSeed` | Minimal exploratory idea | Problem, thesis, mechanism, prediction, risk, and route; no final-card obligation |
| `IdeaGenome` | Stable scientific genes | Candidate ID, route, parents, and core scientific fields |
| `CandidateDossier` | Native research entity | Dossier/Genome/Lineage IDs agree; evolved candidates need 2-4 contributions and hypotheses |
| `CreativeContext` | Preserves exploratory reasoning | Leap, alternatives, surprising prediction, program potential, origin, and upgrades |
| `CandidatePresentation` | LLM-authored presentation layer | Mature display data; a seed may await enrichment |
| `ProvisionalHypothesis`, `Contribution`, and `IdeaFamily` | Falsification, contribution, and comparison context | Each remains traceable and does not certify external novelty or authorize candidate deletion |
| `EvolutionPlan` | Mutation/crossover execution boundary | Mutation has one parent; crossover has two parents and a donor map |
| `CrossoverCompatibilityDecision` | Pair merge decision | Durable three-value enum with no-merge alias normalization |
| `EvolutionPlanDeferral` | Auditable no-child outcome | Concrete rationale and revisit condition |
| `PopulationSnapshot` | Generation pointers | Active/archived disjointness and active elite |

### 6.2 Score Model

`ScoreReport.scores` is `ScoreDimensions` with only `research_value`, `mechanism_integrity`, and `contribution_distinctiveness`. Historic five-dimension artifacts remain readable: retired numerical values migrate into `diagnostics.legacy_numeric_values`; old compatibility fields remain optional compatibility views. New scorers do not require or generate them.

`ProfileFitAssessment` is independent and qualitative. It may change when a researcher changes publication orientation without changing core scientific scores or Evidence Permission.

### 6.3 Evolution, Crossover, and Population Models

`EvolutionPlan`, `CrossoverCompatibilityDecision`, `EvolutionPlanDeferral`, Gene Delta, Complexity Report, `PopulationSnapshot`, and `PortfolioSelection` separate auditable operations from scientific prose. They protect parent/child identity and population state while allowing a no-child, parallel, or underfilled outcome to remain scientifically visible.

### 6.4 Final Card Model

`FinalIdeaCardTranslation` is a non-mutating presentation translation. It binds candidate ID, profile type, core thesis, and canonical contribution/hypothesis IDs. LLM-authored fields include short title, plain-language summary, why-it-matters, representative scenario, scientific/technical core, innovation delta, portfolio relationship, explicit dependencies, composition guidance, candidate-specific recommendation, bottleneck explanation, bounded implications, risks, and claims not to make.

The card cannot alter Genome, Score, SourceRef, or lineage. An absent card is an explicit degradation, never a reason for the renderer to author plausible science.

#### Why a complete Card can still fail, and why it cannot be ignored

A Card is a researcher-facing, non-mutating translation of a Portfolio Candidate. It can therefore be unavailable even when the Candidate itself is scientifically complete: a provider response may be malformed, omit a required presentation field, or drift from the immutable Candidate identity. Such a failure must remain visible and recoverable through the dedicated Card-compiler path. It must not be papered over with deterministic prose, nor may it invalidate the native Candidate, its score, lineage, or portfolio membership.

The recovery chain is deliberately card-local:

```text
saved Candidate / Score / Population
  -> Card Compiler
  -> semantic repair or bounded fresh compile when parsing, schema, coverage, or immutable-field checks fail
  -> Human Recovery Gate after the bounded attempts are exhausted
```

```text
provider timeout | empty response | response parse failure | schema mismatch
coverage mismatch | immutable-field mismatch | stale card/population | missing source data
```

### 6.5 Legacy Projection

`researchos/ideation/legacy_projection.py` is a best-effort compatibility exporter from native populations to retained Gate1 files. It is not the default generator and must not silently call old `ideation.j2`. Native `CandidateDossier`, `ScoreReport`, `PopulationSnapshot`, and `EvolutionPlan` remain authoritative.

Missing legacy score-grid or card fields must leave a traceable candidate visible as seed, unscored, or projection-degraded. They may not delete, overwrite, or invalidate the native population.

### 6.6 Gate1 Validator: Strict Identity, Visible Enrichment Gaps

`validate_t4_gate1_ready()` is not a final-paper completeness validator. Its hard boundary is a traceable candidate pool with coherent Candidate/Score/Lineage/artifact identity, a real conceptual anchor, truthful source-claim and cross-domain provenance, parseable supplied core scores within 1-5, and consistent Pass1/Pass2/Candidate Directions coverage and visibility. Failure here could show a researcher the wrong object, damaged score, or false source relation, so it must enter a diagnostic recovery path.

The following are visible enrichment/degraded states, not grounds to pause an entire population:

- missing Gate1 card prose, long explanations, or complete final-card translation;
- incomplete innovation explanation, basis-source interpretation, or candidate-specific recommendation;
- missing deprecated seven-dimension compatibility grid/rationale;
- a single hypothesis or hypotheses awaiting enrichment;
- missing Profile Fit, scientific-upside, uncertainty, or evidence/validation qualitative diagnostics;
- one independently unscored candidate that still has a complete, traceable candidate/lineage/source boundary.

The UI must disclose these as enrichment-needed, unscored, or an explicit diagnostic. They can be addressed through the Enricher, focused evolution, a reading upgrade, or a human instruction. They cannot cause a renderer to invent generic scientific prose or make the whole population pause.

---

## 7. Validation, Repair, Degradation, and Recovery Gates

### 7.1 Four-state result

`researchos/ideation/response_recovery.py` defines:

| Status | Meaning | Default action |
| --- | --- | --- |
| `valid` | Structure and role contract pass | Continue |
| `repairable` | Scientific content is usable but surface form differs | Normalize/repair/revalidate |
| `degraded` | A local role/object is incomplete, but continuation can remain honest | Persist diagnostic and continue elsewhere |
| `blocked` | Continuation would violate fact, state, lineage, or path integrity | Stop the affected operation and present recovery guidance |

The intended order is:

```text
deterministic normalization
  -> tolerant JSON/YAML/fence parsing
  -> role-specific schema repair
  -> bounded LLM semantic repair
  -> revalidation
  -> route/candidate/plan/card-level degradation
  -> Human Gate only when safe continuation is impossible or needs a research/cost choice
```

### 7.2 Lossless normalization examples

The recovery boundary can extract a unique mapping from fences or prose, parse structured YAML, remove safe trailing commas, wrap a known top-level list or one-item role output, map known camelCase aliases, normalize JSON `"null"`, normalize enum aliases, wrap one conflict string into a list, and map unambiguous `parallel` wording to the durable no-merge enum. It never invents a hypothesis, citation, evidence item, score, provenance, or lineage.

### 7.3 Repairable Constraints

Fences, YAML, known aliases, one-item envelope differences, a minimal seed, missing optional card prose, missing legacy compatibility fields, and a normal no-merge decision are repairable or degradable. Repair may reorganize supplied material and make uncertainty explicit; it may not invent a source, a score, a result, or lineage.

### 7.4 Soft Quality Rules and Heuristics

Route counts, population size, family count, portfolio count, family similarity, lexical interaction shortlist, evidence calibration, validation feasibility, uncertainty, upside, Profile Fit, complexity growth, card completion, and bridge review availability are diagnostics, ordering hints, or human prompts. They are not hidden candidate deletion rules.

### 7.5 Hard-Invariant Failure Behavior

Hard failures preserve the failing object, the error, and all unaffected checkpoints. They block unsafe writing, selection, or lineage use rather than being silently converted to success. The following table contrasts the ordinary local conditions that should instead remain continuous.

| Condition | Correct behavior |
| --- | --- |
| Opportunity Planner failure | Provisional fallback plus diagnostic; routes continue |
| One route fails/underfills | One repair, then partial/unsupported receipt; other routes continue |
| Seed enrichment fails | Original seed survives with `enrichment_degraded` |
| Score batch fails | Repair, isolate, then retain an unscored candidate |
| Mutation fails/no improvement | Archive/defer that plan; preserve parent |
| Crossover rejected/uncertain/parallel | No child; preserve both parents |
| Interaction review fails | Deterministic-degraded graph; evolution continues |
| Final card fails | Card deferred; population and Gate1 continue |
| Portfolio has fewer than three candidates | Show one or two real directions; do not add filler |

**T4-specific recovery Gate.**

If T4 cannot safely reach Gate1 after a non-integrity interruption, `t4_recovery_gate` persists the error and existing artifacts. Its choices are:

1. `retry_t4`: resume from checkpoint without repeating successful routes, scores, or populations.
2. `open_gate1`: show saved candidates only when Gate1 artifacts are coherent.
3. `pause`: retain diagnostics for a later resume.
4. `exit`: end this invocation without deleting artifacts.

Provider, route, score, mutation, crossover, card, renderer, and projection failures are normally recoverable when saved checkpoints exist. Path traversal, unsafe write, legacy overwrite, state corruption, fingerprint/selection mismatch, ID collision, and forged lineage are not candidates for blind retry.

**Generic runtime recovery Gate.**

The dynamic `runtime_recovery_gate` handles recoverable validation, artifact-validation, provider, runtime, environment, and unavailable-human-input interruptions. Legacy persisted records may still mention budget or max-step reasons, but current ResearchOS runs do not impose ordinary internal step/token caps unless a developer explicitly enables a bounded override. The gate persists target task, failure kind, error summary, known outputs, and a recovery directive under `_runtime/recovery/<task>_runtime_recovery.json`.

| Option | Meaning |
| --- | --- |
| `retry_targeted_repair` | Read existing diagnostics/artifacts and repair only the implicated work. |
| `extend_recovery_window` | Legacy compatibility option for old bounded-budget recovery records. Current default runs are already unbounded for ResearchOS step/token accounting. |
| `inspect_then_pause` | Preserve the pending gate and do not rerun yet. |
| `exit` | End this invocation without deleting work; a later resume reopens the decision. |

No recovery option weakens evidence policy, schema integrity, tool permissions, citation truth, or scientific boundaries. Provider context windows, timeouts, environment readiness, and validation failures can still pause the run. An explicit researcher pause is not immediately replaced by another generic recovery gate.

Persisted dynamic Gate presentation/options are authoritative on resume. A legacy workspace with a persisted gate ID missing from the current YAML registry must not crash with `KeyError`.

---

## 8. Failure Isolation and Dynamic Human Recovery Gates

### 8.1 Outcomes That Must Not Pause T4

Planner failure, a partial/unsupported route, seed-enricher degradation, score isolation, an unscored candidate, no substantive mutation, a rejected/uncertain/parallel crossover, interaction-review degradation, card-translation deferral, and an underfilled portfolio are all normal object-level outcomes. They should persist diagnostics and leave valid candidates available.

### 8.2 T4-specific Recovery Gate

When native T4 has saved a population or candidate checkpoint but cannot safely arrive at Gate1, `t4_recovery_gate` offers checkpoint-aware retry, a read-only Gate1 view when artifacts are coherent, pause, or exit. It does not rerun successful work by default.

### 8.3 Generic Runtime Recovery Gate

`runtime_recovery_gate` covers recoverable validation, provider, environment, and runtime interruptions. Legacy budget/max-step records are preserved for old workspaces, but current runs do not use ordinary internal step/token caps. Targeted retry preserves valid artifacts and never relaxes scientific integrity.

### 8.4 Persisted Dynamic Gates and Historic Workspaces

The pending gate's persisted presentation and options are authoritative. Resume may refresh optional configured decoration, but a dynamic recovery gate whose identifier is absent from a newer registry must continue to open rather than raising a registry lookup error.

### 8.5 Conditions That Require a Researcher Decision

Missing core upstream artifacts, an empty traceable population, a stale selection/fingerprint, a lineage conflict, a request to spend more resources, a requested merge, or a decision to enter T4.5 requires an explicit human choice. The system should explain what will call a model, create a candidate, preserve history, or remain reversible.

## 9. Gate1, Public IDs, Cards, and Human-directed Evolution

### 9.1 D# Public IDs

An internal lineage ID such as `S-informed-brainstorm-1` is an artifact key, not a human decision identifier. Gate1 assigns public handles `D1`, `D2`, `D3`, and so on to the visible portfolio and remaining active population.

```text
Gate1 default: D1 Controlled Agent Decision Benchmark
Lineage detail: Internal ID: S-informed-brainstorm-1
                Origin Route: informed_brainstorm
                Generation / Family / Parents / artifact paths: ...
```

State Machine resolves exact `D<number>` tokens back to the active internal ID. Examples include `select D1`, `continue evolution`, `focus D2's mechanism`, `inspect D1 score/evidence/lineage/hypotheses/files`, `compare D1 and D3`, `merge D1 + D2`, and `return to the previous generation`. A direct continue-evolution instruction maps to `continue_evolution`; read-only actions do not call the model or mutate versions.

### 9.2 Summary Table and Complete Card

The summary table is for quick comparison. It should contain a short title, portfolio role, contribution type, core difference, derived scientific readiness, separate Profile Fit, evidence status, and a candidate-specific next action. It should not contain an internal ID, full pitch, long evidence text, unexplained `seed`, or a shared English fallback recommendation.

A full card is expected to surface:

1. D#, short title, role, route, stage, contribution type, and family.
2. One-line thesis and why it matters.
3. A conditional representative scenario without invented numbers.
4. Real-world significance: one concrete decision or process the proposal could affect, with the potential effect stated conditionally rather than as a verified outcome.
5. Problem, thesis, mechanism, competing explanation, and boundary.
6. Innovation type, delta, and non-routine explanation.
7. Contribution package and directly readable draft hypotheses with predictions/tests.
8. Minimum validation, supporting/falsifying outcome, and explicitly unconfirmed dataset/baseline/metric.
9. Evidence level, what it can support, what it cannot support, and required reading upgrade.
10. Candidate differences, dependencies, mergeable genes, and non-mergeable parts.
11. Applicable implications with supported/inferred/speculative status and conditions.
12. Three formal scores, derived summary, Profile Fit, strength, bottleneck, upside/uncertainty diagnostics, and why maturity is currently limited.
13. Risk, early signal, mitigation, kill criterion, and candidate-specific recommendation.

Scientific content comes from the Candidate, ScoreReport, and LLM final-card translation. The renderer performs layout/localization only. A missing upstream explanation must remain visibly degraded rather than being supplied by a deterministic template.

### 9.3 Family and Dependency Presentation

A benchmark/system candidate, an algorithm that uses that system, and an empirical evaluation that depends on the system are not three independent paper choices. Gate1 should describe their shared family, distinct roles, dependency direction, and possible research-program combinations. Portfolio diversity aims to avoid presenting highly dependent members as three unrelated alternatives; it can still retain them transparently when their joint structure is useful.

### 9.4 Gate1 Actions and Version Semantics

| Action | Calls LLM | New candidate | Preserves history |
| --- | ---: | ---: | ---: |
| Inspect score/evidence/lineage/hypotheses/contributions/genome/files/population or compare | No | No | Yes |
| Select a complete candidate | Not at Gate1; T4.5 may call later | No | Yes |
| Continue one evolution round | Yes | Possibly | Yes |
| Focus evolution on D# | Yes | Possibly | Yes; other active candidates remain |
| Regenerate a route | Yes | Possibly | Yes; old route output remains |
| Create crossover | Yes, compatibility first | Only if approved | Yes |
| Compose selected components | Yes, compatibility then confirmation | After confirmation | Yes |
| Keep parallel | No | No | Yes |
| Change orientation | May re-score/recompile orientation view | No | Yes |
| Roll back population | No | No | Yes; changes active pointer only |
| Pause | No | No | Yes |

A seed can be visible and evolve further, but should not reach T4.5 merely because it sounds novel. A provisional Seed is selection-ready only when it has an independent score, a complete LLM Final Card, a traceable core thesis, one or more LLM-authored falsifiable draft hypotheses, and current fingerprints. Its evidence, maturity, and single-hypothesis limitations are carried into T4.5 as explicit audit warnings rather than treated as a post-confirmation failure.

Pass2 adds grounding, risk, and selection guidance; it may not hide a candidate. In particular, when `constraint_status=not_supported_by_current_evidence`, the candidate may remain Gate1-visible for evidence/mechanism enrichment or further evolution, but its `screening_recommendation` must be `revise_before_selection` or another non-direct-selection status. It must never be `proceed`. This prevents an evidence-needing direction from being presented as ready for final selection without deleting a potentially creative idea.

Gate1 is a persistent research conversation, not a one-line command. A researcher can enter `inspect D1`, ask why a score was assigned, compare `D1` with `D3`, then return to a planned action without regenerating T4. Enter adds a line to the current turn. `Ctrl+D` submits the text already entered; a standalone `END` provides the same submission in terminals that capture EOF. A bare `D1` is deliberately ambiguous and asks whether the researcher means inspect, proceed, or optimize. Inspection and comparison are local read-only actions. A proceed, optimization, composition, or re-exploration request is first restated as an operation plan and needs a second confirmation.

The path depends on that confirmed action. Selecting one ready Candidate follows `T4 -> T4-GATE1 -> T4.5` and writes a pre-novelty selection receipt. It does not rerun T4. Evolution, focus, route regeneration, or an approved composition follows `T4 -> T4-GATE1 -> T4` and creates a new preserved version before returning to Gate1. A read-only action stays at Gate1. EOF with no submitted text pauses the workspace with its Gate intact. EOF while an operation plan awaits confirmation preserves that draft and never executes it. `resume` reopens the same durable Gate and does not repeat a completed T4 model run.

### 9.5 Selection Boundary for T4.5

A candidate selected for T4.5 must be current, independently scored, traceable, and bound to a current selection fingerprint. It needs a complete LLM Final Card and at least one LLM-authored falsifiable draft hypothesis; a Seed satisfying those inputs is explicitly provisional, not mature by implication. T4.5 receives a Pre-Novelty brief and search targets; this is not a declaration that novelty has already passed external audit.

### 9.6 Pass2 Visibility and Evidence-needing Candidates

Pass2 may describe risk, uncertainty, and an enrichment path but cannot hide a candidate. A candidate marked `not_supported_by_current_evidence` remains visible for comparison and further evolution, while its non-direct-selection status remains explicit.

---

## 10. Prompt, Schema, Validator, UI, and Resume Consistency

**Every T4 change crosses this chain.**

```text
Prompt instruction
  -> payload builder
  -> tolerant parser / normalization
  -> Pydantic schema
  -> role-specific semantic validation
  -> controller contract
  -> persisted artifact
  -> resume loader
  -> native / legacy projection
  -> Gate1 ViewModel / Rich renderer
  -> State Machine failure classification
  -> regression fixture
```

Typical failures are prompt/schema disagreement on `parallel`; a generator that permits seeds while projection demands a final card; a three-dimension scorer incorrectly blocked by a legacy grid; Profile Fit becoming a hidden multiplier; a renderer requiring a field no LLM role emits; duplicate catalog injection; or a persisted dynamic gate requiring a current registry entry during resume.

## 11. Configuration, Budgets, and Cost Boundaries

```text
config/system_config/t4_evolution.yaml
config/system_config/idea_scoring_rubric.yaml
config/system_config/idea_evidence_permissions.yaml
config/system_config/idea_evolution_operators.yaml
config/system_config/t4_target_profiles.yaml
config/system_config/gates.yaml
```

Current defaults should be understood as cost/coverage targets: P0 maximum 14, active target 7 with a 6-8 desired range, portfolio 1-3, mutation 2-4, crossover 0-2, at most six offspring, opportunity range 3-6, route concurrency two, and scoring batches of three. `family_similarity_threshold=0.45` only recalls candidates for comparison; `complexity_growth_ratio_limit=1.8` is a diagnostic. Default bridge policy `allow_abstract_with_upgrade` permits catalog/abstract material as creative context, not mechanism certification.

## 12. Debugging, Artifact Inspection, and Regression Verification

### 12.1 What to Inspect First

| Symptom | Inspect first | What to determine |
| --- | --- | --- |
| Cross-domain appears empty | bridge plan, canonical catalog index, per-bridge catalog | Catalog absence vs legacy fallback vs no deep-read note vs retrieval failure |
| Route lacks candidates | route checkpoint and diagnostics | Supported/partial/unsupported reason and repair attempt |
| P0 seems too thin | enrichment receipt/diagnostic and dossier | Whether seed survived and enrichment degraded locally |
| Format error | structured-output diagnostics and role repair artifact | Fence/YAML/alias/semantic-repair path |
| Missing score | scoring receipts, isolation plan, unscored receipt | Candidate must stay visible without synthetic ranking |
| No crossover child | plan, compatibility decision, deferral | Parallel/rejected/uncertain is normal no-merge |
| Child missing from population | round diagnostics, Gene Delta, complexity, survival | Local plan failure vs cosmetic/regressive vs Pareto/diversity outcome |
| Gate1 card incomplete | final-card artifact and projection files | Card/projection degraded rather than candidate lost |
| Resume Gate error | `state.yaml` pending gate and State Machine refresh | Persisted dynamic gate should be resumed without registry crash |
| Directive not recognized | Gate selection record, directive artifact, D# mapping | Public-ID resolution and typed directive parse |

### 12.2 Long-term Regression Coverage

At minimum, cover:

- fenced JSON, YAML, trailing comma, prose-wrapped mapping, one-item arrays, aliases, numeric strings, `parallel`, and one-string conflicts;
- historic five-dimension score migration and fresh three-dimension scoring without a legacy grid;
- route timeout/underfill, seed-enricher failure, score isolation, unscored survival, interaction-review degradation, failed/no-improvement child, and all-incompatible crossovers;
- final-card failure with a still-visible Gate1 population;
- abstract/catalog material never promoted to mechanism support;
- forged SourceRef, parent/plan/donor map, ID collision, stale fingerprint, and legacy overwrite rejection;
- `run-task T4` continuing after pre-run confirmation;
- T4-specific and generic runtime recovery gates, including resume from a missing registry entry;
- D# directives, rich/wide/narrow terminal readability, and no long pitch in the summary table;
- bounded real-API and Docker smoke tests that record recovery/degradation without exposing keys or sensitive data.

#### Format and Compatibility

Exercise fences, YAML, aliases, one-item envelopes, numeric strings, localized no-merge aliases, historic score migration, and persisted dynamic Gates.

#### Local Failure and Continuity

Exercise route underfill, enrichment failure, score isolation, interaction degradation, child deferral, card deferral, and small populations.

#### Scientific and State Integrity

Exercise evidence-permission boundaries, SourceRef/parent/plan/donor-map integrity, ID collision prevention, fingerprint validity, and Legacy isolation.

#### Interaction and Recovery

Exercise Pre-run continuation, D# directives, T4 and generic recovery gates, persisted-gate resume, and narrow/wide terminal projection.

#### Bounded Real API and Container Smoke

Use a small non-sensitive workspace, record provider/recovery outcomes without keys, and verify Docker artifact persistence plus resume behavior.

### 12.3 What Not to Do When Repairing T4

- Do not delete candidates, scores, or populations to hide a schema error.
- Do not rerun a whole round because one route or child failed.
- Do not delete a cross-domain catalog because no Bridge note has been deeply read.
- Do not let legacy `ideation.j2` silently take over the native controller.
- Do not fabricate citations, datasets, metrics, results, or mechanisms to fill a card or pass a validator.
- Do not pad a portfolio with dependent members of one family merely to show three cards.
- Do not trade evidence truth for a higher pass rate.

---

## 13. Current Boundaries and Ongoing Audit Priorities

`auto` is currently a configurable 0-3 round budget mode, not an LLM policy that independently decides whether another round is worthwhile. Any future automatic round decision should write an explicit, reviewable artifact and preserve researcher override.

The deterministic Interaction Graph remains a shortlist heuristic, never a semantic verdict or score. Candidate Enricher, Interaction Reviewer, and Final Card Compiler are high-value LLM enrichments whose failures must remain local and visible. Legacy projection remains compatibility-only. Cross-domain catalog migration remains non-destructive and must keep canonical/legacy records from being injected twice.

### 13.1 Contract postmortem: why a legacy flow could run while the native path exposed defects

Native T4 makes previously implicit seams explicit: Cross-domain catalogs and Bridge notes have distinct evidence permissions; native Candidates, Scores, Final Cards, and legacy projections are separate artifacts; and a resume must compare the same scientific input semantics used to create the Population. A path that appeared healthy under a legacy projection can therefore reveal a real contract defect when the native workflow validates the source artifact directly. The correct response is to align the producing and validating contracts, preserve durable recovery state, and add a focused regression test rather than weaken the Gate or fabricate a fallback result.

## 14. Code Map

| Area | Main implementation |
| --- | --- |
| Controller, P0/P1/P2, checkpoints, local repair | `researchos/ideation/evolution_controller.py` |
| Candidate, score, plan, and card schema | `researchos/ideation/models.py` |
| LLM roles, payloads, parser, semantic repair | `researchos/ideation/llm_roles.py` |
| Four-state tolerant recovery | `researchos/ideation/response_recovery.py` |
| Evidence Index and permissions | `researchos/ideation/evidence.py` |
| Cross-domain catalog migration/loading | `researchos/runtime/bridge_catalog.py` |
| Pre-run inspection and config | `researchos/ideation/prerun.py` |
| Score weights and orientation | `researchos/ideation/target_profile.py` |
| Family, Pareto survival, portfolio | `researchos/ideation/population.py` |
| Interaction Graph | `researchos/ideation/interaction.py` |
| Native-to-Gate1 compatibility projection | `researchos/ideation/legacy_projection.py` |
| Selection to T4.5 compilation | `researchos/ideation/selected_compilation.py` |
| Gates, directives, resume, runtime recovery | `researchos/orchestration/state_machine.py` |
| Runtime role assembly and card compilation | `researchos/runtime/orchestrator.py` |
| Complete Pipeline Runner | `researchos/cli_runners/complete_pipeline.py` |
| SingleTask Runner | `researchos/cli_runners/single_task.py` |
| Rich progress rendering | `researchos/ui/idea_evolution_renderer.py` |
| Prompt templates | `researchos/prompts/idea_*.j2` |
| Default exploration parameters | `config/system_config/t4_evolution.yaml` |

## 15. Final Principles

```text
Validators protect integrity.
Repair loops protect continuity.
LLM agents preserve and deepen scientific imagination.
Evolution can work with incomplete ideas without pretending they are complete.
Cross-domain context broadens search without becoming false evidence.
Human Gates retain authority over research direction and exploration cost.
```

The following compact operational references are intentionally artifact-oriented. They make the boundary between creative LLM work and durable controller state inspectable without converting the documentation into a second workflow implementation.

```text
ideation/evidence/evidence_index.jsonl
ideation/evidence/evidence_index_summary.json
```

```text
ideation/evolution/routes/round_0/<route>.json
```

| Evidence condition | Candidate use | Certification boundary |
| --- | --- | --- |
| Full or partial reading | Bounded mechanism grounding | Must remain within the read section and source reference |
| Abstract record | Discovery and analogy | Cannot certify a mechanism or result |
| Metadata record | Resource lead | Cannot become a scientific claim |
| Synthesis inference | Reframing | Cannot act as independent support |
| Brainstorm context | Creative leap | Cannot become a paper, dataset, or finding |
| Catalog-only bridge | Transfer question | Requires a linked note or validation upgrade for support |
| User seed | Research constraint | Does not itself prove an external fact |

```text
ideation/evolution/enrichment/<candidate-id>.json
ideation/evolution/diagnostics/enrichment_<candidate-id>_attempt_<n>.json
```

```text
ideation/evolution/scoring/<batch>.json
ideation/evolution/scoring/<batch>.unscored.json
```

| Formation outcome | Durable result | Population effect |
| --- | --- | --- |
| Supported route | Candidate checkpoint | Candidate may enter P0 |
| Partial route | Reduced candidate set and reason | Keep distinct output; do not pad |
| Unsupported route | Explicit receipt | Other routes continue |
| Enriched seed | Updated candidate artifact | May become evolved maturity |
| Enrichment degraded | Original seed plus warning | Seed remains visible |
| Scored candidate | Three-dimension report | Eligible for scientific ranking |
| Unscored candidate | Unscored receipt | Visible, unranked, not synthetic-scored |

```text
ideation/evolution/interactions/P<n>.json
```

```text
ideation/evolution/plans/round_<n>.json
ideation/evolution/offspring/
```

| Evolution condition | Controller action | Lineage result |
| --- | --- | --- |
| Substantive mutation | Admit and union-score child | Parent remains traceable |
| Cosmetic mutation | Do not admit as improvement | Parent remains active |
| Plan deferral | Persist rationale and revisit condition | No child is manufactured |
| Approved crossover | Compile donor map and permit child | Both parents remain traceable |
| Rejected crossover | Preserve parallel parents | No child is created |
| Uncertain crossover | Preserve parents and record uncertainty | Researcher may revisit later |
| Interaction review unavailable | Use degraded deterministic graph | No scientific relation is invented |

```text
ideation/populations/P<n>.json
ideation/portfolio.json
```

```text
ideation/final_cards/portfolio_cards.json
ideation/_candidate_directions.json
```

| Gate1 condition | Visible status | Next safe action |
| --- | --- | --- |
| Complete scored candidate | Candidate-specific card | Select, compare, or evolve |
| Candidate card deferred | Card enrichment diagnostic | Inspect durable dossier or enrich |
| Seed candidate | Exploration-stage warning | Focus evolution or reading upgrade |
| Evidence-needing candidate | Revise before selection | Enrich mechanism/evidence; do not proceed |
| Unscored candidate | Unranked | Repair scoring or keep for discussion |
| Parallel family members | Relationship/dependency view | Keep parallel or request composition review |
| Stale selection | Reopen Gate1 | Do not bind T4.5 to old population |

The persisted recovery control surface is `state.yaml`, `_runtime/recovery/<task>_runtime_recovery.json`, `t4_recovery_gate`, and `runtime_recovery_gate`. Targeted repair works only on the implicated artifact or runtime cause and reuses valid work. Legacy bounded-extension records are treated as compatibility diagnostics in current default runs; inspect/pause preserves the pending diagnostic; exit removes no artifact; and a hard integrity failure keeps the affected operation blocked rather than allowing a false source or state binding.
