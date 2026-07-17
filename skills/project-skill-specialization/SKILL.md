---
name: project-skill-specialization
execution_scope: state_machine
execution_owner: T5-SPECIALIZE-EXECUTOR-SKILLS
description: 从 T5 reboost 或 legacy handoff 及其权威来源构建、预览或校验项目专属 external-executor Skill Suite。适用于 Suite 缺失、过期、恢复前核验或发布失败诊断；不会补造项目事实、执行实验或替换运行中的 Suite。
tools:
  - read_file
  - list_files
  - glob_files
  - grep_search
  - bash_run
  - finish_task
allowed_read_prefixes:
  - ""
  - project.yaml
  - external_executor/
  - ideation/
  - literature/
  - novelty/
  - user_seeds/
allowed_write_prefixes:
  - external_executor/
---

# Project Skill Specialization

Act as the operational controller for project Skill specialization. Use the bundled wrappers to invoke the repository's deterministic specializer; do not perform context extraction, Guidance rendering, or template editing yourself.

## Establish the operating roots

1. Locate the nearest repository root containing both:
   - `researchos/`;
   - `skills/external_executor_skills/skill_specialization.yaml`.
2. Locate the project workspace containing both:
   - `project.yaml`;
   - `external_executor/`.
3. Treat the directory containing this file as `<skill-dir>`.
4. Read before execution:
   - `references/compiler-interface.md`;
   - `references/source-authority-and-uncertainty.md`;
   - `references/specialization-contract.md`;
   - `references/report-and-return-contract.md`.
5. Read `references/recovery-and-safety.md` before retrying, replacing an existing Suite, or responding to a failed preflight or build.

Do not broaden repository roots, workspace roots, source authority, writable paths, or compiler modes by inference.

## Own only invocation and result interpretation

This Skill owns:

- resolving the repository and workspace passed to the compiler;
- choosing `build`, `dry-run`, or `validate-only` mode from the request;
- running deterministic preflight and specialization wrappers;
- reading `external_executor/report/skill_specialization_report.json`;
- returning a compact status and actionable failure surface.

The Project Skill Specializer owns:

- reading handoff and source artifacts;
- building `project_skill_context.yaml`;
- applying source-over-handoff rules;
- preserving `uncertain` fields;
- validating the Context Schema and specialization mapping;
- copying and rendering all 13 executor Skills;
- verifying marker and template integrity;
- staging, publishing, and rollback;
- writing the specialization report.

Never duplicate those responsibilities in prose edits, ad hoc Python, or direct file manipulation.

## Choose one mode

Use exactly one mode:

- **Build**: generate and atomically publish the project Context and all 13 specialized Skills. This is the default after T5-REBOOST or legacy T5-HANDOFF.
- **Dry run**: execute the complete build and validation path in staging without publishing. Use when the user asks to preview, debug, or test specialization.
- **Validate only**: validate an existing specialized Suite without rebuilding it. Use when the output already exists and only integrity or readiness must be checked.

Do not use validate-only to repair stale or missing outputs. Do not use build to overwrite a Suite whose executor status is `running`.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_specialization.py \
  --workspace <workspace> \
  --repo-root <repo-root> \
  --mode build|dry-run|validate-only \
  --json
```

The preflight checks repository assets, required control artifacts, the 13 template directories and markers, compiler availability, and mode-specific active-executor safety. Use the same mode that will be passed to the specializer.

Proceed when `status` is `pass` or `warning`. Stop when it is `fail`. Treat warnings about optional source artifacts as potential `uncertain` fields, not as permission to invent values.

## Invoke the specializer

### Build and publish

```bash
python <skill-dir>/scripts/run_specialization.py \
  --workspace <workspace> \
  --repo-root <repo-root> \
  --mode build \
  --json
```

### Dry run

```bash
python <skill-dir>/scripts/run_specialization.py \
  --workspace <workspace> \
  --repo-root <repo-root> \
  --mode dry-run \
  --json
```

### Validate existing output

```bash
python <skill-dir>/scripts/run_specialization.py \
  --workspace <workspace> \
  --repo-root <repo-root> \
  --mode validate-only \
  --json
```

Use the wrapper rather than calling internal builder, renderer, validation, or publication modules separately. All entrypoints must converge on the repository's public `specialize_project_skills(...)` service.

## Inspect the durable report

For a published build or validation, read:

```text
<workspace>/external_executor/report/skill_specialization_report.json
```

Produce a compact summary with:

```bash
python <skill-dir>/scripts/summarize_specialization_report.py \
  --workspace <workspace> \
  --json
```

Do not determine readiness by checking only whether `external_executor/skills/` exists. The report, Context Schema validation, 13-Skill count, marker checks, and template-integrity results must agree.

## Handle the result

### `ready`

Confirm that:

- `project_skill_context.yaml` exists and validates;
- all 13 Skills were specialized;
- required uncertain fields are empty;
- template integrity passed;
- the published Suite path is present.

Return control to the `T5-SPECIALIZE-EXECUTOR-SKILLS` task or caller.

### `incomplete`

The Suite was generated successfully, but one or more fields required by an injection mapping remain `uncertain`.

- Preserve the generated Suite and report.
- List the required uncertain paths and their source notes.
- Do not fill them from general knowledge, repository defaults, or conversational assumptions.
- Route their resolution to runtime `context-alignment`, an upstream source correction, or the existing human gate.
- Do not reinterpret `incomplete` as a failed compiler run or a failed scientific audit.

### `failed`

- Confirm that the previous published Suite, if any, was preserved.
- Report the exact error codes, paths, Skill names, and field paths from the wrapper/report.
- Use `references/recovery-and-safety.md` to choose the repair owner.
- Do not bypass Schema validation, change mapping paths, edit generated Guidance manually, or use a force overwrite to make the report pass.

## Non-negotiable boundaries

Never:

- create or edit `project_skill_context.yaml` manually;
- directly write text inside the 13 Guidance marker regions;
- modify text outside those marker regions;
- use an LLM to resolve source conflicts or uncertain values;
- add a per-Skill project-context node or per-Skill configuration file;
- treat handoff as authoritative when a designated source artifact provides a different value;
- perform context alignment, novelty audit, protocol review, resource readiness, or claim approval during specialization;
- copy legacy `skills_customization` into the 13-Skill output;
- publish a partial Suite;
- overwrite a Suite while `executor_status.json` says `running`;
- delete failed, stale, or previous evidence as part of specialization.

## Return contract

Return a compact result containing:

```text
skill=project-skill-specialization
mode=build|dry-run|validate-only
status=ready|incomplete|failed
workspace=<workspace>
context=external_executor/project_skill_context.yaml|not_published
skills=13/13|<count>/13
report=external_executor/report/skill_specialization_report.json|not_published
required_uncertain_fields=<paths>
errors=<codes>
next_action=continue_to_gate|resolve_uncertain_context|repair_specializer|stop
```

Do not include the full project Context, private research content, or long report payload in the return message.
