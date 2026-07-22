# Runtime Architecture, Capability Boundaries, And Recovery

> [English](../en/runtime.md) | [中文](../cn/runtime.md)

This document is for maintainers and advanced users. For commands, start at the root README; for stage semantics, read [agent_pipeline.md](agent_pipeline.md).

## Execution Model

```text
CLI
  -> RuntimeSettings + Workspace initialization
  -> ToolRegistry + Skills + optional MCP adapters
  -> StateMachine / runner
  -> ExecutionContext
  -> AgentRunner -> Agent, SkillAgent, or integrated SkillAgent -> policy-bounded tools
  -> workspace artifacts + validators + events/logs/traces
```

Key implementation paths:

| Responsibility | Path |
| --- | --- |
| CLI and command dispatch | `researchos/cli.py` |
| Complete / single-task runners | `researchos/cli_runners/` |
| State machine and gates | `researchos/orchestration/state_machine.py` |
| Task I/O contract | `researchos/orchestration/task_io_contract.py` |
| Agent execution and validation retry | `researchos/runtime/orchestrator.py` |
| Console/event reporter | `researchos/runtime/observability/` |
| Built-in tool registration | `researchos/tools/builtin.py` |
| Workspace policy | `researchos/tools/workspace_policy.py` |
| Skills | `researchos/skills/` |

## Workspace And Validation

An `ExecutionContext` carries workspace, project, task, run, policy, and runtime metadata. Tools can only read/write allowed workspace-relative paths. The agent's `finish_task` is a request for validation, not a declaration of success. Validators check declared artifacts, schema, state, fingerprints, citations, compile results, and task-specific conditions before state advance.

The state machine is the topology authority:

```text
config/system_config/state_machine.yaml
```

Its inputs/outputs define stage contracts; the Python validator defines whether an artifact is usable. Change both only as a coordinated compatibility change.

If startup finds a mismatch between a YAML node and the Python I/O contract, the CLI shows a Rich error panel before any Agent starts. It identifies the actual `state_machine.yaml`, `task_io_contract.py`, and the missing, unexpected, or path-changed fields, then gives the `validate-config` command. This usually means an old checkout is running, `RESEARCHOS_SYSTEM_CONFIG_DIR` points at stale configuration, or only one side of a Python/YAML change was deployed. No research artifact is written before this validation stops the run. `validate-config` includes both source paths as well, so deployment environments can be compared directly.

## Observability Protocol

`runtime/observability/` receives structured stage/tool events and renders the same information as colored Rich panels, portable no-color text, and JSONL. It should communicate researcher-relevant facts, not chain-of-thought:

- Stage start: input artifact meaning/state, planned calculations and branches.
- Progress: bounded counts, rankings, distributions, decisions, failures, unsupported evidence, and output writes.
- Summary: conclusions, risks, artifact manifest, and downstream consumer.

Raw tool payloads and provider responses belong in traces, not normal console output. The CLI startup panel is centralized in `runtime/cli_ui.py` and emitted once at `main` for every actual command. Runtime commands may later add a workspace discovery summary without replaying the banner.

The normal terminal renders a researcher-facing summary rather than repeating the result already delivered to the Agent. PDF extraction reports page coverage and continuation status; section extraction reports recognized parts; web, command, Docker, LaTeX, and structured-write tools report only status, counts, artifact paths, and a necessary next action. Full PDF text, HTML, stdout, JSON payloads, provider diagnostics, and stack traces remain available in Agent context, traces, and logs for audit without flooding the terminal.

### Terminal Presentation And Information Levels

Every launch first shows a Rich startup card with the workspace, loaded research workflow, model settings, available Skills, and MCP status. The following System Check card reports only whether the model connection and local dependencies are usable; it does not print YAML, `startup_selftest`, a configuration-path dump, or a raw provider trace. `--no-color` disables color only and retains cards and tables. `--verbose` is the mode for configuration paths, complete Tool names, detailed errors, and process files.

Normal execution shows only what a researcher needs to act on: the current activity, what completed, what is still needed, and the next action. High-volume Tools such as PDF text, web pages, command output, and structured records retain their full result in the run record while the terminal reports a page range, file, or status summary. Long text wraps naturally in Rich cells instead of using character-level ellipses. A model wait uses one in-place heartbeat line that prioritizes the current activity and completed public milestones rather than repeatedly printing the same line. Its elapsed time belongs to the visible logical phase rather than an individual provider request, so a retry in T4.5, T5, T8, or another long task does not restart the displayed clock. T4 retains its more granular controller-owned phase clock. A resumed process starts a new display clock, while valid persisted artifacts remain the only authority for deciding what may be skipped.

