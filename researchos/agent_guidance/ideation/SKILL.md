---
name: ideation
description: LLM guidance for ResearchOS T4 idea generation.
---

# T4 Ideation Guidance

Use this guidance for the Research Idea Formation & Evolution workflow. T4 is not a one-shot hypothesis writer: it forms a traceable Candidate Population, evolves it, and then asks the researcher to decide what should advance.

## Evidence Responsibility

- Read the workspace-derived Evidence Index before proposing an Opportunity Map. Core and Bridge paper notes from full/partial reading and abstract-level reading are all available for recall.
- Respect Evidence Permission. Abstract-only material may broaden recall, identify taxonomy or bridge leads, and trigger a reading upgrade. It must not establish a mechanism, validate a design rationale, or support a final claim.
- Preserve source paths, section locators, reading level, uncertainty, and upgrade requirements in every Candidate Genome. Do not turn a missing source into an invented citation.
- Treat `missing_areas.md` as retrieval-coverage telemetry. It can motivate a Gap Exploration route only after synthesis and paper notes support a research question.

## Role Boundary

- `IdeaGeneratorAgent` plans Opportunities and generates route-scoped Idea Seeds. It never scores, ranks, selects, archives, or declares novelty.
- `IdeaScoringAgent` independently scores Candidates. It does not generate, rewrite, merge, or delete Ideas. Its input hides route and parent/child identity.
- `IdeaEvolverAgent` creates only plan-bounded Mutation Child or Compatibility-gated Crossover Child Candidates. It cannot alter Parent selection, Gene Donor Maps, or Survival Selection.
- Runtime code owns fingerprints, Evidence Permission checks, families, lineage, contracts, atomic artifacts, and rollback. It must not author scientific content.

## P0 Formation

- Generate an asymmetric P0: Literature 3, Informed Brainstorm 2-3, Mechanism Challenge 1, Reverse Operation 1, Subgroup Failure 1, Gap Exploration 1, and Cross-domain/Bridge 1-2 when current evidence permits.
- A supplementary or Bridge route may be `unsupported`. Record the reason and preserve the route result rather than fabricating an Idea.
- If a workspace has a confirmed bridge plan, return one LLM-authored Bridge review for every bridge. A supported bridge Candidate is visible at Gate1. A missing viable Candidate requires an explicit `no_candidate_available` escape hatch with reason, kill criterion, and revisit condition.
- Every mature Candidate requires a concise title, one-line thesis, complete Idea Genome, 2-4 Contributions, 2-4 draft hypotheses, falsification path, risks, Evidence Composition, and an LLM-authored Gate1 presentation.

## Evolution And Selection

- Standard mode is a full `P0 -> P1` round: independent scoring, Parent Selection, 2-4 Mutation Child plans, 0-2 compatibility-gated Crossover Child plans, union rescoring, Idea Contract, Gene Delta, Complexity Inflation, Family-level Survival Selection, Population Update, and Portfolio Selection.
- Keep Parents and archived Candidates. Do not silently remove an Idea because it was not selected for the Portfolio.
- A Candidate can be selected as a complete direction. Multiple complete Candidates are parallel by default; never merge them merely because the user named more than one.
- A cross-Candidate Hypothesis, Contribution, or Gene request creates a Human-composed Candidate only after Compatibility Check, independent scoring, and explicit confirmation. Never concatenate hypothesis strings into a final file.
- A complete Candidate selection first creates `ideation/hypothesis_brief.yaml`, `ideation/selected/hypothesis_lineage.json`, and `ideation/selected/t45_search_targets.json`. These are Pre-Novelty drafts, not formal experimental claims.
