# T4 Skill Audit Report

## Scope

This audit reviewed every repository Skill, T4 prompt, renderer, validator, state-machine consumer, and external-executor handoff component found by searching for `T4`, `T4-GATE1`, `T4.5`, `ideation`, `hypothesis_brief.yaml`, `t45_search_targets.json`, `hypotheses.md`, `exp_plan.yaml`, `idea_scorecard.yaml`, `selected_idea_brief.md`, `_candidate_directions.json`, and `_gate1_user_selection.json`. The review verifies the public state path remains `T4 -> T4-GATE1 -> T4 -> T4.5`.

## Contract Decisions

- A complete Gate1 selection creates Pre-Novelty lineage and search-scope artifacts, not an executable experiment protocol.
- Only a passing T4.5 formalization authorizes formal `hypotheses.md`, `exp_plan.yaml`, mapping artifacts, and T5 handoff.
- Multiple complete Candidates remain parallel unless the researcher explicitly asks for composition.
- A component-level selection creates a Human-composed Candidate only after Compatibility Check, a Gene Donor Map, independent scoring, and a second confirmation.
- Abstract-only notes participate in Evidence Routing and reading-upgrade decisions but cannot establish a mechanism, design rationale, strong Claim, external novelty, or T5 protocol.

## Reviewed Skills

| Path or group | Role | Changed | Audit result and compatibility notes |
| --- | --- | --- | --- |
| `researchos/agent_guidance/ideation/SKILL.md` | T4 role guidance | Yes, earlier phase | Documents P0/P1, role separation, Evidence Permission, Bridge escape hatch, component composition, Pre-Novelty output, and 2–4 Contributions/Hypotheses. |
| `skills/hypothesis-compiler/SKILL.md` | Hypothesis drafting | Yes, earlier phase | Accepts a Pre-Novelty brief as trace context; prohibits direct string concatenation and requires compatibility/lineage for cross-Candidate material. It does not grant T5 authority. |
| `skills/paper-outline/SKILL.md` | Provisional or formal outline | Yes | Accepts `hypothesis_brief.yaml` only for a conditional outline, marks it `needs_T4.5_review`, and prefers post-T4.5 formal artifacts when present. |
| `skills/context-re-boosting/SKILL.md` | T5 context reboost | Yes, earlier phase | Records Pre-Novelty files as trace/search context only; formal hypotheses, experiment plan, and novelty audit remain execution authority. |
| `skills/external_executor_skills/context-alignment/SKILL.md` | T5 alignment | Yes, earlier phase | States the same authority boundary. |
| `skills/external_executor_skills/context-alignment/references/source-reading-policy.md` and `scripts/inventory_sources.py` | Alignment source inventory | Yes | Inventories Pre-Novelty artifacts with `pre_novelty_context`; they never become a protocol or Claim authority. |
| `skills/research-reboost/references/reboost-protocol.md` and `scripts/inventory_sources.py` | T5 reboost input contract | Yes | Adds optional Pre-Novelty provenance/search inputs while keeping formal post-T4.5 files required for a usable handoff. |
| `skills/external_executor_skills/{baseline-reproduction,code-and-protocol-review,evidence-packaging,experiment-design,experiment-run,implementation,method-refinement,module-attribution,research-execution,resource-and-baseline-preparation,result-diagnosis,writer-handoff}/SKILL.md` | External execution subskills | No | They consume a validated handoff/result-pack boundary rather than raw T4 selection artifacts. The root preflight continues to require formal hypotheses, experiment plan, and novelty audit. |
| `skills/{citation-*,claim-evidence-map,cross-domain-idea-studio,domain-synthesis-studio,draft-evidence-repair,experiment-design-review,idea-fanout-jury,literature-*,method-builder,paper-*,research-*,survey-*,venue-fit-review}/SKILL.md` | Public research and writing Skills | No | No direct execution authority over T4 selection artifacts was found. Their normal workspace policies preserve evidence boundaries; direct consumers are documented above. |

## Prompt, Runtime, And Consumer Review

| Component | Changed | Result |
| --- | --- | --- |
| `idea_generator.j2`, `idea_scorer.j2`, `idea_evolver.j2`, `idea_opportunity_planner.j2`, `idea_crossover_reviewer.j2` | Yes | Explicit role separation, 2–4 mature Contributions/Hypotheses, Evidence Permission, no self-scoring, no module stacking, and no external novelty claim. |
| `idea_composition_reviewer.j2`, `idea_human_composer.j2` | Previously added | Compatibility-gated composition, complete source lineage, no direct formal-hypothesis write, and independent scoring boundary. |
| `researchos/ideation/{models,llm_roles,legacy_projection}.py` | Yes | Enforces 2–4 mature Contributions/Hypotheses, preserves all four in Gate1 compatibility projection, and leaves legacy partial migration readable. |
| `researchos/runtime/orchestrator.py`, `researchos/orchestration/state_machine.py`, `researchos/orchestration/task_io_contract.py`, `config/system_config/state_machine.yaml`, `researchos/tools/human_gate.py` | Yes | Operation execution, confirmation, composition, Route regeneration, Pre-Novelty handoff, T4.5-only formalization ownership, and Rich rendering preserve the external path. No project-specific research content is hardcoded. |
| `researchos/agents/experimenter.py`, `researchos/tools/external_experiment.py`, `researchos/skills/project_specialization/context_builder.py`, `researchos/prompts/experimenter.j2` | Reviewed | Formal post-T4.5 hypotheses/plan plus novelty audit remain mandatory for execution. No change needed to lower this authority boundary. |
| `researchos/agents/writer.py`, `researchos/prompts/writer.j2` | Reviewed | Writing consumes formal hypotheses/plan and audited results. No pre-T4.5 experimental authority was introduced. |

## Validation

- Targeted evolutionary, Gate1, lifecycle, failure-injection, and Rich UI tests cover P0/P1, Pre-Novelty handoff, Human composition, Route regeneration, rollback-safe snapshot numbering, four-hypothesis projection, migrated T4.5 source validation, and no raw JSON in normal T4 views: `157 passed`.
- Full repository regression: `1412 passed in 77.08s`. `compileall` and `validate-config` both completed successfully. The implementation report records the external Skill inventory and real Workspace migration checks.

## Residual Limits

- T4 evaluates internal-corpus readiness and calibrated evidence, not external novelty. T4.5 remains the novelty/collision authority.
- LLM-generated scientific prose can still be rejected by schema, Evidence Permission, Contract, or compatibility validation; the system preserves the failed artifact and pauses/retries rather than inventing a fallback Candidate.
- External executor Skills intentionally require formal post-T4.5 artifacts. A Pre-Novelty brief cannot be used to bypass T5 protocol preflight.