The user interface says material preparation, paper reading note, relevant paper content or location, and output file. Scholarly terms such as `taxonomy`, `baseline`, `ablation`, `claim`, and `Related Work` remain unchanged. Internal terms such as `schema`, `artifact`, `intake`, Agent names, Tool names, and raw provider errors remain available through `--verbose`, trace, and logs, but are not the primary explanation in the normal interface.

### Temporary Provider Failures

One request uses the same provider/model configured in `config/model_settings.yaml`. On timeout, connection interruption, 502/503/504, or temporary overload, the runtime first follows the same-model retry policy in that file; if recovery still fails, the terminal offers clear Retry now, Retry after waiting, and Pause project choices. Waiting is excluded from effective Agent work time and does not consume a research step.

A pause preserves the current task and all artifacts; use the original `resume` command when service returns. A file written before a pause is reported as staged and awaiting completion validation, not as a ready downstream result. This distinction matters when a recovery compiler records an intentionally blocked handoff or partial report. Detailed mode and the event trace retain the full staged-file inventory and diagnostics. Normal CLI output does not expose API keys, complete SDK stack traces, or internal retry details. Authentication, URL, and model errors directly name `configure-llm` instead of entering meaningless network retries.

## Guided Skill Sessions

Public Skills have a parsed `SKILL.md` contract and a persisted session:

```text
_runtime/skill_sessions/<session-id>.json
user_inputs/<skill>/_intake.md
user_inputs/<skill>/_followup_request.md   # only when semantic input is missing
```

TTY sessions deterministically inspect readiness before provider creation, collect human material under `user_inputs/<skill>/`, recheck, then require explicit execution confirmation. Noninteractive missing-input paths stop at `WAITING_INPUT` without creating an LLM client. Intake may not write final research artifacts.

Skills do not impose artificial internal token/step limits. Provider constraints and real runtime conditions still apply.

### Integrated Skill Workflows

An integrated public Skill adds a declarative `workflow` section to `SKILL.md`. The loader validates phase ids, labels, objectives, operations, and gate flags at discovery. `record_readiness` copies that contract into the normal session file without resetting completed phase records on resume. The bounded `update_skill_workflow` tool can update only the active standalone Skill session and records:

```text
phase id / visible status / summary / artifact paths / evidence boundary / next action
```

This is not nested `run-skill` execution. The current runtime executes one SkillAgent and one policy-bounded ToolRegistry at a time; composed Skills reuse real tools and artifact contracts inside a named phase sequence. That avoids hidden child sessions, path-policy drift, and unclear recovery ownership.

### Provider-Context Abstract Batching

T3 full-text reading remains paper-specific. The LLM client tries to identify the current model's `context window` from OpenAI-compatible `/models` metadata, supporting both `/v1` and non-`/v1` URLs and common fields such as `context_length`, `context_window`, `max_context`, and `max_input_tokens`. The result is cached for the active client and drives file reading, history trimming, and abstract batching. When the provider exposes no verifiable metadata, the runtime uses the `262144`-token `context_window_fallback` from the same `config/model_settings.yaml` file as the provider connection. It is a total context-capacity fallback, not a raw input limit; provider metadata takes priority and researchers normally do not configure context or batch size.

The orchestrator uses the current model's `count_tokens()` and discovered context window to pack abstract records without a fixed paper count. Every returned JSON note is normalized into `shallow_read_notes/<paper>.md`; it is a shallow-reading lead, not full-text evidence.

### Context-Aware File Reads

`read_file` exposes only `path` and `offset`; page size is computed from the effective context window. The runtime reserves `max(8,000, 15% of context)` tokens, capped at 64,000, for prompt/history/future tools and gives a file result 70% of what remains. It returns a complete file only when it fits the automatic full-read share; otherwise it returns an automatic context-sized page and reports the authoritative next offset. T2 `papers_raw.jsonl` is paged at JSONL record boundaries. Result metadata includes the applied budget, the effective context window, and its source: `provider_metadata`, `configured_fallback`, or `explicit_override`.

Batch output remains `ABSTRACT_ONLY` / `abstract_claim_hint`. A malformed or partial batch falls back only for missing papers, while metadata-only records remain in their existing batch triage path. Batch count, per-paper fallback, and provider context are emitted as bounded progress and access-audit facts.

## T3.6 Survey Runtime

`BuildSurveyStateTool` creates section contracts and outline files. It is idempotent for an unchanged survey plan: completed `written`/`revised` sections with existing section files and matching outline fingerprints survive a rebuild. Plan or contract changes intentionally invalidate the affected section state.

