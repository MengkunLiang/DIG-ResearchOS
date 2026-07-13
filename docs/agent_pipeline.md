# ResearchOS Pipeline

The canonical topology is [config/system_config/state_machine.yaml](../config/system_config/state_machine.yaml).
This document explains the researcher-facing contract, not private model
reasoning. All stages use Stage Start -> Progress -> Summary and emit an
artifact manifest.

## Main Flow

```text
T1 -> T2 -> T3 -> T3.5
  -> T3.6 survey gate (optional) -> T4 -> T4.5
  -> T5 external handoff -> T7 evidence/claims -> T7.5 decision
  -> T8 manuscript/review -> T9 submission bundle
```

`HELLO` is a standalone smoke task, not the main-chain origin. Legacy internal
experiment nodes remain compatibility-only; the default research path compiles
an external-executor handoff and later ingests/audits observed results.

## Stages

| Stage | Research question | Key outputs | Human control |
| --- | --- | --- | --- |
| T1 | What is in scope and what constraints/seed materials govern it? | `project.yaml`, scope/bridge artifacts | Scope and bridge gates |
| T2 | Which source-backed papers form the credible candidate pool? | verified papers, domain map, queues, backlog, search log | Coverage/language parameter gate |
| T3 | What does each retained work actually support? | paper notes/cards, comparison table, reading audit | Access/evidence pauses |
| T3.5 | What mechanisms, tensions, contribution spaces, and transfers emerge? | synthesis/workbench, missing-area audit | Optional Survey decision and current-corpus vs targeted-retrieval preference |
| T3.6 | Is a taxonomy-driven survey warranted and sufficiently evidenced? | survey plan/state/sections/audit/real PDF | Survey, outline, corpus, compile recovery gates |
| T4 | Which evidence-grounded contribution direction should be pursued? | Pass1/Pass2 pool, candidate cards, selected brief, hypotheses | Gate1 selection/merge/reanalysis |
| T4.5 | Is the selected contribution differentiated from nearest work? | novelty/collision audit, baseline/claim constraints | Novelty human review |
| T5 | What can an external executor implement without inventing protocol? | handoff pack, executor selection, project-specific skills | Executor gate |
| T7 | What did real runs produce, and what claims survive audit? | ingest, integrity audit, result-to-claim, evidence pack | Evidence sufficiency decision |
| T7.5 | Is evidence sufficient to write, re-experiment, reframe, or stop? | PI decision record | Human admission decision |
| T8 | How are sources/results transformed into an evidence-aligned paper? | style, storyline, sections, reviews, revisions, claim audit | Style/template gate |
| T9 | Is the submission bundle internally consistent and genuinely compiled? | bundle, compile report, PDF/source fingerprint | Environment/recovery pause |

## T2 And T3 Visibility

T2 displays query portfolio deduplication, source contribution, metadata
verification, score distribution, citation-graph hints, reading queue and
backlog reasons. Scores and graph structure are prioritization hints, not final
academic judgments.

T3 reports per-paper access/evidence level, page coverage, extraction/truncation
status, mechanism evidence, design rationale, boundaries, tensions, bridge
points, and unsupported fields. Full text, partial text, abstract-only, and
metadata-only evidence remain visibly distinct.

After deep reading, eligible shallow records may be abstract-read in
provider-context-adaptive batches. The batch plan uses the active model binding
and tokenizer rather than a fixed papers-per-call number. Each paper still
receives a separate note and never becomes full-text evidence merely because it
shared an LLM call with other abstracts.

## T3.6 Survey Branch

T3.6 is a survey paper branch, not a synthesis-to-TeX conversion. Its compact
default structure is Introduction, Background/Scope, Taxonomy, Comparative
Analysis, Challenges, Future Directions, Conclusion, and Abstract.

