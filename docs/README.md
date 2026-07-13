# ResearchOS Documentation

The root [README](../README.md) and [Chinese README](../README.zh-CN.md) are
the operational entry points. This directory contains one source of truth per
topic; generated workspaces, provider traces, PDFs, and secrets do not belong
here.

| Task | Read |
| --- | --- |
| Install, create a workspace, run, resume, or use a Skill | [../README.md](../README.md) / [../README.zh-CN.md](../README.zh-CN.md) |
| Copyable local/Docker first run and recovery recipes | [QUICKSTART.md](QUICKSTART.md) |
| Understand T1-T9, gates, branches, and stage artifacts | [agent_pipeline.md](agent_pipeline.md) |
| Change models, budgets, UI, language, retrieval, or TeX policy | [config.md](config.md) |
| Inspect the configuration directory's maintained file-level notes | [../config/README.md](../config/README.md) |
| Use native TeX, Compose, or Docker fallback | [docker.md](docker.md) |
| Maintain Compose/deployment assets | [../deploy/README.md](../deploy/README.md) |
| Inspect console events, logs, traces, and validation failures | [logging.md](logging.md) |
| Use maintained repository scripts | [../scripts/README.md](../scripts/README.md) |
| Find repository and workspace ownership boundaries | [project_structure.md](project_structure.md) |
| Understand runtime, tool, state, and extension contracts | [runtime.md](runtime.md) |
| Browse and run atomic Skills | [skills.md](skills.md) |
| Develop, test, and release changes | [dev.md](dev.md) |

## Current Operating Contract

1. A workspace is the project source of truth. One writer owns it at a time.
2. `run`, `resume`, `run-task`, tools, Skills, and human gates use the same
   artifact validation and observable event model.
3. Concrete experimental details are allowed only when their current-project
   provenance is explicit. A metric such as AUUC or Qini is valid when sourced;
   it is invalid when guessed from the topic.
4. Every CLI command shows the DIG · BUAA / ResearchOS entry panel once
   unless `--no-banner` or `--quiet` suppresses it.
5. `T3.6` and `T9` validate real TeX compilation. Use `doctor` before a long
   run and repair the named backend before `resume`.
