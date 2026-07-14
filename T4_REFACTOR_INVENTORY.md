# T4 Evolution Refactor Inventory

## Scope and Design Freeze

This inventory records the pre-refactor behavior of T4 in the current repository. It is the design freeze for the evolutionary refactor specified by `ResearchOS_T4_Evolutionary_Ideation_Development_Guide.md` v3.1-human-composition-addendum. It is intentionally based on the repository implementation, not on assumed architecture. No production behavior is changed by this document.

The refactor must preserve the external state-machine sequence exactly:

```text
T4 -> T4-GATE1 -> T4 -> T4.5
```

The new evolutionary phases are internal controller phases. They must not be introduced as new external state-machine nodes. `IdeationAgent` remains the T4 facade and the legacy-compatible selected-idea compilation entry point.

## Current Execution Sequence

1. The state machine declares T4 in `config/system_config/state_machine.yaml`. Its external agent is `ideation`; required literature and optional seed inputs are bound in both the state machine and `researchos/orchestration/task_io_contract.py`.
2. `AgentRunner` prepares a compact T4 context pack before the prompt through `_maybe_prepare_t4_context_pack_before_prompt()`. The pack is written below `ideation/` and includes a revisitable evidence-pool index for cards that did not fit in the first prompt.
3. `IdeationAgent.initial_user_message()` checks `validate_t4_gate1_selection_file()`. When no valid selection exists, it instructs the agent to create the Gate1 candidate pool. When a valid selection exists, it instructs the agent to compile hypotheses, experiment planning, risks, scorecard, rationales, and gate decisions.
4. During the pre-selection run, the orchestrator enforces ordered writes for six Gate1 artifacts. `validate_t4_gate1_ready()` validates this narrower pre-selection artifact set and the runtime routes the completed candidate pool to `T4-GATE1` rather than running the selected-compilation path.
5. `T4-GATE1` is an immediate human gate. The Rich-capable `CLIHumanInterface` reads the candidate overview and records `_gate1_user_selection.json`, including candidate-pool fingerprints. A valid selection returns to T4.
6. The second T4 invocation validates the selection fingerprint against the persisted candidate pool, then emits the legacy-compatible selected outputs. The regular T4 output validator performs schema, hypothesis, experiment, risk, scorecard, candidate-provenance, and Gate1 checks.
7. A successful selected-compilation flow continues to T4.5. T4.5 consumes `hypotheses.md`, `idea_scorecard.yaml`, deep/bridge/shallow notes, and other literature outputs. T5, T7, T8, external executor Skills, and observability components consume the legacy final artifacts.

## Current T4 Components

| Area | Current implementation | Refactor role | Compatibility rule |
|---|---|---|---|
| External FSM | `config/system_config/state_machine.yaml` | Keep unchanged externally | No new public transition between T4 and T4.5 |
| T4 I/O contract | `researchos/orchestration/task_io_contract.py` | Extend with optional internal artifacts where needed | Existing input/output keys and paths remain valid |
| T4 facade | `researchos/agents/ideation.py` | Delegate pre-run, evolutionary run, and selected compilation to the controller | Do not delete `IdeationAgent` or its output validation entry point |
| Current prompt | `researchos/prompts/ideation.j2` | Split role-specific work into new prompts and retain a facade/compatibility prompt | Legacy prompt paths must not become the source of scientific text for the new card renderer |
| Gate1 logic | `researchos/orchestration/state_machine.py`, `researchos/tools/human_gate.py` | Replace candidate source with a Gate ViewModel while preserving gate ID and selection artifact | `t4_gate1_selection_gate` and `_gate1_user_selection.json` stay valid |
| Runtime lifecycle | `researchos/runtime/orchestrator.py` | Add controller checkpoints, resume routing, and user-centered events | Existing provider recovery, pending gate, and task completion semantics remain intact |
| Existing fingerprint helper | `researchos/runtime/artifact_fingerprints.py` | Reuse generic file/directory fingerprints for T4 input/run fingerprints | T4.5 fingerprint contract remains unchanged |
| Progress and observability | `researchos/runtime/progress.py`, `researchos/runtime/observability/*` | Consume controller events and ViewModels through Rich renderers | Normal mode must not leak raw JSON or private reasoning |
| T4 progress tool | `researchos/tools/ideation_progress.py` | Migrate from Pass1/Pass2 event names to bounded phase events with backward-compatible parsing | Durable public telemetry only; no unbounded reasoning field |
| Existing deterministic analysis | `researchos/tools/ideation_analysis.py`, `researchos/tools/ideation_tools.py` | Reuse only for structured summaries and validators where semantically safe | It must not replace independent semantic scoring |
| Schema validation | `researchos/schemas/validator.py`, JSON schemas | Add new schemas and project legacy projections | Existing scorecard, rationale, gate decision, and experiment-plan schemas continue to validate |

