# ResearchOS Documentation

Use this index to choose the shortest document that answers the current task.
The root README is intentionally an operating guide; it does not duplicate the
stage-by-stage contract.

## Start With Your Job

| You need to... | Read |
| --- | --- |
| Install, configure keys, run a project, or recover it | [../README.md](../README.md) or [../README.zh-CN.md](../README.zh-CN.md) |
| Get a first local or Compose run working | [QUICKSTART.md](QUICKSTART.md) |
| Run a standalone literature, idea, drafting, polishing, revision, audit, or compile Skill | [QUICKSTART.md](QUICKSTART.md) and [runtime.md](runtime.md) |
| Choose a native/Docker TeX backend or fix a build | [docker.md](docker.md) |
| Change models, budgets, language, search, runtime settings, or internal venue-aware writing profiles | [config.md](config.md) and [../config/README.md](../config/README.md) |
| Inspect a pause, provider failure, tool call, or artifact validation error | [logging.md](logging.md) |
| Understand a repository or workspace path | [project_structure.md](project_structure.md) |
| Use maintained validation or recovery utilities | [../scripts/README.md](../scripts/README.md) |
| Understand all research stages and their artifact contracts | [agent_pipeline.md](agent_pipeline.md) |
| Extend the runtime, an agent, a tool, or a validator | [runtime.md](runtime.md) and [dev.md](dev.md) |
| Run or maintain Docker Compose | [../deploy/README.md](../deploy/README.md) |

## Documentation Boundaries

| Document | Purpose | Does not duplicate |
| --- | --- | --- |
| Root README | User installation, configuration, run, resume, guided Skill entry, and first diagnostics | Full stage semantics |
| `QUICKSTART.md` | Copyable first-run checklist | Configuration reference |
| `docker.md` | Native versus Compose operation, TeX backends, build troubleshooting | External experiment protocol |
| `logging.md` | CLI progress, logs, traces, and failure triage | Full runtime implementation |
| `project_structure.md` | Ownership of repository and workspace directories | Artifact schema details |
| `agent_pipeline.md` | Canonical state-machine, stage, T4 observability, and artifact reference | Installation instructions |
| `runtime.md` | Runtime internals, guided Skill contract, session model, and extension points | User walkthrough |
| `dev.md` | Contributor setup, tests, and change checklist | End-user deployment |

## Sources Of Truth

| Question | Source |
| --- | --- |
| State-machine topology and task I/O | `config/system_config/state_machine.yaml` |
| Gate options and presentation | `config/system_config/gates.yaml` |
| Runtime defaults | `config/runtime.yaml` and `config/user_settings.yaml` |
| Current Python behavior | `researchos/` and tests |
| Current project progress | `<workspace>/state.yaml` and `<workspace>/_runtime/` |

Generated workspaces, traces, logs, PDFs, Docker test directories, and `.env`
files do not belong in this documentation tree.
