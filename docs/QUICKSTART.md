# Quick Start And Recovery

This guide is a copyable operational path. Read the root README first for the
native/Docker choice and installation prerequisites.

## 1. Preflight

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

`doctor` is required before T3.6/T9 PDF work. It reports the actual native or
Docker TeX path, not merely whether Python can import a package.

## 2. Create And Start

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "your research topic"

python -m researchos.cli run --workspace ./workspace/project-a
```

The terminal first shows the DIG Lab · BUAA / ResearchOS panel, then a Stage
Start panel. At each gate, read the inputs, decision table, artifact paths, and
risk/unsupported notes before answering. `--no-banner` is only for scripts;
`--no-color` is for ANSI-free output.

## 3. T2 Literature Parameters

At the T2 gate, select a profile or enter one sentence. Unspecified fields keep
the recommended profile.

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

The confirmation panel writes `literature/literature_params.json`. English
manuscript language does not by itself exclude Chinese literature; explicitly
state the inclusion policy when that matters.

## 4. Survey Branch

T3.6 is optional. If selected, the sequence is taxonomy plan -> outline/corpus
gate -> survey state -> one deterministic taxonomy figure -> sections ->
assembly/review -> real TeX compilation.

The only permitted survey figure is:

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

It uses only explicit taxonomy labels and direct resolved note-card links. It
does not compare performance, baselines, retrieved relevance scores, or inferred
risk. The renderer prefers Times New Roman and selects a documented academic
serif fallback when it is unavailable.

If a section validator pauses, inspect it before changing prose:

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

If the file is valid, `resume` continues. Do not repeatedly add validation
retries for the same deterministic error.

## 5. Resume A Paused Project

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
```

Typical pause handling:

| Pause | Fix | Continue |
| --- | --- | --- |
| Human gate | Read the terminal decision surface and choose | `resume` is automatic after input or rerun `resume` |
| Missing material for a Skill | Add/answer the requested `user_inputs/<skill>/...` file | `run-skill ... --session-id <id> --resume` |
| Provider failure | Fix `.env`, endpoint, model route, or wait for service | `resume` |
| TeX environment | Run `doctor`, install host TeX or build Docker image | `resume` |
| Validation error | Run `validate --task <task>`, repair the named artifact | `resume` |
| External executor wait | Write the declared executor result pack | `resume` |

## 6. Debug One Stage

`run-task` does not advance the entire pipeline:

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/project-a
python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a
python -m researchos.cli run-task T9 --workspace ./workspace/project-a
```

Use `validate` after an artifact repair. Use `trace <run-id>` for the bounded
human rendering of a prior run and inspect `_runtime/logs/researchos.log` for
the detailed operational timeline.

## 7. Start A Guided Skill

```bash
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a
```

TTY intake collects material and asks for explicit execution. Automation must
be explicit:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

In non-interactive mode, absent material produces `WAITING_INPUT` and no LLM
provider is initialized. See [skills.md](skills.md).

## 8. Reuse Another Project Carefully

Create a new target workspace and reference the source only through the
supported initialization path:

```bash
python -m researchos.cli run \
  --workspace ./workspace/project-b \
  --from ./workspace/project-a \
  --start-task T4
```

The target retains its own state, gates, logs, and output artifacts. Confirm
provenance before reusing literature, claims, or protocol details.
