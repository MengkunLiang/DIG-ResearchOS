# ResearchOS Config Directory

This directory contains the checked-in configuration tree used by both Native
Mode and Docker Mode. It is a directory map, not the full configuration manual.
For precedence rules, examples, and field-level guidance, read
[docs/config.md](../docs/config.md).

## Files

| Path | Role | Edit In Daily Use |
| --- | --- | --- |
| `user_settings.yaml` | User-facing model, budget, timeout, and retry preferences. | Yes |
| `runtime.yaml` | Workspace root, runtime directory, logging, UI, web fetch, Docker image, and environment defaults. | Occasionally |
| `model_routing.yaml` | Endpoint, provider, profile, tier, fallback, and context truncation routing. | Only when changing providers or models |
| `agent_params.yaml` | Agent capability registry, tool permissions, prompts, modes, and mechanical behavior thresholds. | Only when changing agent behavior |
| `mcp.example.yaml` | Example MCP server configuration. | Copy when needed |
| `mcp.yaml` | Optional local MCP server configuration. | Local only when used |
| `system_config/state_machine.yaml` | Workflow topology, task contracts, transitions, and stage metadata. | System contract |
| `system_config/gates.yaml` | Human gate presentation and option metadata. | System contract |
| `system_config/cdr_schema.yaml` | CDR schema used by reading, ideation, and writing prompts. | System contract |
| `system_config/venue_style_map.yaml` | Venue/style defaults for IS, UTD, CCF-A, and related writing paths. | System contract |

## Single Source Rules

- Secrets live in the repository root `.env`, created from `.env.example`.
- Non-secret ResearchOS settings live in this root `config/` directory.
- Docker Mode bind-mounts this same `config/` directory read-only into
  `/app/config`; there is no `deploy/config/` copy.
- Workspace data lives under the top-level `workspace/` directory by default.
- Generated logs, traces, PDFs, and experiment artifacts do not belong here.

## Configuration Precedence

ResearchOS resolves user-facing settings in this order:

```text
CLI arguments
> environment variables
> config/user_settings.yaml
> config/runtime.yaml and other checked-in defaults
```

State-machine contracts and validator expectations remain in
`config/system_config/`. Do not use local `.env` or workspace artifacts to
silently change those contracts.

## Quick Checks

From the repository root:

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor
```

For Docker Mode:

```bash
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Use `docker compose ... config --quiet` for path validation. The non-quiet form
can print resolved environment values, so avoid pasting it when `.env` contains
API keys.
