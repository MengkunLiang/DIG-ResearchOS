# Configuration

ResearchOS separates daily user settings from versioned system contracts. Run
this after a configuration change:

```bash
python -m researchos.cli validate-config
```

## Configuration Layers

| File | Owner | Change for |
| --- | --- | --- |
| `.env` | Local operator | API keys, endpoint secrets, local environment values |
| `config/user_settings.yaml` | User | Model/profile, budgets, timeouts, retries, temporary agent overrides |
| `config/runtime.yaml` | Runtime operator | Workspace root, logging, UI, human backend, TeX/Docker policy |
| `config/agent_params.yaml` | System maintainer | Agent capabilities, tool access, reading/retrieval behavior |
| `config/model_routing.yaml` | System maintainer | Endpoint/profile/fallback definitions |
| `config/system_config/*.yaml` | System maintainer | State topology, gates, schemas, venue profiles |
| `<workspace>/literature/literature_params.json` | Human gate | This project's T2/T3 coverage and language policy |

Do not put secrets in YAML committed to Git. Do not use system configuration to
override a one-off project choice; use the workspace gate artifact instead.

## Daily Settings

`config/user_settings.yaml` overlays defaults without changing the state
machine. Typical controls:

```yaml
llm:
  default_profile: deepseek
  defaults:
    tier: heavy
  agents:
    ideation:
      temperature: 0.75

budget:
  defaults:
    unlimited_budget: true

runtime:
  timeouts:
    llm_call: 90
  retry_policy:
    llm_retries: 10
```

Skills intentionally have no internal token or step limit. Provider context,
rate, availability, credentials, runtime failures, explicit completion, and
human input still bound execution.

## Runtime Settings

`config/runtime.yaml` controls the shared environment:

```yaml
workspace:
  default_root: ./workspace
  runtime_dir: _runtime

ui:
  no_banner: false
  quiet: false
  verbosity: normal
  no_color: false
  json_events: false

latex:
  default_backend: auto
  allow_docker_fallback: true
  docker_image: researchos/system:latest
```

All actual CLI commands show one startup panel unless `ui.no_banner`,
`--no-banner`, or `--quiet` suppresses it. `--no-color` is suitable for CI and
copyable output. `json_events` persists events in all cases and additionally
mirrors them to stdout when enabled.

## T2/T3 Project Parameters

Do not hand-edit global reading defaults to change one project. Confirm the T2
gate and inspect the saved artifact:

```bash
cat ./workspace/project-a/literature/literature_params.json
```

It records active-pool size, deep-read min/target/max, abstract sweep,
require-target behavior, manuscript language, and Chinese-literature policy.
Language and literature inclusion are separate decisions.

## Experimental Protocols

Metrics, datasets, baselines, seeds, compute budgets, and thresholds are not
global configuration defaults. They belong to traceable project artifacts or
human input. A named metric such as AUUC/Qini is permitted when sourced for the
current project, and forbidden only when guessed from topic or convention.

## System Contracts

The files under `config/system_config/` define the state machine and gates.
Changing them changes compatibility and must be accompanied by validator,
documentation, and regression updates:

- `state_machine.yaml`: tasks, inputs, outputs, branches, recovery topology.
- `gates.yaml`: choices and presentation contract.
- `cdr_schema.yaml`: contribution-design-rationale representation.
- `venue_style_map.yaml`, `venue_writing_profiles.yaml`: internal writing
  guidance, not current official venue policy.

Consult the official venue CFP/template before submission.