Each `T3.6-SEC-*` task is also a task-scoped write sandbox. It may write only its own `drafts/survey/sections/<section>.tex` file and update the shared `drafts/survey/survey_state.json` entry for that same section. It cannot rebuild section outlines, write another section, assemble the survey, generate figures, or compile PDF. On `resume`, a section whose file and state pass its validator is advanced without a second LLM rewrite.

The survey visual tool generates at most one vector PDF:

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

It reads explicit taxonomy structure and resolved local note-card links only. The renderer prefers Times New Roman and records its installed serif fallback in `survey_visual_manifest.json`. Performance, baseline, cross-study gain, ranking, or inferred-risk plots are rejected by policy and assembly validation.

The Survey audit also checks the physical LaTeX layout. For built-in two-column CCF templates (ICML, NeurIPS, ICLR, and KDD), a full-page-width taxonomy image must be enclosed in `figure*`; a normal `figure` must use `\\columnwidth`, `\\linewidth`, or a strictly smaller width. A normal `figure` with `width=\\textwidth` can compile while drawing through the adjacent column, so `survey_graphics_layout` blocks it before review or final compile. This is a layout rule, not a content rule: it does not authorize additional figures or relax the taxonomy-only visual manifest.

`T3.6-REVIEW` has a stricter derived-artifact boundary. It can revise `drafts/survey/sections/<section>.tex`, then call `assemble_survey` and `audit_survey_coverage`; it cannot use ordinary `write_file` to overwrite `drafts/survey/survey.tex`. This prevents a context-limited repair from replacing a complete survey with only the text the model happened to read. A title/template correction is supplied to `assemble_survey(title=...)` and must be recorded in `survey_review_actions.json`; a later normal assembly should use the repaired title source rather than a hidden manual TeX edit.

## Experimental Detail Integrity

The runtime is provenance-bound, not metric-name-bound. A concrete dataset, metric, baseline, seed, resource value, or threshold can be used when the current project explicitly supplies it through an allowed input or audited artifact. The relevant source path and section/field must accompany its use. Otherwise the value is `unknown`, `proposed_not_verified`, or a blocker. This applies to AUUC/Qini just as it applies to accuracy/F1.

## Skill Capability Contracts

Every guided public Skill is loaded only after deterministic validation of its `SKILL.md` contract. Every advertised input location must fall under its `allowed_read_prefixes`; every advertised output must fall under its `allowed_write_prefixes`. The runtime repeats those boundaries in the Skill system context. This prevents a readiness panel from advertising a path that would later fail with `access_denied`.

This check covers workspace-relative paths. A special-purpose tool that works with an explicitly approved external local source keeps its external-path validation inside that tool; a Skill must not use `read_file` to probe an absolute path outside the workspace.

### Profile-Based Tool Surface

The public catalog uses declared capability profiles instead of giving every Skill a minimal, divergent tool list. All public Skills receive `workspace_navigation` (`list_files`, `glob_files`, `grep_search`) under their own read policy. Appropriate workflows additionally receive narrowly grouped literature discovery, paper acquisition/curation, corpus processing, structured artifact, ideation, review, manuscript, Survey, TeX, or executor-handoff tools. Use the catalog to inspect the resolved surface before a demo or run:

```bash
python -m researchos.cli describe-skill paper-comparison \
  --workspace ./workspace/project-a
python -m researchos.cli list-skills --workspace ./workspace/project-a --verbose
```

Profiles are additive convenience, not unrestricted authority. They do not add `bash_run` or `docker_exec`; `WorkspaceAccessPolicy` still controls every workspace path; and acquisition tools require an explicit source request and a writable declared destination. See [skills.md](skills.md) for the profile map.

## Structured Artifact Diagnostics

`write_structured_file` validates a JSON/YAML object before it creates or changes the target file. On a schema failure, the tool now returns a bounded repair list in both the agent context and terminal event. Each item carries an instance path, rule, and message, for example:

```text
$.ideas[2].decision.rejection_reason [type]: requires ['array'], current is str
$.ideas[0].counterfactual_check [required]: missing required field
$.ideas[0].basis.literature_observations[0].strength [enum]: supporting is not allowed
```

The file is not partially written. Correct the listed fields, then retry the same `write_structured_file` call. Do not delete candidate records merely to reduce a schema error: T4 requires the complete Gate1 pool, including deferred, merged, and rejected candidates. The terminal error code remains `schema_validation_failed` for automation; the field-level diagnostics are the actionable cause.

