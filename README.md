# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS is an artifact-first research runtime for auditable literature work, evidence-bounded ideation, external-executor handoff, manuscript production, review, and submission packaging. A project lives in a workspace: durable files, not chat history, are the source of truth.

```text
T1 scope -> T2 discover -> T3 read -> T3.5 synthesize
  -> optional T3.6 survey -> T4 ideas -> T4.5 novelty
  -> T5 external execution -> T8 manuscript -> T9 submission bundle
```

## Before You Run

- Use one writer per workspace. Do not run native and Docker commands against the same project concurrently.
- Store provider secrets in local `.env` or the ignored `config/model_settings.yaml`; never commit either file, workspaces, PDFs, runtime logs, or generated submissions.
- Python packages belong in `requirements.txt` / `pyproject.toml`. TeX, `latexmk`, and fonts are OS or Docker-image dependencies.

## Native Setup

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos
pip install -e .
python -m researchos.cli configure-llm
```

Pip-only setup is also supported:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

T3.6 and T9 need a real TeX backend to produce and validate PDFs. On Ubuntu/Debian, install the host toolchain or use the Docker fallback described below:

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

Verify configuration, dependencies, the selected TeX backend, and provider connectivity before a long run:

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

Docker uses the same CLI, validators, state machine, and workspace format. The host `workspace/` directory is mounted at `/app/workspace` in the container. The supplied image includes Python dependencies, matplotlib, TeX Live, `latexmk`, pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX support.

```bash
python -m researchos.cli configure-llm
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

Native `latex.default_backend: auto` uses local `latexmk`, then local `tectonic`, then the allowlisted Docker TeX image when enabled. Compose never uses Docker-in-Docker. See [Docker and TeX](docs/en/docker.md).

## Create And Run A Project

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "memory systems for LLM agents"

python -m researchos.cli run --workspace ./workspace/project-a
```

`run` stops at consequential human gates. T2 accepts one natural-language coverage request, including manuscript language and Chinese-literature policy:

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

English wording such as `candidate pool 30, deep read 15, abstract read 15, English manuscript, exclude Chinese literature` is equivalent. The confirmed values are saved in `literature/literature_params.json` before retrieval starts.

T3 processes strong-evidence papers individually because page coverage and section evidence are paper-specific. Its post-read abstract sweep packs independent abstracts into provider-context-sized calls and writes one separate `ABSTRACT-ONLY` note per paper. A requested "abstract read N" is a real note-count target: metadata-only triage never counts, readable backlog records refill a shortfall, and an unresolved shortfall pauses T3 rather than entering T3.5. Shallow papers with a local PDF are listed in `literature/reading_upgrade_queue.jsonl` for a real full-text or scoped partial-text upgrade; downloading a PDF never promotes evidence. Books, monographs, and sources over 100 pages default to question-driven chapter/page reading with recorded coverage.

To pause a live command, press `Ctrl+C` once. ResearchOS persists the workspace as `PAUSED` and prints the matching `resume` command. `Ctrl+Z` only suspends the shell job; use `fg` and then `Ctrl+C` to recover from an accidental suspend.

## Daily Commands

| Goal | Command |
| --- | --- |
| Initialize an empty project | `python -m researchos.cli init-workspace --workspace ./workspace/project-a --project-id project-a --topic "research topic"` |
| Start a complete pipeline in a new workspace | `python -m researchos.cli run --workspace ./workspace/project-a` |
| Inspect current stage and pause reason | `python -m researchos.cli status --workspace ./workspace/project-a` |
| Scan all local workspaces, active processes, gates, and stale states | `python -m researchos.cli workspace-status --workspace-root ./workspace` |
| Continue a paused project | `python -m researchos.cli resume --workspace ./workspace/project-a` |
| Revalidate T3 and fill only missing reading coverage | `python -m researchos.cli resume --workspace ./workspace/project-a --from-task T3` |
| Re-enter this workspace at T4 after validating T4 prerequisites | `python -m researchos.cli resume --workspace ./workspace/project-a --from-task T4` |
| Add missing T4 inputs from another workspace, then resume this project | `python -m researchos.cli resume --workspace ./workspace/t4-debug --from ./workspace/project-a --from-task T4` |
| Return to the optional Survey decision | `python -m researchos.cli resume --workspace ./workspace/project-a --from-task T3.6` |
| Start a new full pipeline at T4 using validated upstream artifacts from another workspace | `python -m researchos.cli run --workspace ./workspace/project-b --from ./workspace/project-a --start-task T4` |
| Debug only T4 in a fresh workspace copied from another project | `python -m researchos.cli run-task T4 --workspace ./workspace/t4-debug --from ./workspace/project-a` |
| Run T5 research reboost only | `python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a` |
| Run the T5 executor-selection gate after T5 specialization | `python -m researchos.cli run-task T5-EXECUTOR-GATE --workspace <workspace>` |
| Accept the completed external T5 handoff and run the full T8 chain | `python -m researchos.cli run-task T8 --workspace <workspace>` |
| Run one task without advancing the full pipeline | `python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| Validate one task's artifacts | `python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| Regenerate the deterministic Survey audit without an LLM | `python -m researchos.cli audit-survey --workspace ./workspace/project-a` |
| Inspect a recorded run | `python -m researchos.cli trace <run-id> --workspace ./workspace/project-a` |
| Configure and verify the shared LLM connection | `python -m researchos.cli configure-llm` |
| Check the configured LLM connection only | `python -m researchos.cli selftest` |
| Check Python, PDF, and TeX prerequisites | `python -m researchos.cli doctor --workspace ./workspace/project-a` |
| Check state-machine and runtime configuration | `python -m researchos.cli validate-config` |
| List independently runnable Skills | `python -m researchos.cli list-skills --workspace ./workspace/project-a` |
| Browse Skills and their readiness conditions | `python -m researchos.cli browse-skills --workspace ./workspace/project-a` |
| Inspect a Skill's input/output/recovery contract | `python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a` |
| Start or resume a guided Skill | `python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a --session-id reading-01` |
| Inspect resumable Skill sessions | `python -m researchos.cli skill-status --workspace ./workspace/project-a` |

