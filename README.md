# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS is an artifact-first research runtime for auditable literature work, evidence-bounded ideation, external-executor handoff, manuscript production, review, and submission packaging. A project lives in a workspace: durable files, not chat history, are the source of truth.

```text
T1 scope -> T2 discover -> T3 read -> T3.5 synthesize
  -> optional T3.6 survey -> T4 ideas -> T4.5 novelty
  -> T5 external execution -> T8 manuscript -> T9 submission bundle
```

## Start Here

Choose the path that matches the state of your work. Do not edit `state.yaml` to jump between stages, and do not run `run` again inside a directory that already has a `state.yaml`.

| Your situation | First command | What happens next | Do not do this |
| --- | --- | --- | --- |
| Start a research project from zero | `init-workspace`, then `run` | Creates an independent workspace and begins at T1. Research scope and literature-coverage Gates will ask for your decisions. | Do not create or edit `state.yaml` first. |
| Continue the same project after Ctrl+C, a Gate, or a transient service interruption | `resume --workspace <directory>` | Continues from durable state. At T2/T3, a light decision lets you keep the current scope or adjust it. | Do not call `run` again or write from a second terminal. |
| Deliberately revisit T2, T3, or T4 in the same project | `resume --workspace <directory> --from-task T2` | Re-enters the chosen research decision surface after prerequisite checks. T2 opens full parameter selection; T3 reviews retrieval coverage first. | Do not substitute `run-task` for a normal workflow restart. |
| Create a new project from another project's materials | `run --workspace <new-dir> --from <source-dir> --start-task T2` | Copies declared upstream material into a new workspace. The imported project gets its own T2/T3 parameter or coverage decision. | It does not merge the source `state.yaml`, history, or runtime logs. |
| Do one focused job, such as a PDF note card or a field synthesis | `browse-skills`, then `run-skill` | Creates a standalone, resumable Skill session with its own declared inputs, outputs, and recovery command. | Do not start pipeline-owned or T5 executor Skills with `run-skill`. |
| T5 is waiting for an external executor | Select the executor at the Gate, then follow `external_executor/AGENTS.md` | The executor works in the same workspace and returns auditable resources, code, results, figures, and evidence. | Do not run `resume`, a second executor, or T8 while that executor is writing. |

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
| T4.5 novelty audit | Check similar work and mechanism differences; turn the selected direction into a formal research package. | Review the novelty verdict and required baselines. | `ideation/proposal/research_proposal.md`, `ideation/hypotheses.md`, `ideation/exp_plan.yaml` |
| T5 external-execution preparation | Compile the T4.5 package into an executor handoff whose research constraints cannot be silently changed. | Resolve only settings that affect research boundaries; place existing resources or let the executor prepare public ones. | `external_executor/handoff_pack.json`, `resources/` |
| T8 writing | Write, review, and revise using verified experimental facts. | Choose a writing style or template. | `drafts/` and experiment claim/evidence files |
| T9 submission | Review, genuinely compile, and package the submission. | Only when an environment or compilation recovery is needed. | `submission/`, final PDF, and compile report |

The two most important boundaries are these: T4.5 turns an interesting idea into a research plan with hypotheses, an experiment plan, and risk boundaries. T5 is not an experiment runner: it prepares and verifies an execution contract; an external Codex, Claude, or human executor then performs the real work in the same workspace.

When T4.5 succeeds, the terminal displays a “key research files” table that points directly to the proposal, hypotheses, experiment plan, contribution/validation maps, stopping criteria, and novelty audit, with their next use. T5 consumes those files; you do not need to find them from memory.

When reviewing a T4.5 result yourself, read these files in this order. They are a plan and a falsification contract, not experimental findings.

| Read first | Why it matters | T5 use |
| --- | --- | --- |
| `ideation/proposal/research_proposal.md` | The complete research story: problem, mechanism, theoretical and practical implications, contribution, study design, risks, and limitations. | Preserves the research intent and boundaries behind the structured controls. |
| `ideation/hypotheses.md` | Falsifiable central and supporting hypotheses, assumptions, expected observations, and alternatives. | Prevents an executor from turning a hypothesis into a reported result. |
| `ideation/exp_plan.yaml` | Planned tasks, metrics, required baselines, datasets/benchmarks when known, and evaluation rules. | Becomes the core experimental constraint. |
| `ideation/contribution_hypothesis_map.yaml` and `ideation/validation_map.yaml` | Which contribution depends on which hypothesis and what evidence could validate or refute it. | Becomes the claim/evidence boundary for implementation and T8. |
| `ideation/kill_criteria.yaml` | Conditions that narrow, stop, or reject a claim. | Keeps negative results and invalidation paths visible. |
| `ideation/novelty_audit.md` | Similar-work collisions, mechanism distinctions, required baselines, and unresolved gaps. | Defines the comparison and novelty boundary; unresolved gaps remain visible. |