T4 forms an Evidence Index and an asymmetric P0 rather than a fixed number of idea cards. Standard mode completes `P0 -> P1`: it recalls full/partial and abstract-level paper-reading notes with different Evidence Permission, forms an Opportunity Map, generates route-specific Candidates, runs Independent Scoring, creates plan-bounded Mutation Children and conditional Crossover Children, then performs Survival Selection. Union rescoring runs only when an admitted Child changes the Population. If no Child survives validation, the Controller reuses the existing independent Parent reports with an explicit score-reuse receipt; it neither invents a new score nor makes another provider call. Route quotas, P0 targets, family distribution, MMR, and Portfolio count are scheduling or ranking targets, not completion gates; Gate1 can show the available 1–3 best directions while retaining the complete Active Population and Archive.

Native T4 records four validation outcomes: `valid` continues, `repairable` goes through lossless extraction, deterministic normalization, and bounded repair, `degraded` preserves usable content with an explicit risk, and only `blocked` stops. `blocked` is reserved for Hard Invariants: evidence permission/provenance violations, fabricated or untraceable citations, Candidate/Parent/Plan lineage conflicts, ID overwrite, fingerprint/workspace corruption, or Legacy overwrite risk. Markdown fences, YAML, envelope differences, absent enrichable fields, one failed Route, quota shortfall, incompatible Crossover, unavailable scoring, and deferred display translation are repairable or degraded outcomes, recorded as diagnostics rather than reasons to discard a Round.

An initial Route may return a minimal `IdeaSeed`: problem, thesis, candidate mechanism, contribution sketch, one falsifiable prediction, main uncertainty, and Route origin. The Controller owns IDs, lineage, versioning, quotas, retry, artifacts, and resume. Evidence constrains certification, not imagination: every normal Route may use general scholarly knowledge, counterfactual reasoning, and structural cross-domain analogy as well as the workspace context. Such content is recorded in `CreativeContext` with a conceptual leap, competing explanations, surprising prediction, research-program potential, and its verification/reading upgrade; LLM parametric knowledge is marked `knowledge_origin=llm_parametric_knowledge`, `evidence_status=conjectural`, and `verification_required=true`. It may generate hypotheses but cannot certify a mechanism, paper, dataset, metric, or result. Mature Candidates still require complete presentation and multiple hypotheses. A score that exhausts bounded retry becomes `unscored`, never a synthetic score or a deleted Candidate; resume and Profile revision use only independently obtained scores and keep unscored Candidates visible for review or retry. The runtime validates structure, provenance, permissions, and lifecycle rules; it does not invent scientific prose. On `resume`, incomplete native artifacts are repaired from their last valid Phase. `ideation.j2` remains explicit Legacy-only and cannot overwrite native artifacts.

T4 deliberately distinguishes present maturity from research upside. `overall_readiness` describes how complete and evidence-calibrated a Candidate is now; the independent Scorer's `scientific_upside` describes the value of its problem reframing, mechanism, surprising prediction, or research programme if its conjectures survive validation. A `wildcard_recommended` Candidate can remain visible beside mature directions for human comparison, but it never becomes evidence-certified or T4.5-selectable by that label. Likewise, a documented `no_improvement`, `incompatible`, or `deferred` Evolution Plan outcome preserves its Parent and a revisit condition rather than forcing a cosmetic Child. Complexity is a review and evolution target, not an automatic rejection.

The T4 live view separates the current activity, current deliverable, and following phase. For example, Opportunity Mapping appears as `Research Opportunity Mapping (Opportunity Map)`; while it runs, the view says that the current deliverable is a research-opportunity list (not a final Candidate) and that the following phase is multi-perspective Idea divergence. This prevents the same Opportunity Map from being presented once as the current activity and again as a misleading “next step.”

Runtime-generated `_DIR_GUIDE.md` files are operational guidance, not scientific input: they do not invalidate an input fingerprint. For T4 evidence availability, an absent note root and an empty note root are equivalent; actual note bytes remain fingerprint-bound. Native Seeds and unscored Candidates stay visible. A Seed may enter T4.5 provisionally only after it has an independent score, a complete LLM Final Card, a traceable thesis, and at least one LLM-authored draft hypothesis; its maturity and evidence warnings are inputs to the audit rather than a hidden return to T4. An unscored Candidate or one missing those structural inputs remains blocked before confirmation. Historical Candidate artifacts with none of the native lifecycle fields retain their historical resume contract rather than being reclassified as new Seeds.

## Extension Points

1. Add a bounded tool and register it in `tools/builtin.py`.
2. Define access paths and structured parameters.
3. Add or extend the artifact schema/validator before an agent depends on it.
4. Update the state-machine contract if the stage topology changes.
5. Emit structured observability facts through the existing reporter.
6. Add focused tests, a CLI/runtime integration test as needed, and update docs.

Avoid side-channel filesystem writes, raw shell execution for research artifacts, and prompt-only state transitions. They bypass provenance, recovery, and audit.
