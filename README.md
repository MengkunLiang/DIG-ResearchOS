# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS is an artifact-first, multi-agent research runtime. It turns a
workspace into an auditable research project: literature discovery and reading,
evidence-grounded ideation, external experiment handoff, paper writing, review,
and submission packaging.

The workspace, not an LLM chat history, is the source of truth. Every stage
writes named artifacts, validates them, and can resume from what is already on
disk.

```text
T1 topic -> T2 literature -> T3 reading -> T3.5 synthesis
  -> optional T3.6 survey -> T4 ideas -> T4.5 novelty
  -> T5 external-executor handoff -> T7 evidence and claims
  -> T8 paper writing/review -> T9 submission bundle
```

## Choose A Runtime

Use exactly one writer for a workspace at a time.

| Mode | Best for | LaTeX behavior |
| --- | --- | --- |
| Native | Development, debugging, direct local use | `auto` prefers local `latexmk`, then local `tectonic`, then the configured Docker TeX image. |
| Docker Compose | Reproducible CLI environment and deployment | The image contains `latexmk`, pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX support. It compiles inside the container and never needs Docker-in-Docker. |

`pyproject.toml` is the package-metadata source; `requirements.txt` is its
Docker-compatible runtime/dev dependency counterpart. Both contain Python
dependencies only. TeX Live, `latexmk`, and fonts are operating-system
dependencies, so they are installed with the OS package manager or baked into
the Docker image.

## Native Installation

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos

cp .env.example .env
```

For a pip-only environment, use `pip install -r requirements.txt && pip install -e .`.

For T3.6 survey PDFs and T9 submission PDFs, install the host TeX toolchain.
This is the recommended Debian/Ubuntu command and includes Chinese support:

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

On macOS install MacTeX (or BasicTeX plus `latexmk`). On Windows install MiKTeX
or TeX Live and ensure `latexmk`, `pdflatex`, `xelatex`, and `bibtex` are on
`PATH`.

Then verify the complete environment, including the actual selected LaTeX
backend:

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/agentic
python -m researchos.cli selftest
```

If the editable installation is unavailable, run the checkout explicitly:

```bash
PYTHONPATH="$PWD" python -m researchos.cli doctor --workspace ./workspace/agentic
```

## Docker Compose Installation

Docker mode runs the same CLI, state machine, validators, and workspace format.
The host `workspace/` directory is mounted at `/app/workspace`; your project
artifacts remain on the host when the container exits.

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

On Linux with a slow Docker bridge network, build through the host network:

```bash
docker build --network=host -t researchos/system:latest -f infra/docker/Dockerfile .
```

This affects only image construction. Runtime compilation is still isolated and
does not receive network access.

## Configure And Start A Project

Put secrets such as provider keys in `.env`. Put non-secret runtime choices in
`config/user_settings.yaml` and `config/runtime.yaml`. Do not put credentials,
generated artifacts, or PDFs in Git.

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "memory systems for LLM agents"

python -m researchos.cli run --workspace ./workspace/project-a
```

The CLI uses human gates for consequential choices. T2 accepts natural-language
coverage input, including language policy, for example:

```text
candidate pool 30, deep read 15, abstract read 15, English manuscript, exclude Chinese literature
```

For an English manuscript with Chinese sources excluded, the saved T2 policy
prevents non-seed Chinese papers from entering the active pool. Chinese, mixed,
or explicitly inclusive projects keep Chinese candidates subject to normal
evidence and citation-quality review.

For a minimal runtime smoke check that does not start research work:

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/project-a
```

## Research-Facing CLI Observability

Every `run`, `resume`, and `run-task` uses the same Stage Start -> Stage
Progress -> Stage Summary protocol. It reports the research activity, not raw
model reasoning or full tool payloads:

- **Stage Start** shows the goal, research question, planned operations, and a
  table of declared inputs/expected outputs with meaning, validation state,
  size/count, and downstream use.
- **Stage Progress** shows bounded research facts such as T2 query/source
  coverage, candidate ranking and reading priority; T3 evidence coverage;
  T3.5 mechanism/tension summaries; T4 origin, supplement, grounding, and
  candidate governance; T7 run/claim audits; and T8/T9 evidence/compile state.
- **Stage Summary** shows conclusions, risks/unsupported evidence, actual
  workspace reads, and an Artifact Manifest with `created`, `updated`,
  `reused`, `missing`, or `invalid` disposition.

