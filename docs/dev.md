# ResearchOS Developer Guide

This document is for contributors changing runtime behavior, agents, tools,
prompts, validators, Docker, or configuration. For ordinary installation and
recovery, use the root README instead.

## 1. Development Model

ResearchOS is a multi-agent, artifact-first state-machine runtime:

```text
CLI -> runner -> StateMachine -> ExecutionContext -> AgentRunner
    -> task agent / SkillAgent -> tools -> workspace artifacts -> validator
```

An LLM response is not a successful stage. A stage succeeds only after the
declared artifact contract validates. This is why a change must be assessed at
four layers:

1. task state/configuration and transition
2. agent/prompt/tool behavior
3. artifact validator and resume semantics
4. unit, real integration, and user-visible progress tests

Useful source locations:

| Path | Responsibility |
| --- | --- |
| `researchos/cli.py` | CLI parsing, startup checks, `doctor`, tool/agent registration. |
| `researchos/cli_runners/` | Full-pipeline and single-task execution. |
| `researchos/orchestration/` | State transition, gates, task I/O contracts, recovery. |
| `researchos/agents/` | Task-specific behavior and output validation. |
| `researchos/tools/` | Workspace-bounded deterministic capabilities. |
| `researchos/runtime/` | LLM client, progress, logs, trace, config, workspace initialization. |
| `config/system_config/` | State-machine/gate contracts. |
| `tests/unit/`, `tests/real/` | Deterministic and capability-dependent regression coverage. |

## 2. Local Environment

```bash
conda create -n researchos python=3.11 -y
conda activate researchos
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

Run the baseline checks before changing code:

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/agentic
python -m researchos.cli selftest
```

### TeX Is Not A Python Dependency

`requirements.txt` stays Python-only. Install the host compiler to exercise
native T3.6/T9 paths on Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

The host runs `latexmk` before it uses Docker. The real Docker backend test must
therefore explicitly request `backend="docker"`; otherwise a correctly
configured host will use native TeX instead.

## 3. Developer Smoke Workflow

Never use the user's production workspace for ad hoc development runs.

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/dev-smoke \
  --project-id dev-smoke \
  --topic "runtime smoke test"

python -m researchos.cli run-task HELLO --workspace ./workspace/dev-smoke
python -m researchos.cli status --workspace ./workspace/dev-smoke
```

For a small real-pipeline integration run:

```bash
python -m researchos.cli run_smoke \
  --workspace ./workspace/dev-smoke-t2 \
  --from ./workspace/dev-smoke \
  --active-pool-max 20 \
  --deep-read-target 3 \
  --abstract-sweep 5 \
  --skip-startup-selftest
```

`run_smoke` is an integration test aid, not valid final literature coverage.

## 4. Docker Development

The Dockerfile builds a complete CLI/PDF-validation runtime. Compose uses the
same source contracts as native mode and must not use Docker-in-Docker.

```bash
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

When a Linux host has an unusually slow Docker bridge route to Debian mirrors,
use host networking only for image construction:

```bash
docker build --network=host \
  -t researchos/system:latest \
  -f infra/docker/Dockerfile .
```

The running Compose service must remain unprivileged, without a Docker socket,
and compile TeX from packages included in its own image.

## 5. Developing A Guided Standalone Skill

Do not add a public Skill as a long prompt that assumes another product's tools
or an implicit chat history. It must be discoverable by
`researchos/skills/loader.py`, executable through `SkillAgent`, and explicit
about the files a researcher must supply.

1. Create `skills/<skill-name>/SKILL.md` with a unique lowercase name and a
   trigger-quality description.
2. Prefer current runtime tool names in `tools:` and set `strict_tools: true`.
   A public Skill may not silently drop unavailable tool names.
3. Declare bounded `allowed_read_prefixes` and `allowed_write_prefixes`.
4. Declare matching `outputs_expected` and `interaction.outputs`.
5. Add `interaction.required_inputs`/`optional_inputs` with exact
   workspace-relative alternatives, extension checks, minimum sizes, Chinese
   labels, descriptions, and output meanings.
6. Make the body describe the real tools/artifacts to use, evidence limits,
   audit steps, and a final human-readable summary. Do not reference a missing
   script, another agent product, `$ARGUMENTS`, or an unregistered MCP tool.
7. Do not touch `skills/external_executor_skills/` while building a standalone
   Skill; that tree follows the external-executor handoff protocol instead.
8. Treat project artifacts as candidate inputs. The body must read the generated
   `user_inputs/<skill>/_intake.md`; when a required fact is semantically absent,
   it must write `_followup_request.md`, call `ask_human`, and resume from the
   recorded response rather than guessing.

Before an LLM run, exercise the contract itself:

```bash
researchos list-skills --workspace ./workspace/dev-smoke --verbose
researchos browse-skills --workspace ./workspace/dev-smoke
researchos describe-skill my-skill --workspace ./workspace/dev-smoke
researchos run-skill my-skill "test request" --workspace ./workspace/dev-smoke
researchos skill-status --workspace ./workspace/dev-smoke
```

