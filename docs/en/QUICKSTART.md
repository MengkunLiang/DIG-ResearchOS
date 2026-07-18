# Quick Start And Recovery

> [English](../en/QUICKSTART.md) | [中文](../cn/QUICKSTART.md)

This guide is a copyable operational path. Read the root README first for the native/Docker choice and installation prerequisites.

## 1. Preflight

```bash
python -m researchos.cli configure-llm
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

`doctor` is required before T3.6/T9 PDF work. It reports the actual native or Docker TeX path, not merely whether Python can import a package.

## 2. Create And Start

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "your research topic"

python -m researchos.cli run --workspace ./workspace/project-a
```

The terminal first shows the DIG · BUAA / ResearchOS panel, then a Stage Start panel. At each gate, read the inputs, decision table, artifact paths, and risk/unsupported notes before answering. `--no-banner` is only for scripts; `--no-color` is for ANSI-free output.

## 3. T2 Literature Parameters

At the T2 gate, select a profile or enter one sentence. Unspecified fields keep the recommended profile.

```text
30 candidate papers, 15 deep reads, 15 lightweight abstract reads; English manuscript; exclude Chinese-language retrieval.
```

The confirmation panel writes `literature/literature_params.json`. English manuscript language does not by itself exclude Chinese literature; explicitly state the inclusion policy when that matters.

## 4. Survey Branch

T3.6 is optional. The gate after T3.5 first asks whether to skip Survey, write with the current corpus, or request one targeted supplement before Survey planning. The preference is persisted in `drafts/survey/decision.json`; it is not a claim that newly retrieved records are ready for prose. If selected, the sequence is taxonomy plan -> outline/corpus gate -> optional targeted expansion plan -> survey state -> one deterministic taxonomy figure -> sections -> assembly/review -> real TeX compilation.

The only permitted survey figure is:

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

It uses only explicit taxonomy labels and direct resolved note-card links. It does not compare performance, baselines, retrieved relevance scores, or inferred risk. The renderer prefers Times New Roman and selects a documented academic serif fallback when it is unavailable.

If a section validator pauses, inspect it before changing prose:

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

If the file is valid, `resume` continues. Do not repeatedly add validation retries for the same deterministic error.