## Existing Artifact Lifecycle

### Pre-selection candidate pool

The present pre-selection flow persists the following artifacts in a required write order. They are durable and must remain discoverable after the refactor.

| Artifact | Current meaning | New semantic role | Refactor treatment |
|---|---|---|---|
| `ideation/t4_context_pack.json` | Compact prompt context | Compatibility cache only | Replace its scientific source role with the Evidence Index and retain it for old workspaces |
| `ideation/t4_context_pack.md` | Human-readable compact context | Compatibility summary | Continue writing a concise navigation summary when useful |
| `ideation/t4_evidence_pool.json` | Deferred note-card lookup index | Legacy evidence-pool index | Retain and map entries into the Evidence Index where possible |
| `ideation/_pass1_forward_candidates.json` | Raw Pass1 candidate set | Round-0 route seed projection | Preserve every route result, including unsupported or rejected records |
| `ideation/_pass2_grounding_review.json` | Grounding/recommendation review | Initial independent scoring and grounding projection | Cover all P0 candidates; never silently delete a candidate |
| `ideation/_candidate_directions.json` | Gate-ready candidate structure | Active-population Gate projection | Project the current generation from Candidate Dossiers |
| `ideation/_family_distribution.md` | Candidate family narrative | Legacy family summary | Generate from Idea Family artifacts |
| `ideation/_gate1_candidate_cards.md` | Markdown candidate deck | Markdown fallback for the Gate ViewModel | Keep model-authored/project-derived scientific prose; no deterministic invention |
| `ideation/_gate1_selection_brief.md` | Short selection brief | Post-run Gate summary fallback | Generate from the Gate ViewModel with natural, user-centered copy |
| `ideation/bridge_coverage_review.json` | Bridge audit and escape-hatch record | Bridge Route audit | Preserve explicit `unsupported` / escape-hatch outcomes; never fabricate a bridge candidate |
| `ideation/_gate1_user_selection.json` | Gate directive with candidate-pool fingerprint | Human directive compatibility record | Bind it to the active population fingerprint as well as legacy pool fingerprints |

### Selected compilation and downstream artifacts

| Artifact | Current consumer classes | New source/projection rule |
|---|---|---|
| `ideation/selected_idea_brief.md` | Gate presentation, T4.5 human review, hypothesis compiler Skill | After selection, become the human-readable pre-novelty brief; retain a concise compatibility view |
| `ideation/hypothesis_brief.yaml` | New internal/T4.5 handoff | New pre-novelty artifact; not a replacement for final legacy hypotheses until post-T4.5 compilation |
| `ideation/selected/t45_search_targets.json` | New T4.5 search handoff | New, additive artifact |
| `ideation/selected/hypothesis_lineage.json` | New selected-idea lineage handoff | New, additive artifact |
| `ideation/hypotheses.md` | T4.5, T5, T6, T7, T8, Skills | Legacy final projection, produced by the selected compilation path and kept compatible |
| `ideation/exp_plan.yaml` | T5, T6, T7, T8, external executor Skills | Legacy validation-plan projection with valid `hypothesis_ref` values |
| `ideation/idea_scorecard.yaml` | T4.5, T5, T7, observability, Skills | Compatibility ledger projected from Dossiers and independent Score Reports; not the new source of truth |
| `ideation/idea_rationales.json` | T4 validator and downstream audit | Compatibility provenance projection, retaining all relevant candidate and hypothesis links |
| `ideation/risks.md` | T4/T5/T7/T8 users and consumers | Selected idea risk projection, including evidence and validation risks |
| `ideation/rejected_ideas.md` | T4 audit and users | Archive summary generated from Population/Survival records |
| `ideation/gate_decisions.json` | T4 validation, T4.5 fingerprinting | Compatibility decision ledger, augmented with selected population/version references |

## Required New Internal Artifact Families

The following are additive internal artifacts. They are the source of truth for evolutionary behavior; legacy files above remain projections.

```text
ideation/t4_run_config.json
ideation/evidence/evidence_index.jsonl
ideation/evidence/evidence_index_summary.json
ideation/evidence/opportunities.json
ideation/evidence/bundles/
ideation/genomes/
ideation/families/
ideation/populations/P0.json
ideation/populations/P1.json
ideation/scoring/
ideation/evolution/state.json
ideation/evolution/round_*.json
ideation/candidates/
ideation/archive/
ideation/human_directives/
ideation/human_compositions/
ideation/selected/
```

Every write must be atomic, fingerprint-bound, idempotent, and safe to retain through rollback. A rollback changes the active population pointer; it never deletes a later generation.

## Evidence and Reading-Level Baseline

The current `prepare_t4_context_pack()` code reads the synthesis workbench, selected paper-note cards, and fallback Markdown note cards. It distinguishes a compact initial set from a revisitable deferred pool. This is useful infrastructure but does not provide the required explicit Evidence Permission model.

