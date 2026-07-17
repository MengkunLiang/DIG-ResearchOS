# ResearchOS Documentation Map

> [English](../en/README.md) | [中文](../cn/README.md)

This is a maintained English documentation set, not a pointer-only reference. The Chinese counterpart is [../cn/README.md](../cn/README.md); both describe the same code, contracts, and workspace artifacts. A project's audited workspace remains its durable source of truth.

| Need | Start here | Then read |
| --- | --- | --- |
| Install, initialize, run, resume | [QUICKSTART.md](QUICKSTART.md) | [../../README.md](../../README.md) |
| Check which workspaces are actually active | [QUICKSTART.md](QUICKSTART.md) | `workspace-status --workspace-root ./workspace` |
| Understand T1-T9, gates, branches, and artifacts | [agent_pipeline.md](agent_pipeline.md) | [agent_pipeline_detail.md](agent_pipeline_detail.md) |
| Study T4 Population Evolution, prompts, validation, and recovery in depth | [t4_idea_evolution.md](t4_idea_evolution.md) | [runtime.md](runtime.md) · [skills.md](skills.md) |
| Diagnose logs, traces, Survey/T4/T5 failures | [logging.md](logging.md) | [runtime.md](runtime.md) |
| Run DOI/arXiv/URL/topic based Skills | [skills.md](skills.md) | [QUICKSTART.md](QUICKSTART.md) |
| Configure a provider/model or inspect system defaults | [config.md](config.md) | `config/README.md` |
| Use native or Compose and repair TeX | [docker.md](docker.md) | [logging.md](logging.md) |
| Understand repository/workspace ownership | [project_structure.md](project_structure.md) | [runtime.md](runtime.md) |
| Extend tools, agents, schemas, state machine, or Skills | [dev.md](dev.md) | [runtime.md](runtime.md) |
| Inspect deployment, configuration examples, or maintenance scripts | [../../deploy/README.md](../../deploy/README.md) | [../../config/README.md](../../config/README.md) · [../../scripts/README.md](../../scripts/README.md) |
| Use consistent terms or audit documentation quality | [../STYLE_AND_TERMINOLOGY_GUIDE.md](../STYLE_AND_TERMINOLOGY_GUIDE.md) | `python scripts/check_docs.py` |

## Operating Principles

1. Exactly one writer owns a workspace at a time. `run`, `resume`, `run-task`, Skills, tools, and gates write the same artifact/event model.
2. A named metric, baseline, dataset, command, or result needs current-project, traceable support. AUUC/Qini are allowed when sourced; topic-based protocol guessing is not.
3. `run-task` isolates one node, `validate` checks stored artifacts, and `audit-survey` re-runs the deterministic Survey audit after a real repair.
4. T4 uses model-authored Candidate framing, mechanisms, 2–4 Draft Hypotheses, contributions, and recommendations inside an Evidence-Routed `P0 -> P1` workflow. Runtime code enforces Evidence Permission, schemas, lineage, scoring separation, and public Rich progress; it does not replace research ideas with templates.
5. T3.6/T9 acceptance requires a real TeX compile. Repair the named environment issue and resume instead of increasing prose retries.
6. `workspace-status` is an operational overview. `state.yaml` and `_runtime/events` are the recovery authority; an existing but stopped process is not active execution.

## Language Links

- [Chinese documentation map](../cn/README.md)
- [Chinese detailed pipeline guide](../cn/agent_pipeline_detail.md)
- [English detailed pipeline companion](agent_pipeline_detail.md)
- [Documentation root](../../README.md)
