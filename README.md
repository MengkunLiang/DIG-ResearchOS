# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS is an artifact-first research runtime for auditable literature work, evidence-bounded ideation, external-executor handoff, manuscript production, review, and submission packaging. A project lives in a workspace: durable files, not chat history, are the source of truth.

```text
T1 scope -> T2 discover -> T3 read -> T3.5 synthesize
  -> optional T3.6 survey -> T4 ideas -> T4.5 novelty
  -> T5 external execution -> T8 manuscript -> T9 submission bundle
```

## Understand The Flow Before Internal Names

`T` is a workflow-stage label, not a command you need to type every day. In normal use, start a new project with `run` and continue a paused one with `resume`. ResearchOS stops at a `Gate` only when a research decision needs your input and explains the next action. `T3.5` is the synthesis after T3 reading, `T3.6` is the optional Survey branch, and `T4.5` is the novelty audit after T4 ideation, not a separate “half task” to run manually.

Longer names such as `T5-REBOOST-GATE`, `T5-PROTOCOL-GATE`, and `T3.6-SEC-INTRO` are internal checkpoints used in terminal status and targeted debugging. First-time users do not need to memorize them; use the table below to see which part of the research workflow they belong to. There is no user-facing T6/T7 in the current main path: identically named old nodes exist only for historical-workspace compatibility, while real experiments use T5's external-executor path.

| Stage you will see | Plain-language purpose | When you decide | Files to open first |
| --- | --- | --- | --- |
| T1 scope | Define the question, boundaries, target venue, and seed material. | Confirm the topic and scope during initialization. | `project.yaml` |
| T2 literature | Search, deduplicate, verify, and prioritize the reading pool. | Choose literature coverage, deep-read target, manuscript language, and Chinese-literature policy. | `literature/literature_params.json`, reading queue |
| T3 reading | Read papers and record page- or section-recoverable evidence. | Only when a key PDF/evidence source is unavailable or needs supplementation. | `literature/deep_read_notes/`, `comparison_table.csv` |
| T3.5 synthesis | Turn the literature into mechanisms, method differences, tensions, and research gaps. | Decide whether to take the optional Survey branch. | `literature/synthesis.md` |
| T3.6 optional Survey | Write a field Survey only when the current evidence justifies it; otherwise it is skipped. | Skip, write from the present corpus, or request one targeted supplement. | `drafts/survey/` |
| T4 research ideas | Generate, compare, and evolve multiple research directions. | Proceed, optimize, explore again, or inspect a Candidate only. | Candidate Cards, scores, evidence, and lineage under `ideation/` |
| T4.5 novelty audit | Check similar work and mechanism differences; turn the selected direction into a formal research package. | Review the novelty verdict and required baselines. | `ideation/proposal/research_proposal.md`, `hypotheses.md`, `exp_plan.yaml` |
| T5 external-execution preparation | Compile the T4.5 package into an executor handoff whose research constraints cannot be silently changed. | Resolve only settings that affect research boundaries; place existing resources or let the executor prepare public ones. | `external_executor/handoff_pack.json`, `resources/` |
| T8 writing | Write, review, and revise using verified experimental facts. | Choose a writing style or template. | `drafts/` and experiment claim/evidence files |
| T9 submission | Review, genuinely compile, and package the submission. | Only when an environment or compilation recovery is needed. | `submission/`, final PDF, and compile report |

The two most important boundaries are these: T4.5 turns an interesting idea into a research plan with hypotheses, an experiment plan, and risk boundaries. T5 is not an experiment runner: it prepares and verifies an execution contract; an external Codex, Claude, or human executor then performs the real work in the same workspace.

When T4.5 succeeds, the terminal displays a “key research files” table that points directly to the proposal, hypotheses, experiment plan, contribution/validation maps, stopping criteria, and novelty audit, with their next use. T5 consumes those files; you do not need to find them from memory.

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

## Everyday Use: Choose A Command By Goal

Most projects repeatedly use only `status`, `run`, and `resume`. Do not edit `state.yaml` to skip a stage, and do not treat `run-task` as a shortcut for the complete workflow.

