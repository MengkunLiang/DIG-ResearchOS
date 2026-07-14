# Native, Docker, And LaTeX

> [English](../en/docker.md) | [中文](../cn/docker.md)

ResearchOS has one runtime contract. Native and Docker Compose execute the same CLI, artifacts, validators, human gates, and state machine.

They also execute the same public Skill contracts, integrated workflow sessions, provider-context abstract batching, and Survey evidence gates. Do not use native and containerized commands as concurrent writers for one workspace.

| Mode | Workspace path | TeX location | Use when |
| --- | --- | --- | --- |
| Native | Host path, for example `workspace/project-a` | Host TeX, then allowed Docker fallback | Development and direct local use |
| Docker Compose | `/app/workspace/project-a` | TeX inside ResearchOS image | Reproducible CLI environment |

Do not use both modes as concurrent writers for a workspace.

## Native TeX

`latex.default_backend: auto` selects, in order:

1. Local `latexmk`.
2. Local `tectonic`.
3. The allowlisted `latex.docker_image` when Docker fallback is enabled.

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
```

Install host TeX on Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

macOS requires MacTeX or BasicTeX plus `latexmk`. Windows requires MiKTeX or TeX Live with `latexmk`, `pdflatex`, `xelatex`, and `bibtex` on `PATH`.

## Compose

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a

docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

The Compose service does not mount the Docker socket and does not use Docker-in-Docker. The image must contain TeX itself. On Linux, set `RESEARCHOS_UID=$(id -u)` and `RESEARCHOS_GID=$(id -g)` in `.env` when needed for host-writable outputs.

## Why TeX Is Not In requirements.txt

`requirements.txt` installs Python packages, including matplotlib for the one deterministic Survey taxonomy figure. TeX Live, `latexmk`, and fonts are system dependencies; install them through the host package manager or bake them into `infra/docker/Dockerfile`.

## Repair And Resume

| `doctor` / preflight result | Repair |
| --- | --- |
| `latexmk_found_on_current_path` | Continue. |
| `docker_tex_image_verified` | Continue with configured Docker fallback. |
| Docker daemon/image unavailable | Start Docker and build the configured image, or install host TeX. |
| Image lacks TeX commands | Rebuild `researchos/system:latest` from `infra/docker/Dockerfile`. |
| Compile error in a `.tex` file | Read the compile report/log, repair the named source or asset, then `resume`. |

Never solve a TeX preflight failure by increasing LLM retries. The runtime pauses before writing more prose so the environment can be repaired first.
