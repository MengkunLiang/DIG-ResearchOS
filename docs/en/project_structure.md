# Repository, Workspace, And Ownership Boundaries

> [English](../en/project_structure.md) | [中文](../cn/project_structure.md)

ResearchOS separates version-controlled system code from user-owned workspaces. Never treat repository fixtures, a different project workspace, or a model example as input evidence for the current project.

## Repository

```text
DIG-ResearchOS/
├── researchos/                 Python runtime, agents, tools, schemas, CLI
├── config/                     Runtime and system contracts
│   ├── model_settings.yaml     Local provider/model settings, created by configure-llm
│   ├── mcp.yaml                Optional MCP server list
│   └── system_config/          Runtime defaults, Agent contracts, state machine, gates, schemas
├── skills/                     Discoverable atomic and integrated public Skills
│   └── external_executor_skills/  External executor assets; separate ownership
├── prompts/                    Agent prompt templates
├── docs/                       Maintained usage and developer documentation
├── deploy/                     Docker Compose definition
├── infra/docker/               ResearchOS image and TeX environment
├── scripts/                    Maintained repository utilities
├── requirements.txt            Python dependencies
├── pyproject.toml              Package metadata and tool configuration
└── environment.yml             Conda environment definition
```

`AGENTS.md`, `BACKGROUND.md`, local `.env`, `workspace/`, and `tests/` are ignored by the repository policy in this checkout. They are available locally where applicable but are not release artifacts.

## Workspace

```text
workspace/<project>/
├── project.yaml                Research scope and user constraints
├── state.yaml                  Current state-machine position and gates
├── user_inputs/                Human-provided Skill intake and follow-ups
├── user_seeds/                 Optional user seed materials
├── literature/                 Retrieval, paper cards, queues, synthesis
├── ideation/                   T4 candidates, selection, hypotheses, audits
├── drafts/                     Survey/manuscript sections, claims, reviews
├── external_executor/          T5 handoff and executor return contract
├── experiments/                Ingested run evidence and claim mappings
├── submission/                 Final bundle, compile report, fingerprints
└── _runtime/                   Logs, traces, event JSONL, Skill sessions/workflow state
```

### Ownership Rules

| Path | Writer | Purpose |
| --- | --- | --- |
| `project.yaml`, `user_seeds/`, `user_inputs/` | Human, guided intake | Scope, constraints, supplied material |
| `literature/`, `ideation/`, `drafts/` | ResearchOS after validation | Auditable research artifacts |
| `external_executor/` | ResearchOS + selected external executor | Handoff and protocol-bound return files |
| `experiments/` | Result ingestion and audit tools | Observed results, not model guesses |
| `_runtime/` | Runtime only | Operational state; do not edit to change research conclusions |

Use workspace-relative paths in prompts, artifacts, and Skill contracts. Do not copy an artifact between projects without recording its source and validating it under the target project's constraints.