| What you want to do now | Preferred command | What ResearchOS does |
| --- | --- | --- |
| See where the project stopped and whether it needs a choice | `status --workspace <project-dir>` | Shows the current stage, Gate, latest actionable message, and next command. |
| Create a research project from scratch | `init-workspace`, then `run` | Creates a new workspace and starts the complete workflow. |
| Continue the same paused project | `resume --workspace <project-dir>` | Reuses confirmed choices and valid files; it does not repeat completed model calls. |
| Intentionally restart at T2/T3/T4 | `resume --from-task T4` | Checks the selected stage's prerequisites before safe re-entry. |
| Start a new project with another project's material | `run --from <source-dir> --start-task T4` | Creates a separate target workspace and copies declared upstream inputs without merging histories. |
| Diagnose one stage only | `run-task T4` | Runs that task only; it does not advance the complete main path. |
| Browse or run a focused capability | `browse-skills` / `run-skill` | Works in an independent, resumable Skill session. |

### 1. System Status And Diagnostics

```bash
# Where is this project paused and what action is needed?
python -m researchos.cli status --workspace ./workspace/project-a

# Which local workspaces are active, paused, or stale?
python -m researchos.cli workspace-status --workspace-root ./workspace

# Check the provider, local PDF/TeX prerequisites, and state-machine configuration.
python -m researchos.cli selftest
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli validate-config

# Inspect one recorded execution or validate one stage's saved outputs.
python -m researchos.cli trace <run-id> --workspace ./workspace/project-a
python -m researchos.cli validate --task T4 --workspace ./workspace/project-a
```

### 2. Run A Complete Project Or One Stage

```bash
# Create and start a new complete project.
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a --project-id project-a --topic "research topic"
python -m researchos.cli run --workspace ./workspace/project-a

# Run one isolated stage for diagnosis. It keeps the rest of the pipeline still.
python -m researchos.cli run-task T4 --workspace ./workspace/t4-debug \
  --from ./workspace/project-a
```

### 3. Resume, Re-enter, Or Migrate

```bash
# Normal continuation: reuse prior confirmed choices and completed artifacts.
python -m researchos.cli resume --workspace ./workspace/project-a

# Deliberately reopen a validated stage in the same workspace.
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T3
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T4

# Reopening T2 always shows the saved coverage/language parameters once more.
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T2

# Create a separate target from another project's validated upstream files.
python -m researchos.cli run --workspace ./workspace/project-b \
  --from ./workspace/project-a --start-task T4

# Fill only missing declared inputs from another workspace before re-entry.
python -m researchos.cli resume --workspace ./workspace/t4-debug \
  --from ./workspace/project-a --from-task T4
```

`resume --from-task` never merges histories or asks you to edit `state.yaml`. `T3.6` is the optional Survey decision; `T8` is the writing entry gate. An explicit T2 re-entry reopens the saved parameter confirmation, or the full parameter chooser when a legacy record is missing, so a new search cannot silently use stale scope settings. An explicit T3 re-entry first opens the retrieval-coverage decision; as long as the reading queue remains, missing historical summaries such as `search_log.md` do not suppress that choice.

### 4. Skills: Focused, Resumable Workflows

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01
python -m researchos.cli skill-status --workspace ./workspace/project-a
```

### 5. T5 External Execution: From Research Plan To Real Experiments

The normal pipeline reaches T5 automatically after T4.5. T5 deterministically compiles the handoff and publishes the 13 project-specific executor Skills; it does not ask a model to reconstruct these control files. It then pauses at Protocol Readiness; the optional material inventory is only for resources you already have, while missing public resources can be prepared automatically before full executor selection.

In normal use, do not start these T5 subnodes yourself. T5 reads and preserves the complete T4.5 proposal, formal hypotheses, experiment plan, novelty audit, and stopping criteria. It neither repeats T4/T4.5 nor permits an executor to silently change the research task, core mechanism, required baseline set, benchmark scope, or paper-claim boundary. Seeds use an auditable stable default ensemble unless the project already declares its own seed policy.

```text
T4.5 passes
  -> T5 compiles the research handoff and project-specific Skills
  -> Protocol confirmation: separate automatically preparable resources/settings from real research-boundary changes
  -> Optional local-material inventory, or let a bounded executor prepare public resources
  -> Choose a Codex / Claude / manual executor
  -> External execution writes auditable results
  -> T8 receives results and starts manuscript work
