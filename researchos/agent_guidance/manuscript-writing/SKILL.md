---
name: manuscript-writing
description: LLM guidance for staged academic manuscript writing in ResearchOS.
---

# Manuscript Writing Guidance

Use this guidance during T8 writing and revision.

## Core Principle

Write the paper as a staged research argument, not as one long generation. Tools organize resources, inventories, section plans, assembly, and mechanical audits. The Writer LLM is responsible for the scientific story, claims, section prose, and venue-aware positioning.

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
- Conclusion limitations subsection: direct-full evidence boundaries, skipped pilot/novelty-final risks if applicable, external validity, cost, and failure cases.
- Abstract and Conclusion: write after main sections; no new claims.

## Evidence Rules

- Numbers must come from `experiments/results_summary.json`, `experiments/ablations.csv`, or indexed run artifacts.
- Figures must come from existing generated assets or be explicitly marked as TODO with a generation plan.
- Claims about prior work must cite `literature/related_work.bib` keys and be traceable to `literature/synthesis.md` or paper notes.
- If T5/T6 were skipped, state the evidence boundary in Conclusion's Limitations subsection and avoid claiming pilot-validated novelty-final evidence.

## Tool Boundary

- Use `build_manuscript_resource_index`, `plan_manuscript_sections`, `plan_manuscript_evidence`, `build_alignment_matrix`, `assemble_manuscript`, `audit_manuscript_claims`, and `audit_writing_craft` for mechanical workflow.
- Do not let tools invent claims, choose final framing, or write the final argument.
- If a tool audit flags a number/citation/figure, fix the source or mark the limitation; do not hide the issue.