For the T5 material gate, Codex/Claude launch, external A-F phases, and T8 handoff contract, see [T5 External Executor Guide](docs/en/t5_external_executor.md).

Use `run --from <source-workspace> --start-task <task>` only to initialize a new target workspace from another project's validated upstream artifacts. `run-task <task> --from <source-workspace>` performs the same declared-input copy but executes only that task. For every literature-dependent downstream stage (`T3.5`, `T3.6`, `T4`, `T5`, and `T8`), the import closure includes the complete `literature/` tree, so initialized empty note directories cannot suppress real source paper cards. `bridge_notes/` remains the paper-note root; `cross_domain_catalogs/` is the independent retrieval catalog and is imported alongside it. The import happens before the model connection check, so a provider outage does not discard the prepared debugging workspace. Neither command is a state merge. The recovery guide is in [Quick Start](docs/en/QUICKSTART.md).

`resume --from-task <task>` is the deliberate same-workspace recovery command. It validates the target task's prerequisites, clears the old pending gate, and records the re-entry in `state.yaml`. Add `--from <source-workspace>` when an existing target needs missing declared inputs before it resumes; source state/history are never merged. Public names are accepted: `T3.6` means `T3.6-GATE-SURVEY`, the "write a Survey?" decision; `T8` means the writing-style Gate. After an optional Survey has finished, `resume --from-task T4` is the supported way to proceed without revisiting its post-Survey gate; it still refuses to run if T4 inputs are incomplete.

| Workspace situation | Use | Behaviour |
| --- | --- | --- |
| New directory, no `state.yaml` | `run` | Creates and starts a new pipeline. |
| Paused after Ctrl+C, a provider outage, or a gate | `resume` | Continues the same workspace from durable artifacts. |
| Existing workspace, restart a validated later stage | `resume --from-task T4` | Re-enters only after the selected task's prerequisites pass. |
| Another project's upstream artifacts | `run --from <source> --start-task T4` | Creates a distinct target workspace and copies only declared prerequisites. |
| Isolated debugging with another project's materials | `run-task T4 --from <source>` | Copies the task's declared inputs first, then runs only T4; source state and artifacts are untouched. |
| Existing `state.yaml`, but `run` was entered again | Do not continue | The command refuses to overwrite or implicitly resume; use `resume`. |
| No `state.yaml`, but `resume` was entered | Do not create | The command refuses to manufacture a project; use `run`. |
| `COMPLETED` workspace | Start a new workspace | `resume` is refused so completed artifacts are not silently rewritten. |

For an interrupted survey section, validate before resuming. A valid `T3.6-SEC-*` output advances without rewriting the completed section; the section worker is restricted to that file and its matching survey state entry.

## Complete CLI Command Reference

<!-- CLI_COMMAND_REFERENCE_START -->

The table below is intentionally complete and is checked against the live CLI parser in unit tests. Shared flags such as `--workspace`, `--no-color`, `--verbose`, `--verbosity`, `--quiet`, and `--no-banner` are accepted by each operational command unless its help says otherwise.

