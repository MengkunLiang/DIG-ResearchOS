# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS is an artifact-first research runtime for auditable literature work,
evidence-bounded ideation, external-executor handoff, manuscript production,
review, and submission packaging. A project lives in a workspace: durable files,
not chat history, are the source of truth.

```text
T1 scope -> T2 discover -> T3 read -> T3.5 synthesize
  -> optional T3.6 survey -> T4 ideas -> T4.5 novelty
  -> T5 executor handoff -> T7 evidence and claims
  -> T8 manuscript -> T9 submission bundle
```

## Before You Run

- Use one writer per workspace. Do not run native and Docker commands against
  the same project concurrently.
- Put provider secrets only in `.env`. Do not commit `.env`, workspaces, PDFs,
  runtime logs, or generated submissions.
- Python packages belong in `requirements.txt` / `pyproject.toml`. TeX,
  `latexmk`, and fonts are OS or Docker-image dependencies.

## Native Setup

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos
cp .env.example .env
pip install -e .
```

Pip-only setup is also supported:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

T3.6 and T9 need a real TeX backend to produce and validate PDFs. On
Ubuntu/Debian, install the host toolchain or use the Docker fallback described
below:

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

Verify configuration, dependencies, the selected TeX backend, and provider
connectivity before a long run:

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

When running directly from a checkout before editable installation:

```bash
PYTHONPATH="$PWD" python -m researchos.cli doctor --workspace ./workspace/project-a
```

## Docker Compose Setup

Docker uses the same CLI, validators, state machine, and workspace format. The
host `workspace/` directory is mounted at `/app/workspace` in the container.
The supplied image includes Python dependencies, matplotlib, TeX Live,
`latexmk`, pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX support.

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Example project commands in Compose:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspace/project-a \
  --project-id project-a --topic "memory systems for LLM agents"

docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a
```

Native `latex.default_backend: auto` uses local `latexmk`, then local
`tectonic`, then the allowlisted Docker TeX image when enabled. Compose never
uses Docker-in-Docker. See [docs/docker.md](docs/docker.md).

## Create And Run A Project

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "memory systems for LLM agents"

python -m researchos.cli run --workspace ./workspace/project-a
```

`run` stops at consequential human gates. T2 accepts one natural-language
coverage request, including manuscript language and Chinese-literature policy:

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

English wording such as `candidate pool 30, deep read 15, abstract read 15,
English manuscript, exclude Chinese literature` is equivalent. The confirmed
values are saved in `literature/literature_params.json` before retrieval starts.

T3 processes full-text papers individually because page coverage and section
evidence are paper-specific. Its post-read abstract sweep is different: when
the active provider reports a usable context window, ResearchOS packs multiple
independent abstracts into a provider-context-sized call, then writes one
separate `ABSTRACT-ONLY` note per paper. There is no fixed "papers per batch"
limit. The provider binding and its tokenizer determine the packing plan;
malformed batch output falls back only for the affected paper(s).

## Daily Commands

| Goal | Command |
| --- | --- |
| Inspect current stage and pause reason | `python -m researchos.cli status --workspace ./workspace/project-a` |
| Continue a paused project | `python -m researchos.cli resume --workspace ./workspace/project-a` |
| Run one task without advancing the full pipeline | `python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| Validate one task's artifacts | `python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| Inspect a recorded run | `python -m researchos.cli trace <run-id> --workspace ./workspace/project-a` |
| Check environment and TeX selection | `python -m researchos.cli doctor --workspace ./workspace/project-a` |
| Check state-machine and runtime configuration | `python -m researchos.cli validate-config` |

Use `run --from <source-workspace> --start-task <task>` only to initialize a
new target workspace from another project's validated upstream artifacts. It is
not a merge operation. The recovery guide is in
[docs/QUICKSTART.md](docs/QUICKSTART.md).

For an interrupted survey section, validate before resuming. A valid
`T3.6-SEC-*` output advances without rewriting the completed section; the
section worker is restricted to that file and its matching survey state entry.

## Guided Skills

ResearchOS exposes both atomic Skills and composed, resumable research
workflows. Atomic Skills cover paper intake, DOI/title resolution, note cards,
evidence matrices, ideation, writing, review, polishing, compilation, and
submission checks. Integrated Skills add explicit subphases, evidence gates,
artifact manifests, and durable recovery for multi-step work:

