# Observability, Logs, And Troubleshooting

> [English](../en/logging.md) | [中文](../cn/logging.md)

ResearchOS separates researcher-facing process information from machine-level diagnostics. The terminal is designed to explain inputs, calculations, decisions, output artifacts, and blockers without exposing private model reasoning or raw prompt payloads.

## Console

Every CLI command starts with the DIG · BUAA / ResearchOS panel unless `--no-banner` or `--quiet` is supplied. Interactive terminals use color and the three-frame `D -> DI -> DIG` mark; non-TTY output is a single portable panel.

Formal stages use the same flow:

1. **Stage Start** — goal, research question, input artifact table, validation, planned operations, expected outputs, possible branches/gates.
2. **Stage Progress** — bounded statistics, Top-N, distributions, decisions, failures, backlog, and unsupported evidence; never full tool payloads.
3. **Stage Summary** — conclusions, risks, artifact manifest, downstream use, and required human action.

Use:

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity concise
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --no-color
```

`concise` still reports inputs, outputs, and human actions. `normal` is the default. `detailed` adds bounded per-query, per-paper, per-bridge, or per-candidate information. `--json-events` mirrors sanitized event JSON to stdout and should not be mixed with a human gate session.

## Files Written Per Run

```text
<workspace>/_runtime/
├── logs/researchos.log           Human-readable operational timeline
├── logs/researchos-debug.log     Lower-level debug logging
├── traces/<run-id>.jsonl         Machine trace with messages/tool payloads
├── events/<run-id>.jsonl         Sanitized researcher-facing event stream
└── skill_sessions/<id>.json      Guided Skill state and resume information
```

Events are written even without `--json-events`. `trace` is sensitive operational data and should not be posted publicly without review.

Integrated Skill sessions add a `workflow` object in the same session file. It contains phase labels, current phase, visible status, summary, artifact paths, evidence boundary, and next action. It intentionally excludes prompts, private reasoning, and full tool payloads. `skill-status` renders this phase state; inspect the session JSON only when a recovery diagnosis needs the durable detail.

## First Debugging Commands

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli validate --task T3.6-SEC-INTRO --scope outputs --workspace ./workspace/project-a
python -m researchos.cli validate --task T3.6-VISUALS --scope inputs --workspace ./workspace/project-a
python -m researchos.cli trace <run-id> --workspace ./workspace/project-a
tail -n 120 ./workspace/project-a/_runtime/logs/researchos.log
```

## Interpret Common States

| Console state | Meaning | Correct action |
| --- | --- | --- |
| `WAITING_INPUT` | A Skill needs human material | Read `user_inputs/<skill>/_intake.md` or `_followup_request.md`; add/answer it and resume the same session. |
| `WAITING_CONFIRMATION` | Inputs are ready but Skill execution is not authorized | Explicitly choose Run or Pause. |
| `WAITING_ENVIRONMENT` | TeX, Python package, Docker, or provider prerequisite is absent | Run `doctor`/preflight; repair the named environment item; resume. |
| `DEGRADED` | A non-blocking source/tool failed while alternatives continue | Read the stage summary and source-health table; do not assume zero coverage. |
| `unsupported` | Evidence cannot support the requested conclusion | Add the named evidence, weaken the claim, or choose another direction. |
| `waiting_evidence` (Skill phase) | An integrated workflow has a known evidence gap after preflight | Authorize scoped retrieval, upload the named source, or choose a narrower/weakly worded output. |
| Validation failure | A prerequisite contract is not ready, or an artifact exists but breaks its declared contract | Run `validate --task <task> --scope inputs` for prerequisites or `--scope outputs` for generated artifacts, repair the named file/state, then resume. |

## T3.6 Example: Section Validation

Survey section validation checks a writing contract, citations, language, section ownership, and persisted state. It is not a request for a magic literal phrase. If an Introduction is rejected, inspect the named artifact and state:

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

`build_survey_state` is idempotent for the same survey plan: it preserves completed sections whose file and outline fingerprints remain valid. A changed plan or writing contract deliberately returns affected sections to pending.

For `T3.6-SEC-*`, `resume` first validates the one declared section and its matching `survey_state` entry. When both are valid, the console reports that the section is being advanced without another provider rewrite. A section task cannot write sibling sections, outlines, survey assembly files, figures, or compile outputs; an attempted cross-section mutation is an explicit access error rather than a hidden state change.

## T3.6 Assembly And Survey Audit

