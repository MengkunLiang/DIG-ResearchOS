# ResearchOS

ResearchOS is an artifact-first research runtime for taking a project from idea to literature review, hypothesis generation, experimentation, paper drafting, review, and submission packaging.

It is not a single “chat agent”. It is a staged system built from:

- a state machine
- task-specific agents
- a tool runtime
- workspace-based artifacts
- validation, resume, gates, and tracing

If you want the shortest mental model:

```text
idea
 -> literature scouting
 -> deep reading
 -> synthesis
 -> optional taxonomy-driven survey paper
 -> hypothesis generation
 -> novelty audit
 -> external experiment re-boost / handoff / automatic skill customization / executor wait
 -> result ingest / integrity audit / result-to-claim
 -> PI evaluation
 -> writing / review / revision
 -> submission bundle
```

## What ResearchOS Can Do

Current implemented workflow:

```text
T1
 -> T2-PARAM-GATE
 -> T2-PARAM-CONFIRM-GATE
 -> T2
 -> T2-COVERAGE-GATE
 -> T3
 -> T3.5
 -> T3.6-GATE-SURVEY
    -> no: T4
    -> yes: T3.6-TEMPLATE-GATE -> T3.6-PLAN -> T3.6-GATE-OUTLINE -> T3.6-GATE-CORPUS
            -> optional T3.6-EXPAND
            -> T3.6-STATE
            -> T3.6-SEC-* section-by-section
            -> T3.6-ASSEMBLE -> T3.6-REVIEW -> T3.6-COMPILE -> T3.6-FEED
            -> T3.6-POST-SURVEY-GATE
 -> T4
    -> candidate pool ready: T4-GATE1 -> user selects/merges/reanalyzes -> T4
 -> T4.5
    -> pass*: T5-REBOOST-GATE
    -> reframe/drop/unknown: T4.5-HUMAN-REVIEW -> user chooses T5-REBOOST-GATE/T4/done
 -> T5-REBOOST-GATE
 -> T5-HANDOFF
 -> T5-SKILL-CUSTOMIZATION-GATE
 -> T5-EXPR-MATERIAL-GATE
 -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> codex_cli / claude_code_window / manual: T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
 -> T7.5
 -> human gate
 -> T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-*
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
 -> T9
 -> done
```

Key runtime features already wired:

- full pipeline execution via `run` / `resume`
- single-task debugging via `run-task`
- task resume / recovery for interrupted stages
- T2 parameter confirmation and post-search coverage gates
- optional T3.6 taxonomy-driven survey branch with template, outline, corpus, compile, and post-survey gates
- T4 Gate1 candidate selection, merge, new-idea, and reanalysis flow
- LLM routing with profile, tier, fallback, retries, and provider selftest
- artifact validation after each task
- human gates in the state machine
- skill discovery and `run-skill`
- MCP server loading and tool registration
- external executor context re-boost, handoff, automatic skill customization, material placement, dry-run/wait, ingest, integrity audit, and result-to-claim
- local `latexmk` based LaTeX compilation and optional legacy Docker tooling
- trace and log recording under each workspace

## Core Concepts

### Workspace-first

Every project runs inside a workspace. The workspace is the source of truth.

Typical directories:

- `user_seeds/`
- `literature/`
- `resources/`
- `ideation/`
- `novelty/`
- `external_executor/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`

`init-workspace`, `run`, `resume`, and `run-task` idempotently refresh the standard directory tree and write `_DIR_GUIDE.md` files for workspace subdirectories. Generated guides are table-based: one table explains purpose, producing stage, consumers, editability, and validation rules; a second table lists key files/subdirectories and their use. Custom guide files are preserved.

New workspaces only create directories used by the current main pipeline. Legacy/optional directories such as `pilot/`, top-level `reviews/`, and workspace-local `skills/` are not created by default; if they already exist in an old workspace, ResearchOS writes legacy guides but does not delete artifacts. External code/assets under `external_executor/workdir`, `resources/repos`, PDFs, and figure folders are not recursively modified.

### Full pipeline vs single task

There are two important ways to run the system:

- `run` / `resume`
  This advances the full state machine, handles gates, and moves across tasks.
- `run-task`
  This runs exactly one task for debugging and does not advance the workflow.