| Need | Integrated Skill | What it does |
| --- | --- | --- |
| Understand a field | `domain-synthesis-studio` | Scope -> optional retrieval -> method/mechanism/tension synthesis -> Survey or Idea decision |
| Compare sources from DOI/PDF | `literature-comparison-studio` | Source resolution -> section evidence -> comparison matrix and claim boundary |
| Build a review corpus | `literature-review-studio` | Review scope -> retrieval -> reading coverage -> taxonomy readiness -> Survey handoff |
| Prepare a Survey safely | `survey-evidence-package` | Corpus sufficiency -> taxonomy/storyline -> targeted supplement decision -> T3.6 handoff |
| Generate cross-domain directions | `cross-domain-idea-studio` | Bridge evidence -> transfer-risk audit -> candidate jury -> human selection |
| Read several papers together | `paper-reading-workbench` | DOI/PDF intake -> prioritized cards -> question answers -> cross-paper learning |
| Build or repair writing evidence | `related-work-builder`, `draft-evidence-repair` | Traceable Related Work or manuscript claim/citation repair package |

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a

python -m researchos.cli run-skill domain-synthesis-studio \
  "Synthesize this field and decide whether a Survey is justified" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli run-skill literature-review-studio \
  "Prepare an English review of trustworthy LLM agent memory methods" \
  --workspace ./workspace/project-a --session-id agent-memory-survey
```

In a TTY, `run-skill` collects missing material through a restricted multi-turn
intake, stages only human-supplied material under `user_inputs/<skill>/`,
rechecks readiness, then asks for an explicit `执行` / `暂停` decision. It does
not generate research, manuscript, experiment, or citation deliverables while
required input is missing. The constrained intake Agent may use the provider to
ask for one fact at a time and encode only human-provided material into the
declared input files. Automation should use `--non-interactive`; missing inputs
then create a resumable `WAITING_INPUT` session without constructing a provider
client.

An integrated Skill records `pending`, `running`, `completed`, `waiting_input`,
`waiting_evidence`, or `skipped` for each declared research phase in the same
session file. It may use source-returning search tools to try to supplement
missing literature after the researcher authorizes that scope. Search leads,
metadata, and abstracts remain visibly weaker than section-level or full-text
evidence; a workflow may not advance a strong scholarly claim merely because it
successfully found more records.

The catalog's input/output paths are checked against each Skill's workspace
permissions at discovery time. A listed public Skill therefore cannot advertise
a path which will later be rejected as `access_denied`.

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 \
  --resume
```

See [docs/skills.md](docs/skills.md) for the capability map and input contract.

## Evidence Boundary

ResearchOS does not ban a metric, dataset, baseline, or benchmark by name.
AUUC, Qini, accuracy, F1, or any other concrete protocol detail is valid when a
user-provided file, audited workspace artifact, or verified plan explicitly
identifies it and provides a traceable source. The system must not infer that
detail from a topic, method name, field convention, or an example. Missing
details remain `unknown`, `proposed_not_verified`, or a human/evidence blocker.

## CLI Display And Diagnostics

Every actual CLI command displays the DIG · BUAA / ResearchOS startup panel
once by default. In an interactive terminal it uses the progressive `D -> DI ->
DIG` color animation; non-TTY output receives one portable static panel.

- `--no-banner` suppresses it for scripts.
- `--no-color` removes ANSI color while preserving the same information.
- `--verbosity concise|normal|detailed` changes research-process detail.
- `--quiet` restricts console output to essential state, errors, pauses, and
  final outcomes.
- `--json-events` mirrors bounded structured events to stdout; every run also
  writes `<workspace>/_runtime/events/<run-id>.jsonl`.

Console panels expose stage inputs, calculations, decisions, risks, and artifact
manifests. They never expose private model reasoning or raw prompt payloads.
Use [docs/logging.md](docs/logging.md) for log and trace triage.

## Documentation

| Need | Document |
| --- | --- |
| First run and recovery | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| Pipeline stages and artifacts | [docs/agent_pipeline.md](docs/agent_pipeline.md) |
| Configuration | [docs/config.md](docs/config.md) |
| Native/Docker/TeX | [docs/docker.md](docs/docker.md) |
| Runtime, events, and extension points | [docs/runtime.md](docs/runtime.md) |
| Skills | [docs/skills.md](docs/skills.md) |
| Logs, traces, and debug procedure | [docs/logging.md](docs/logging.md) |
| Repository and workspace layout | [docs/project_structure.md](docs/project_structure.md) |
| Contributor workflow | [docs/dev.md](docs/dev.md) |

Additional operational references: [repository/workspace layout](./docs/project_structure.md),
[Compose deployment](./deploy/README.md), and [maintained scripts](./scripts/README.md).