| Command | Purpose | First place to read |
| --- | --- | --- |
| `init-workspace` | Create a standard workspace and optional `project.yaml`. | This README, [Quick Start](docs/en/QUICKSTART.md) |
| `run` | Run the complete state-machine pipeline; supports `--from` and `--start-task` for a new target workspace. | [Quick Start](docs/en/QUICKSTART.md) |
| `run_smoke` | Run a reduced but real pipeline integration profile. | `researchos run_smoke --help` |
| `resume` | Continue a paused workspace, or safely re-enter its `--from-task` after prerequisite validation. | [Quick Start](docs/en/QUICKSTART.md) |
| `run-t8` | Compatibility alias for `run-task T8`; independently accepts the modern T5 Writer Handoff and runs the complete T8 chain. | [Quick Start](docs/en/QUICKSTART.md) |
| `run-task` | Diagnose one state-machine task; public `T8` is the explicit full-chain handoff entry. | [Quick Start](docs/en/QUICKSTART.md) |
| `status` | Show a compact workspace state and next action; `--detail` prints raw state. | [Logging](docs/en/logging.md) |
| `workspace-status` | Scan a workspace root; distinguish active, stopped, stale, paused, and orphan workspaces. Add `--verbose` for error detail. | [Quick Start](docs/en/QUICKSTART.md) |
| `configure-llm` | Save and test the provider, URL, key, model, and same-model retry policy used by every stage. | [Configuration](docs/en/config.md) |
| `selftest` | Check configured LLM endpoint connectivity. | This README |
| `doctor` | Check Python, native/Docker, and TeX prerequisites. | [Docker and TeX](docs/en/docker.md) |
| `trace` | Render one run trace; `--raw` prints JSONL. | [Logging](docs/en/logging.md) |
| `validate` | Validate declared artifacts for one task or the current context. | [Quick Start](docs/en/QUICKSTART.md) |
| `audit-survey` | Rebuild the deterministic T3.6 Survey audit without a model call. | [Logging](docs/en/logging.md) |
| `validate-config` | Validate state machine, gates, runtime, and configuration contracts. | [Configuration](docs/en/config.md) |
| `specialize-executor-skills` | Generate or validate the project-specific T5 external-executor Skill suite. | [Quick Start](docs/en/QUICKSTART.md) |
| `run-skill` | Start or resume one guided Skill; supports `--session-id`, `--resume`, and non-interactive execution. | [Skills](docs/en/skills.md) |
| `list-skills` | List all discoverable Skills and their declared capabilities. | [Skills](docs/en/skills.md) |
| `browse-skills` | Browse and choose a Skill through terminal cards. | [Skills](docs/en/skills.md) |
| `describe-skill` | Display one Skill's input/output/recovery/capability contract. | [Skills](docs/en/skills.md) |
| `skill-status` | Show resumable guided Skill sessions and integrated workflow phases. | [Skills](docs/en/skills.md) |

<!-- CLI_COMMAND_REFERENCE_END -->

## Reading Files Without Losing Context

`read_file` is context-aware, not a fixed 200/3,000-character reader. Before building context-sensitive tools, ResearchOS queries direct and collection model metadata on compatible base paths, including both `/v1` and non-`/v1` OpenAI-style deployments. It uses a matched `context_length`, `context_window`, `max_context`, `max_input_tokens`, or compatible nested capacity field when the provider exposes one. The result is cached for the active run and is shared by file reads, history truncation, and abstract batching. If a relay does not publish verifiable capacity metadata, the system fallback is 128k tokens; it is not a claim about the provider's public API limit. Per-task context overrides are ignored in the one-model configuration, so user-facing behavior follows the configured provider rather than an old Agent tier.

The effective capacity reserves room for the system prompt/history/tool calls and allocates 70% of the remainder to a file result. It reads the whole file only when it fits the automatic full-read share, then uses a context-sized page. The public `read_file` schema deliberately exposes only `path` and `offset`; it does not accept a manual `max_chars` budget, so a model cannot issue `max_chars=200` and force repeated tiny reads. For a known local section, use `grep_search` to find the offset and call `read_file` again with that offset. Result metadata records the applied capacity source and page budget. For a large T2 `literature/papers_raw.jsonl`, reading remains available, but checkpoint-safe pages begin before the raw pool can consume the working retrieval plan, preserve JSONL record boundaries, and carry completed queries, source coverage, raw count, and the authoritative `next_offset`.

## Guided Skills

ResearchOS exposes both atomic Skills and composed, resumable research workflows. Atomic Skills cover paper intake, DOI/title resolution, note cards, evidence matrices, ideation, writing, review, polishing, compilation, and submission checks. Integrated Skills add explicit subphases, evidence gates, artifact manifests, and durable recovery for multi-step work:

