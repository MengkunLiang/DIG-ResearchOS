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

## Final Writing Rules

- Every important claim should cite paper-note IDs.
- Avoid generic template sentences. Explain the actual technical pattern observed in this project.
- Preserve uncertainty: use "candidate", "suggests", or "needs verification" when evidence is weak.
