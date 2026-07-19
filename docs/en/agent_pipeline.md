# ResearchOS Pipeline

> [English](../en/agent_pipeline.md) | [中文](../cn/agent_pipeline.md)

The canonical topology is [config/system_config/state_machine.yaml](../../config/system_config/state_machine.yaml). This document explains the researcher-facing contract, not private model reasoning. All stages use Stage Start -> Progress -> Summary and emit an artifact manifest.

## Main Flow

```text
T1 -> T2 -> T3 -> T3.5
  -> T3.6 survey gate (optional) -> T4 -> T4.5
  -> T5 reboost -> T5 specialize executor Skills -> T5 executor gate
  -> T5 external execution/wait
  -> T8 manuscript/review -> T9 submission bundle
```

`HELLO` is a standalone smoke task, not the main-chain origin. Legacy internal experiment nodes remain compatibility-only; the default research path compiles an external-executor handoff and then passes the external executor's research report directly into T8.

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
| T5 | What can an external executor implement without inventing protocol, and what research report is ready for writing? | handoff pack, project-specific Skill suite, specialization execution record, executor selection, `external_executor/executor_research_report.md` | Executor gate |
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

T4 is an artifact-first Research Idea Formation & Evolution workflow. Its Gate1 transition is explicit rather than fixed: selecting a ready Candidate moves from `T4 -> T4-GATE1 -> T4.5`; evolving or optimizing moves from `T4 -> T4-GATE1 -> T4` and creates a new preserved Candidate version; read-only inspection remains at Gate1. Its internal controller forms a Population rather than asking one model to write a final hypothesis immediately.

Before a run, a Rich confirmation panel explains the available paper-reading notes, warnings, the selected mode, expected work, expected duration category, and rollback behavior. Standard mode completes a full `P0 -> P1` round: Evidence Routing, Opportunity Map, asymmetric Multi-route Generation, Idea Genome and Family construction, Independent Scoring, Parent Selection, Mutation/Crossover planning, offspring generation, union rescoring, Idea Contract, Family-level Survival Selection, Population Update, and Portfolio Selection. P0 uses Literature (3), Informed Brainstorm (2–3), one candidate from each supplement route when evidence permits, and one or two Cross-domain/Bridge candidates. These are exploration budgets, not an instruction to manufacture filler. A route may be explicitly `unsupported`; the system records why instead of inventing an Idea. The live panel separates current activity, current deliverable, and following phase: `Research Opportunity Mapping (Opportunity Map)` produces a research-opportunity list rather than a final Candidate, then moves to multi-perspective Idea divergence. Normal presentation uses Rich only for key results such as Evidence Routing, initial Population, independent scoring, and Portfolio; preparation and internal transitions use one compact status line so they do not fill the terminal.

The Evidence Index recalls Core and Bridge notes from both full/partial reading and abstract-level reading. Evidence Permission controls what each note may support: abstract-only material can broaden recall, identify a taxonomy lead, or request a reading upgrade, but cannot validate a mechanism, a design rationale, or a strong final claim. Evidence constrains certification, not imagination: normal Generator routes may use scholarly knowledge, counterfactual reasoning, and structural cross-domain analogy, recorded as `conjectural`, verification-required `CreativeContext`, to propose a leap beyond the supplied text. Every Candidate retains source paths, reading level, uncertainty, and upgrade requirements. If a Route response lacks a required structured field, such as a Bridge explanation, the system performs one targeted repair for that Route only; network, authentication, and provider errors are never retried as content failures. When an interruption occurs after route planning, `resume` reuses the Evidence Index and Opportunity Map with a matching input fingerprint and retries only unfinished Routes.