The third command must produce `WAITING_INPUT` without provider access when a
required input is missing. Add the file, then use `--session-id ... --resume`.
During a real run, assert that the session stores only observable runtime events
(`awaiting_llm`, `tool_running`, `tool_completed`), never a hidden-reasoning transcript.
For a real success path, use `MockLLMClient` at unit scope and a narrowly
scoped real CLI/provider test only when its capability is available. Add tests
for discovery, strict tool mapping, missing-input persistence, successful
resume input validation, and declared outputs.

## 6. Tests Required For Changes

Run the narrow test first, then the affected suite, then full regression before
finishing cross-cutting work:

```bash
pytest -q tests/unit
pytest -q tests/real
python -m compileall -q researchos
git diff --check
```

For a LaTeX/runtime change, also run:

```bash
pytest -q tests/real/test_docker_latex_backend.py
pytest -q tests/real/test_survey_visuals.py
python -m researchos.cli doctor --workspace ./workspace/agentic
```

The real Docker test compiles a Chinese XeLaTeX document and an English
pdflatex/BibTeX document, checks `%PDF`, and verifies compile reports.
`test_survey_visuals.py` performs real matplotlib generation, checks PNG bytes,
embeds the generated visual into a real PDF, and verifies the safe wide-table
resizebox report. Run an equivalent Compose-native smoke command after changing
the Dockerfile or Python graphics requirements.

Test classification:

| Test location | Contract |
| --- | --- |
| `tests/unit/` | No external service or Docker requirement; mock process/network boundaries. |
| `tests/real/` | Real capabilities such as Docker, TeX, providers, or local tools; skip with a clear reason when unavailable. |
| `tests/manual/` | Local temporary probes; never commit them. |

## 7. Change Checklist

### Agent, Prompt, Or State Change

- Update the task node in `config/system_config/state_machine.yaml` when its
  inputs, outputs, transition, or gate changes.
- Update the agent output validator and task I/O contract together.
- Preserve valid artifacts on resume; do not require model memory.
- Add a test for any recovery path, prefinalize shortcut, or human gate change.
- Update `docs/agent_pipeline.md` only for canonical stage-contract changes.

### Tool Or Environment Change

- Keep file access within `WorkspaceAccessPolicy`.
- Return a concrete recoverable error for environment absence.
- Add a preflight before expensive LLM stages when a deterministic dependency is
  required, as T3.6-COMPILE and T9 do for TeX.
- Test host/native and container behavior separately when both are supported.
- Update `doctor`, README, `docs/docker.md`, and `deploy/README.md` together.

### User Experience Change

- Emit concise observable stage progress, not private model reasoning.
- Write full data to a named artifact and show its path plus a useful summary.
- Add a stage completion summary: work performed, outputs, meaning, and next
  action.
- Keep human gates parseable from natural-language input where configured; do
  not force users through one-field-at-a-time prompts unnecessarily.

### Guided Skill Change

- Verify `list-skills` and `describe-skill` without an LLM endpoint.
- Verify `browse-skills` renders cards and can exit without starting a provider.
- Verify a missing-input `run-skill` writes a session and does not initialize a
  provider, then verify the same `--session-id --resume` accepts a repaired input.
- Verify standalone and project workspace modes, intake-packet persistence,
  follow-up-tool availability, and no accidental satisfaction of requirements by `_intake.md`.
- Verify all `strict_tools` names are present in a real builtin `ToolRegistry`.
- Verify the Skill body calls current tools and every declared output exists on
  the successful path.
- Update both READMEs, `docs/QUICKSTART.md`, `docs/runtime.md`, and
  `docs/project_structure.md` when the public input/output contract changes.

### Venue-Aware Writing Change

- Update `config/system_config/venue_writing_profiles.yaml`, Writer prompt,
  `writing_style.json` gate payload, `writing_storyline.md` validator, and
  `audit_writing_craft` together.
- Add contrast tests for at least one UTD/IS profile and one concrete CCF venue
  profile. Do not encode a remembered official page limit as an internal budget.
- Treat section budgets and storyline coverage as diagnostics unless a separate
  current official template/validator proves a hard requirement.

## 8. Debugging A Failure

```bash
python -m researchos.cli status --workspace ./workspace/dev-smoke
tail -n 120 ./workspace/dev-smoke/_runtime/logs/researchos.log
python -m researchos.cli trace <run_id> --workspace ./workspace/dev-smoke
```

For `WAITING_ENVIRONMENT`, use `doctor` and repair the exact selected backend.
For a provider failure, run `selftest`, preserve workspace artifacts, and
`resume`. For validator failure, fix the named artifact rather than bypassing
the validator.

## 9. Documentation Ownership

| Change | Documentation to update |
| --- | --- |
| Installation, commands, resume UX | `README.md`, `README.zh-CN.md`, `docs/QUICKSTART.md` |
| Docker, TeX, image, Compose | `docs/docker.md`, `deploy/README.md`, `docs/project_structure.md` |
| Logging, progress, traces, pause recovery | `docs/logging.md`, `docs/runtime.md` |
| Configuration defaults | `docs/config.md`, `config/README.md` |
| Stage behavior/artifacts | `docs/agent_pipeline.md`, state-machine comments |
| Public standalone Skill contract or workflow | Both READMEs, `docs/QUICKSTART.md`, `docs/runtime.md`, `docs/project_structure.md`, `docs/logging.md` |

Keep root README concise. Do not duplicate full stage details across user guides.
