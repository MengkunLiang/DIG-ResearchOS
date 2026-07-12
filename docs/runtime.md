# Runtime Architecture

This document is for maintainers and advanced users. For commands, start at the
root README; for stage semantics, read [agent_pipeline.md](agent_pipeline.md).

## Execution Model

```text
CLI
  -> RuntimeSettings + Workspace initialization
  -> ToolRegistry + Skills + optional MCP adapters
  -> StateMachine / runner
  -> ExecutionContext
  -> AgentRunner -> Agent or SkillAgent -> policy-bounded tools
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

An `ExecutionContext` carries workspace, project, task, run, policy, and
runtime metadata. Tools can only read/write allowed workspace-relative paths.
The agent's `finish_task` is a request for validation, not a declaration of
success. Validators check declared artifacts, schema, state, fingerprints,
citations, compile results, and task-specific conditions before state advance.

The state machine is the topology authority:

```text
config/system_config/state_machine.yaml
```

Its inputs/outputs define stage contracts; the Python validator defines whether
an artifact is usable. Change both only as a coordinated compatibility change.

## Observability Protocol

`runtime/observability/` receives structured stage/tool events and renders the
same information as colored Rich panels, portable no-color text, and JSONL.
It should communicate researcher-relevant facts, not chain-of-thought:

- Stage start: input artifact meaning/state, planned calculations and branches.
- Progress: bounded counts, rankings, distributions, decisions, failures,
  unsupported evidence, and output writes.
- Summary: conclusions, risks, artifact manifest, and downstream consumer.

Raw tool payloads and provider responses belong in traces, not normal console
output. The CLI startup panel is centralized in `runtime/cli_ui.py` and emitted
once at `main` for every actual command. Runtime commands may later add a
workspace discovery summary without replaying the banner.

## Guided Skill Sessions

Public Skills have a parsed `SKILL.md` contract and a persisted session:

```text
_runtime/skill_sessions/<session-id>.json
user_inputs/<skill>/_intake.md
user_inputs/<skill>/_followup_request.md   # only when semantic input is missing
```

TTY sessions deterministically inspect readiness before provider creation,
collect human material under `user_inputs/<skill>/`, recheck, then require
explicit execution confirmation. Noninteractive missing-input paths stop at
`WAITING_INPUT` without creating an LLM client. Intake may not write final
research artifacts.

Skills do not impose artificial internal token/step limits. Provider constraints
and real runtime conditions still apply.

## T3.6 Survey Runtime

`BuildSurveyStateTool` creates section contracts and outline files. It is
idempotent for an unchanged survey plan: completed `written`/`revised` sections
with existing section files and matching outline fingerprints survive a rebuild.
Plan or contract changes intentionally invalidate the affected section state.

Each `T3.6-SEC-*` task is also a task-scoped write sandbox. It may write only
its own `drafts/survey/sections/<section>.tex` file and update the shared
`drafts/survey/survey_state.json` entry for that same section. It cannot rebuild
section outlines, write another section, assemble the survey, generate figures,
or compile PDF. On `resume`, a section whose file and state pass its validator
is advanced without a second LLM rewrite.

The survey visual tool generates at most one vector PDF:

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

It reads explicit taxonomy structure and resolved local note-card links only.
The renderer prefers Times New Roman and records its installed serif fallback in
`survey_visual_manifest.json`. Performance, baseline, cross-study gain, ranking,
or inferred-risk plots are rejected by policy and assembly validation.

## Experimental Detail Integrity

The runtime is provenance-bound, not metric-name-bound. A concrete dataset,
metric, baseline, seed, resource value, or threshold can be used when the
current project explicitly supplies it through an allowed input or audited
artifact. The relevant source path and section/field must accompany its use.
Otherwise the value is `unknown`, `proposed_not_verified`, or a blocker. This
applies to AUUC/Qini just as it applies to accuracy/F1.

## Skill Capability Contracts

Every guided public Skill is loaded only after deterministic validation of its
`SKILL.md` contract. Every advertised input location must fall under its
`allowed_read_prefixes`; every advertised output must fall under its
`allowed_write_prefixes`. The runtime repeats those boundaries in the Skill
system context. This prevents a readiness panel from advertising a path that
would later fail with `access_denied`.

This check covers workspace-relative paths. A special-purpose tool that works
with an explicitly approved external local source keeps its external-path
validation inside that tool; a Skill must not use `read_file` to probe an
absolute path outside the workspace.

## Extension Points

1. Add a bounded tool and register it in `tools/builtin.py`.
2. Define access paths and structured parameters.
3. Add or extend the artifact schema/validator before an agent depends on it.
4. Update the state-machine contract if the stage topology changes.
5. Emit structured observability facts through the existing reporter.
6. Add focused tests, a CLI/runtime integration test as needed, and update docs.

Avoid side-channel filesystem writes, raw shell execution for research artifacts,
and prompt-only state transitions. They bypass provenance, recovery, and audit.
