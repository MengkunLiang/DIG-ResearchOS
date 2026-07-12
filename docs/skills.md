# Skills

Skills are discoverable, atomic workflows stored as `skills/<name>/SKILL.md`.
They run through the same workspace policy, ToolRegistry, trace, event, output
validation, and recovery model as pipeline agents. The protected
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