## The Workspace Is The Project Record

Each project runs in one independent workspace. Terminal scrollback, chat history, and model memory are not project facts: recovery, downstream stages, and audits read the durable files in this directory. The locations below are the ones a researcher most often needs to inspect or populate.

| Location | Who writes / reads it | When you should use it |
| --- | --- | --- |
| `project.yaml` | T1 writes; every stage reads | Inspect the research question, scope, venue, and initial constraints. Do not edit it to skip a stage. |
| `user_seeds/` | You provide; T1/T2/T3 read | Add seed PDFs, DOIs, ideas, and explicit constraints before or during early project setup. |
| `literature/` | T2/T3 and literature Skills write; T3.5-T5 read | Inspect search records, reading queues, deep/shallow notes, synthesis, and discovered resource leads. |
| `ideation/` | T4/T4.5 write; T5 reads | Inspect Candidate Cards, the proposal, formal hypotheses, experiment plan, novelty audit, and stopping criteria. |
| `resources/` | You or T5 Phase B add; external executor reads | Add source datasets, code, benchmarks, baselines, or weights you already possess. |
| `external_executor/` | T5 and the external executor write; T8 reads | Holds the handoff, specialized Skills, runnable code, raw results, figures, evidence package, and return report. |
| `drafts/` | T3.6/T8 write | Inspect Survey/manuscript drafts, writing handoffs, and result-to-claim mappings. |
| `submission/` | T9 writes | Inspect final compilation, review, and submission package. |
| `user_inputs/<skill>/` | A standalone Skill stages material you provide | Use only when `describe-skill` asks for material for that Skill. |
| `_runtime/` | ResearchOS writes | Stores state, events, traces, logs, Skill sessions, and recovery records. Read it for diagnosis; do not edit it during ordinary research. |

One workspace has one writer at a time. A second terminal may run read-only `status`, `trace`, or inspect files, but must not run another `run`, `resume`, `run-skill`, or external executor against the same workspace.

## Before You Run

- Use one writer per workspace. Do not run native and Docker commands against the same project concurrently.
- Store provider secrets in local `.env` or the ignored `config/model_settings.yaml`; never commit either file, workspaces, PDFs, runtime logs, or generated submissions.

### Model Connection And Context Capacity

`python -m researchos.cli configure-llm` creates the active local connection file at `config/model_settings.yaml`. For manual setup, copy `config/model_settings.example.yaml` to that exact output path, then set `provider`, `api_key`, and `model`; `openai_compatible` also requires `api_base`. The example itself is never read. Verify a manual edit with `python -m researchos.cli selftest`; a custom target can be checked with `python -m researchos.cli selftest --model-settings /absolute/path/model_settings.yaml`.

The same `config/model_settings.yaml` also contains `context_window_fallback: 262144` and `truncation`, so connection and context settings live together. The fallback is not a raw prompt-input limit: it is the total token capacity ResearchOS assumes only when a provider cannot report the model's real context window, shared by prompts, research materials, history, Tool I/O, and response space. A provider-reported real capacity always takes priority. See [Configuration](docs/en/config.md) for the exact fields and fallback semantics.
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
| Continue the same paused project | `resume --workspace <project-dir>` | Reuses confirmed choices and valid files. A T2/T3 "continue" choice does not repeat retrieval. |
| Intentionally restart at T2/T3/T4 | `resume --from-task T4` | Checks the selected stage's prerequisites before safe re-entry; T2/T3 first reopen their research-scope decision. |
| Start a new project with another project's material | `run --from <source-dir> --start-task T4` | Creates a separate target workspace and copies declared upstream inputs without merging histories. |
| Diagnose one stage only | `run-task T4` | Runs that task only and does not advance the main path, except that public T8 accepts a verified external-executor handoff. |
| Browse or run a focused capability | `browse-skills` / `run-skill` | Uses an independent, resumable Skill session. Read its input/output contract first. |

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

# Explicit T2 re-entry: choose full coverage parameters for this new pass.
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T2