Use the presentation controls below. `concise` still includes declared inputs,
outputs, and required human action. `--json-events` mirrors bounded event JSON
to stdout for integration tooling; every run persists the same event stream to
`<workspace>/_runtime/events/<run_id>.jsonl`, whether or not this flag is set.

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity concise --no-color
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --json-events
```

The console is deliberately not a chain-of-thought feed. Tool hints,
retrieval coverage gaps, citation-graph signals, and automatic clusters remain
labelled as hints or evidence boundaries until source artifacts support a
stronger conclusion. See [docs/logging.md](docs/logging.md) for the distinction
between console summaries, logs, traces, and event JSONL.

Interactive terminals use colored Rich panels for stage headers, Agent
Markdown, Tool start/result traces, warnings, and Artifact manifests. Agent
Markdown is normalized before rendering, so malformed keycap numbering such as
`1️⃣` is displayed as ordinary ordered-list syntax rather than terminal-glyph
fragments. `--no-color` preserves the same information as ANSI-free,
copyable text. Standalone Skills use this protocol too: `list-skills` groups
atomic capabilities by research workflow, while `describe-skill`, `run-skill`,
and `skill-status` show explicit input paths, artifact meanings, session state,
and recovery commands.

## Guided Standalone Skills

Skills are not LangChain chains or opaque chat prompts. A discoverable
`skills/<name>/SKILL.md` is wrapped as a `SkillAgent` by the same runtime used
for pipeline agents. Public academic skills first check a declared input
contract, save a resumable session, and only then initialize an LLM.

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill paper-outline --workspace ./workspace/project-a
```

`describe-skill` prints the exact workspace-relative upload paths, acceptable
file types, outputs, and a copyable recovery command. For example, put a short
research brief at `user_inputs/paper-outline/brief.md`, then start a separate
outline session:

```bash
python -m researchos.cli run-skill paper-outline \
  "Create an English empirical-paper outline for a NeurIPS-style submission" \
  --workspace ./workspace/project-a \
  --session-id neuri-2026-outline
```

For a noninteractive command, a missing required file writes
`_runtime/skill_sessions/neuri-2026-outline.json`, prints what to upload and
where, and returns a resumable waiting state. In a real terminal,
`--interactive` starts a restricted multi-turn intake: it asks the human to
upload or paste material, can organize only the supplied material under the
declared `user_inputs/<skill>/` path, and asks again when a required fact is
still absent. It then rechecks before running the actual Skill. Intake cannot
create paper, experiment, citation, or other final outputs. After adding the
file or completing intake, continue with the same session:

```bash
python -m researchos.cli run-skill paper-outline \
  --workspace ./workspace/project-a \
  --session-id neuri-2026-outline \
  --resume

python -m researchos.cli skill-status --workspace ./workspace/project-a
```

Every guided session also writes `user_inputs/<skill>/_intake.md`. In a
standalone workspace it is the editable upload checklist. In a project
workspace it records files discovered from the project, but those files remain
candidate material: the running Skill must inspect whether they semantically
support the requested output. A focused gap becomes
`user_inputs/<skill>/_followup_request.md`, followed by a real human response
and the same `--session-id ... --resume` path. This makes multi-turn work
traceable without treating a model memory or an existing filename as evidence.

Standalone and project-backed Skills have **no token limit and no step limit**.
They stop only through an explicit completion, a human-input pause,
cancellation, provider/runtime failure, or artifact validation outcome. This
does not promise an unlimited provider context window or bypass provider-side
rate, availability, and account limits.

`browse-skills` is a line-based card browser: enter a number/name to inspect a
complete contract, or enter a plain keyword such as `literature`, `文献`,
`Idea`, or `创新点` for deterministic bilingual alias/fuzzy search. Only
`run <number>` begins its guided input session. Colored category panels make
the workflow position, purpose, required/optional input count, outputs, and
next command explicit. During
a running Skill, `skill-status` reports the persisted observable phase, step,
current tool, outputs, and resume command. It does not expose private model
reasoning.

For T2, the console distinguishes a declared optional input that was not
provided (`SKIPPED`), a retrievable-source rate limit/network condition while
fallback sources continue (`DEGRADED`), and a blocking `FAILED` condition. A
T2 source-health summary reports actual source availability and cooldowns.
Paper cards are optional, provenance-bearing inputs for T4.5, T5, the external
executor, T7, and T8; they support rationale, baseline, limitation, and
related-work checks, never empirical performance or experimental claims.

The public workflow now includes atomic literature entry points:
`research-material-ingest` registers user-supplied PDFs, data, code, and use
boundaries; `paper-identifier-resolver` turns DOI/arXiv/title lists into
source-traceable records; and `pdf-note-card` or the narrower
`paper-section-evidence` read one uploaded PDF. `citation-graph-explorer`,
`paper-comparison`, `literature-evidence-matrix`, `literature-gap-map`, and
`citation-library-curator` cover bounded snowballing, comparison, evidence
tables, opportunity mapping, and bibliographic cleanup before synthesis. The
writing lane adds `claim-evidence-map`, `venue-fit-review`, and
non-destructive `paper-peer-review` alongside `paper-outline`, `paper-write`,
`citation-provenance-audit`, `paper-polish`, and `paper-revision`.
`survey-visuals` creates at most one factual taxonomy-overview PDF from the
explicit taxonomy tree and directly linked paper identifiers in `survey_plan.json`.
Every direct identifier must resolve to a local structured note card; otherwise
the tool removes any stale canonical PDF and writes an explicit `skipped`
manifest. This is a source-link audit, not a claim that the linked work has
stronger empirical evidence.
It never renders cross-paper performance, relative gains, T2 screening scores,
or inferred heatmaps; it writes a skipped manifest when the taxonomy itself is
insufficient. Every Skill preserves source files, uses workspace-backed
evidence/citation keys, and writes declared audits beside its outputs. See the
[atomic Skill capability map](./docs/skills.md), [runtime contract](./docs/runtime.md),
and [copyable examples](./docs/QUICKSTART.md).