```

The Protocol Confirmation page is not an error page. `ready` means that a full executor can be selected. `protocol_decision_required` does **not** mean that you must manually find data, code, a baseline, a benchmark, or weights: choose “let the external executor prepare resources.” It runs Phase A/B only, searches authorized public sources, locks revisions, performs license/security/protocol review, and records provenance; then it stops and T5 recompiles on `resume`. For a setting that stays inside the existing T4.5 scope, Phase B records the exact selected package/version/model/scale in its operational-settings receipt, so the full executor can consume it without a new human form. An undeclared seed policy uses a stable auditable default ensemble. Only `blocked` means that a minimum experiment definition is genuinely missing. A human decision is reserved for changing the T4.5-defined task, core mechanism, required-baseline set, benchmark scope, or claim/contribution boundary—not for ordinary public-resource retrieval.

```bash
# Targeted T5 diagnostics only. These do not run an experiment.
python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a
python -m researchos.cli run-task T5-SPECIALIZE --workspace ./workspace/project-a
python -m researchos.cli run-task T5-PROTOCOL-GATE --workspace ./workspace/project-a
```

If you already have resources, place them in the following locations before executor selection; this is optional. When you do not, use “let the external executor prepare resources” rather than manually downloading unknown repositories. Do not put raw data or downloaded repositories directly under `external_executor/expr/`:

```text
resources/datasets/      datasets
resources/baselines/     baseline material
resources/benchmarks/    benchmarks, official evaluation, or protocols
resources/repos/         user-provided repositories or archives

external_executor/expr/  only deployed, directly runnable baseline or method assets
```

After automatic resource preparation finishes, stop the external executor and run `resume`; ResearchOS accepts the Phase B report and recompiles T5. When protocol and materials are ready, choose Codex CLI at the executor Gate, then start it from the **workspace root**:

```bash
cd workspace/project-a
codex
```

```text
Please read external_executor/AGENTS.md and execute external_executor/skills/research-execution/SKILL.md.
```

External execution must produce these four return files before it finishes:

```text
external_executor/executor_research_report.md
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

When Writer Handoff has finished but did not automatically enter T8, and the external executor has stopped writing, accept the verified result with `python -m researchos.cli run-task T8 --workspace ./workspace/project-a`. Do not run `resume`, a second executor, or `run-task T8` in another terminal while the external executor is writing. See the [T5 External Executor Guide](docs/en/t5_external_executor.md) for the full A-F phase contract and artifact paths.

Use `run --from <source-workspace> --start-task <task>` only to initialize a new target workspace from another project's validated upstream artifacts. `run-task <task> --from <source-workspace>` performs the same declared-input copy but executes only that task. For every literature-dependent downstream stage (`T3.5`, `T3.6`, `T4`, `T5`, and `T8`), the import closure includes the complete `literature/` tree, so initialized empty note directories cannot suppress real source paper cards. `bridge_notes/` remains the paper-note root; `cross_domain_catalogs/` is the independent retrieval catalog and is imported alongside it. The import happens before the model connection check, so a provider outage does not discard the prepared debugging workspace. Neither command is a state merge. The recovery guide is in [Quick Start](docs/en/QUICKSTART.md).

`resume --from-task <task>` is the deliberate same-workspace recovery command. It validates the target task's prerequisites, clears the old pending gate, and records the re-entry in `state.yaml`. T2/T3 are researcher-facing exceptions: re-entry restores the parameter or coverage decision first, and the coverage Gate can use a saved reading queue to repair missing historical summaries. Add `--from <source-workspace>` when an existing target needs missing declared inputs before it resumes; source state/history are never merged. Public names are accepted: `T3.6` means `T3.6-GATE-SURVEY`, the "write a Survey?" decision; `T8` means the writing-style Gate. After an optional Survey has finished, `resume --from-task T4` is the supported way to proceed without revisiting its post-Survey gate; it still refuses to run if T4 inputs are incomplete.

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
| T5 external execution and Codex/Claude handoff | [T5 External Executor Guide](docs/en/t5_external_executor.md) |
| Runtime, events, and extension points | [Runtime](docs/en/runtime.md) |
| Skills | [Skills](docs/en/skills.md) |
| Logs, traces, and debug procedure | [Logging and Troubleshooting](docs/en/logging.md) |
| Repository and workspace layout | [Project Structure](docs/en/project_structure.md) |
| Contributor workflow | [Development](docs/en/dev.md) |

Additional operational references: [repository/workspace layout](./docs/en/project_structure.md), [Compose deployment](./deploy/README.md), and [maintained scripts](./scripts/README.md).
