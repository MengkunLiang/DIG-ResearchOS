# ResearchOS Pipeline

> [English](../en/agent_pipeline.md) | [中文](../cn/agent_pipeline.md)

The canonical topology is [config/system_config/state_machine.yaml](../../config/system_config/state_machine.yaml). This document explains the researcher-facing contract, not private model reasoning. All stages use Stage Start -> Progress -> Summary and emit an artifact manifest.

## Main Flow

```text
T1 -> T2 -> T3 -> T3.5
  -> T3.6 survey gate (optional) -> T4 -> T4.5
  -> T5 external handoff -> T7 evidence/claims -> T7.5 decision
  -> T8 manuscript/review -> T9 submission bundle
```

`HELLO` is a standalone smoke task, not the main-chain origin. Legacy internal experiment nodes remain compatibility-only; the default research path compiles an external-executor handoff and later ingests/audits observed results.

## Stages

| Stage | Research question | Key outputs | Human control |
| --- | --- | --- | --- |
| T1 | What is in scope and what constraints/seed materials govern it? | `project.yaml`, scope/bridge artifacts | Scope and bridge gates |
| T2 | Which source-backed papers form the credible candidate pool? | verified papers, domain map, queues, backlog, search log | Coverage/language parameter gate |
| T3 | What does each retained work actually support? | paper notes/cards, comparison table, reading audit | Access/evidence pauses |
| T3.5 | What mechanisms, tensions, contribution spaces, and transfers emerge? | synthesis/workbench, missing-area audit | Optional Survey decision and current-corpus vs targeted-retrieval preference |
| T3.6 | Is a taxonomy-driven survey warranted and sufficiently evidenced? | survey plan/state/sections/audit/real PDF | Survey, outline, corpus, compile recovery gates |
| T4 | Which evidence-grounded research idea should be evolved or selected? | P0/P1/P2 Population, Evidence Index, scores, lineage, Portfolio, Pre-Novelty brief | Pre-run confirmation; Gate1 directives, composition, rollback |
| T4.5 | Does the selected Pre-Novelty idea remain differentiated after targeted novelty/collision review? | novelty/collision audit; on pass only, formal hypotheses, maps, kill criteria, experiment plan | Novelty human review |
| T5 | What can an external executor implement without inventing protocol? | handoff pack, executor selection, project-specific skills | Executor gate |
| T7 | What did real runs produce, and what claims survive audit? | ingest, integrity audit, result-to-claim, evidence pack | Evidence sufficiency decision |
| T7.5 | Is evidence sufficient to write, re-experiment, reframe, or stop? | PI decision record | Human admission decision |
| T8 | How are sources/results transformed into an evidence-aligned paper? | style, storyline, sections, reviews, revisions, claim audit | Style/template gate |
| T9 | Is the submission bundle internally consistent and genuinely compiled? | bundle, compile report, PDF/source fingerprint | Environment/recovery pause |

## T2 And T3 Visibility

T2 displays query portfolio deduplication, source contribution, metadata verification, score distribution, citation-graph hints, reading queue and backlog reasons. Scores and graph structure are prioritization hints, not final academic judgments.

T3 reports per-paper access/evidence level, page coverage, extraction/truncation status, mechanism evidence, design rationale, boundaries, tensions, bridge points, and unsupported fields. Full text, partial text, abstract-only, and metadata-only evidence remain visibly distinct.

After deep reading, eligible shallow records may be abstract-read in provider-context-adaptive batches. The batch plan uses the active model binding and tokenizer rather than a fixed papers-per-call number. Each paper still receives a separate note and never becomes full-text evidence merely because it shared an LLM call with other abstracts.

## T3.6 Survey Branch

T3.6 is a survey paper branch, not a synthesis-to-TeX conversion. Its compact default structure is Introduction, Background/Scope, Taxonomy, Comparative Analysis, Challenges, Future Directions, Conclusion, and Abstract.

The T3.5 Survey gate first asks whether to skip Survey, write with the current corpus, or request one targeted supplement before writing. The preference is persisted in `drafts/survey/decision.json`, made visible during taxonomy/corpus planning, and does not turn search leads into Survey evidence. Use `survey-evidence-package` when the researcher wants a standalone, guided pre-writing sufficiency/taxonomy workflow.

- `build_survey_state` produces section writing contracts and preserves valid completed sections when it is reissued under the same plan.
- `build_survey_figures` may create only `drafts/survey/figures/fig_taxonomy_overview.pdf`.
- The figure encodes explicit taxonomy labels and direct resolved note-card links. It never encodes performance, relative gains, baselines, source scores, or inferred evidence strength.
- `latex_compile` requires a real backend. Repair TeX/Docker environment errors before resume rather than spending further writing retries.
- Each `T3.6-SEC-*` worker is sandboxed to its one declared section plus the matching shared state entry. A valid interrupted section is validator-checked and advanced on resume instead of being silently rewritten.
- `T3.6-ASSEMBLE` first creates `survey.tex`, then runs a deterministic audit. Use `audit-survey --workspace <workspace>` after a concrete repair to regenerate `survey_audit.md/json` without contacting a provider. Citation diversity uses both a small-corpus floor and a total-use-scaled repeat limit; it does not reject a 104-use survey solely because a legitimate citation is used 13 times.
- `T3.6-REVIEW` treats `survey.tex` as derived. It reviews and patches only source sections, then uses `assemble_survey` to regenerate the wrapper and `audit_survey_coverage` to refresh evidence checks. Ordinary full-file writes to `survey.tex` are rejected in this phase because a partial context read can otherwise destroy the assembled document. A review-driven assembly makes the prior PDF/report stale by design; T3.6-COMPILE then performs one real compile.

