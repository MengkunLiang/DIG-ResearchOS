---
name: manuscript-writing
description: LLM guidance for staged academic manuscript writing in ResearchOS.
---

# Manuscript Writing Guidance

Use this guidance during T8 writing and revision.

## Core Principle

Write the paper as a staged research argument, not as one long generation. Tools organize resources, inventories, section plans, assembly, and mechanical audits. The Writer LLM is responsible for the scientific story, claims, section prose, and venue-aware positioning.

## Publication Prose and Citation Preferences

Treat these as editorial standards for writing and review. When they conflict with factual accuracy, verified evidence, or a target-venue template, preserve the fact and express it naturally.

- Aim for the clean, precise, and restrained prose expected in strong UTD/FT50/CCF-A work. Do not use empty importance claims, stock transitions, or repeated statements to make a section look longer.
- In ordinary body prose, do not rely on dashes, colons, or label sentences such as
  `Problem:`, `Gap:`, `Insight:`, or `Implication:`. Definitions, equations, tables, contribution lists, and a venue template may require limited punctuation of that kind, but the argument itself should use complete sentences that state causality, contrast, qualification, or consequence.
- Do not construct an argument as a sequence of parallel short sentences. Let each paragraph develop one claim through a readable order such as definition, mechanism, evidence, boundary, and implication. Move to a new term, paper, result, or practical consequence only after explaining why the preceding discussion leads there.
- Keep the section hierarchy compact. Do not create a subsection or `\paragraph{}` for every artifact, claim, or paper. Use a heading only when the argument changes function in a way that helps the reader navigate the paper.
- Explain a technical or theoretical term when it first matters to the argument. When useful, use a real or conditional `such as` scenario to make an abstract mechanism legible. An illustrative example must not be presented as data, a result, or a fact that has not been verified.
- Every citation must be real, available in the bibliography, and semantically matched to the precise claim it supports. Verify the paper's subject, method, setting, and evidence level in its note card, citation pool, or source record before citing it.
  Prefer genuinely relevant work from UTD/FT50/CCF-A venues, leading marketing, economics, management science, information systems, and computer-science outlets, together with foundational, highly cited, influential, and important recent work.
  Venue quality never permits a background citation to support a mechanism, causal effect, empirical result, business implication, or contribution that it does not actually establish.
- Treat citation selection as a source-reading decision, not a formatting operation.
  Do not infer support from a title, author reputation, venue, abstract snippet, or model memory. If the available source record cannot verify the intended wording, remove the citation and dependent strong claim, or use an explicitly bounded motivation statement. Broaden coverage through verified work that represents a distinct development stage, research tradition, comparison dimension, or evidence boundary, never through an unrelated citation bundle.

## Required Stages

- Resource index: inventory project, literature, hypotheses, novelty audit, experiment results, ablations, figures, tables, code, logs, and bibliography.
- Section plan: decide what each section must prove, which artifacts support it, and what remains missing.
- Outline: build the argument arc before drafting prose.
- Section drafts: draft `abstract`, `introduction`, `related_work`, `methodology`, `experiments`, `analysis`, and `conclusion` as separate files under `drafts/sections/`. Limitations are written inside Conclusion as `\subsection{Limitations}`.
- Assembly: combine section drafts into `drafts/paper.tex` mechanically, then polish transitions globally.
- Audit: check citations, numeric values, figure/table references, missing sections, and overclaims.
- Review and revise: treat reviewer reports as a change list; update prose, tables, figures, and audit notes.

## Section Responsibilities

- Introduction: motivation funnel, precise gap, why existing approaches are insufficient, proposed insight, contribution bullets, headline evidence. Do not oversell.
- Related Work: taxonomy and contrastive positioning from synthesis and bibliography. Every citation must map to a real BibTeX key.
- Methodology: describe the proposed mechanism, algorithm/protocol, implementation choices, and how it differs from baselines.
- Experiments: datasets/settings, baselines, metrics, main results, ablations, seed ensemble, compute budget, and quality controls.
- Analysis: connect ablations and failures back to hypotheses and alternative explanations.
- Conclusion limitations subsection: external-executor evidence boundaries, mock/dry-run status, result-to-claim limitations, external validity, cost, and failure cases.
- Abstract and Conclusion: write after main sections; no new claims. Abstract should not contain formal citations.

## Section Depth

Do not turn section-by-section drafting into several short placeholders. Except for the Abstract, each section should be fully developed according to its evidence:

- Methodology should cover artifact overview, component roles, inputs/outputs, algorithm or notation, design choices, and rejected alternatives.
- Experiments should cover RQs, data/splits, baselines, metrics, seeds/compute, main results, ablations, error analysis, and result provenance.
- Related Work should use 2-4 taxonomy or competing-rationale subsections, not a paper-by-paper laundry list.
- Analysis should explain support for design rationales, alternative explanations, failure cases, sensitivity, and boundaries.
- Introduction and Conclusion should form a complete problem-method-evidence-contribution chain.

Avoid hard word-count floors. If evidence is missing, use natural-language limitations, weaken/remove the unsupported claim, or record the issue in audit/self-check files. Do not leave literal TODO/TBD/LLM_REVIEW_REQUIRED/PLACEHOLDER tokens in final TeX. If evidence is available, develop the section to the density expected by the target venue.

## Evidence Rules

- Numbers must come from `drafts/experiment_evidence_pack.json`, `drafts/result_to_claim.json`, `experiments/results_summary.json`, `experiments/ablations.csv`, or indexed run artifacts.
- Figures must come from existing generated assets or be planned in figure/table registries. If a figure cannot be generated, explain the limitation or remove the reference from final TeX.
- Claims about prior work must cite `literature/related_work.bib` keys and be traceable to `literature/synthesis.md` or paper notes.
- Treat `synthesis.md` `[note:...]` anchors as evidence provenance only. Convert them to real BibTeX keys from `related_work.bib` before writing TeX; do not paste `[note:...]` into `paper.tex`.
- Introduction and Related Work need visible citation-backed positioning, not only a bibliography at the end. Use representative citations in claim-bearing paragraphs.
- If evidence is external, weak, mock-only, or not fully audited, state the boundary in Conclusion's Limitations subsection and avoid claiming unsupported empirical validation.
- Abstract should not contain formal citations: no LaTeX citation commands, no author-year parenthetical citations, and no numeric citation brackets. Refer to method families or problem classes in the Abstract, then put concrete prior-work citations in Introduction or Related Work.

## Tool Boundary

- Use `build_manuscript_resource_index`, `plan_manuscript_sections`, `plan_manuscript_evidence`, `build_alignment_matrix`, `assemble_manuscript`, `audit_manuscript_claims`, `audit_writing_craft`, and `audit_paper_claims` for mechanical workflow.
- Do not let tools invent claims, choose final framing, or write the final argument.
- If a tool audit flags a number/citation/figure, fix the source or mark the limitation; do not hide the issue.
