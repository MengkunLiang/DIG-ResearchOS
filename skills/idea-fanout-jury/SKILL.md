---
name: idea-fanout-jury
description: Guide T4 idea fan-out and jury review without hardcoding domain knowledge.
allowed_tools:
  - read_file
  - analyze_idea_concentration
  - compute_idea_novelty_signal
  - finish_task
max_steps: 12
max_tokens_total: 120000
temperature: 0.4
---

# Idea Fanout Jury

Generate ideas through multiple origins: direct synthesis thinking, seed idea extension, mechanism critique, reverse operation, subgroup failure, and gap exploration.

Use tools only for mechanical hints such as origin concentration and novelty-signal proximity. LLM reasoning must decide whether an idea is promising, risky, or worth human review.

