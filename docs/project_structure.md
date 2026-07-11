# Project Structure

ResearchOS keeps one core implementation and supports two launch modes:
Native Mode and Docker Mode. Docker is a packaging layer around the same CLI,
state machine, validators, gates, workspace layout, and artifact contracts.

## Top-Level Directories

| Path | Role | Commit Policy |
| --- | --- | --- |
| `researchos/` | Main Python package. Agents, runtime, orchestration, tools, schemas, and skill loading live here. | Commit source and package data. Do not commit caches. |
| `config/` | Checked-in defaults, runtime settings, model routing, agent parameters, and system contracts. | Commit safe defaults and schemas. Keep secrets out. |
| `docs/` | User, developer, runtime, and design documentation. | Commit curated docs. Do not store generated artifacts. |
| `deploy/` | User-facing Docker Compose deployment folder. Contains the single Compose file and wrapper scripts. | Commit wrappers and Compose. Env/config stay at the repository root; do not commit `.env` or generated workspace. |
| `infra/docker/` | Low-level Docker image build assets and compatibility run helpers. | Commit Dockerfile and helper scripts. User-facing Docker docs should point to `deploy/` first. |
| `scripts/` | Maintained utility scripts, currently artifact validation, model probing, and recovery helpers. | Commit small reusable utilities only. Do not use this for ad hoc debugging. |
| `tests/` | Automated pytest coverage. `tests/unit/` is deterministic; `tests/real/` may need credentials or local tools; `tests/manual/` is local-only and ignored. | Commit `tests/unit/` and intentional `tests/real/` tests. Do not commit `tests/manual/`. |
| `skills/` | Runtime and external executor skills copied or referenced by handoff stages. | Commit skill source and shared references. |
| `latex_templete/` | Local LaTeX venue templates used by T3.6 and T8/T9 assembly. | Commit template source/assets. Ignore LaTeX build outputs. |
| `workspace/` | Default local workspace root for Native and Docker mode. Existing user projects and smoke runs live here. | Generated. Do not commit. |
| `tmp/` | Local temporary experiments and debug output. | Generated. Do not commit. |

## `deploy/` vs `infra/docker/`

Use `deploy/` when you are a ResearchOS user running the system through Docker
Compose:

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

`deploy/compose.yaml` is the only Docker Compose entry point. Use
`docker compose -f deploy/compose.yaml ...` from the repository root, or call
the wrapper scripts from `deploy/`.

The wrapper scripts set `${RESEARCHOS_UID}:${RESEARCHOS_GID}` automatically on
systems that expose `id`, so bind-mounted workspace files remain host-editable.
Direct Compose defaults to `0:0` for compatibility with root-owned checkouts;
direct Compose users can set UID/GID in `.env` when needed.

The top-level `workspace/` directory owns the host-visible workspace bind
mount:

```text
workspace/<project>  <->  /app/workspace/<project>
```

Use `infra/docker/` when you are maintaining the image itself:

```bash
docker build -t researchos:test -f infra/docker/Dockerfile .
bash infra/docker/run.sh doctor --workspace /app/workspace/dev
```

`infra/docker/` is intentionally lower-level. It should not become a second
ResearchOS implementation and it should not own user workspace.

Docker Compose also bind-mounts the root `config/` directory read-only into
`/app/config`, so Docker Mode and Native Mode share one non-secret
configuration tree. There is intentionally no `deploy/config/` copy.

There is intentionally no root `docker-compose.yml` or root `compose.yaml`.
`deploy/compose.yaml` is the single Compose entry point. `pyproject.toml` is not
a deployment duplicate; it is the Python package, console script, package data,
and pytest configuration file.

## `scripts/` vs `tests/`

`tests/` is the automated test suite:

```bash
python -m pytest tests/unit -q
```

The repository default pytest discovery is intentionally scoped to
`tests/unit/`, so a plain `python -m pytest -q` does not accidentally run real
API, Docker, or local-tool integration checks. Run those explicitly:

```bash
python -m pytest tests/real -q
```

`scripts/` is for maintained utilities that are useful across projects. Ad hoc
manual diagnostics belong in local ignored `tests/manual/`. Repeatable tests
belong under `tests/unit/` or `tests/real/`.

## Native Mode Workspace

Native Mode works directly on a host path:

```bash
researchos init-workspace --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "memory systems for llm agents"
researchos run --workspace ./workspace/project-a
```

The workspace contains the research artifacts and `_runtime/` provenance. It is
the source of truth for resume.

## Docker Mode Workspace

Docker Mode uses the same workspace structure through a bind mount:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a
```

The real files are on the host:

```text
workspace/project-a
```

Container paths such as `/app/workspace/project-a` may appear in runtime
provenance logs, but artifact contracts should prefer workspace-relative paths.

## External Executor

The default external executor flow is host-side:

```bash
cd workspace/project-a/external_executor/workdir
codex
```

The executor writes results back into the same workspace. Then ResearchOS
resumes through the same CLI:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

There is no upload/download step, Docker-in-Docker, Docker socket mount, or
privileged container requirement in the default flow.

## Files That Should Stay Local

Keep these out of git and Docker images:

- `.env`, `.env.*`
- `workspace/`
- `_runtime/` logs and traces
- generated PDFs, LaTeX auxiliary files, and submission build outputs
- external datasets, model weights, credentials, and executor scratch results