## 5. Resume A Paused Project

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
python -m researchos.cli workspace-status --workspace-root ./workspace
```

`workspace-status` combines durable `state.yaml`, recent `_runtime/events/*.jsonl`, and an advisory local-process match in one Rich table. The default scan keeps only workspace, task, state, activity, event age, and gate; add `--verbose` for the final error/event detail. Only “local execution” means that this host still has a non-stopped ResearchOS process. A `RUNNING` state paired with “stopped/suspended” is not active work: inspect the workspace and terminal job before choosing `resume`. Process information is advisory; the state file and durable events remain the recovery source of truth.

`status` shows a compact project summary by default: current step, state, pending decision, latest actionable message, and the next command. Use `status --detail` only when you need the complete raw `state.yaml` for debugging.

### Safe interruption

Press `Ctrl+C` once to pause a running project. ResearchOS stops the current command, marks `state.yaml` as `PAUSED`, and prints a copyable `resume` command; persisted papers, notes, and stage artifacts remain in place. The same path applies while the terminal is waiting for a provider or for user input. A second `Ctrl+C` requests an immediate exit and should be used only when you do not need to wait for cleanup.

Do not use `Ctrl+Z` to end a project. It only suspends the shell job: the process remains in the job list and has neither exited normally nor established a safe project pause. After an accidental `Ctrl+Z`, run `fg` in that terminal and press `Ctrl+C` once, or inspect `state.yaml` and `workspace-status` before handling the suspended job.

## Command Index

| Command | Use | Common form |
| --- | --- | --- |
| `init-workspace` | Create a project workspace and baseline inputs | `init-workspace --workspace <dir> --project-id <id> --topic <topic>` |
| `run` | Run the full pipeline; optionally reuse verified prerequisites from another project | `run --workspace <dir>`; `run --workspace <new> --from <source> --start-task T4` |
| `run_smoke` | Run a real-tool smoke workflow | `run_smoke --workspace <dir>` |
| `resume` | Continue a paused project | `resume --workspace <dir>`; use `--from-task <task>` for deliberate same-workspace reentry |
| `run-task` | Diagnose or execute one task without advancing the main pipeline; public `T8` is the deliberate exception that accepts the external handoff and runs the complete T8 chain | `run-task T4 --workspace <dir>`; `run-task T8 --workspace <dir>` |
| `status` / `workspace-status` | Inspect one project or a workspace root; `status --detail` prints raw state | `status --workspace <dir>`; `workspace-status --workspace-root ./workspace` |
| `configure-llm` / `selftest` | Configure and check the provider/model connection shared by every stage | `configure-llm`; `selftest` |
| `doctor` | Check local/Docker/TeX dependencies | `doctor --workspace <dir>` |
| `trace` / `validate` | Inspect a bounded run summary, validate prerequisites, or validate stored task results | `trace <run-id> --workspace <dir>`; `validate --task T4 --scope inputs --workspace <dir>`; `validate --task T4 --scope outputs --workspace <dir>` |
| `audit-survey` | Rebuild the deterministic Survey coverage audit | `audit-survey --workspace <dir>` |
| `validate-config` | Check state-machine, gate, routing, and runtime configuration | `validate-config` |
| `run-task T5-SPECIALIZE-EXECUTOR-SKILLS` | Run only the LLM-backed repository Skill that publishes and validates the project-specific T5 executor Skill suite | `run-task T5-SPECIALIZE-EXECUTOR-SKILLS --workspace <dir>` |
| `specialize-executor-skills` | Offline deterministic preview, repair, or validation of the same suite | `specialize-executor-skills --workspace <dir> --deterministic` |
| `list-skills` / `browse-skills` / `describe-skill` | Discover Skills and inspect their contracts | `describe-skill <skill> --workspace <dir>` |
| `run-skill` / `skill-status` | Start/resume an independent Skill session or inspect sessions | `run-skill <skill> --workspace <dir> --session-id <id> --resume` |

Typical pause handling:

| Pause | Fix | Continue |
| --- | --- | --- |
| Human gate | Read the terminal decision surface and choose | `resume` is automatic after input or rerun `resume` |
| Missing material for a Skill | Add/answer the requested `user_inputs/<skill>/...` file | `run-skill ... --session-id <id> --resume` |
| Provider failure | Check `model_settings.yaml` or `.env`, then wait for service if the connection is valid | `resume` |
| TeX environment | Run `doctor`, install host TeX or build Docker image | `resume` |
| Validation error | Run `validate --task <task> --scope inputs` for missing prerequisites or `--scope outputs` for generated artifacts, then repair the named file or contract | `resume` |
| External executor wait | Write the declared executor result pack and the core T8 handoff report `external_executor/executor_research_report.md` | `resume` |

## 6. Debug One Stage

`run-task` does not advance the entire pipeline:

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/project-a
python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a
python -m researchos.cli run-task T5-SPECIALIZE-EXECUTOR-SKILLS --workspace ./workspace/project-a
python -m researchos.cli run-task T9 --workspace ./workspace/project-a
```

Use `validate` after an artifact repair. Use `trace <run-id>` for the bounded human rendering of a prior run and inspect `_runtime/logs/researchos.log` for the detailed operational timeline.

For T4, the model authors Candidate framing, mechanisms, 2–4 Draft Hypotheses, contributions, score explanations, and researcher-facing Portfolio prose from workspace context plus clearly labelled conjectural scholarly knowledge or structural analogy. Standard mode completes a full `P0 -> P1` Evolution Round, not a single rewrite. Evidence certifies claims; it does not limit the model to paraphrasing the Evidence Bundle. Rich panels show `Research Opportunity Mapping (Opportunity Map)`, multi-perspective Idea divergence, Independent Scoring, Evolution Planning, Offspring & Rescoring, and Survival & Portfolio without raw JSON or hidden reasoning. During a provider call, the terminal distinguishes the current activity, its current deliverable, and the following phase rather than repeating Opportunity Map as both the current work and “next step”; it emits a low-frequency Live Runtime panel after 12 seconds and then every 30 seconds.

## 7. T5 Executor Skills And Recovery

T5 now separates semantic handoff compilation from executor Skill publication:

```text
T5-REBOOST-GATE -> T5-SPECIALIZE-EXECUTOR-SKILLS -> T5-EXECUTOR-GATE
```

`T5-SPECIALIZE-EXECUTOR-SKILLS` is the formal LLM-backed task:

```text
ResearchOS Task
-> LLM consumes skills/project-skill-specialization
-> Skill calls the deterministic Project Skill Specializer wrapper
-> ResearchOS independently validates the durable artifacts
```

A valid specialization writes `external_executor/project_skill_context.yaml`, `external_executor/schemas/project_skill_context.schema.json`, `external_executor/report/skill_specialization_report.json`, all 13 complete `external_executor/skills/<skill>/` directories with their project-specific `SKILL.md`, and `external_executor/report/skill_specialization_execution.json` before executor selection. `ready` and `incomplete` both allow the executor gate; `failed` stops.

To run only the T5 reboost module without advancing the full pipeline:

```bash
python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a
```

`T5-REBOOST` publishes the semantic handoff and files needed by the next steps at stable root paths: `external_executor/handoff_pack.json`, `paper_card_evidence_index.json`, `expected_outputs_schema.json`, `allowed_paths.txt`, `AGENTS.md`, and `CLAUDE.md`. Its process reports are written under `external_executor/report/`: `reboost_report.json`, `reboost_validation_report.json`, and, when the model supplies a handoff candidate, `reboost_llm_candidate_handoff_pack.json` plus `reboost_llm_candidate_validation_report.json`. `external_executor/expr/` is created by workspace initialization and is used later for material placement and deployed method/baseline assets; T5-REBOOST does not create `expr/MATERIALS_CHECKLIST.json` or `expr/README.md`. `T5-EXECUTOR-GATE` writes executor control receipts under `external_executor/report/`; executor-specific prompt files are no longer generated.

To run only the project Skill specialization task without advancing the full pipeline:

```bash
python -m researchos.cli run-task T5-SPECIALIZE-EXECUTOR-SKILLS \
  --workspace <workspace>
```

After specialization completes, run the executor-selection gate against the same workspace:

```bash
python -m researchos.cli run-task T5-EXECUTOR-GATE \
  --workspace <workspace>
```

After a real Codex/Claude/manual executor finishes, the `research-execution` root routes `launch-t8` and runs the following command in the same executor session. There is no need to exit the executor and manually run `resume`:

```bash
python -m researchos.cli run-task T8 --workspace <workspace>
```

The command independently validates the modern Writer Handoff, treats `external_executor/executor_research_report.md` as the primary T8 research-fact input, and derives `drafts/t5_t8_handoff.json`, `drafts/experiment_evidence_pack.json`, and `drafts/result_to_claim.json`. It then safely enters or resumes the complete T8 state-machine chain. `result_pack.json`, `report/run_manifest.json`, `raw_results/`, `evidence_package/`, `figure/`, `table/`, and `expr/` remain traceable supporting inputs rather than replacements for the report. Concrete node names such as `run-task T8-RESOURCE` retain their isolated single-task behavior.

For a workspace created by an older release that is already paused in `T5-EXTERNAL-WAIT` without `external_executor/skills/`, the offline deterministic command can repair or validate the suite without calling a model:

```bash
python -m researchos.cli specialize-executor-skills \
  --workspace ./workspace/project-a --deterministic

python -m researchos.cli validate --task T5-EXECUTOR-GATE \
  --workspace ./workspace/project-a

# Validate the published context, report, and 13 Skills without calling an LLM.
python -m researchos.cli specialize-executor-skills \
  --workspace ./workspace/project-a --validate-only
```

To rebuild both the reboost handoff and the suite from the normal pipeline entry, use the supported state reentry command rather than editing `state.yaml`:

```bash
python -m researchos.cli resume --workspace ./workspace/project-a \
  --from-task T5-REBOOST-GATE
```

## 8. Start A Guided Skill

```bash
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a
```

TTY intake collects material and asks for explicit execution. Automation must be explicit:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

In non-interactive mode, absent material produces `WAITING_INPUT` and no LLM provider is initialized. See [skills.md](skills.md).

For a complete field or review workflow, use an integrated Skill. It asks first whether it may search for missing literature, records visible subphases in the same session, and asks again before entering Survey preparation or hypothesis selection:

```bash
python -m researchos.cli run-skill domain-synthesis-studio \
  "Synthesize this field; inspect the corpus first, ask before scoped retrieval if it is insufficient, then decide whether to prepare a survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli skill-status --workspace ./workspace/project-a
```

The `skill-status` panel shows the active integrated phase, completed artifacts, evidence boundary, and the exact same-session resume command.

## 9. Reuse Another Project Carefully

Create a new target workspace and reference the source only through the supported initialization path:

```bash
python -m researchos.cli run \
  --workspace ./workspace/project-b \
  --from ./workspace/project-a \
  --start-task T4
```

The target retains its own state, gates, logs, and output artifacts. Confirm provenance before reusing literature, claims, or protocol details.

`run-task T4 --workspace <new> --from <source>` can also copy T4's declared inputs from another project, but it runs T4 only and never advances the complete pipeline. For a debug workspace that already has `state.yaml`, use `resume --workspace <target> --from <source> --from-task T4`: it merges missing T4 inputs first and then resumes the complete pipeline. Copying happens before the model connection check, so a temporary provider failure still leaves the imported target workspace available. For every literature-dependent downstream stage (`T3.5`, `T3.6`, `T4`, `T5`, and `T8`), import includes the complete `literature/` artifact tree rather than only the first sub-node's narrow input list. This transfers real paper cards, queues, synthesis, BibTeX, and the independent Cross-domain catalog even when the target already has empty standard directories. In a `resume --from` import, existing target files are preserved. The source workspace is never changed.

`literature/bridge_notes/` has **not** been renamed: it remains the canonical root for actual full/partial Bridge paper notes. `literature/cross_domain_catalogs/` is the separate B1/B2 retrieval-and-metadata catalog. Historic catalog JSON colocated under `bridge_notes/` is copied non-destructively into the catalog root for compatibility; it never replaces or removes a paper note.

`resume --from-task T3.6` is the public alias for the "write a Survey?" entry Gate, `T3.6-GATE-SURVEY`. With `--from <source>`, it imports the complete source literature tree before entering the Gate, so later PLAN/VISUALS nodes receive the same paper corpus rather than initialized empty note directories. T3.6 PLAN and VISUALS reject an empty required paper-note root before a model request is submitted.

## 10. Note-Card Selection And Revisit

T2 selection is not deletion. Identity-verified records remain in `papers_verified.jsonl`, `papers_backlog.jsonl`, and `deep_read_queue.jsonl`; `triaged_out` only means a record does not consume the current deep-read target. T3 prioritizes the pending queue and may use readable backlog material in its abstract sweep without upgrading it to full-text evidence.

T4 indexes the available mainline and Bridge paper-reading notes before it forms an Opportunity Map. Full/partial notes can anchor a bounded mechanism or design rationale; abstract-level notes still contribute to recall, taxonomy, Bridge discovery, candidate mechanisms, and reading-upgrade requests, but cannot establish a mechanism or strong Claim. The controller creates route-specific Evidence Bundles rather than giving every route the same long context. It preserves source paths, reading level, uncertainty, and upgrade requirements in `ideation/evidence/`; no unselected note is deleted. A verified/backlog record without a note remains a metadata/abstract-level lead until it is read at the appropriate depth.