### Agents do not “remember” progress

Progress is recovered from artifacts on disk, not from hidden chat memory.

That is why resume and interruption recovery work best when the relevant outputs have already been written to the workspace.

## Repository Map

| Path | Purpose |
| --- | --- |
| `researchos/agents/` | task-specific agents |
| `researchos/runtime/` | runner, config, LLM client, trace, logging |
| `researchos/orchestration/` | state machine, gates, task I/O contract |
| `researchos/tools/` | builtin tools, MCP adapter, filesystem, paper tools |
| `researchos/skills/` | skill loader, aliases, runner |
| `config/` | user settings, model routing, agent params, runtime config, and `system_config/` workflow contracts |
| `docs/` | documentation index, quickstart, pipeline/runtime references, and design archive |
| `deploy/` | user-facing Docker Compose deployment, Docker env example, and wrappers; project data stays in top-level `workspaces/` |
| `infra/docker/` | low-level Docker image build assets and compatibility helpers |
| `scripts/` | maintained utility scripts such as artifact validation and recovery helpers |
| `tests/` | automated pytest coverage; `tests/unit/` is deterministic, `tests/real/` may need credentials/local tools, `tests/manual/` is local-only |
| `workspaces/` | default generated workspace root for Native and Docker mode; do not commit |
| `workspace/` | legacy/local generated workspace root still accepted when passed explicitly; do not commit |

For the full directory contract, see [docs/project_structure.md](docs/project_structure.md).
For a documentation map, see [docs/README.md](docs/README.md).

## Installation

### Option A: Host installation

Recommended for development and debugging.

```bash
git clone <your-repo-url> ResearchOS
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -e .
```

`requirements.txt` is the single dependency file for local use. It includes the
runtime, LLM routing, PDF/BibTeX processing, and pytest development
dependencies. It does not install a local experiment training stack by default.

For T3.6 survey compilation or T9 submission compilation, install a host TeX
distribution with `latexmk`. On Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  texlive-latex-base \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-xetex \
  texlive-lang-chinese \
  latexmk
```

If `researchos` is not found or behaves differently from the current source tree, use:

```bash
PYTHONPATH=/absolute/path/to/ResearchOS python -m researchos.cli ...
```

### Option B: Docker installation

Optional. Use this when you want to run the ResearchOS CLI in a clean Python
container. Docker is not required for the default pipeline, external executor
flow, or LaTeX compilation.

Use [deploy/](deploy/) for ordinary Docker Compose runs. `infra/docker/` is the
lower-level image build area used by maintainers.

```bash
cd ResearchOS
cp deploy/.env.example deploy/.env
mkdir -p workspaces
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Then run the same CLI in Docker:

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspaces/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"

docker compose -f deploy/compose.yaml run --rm researchos \
  run-task HELLO --workspace /app/workspaces/local-test2