The T3.5 Survey gate first asks whether to skip Survey, write with the current
corpus, or request one targeted supplement before writing. The preference is
persisted in `drafts/survey/decision.json`, made visible during taxonomy/corpus
planning, and does not turn search leads into Survey evidence. Use
`survey-evidence-package` when the researcher wants a standalone, guided
pre-writing sufficiency/taxonomy workflow.

- `build_survey_state` produces section writing contracts and preserves valid
  completed sections when it is reissued under the same plan.
- `build_survey_figures` may create only
  `drafts/survey/figures/fig_taxonomy_overview.pdf`.
- The figure encodes explicit taxonomy labels and direct resolved note-card
  links. It never encodes performance, relative gains, baselines, source scores,
  or inferred evidence strength.
- `latex_compile` requires a real backend. Repair TeX/Docker environment errors
  before resume rather than spending further writing retries.
- Each `T3.6-SEC-*` worker is sandboxed to its one declared section plus the
  matching shared state entry. A valid interrupted section is validator-checked
  and advanced on resume instead of being silently rewritten.

## T4 Candidate Governance

T4 has three clearly separated sources:

1. **Mainline forward divergence** from synthesis, cards, user seeds, survey
   insights, and controlled free reasoning.
2. **Cross-domain bridge candidates** with transferable mechanism, source
   evidence, mapping, and migration risk.
3. **Coverage supplements**: mechanism challenge, reverse operation, subgroup
   failure, and missing-area exploration. They are checks for omitted angles,
   not four compulsory Idea templates.

Each supplement channel can be `unsupported`. A retrieval coverage gap is never
silently promoted to a research gap.

Pass1 creates candidates and distribution checks. Pass2 records nearest work,
grounding, routine risk, feasibility, contribution character, and recommendation
without silently deleting candidates. Gate1 cards present a short title,
innovation, H1/H2/H3 drafts, merge opportunities, scoring rationale, supporting
note sections, risk, and protocol boundary.

The CLI first shows a compact candidate index with lane, innovation type,
hypothesis count, and Pass2 recommendation, then prints the complete card. A
long direction description or supporting-paper title is wrapped into the card
body rather than being used as an unbounded heading. Candidate cards retain the
full direction, source-note paths, and protocol evidence status for inspection.

During generation, T4 writes a durable progress ledger and emits only bounded
public events: context-pack preparation, per-candidate Pass1 persistence,
supplement-channel state, Pass2 recommendation, persisted score snapshot, and
Gate1-card completion. It never emits hidden model deliberation. The Gate1
selection is fingerprinted against the displayed pool; changed candidates force
a new presentation, while `select`, `merge`, `new idea`, and `reanalyze` keep
their own durable lineage records.

`minimum_experiment` is a candidate proposal. `supported` and `user_provided`
details require `source_refs`; `proposed_not_verified` and `unknown` remain
explicitly provisional. A metric such as AUUC/Qini is allowed when source-bound
for the current project and must not be guessed because of the research topic.

## T5-T7 External Evidence Path

ResearchOS prepares an executor handoff but does not represent mock or external
natural-language summaries as empirical fact. External execution returns
declared raw artifacts, configs, logs, hashes, and result packs. T7 ingests,
audits integrity/fairness/provenance, maps observed evidence to claims, and
creates must-not-claim boundaries. A mock dry run verifies the protocol chain
only and contains no fabricated metric values.

## T8-T9 Writing And Submission

T8 builds a resource index and alignment matrix before section drafting. Venue
profiles shape narrative density but are not official page-limit or policy
sources. T9 assembles the bundle, invokes real compilation, records warnings and
errors, fingerprints source/PDF, and checks that claim audit matches the current
version.

## Resume Rules

- `resume` continues the current workspace after the named blocker is fixed.
- `run-task <task>` is for isolated diagnosis and does not advance the main
  pipeline.
- `validate --task <task>` checks a repaired artifact before a costly resume.
- `run --from <workspace> --start-task <task>` initializes a new project from
  another workspace; it is not a state merge.

See [logging.md](logging.md) for observable states and [runtime.md](runtime.md)
for implementation contracts.