# Create a separate target from another project's validated upstream files.
# Migration to T2 opens full parameter selection; migration to T3 reviews the queue and coverage.
python -m researchos.cli run --workspace ./workspace/project-b \
  --from ./workspace/project-a --start-task T2

python -m researchos.cli run --workspace ./workspace/project-c \
  --from ./workspace/project-a --start-task T3

# Fill only missing declared inputs from another workspace before re-entry.
python -m researchos.cli resume --workspace ./workspace/t4-debug \
  --from ./workspace/project-a --from-task T4
```

`resume --from-task` never merges histories or asks you to edit `state.yaml`. `T3.6` is the optional Survey decision; `T8` is the writing entry gate. Ordinary `resume` returning to T2/T3 first shows a light continue-or-adjust Gate; confirming continuation does not start a new search. Explicit T2 re-entry or `--from` migration opens the full parameter chooser, so a new search cannot silently use stale scope. Explicit T3 re-entry or migration opens the retrieval-coverage decision first; as long as the reading queue remains, missing historical summaries such as `search_log.md` do not suppress that choice.

| Entry | First decision when the target is T2 | First decision when the target is T3 | Are current papers and notes retained? |
| --- | --- | --- | --- |
| `resume --workspace <same-project>` | Parameter confirmation: continue or adjust | Coverage confirmation: continue, supplement retrieval, or adjust | Yes |
| `resume --from-task T2/T3` | Full parameter selection | Coverage review when a queue exists; parameter selection otherwise | Yes |
| `run --from <source> --start-task T2/T3` | Full parameter selection | Coverage review when a queue exists; parameter selection otherwise | Declared source material is copied into a new project; source remains unchanged |
| `resume --from <source> --from-task T2/T3` | Full parameter selection | Coverage review when a queue exists; parameter selection otherwise | Target remains; only missing declared inputs are added |

### 4. Skills: Focused, Resumable Workflows

Skills are not alternate names for T1-T9. A Skill is a separately started capability with a declared read/write boundary and persistent session, for example making a paper note card, comparing papers, synthesizing a field, or repairing evidence in a draft. The complete pipeline remains owned by `run` and `resume`. T5's project-specific executor Skills are owned by the state machine and external executor, so `run-skill` deliberately refuses to start them.

| Command | What to do first | Where the result lives | Use it when |
| --- | --- | --- | --- |
| `list-skills` | List only user-launchable standalone Skills. | Read-only terminal catalog. | You do not know the Skill name or available capabilities. |
| `browse-skills` | Browse by category or keyword and inspect a card. | Read-only terminal catalog. | You want to select a tool by research goal. |
| `describe-skill <name>` | Read purpose, required/optional inputs, permitted paths, outputs, examples, and recovery behavior. | Read-only terminal contract. | **Before first using any Skill.** |
| `run-skill <name> "request"` | Create or continue a session; interactively collect only missing material. | `user_inputs/<skill>/`, the Skill's declared output paths, and `_runtime/skill_sessions/<id>.json`. | The Skill contract matches the focused job. |
| `skill-status` | Inspect a session's phase state, checked files, and printed recovery command. | Read-only session state. | After interruption, while waiting for input, or to verify completion. |

```bash
# Discover capabilities and inspect the contract. list-skills excludes pipeline-owned Skills.
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a

# A session ID makes the work recoverable. Use distinct IDs for distinct papers.
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01 "Create a traceable reading card for this paper"

# Resume the same session after an interruption, a missing-input pause, or a confirmation pause.
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01 --resume

