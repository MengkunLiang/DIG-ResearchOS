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
 -> hypothesis generation
 -> novelty audit
 -> pilot experiment
 -> full experiment
 -> PI evaluation
 -> writing / review / revision
 -> submission bundle
```

## What ResearchOS Can Do

Current implemented workflow:

```text
T1
 -> T2
 -> T3
 -> T3.5
 -> T4
 -> T4.5
 -> T5
 -> T6
 -> T7
 -> T7.5
 -> human gate
 -> T8-WRITE
 -> T8-DRAFT
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T9
 -> done
```

Key runtime features already wired:

- full pipeline execution via `run` / `resume`
- single-task debugging via `run-task`
- task resume / recovery for interrupted stages
- LLM routing with profile, tier, fallback, retries, and provider selftest
- artifact validation after each task
- human gates in the state machine
- skill discovery and `run-skill`
- MCP server loading and tool registration
- Docker-based experiment / LaTeX execution support
- trace and log recording under each workspace

## Core Concepts

### Workspace-first

Every project runs inside a workspace. The workspace is the source of truth.

Typical directories:

- `user_seeds/`
- `literature/`
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`

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
| `config/` | state machine, model routing, agent params, runtime config |
| `docs/` | detailed system documentation |
| `infra/docker/` | Docker build and run scripts |
| `tests/` | unit and real-environment tests |
| `workspace/` | default local workspaces |

## Installation

### Option A: Host installation

Recommended for development and debugging.

```bash
git clone <your-repo-url> ResearchOS
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
```

Optional PDF extras:

```bash
pip install -r requirements-optional-pdf.txt
```

If `researchos` is not found or behaves differently from the current source tree, use:

```bash
PYTHONPATH=/absolute/path/to/ResearchOS python -m researchos.cli ...
```

### Option B: Docker installation

Recommended for:

- reproducible execution
- T5 / T7 experiments
- T9 LaTeX compilation
- avoiding host dependency drift

```bash
cd ResearchOS
bash infra/docker/build.sh
```

Then run commands through the wrapper:

```bash
bash infra/docker/run.sh selftest
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

See [docs/docker.md](./docs/docker.md) for full details.

## Environment Variables

Copy the template first:

```bash
cp .env.example .env
```

The most commonly used variables are:

| Variable | Purpose |
| --- | --- |
| `SILICONFLOW_API_KEY` | SiliconFlow models |
| `SILICONFLOW_BASE_URL` | SiliconFlow-compatible base URL override |
| `OPENROUTER_API_KEY` | OpenRouter fallback/provider routing |
| `OPENAI_API_KEY` | OpenAI official or compatible endpoint |
| `OPENAI_BASE_URL` | OpenAI-compatible custom base URL |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `S2_API_KEY` | Semantic Scholar API |
| `RESEARCHER_EMAIL` | email identity for paper APIs |
| `GITHUB_TOKEN` | optional, for MCP / GitHub integrations |

Important rule:

- secrets belong in `.env`
- runtime behavior belongs in `config/*.yaml`

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
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 4. Run a smoke task

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/local-test2
```

### 5. Run the full pipeline

```bash
python -m researchos.cli run --workspace ./workspace/local-test2
```

### 6. Resume an interrupted pipeline

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

## Typical Usage Patterns

### Full project run

Best when you want the complete workflow, including gates and transitions.

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory for long-horizon llm agents"

python -m researchos.cli run --workspace ./workspace/local-test2
```

If the run pauses or stops due to a gate, budget expansion decision, or intentional interruption:

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

### Single-agent debugging

Best when you are fixing or testing one stage.

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/local-test2
python -m researchos.cli run-task T7.5 --workspace ./workspace/local-test2
python -m researchos.cli run-task T9 --workspace ./workspace/local-test2
```

You can also copy upstream artifacts from another workspace:

```bash
python -m researchos.cli run-task T8-WRITE \
  --workspace ./workspace/scratch \
  --from ./workspace/local-test2
```

Notes:

- `run` and `resume` advance the full state machine
- `run-task` only executes one stage
- but if you re-run `run-task` on the same workspace, many stages now continue from existing artifacts instead of starting from an empty slate

### Inspect status and trace

```bash
python -m researchos.cli status --workspace ./workspace/local-test2
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/local-test2
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T7
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

- T7.5 evaluation decision
- post-evaluation branch choice
- submission / final decision points

These only fully participate when using `run` / `resume`, not when using isolated `run-task`.

## Documentation Map

Start here depending on your role:

- Workflow overview: [docs/agent_pipeline.md](./docs/agent_pipeline.md)
- Runtime internals: [docs/runtime.md](./docs/runtime.md)
- Docker: [docs/docker.md](./docs/docker.md)
- Configuration: [docs/config.md](./docs/config.md)
- Developer guide: [docs/dev.md](./docs/dev.md)
- Full workflow and per-agent details: [docs/agent_pipeline.md](./docs/agent_pipeline.md)

## Current Implementation Status

The current codebase is usable, but it is still an evolving research runtime.

Practical expectations:

- the pipeline is runnable
- resume and artifact recovery are implemented for major stages
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
2. `workspace/<name>/_runtime/logs/researchos.log`
3. `workspace/<name>/_runtime/traces/*.jsonl`
4. the actual task artifacts in the workspace

## License and Project-Specific Notes

Check repository-local files such as:

- `CLAUDE.md`
- `config/README.md`
- `docs/agent_pipeline.md`

for repository-specific conventions and implementation details.
