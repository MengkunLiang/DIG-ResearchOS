# Skills

Skills are discoverable workflows stored as `skills/<name>/SKILL.md`. They may
be atomic or integrated: an integrated Skill declares durable research phases,
evidence boundaries, and human decision points while reusing the same workspace
policy, ToolRegistry, trace, event, output validation, and recovery model as
pipeline agents. The protected
`skills/external_executor_skills/` directory has separate ownership and is not
part of the public-Skill rewrite path.

## Discover Before Running

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
```

`browse-skills` supports a number, full name, or bilingual fuzzy keyword such
as `文献`, `literature`, `Idea`, or `创新点`. Inspect the card before `run <id>`:
it explains purpose, inputs, output artifacts, limits, and recovery command.

## Guided Session Contract

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01
```

In a TTY, the default flow is:

1. Read the declared input contract and check local files.
2. Ask for one missing material or fact at a time.
3. Stage only human-provided material in `user_inputs/<skill>/`.
4. Recheck deterministic readiness.
5. Persist `WAITING_CONFIRMATION` and ask for explicit `执行` / `暂停`.
6. Run the Skill only after explicit authorization.
7. Persist observable phase, current tool, outputs, summary, and a resume
   command in `_runtime/skill_sessions/<session-id>.json`.

Before a guided Skill is listed or run, ResearchOS validates that every input
path in its contract is readable and every declared output is writable under
that Skill's workspace permissions. The runtime displays the same capability
boundary to the Skill. This is intentionally strict: a public Skill must not
advertise a file location that later becomes `access_denied`.

When a running Skill identifies a semantic evidence gap, it writes
`user_inputs/<skill>/_followup_request.md` before asking the human. It may not
guess missing source, venue, citation, experiment, or result information.

For automation or pipes:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

Missing input then produces recoverable `WAITING_INPUT` and does not construct a
provider client. Continue after adding material:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 --resume
```

## Capability Groups

| Group | Typical Skills | Outcome |
| --- | --- | --- |
| Research intake | `research-material-ingest`, identifier/PDF resolution | Inventory of user materials and provenance |
| Paper evidence | `pdf-note-card`, section evidence, note review | Citable paper cards with evidence boundaries |
| Literature analysis | query planning, citation graph, comparison, evidence matrix, gap map | Bounded retrieval and synthesis artifacts |
| Ideas and design | idea fanout, hypothesis compiler, experiment design review | Candidate/governance artifacts, not invented protocol facts |
| Writing | paper outline, paper write, claim-evidence map | Draft structure and evidence-aligned prose |
| Review and revision | venue fit, peer review, polish, revision | Auditable review findings and patches |
| Finalization | paper compile, submission readiness | Real compile/status checks and submission artifacts |

## Integrated Research Workflows

The following public Skills are composed workflows, not aliases for a single
LLM prompt. They all begin with a guided contract, write an artifact manifest,
persist phase status in `_runtime/skill_sessions/<id>.json`, and use explicit
human gates before scope expansion, costly reading, candidate selection, or
Survey handoff.

| Skill | Main phases | Key outputs | Gate behavior |
| --- | --- | --- | --- |
| `domain-synthesis-studio` | inventory -> retrieval decision -> source supplement -> synthesis -> next-path decision | domain report, method family map, tension map, evidence register | Asks whether to synthesize current material, authorize scoped retrieval, or upload sources; then offers Survey/Idea/reading routes. |
| `literature-comparison-studio` | comparison contract -> source readiness -> section evidence -> comparison audit | comparison report/CSV/JSON, claim boundary | DOI/title/PDF failures become a focused upload/clarification request; unknown cells remain unknown. |
| `literature-review-studio` | review scope -> query/retrieval -> reading coverage -> synthesis/taxonomy -> Survey handoff | corpus inventory, query portfolio, matrix, synthesis, readiness report | Requires retrieval authorization and later asks whether to prepare Survey, supplement reading, or stop at a field synthesis. |
| `survey-evidence-package` | intent -> sufficiency -> supplement decision -> handoff | corpus sufficiency, taxonomy candidates, storyline, evidence package | Does not write a survey manuscript. It makes the Survey evidence decision visible first. |
| `cross-domain-idea-studio` | target contract -> bridge retrieval -> transfer audit -> candidate jury | bridge plan, transfer cards, risk register, candidate pool | A bridge analogy is not proof. Candidates require a human selection before hypothesis compilation. |
| `paper-reading-workbench` | source contract -> access -> evidence reading -> cross-paper learning | reading index, cards, answers, cross-paper summary | Reads PDFs/sections by question and preserves full/partial/abstract/metadata status. |
| `research-landscape-report` | scope -> mapping/coverage -> opportunity decision | landscape report/data, coverage, opportunity register | Retrieval gaps and graph signals are reported separately from research opportunities. |
| `related-work-builder` | positioning -> evidence binding -> section draft | TeX section, evidence map, citation/claim audits | Does not create citations or direct-baseline claims without sources. |
| `draft-evidence-repair` | manuscript contract -> evidence inventory -> repair decision -> package | repair report/JSON, patch plan, claim boundary | Missing evidence leads to a human choice: supplement, weaken, delete, or pause. |

Use the new workflows through the ordinary CLI; no special runner is required:

```bash
python -m researchos.cli run-skill domain-synthesis-studio \
  "综合该领域，先判断是否需要定向检索，再决定是否准备 Survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli run-skill cross-domain-idea-studio \
  "用已审计桥接证据生成跨域候选，不要未验证实验配置" \
  --workspace ./workspace/project-a --session-id bridge-ideas
```

An integrated session presents a phase table in readiness, completion, and
`skill-status` views. Valid states are `pending`, `running`, `completed`,
`waiting_input`, `waiting_evidence`, and `skipped`. The Skill calls the bounded
`update_skill_workflow` tool at phase boundaries; this records only user-facing
research progress, not model reasoning or raw prompts.

### Automatic Supplementation

When an integrated Skill has a source-returning search tool and the researcher
authorizes retrieval, it can try to supplement missing literature itself. The
result is a lead/provenance record, not automatic strong evidence. A source must
be read at the required granularity before it can support a mechanism, causal
claim, taxonomy core, baseline comparison, or paper positioning. The workflow
asks for upload, narrowing, or a separate reading Skill when automated search
cannot close that evidence gap.

Use the live catalog rather than this table for exact names: the catalog is the
installed capability source of truth.

## Evidence Boundary

A Skill can use AUUC, Qini, accuracy, F1, named datasets, baselines, seeds, or
resource numbers only when its current-project allowed inputs or audited
artifacts explicitly identify them. This is not a ban on those names. It is a
provenance requirement: missing details remain `unknown` or
`proposed_not_verified` and trigger a focused follow-up.

`idea-fanout-jury` illustrates the boundary. With an evidence-backed synthesis
or paper cards it can produce scored, source-anchored directions. Without them
it may only produce a labelled preliminary concept set with a missing-evidence
ledger. It must not invent the current project's dataset, baseline, metric,
AUUC/Qini value, budget, seed, command, or numerical expectation.

## Status

```bash
python -m researchos.cli skill-status --workspace ./workspace/project-a
python -m researchos.cli skill-status pdf-note-card --workspace ./workspace/project-a
```

The status panel reports session mode, readiness, current observable phase,
tool activity, outputs, blockers, and the exact resume command. It does not
display private model reasoning.