| Need | Integrated Skill | What it does |
| --- | --- | --- |
| Understand a field | `domain-synthesis-studio` | Scope -> optional retrieval -> method/mechanism/tension synthesis -> Survey or Idea decision |
| Compare sources from DOI/PDF | `literature-comparison-studio` | Source resolution -> section evidence -> comparison matrix and claim boundary |
| Build a review corpus | `literature-review-studio` | Review scope -> retrieval -> reading coverage -> taxonomy readiness -> Survey handoff |
| Prepare a Survey safely | `survey-evidence-package` | Corpus sufficiency -> taxonomy/storyline -> targeted supplement decision -> T3.6 handoff |
| Generate cross-domain directions | `cross-domain-idea-studio` | Bridge evidence -> transfer-risk audit -> candidate jury -> human selection |
| Start or resume native idea evolution | `t4-evolution` | Inspect T4 evidence/state -> explain preserved Population -> safe native pipeline handoff |
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

In a TTY, `run-skill` collects missing material through a restricted multi-turn intake, stages only human-supplied material under `user_inputs/<skill>/`, rechecks readiness, then asks for an explicit `执行` / `暂停` decision. It does not generate research, manuscript, experiment, or citation deliverables while required input is missing. The constrained intake Agent may use the provider to ask for one fact at a time and encode only human-provided material into the declared input files. Automation should use `--non-interactive`; missing inputs then create a resumable `WAITING_INPUT` session without constructing a provider client.

An integrated Skill records `pending`, `running`, `completed`, `waiting_input`, `waiting_evidence`, or `skipped` for each declared research phase in the same session file. It may use source-returning search tools to try to supplement missing literature after the researcher authorizes that scope. Search leads, metadata, and abstracts remain visibly weaker than section-level or full-text evidence; a workflow may not advance a strong scholarly claim merely because it successfully found more records.

The catalog's input/output paths are checked against each Skill's workspace permissions at discovery time. A listed public Skill therefore cannot advertise a path which will later be rejected as `access_denied`.

For `pdf-note-card`, `paper-comparison`, and `literature-comparison-studio`, guided intake can also use a DOI, arXiv/OpenAlex ID, direct PDF URL, exact title, or an explicitly scoped topic-plus-count request. It records the identifier/query and retrieval result under `user_inputs/<skill>/_source_resolution.md`, downloads only to that Skill's declared input area, and preserves metadata/search hits as weaker-than-PDF evidence. Entering menu option `2` or `暂停` at the missing-input control immediately persists the session as `WAITING_INPUT`; it does not start another intake round.

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 \
  --resume
```

See [Skills](docs/en/skills.md) for the capability map and input contract.

## Evidence Boundary

ResearchOS does not ban a metric, dataset, baseline, or benchmark by name. AUUC, Qini, accuracy, F1, or any other concrete protocol detail is valid when a user-provided file, audited workspace artifact, or verified plan explicitly identifies it and provides a traceable source. The system must not infer that detail from a topic, method name, field convention, or an example. Missing details remain `unknown`, `proposed_not_verified`, or a human/evidence blocker.

## CLI Display And Diagnostics

Every actual CLI command displays the DIG · BUAA / ResearchOS startup panel once by default. In an interactive terminal it uses the progressive `D -> DI -> DIG` color animation; non-TTY output receives one portable static panel.

- `--no-banner` suppresses it for scripts.
- `--no-color` removes ANSI color while preserving the same information.
- `--verbosity concise|normal|detailed` changes research-process detail.
- `--quiet` restricts console output to essential state, errors, pauses, and final outcomes.
- `--json-events` mirrors bounded structured events to stdout; every run also writes `<workspace>/_runtime/events/<run-id>.jsonl`.

Console panels expose stage inputs, calculations, decisions, risks, and artifact manifests. They never expose private model reasoning or raw prompt payloads. Use [Logging and Troubleshooting](docs/en/logging.md) for log and trace triage.

## Documentation

| Need | Document |
| --- | --- |
| First run and recovery | [Quick Start](docs/en/QUICKSTART.md) |
| Pipeline stages and artifacts | [Pipeline Overview](docs/en/agent_pipeline.md) |
| Full pipeline and artifact contracts | [Pipeline Detail](docs/en/agent_pipeline_detail.md) |
| Configuration | [Configuration](docs/en/config.md) |
| Native/Docker/TeX | [Docker and TeX](docs/en/docker.md) |
| Runtime, events, and extension points | [Runtime](docs/en/runtime.md) |
| Skills | [Skills](docs/en/skills.md) |
| Logs, traces, and debug procedure | [Logging and Troubleshooting](docs/en/logging.md) |
| Repository and workspace layout | [Project Structure](docs/en/project_structure.md) |
| Contributor workflow | [Development](docs/en/dev.md) |

Additional operational references: [repository/workspace layout](./docs/en/project_structure.md), [Compose deployment](./deploy/README.md), and [maintained scripts](./scripts/README.md).