The refactor must index all available core and bridge notes, including both reading tracks. Full/partial text can provide bounded support. Abstract-only material must remain available for recall, Opportunity discovery, inspiration, Bridge hypotheses, and reading-upgrade triggers; it cannot substantiate an established mechanism, strong boundary, final novelty statement, or strong quantitative claim. Metadata-only entries are resource leads only. Synthesis inference and brainstorm content are never elevated into external evidence.

## Current Validation and Resume Baseline

* `IdeationAgent.validate_outputs()` validates final selected outputs, all Pass artifacts, bridge provenance, hypothesis references, scorecard structure, selected CDR design rationale, risks, and Gate1 selection consistency.
* `validate_t4_gate1_ready()` intentionally validates only the candidate-pool stage, enabling an immediate Gate1 pause before final hypotheses or experiment plans exist.
* The orchestrator detects existing valid pre-selection and selected artifacts to avoid redundant LLM work. It also writes durable T4 progress and checks ordered Gate1 artifact writes.
* `_gate1_user_selection.json` is stale when its stored candidate-pool file fingerprints differ from the current candidate pool.
* Existing generic fingerprints support file and directory hashing, and T4.5 already checks an input fingerprint report. T4 does not yet have a complete population/run-config fingerprint, phase completion markers, or generation rollback model.

## Current User Interface Baseline

The current repository has Rich-capable stage reporting and a custom T4 Gate overview formatter. Candidate cards can be model-authored and the human interface presents candidate IDs, explanations, and selection input. The current implementation still presents Pass1/Pass2 as the user-facing process and emits multiple line-level progress events. The refactor must move normal-mode presentation to eight evolutionary phases with concise, stateful Rich views:

```text
Pre-run readiness -> Evidence Routing -> Opportunity Map -> Multi-route Generation
-> Genome and Family -> Independent Scoring -> Evolution Planning
-> Offspring and Rescoring -> Survival and Portfolio -> Post-run Gate
```

The existing tool/result renderer remains responsible for generic runtime output. New T4 UI code must provide controller-derived ViewModels, action consequence copy, bounded heartbeat activity, and read-only inspection views without exposing raw JSON, schema fields, model traces, provider exceptions, or internal deliberation in normal mode.

## Downstream Dependency Inventory

| Dependency | Why it is affected | Required compatibility check |
|---|---|---|
| `NoveltyAuditorAgent` and T4.5 | Reads final hypotheses, scorecard, deep/bridge/shallow notes | Pre-novelty selected artifacts must not break current T4.5 required inputs |
| `ExperimenterAgent`, T5/T5-HANDOFF, external executor | Reads hypotheses and experiment plan | Final projections retain existing field names, valid hypothesis references, and experiment semantics |
| T6/T7/T8 agents and state-machine contracts | Consume T4 selected artifacts | No path rename or contract removal |
| `skills/hypothesis-compiler` | Reads Gate/selected idea artifacts | Update selection, lineage, and pre-novelty wording while retaining old inputs |
| `skills/paper-outline` | Reads `ideation/hypotheses.md` | Preserve final hypotheses artifact and explain pre-novelty versus formal status |
| `skills/research-reboost` and external executor Skills | Require hypotheses, experiment plan, scorecard | Preserve required files and extend handoff provenance safely |
| Observability extractors/stage catalog/progress | Reads candidate artifacts for CLI status | Map new Dossier/Population data to the existing concise views |
| Validator/schema registry | Validates current YAML/JSON contracts | Add schemas without weakening legacy selected-output validation |
| Agent guidance and project specialization | Describes T4 inputs and outputs | Synchronize role separation, evidence permission, lineage, and user language |

## Known Migration Conditions

1. A workspace containing only legacy Pass1/Pass2/candidate-direction artifacts must migrate to P0 with `migration_quality=legacy_partial`. The migration may construct placeholders but cannot claim a completed Evolution round.
2. A valid legacy Gate1 selection must remain usable. It can continue through selected compilation after a fingerprint check, without rerunning T2 or T3.
3. A changed upstream input invalidates the active population and Gate decision. The old generation is archived rather than overwritten.
4. A UI-only verbosity or rendering change does not invalidate a population.
5. Existing context-pack and deferred-pool artifacts remain readable. They are not allowed to become a mechanism-evidence shortcut.

## Phase-A Test Baseline

The pre-refactor checkpoint was pushed as `f647ee4`. Before this Inventory phase, the repository passed:

```text
python -m compileall -q researchos
python -m researchos.cli validate-config --no-banner --no-color --quiet
pytest -q
1342 passed
```

Phase A changes only this inventory and the companion compatibility matrix. Its exit test will run focused T4 state-machine, human-gate, validator, observability, and ideation tests. Production code changes begin only in the typed model/schema phase.
