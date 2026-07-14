# Repository, Workspace, And Ownership Boundaries

> [English](../en/project_structure.md) | [中文](../cn/project_structure.md)

ResearchOS separates version-controlled system code from user-owned workspaces. Never treat repository fixtures, a different project workspace, or a model example as input evidence for the current project.

## Repository

```text
DIG-ResearchOS/
├── researchos/                 Python runtime, agents, tools, schemas, CLI
├── config/                     Runtime and system contracts
│   ├── model_settings.yaml     Local provider/model settings, created by configure-llm
│   ├── mcp.yaml                Optional MCP server list
│   └── system_config/          Runtime defaults, Agent contracts, state machine, gates, schemas
├── skills/                     Discoverable atomic and integrated public Skills
│   └── external_executor_skills/  External executor assets; separate ownership
├── prompts/                    Agent prompt templates
├── docs/                       Maintained usage and developer documentation
├── deploy/                     Docker Compose definition
├── infra/docker/               ResearchOS image and TeX environment
├── scripts/                    Maintained repository utilities
├── requirements.txt            Python dependencies
├── pyproject.toml              Package metadata and tool configuration
└── environment.yml             Conda environment definition
```

`AGENTS.md`, `BACKGROUND.md`, local `.env`, `workspace/`, and `tests/` are ignored by the repository policy in this checkout. They are available locally where applicable but are not release artifacts.

## Workspace

```text
workspace/<project>/
├── project.yaml                Research scope and user constraints
├── state.yaml                  Current state-machine position and gates
├── user_inputs/                Human-provided Skill intake and follow-ups
├── user_seeds/                 Optional user seed materials
├── literature/                 Retrieval, paper cards, queues, synthesis
├── ideation/                   T4 candidates, selection, hypotheses, audits
├── drafts/                     Survey/manuscript sections, claims, reviews
├── external_executor/          T5 handoff and executor return contract
├── experiments/                Ingested run evidence and claim mappings
├── submission/                 Final bundle, compile report, fingerprints
└── _runtime/                   Logs, traces, event JSONL, Skill sessions/workflow state
```

### Ownership Rules

| Path | Writer | Purpose |
| --- | --- | --- |
| `project.yaml`, `user_seeds/`, `user_inputs/` | Human, guided intake | Scope, constraints, supplied material |
| `literature/`, `ideation/`, `drafts/` | ResearchOS after validation | Auditable research artifacts |
| `external_executor/` | ResearchOS + selected external executor | Handoff and protocol-bound return files |
| `experiments/` | Result ingestion and audit tools | Observed results, not model guesses |
| `_runtime/` | Runtime only | Operational state; do not edit to change research conclusions |

### T4 Ideation Artifacts

`ideation/` is a versioned decision record, not a scratch directory. Before selection it contains `evidence/` (Evidence Index, Opportunity Map, and Route-specific bundles), `populations/` (`P0`, `P1`, and later snapshots), `genomes/`, `families/`, `scoring/`, `evolution/` (plans, offspring, contracts, diagnostics, state, and operation outcomes), `candidates/`, `archive/`, and `human_directives/`. The retained `_pass1_forward_candidates.json`, `_pass2_grounding_review.json`, `_candidate_directions.json`, `_gate1_candidate_cards.md`, and `_gate1_selection_brief.md` are compatibility projections for Gate1 consumers; they do not replace the Population snapshots.

After a complete Candidate is selected, `hypothesis_brief.yaml`, `selected/hypothesis_lineage.json`, `selected/t45_search_targets.json`, and `selected/pre_novelty_brief.md` describe a Pre-Novelty research direction. They preserve lineage and define a targeted T4.5 audit scope, but do not authorize T5. Only a passing T4.5 audit may create or update formal `hypotheses.md`, `exp_plan.yaml`, `contribution_hypothesis_map.yaml`, `validation_map.yaml`, `kill_criteria.yaml`, and `post_novelty_formalization.json`.

`literature/deep_read_notes/`, `literature/shallow_read_notes/`, and `literature/bridge_notes/` are the only live Paper Note roots. Deep and Bridge notes can provide bounded full/partial-reading evidence; shallow notes are abstract-level recall only. Old `paper_notes*` directories are handled only by the explicit workspace migration layer, which records a migration report and never makes legacy paths a second live source. A conflicting legacy note is preserved under `literature/note_migration_conflicts/` for review instead of being silently duplicated in an evidence root.

`ideation/t4_target_profile.json` records the researcher-confirmed Publication Orientation. `ideation/final_cards/portfolio_cards.json` contains non-mutating, profile-aware Impact Translations only for the final Portfolio Candidates. The Candidate Dossiers and Population snapshots remain the scientific source of truth; the final cards must echo their thesis, contribution IDs, and hypothesis IDs exactly.

Use workspace-relative paths in prompts, artifacts, and Skill contracts. Do not copy an artifact between projects without recording its source and validating it under the target project's constraints.