```

The host files live in `workspaces/local-test2`. See
[deploy/README.md](./deploy/README.md) and [docs/docker.md](./docs/docker.md)
for full details.

On Linux, the wrapper scripts set the container UID/GID to the current user so
bind-mounted workspace files stay editable on the host. Direct Compose defaults
to `0:0` for compatibility with root-owned checkouts; set `RESEARCHOS_UID` and
`RESEARCHOS_GID` in `deploy/.env` if you want direct Compose to write as your
user.

## Environment Variables

Copy the template first:

```bash
cp .env.example .env
```

The most commonly used variables are:

| Variable | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek endpoint used by the checked-in default profile |
| `DEEPSEEK_BASE_URL` | DeepSeek-compatible base URL override |
| `SILICONFLOW_API_KEY` | SiliconFlow models |
| `SILICONFLOW_BASE_URL` | SiliconFlow-compatible base URL override |
| `OPENROUTER_API_KEY` | OpenRouter fallback/provider routing |
| `OPENAI_API_KEY` | OpenAI official or compatible endpoint |
| `OPENAI_BASE_URL` | OpenAI-compatible custom base URL |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `S2_API_KEY` | Semantic Scholar API |
| `ELSEVIER_API_KEY` | Elsevier / Scopus search API |
| `ELSEVIER_INSTTOKEN` | Optional Elsevier institutional token |
| `RESEARCHER_EMAIL` | email identity for paper APIs |
| `GITHUB_TOKEN` | optional, for MCP / GitHub integrations |

Important rule:

- secrets belong in `.env`
- day-to-day runtime behavior belongs in `config/user_settings.yaml`, `config/runtime.yaml`, and `.env`
- workflow contracts live under `config/system_config/`; they are active, but ordinary users normally do not edit them
- agent defaults live in `config/agent_params.yaml`, grouped by `llm`, `budget`, `tools`, `prompt`, `behavior`, and `modes`; legacy flat fields are still accepted for compatibility, but the checked-in config uses the sectioned form

See [docs/config.md](./docs/config.md) for the full configuration model.

## Quick Start

### 1. Validate configuration

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

### 2. Run startup selftest

```bash
python -m researchos.cli selftest
```

This command now checks both provider connectivity and critical PDF-processing dependencies.

### 3. Initialize a workspace

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspaces/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 4. Run the minimal smoke task

```bash
python -m researchos.cli run-task HELLO --workspace ./workspaces/local-test2
```

`HELLO` only checks the runtime/tool/write/finish path. It does not exercise the
research workflow.

### 5. Run a real pipeline smoke

For faster development debugging, use `run_smoke`. It runs the real state
machine, but pre-writes small workspace-local literature parameters and lowers
all agent nodes to the `medium` model tier.

```bash
python -m researchos.cli run_smoke \
  --workspace ./workspaces/smoke-t2 \
  --from ./workspaces/local-test2 \
  --active-pool-max 20 \
  --deep-read-target 3 \
  --abstract-sweep 5 \
  --skip-startup-selftest
```

By default `run_smoke` starts from `T2`, writes
`literature/literature_params.json` and
`literature/literature_params_confirmation.json`, and does not overwrite an
existing `literature/literature_params.json` unless `--force-smoke-params` is
provided. It is for integration debugging, not for final literature coverage.

### 6. Run the full pipeline

```bash
python -m researchos.cli run --workspace ./workspaces/local-test2
```

### 7. Resume an interrupted pipeline

```bash
python -m researchos.cli resume --workspace ./workspaces/local-test2
```

`resume` only continues a paused/interrupted state in the same workspace. If you
want to keep upstream artifacts from another workspace but restart the full
state machine from a later task, create a new target workspace and use
`run --from --start-task`:

```bash
python -m researchos.cli run \
  --workspace ./workspaces/new-test5-t2-redo \
  --from ./workspaces/new-test5 \
  --start-task T2

python -m researchos.cli run \
  --workspace ./workspaces/new-test5-t3-redo \
  --from ./workspaces/new-test5 \
  --start-task T3
```

When `--start-task` is omitted, `run --from` defaults to `T2`. The target
workspace must not already contain `state.yaml`; ResearchOS copies only the
declared inputs for the chosen start task, not stale outputs from the old task.

### 8. Survey Seed Outline

For a survey project, place a Markdown seed outline under `user_seeds/`:

```bash
cp /mnt/data/reference/算法风险综述_种子提纲.md \
  ./workspaces/algorithm-risk-survey/user_seeds/算法风险综述_种子提纲.md
