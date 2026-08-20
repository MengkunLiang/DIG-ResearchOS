---
name: literature-synthesis
description: LLM guidance for staged literature synthesis.
---

# Literature Synthesis Guidance

Use this guidance when writing `literature/synthesis.md`.

## LLM Responsibilities

- Read enough paper notes to understand mechanisms, evidence, limitations, and disagreement points.
- Classify method families by actual method behavior, not title keywords or venue labels.
- Identify shared assumptions only when they are supported by specific notes.
- Separate full-text evidence from abstract-only evidence.
- Turn paper-note gaps and reviewed coverage hints into actionable research questions with related paper IDs and plausible experimental angles. A `missing_areas.md` item is only a retrieval coverage hint until you verify it against notes and synthesis reasoning.

## Tool Boundary

- `build_synthesis_workbench` is an evidence organizer. It can extract snippets, store your `llm_insights`, build an outline, and prepare a guidance draft.
- The tool must not be treated as the author of final claims.
- If a workbench candidate conflicts with your reading, rewrite or discard it.
- Compare the citation-coverage target with the actual claim-usable notes before drafting. If a distinct development stage, research stream, or contradiction is genuinely missing, run one bounded `targeted_literature_supplement` with explicit queries and a retrieval reason. Treat every generated note as abstract-level until a Reader records full or partial reading coverage.

## Final Writing Rules

- Every important claim should cite real paper-note anchors. Prefer `[note:<paper_note_id>]`; legacy `[<paper_note_id>]` is accepted only for old drafts.
- `synthesis.md` is Markdown evidence provenance, not final LaTeX. Do not rely on author-year prose alone; it is not machine-checkable.
- If you also use `\cite{bibkey}` to align with later TeX writing, the key must exist in `literature/related_work.bib` and correspond to a real note.
- Avoid generic template sentences. Explain the actual technical pattern observed in this project.
- Express uncertainty where it changes the interpretation of a material claim. Do not add defensive qualifiers or provenance labels to ordinary explanation merely because reasoning was involved.
- Synthesize note evidence, project context, and scholarly model knowledge in one argument. Model reasoning may connect findings, explain a concept, derive an implication, suggest a mechanism, or formulate a hypothesis. It must not fabricate a citation or silently turn memory into a paper attribution, empirical result, numeric fact, established causal finding, or external novelty verdict.
