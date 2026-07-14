# Specialization Contract

## Authoritative repository assets

```text
skills/external_executor_skills/
├── skill_specialization.yaml
├── schemas/project_skill_context.schema.json
└── <13 template Skill directories>/
```

The mapping file is the only per-Skill injection specification. Do not add `skills` data to `project_skill_context.yaml` and do not create per-Skill project configuration files.

## Generated workspace outputs

```text
external_executor/
├── project_skill_context.yaml
├── schemas/project_skill_context.schema.json
├── skill_specialization_report.json
└── skills/<13 specialized Skill directories>/
```

`project_skill_context.yaml` stores stable project facts. Runtime artifacts continue to own iteration plans, reviews, runs, diagnoses, attributions, realized methods, and evidence packages.

## Required Context properties

The generated Context must:

- use `project_skill_context.v1`;
- include the workspace-relative `$schema` path;
- validate against the repository Schema copied into the workspace;
- contain the fixed top-level business areas defined by that Schema;
- contain no top-level `skills` node;
- carry field-level status, sources, and notes through `field_metadata`;
- use Schema-permitted empty values for uncertain fields.

## Mapping behavior

`skill_specialization.yaml` controls:

- begin and end markers;
- section IDs and headings;
- every `inject.path`;
- labels;
- render types;
- required flags;
- item limits;
- object display fields;
- detailed Context references;
- uncertain-field handling.

Every injected dotted path and detailed reference must resolve against both the Context Schema and the generated Context instance.

## Guidance rendering

Only the content inside this region may change:

```markdown
<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
...
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->
```

Render confirmed values in their mapped sections. Move uncertain values to `Uncertain project fields`; do not render empty uncertain values as facts. Generate `Detailed project context` as references to the shared Context file rather than copying large objects.

The renderer must be deterministic. It may not ask an LLM to paraphrase, rank, merge, complete, or resolve fields.

## Template integrity

For each of the 13 Skills:

1. require exactly one begin marker and one end marker;
2. require begin before end;
3. replace only marker contents;
4. canonicalize both template and output by substituting the marker region with the same placeholder;
5. require all remaining text to match.

A template-integrity mismatch is a failed specialization.

## Staging and publication

Build the Context, copied Schema, report, and 13 Skills in a sibling staging directory. Validate all outputs before publication.

Publish using a directory swap with backup and rollback. Never expose a partially generated formal `external_executor/skills/` directory. Never use a previous workspace copy as a template.

## Modes

```text
build
  Build, validate, and publish.

dry-run
  Build and validate in staging; publish nothing.

validate-only
  Validate existing Context, report, and 13-Skill output; rebuild nothing.
```
