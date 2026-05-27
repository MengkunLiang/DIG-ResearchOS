---
name: ideation
description: LLM guidance for ResearchOS T4 idea generation.
---

# Ideation Guidance

Use this guidance before generating T4 candidates.

## LLM Responsibilities

- Start from understanding: read the synthesis, missing areas, comparison table, and seed ideas before proposing candidates.
- Generate core ideas through free reasoning from the literature and the user's seed ideas first.
- Use the four constraint channels as coverage supplements, not as the whole search space:
  - mechanism challenge
  - reverse operation
  - subgroup failure
  - missing-area exploration from reviewed retrieval coverage hints
- Make each idea falsifiable: mechanism, prediction, counterfactual, minimum experiment, and kill criteria must be specific enough for a cheap pilot.
- Preserve uncertainty and rejected alternatives in artifacts; do not hide weak candidates.

## Tool Boundary

- Tools write structured artifacts, validate schemas, and ask the user at gates.
- Tools should not generate scientific ideas or decide novelty.
- Intermediate thinking should be persisted as `_candidate_directions.json`, `_family_distribution.md`, `idea_scorecard.yaml`, and `idea_rationales.json`, not hardcoded into tool behavior.

## Candidate Mix

- Include at least one LLM-free-reasoned idea from synthesis.
- If seed ideas exist, include at least one seed-derived or seed-refined idea.
- Add four supplement ideas, one from each constraint channel, unless evidence for a channel is genuinely absent; if absent, record why and create an alternate evidence-driven candidate.
- Treat `missing_areas.md` as retrieval coverage telemetry. Convert it into a research idea only after checking synthesis and paper notes.
- Do not force the final selected idea to come from the four supplement channels.