## Venue-Aware Paper Writing

T8 stores the user-confirmed template/style decision in
`drafts/writing_style.json` and produces `drafts/writing_storyline.md` before
section prose. The storyline links problem, rationale or technical root cause,
core insight, design choice, claim, evidence, alternatives, and limitations.

- UTD/IS/INFORMS profiles emphasize a complete phenomenon/theory/rationale ->
  mechanism -> design principle -> evidence -> bounded theoretical and
  practical implication story.
- NeurIPS, ICML, ICLR, and KDD profiles use concise technical-bottleneck-first
  writing, mapping each contribution to a method component and main result,
  ablation, analysis, or failure evidence.
- `venue_writing_profiles.yaml` holds internal drafting-density targets and
  reviewer questions. It does **not** state official page limits, anonymity
  requirements, or current venue policy. Verify those from the current official
  CFP/template before submission.

`audit_writing_craft` reports the resolved profile, section word counts, and
storyline coverage as diagnostics. A missing or short section is not repaired
by filler prose: the writer must strengthen an evidence-backed rationale or
retain a limitation.

## Resume And Recovery

First inspect status and the last failure:

```bash
python -m researchos.cli status --workspace ./workspace/project-a
tail -n 100 ./workspace/project-a/_runtime/logs/researchos.log
```

### Resume The Same Project

Use this after a human gate, provider timeout, tool/environment repair, or
interrupted process. It preserves valid artifacts and rebuilds context from the
workspace.

```bash
python -m researchos.cli resume --workspace ./workspace/project-a
```

For Compose, use the container path:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

### Rerun One Stage

Use `run-task` for focused debugging. It does not advance the full state
machine.

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/project-a
python -m researchos.cli run-task T3.6-COMPILE --workspace ./workspace/project-a
python -m researchos.cli run-task T9 --workspace ./workspace/project-a
```

### Start A New Workspace From An Earlier Project

`resume` never merges projects. To reuse only the declared upstream inputs from
another workspace, create a new target workspace and choose the start task:

```bash
python -m researchos.cli run \
  --workspace ./workspace/project-a-t3-redo \
  --from ./workspace/project-a \
  --start-task T3
```

When `--start-task` is omitted, `run --from` starts at T2. The target must not
already contain a conflicting `state.yaml`; stale outputs from the old task are
not copied as evidence.

### LaTeX Environment Recovery

Run `doctor` before retrying T3.6 or T9. A preflight now runs before the LLM is
called, so missing TeX pauses before writing work is consumed.

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
```

Do not create a PDF or compile report manually. ResearchOS verifies the TeX,
PDF, log, bibliography, and dependency fingerprints before it accepts a
compiled survey or submission bundle.

## Documentation

| Need | Read |
| --- | --- |
| Fast first run | [docs/QUICKSTART.md](./docs/QUICKSTART.md) |
| Native/Docker, TeX, and image troubleshooting | [docs/docker.md](./docs/docker.md) |
| Configuration and language/citation policy | [docs/config.md](./docs/config.md) |
| Logs, traces, status, and recovery diagnostics | [docs/logging.md](./docs/logging.md) |
| Repository and workspace directories | [docs/project_structure.md](./docs/project_structure.md) |
| Compose wrappers and host-side executor operation | [deploy/README.md](./deploy/README.md) |
| Maintained validation and recovery utilities | [scripts/README.md](./scripts/README.md) |
| Full stage and artifact contract | [docs/agent_pipeline.md](./docs/agent_pipeline.md) |
| Runtime internals and extension points | [docs/runtime.md](./docs/runtime.md) |
| Contributor workflow and tests | [docs/dev.md](./docs/dev.md) |
| Documentation index | [docs/README.md](./docs/README.md) |

## Core Guarantees

- Evidence claims and citations are checked against workspace provenance; the
  system does not treat model-generated references as verified sources.
- T3.6 and T9 require a real PDF, log, and compile report, not a claimed result.
- Human gates preserve selections in files and make them recoverable.
- External experiments use an explicit handoff/result-pack contract; model prose
  alone is not accepted as an experimental result.
- Logs and per-run traces are stored under `<workspace>/_runtime/`.

See [AGENTS.md](AGENTS.md) for repository contribution constraints.
