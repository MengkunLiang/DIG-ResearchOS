# Repository Guidelines

## Project Structure & Module Organization

ResearchOS is a Python 3.11 package centered on `researchos/`. Key modules include `agents/` for task agents, `runtime/` for configuration, logging, tracing, and LLM clients, `orchestration/` for the state machine and gates, `tools/` for built-in and MCP tools, `schemas/` for validation, and `skills/` for runtime skill loading. Tests live in `tests/unit/`; debugging scripts live in `scripts/`. Configuration is under `config/`, with workflow contracts in `config/system_config/`. Documentation is in `docs/`, Docker helpers in `infra/docker/`, LaTeX assets in `latex_templete/`, and generated/local artifacts under `workspace/` and `tmp/`.

## Build, Test, and Development Commands

- `conda create -n researchos python=3.11 -y && conda activate researchos`: create the recommended local environment.
- `pip install -r requirements.txt && pip install -e .`: install runtime, LLM/PDF support, test dependencies, common experiment packages, and editable package entry points.
- `python -m pytest tests/unit -q`: run the unit suite configured by `pyproject.toml`.
- `python -m researchos.cli validate-config`: validate active YAML configuration.
- `python -m researchos.cli selftest`: check provider routing and dependencies.
- `python -m researchos.cli init-workspace --workspace ./workspace/dev-smoke --project-id dev-smoke --topic "runtime smoke test"`: create a smoke workspace.
- `python -m researchos.cli run-task HELLO --workspace ./workspace/dev-smoke`: run the smallest loop.
- `bash infra/docker/build.sh` and `bash infra/docker/run.sh selftest`: build and use the Docker workflow.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, `from __future__ import annotations` in new Python modules, and `pathlib.Path` for filesystem paths. Follow snake_case for functions, modules, tests, and YAML keys. Prefer dataclasses or Pydantic models for structured state. There is no configured formatter; keep changes readable and consistent with nearby code.

## Testing Guidelines

Tests use `pytest` with `pytest-asyncio` in auto mode. Name tests `test_*.py` and keep unit tests in `tests/unit/`. Add focused coverage for changed runtime behavior, state transitions, validators, tools, or workspace artifacts. Use temporary directories instead of checked-in `workspace/` data.

## Commit & Pull Request Guidelines

Recent commits use short imperative summaries such as `Improve ResearchOS writing flow and progress UX`; keep subjects concise and outcome-focused. Pull requests should describe the behavior change, list validation commands, link related issues or design docs, and include screenshots or trace snippets when CLI UX, gates, or generated artifacts change.

## Security & Configuration Tips

Keep secrets in `.env`, not in YAML or committed workspaces. Runtime behavior belongs in `config/user_settings.yaml` and `config/runtime.yaml`; edit `config/system_config/` only when changing workflow contracts. Avoid committing generated traces, logs, PDFs, and experiment outputs unless they are intentional fixtures.
