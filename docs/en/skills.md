# Skills: Discovery, Inputs, Retrieval, Execution, And Recovery

> [English](../en/skills.md) | [中文](../cn/skills.md)

Skills are discoverable workflows stored as `skills/<name>/SKILL.md`. They may be atomic or integrated: an integrated Skill declares durable research phases, evidence boundaries, and human decision points while reusing the same workspace policy, ToolRegistry, trace, event, output validation, and recovery model as pipeline agents. The protected `skills/external_executor_skills/` directory has separate ownership and is not part of the public-Skill rewrite path.

Each repository Skill has an execution-scope contract. Existing independent Skills retain the compatible `standalone` default, while every non-standalone repository Skill declares its scope and owner explicitly. `list-skills`, `browse-skills`, and `run-skill` expose only Skills with a standalone session contract. Pipeline-owned Skills remain loadable by their declared stage, but direct invocation stops before workspace initialization or model setup and explains the owning stage. In particular, `research-reboost` belongs to `T5-REBOOST-GATE`, `project-skill-specialization` belongs to `T5-SPECIALIZE-EXECUTOR-SKILLS`, and the legacy `method-builder` is internal to the project-specific external-executor suite. This separation prevents a command-line session from treating a pipeline artifact contract as an empty-workspace prompt.

## Discover Before Running

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
```

`browse-skills` supports a number, full name, or a localized fuzzy keyword such as `literature` or `Idea`. Inspect the card before `run <id>`: it explains purpose, inputs, output artifacts, limits, and recovery command.

## Guided Session Contract

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01
```

In a TTY, the default flow is:

1. Read the declared input contract and check local files.
2. Ask for one missing material or fact at a time through the explicit `ask_human` channel.
3. Stage only human-provided material, or an explicitly authorized remote source, in `user_inputs/<skill>/`.
4. Recheck deterministic readiness.
5. Persist `WAITING_CONFIRMATION` and ask for an explicit Run or Pause decision.
6. Run the Skill only after explicit authorization.
7. Persist observable phase, current tool, outputs, summary, and a resume command in `_runtime/skill_sessions/<session-id>.json`.

Before a guided Skill is listed or run, ResearchOS validates that every input path in its contract is readable and every declared output is writable under that Skill's workspace permissions. The runtime displays the same capability boundary to the Skill. This is intentionally strict: a public Skill must not advertise a file location that later becomes `access_denied`.

When a running Skill identifies a semantic evidence gap, it writes `user_inputs/<skill>/_followup_request.md` before asking the human. It may not guess missing source, venue, citation, experiment, or result information.

### Remote Paper Sources And Pause Semantics

`pdf-note-card`, `paper-comparison`, and `literature-comparison-studio` can resolve a source during guided intake when the researcher explicitly supplies a DOI, arXiv/OpenAlex identifier, direct PDF URL, exact title, or a topic plus requested count. The restricted intake Agent receives only declared source-resolution tools plus file staging tools. It cannot run a shell, alter research outputs, or browse unrelated workspace paths.

| Input form | What the intake does | Evidence status after intake |
| --- | --- | --- |
| Uploaded PDF | Inspects the declared input path and passes it to the Skill. | The PDF is an unread source until section extraction. |
| DOI/arXiv/OpenAlex ID or direct URL | Attempts metadata resolution and PDF download to the declared `user_inputs/<skill>/` path. | Download outcome and identifier are written to `_source_resolution.md`; metadata alone is not section evidence. |
| Exact title | Searches declared academic sources and asks a focused clarification when more than one match is consequential. | Search results are leads, not verified paper evidence. |
| Topic plus count | Records query, requested count, candidates, selection rule, and access results before reading/comparing. | Unread or inaccessible candidates remain explicitly weak/unknown. |

For a PDF note card, a direct source request can be supplied as the Skill request:

```bash
python -m researchos.cli run-skill pdf-note-card \
  "Read DOI 10.1145/nnnnnnn.nnnnnnn and build a method/limitation note card" \
  --workspace ./workspace/project-a --session-id reading-doi-01
```

For comparison, provide two identifiers or authorize a narrow topic retrieval:

```bash
python -m researchos.cli run-skill paper-comparison \
  "Compare DOI 10.xxxx/a and arXiv:2501.01234 on treatment heterogeneity" \
  --workspace ./workspace/project-a --session-id compare-two

python -m researchos.cli run-skill literature-comparison-studio \
  "Find and compare 4 recent papers on the declared research topic; prefer readable full text" \
  --workspace ./workspace/project-a --session-id compare-topic
```