# Inspect the actual files and next action recorded for every session.
python -m researchos.cli skill-status --workspace ./workspace/project-a
```

The interactive lifecycle is: **request -> input check -> missing-material intake -> explicit Run confirmation -> declared outputs -> recoverable `skill-status` record**. If required input is missing, ResearchOS stages only the material you provide under `user_inputs/<skill>/` and records the session; it never fabricates a paper, citation, experiment, or completion status. `--non-interactive` is for scripts: missing inputs produce `WAITING_INPUT` without constructing a provider client. Add material, then resume with the same `--session-id ... --resume`.

Use `describe-skill` as the source of truth for each Skill's live paths and outputs. Typical starting points are:

| What you need | Start with | Typical input | Typical output and boundary |
| --- | --- | --- | --- |
| Read one paper with traceable evidence | `pdf-note-card` | A PDF, DOI, arXiv/OpenAlex ID, direct URL, or exact title. | `literature/skill_pdf_note_cards/`; it does not replace T3's canonical deep-read queue. |
| Compare methods, findings, and limitations across papers | `literature-comparison-studio` | A set of DOI/PDFs or exact paper titles. | Comparison matrix and evidence boundary; an abstract is not silently upgraded to full-text evidence. |
| Understand a field before choosing a research path | `domain-synthesis-studio` | Field question, scope, and intended use. | Domain report, mechanism/tension map, and next-step handoff; scoped retrieval requires authorization. |
| Build a review evidence package | `literature-review-studio` or `survey-evidence-package` | Review scope, language, period, and current corpus. | Taxonomy/coverage/Survey handoff; it does not substitute for formal T3.6 writing and TeX compilation. |
| Read a corpus and ask cross-paper questions | `paper-reading-workbench` | Paper identifiers/PDFs, questions, or a bounded topic. | Prioritized cards and cross-paper learning records; it does not mutate pipeline state. |
| Build or repair writing evidence | `related-work-builder` or `draft-evidence-repair` | Draft text, claims, citations, or existing notes. | Traceable writing package; it cannot invent citations or experimental results. |

For integrated workflows, source-resolution input, permission boundaries, and every available Skill, see [Skills](docs/en/skills.md).

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

`proposed_not_verified` is a claim-verification label, not a resource or T5 failure. It means the central hypothesis or contribution is intentionally proposed and still needs the external experiment to test it. Background evidence may be `source_supported`, while a discovered resource may be only a lead. These labels stay separate so a planned result is never written as an observed result.

| What you see at T5 | What you should choose or do | What ResearchOS does | Do not do this |
| --- | --- | --- | --- |
| `ready`, and you have local data/code/weights | Choose the local-material inventory. | Inventories `resources/` and already deployed `external_executor/expr/` assets, then returns to executor selection. | Do not put raw downloads into `expr/`. |
| `protocol_decision_required`, or you have no resources | Choose “let the external executor prepare resources.” | Runs bounded Phase A/B to discover public resources, lock revisions, review license/security/protocol fit, and record provenance. It does not run formal experiments. | You do not need to manually search public datasets, repositories, or benchmarks first. |
| You need a different task, mechanism, required baseline, benchmark scope, or claim boundary | Choose protocol recompilation or return to T4. | Preserves current material and makes the research-boundary change an explicit upstream decision. | Do not hand-edit the handoff or let an executor silently redefine the study. |
| Executor selection is visible | Choose Codex CLI, Claude Code, or a human executor. | Preserves the handoff, allowed-path policy, output schema, and specialized Skills. | Do not treat a mock/dry-run as experimental evidence. |
| “Waiting for external executor return” is visible | Finish the external execution in the same workspace. | The root executor Skill validates Writer Handoff, then automatically starts the ResearchOS T8 bridge. T8 independently verifies facts, hashes, the manifest, and claim boundaries before writing. | Do not run `resume`, another executor, or `run-task T8` while the executor writes. |

```bash
# Targeted T5 diagnostics only. These do not run an experiment.
python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a
# Canonical status-machine name. The short alias T5-SPECIALIZE is also accepted.
python -m researchos.cli run-task T5-SPECIALIZE-EXECUTOR-SKILLS --workspace ./workspace/project-a
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

External execution must produce this validated modern Writer Handoff package before it finishes. The first file is the reader-facing report; the remaining files bind it to the actual execution state, provenance, and structured facts:

```text
external_executor/executor_research_report.md
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
external_executor/report/phase_F/writer_handoff_facts.json
external_executor/report/phase_F/writer_handoff_validation.json
```

The `research-execution` root Skill normally runs `python -m researchos.cli run-task T8 --workspace <workspace>` itself immediately after Writer Handoff succeeds. ResearchOS then rejects mock/dry-run, `blocked`, `failed`, stale, hash-mismatched, or result-less handoffs; for an accepted handoff it creates `drafts/t5_t8_handoff.json`, `drafts/experiment_evidence_pack.json`, and `drafts/result_to_claim.json` before T8 begins writing. Only when the root Skill explicitly reports that this bridge command could not start, and the executor has stopped writing, should you run that command once manually. Do not use `resume` as a substitute for the bridge while an executor is writing. See the [T5 External Executor Guide](docs/en/t5_external_executor.md) for the full A-F phase contract and artifact paths.

### Advanced Migration And Recovery Guarantees

The recovery matrix above is the normal decision guide. The following rules explain the less common cases without changing that guide:

