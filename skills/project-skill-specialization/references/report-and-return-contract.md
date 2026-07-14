# Report and Return Contract

## Durable report

Published builds and validate-only runs use:

```text
external_executor/skill_specialization_report.json
```

The report is the durable specialization result. It is not a research audit.

## Required report surface

The caller must be able to read:

```text
schema_version
status
context_file
context_schema
mapping_file
skills_output
skills_total
skills_specialized
skills[]
required_uncertain_fields
optional_uncertain_fields
source_overrides
missing_paths
schema_errors
mapping_errors
render_errors
template_integrity_errors
generated_at
```

Per-Skill entries should include:

```text
skill_name
template
output
status
confirmed_injections
uncertain_injections
required_uncertain_paths
optional_uncertain_paths
detail_refs
template_integrity
```

## Status semantics

### `ready`

- Context and Schema validate;
- mapping and paths validate;
- all 13 Skills exist and are specialized;
- marker and template integrity pass;
- no required injection field is uncertain.

### `incomplete`

- all structural checks and all 13 Skill generations succeeded;
- one or more mapping items marked `required: true` remain uncertain.

`incomplete` is a successful compiler output. It does not mean the research design failed audit.

### `failed`

At least one Schema, mapping, path, template, marker, render, staging, publication, rollback, or active-Suite protection condition failed.

A failed build must not replace an existing valid published Suite.

## Wrapper exit behavior

```text
ready       exit 0
incomplete  exit 0
failed      exit 1
usage error exit 2
```

## LLM return behavior

Return only navigation and decision information:

- mode;
- status;
- workspace;
- Context path;
- count of specialized Skills;
- report path;
- required uncertain paths;
- error codes;
- next action.

Do not echo full project Context, source text, handoff content, or the complete report.

## Gate routing

```text
ready
  continue to T5 specialization review or the next configured gate.

incomplete
  expose required uncertain paths; allow runtime alignment, source correction, or human review according to the existing state machine.

failed
  stop before downstream material/executor gates and repair the compiler input or repository asset named by the error.
```
