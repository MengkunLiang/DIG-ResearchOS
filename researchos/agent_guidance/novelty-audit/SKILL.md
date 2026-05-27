---
name: novelty-audit
description: LLM guidance for mechanism-aware novelty auditing.
---

# Novelty Audit Guidance

Use this guidance before judging a hypothesis as novel or collided.

## LLM Responsibilities

- Extract the hypothesis mechanism as a causal claim, not just a method description.
- Search by method terms, task terms, and mechanism terms.
- Compare candidates along mechanism, task, data regime, experimental setup, claimed contribution, and evidence strength.
- Decide the final novelty label yourself after reading the candidate evidence.

## Tool Boundary

- `extract_mechanism_tuple` persists your tuple and may add normalization hints.
- `compare_mechanism_tuples` returns a mechanical similarity hint only.
- A `possible_true_collision` hint is not a final collision. Confirm whether the candidate actually covers the same contribution.

## Reporting Rules

- If overlap is real, name the exact paper and explain the shared mechanism.
- If overlap is partial, turn it into a required baseline or crucial experiment.
- If evidence is abstract-only, lower confidence unless a later metadata/full-text check supports it.
