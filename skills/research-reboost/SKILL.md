---
name: research-reboost
description: Compile ResearchOS Pre-T5 research artifacts into a source-traceable, schema-valid external_executor/handoff_pack.json. Use when T5-HANDOFF must re-boost project, literature, hypothesis, novelty, risk, and experiment-plan artifacts into executable context; when Method Intent Drafting must be performed inside reboost; or when an existing handoff pack must be reconciled, repaired, or validated before project-specific skill compilation or external execution. Do not run experiments, implement the method, compile the executor skill suite, or produce the final realized method.
---

# Research Reboost

Compile research intent into one auditable executor contract. Treat reboost as semantic recompilation, not summarization. Produce `method_intent` during the same workflow as `context_reboost`; never run it as a parallel, independent drafting path.

## Required result

Write `external_executor/handoff_pack.json` conforming to `references/handoff_pack.schema.json`. Keep all substantive statements traceable through stable source references. Mark unresolved conflicts; do not silently choose a convenient interpretation.

The pack is an execution-time source of truth for T5 external work, but `method_intent` is only an implementation constraint. It is never the final Method source for T8.

## Workflow

### 1. Locate inputs and establish the output boundary

Find the project root and the required Pre-T5 files listed in `references/reboost-protocol.md`. Use project-relative paths in the pack. Do not copy whole source documents into the handoff.

Run the deterministic inventory first:

```bash
python3 scripts/inventory_sources.py \
  --project-root <project-root> \
  --output <temporary-source-inventory.json>
```

Treat the inventory as discovery evidence, not semantic interpretation. If a required source is missing, continue only far enough to produce a blocked diagnosis; do not invent its content.

### 2. Read sources by decision relevance

Read all available required sources. Backtrack into optional paper notes, resources, or user seeds only when a required source leaves a concrete decision unsupported or ambiguous. Record every actually consulted file in `source_manifest`; set `used=false` for discovered but unread optional files.

Load `references/reboost-protocol.md` before resolving conflicts or deciding whether optional backtracking is required.

### 3. Build an evidence ledger before synthesis

Assign stable IDs to sources, hypotheses, mechanism invariants, modules, baselines, claims, experiments, and gates. For each decision-bearing statement, record one or more `source_refs` with a locator and a short relevance note.

Distinguish:

- source fact: explicitly supported by a Pre-T5 artifact;
- reconciliation: a choice made under the precedence policy;
- inference: a conservative connection not stated verbatim;
- unresolved item: a missing or conflicting decision that cannot be settled safely.

Never present inference as source fact. Put material unresolved decisions in `unresolved_items` and set `generation_status` accordingly.

### 4. Reconcile the research contract

Compile `context_reboost` from the ledger:

- project goal, research question, central hypothesis, and falsification conditions;
- target setting and explicit exclusions;
- core mechanism, mechanism invariants, and contribution intent;
- novelty-audit resolution and required-baseline consequences;
- execution priorities, risks, and known context mismatches.

Use the conflict rules in `references/reboost-protocol.md`. Novelty constraints control required baselines and must-not-claim boundaries; the experiment plan controls protocol details only where it does not weaken those constraints. Preserve every material mismatch even after applying precedence.

### 5. Draft method intent inside reboost

Derive `method_intent` from the reconciled context, not directly from one source file. Keep both required constants:

```json
{
  "status": "draft_intent_only",
  "not_final_method_source": true
}
```

Specify candidate modules, intended interfaces, algorithm flow, refinement permissions, silent-change prohibitions, mechanism-to-ablation mappings, and an initial framework sketch. Label core, candidate, and supporting modules explicitly. Do not turn an implementation convenience into a contribution or settle an unsupported design choice.

### 6. Compile executable evidence contracts

Build the following as peer fields of the pack, using IDs rather than duplicated prose:

- `baseline_matrix`: requirement, role, availability, fairness, reproduction target, and substitution policy;
- `claim_evidence_matrix`: claim ceiling, required comparisons, acceptance criteria, weakening/falsification criteria, and prohibited interpretations;
- `minimum_experiment_loop`: required experiments plus ordered gates from alignment through packaging;
- `iteration_budget`: bounded rounds, stop triggers, and required action;
- `claim_boundaries`: novelty, method-versus-engineering, conditional claims, and must-not-claim rules;
- `writer_handoff_contract`: audited artifacts the executor must ultimately return;
- `execution_contract`: allowed paths, authority, scope-change policy, resource policy, and startup pointers.

Keep one canonical definition for each entity. Other sections reference its ID.

### 7. Run cross-consistency review

Before writing the final file, check:

- every required novelty baseline appears in `baseline_matrix`;
- every claim maps to concrete experiments and comparisons;
- every claimed mechanism maps to a module and ablation or diagnostic;
- every required experiment appears in the ordered gate flow;
- all IDs and references resolve;
- source coverage and mismatch status agree with `generation_status`;
- `method_intent` stays within the central hypothesis and novelty boundary;
- writer outputs are downstream audited facts, not promises to reuse draft intent.

### 8. Validate before handoff

Write the candidate pack, then run:

```bash
python3 scripts/validate_handoff.py \
  --handoff <project-root>/external_executor/handoff_pack.json \
  --schema references/handoff_pack.schema.json \
  --source-root <project-root>
```

Fix all schema and semantic errors. A completed pack must return exit code `0`. A structurally valid pack may remain `needs_review` or `blocked`, but must not be presented as ready for external execution.

## Status gates

- `completed`: required sources are available, no blocking mismatch remains, cross-references resolve, and validation passes.
- `needs_review`: the pack is usable for human resolution but contains a material non-blocking ambiguity, substitution, or inference requiring approval.
- `blocked`: a required source, central scope decision, novelty constraint, or minimum executable contract is unavailable or contradictory.

Do not invoke project-specific skill compilation or the external executor unless status is `completed`.

## Output discipline

Return the validated handoff artifact first, followed by a concise status summary listing source coverage, mismatches, unresolved items, and validation result. Do not emit hidden reasoning, fabricate research content, run experiments, implement code, or generate `realized_method_package`.

## Resources

- `references/handoff_pack.schema.json`: normative JSON Schema for the output.
- `references/reboost-protocol.md`: source roles, precedence, reconciliation, provenance, and quality gates.
- `scripts/inventory_sources.py`: deterministic required/optional input discovery and hashing.
- `scripts/validate_handoff.py`: dependency-free schema-subset and ResearchOS semantic validation.