## T4 Candidate Governance

T4 is an artifact-first Research Idea Formation & Evolution workflow. It preserves the public transition `T4 -> T4-GATE1 -> T4 -> T4.5`, while its internal controller forms a Population rather than asking one model to write a final hypothesis immediately.

Before a run, a Rich confirmation panel explains the available paper-reading notes, warnings, the selected mode, expected work, and rollback behavior. Standard mode completes a full `P0 -> P1` round: Evidence Routing, Opportunity Map, asymmetric Multi-route Generation, Idea Genome and Family construction, Independent Scoring, Parent Selection, Mutation/Crossover planning, offspring generation, union rescoring, Idea Contract, Family-level Survival Selection, Population Update, and Portfolio Selection. P0 uses Literature (3), Informed Brainstorm (2–3), one candidate from each supplement route when evidence permits, and one or two Cross-domain/Bridge candidates. A route may be explicitly `unsupported`; the system records why instead of inventing an Idea.

The Evidence Index recalls Core and Bridge notes from both full/partial reading and abstract-level reading. Evidence Permission controls what each note may support: abstract-only material can broaden recall, identify a taxonomy lead, or request a reading upgrade, but cannot validate a mechanism, a design rationale, or a strong final claim. Every Candidate retains source paths, reading level, uncertainty, and upgrade requirements.

Generator, Scorer, and Evolver are separate roles. The Generator creates route-scoped Candidates but cannot score or select them. The Scorer receives blinded Candidates and cannot create or rewrite them. The Evolver can only create a plan-bounded Mutation Child or Compatibility-gated Crossover Child. A mature Idea Card shows a concise thesis, Overall Readiness and five independent dimensions with explanations, research problem, contribution package, draft hypotheses, mechanism chain, risks, Evidence Composition, recommendation, and paths to the underlying paper-reading notes.

Gate1 shows 1–3 Portfolio Candidates first while retaining 6–8 Active Candidates and the complete Archive. The researcher may proceed with one complete Candidate, run another Generation, focus a Candidate or Idea Family, create a Crossover, compose selected hypotheses/contributions/genes, keep complete Ideas in parallel, inspect score/evidence/lineage, regenerate one Route, rollback, or pause. Read-only actions do not call a model. Every state-changing action explains expected work, whether a model is called, what is retained, rollback availability, and whether T4.5 is reached.

Natural language is parsed into an `IdeaDirective` by an optional LLM parser and then deterministically validated against IDs, components, fingerprints, and confirmation rules. Multiple complete Candidates are parallel by default. Component-level requests create a Human-composed Candidate only after a Compatibility Check, an explicit Gene Donor Map, a second confirmation, Independent Scoring, and a new Population snapshot. Source Candidates are never overwritten or concatenated into a hypothesis file.

Choosing one complete Candidate creates `ideation/hypothesis_brief.yaml`, lineage, search targets, and a Pre-Novelty brief. These artifacts are not an experiment authority. T4 moves directly to T4.5, which audits novelty/collisions first. Only a passing T4.5 verdict may compile formal `hypotheses.md`, the Contribution–Hypothesis Mapping, Validation Map, Kill Criteria, `exp_plan.yaml`, and the post-novelty formalization manifest consumed by T5.

## T5-T7 External Evidence Path

ResearchOS prepares an executor handoff but does not represent mock or external natural-language summaries as empirical fact. External execution returns declared raw artifacts, configs, logs, hashes, and result packs. T7 ingests, audits integrity/fairness/provenance, maps observed evidence to claims, and creates must-not-claim boundaries. A mock dry run verifies the protocol chain only and contains no fabricated metric values.

## T8-T9 Writing And Submission

T8 builds a resource index and alignment matrix before section drafting. Venue profiles shape narrative density but are not official page-limit or policy sources. T9 assembles the bundle, invokes real compilation, records warnings and errors, fingerprints source/PDF, and checks that claim audit matches the current version.

## Resume Rules

- `resume` continues the current workspace after the named blocker is fixed.
- `run-task <task>` is for isolated diagnosis and does not advance the main pipeline.
- `validate --task <task>` checks a repaired artifact before a costly resume.
- `run --from <workspace> --start-task <task>` initializes a new project from another workspace; it is not a state merge.

See [logging.md](logging.md) for observable states and [runtime.md](runtime.md) for implementation contracts.