`validate --task T3.6-ASSEMBLE` checks the currently stored assembly manifest and audit. After correcting a cited source, BibTeX entry, section file, plan, or state file, use the deterministic audit command before a provider-backed resume:

```bash
python -m researchos.cli audit-survey \
  --workspace ./workspace/project-a

python -m researchos.cli validate \
  --task T3.6-ASSEMBLE \
  --workspace ./workspace/project-a
```

`audit-survey` writes `drafts/survey/survey_audit.json` and `.md`, reports only blocking failed checks, and does not call an LLM. It separates three common cases:

| Failure | Meaning | Repair scope |
| --- | --- | --- |
| `citation_diversity` | Citation use is genuinely concentrated or too few distinct keys are cited. The repeat cap scales with total citation occurrences, so a long survey is not rejected merely because a foundational paper exceeds a fixed count. | Add relevant existing citations or remove redundant citations only where claim support permits. |
| `bibliography_quality` | A cited BibTeX record is malformed, has a placeholder, or has the wrong journal/conference type. | Repair `literature/related_work.bib`, then reassemble once so fingerprints update. |
| `citation_claim_alignment` or section/depth checks | The cited paragraph or named section does not satisfy its actual contract. | Modify only the implicated section and its evidence anchors; do not rewrite unrelated Survey sections. |
| `survey_graphics_layout` | A double-column CCF template contains a normal `figure` whose image uses an unsafe `\\textwidth` width. It may compile but overlap the second text column. | Use `figure*` for the full-width taxonomy visual, or change an ordinary figure to `\\columnwidth`/`\\linewidth`; reassemble, audit, and compile. |

The assemble Agent receives the failed-check detail directly. It must not call `assemble_survey` repeatedly without changing an input relevant to that detail. If the needed evidence is unavailable, it writes a repair plan and pauses rather than hiding the blocker behind retries.

Normal `latex_compile` calls do not rewrite an audited TeX source. The optional wide-table `resizebox` transform is explicit opt-in because it is a source edit: use it only in a deliberate writing-repair step, then reassemble and re-audit before treating the result as current.

## T3.6 Review And Compile Recovery

`T3.6-REVIEW` checks presentation and scholarly structure after assembly. It can patch a named section, reassemble, re-audit, write the review/action files, and bind current input fingerprints. It must not directly overwrite `survey.tex`: the document is derived from the section files, template, state, and bibliography. If a review detects a title or template defect, rebuild with `assemble_survey(title=...)`, record the action, and make the title source durable before a later assembly.

After any review-driven assembly, the old PDF/report is intentionally stale. The deterministic compile validator reports that directly, for example `survey_compile_report.pdf_mtime is older than the current survey.tex`. This is not an audit loop: run the real `latex_compile` stage once, then validate `T3.6-COMPILE` again. Never hand-edit `survey_compile_report.json` or reuse a prior PDF hash.

## T4 Structured Write Failures

T4 writes `ideation/idea_rationales.json` and `ideation/idea_scorecard.yaml` through `write_structured_file`. A failure means the object was rejected before disk write, not that the runtime lost the file. The progress result now includes schema, target artifact, and precise paths.

| Diagnostic pattern | Meaning | Correct repair |
| --- | --- | --- |
| `$.ideas[n].counterfactual_check [required]` | Every scorecard candidate needs a counterfactual outcome class. | Add one of `collapses`, `survives_weakened`, `independent`, or `insufficient_evidence`, plus the explanatory note. |
| `decision.rejection_reason [type]` | The schema needs a list even for one explanation. | Use `rejection_reason: ["reason"]`; retain `can_revisit_if` for rejected/deferred/merged candidates. |
| `literature_observations[n].strength [enum]` | A prose label is not a valid evidence level. | Use `direct`, `indirect`, or `weak`; do not substitute `supporting`. |
| `minimum_experiment.source_refs [required]` | A claimed supported/user-provided protocol needs a source. | Add workspace/path anchors or downgrade honestly to `proposed_not_verified`/`unknown`. |

The T4 prompt and Gate1 renderer retain all candidates. A repeated error with a different field is not a reason to call the same write blindly: repair the reported object and retry. `trace <run-id>` contains the complete bounded diagnostic payload when an external wrapper only prints the error code.

## What Not To Infer From Logs

- A retrieval coverage gap is not a research gap.
- A citation-graph or ranking signal is not a final scientific judgment.
- A Tool success only means the tool completed; stage validation decides whether its artifact is usable.
- A model's natural-language plan is not an experiment protocol without traceable current-project inputs.
