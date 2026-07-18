---
name: research-reboost
execution_scope: state_machine
execution_owner: T5-REBOOST-GATE
description: Compile ResearchOS Pre-T5 research artifacts into a source-traceable, schema-valid external_executor/handoff_pack.json. Use when T5-HANDOFF must re-boost project, literature, hypothesis, novelty, risk, and experiment-plan artifacts into executable context; when Method Intent Drafting must be performed inside reboost; or when an existing handoff pack must be reconciled, repaired, or validated before project-specific skill compilation or external execution. Do not run experiments, implement the method, compile the executor skill suite, or produce the final realized method.
tools:
  - read_file
  - list_files
  - compile_research_reboost_handoff
allowed_read_prefixes:
  - ""
  - project.yaml
  - literature/
  - ideation/
  - novelty/
  - resources/
  - user_seeds/
  - external_executor/
allowed_write_prefixes:
  - external_executor/
---

# Research Reboost

Compile research intent into one auditable executor contract. Treat reboost as semantic recompilation, not summarization. Produce `method_intent` during the same workflow as `context_reboost`; never run it as a parallel, independent drafting path.

## Required result

Write `external_executor/handoff_pack.json` conforming to `references/handoff_pack.schema.json`. Keep all substantive statements traceable through stable source references. Mark unresolved conflicts; do not silently choose a convenient interpretation.

The pack is an execution-time source of truth for T5 external work, but `method_intent` is only an implementation constraint. It is never the final Method source for T8.

## Workflow

### 1. Locate inputs and establish the output boundary

Find the project root and the required Pre-T5 files listed in `references/reboost-protocol.md`. Use project-relative paths in the pack. Do not copy whole source documents into the handoff.

In standalone Codex-style environments, run the deterministic inventory first:

```bash
python3 scripts/inventory_sources.py \
  --project-root <project-root> \
  --output <temporary-source-inventory.json>
```

Treat the inventory as discovery evidence, not semantic interpretation. The current T4.5 flow supplies the selected Candidate dossier and `kill_criteria.yaml`; `idea_scorecard.yaml` and `risks.md` are legacy fallbacks, not extra files a current workspace must recreate. When present, `research_dossier.json`, `validation_map.yaml`, `contribution_hypothesis_map.yaml`, and `ideation/proposal/research_proposal.md` preserve the post-novelty research meaning that a short hypothesis page cannot safely carry. The Proposal must be read together with `proposal_manifest.json`, recorded in `source_manifest`, and carried in `context_reboost.research_context.proposal_context`. It is a planning source, not empirical evidence, not a substitute for `exp_plan.yaml`, and not a final writer fact source. If neither member of a required source role is available, continue only far enough to produce a blocked diagnosis; do not invent its content.

When this Skill is executed inside the ResearchOS T5 state machine, use the registered `compile_research_reboost_handoff` tool instead of shelling out. First read the required sources and compile the full `handoff_pack` object yourself under this Skill contract; then pass that object as the tool's `handoff_pack` argument. The tool is only the publication and validation boundary: it writes the pretty-printed JSON, runs the bundled validator, publishes the T5 executor control files, and stores reboost diagnostics under `external_executor/report/`. It does not create or populate `external_executor/expr/`; workspace initialization owns that directory. If the argument is omitted, the tool may fall back to its deterministic repair compiler for legacy or offline recovery, but the normal T5 path should provide the LLM-compiled pack.

### 2. Read sources by decision relevance

Read all available required sources. Backtrack into optional paper notes, Cross-domain catalogs, the literature resource catalog, resources, or user seeds only when a required source leaves a concrete decision unsupported or ambiguous. When `literature/resource_catalog.jsonl` exists, read it together with `literature/resource_catalog_summary.json` before declaring a baseline, dataset, benchmark, model, code, or supplement unavailable. The catalog is a paper-associated discovery ledger, not an approved asset list: it may identify an official-source lead and a Phase B requirement, but it cannot certify access, licensing, revision, security, protocol equivalence, or empirical performance. Record every actually consulted file in `source_manifest`; set `used=false` for discovered but unread optional files. A Cross-domain catalog is retrieval context, not direct experiment evidence: it can guide a baseline or follow-up reading decision but cannot certify a mechanism, implementation detail, baseline equivalence, or result.

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
- `research_context`: research problem, scholarly stakes, conditional practical and commercial implications, affected stakeholders or processes, contribution intent, evidence status, and source references. Preserve `unknown` or `proposed_not_verified`; never translate this context into observed business or scientific results;
- target setting and explicit exclusions;
- core mechanism, mechanism invariants, and contribution intent;
- novelty-audit resolution and required-baseline consequences;
- execution priorities, risks, and known context mismatches.
- when present, `resource_discovery_context`: catalog path, counts, types, and the Phase B verification boundary. Do not promote a discovered link to an acquired resource or an experiment-ready baseline.

Use the conflict rules in `references/reboost-protocol.md`. Novelty constraints control required baselines and must-not-claim boundaries; the experiment plan controls protocol details only where it does not weaken those constraints. Preserve every material mismatch even after applying precedence.

### 5. Draft method intent

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
