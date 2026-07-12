# ResearchOS: Native, Docker, And LaTeX

ResearchOS has two supported runtime modes. They execute the same Python CLI,
state machine, validators, human gates, and workspace contracts. Docker is not
a second implementation.

| Mode | Start command | Workspace path | TeX compiler location |
| --- | --- | --- | --- |
| Native | `python -m researchos.cli ...` | host path, for example `workspace/project-a` | host TeX, then configured Docker fallback when needed |
| Docker Compose | `docker compose ... researchos ...` | `/app/workspace/project-a` in the container | the ResearchOS container itself |

Do not run both modes against the same workspace simultaneously.

## 1. LaTeX Backend Contract

T3.6 survey compilation and T9 submission compilation require a real PDF and
log. They call `latex_compile`, which writes a compile report with source,
dependency, PDF, and log fingerprints.

### Native Mode

`backend=auto` chooses the first usable option in this order:

1. Host `latexmk`.
2. Host `tectonic`.
3. The configured allowlisted Docker TeX image, when
   `latex.allow_docker_fallback: true`.

The checked-in default is:

```yaml
latex:
  default_backend: auto
  allow_docker_fallback: true
  docker_image: researchos/system:latest
```

The fallback bind-mounts only the active workspace, runs with `--network none`,
does not mount a GPU, and uses the Docker execution policy. It is intended for
a host that runs ResearchOS but does not have TeX installed.

### Compose Mode

Compose intentionally does **not** mount `/var/run/docker.sock` and does not
use Docker-in-Docker. The `researchos/system:latest` image includes:

- `latexmk`
- pdfLaTeX and XeLaTeX
- BibTeX
- `texlive-latex-base`, `texlive-latex-extra`, and recommended fonts
- `texlive-lang-chinese`

When ResearchOS itself runs in Compose, `latex_compile` invokes this local
container toolchain directly. A custom slim Compose image without TeX fails the
preflight with `container_missing_local_tex_toolchain`; rebuild it from the
provided Dockerfile or add an equivalent TeX toolchain.

## 2. Native Setup

Python dependencies remain in `requirements.txt`; system TeX packages do not.
The Python set includes matplotlib for deterministic survey visuals. It is installed in both native Python
environments and the ResearchOS image through `requirements.txt`; it is unrelated to the TeX toolchain.
On Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk \
  texlive-latex-base \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-xetex \
  texlive-lang-chinese
```

Then verify actual selection:

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
```

Expected native result after installation:

```text
[OK   ] LaTeX backend: latexmk_found_on_current_path
```

If no host TeX exists but Docker is healthy and the image contains TeX, expected
output is:

```text
[OK   ] LaTeX backend: docker_tex_image_verified; image=researchos/system:latest
```

## 3. Compose Setup

From the repository root:

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

The only Compose entry point is `deploy/compose.yaml`. Its bind mounts are:

```text
host workspace/  <->  /app/workspace/
host config/     ->   /app/config/ (read-only)
```

Example commands:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspace/project-a \
  --project-id project-a --topic "memory systems for LLM agents"

docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a

docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

On Linux, set `RESEARCHOS_UID=$(id -u)` and `RESEARCHOS_GID=$(id -g)` in `.env`
when direct Compose should write host-editable files as the current user. The
provided shell wrapper does this automatically.

## 4. Build The Image

Standard build:

```bash
docker compose -f deploy/compose.yaml build researchos
```

The TeX image is intentionally larger than a pure Python image because PDF
validation is a correctness requirement, not an optional export feature.

Some Linux hosts have a slow Docker bridge route to package mirrors while the
host network is healthy. In that situation build the same Dockerfile through
the host network:

```bash
docker build --network=host \
  -t researchos/system:latest \
  -f infra/docker/Dockerfile .
```

This is a build-time workaround only. Never add a Docker socket or a host
network to the running Compose service merely to compile TeX.

## 5. Preflight, Failure, And Resume

Before `T3.6-COMPILE` and `T9`, the runtime executes a LaTeX preflight before
consuming LLM steps. It checks the selected local or Docker path and reports
what is missing.

| Doctor/preflight result | Meaning | Repair |
| --- | --- | --- |
| `latexmk_found_on_current_path` | Native TeX is ready | Continue. |
| `docker_tex_image_verified` | Native process will use the Docker TeX image | Start Docker/build the image, then continue. |
| `docker_command_not_found` or daemon unavailable | Docker fallback cannot start | Install/start Docker, or install host TeX. |
| `docker_image_missing` | Configured image is absent | Build or pull the configured allowlisted image. |
| `docker_tex_commands_missing` | Image is not a TeX image | Rebuild from `infra/docker/Dockerfile`. |
| `container_missing_local_tex_toolchain` | A custom application container lacks TeX | Rebuild the container with the required packages. |

After repair, do not restart a project from scratch:

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
```

For a LaTeX source error rather than an environment failure, inspect the log
next to the source, repair the relevant section/template/bibliography, and
resume. Do not manually create `survey.pdf`, `main.pdf`, or a compile report.

### Table and Figure Layout

`latex_compile` safely inspects ordinary wide `tabular` blocks before compiling. When `auto_fit_wide_tables=true`
(the default), a structurally wide table is wrapped as `\resizebox{\textwidth}{!}{% ... }` only when the active
source does not already use a sizing wrapper and the template does not explicitly prohibit it. The compile report
records `table_layout.resizebox_inserted`; this is an auditable source adjustment, not a visual guess. Templates such
as AAAI that explicitly forbid `resizebox` are skipped. Pass `auto_fit_wide_tables=false` when a venue or author
requires manual table layout.

Survey figures are generated before section writing from `comparison_table.csv`. They are normal local PNG files under
`drafts/survey/figures/`; each corpus-landscape or method-taxonomy visual requires at least two categories and eight
valid corresponding rows by default. The manifest records DPI/font/palette, source-row coverage, year/method coverage,
and generated or skipped reasons. A manifest with `status=skipped` is valid and must not be replaced by decorative images.

## 6. Scope And Security

The ResearchOS image is a CLI and PDF-validation runtime. It is not the default
GPU training environment. The external executor owns project-specific datasets,
weights, CUDA/PyTorch stacks, and real experiment execution.

The Compose service intentionally does not mount:

- `/var/run/docker.sock`
- the host root filesystem
- model weights or credentials beyond explicit `.env` variables

Do not bake API keys, workspaces, seed papers, experiment outputs, or Codex/
Claude credentials into the image.

For the Compose wrapper and host-side external executor workflow, see
[../deploy/README.md](../deploy/README.md). For the short operational path, see
[../README.zh-CN.md](../README.zh-CN.md).