- `run --from <source-workspace> --start-task <task>` initializes a **new** target workspace from another project's declared, validated upstream artifacts. It never merges state or history. `run-task <task> --from <source-workspace>` copies the same declared inputs but runs only that one diagnostic task.
- For literature-dependent downstream stages (`T3.5`, `T3.6`, `T4`, `T5`, `T8`), the import closure contains the complete `literature/` tree. Empty imported directories therefore cannot hide real note cards; `bridge_notes/` and `cross_domain_catalogs/` are both retained when declared.
- Import happens before the model connection check, so a provider outage does not discard a prepared diagnostic workspace. `resume --from <source>` adds only missing declared inputs to an existing target; it also never merges source state/history.
- A workspace with `state.yaml` must use `resume`; a directory without it must use `run`; a `COMPLETED` workspace is intentionally not resumed because its artifacts must not be silently rewritten.
- Public aliases are accepted: `T3.6` opens the Survey decision, `T5-SPECIALIZE` resolves to `T5-SPECIALIZE-EXECUTOR-SKILLS`, and `T8` opens the writing-style Gate. After a finished optional Survey, `resume --from-task T4` proceeds to Idea work only if T4 prerequisites still pass.
- For an interrupted Survey section, validate the section before resuming. A valid `T3.6-SEC-*` output advances without rewriting its completed `.tex` file or its matching survey-state entry.

See [Quick Start](docs/en/QUICKSTART.md) for recovery examples and [Logging and Troubleshooting](docs/en/logging.md) for trace-based diagnosis.

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

`read_file` is context-aware, not a fixed 200/3,000-character reader. Before building context-sensitive tools, ResearchOS queries direct and collection model metadata on compatible base paths, including both `/v1` and non-`/v1` OpenAI-style deployments. It uses a matched `context_length`, `context_window`, `max_context`, `max_input_tokens`, or compatible nested capacity field when the provider exposes one. The result is cached for the active run and is shared by file reads, history truncation, and abstract batching. If a relay does not publish verifiable capacity metadata, the system uses the `262144`-token `context_window_fallback` in the same `config/model_settings.yaml` file. It is a total context-capacity fallback, not a raw prompt-input limit or a claim about the provider's public API limit. Per-task context overrides are ignored in the one-model configuration, so user-facing behavior follows the configured provider rather than an old Agent tier.

The effective capacity reserves room for the system prompt/history/tool calls and allocates 70% of the remainder to a file result. It reads the whole file only when it fits the automatic full-read share, then uses a context-sized page. The public `read_file` schema deliberately exposes only `path` and `offset`; it does not accept a manual `max_chars` budget, so a model cannot issue `max_chars=200` and force repeated tiny reads. For a known local section, use `grep_search` to find the offset and call `read_file` again with that offset. Result metadata records the applied capacity source and page budget. For a large T2 `literature/papers_raw.jsonl`, reading remains available, but checkpoint-safe pages begin before the raw pool can consume the working retrieval plan, preserve JSONL record boundaries, and carry completed queries, source coverage, raw count, and the authoritative `next_offset`.

## Advanced Skill Boundaries And Integrated Workflows

The [Skills](docs/en/skills.md) guide is the complete contract reference. This section records the advanced behavior that matters when choosing an integrated workflow or interpreting its evidence. Atomic Skills cover paper intake, DOI/title resolution, note cards, evidence matrices, ideation, writing, review, polishing, compilation, and submission checks. Integrated Skills add explicit subphases, evidence gates, artifact manifests, and durable recovery for multi-step work:

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

An integrated session records `pending`, `running`, `completed`, `waiting_input`, `waiting_evidence`, or `skipped` for each declared phase in the same session file. It can use source-returning search only after the researcher authorizes the scope. Search leads, metadata, and abstracts remain weaker than section-level or full-text evidence, and cannot by themselves upgrade a scholarly mechanism or result claim.

For `pdf-note-card`, `paper-comparison`, and `literature-comparison-studio`, guided intake accepts an uploaded PDF, DOI, arXiv/OpenAlex ID, direct PDF URL, exact title, or an explicitly scoped topic-plus-count request. It records the identifier/query and retrieval outcome in `user_inputs/<skill>/_source_resolution.md`, downloads only into that Skill's declared input area, and leaves metadata/search hits visibly weaker than a read PDF. The catalog validates declared input/output paths against workspace permissions before public display, so a listed standalone Skill cannot later advertise a path that becomes `access_denied`.

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
