# ResearchOS Documentation

This directory contains user, developer, and design documentation for
ResearchOS. Start with the shortest document that matches the task you are
doing, then move to the deeper references only when needed.

## Start Here

| Document | Status | Use When |
| --- | --- | --- |
| [../README.md](../README.md) | Canonical entry | You need the main project overview and first run commands. |
| [QUICKSTART.md](QUICKSTART.md) | Canonical guide | You want to create a workspace and run the core CLI quickly. |
| [project_structure.md](project_structure.md) | Canonical reference | You need to understand repository directories such as `deploy/`, `infra/docker/`, `scripts/`, and `tests/`. |
| [../deploy/README.md](../deploy/README.md) | Docker deployment guide | You want to run ResearchOS through Docker Compose and host-visible workspaces. |
| [../scripts/README.md](../scripts/README.md) | Utility scripts guide | You need to understand which helper scripts are maintained in git. |
| [../config/README.md](../config/README.md) | Configuration map | You are editing checked-in defaults or local user settings. |
| [docker.md](docker.md) | Operations guide | You want the Native Mode vs Docker Mode model and Docker troubleshooting details. |
| [dev.md](dev.md) | Developer guide | You are changing ResearchOS code and need development workflows. |

## Runtime And Pipeline

| Document | Status | Scope |
| --- | --- | --- |
| [agent_pipeline.md](agent_pipeline.md) | Reference | Full state-machine and agent-stage reference. |
| [runtime.md](runtime.md) | Reference | Runtime, tracing, resume, task execution, and operational internals. |
| [config.md](config.md) | Canonical reference | Configuration precedence and runtime settings. |
| [logging.md](logging.md) | Operations guide | CLI output, logs, traces, and debugging commands. |
| [artifact_flow_map.md](artifact_flow_map.md) | Reference | High-level artifact movement between stages. |
| [tool_llm_boundaries.md](tool_llm_boundaries.md) | Reference | Boundary between deterministic tools and LLM reasoning. |

## Writing, Submission, And External Execution

| Document | Status | Scope |
| --- | --- | --- |
| [manuscript.md](manuscript.md) | Canonical guide | T8 writing, review, revision, and T9 submission behavior. |
| [external_executor.md](external_executor.md) | Canonical guide | Current user-facing external executor handoff flow. |
| [external_executor_protocol.md](external_executor_protocol.md) | Protocol reference | Result pack and executor protocol details. |
| [resource_search.md](resource_search.md) | Reference | Resource and literature search behavior. |

## Design Archive

The following documents are useful for historical context and deeper design
work, but they are not the shortest path for ordinary operation:

| Document | Status | Notes |
| --- | --- | --- |
| [full_pipeline_redesign.md](full_pipeline_redesign.md) | Design archive | Pipeline redesign notes; current behavior is governed by `config/system_config/state_machine.yaml`. |
| [experiment_module_redesign.md](experiment_module_redesign.md) | Design archive | External experiment module redesign notes; use `external_executor.md` for operations. |
| [migration_legacy_experiments.md](migration_legacy_experiments.md) | Legacy / migration | Legacy experiment migration notes. |
| [ResearchOS_external_executor_design.md](ResearchOS_external_executor_design.md) | Design archive | Extended external executor design notes. |
| [re-boost.md](re-boost.md) | Design / operations note | Context re-boost design and operational notes. |
| [reference_project_review.md](reference_project_review.md) | Design archive | Reference project review notes. |

## Sources Of Truth

| Question | Source |
| --- | --- |
| Current state-machine topology | `config/system_config/state_machine.yaml` |
| Runtime configuration precedence | [config.md](config.md) |
| Native vs Docker operation | [docker.md](docker.md) |
| External executor handoff and result ingest | [external_executor.md](external_executor.md), [external_executor_protocol.md](external_executor_protocol.md) |
| Writing, LaTeX, and submission bundle | [manuscript.md](manuscript.md) |

## Generated Artifacts

Do not put generated workspaces, traces, logs, PDFs, Docker test workspaces, or
local `.env` files into docs. Runtime outputs belong in `workspaces/`, an
explicit legacy `workspace/` project path, or a temporary directory outside the
repo.