When a post-intake control shows `[1] continue collecting missing material` and `[2] pause and preserve the session`, option `1` starts the next focused intake round and option `2` immediately persists `WAITING_INPUT` and returns to the shell. The localized UI uses equivalent labels. An unrecognized response is re-asked; it never silently starts another intake round. Resume with the same session ID after adding material or changing the request.

For automation or pipes:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

Missing input then produces recoverable `WAITING_INPUT` and does not construct a provider client. Continue after adding material:

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 --resume
```

## Skill Pages And Material Preparation

Start with `browse-skills` or `describe-skill <name>`. The catalog is for fast selection: each entry shows only its purpose, the number of materials it needs, and the number of files it produces. The detail page then uses Rich tables for material locations, why each material is needed, available capabilities, outputs, and the recovery command. Complete Tool names and implementation detail are hidden by default and appear with `--verbose`.

On the first run of a guided Skill, the system checks existing project files and that Skill's material directory. When the materials are ready, it explains that the Skill can begin and asks for execution confirmation. When material is missing, it asks only for the next missing item and offers upload, a DOI/arXiv/OpenAlex ID, URL, exact title, or, for supported Skills, a topic plus count. Typing pause, exit, or later preserves the current session and returns to the terminal without another question.

In the interface, a paper reading note is a note that preserves its source, reading coverage, and location in the paper. Relevant paper content or location means a revisitable paragraph, heading, or page. These labels do not require a researcher to understand internal terms such as `section anchor`, `artifact`, or `schema`. Use `--verbose`, `trace`, or the run log only when technical diagnosis is needed.

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

### Capability Profiles And Tool Boundaries

Every public Skill now receives the `workspace_navigation` profile: `list_files`, `glob_files`, and `grep_search`. These utilities obey the Skill's own `allowed_read_prefixes`; they do not provide a route to inspect another workspace or arbitrary host paths. The catalog also resolves an explicit profile set per Skill and displays it through `list-skills` and `describe-skill`.

| Profile | Adds | Used for |
| --- | --- | --- |
| `literature_discovery` | Multi-source, Semantic Scholar, arXiv, OpenAlex, Crossref, Scopus, INFORMS search and metadata lookup | DOI/title/topic discovery, source triangulation, venue-aware searching |
| `paper_acquisition` | PDF fetch, PDF text/section extraction, local record lookup | Reading a named paper or comparing retrieved candidates |
| `paper_curation` | Seed-paper processing and note-card saving | Turning resolved material into durable evidence cards |
| `literature_processing` | Query expansion, deduplication, screening, access audit, deep-read queue, citation graph and synthesis workbench | Review-scale corpus management and evidence coverage |
| `structured_artifacts` | Schema-checked YAML/JSON writing | Machine-readable plans, scorecards, manifests, and audit records |
| `idea_analysis` | Concentration, novelty-signal, mechanism/design-rationale tuple tools | Evidence-bounded candidate comparison and innovation auditing |
| `claim_review` | Claim, evidence, and writing-craft audits | Draft repair, peer review, polish, and submission checks |
| `manuscript_planning` / `survey_workflow` / `tex_delivery` | Manuscript/Summary assembly, Survey audit/figures, actual TeX compilation | Writing and deliverable workflows with declared outputs |

Profiles are additive and visible, but they are not ambient authority. They do not grant `bash_run` or `docker_exec`; file access remains constrained by the individual Skill contract; source acquisition requires an explicit DOI/arXiv/OpenAlex ID, URL, exact title, or topic-plus-count request and a writable declared destination. This gives a reading or review Skill enough tools to resolve and inspect evidence without allowing unrelated workspace mutation or arbitrary host execution.

## T4 And Downstream Skills

T4 uses role-separated Generator, Scorer, and Evolver capabilities. Its Gate1 transition is action-dependent: selecting a ready Candidate follows `T4 -> T4-GATE1 -> T4.5`, while evolution, focused optimization, route regeneration, or confirmed composition follows `T4 -> T4-GATE1 -> T4` and later returns to Gate1. Inspection and comparison remain read-only at Gate1. Generator forms evidence-calibrated, creatively divergent Candidates; Scorer independently evaluates blinded Candidates and never creates an Idea; Evolver creates only plan-bounded Mutation Children or Compatibility-gated Crossover Children. Evidence is not a closed idea space: normal Generator routes may use scholarly knowledge, counterfactual reasoning, and structural cross-domain analogy when the resulting claims remain visibly conjectural and verification-required. A Bridge route may return `unsupported` with an escape-hatch record when the workspace does not contain a defensible structural transfer.

Use `t4-evolution` when the researcher needs a safe native-T4 entry point. It checks the current Evidence Index, pre-run confirmation, Population, Portfolio, and resume state; it then explains whether the next pipeline action creates P0, resumes an unfinished Route or scoring batch, waits for Gate1, or continues a confirmed selection toward T4.5. The Skill writes a researcher-readable launch note and never edits native T4 artifacts. Start a new entry with `python -m researchos.cli run --workspace <workspace> --from-task T4`; resume an interrupted or waiting run with `python -m researchos.cli resume --workspace <workspace>`. Do not run concurrent commands for one workspace.

T4 treats semantic format recovery separately from scientific safety and records `valid`, `repairable`, `degraded`, or `blocked`. `blocked` protects Hard Invariants only: source/evidence-permission violations, fabricated or untraceable citations, Candidate/Parent/Plan lineage conflicts, ID overwrite, fingerprint/workspace corruption, and Legacy overwrite risk. Markdown fences, YAML, aliases, object/list envelope differences, absent enrichable fields, one failed Route or scoring call, quota shortfall, and incompatible Crossover do not abort a Round. They go through tolerant extraction, deterministic normalization, schema-only repair, targeted semantic repair, and revalidation; usable but incomplete work continues as `degraded` with a diagnostic.

Generator may submit a minimal `IdeaSeed` rather than a final paper plan. A Seed needs problem, thesis, candidate mechanism, contribution sketch, one falsifiable prediction, main uncertainty, and Route origin; presentation, multiple hypotheses, a full Evidence Map, experimental detail, and impact are enrichment work. `CreativeContext` preserves a conceptual leap, competing explanations, surprising prediction, and research-program potential so initial structure does not flatten a non-incremental idea. LLM parametric knowledge may propose a `conjectural`, `verification_required=true` idea, but cannot certify a citation, mechanism, dataset, metric, or result. Scoring separates current `overall_readiness` from `scientific_upside`; an LLM-recommended Wildcard remains a human-visible comparison option, not a selection or evidence bypass. A Scorer failure after bounded retry leaves the Candidate `unscored` rather than deleting it or fabricating a score; Mutation failure keeps the Parent, a documented `no_improvement` deferral keeps the Parent without a cosmetic Child, `incompatible` Crossover is a normal review result, and a Portfolio may contain fewer than three directions. Validators protect integrity. Repair loops protect continuity. Evolution handles incomplete ideas. Human Gates retain final authority.

Publication orientation now distinguishes the internal `utd_is` lens from the `ccf_cs` lens. `utd_is` emphasizes phenomenon, theoretical tension, explanatory mechanism, identification, boundary conditions, and organizational implications. `ccf_cs` emphasizes a precise computational problem, technical mechanism, evaluation discipline, robustness, efficiency, and reproducibility. They are configurable research lenses rather than claims about a venue's current official review policy. Legacy `management_is` and `technical_cs` profiles remain readable for existing workspaces.

At Gate1, selecting one complete Candidate creates a Pre-Novelty brief and a T4.5 search scope. `hypothesis-compiler`, `paper-outline`, and other non-execution Skills may use that brief to trace the selected direction or prepare explicitly provisional material, but they must not treat it as proof of novelty or an executable protocol. Component-level requests first create a Human-composed Candidate through a Compatibility Check, Gene Donor Map, Independent Scoring, and a second confirmation; source Candidates remain preserved. T5 and all executor Skills require the post-T4.5 formal hypotheses, experiment plan, and accepted novelty audit before they can plan or run experiments.

## Integrated Research Workflows

The following public Skills are composed workflows, not aliases for a single LLM prompt. They all begin with a guided contract, write an artifact manifest, persist phase status in `_runtime/skill_sessions/<id>.json`, and use explicit human gates before scope expansion, costly reading, candidate selection, or Survey handoff.

| Skill | Main phases | Key outputs | Gate behavior |
| --- | --- | --- | --- |
| `domain-synthesis-studio` | inventory -> retrieval decision -> source supplement -> synthesis -> next-path decision | domain report, method family map, tension map, evidence register | Asks whether to synthesize current material, authorize scoped retrieval, or upload sources; then offers Survey/Idea/reading routes. |
| `literature-comparison-studio` | comparison contract -> DOI/title/PDF/topic source readiness -> section evidence -> comparison audit | comparison report/CSV/JSON, claim boundary | Supports two identifiers, uploaded PDFs, source lists, or an explicit topic-plus-count request; unknown cells remain unknown. |
| `literature-review-studio` | review scope -> query/retrieval -> reading coverage -> synthesis/taxonomy -> Survey handoff | corpus inventory, query portfolio, matrix, synthesis, readiness report | Requires retrieval authorization and later asks whether to prepare Survey, supplement reading, or stop at a field synthesis. |
| `survey-evidence-package` | intent -> sufficiency -> supplement decision -> handoff | corpus sufficiency, taxonomy candidates, storyline, evidence package | Does not write a survey manuscript. It makes the Survey evidence decision visible first. |
| `cross-domain-idea-studio` | target contract -> bridge retrieval -> transfer audit -> candidate jury | bridge plan, transfer cards, risk register, candidate pool | A bridge analogy is not proof. Candidates require a human selection before hypothesis compilation. |
| `t4-evolution` | state check -> researcher choice -> native-T4 handoff | launch/resume note | Explains the resume-safe native pipeline action and preserves every Population version; it never edits native T4 artifacts. |
| `paper-reading-workbench` | source contract -> access -> evidence reading -> cross-paper learning | reading index, cards, answers, cross-paper summary | Reads PDFs/sections by question and preserves full/partial/abstract/metadata status. |
| `research-landscape-report` | scope -> mapping/coverage -> opportunity decision | landscape report/data, coverage, opportunity register | Retrieval gaps and graph signals are reported separately from research opportunities. |
| `related-work-builder` | positioning -> evidence binding -> section draft | TeX section, evidence map, citation/claim audits | Does not create citations or direct-baseline claims without sources. |
| `draft-evidence-repair` | manuscript contract -> evidence inventory -> repair decision -> package | repair report/JSON, patch plan, claim boundary | Missing evidence leads to a human choice: supplement, weaken, delete, or pause. |

Use the new workflows through the ordinary CLI; no special runner is required:

```bash
python -m researchos.cli run-skill domain-synthesis-studio \
  "Synthesize this field; first decide whether scoped retrieval is needed, then whether to prepare a survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli run-skill cross-domain-idea-studio \
  "Generate cross-domain candidates from audited bridge evidence; do not assume unverified experimental settings" \
  --workspace ./workspace/project-a --session-id bridge-ideas

