# Module Attribution Policy

## Purpose

Phase E2 converts diagnostic and intervention evidence into bounded statements about modules and mechanisms. It does not plan new experiments, alter code, or decide the next iteration.

## Ownership

This skill owns:

- the attribution preflight, snapshot, deterministic facts and report;
- per-iteration module/mechanism attribution records;
- advisory keep/modify/drop/narrow/collect-evidence recommendations.

It does not own:

- experiment execution or repair;
- module implementation mappings written by `implementation`;
- formal review approval;
- result diagnosis;
- iteration decisions, budgets or scope changes;
- claim-boundary edits;
- realized method or framework figure generation.

## Required sequence

```text
resolve current diagnosis
→ pin evidence snapshot
→ inventory modules/mechanisms
→ normalize interventions
→ estimate direct effects
→ analyze interactions/confounds
→ derive facts
→ evidence-graded interpretation
→ attribution gate
→ narrow apply
→ return to root
```

## Separation of statements

Always distinguish:

1. **Implementation fact** — a module exists in code/config.
2. **Association** — module state co-varies with an outcome.
3. **Intervention effect** — an explicit module manipulation changes an outcome under a controlled comparison.
4. **Mechanism attribution** — evidence supports a proposed causal pathway.
5. **Research decision** — keep, modify, narrow, rerun, or stop at workflow level.

This skill may produce 1–4 with evidence labels and local recommendations. The root owns 5.

## Resume and staleness

An attribution is reusable only when its input fingerprint remains valid. Changes to any of the following make affected attributions stale:

- current diagnosis or its evidence snapshot;
- intervention runs or metric artifacts;
- protocol, dataset, split, preprocessing or metric direction;
- implementation/module mapping;
- ablation switch semantics;
- fairness fingerprint;
- claim/mechanism mapping.

Preserve stale attribution records as history.

## Honest partial work

If only implementation facts or correlational hints exist, produce a partial report with unsupported mechanism questions. Do not create synthetic causal certainty to satisfy the schema.
