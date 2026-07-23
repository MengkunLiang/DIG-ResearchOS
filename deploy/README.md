# ResearchOS Docker Mode Deployment

This directory provides the optional Docker Compose entry point for ResearchOS.
It runs the same ResearchOS CLI, state machine, validators, gates, and workspace
contracts as Native Mode.

Docker is a deployment wrapper, not a second ResearchOS implementation.

## `deploy/` vs `infra/docker/`

Use this directory when you want to run ResearchOS through Docker Compose.
It owns the user-facing files:

- `compose.yaml`
- `researchos.sh`
- `researchos.ps1`
- top-level `workspace/` bind mount

It does not own a separate config tree. Docker Mode reads the same root
`config/` directory as Native Mode, with secrets and local Docker overrides
coming from the root `.env`.

Use `infra/docker/` only when maintaining or debugging the Docker image itself.
`infra/docker/Dockerfile` builds the runtime image used by this Compose file,
but it is not a separate CLI, state machine, workspace format, or execution
mode.

## 1. Prepare

From the repository root:

```bash
cp .env.example .env
mkdir -p workspace
```

Windows PowerShell uses the equivalent commands:

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force workspace
```

Run `python -m researchos.cli configure-llm` on the host before Docker Mode. It can store the API key in local `.env` or ignored `config/model_settings.yaml`; do not commit either file.

On Windows, run `py -m researchos.cli configure-llm` in PowerShell before using `researchos.ps1`. Docker mounts `config/` read-only, so this setup cannot be deferred into the container.

Edit non-secret runtime preferences in the root `config/` directory. Day-to-day
settings normally belong in `config/model_settings.yaml`; all runtime contracts stay
under `config/system_config/`. Docker Mode and Native Mode use the same files.

On Linux, the wrapper scripts set `${RESEARCHOS_UID}:${RESEARCHOS_GID}` to the
current user before calling Compose, so bind-mounted workspace files stay
editable. Direct `docker compose` defaults to `0:0` for compatibility with
root-owned checkouts; if you want direct Compose to write as your user, set
these in `.env`:

```bash
RESEARCHOS_UID=$(id -u)
RESEARCHOS_GID=$(id -g)
```

## 2. Build Or Pull

Build locally:

```bash
docker compose -f deploy/compose.yaml build
```

`researchos/system:latest` is a reproducible CLI and PDF-validation image. It
contains `latexmk`, pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX packages, so the
Compose service can compile T3.6/T9 documents directly without Docker-in-Docker.

On a Linux machine where Docker's bridge network is much slower than the host
network during a first TeX Live build, build the same image with host networking:

```bash
docker build --network=host \
  -t researchos/system:latest \
  -f infra/docker/Dockerfile .
```

This is only a build-time optimization. Do not add a Docker socket or host
network to the running Compose service.

`deploy/compose.yaml` is the single Compose entry point. Use
`docker compose -f deploy/compose.yaml ...` from the repository root, or use the
wrapper scripts in this directory.

Or pull a published image if your team has one:

```bash
docker compose -f deploy/compose.yaml pull
```

Pin a version tag for formal projects instead of relying on `latest`.

`docker compose config` is useful for debugging Compose paths, but it can print
resolved environment values. Do not paste its full output into issues or chats
if `.env` contains API keys.

## 3. Run The Same CLI

Direct Compose:

```bash
docker compose -f deploy/compose.yaml run --rm researchos doctor

docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspace/project-a \
  --project-id project-a \
  --topic "memory systems for llm agents"

docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a

docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

Wrapper script on macOS/Linux:

```bash
cd deploy
./researchos.sh doctor
./researchos.sh init project-a --topic "memory systems for llm agents"
./researchos.sh run project-a
./researchos.sh resume project-a
./researchos.sh run-task project-a T3
```

Windows PowerShell with Docker Desktop running Linux containers:

```powershell
cd deploy
.\researchos.ps1 doctor
.\researchos.ps1 init project-a -Topic "memory systems for llm agents"
.\researchos.ps1 run project-a
.\researchos.ps1 resume project-a
.\researchos.ps1 run-task project-a T3
```

## 4. Bind Mount Workspace

The Compose file uses a host bind mount:

```yaml
volumes:
  - ../workspace:/app/workspace
  - ../config:/app/config:ro
```

Host path:

```text
workspace/project-a
config/
```

Container path:

```text
/app/workspace/project-a
/app/config
```

The workspace mount is writable project data. The config mount is read-only and
keeps Docker and Native Mode on the same non-secret settings. Removing,
rebuilding, or upgrading the container does not delete the host workspace.

Do not run Native Mode and Docker Mode against the same workspace at the same
time. Use one writer at a time.

## 5. External Executor On The Host

The default experiment path is host-side execution:

1. ResearchOS runs in Native Mode or Docker Mode until T5 handoff.
2. The workspace already exists on the host under `workspace/<project>`.
3. On the host, open:

   ```bash
   cd workspace/project-a/external_executor/workdir
   codex
   ```

   Or use Claude Code/manual execution according to the selected executor.

4. The external executor writes `external_executor/result_pack.json` and related
   artifacts back into the same workspace.
5. Resume ResearchOS:

   ```bash
   docker compose -f deploy/compose.yaml run --rm researchos \
     resume --workspace /app/workspace/project-a
   ```

There is no upload/download step, no Docker-in-Docker, no Docker socket mount,
and no requirement to run Codex inside the ResearchOS container.

## 6. Security And Scope

The image must not contain API keys, user workspace, seed papers, experiment
data, model weights, Codex credentials, SSH keys, or long-term results.

The Compose file intentionally does not mount:

- `/var/run/docker.sock`
- the host root filesystem
- Docker named volumes for the primary workspace

The service does not request privileged mode. If a local file permission issue
appears, fix the host directory permissions rather than broadening container
privileges.

## 7. LaTeX

The default image deliberately contains the TeX toolchain required for real
T3.6/T9 PDF verification. In Compose mode, `latex_compile` uses that local
container toolchain. The service does not mount `/var/run/docker.sock` and does
not attempt Docker-in-Docker.

The same runtime configuration also supports native execution: `auto` prefers
host `latexmk`, then host `tectonic`, then the configured Docker TeX image when
host TeX is unavailable. Use the actual preflight rather than guessing:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  doctor --workspace /app/workspace/project-a
```

If a custom image is substituted through `RESEARCHOS_IMAGE`, it must retain
`latexmk`, pdfLaTeX, XeLaTeX, and BibTeX. Otherwise T3.6-COMPILE and T9 pause
before invoking an LLM with an actionable environment reason. See
[Docker and TeX](../docs/en/docker.md) for the backend order and repair table.