```

T1/T2/T3/T3.6 normalize it into
`user_seeds/seed_outline_profile.json` and derived `seed_ideas.md`,
`seed_constraints.md`, and `seed_external_resources.jsonl`.
`representative_literature_directions` are query/taxonomy priors only; they are
not verified citations and are never written to `seed_papers.jsonl`. A survey
profile widens T2/T3 literature coverage via `config/agent_params.yaml`
`behavior_profiles.survey`.

T3.6 survey writing is gated by generic review-paper quality standards rather
than topic-specific templates. `survey_plan.json` must declare
`writing_language`, `central_question`, `scope_boundaries`,
`review_contribution`, and `quality_plan`; core outline entries must include a
reader question and section argument. Section validation rejects mixed-language
drafts, very short sections, paper-by-paper summaries, generic future/gap prose,
unknown BibTeX keys, and internal planning labels.

`build_survey_state` writes a per-section `Section Writing Contract` into each
`drafts/survey/section_outlines/*.md`, so Abstract, Introduction, Background,
Taxonomy, Comparison, Challenges, Future, and Conclusion have different
purpose/content/shape/evidence rules. In the default compact mode, `theme_*`
slots are skipped only as standalone chapters; their content must be absorbed by
Taxonomy and Comparative Analysis, and the audit check
`compact_theme_content_absorbed` fails if any taxonomy class disappears.

To redo only the survey branch after T2/T3/T3.5 are already good, keep
`literature/` and `user_seeds/`, move or delete stale `drafts/survey/` outputs,
set `state.yaml` to `current_task: T3.6-PLAN` and `status: PAUSED`, then run:

```bash
python -m researchos.cli resume --workspace ./workspaces/local-test2
```

## Typical Usage Patterns

### Full project run

Best when you want the complete workflow, including gates and transitions.

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspaces/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory for long-horizon llm agents"

python -m researchos.cli run --workspace ./workspaces/local-test2
```

If the run pauses or stops due to a gate, budget expansion decision, or intentional interruption:

```bash
python -m researchos.cli resume --workspace ./workspaces/local-test2
```

### Single-agent debugging

Best when you are fixing or testing one stage.

```bash
python -m researchos.cli run-task T3 --workspace ./workspaces/local-test2
python -m researchos.cli run-task T3.6-GATE-SURVEY --workspace ./workspaces/local-test2
python -m researchos.cli run-task T3.6-TEMPLATE-GATE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T3.6-PLAN --workspace ./workspaces/local-test2
python -m researchos.cli run-task T3.6-ASSEMBLE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-REBOOST-GATE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-HANDOFF --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-SKILL-CUSTOMIZATION-GATE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-EXPR-MATERIAL-GATE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-EXECUTOR-GATE --workspace ./workspaces/local-test2
python -m researchos.cli run-task T5-DRY-RUN --workspace ./workspaces/local-test2
python -m researchos.cli run-task T7-INGEST --workspace ./workspaces/local-test2  # only after dry-run/wait accepted
python -m researchos.cli run-task T7.5 --workspace ./workspaces/local-test2
python -m researchos.cli run-task T9 --workspace ./workspaces/local-test2
```

For real external execution, `T5-REBOOST-GATE` calls the configured LLM provider directly to generate `external_executor/handoff_pack.json#context_reboost` and `external_executor/reboost_report.json`; no separate Codex CLI re-boost step is needed. `T5-HANDOFF` copies the 13 external executor templates into `external_executor/skills/`, and `T5-SKILL-CUSTOMIZATION-GATE` then calls the LLM provider directly to customize those copies and write `external_executor/skills/customization_report.json`. The chain still pauses for `external_executor/expr/` material placement. When `T5-EXECUTOR-GATE` selects `codex_cli`, `claude_code_window`, or `manual`, the external executor must write `external_executor/result_pack.json`, `executor_status.json`, and `run_manifest.json`, then `resume` continues into `T7-INGEST`.

Plain `run-task T5/T6/T7` is retired to avoid accidentally entering the old internal experiment design. Use `T5-REBOOST-GATE`, `T5-HANDOFF`, `T7-POST-NOVELTY`, or the explicit `LEGACY-* --allow-legacy` entries when debugging legacy behavior.

You can also copy upstream artifacts from another workspace:

```bash
python -m researchos.cli run-task T8-WRITE \
  --workspace ./workspaces/scratch \
  --from ./workspaces/local-test2
```

Notes:

- `run` and `resume` advance the full state machine
- `run-task` only executes one stage
- but if you re-run `run-task` on the same workspace, many stages now continue from existing artifacts instead of starting from an empty slate

### Inspect status and trace

```bash
python -m researchos.cli status --workspace ./workspaces/local-test2
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspaces/local-test2
python -m researchos.cli validate --workspace ./workspaces/local-test2 --task T7-AUDIT
```

## Skills

ResearchOS supports standalone skills through `SKILL.md`.

Current commands:

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill deepxiv "summarize recent memory papers for llm agents"
```

Current repository skills include paper-related examples such as:

- `paper-compile`
- `paper-write`
- `deepxiv`

Notes:

- skill discovery now reads `SKILL.md` frontmatter
- tool aliases like `Bash(*)`, `Glob(*)`, and `Grep(*)` are translated into runtime tools
- some advanced Claude-style tools may degrade if they are not registered in the current runtime

Skill details are explained in [docs/runtime.md](./docs/runtime.md) and [docs/dev.md](./docs/dev.md).

## MCP

ResearchOS can load MCP server definitions and expose their tools to agents.

Main files:

- `config/mcp.example.yaml`
- `config/mcp.yaml`

At startup, the CLI summary reports how many MCP servers and MCP tools were loaded.

See [docs/runtime.md](./docs/runtime.md) and [docs/config.md](./docs/config.md) for details.

## Budget, Fallback, Resume, and Human Gates

These are some of the most important runtime behaviors.

### Budget handling

Agents run under per-task budgets:

- max steps
- token budget
- wall-clock budget

If a task hits its budget, the runtime can present a gate asking whether to extend the budget and continue.

### LLM fallback

Profiles in `config/model_routing.yaml` can define fallback candidates.

Typical behavior:

- try primary model
- if it fails, try fallback model
- only then start another retry round

### Resume

Many stages now support task-specific recovery. For example:

- T3 rebuilds a pending deep-read queue from structurally valid notes; stale notes without `Reading Coverage` remain pending
- T5 / T7 rebuild resume state from existing experiment artifacts
- T7.5 / T8 / T9 reuse existing outputs instead of pretending they do not exist

### Human gates

Human confirmation can appear in the state machine. Current important examples include:

- T2 literature-parameter and coverage confirmation
- T3.6 survey template, outline, corpus, and post-survey decisions
- T4 Gate1 idea selection / merge / reanalysis
- T4.5 non-pass novelty review
- T5 material placement and executor selection; skill customization is automatic
- T7.5 evaluation decision
- T8 writing style/template selection
- submission / final decision points

These only fully participate when using `run` / `resume`, not when using isolated `run-task`.

## Documentation Map

Start here depending on your role:

- Workflow overview: [docs/agent_pipeline.md](./docs/agent_pipeline.md)
- Project structure: [docs/project_structure.md](./docs/project_structure.md)
- Docker deployment: [deploy/README.md](./deploy/README.md)
- Manual diagnostics: [scripts/README.md](./scripts/README.md)
- Runtime internals: [docs/runtime.md](./docs/runtime.md)
- Docker: [docs/docker.md](./docs/docker.md)
- Configuration: [docs/config.md](./docs/config.md)
- Developer guide: [docs/dev.md](./docs/dev.md)
- Full documentation index: [docs/README.md](./docs/README.md)

## Current Implementation Status

The current codebase is usable, but it is still an evolving research runtime.

Practical expectations:

- the pipeline is runnable
- resume and artifact recovery are implemented for major stages
- T2 has parameter and coverage gates before deep reading
- T3.6 can produce an optional survey paper before or instead of continuing to T4
- T5 is an external-executor protocol chain, not an in-process experiment runner
- T9 now behaves as a compile-and-repair submission stage
- provider instability can still affect long runs
- some config fields are fully wired, while others are declarative or partially wired
- some skills may degrade if they depend on tools that are not registered in the current runtime

## Common Questions

### Why do I see different behavior between `researchos` and `python -m researchos.cli`?

Usually because the installed console script is not bound to the same interpreter or source tree as your current shell.

Use:

```bash
PYTHONPATH=/absolute/path/to/ResearchOS python -m researchos.cli ...
```

or reinstall:

```bash
pip install -e .
```

### Why does a task start from scratch after interruption?

Most often:

- you changed workspaces
- the relevant artifacts were never written before interruption
- the task has recovery logic, but the expected files are missing or malformed

### Why does `run-task` behave differently from `run`?

Because `run-task` only executes one stage and does not advance the full state machine.

Use `run` or `resume` when you need:

- gate handling
- automatic next-task transitions
- the full T7 -> T7.5 -> human gate -> T8 chain

### Where should I debug first?

Look in this order:

1. CLI error summary
2. `workspaces/<name>/_runtime/logs/researchos.log`
3. `workspaces/<name>/_runtime/traces/*.jsonl`
4. the actual task artifacts in the workspace

## License and Project-Specific Notes

Check repository-local files such as:

- `AGENTS.md`
- `BACKGROUND.md`
- `config/README.md`
- `docs/agent_pipeline.md`

for repository-specific conventions and implementation details.
