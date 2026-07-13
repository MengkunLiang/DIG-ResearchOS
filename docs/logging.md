# Observability, Logs, And Debugging

ResearchOS separates researcher-facing process information from machine-level
diagnostics. The terminal is designed to explain inputs, calculations,
decisions, output artifacts, and blockers without exposing private model
reasoning or raw prompt payloads.

## Console

Every CLI command starts with the DIG · BUAA / ResearchOS panel unless
`--no-banner` or `--quiet` is supplied. Interactive terminals use color and the
three-frame `D -> DI -> DIG` mark; non-TTY output is a single portable panel.

Formal stages use the same flow:

1. **Stage Start** — goal, research question, input artifact table, validation,
   planned operations, expected outputs, possible branches/gates.
2. **Stage Progress** — bounded statistics, Top-N, distributions, decisions,
   failures, backlog, and unsupported evidence; never full tool payloads.
3. **Stage Summary** — conclusions, risks, artifact manifest, downstream use,
   and required human action.

Use:

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity concise
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --no-color
```

`concise` still reports inputs, outputs, and human actions. `normal` is the
default. `detailed` adds bounded per-query, per-paper, per-bridge, or
per-candidate information. `--json-events` mirrors sanitized event JSON to
stdout and should not be mixed with a human gate session.

## Files Written Per Run

```text
<workspace>/_runtime/
├── logs/researchos.log           Human-readable operational timeline
├── logs/researchos-debug.log     Lower-level debug logging
├── traces/<run-id>.jsonl         Machine trace with messages/tool payloads
├── events/<run-id>.jsonl         Sanitized researcher-facing event stream
└── skill_sessions/<id>.json      Guided Skill state and resume information
```

Events are written even without `--json-events`. `trace` is sensitive
operational data and should not be posted publicly without review.

Integrated Skill sessions add a `workflow` object in the same session file. It
contains phase labels, current phase, visible status, summary, artifact paths,
evidence boundary, and next action. It intentionally excludes prompts, private
reasoning, and full tool payloads. `skill-status` renders this phase state;
inspect the session JSON only when a recovery diagnosis needs the durable detail.

## First Debugging Commands

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a
python -m researchos.cli trace <run-id> --workspace ./workspace/project-a
tail -n 120 ./workspace/project-a/_runtime/logs/researchos.log
```

## Interpret Common States

| Console state | Meaning | Correct action |
| --- | --- | --- |
| `WAITING_INPUT` | A Skill needs human material | Read `user_inputs/<skill>/_intake.md` or `_followup_request.md`; add/answer it and resume the same session. |
| `WAITING_CONFIRMATION` | Inputs are ready but Skill execution is not authorized | Explicitly choose `执行` or `暂停`. |
| `WAITING_ENVIRONMENT` | TeX, Python package, Docker, or provider prerequisite is absent | Run `doctor`/preflight; repair the named environment item; resume. |
| `DEGRADED` | A non-blocking source/tool failed while alternatives continue | Read the stage summary and source-health table; do not assume zero coverage. |
| `unsupported` | Evidence cannot support the requested conclusion | Add the named evidence, weaken the claim, or choose another direction. |
| `waiting_evidence` (Skill phase) | An integrated workflow has a known evidence gap after preflight | Authorize scoped retrieval, upload the named source, or choose a narrower/weakly worded output. |
| Validation failure | Artifact exists but breaks its declared contract | Run `validate --task`, repair the named file/state, then resume. |

## T3.6 Example: Section Validation

Survey section validation checks a writing contract, citations, language,
section ownership, and persisted state. It is not a request for a magic literal
phrase. If an Introduction is rejected, inspect the named artifact and state:

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

`build_survey_state` is idempotent for the same survey plan: it preserves
completed sections whose file and outline fingerprints remain valid. A changed
plan or writing contract deliberately returns affected sections to pending.

For `T3.6-SEC-*`, `resume` first validates the one declared section and its
matching `survey_state` entry. When both are valid, the console reports that
the section is being advanced without another provider rewrite. A section task
cannot write sibling sections, outlines, survey assembly files, figures, or
compile outputs; an attempted cross-section mutation is an explicit access
error rather than a hidden state change.

## What Not To Infer From Logs

- A retrieval coverage gap is not a research gap.
- A citation-graph or ranking signal is not a final scientific judgment.
- A Tool success only means the tool completed; stage validation decides whether
  its artifact is usable.
- A model's natural-language plan is not an experiment protocol without
  traceable current-project inputs.