Generator, Scorer, and Evolver are separate roles. The Generator creates route-scoped Candidates but cannot score or select them. The Scorer receives blinded Candidates and cannot create or rewrite them; it distinguishes current readiness from scientific upside and may retain a high-upside Wildcard for human comparison. The Evolver creates a plan-bounded Mutation Child or Compatibility-gated Crossover Child, or records an explicit no-improvement/incompatibility deferral when forcing a Child would be cosmetic. A mature Idea Card shows a concise thesis, Overall Readiness and five independent dimensions with explanations, research problem, contribution package, draft hypotheses, mechanism chain, risks, Evidence Composition, recommendation, and paths to the underlying paper-reading notes. A provisional Seed can enter T4.5 when it has an independently produced score, a complete LLM Final Card, a traceable core thesis, and at least one LLM-authored falsifiable draft hypothesis. Its seed maturity, evidence gaps, and any single-hypothesis limitation travel with the Pre-Novelty brief as audit warnings. A missing Final Card or score, an empty thesis, or no draft hypothesis remains a clear selection block.

Gate1 shows 1–3 Portfolio Candidates first while retaining 6–8 Active Candidates and the complete Archive. The researcher may proceed with one complete Candidate, run another Generation, focus a Candidate or Idea Family, create a Crossover, compose selected hypotheses/contributions/genes, keep complete Ideas in parallel, inspect score/evidence/lineage, regenerate one Route, rollback, or pause. Read-only actions do not call a model. Every state-changing action explains expected work, whether a model is called, what is retained, rollback availability, and whether T4.5 is reached.

Natural language is parsed into an `IdeaDirective` by an optional LLM parser and then deterministically validated against IDs, components, fingerprints, and confirmation rules. Multiple complete Candidates are parallel by default. Component-level requests create a Human-composed Candidate only after a Compatibility Check, an explicit Gene Donor Map, a second confirmation, Independent Scoring, and a new Population snapshot. Source Candidates are never overwritten or concatenated into a hypothesis file.

Choosing one complete Candidate creates `ideation/hypothesis_brief.yaml`, lineage, search targets, and a Pre-Novelty brief. These artifacts are not an experiment authority. T4 moves directly to T4.5, which audits novelty/collisions first. Only a passing T4.5 verdict may compile formal `hypotheses.md`, the Contribution–Hypothesis Mapping, Validation Map, Kill Criteria, `exp_plan.yaml`, and the post-novelty formalization manifest consumed by T5.

## T5 To T8 External Evidence Path

ResearchOS prepares an executor handoff, then runs `T5-SPECIALIZE-EXECUTOR-SKILLS`: an LLM consumes the repository `project-skill-specialization` Skill, calls the deterministic wrapper, and ResearchOS independently validates the published context/report/13-Skill suite before executor selection. Phase B uses `resources/` as its source-material root, while `external_executor/expr/` holds only deployed runnable baseline or method assets. External execution must leave the core T8 handoff file at `external_executor/executor_research_report.md`; other files under `external_executor/` remain supporting provenance. After evidence packaging, `writer-handoff` compiles and validates the report, final status, result pack, manifest, figures, and tables. The root then executes the routed `run-task T8` command in the same executor session. ResearchOS independently accepts the frozen handoff, derives T8 evidence/claim indexes, and delegates to the existing full T8 pipeline. A mock dry run verifies the protocol chain only and does not create empirical claims. See the [T5 External Executor Guide](t5_external_executor.md) for commands, the material gate, and A-F artifact paths.

## T8-T9 Writing And Submission

T8 consumes `external_executor/executor_research_report.md` as its primary fact report and the accepted result pack, manifest, raw evidence, realized method package, attribution, figures, and tables through its resource/evidence indexes. It builds a resource index and alignment matrix before section drafting. Venue profiles shape narrative density but are not official page-limit or policy sources. T9 assembles the bundle, invokes real compilation, records warnings and errors, fingerprints source/PDF, and checks that claim audit matches the current version.

## Resume Rules

- `resume` continues the current workspace after the named blocker is fixed.
- `run-task <task>` is for isolated diagnosis and does not advance the main pipeline; `run-task <task> --from <source>` first copies the task's declared inputs and never modifies the source workspace.
- `validate --task <task>` checks a repaired artifact before a costly resume.
- `run --from <workspace> --start-task <task>` initializes a new project from another workspace; it is not a state merge.
- `resume --from-task T3.6` is accepted as the public Survey-decision alias for `T3.6-GATE-SURVEY`.

See [logging.md](logging.md) for observable states and [runtime.md](runtime.md) for implementation contracts.