python -m researchos.cli run-skill t4-evolution \
  "Inspect the current T4 state and tell me the one safe command to resume it" \
  --workspace ./workspace/project-a --session-id native-t4
```

An integrated session presents a phase table in readiness, completion, and `skill-status` views. Valid states are `pending`, `running`, `completed`, `waiting_input`, `waiting_evidence`, and `skipped`. The Skill calls the bounded `update_skill_workflow` tool at phase boundaries; this records only user-facing research progress, not model reasoning or raw prompts.

### Automatic Supplementation

When an integrated Skill has a source-returning search tool and the researcher authorizes retrieval, it can try to supplement missing literature itself. The result is a lead/provenance record, not automatic strong evidence. A source must be read at the required granularity before it can support a mechanism, causal claim, taxonomy core, baseline comparison, or paper positioning. The workflow asks for upload, narrowing, or a separate reading Skill when automated search cannot close that evidence gap.

Use the live catalog rather than this table for exact names: the catalog is the installed capability source of truth.

## Evidence Boundary

A Skill can use AUUC, Qini, accuracy, F1, named datasets, baselines, seeds, or resource numbers only when its current-project allowed inputs or audited artifacts explicitly identify them. This is not a ban on those names. It is a provenance requirement: missing details remain `unknown` or `proposed_not_verified` and trigger a focused follow-up.

`idea-fanout-jury` illustrates the boundary. With an evidence-backed synthesis or paper cards it can produce scored, source-anchored directions. Without them it may only produce a labelled preliminary concept set with a missing-evidence ledger. It must not invent the current project's dataset, baseline, metric, AUUC/Qini value, budget, seed, command, or numerical expectation.

## Status

```bash
python -m researchos.cli skill-status --workspace ./workspace/project-a
python -m researchos.cli skill-status pdf-note-card --workspace ./workspace/project-a
```

The status panel reports session mode, readiness, current observable phase, tool activity, outputs, blockers, and the exact resume command. It does not display private model reasoning.
