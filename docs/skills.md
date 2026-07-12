# ResearchOS Skill Capability Map

This map describes user-facing, standalone Skills. Every public Skill is a
guided, workspace-backed session: `describe-skill` shows exact upload paths;
`--interactive` can collect pasted material through the restricted intake
turn; `skill-status` shows persisted work and the recovery command. A missing
input never becomes invented research evidence.

```bash
researchos list-skills --workspace ./workspace/project-a
researchos describe-skill pdf-note-card --workspace ./workspace/project-a
researchos browse-skills --workspace ./workspace/project-a
```

## Atomic Literature Work

| Need | Skill | Required material | Main outputs |
| --- | --- | --- | --- |
| Register your own PDFs, data, code, and restrictions | `research-material-ingest` | material inventory and listed files | user-seed manifest and import summary |
| Register DOI, arXiv, provider ID, or title | `paper-identifier-resolver` | `user_inputs/paper-identifier-resolver/identifiers.md` | source-traceable records, report, conservative BibTeX candidates |
| Read one uploaded PDF | `pdf-note-card` | `user_inputs/pdf-note-card/paper.pdf` | standalone section-aware note card and index |
| Verify one precise question from one PDF | `paper-section-evidence` | PDF plus request | section/page-anchored evidence report and JSON |
| Compare sources | `paper-comparison` | workspace-relative note/record path list | comparison report and JSON |
| Explore one-hop references from seed papers | `citation-graph-explorer` | DOI/OpenAlex seed list | bounded citation neighborhood and mechanical domain map |
| Build a review-ready table | `literature-evidence-matrix` | bounded note/record path list | CSV, coverage report, matrix JSON |
| Separate evidence-supported opportunities from retrieval gaps | `literature-gap-map` | bounded note/synthesis/matrix path list | gap map, counter-evidence, and targeted follow-up needs |
| Curate a bibliography | `citation-library-curator` | `.bib` library | separate candidate library and provenance audit |
| Retrieve focused material | `literature-query-plan`, `literature-evidence-scout` | topic/question brief | query portfolio or evidence records |
| Verify a precise paper-note section | `paper-note-review` | claim/section request plus existing notes | section-level evidence report |
| Examine available data/code/baselines | `literature-resource-scout` | research brief | source and reproducibility inventory |

`pdf-note-card` writes under `literature/skill_pdf_note_cards/` and deliberately
does not pretend to complete the T3 deep-read queue. To make a canonical T3
note, use the pipeline's queue/Reader workflow. Identifier resolution does not
claim that an API failure means the cited paper is absent; every unresolved or
ambiguous input remains in its output record.

`paper-section-evidence` is the narrow alternative when a user needs one answer
from exact Method/Experiment/Limitations sections rather than a whole note card.
`citation-graph-explorer` only performs a bounded first hop and labels provider
fallbacks. Its domain map is an organization aid, not a relevance, novelty, or
quality score. `literature-gap-map` never turns a missing search result into a
research claim: every potential opportunity carries source anchors, counter-evidence,
and a concrete evidence upgrade path.

## Paper-Card Evidence Boundaries

Pipeline-created cards under `literature/paper_notes/`,
`literature/paper_notes_bridge/`, and `literature/paper_notes_abstract/` are
not limited to T3.5, T4, and T8. They are optional, provenance-bearing inputs
for T4.5 novelty/collision review, T5 handoff construction, external execution
context, T7 audit/claim-boundary review, and T8 related-work/method writing.

They may support mechanism and design-rationale context, baseline identity and
reproduction provenance, limitations, boundary conditions, and related-work
traceability. They may not support the proposed method's empirical performance,
replace `result_pack`/raw results/integrity audit/result-to-claim mapping, or
expand the T4.5 novelty verdict. T5 writes
`external_executor/paper_card_evidence_index.json` and repeats these boundaries
in `external_executor/input_manifest.json` so an external executor can find
relevant cards without treating them as experiment facts.

## Synthesis, Ideas, And Experiments

| Need | Skill | Main outputs |
| --- | --- | --- |
| Bound a topic and research question | `research-scope` | scope brief and structured scope record |
| Form a human-selectable candidate pool | `idea-fanout-jury` | evidence/score/risk jury report and JSON |
| Compile one selected direction | `hypothesis-compiler` | falsifiable hypotheses and test plan |
| Review an experimental design | `experiment-design-review` | controls/metrics/risk review |
| Build one source-audited taxonomy visual | `survey-visuals` | taxonomy-only manifest and one supported figure, or explicit `skipped` |

External execution, result ingestion, and executor-specific Skills remain a
separate protocol. Standalone Skills can prepare inputs and audits but do not
launch or alter external executors.

## Writing, Review, And Submission

| Need | Skill | Main outputs |
| --- | --- | --- |
| Build the research story and section plan | `paper-outline` | outline, storyline, evidence map, readiness report |
| Draft an evidence-bounded manuscript | `paper-write` | LaTeX draft, craft and claim audits |
| Map several intended claims before drafting | `claim-evidence-map` | claim-to-section evidence map and JSON |
| Check citation keys and provenance | `citation-provenance-audit` | citation/claim provenance report |
| Conduct a non-destructive peer review | `paper-peer-review` | review report, structured findings, priorities |
| Check a draft against a concrete venue policy | `venue-fit-review` | venue-fit report, structured findings, revision order |
| Polish or answer reviews | `paper-polish`, `paper-revision` | independent edited copy or response/revision bundle |
| Deterministically audit strong claims | `paper-claim-audit` | claim audit report and JSON |
| Compile and check submission readiness | `paper-compile`, `submission-readiness` | real PDF/report or readiness decision |

`paper-peer-review` is a review, not a manuscript editor. It applies the
venue-aware UTD/IS and CCF-A story checks already used by the writer, preserves
the source manuscript, and sends approved fixes to `paper-revision`.

## Example Entry Points

```bash
# One uploaded PDF -> source-registered, section-aware note card
mkdir -p ./workspace/project-a/user_inputs/pdf-note-card
cp /absolute/path/to/paper.pdf ./workspace/project-a/user_inputs/pdf-note-card/paper.pdf
researchos run-skill pdf-note-card \
  "重点提取方法、结果、局限与可用于机制分析的 section" \
  --workspace ./workspace/project-a --session-id paper-a-note

# DOI/arXiv/title list -> verified/ambiguous/unresolved records
researchos run-skill paper-identifier-resolver \
  "为 Related Work 解析这些标识符，不补造任何引用字段" \
  --workspace ./workspace/project-a --interactive

# A set of notes -> comparison or evidence matrix
researchos run-skill paper-comparison \
  "比较 treatment heterogeneity 的机制与可用基线" \
  --workspace ./workspace/project-a --interactive

# One focused question -> section and page anchored source evidence
researchos run-skill paper-section-evidence \
  "核验该方法的处理异质性机制与实际报告的局限" \
  --workspace ./workspace/project-a --interactive

# Source notes -> a conservative map of research opportunities and missing evidence
researchos run-skill literature-gap-map \
  "区分可进入 Idea 讨论的问题与仍需补检的文献覆盖不足" \
  --workspace ./workspace/project-a --interactive
```

For every command, `describe-skill <name>` is the source of truth for upload
paths, permitted file types, outputs, and recovery.
