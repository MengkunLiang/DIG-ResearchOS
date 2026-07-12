# ResearchOS Project Structure

ResearchOS has one implementation and one workspace contract. Native and
Docker Compose runs use the same source tree, configuration hierarchy, state
machine, and artifacts.

## Repository Directories

| Path | Owns | Commit policy |
| --- | --- | --- |
| `researchos/` | Python package: agents, runtime, orchestration, tools, schemas, and skills loader | Commit source; never commit caches. |
| `config/` | Checked-in defaults and workflow contracts | Commit safe defaults; secrets remain outside. |
| `docs/` | User, operations, runtime, and contributor documentation | Commit curated documents only. |
| `deploy/` | User-facing Compose entry point and wrappers | Commit; it has no duplicate config tree. |
| `infra/docker/` | Dockerfile and low-level image build helpers | Commit; maintainers use it to build `researchos/system:latest`. |
| `latex_templete/` | Local venue templates used by T3.6/T8/T9 | Commit source/template assets; ignore generated auxiliaries. |
| `skills/` | Standalone guided Skill source plus the separately governed `external_executor_skills/` protocol tree | Commit source/contracts and references; do not put workspace uploads here. |
| `scripts/` | Reusable repository maintenance utilities | Commit maintained tools, not ad hoc debugging. |
| `tests/unit/` | Deterministic automated tests | Commit. |
| `tests/real/` | Real API, Docker, or local-tool integration tests | Commit intentional tests; they may require local capabilities. |
| `workspace/` | Default user project root | Generated; do not commit. |
| `tmp/` | Local scratch work | Generated; do not commit. |

## The Runtime Boundary

```text
Native:  repository + host Python + host workspace
Docker:  repository -> image + /app/workspace bind mount
```

Compose mounts the same two authoritative paths:

```text
./workspace  <->  /app/workspace      writable project artifacts
./config     ->   /app/config          read-only shared configuration
```

`deploy/compose.yaml` is the only Compose entry point. `infra/docker/` is not a
second deployment or configuration tree. Never create a `deploy/config/` copy.

## TeX Ownership

| Location | Purpose | Required contents |
| --- | --- | --- |
| Host OS | Native compilation or first choice for `auto` | `latexmk`, pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX packages when Chinese output is needed. |
| `infra/docker/Dockerfile` | Compose-native compilation and native Docker fallback image | The same TeX toolchain is installed in `researchos/system:latest`. |
| `requirements.txt` | Python runtime, PDF/BibTeX tooling and deterministic survey-figure dependencies | Never add TeX packages here; matplotlib is Python-level, TeX remains system/image-owned. |
| Workspace | TeX source and generated evidence | `drafts/survey/` and `submission/bundle/`; PDFs/logs are generated, not source. |

The running Compose service does not receive a Docker socket. It compiles with
the TeX packages already inside its own image. See [docker.md](docker.md).

## Workspace Directories

Each `init-workspace`, `run`, `resume`, and `run-task` operation initializes
missing directories and writes non-destructive `_DIR_GUIDE.md` files.

| Path | Primary content | Main consumers |
| --- | --- | --- |
| `project.yaml` | Research topic and project-level settings | All stages. |
| `state.yaml` | Current task, status, and pending gate | State machine and `resume`. |
| `user_seeds/` | User-provided PDFs, initial ideas, and scope constraints | T1-T4 and writing. |
| `literature/` | Search records, retained pool, notes, synthesis, and citation provenance | T2-T4/T8. |
| `ideation/` | Candidate pool, Gate1 selection, hypotheses, and experiment plan | T4-T5. |
| `external_executor/` | Handoff protocol, skills, materials gate, result pack | T5-T7. |
| `experiments/` | Ingested runs, integrity audit, and result-to-claim artifacts | T7-T8. |
| `drafts/` | Survey/paper sections, audits, templates, deterministic figures, and compile evidence | T3.6/T8/T9. |
| `user_inputs/` | Standalone Skill uploads, deterministic intake checklist, and focused multi-turn follow-up requests | Guided Skills. |
| `submission/` | Migrated submission bundle and compile report | T9. |
| `_runtime/` | Logs, traces, pipeline resume states, guided Skill sessions, and failed-artifact archives | Operations and recovery. |

### Guided Skill uploads and sessions

Standalone Skills never ask users to copy material into the repository's `skills/`
source tree. Each Skill declares its accepted workspace paths. User-supplied
standalone material normally belongs under a named directory such as
`user_inputs/paper-outline/brief.md` or `user_inputs/paper-revision/reviews.md`.
Existing pipeline artifacts (for example `drafts/outline.md` or
`literature/synthesis.md`) may be accepted as alternatives when the Skill
contract says so.

Each invocation stores only interaction state—not the uploaded contents—at
`_runtime/skill_sessions/<session-id>.json`. The file records the request,
which declared input path passed validation, the last observable step/phase/tool,
output existence, final stop reason, metrics, and trace path. It is safe to inspect and is the source of truth for
`researchos run-skill ... --resume` and `researchos skill-status`.

Every guided Skill also writes `user_inputs/<skill>/_intake.md`. It distinguishes an independent workspace,
where the user supplies the declared upload paths, from a project workspace, where known artifacts are only candidate
inputs. A running Skill that finds a semantic gap writes `user_inputs/<skill>/_followup_request.md` before asking the
human; this keeps the question, answer path and later resume visible in project data. These two files are process
metadata, not paper evidence and not final Skill outputs.

T8 keeps a venue-aware writing contract in `drafts/writing_style.json` and a visible research-story contract in
`drafts/writing_storyline.md`. The latter links the problem, rationale or technical root reason, insight, design,
evidence, alternative explanations, limitations and reviewer questions. `drafts/craft_audit.*` then records internal
profile diagnostics and section counts; neither file defines official submission requirements.

`drafts/survey/figures/` is generated project data. Its
`survey_visual_manifest.json` records which comparison-table-derived figures exist, their input fingerprints,
font/DPI, and explicit skipped reasons. Do not upload arbitrary images there or treat a skipped manifest as a request
to fabricate a visual.

The workspace is durable project data. Do not delete it when rebuilding a
Docker image. Do not make Native and Docker processes write it concurrently.

## Repository Versus Project Data

Keep these local and out of Git/Docker images:

- `.env` and provider credentials
- `workspace/` artifacts, traces, PDFs, and submission build outputs
- datasets, model weights, external code checkouts, and executor scratch data
- `tests/manual/` diagnostics and temporary images

Use `docs/agent_pipeline.md` for the full artifact contract and
`docs/logging.md` for diagnosing a specific workspace.
