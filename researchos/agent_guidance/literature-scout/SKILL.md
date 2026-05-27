---
name: literature-scout
description: LLM guidance for designing literature search profiles and queries.
---

# Literature Scout Guidance

Use this guidance before calling search or paper-processing tools.

## LLM Responsibilities

- Infer the research domain from `project.yaml`, seed papers, seed ideas, constraints, and user-provided resources.
- Build a `domain_profile` with inclusion concepts, exclusion concepts, ambiguous terms, target venue/category hints, dataset or benchmark names, and related subfields.
- Design diverse queries from multiple angles: core mechanism, task/application, evaluation setting, baseline family, adjacent field, and recent terminology.
- Run the default broad sources plus `informs_search` for the core queries. INFORMS is a useful low-cost supplement for OR/MS, management science, supply chain, queueing, and optimization coverage; empty results are acceptable and should not block T2.
- Decide whether a candidate is genuinely relevant. Tools may rank, deduplicate, verify metadata, and persist records, but they cannot make final domain judgments.

## Tool Boundary

- `expand_queries` only combines your query ideas with seed-title phrases and date windows.
- `filter_by_domain` only applies the `domain_profile` you provide. If the profile is weak, keep uncertain papers instead of deleting them.
- `enrich_papers` can apply your `llm_annotations`; without them it only performs conservative schema completion.

## Output Discipline

- Do not invent paper metadata.
- Do not let venue names alone decide relevance.
- When uncertain, preserve the paper with a review note rather than filtering it out.
