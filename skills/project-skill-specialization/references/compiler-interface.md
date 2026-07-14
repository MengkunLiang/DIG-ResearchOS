# Compiler Interface

## Purpose

This Skill is a companion operational interface for the deterministic ResearchOS Project Skill Specializer. The compiler service is the only implementation of Context extraction, Guidance rendering, validation, staging, publication, and rollback.

## Public Python API

The repository must expose:

```python
from researchos.skills.project_specialization.compiler import (
    specialize_project_skills,
)

result = specialize_project_skills(
    workspace=workspace_path,
    repo_root=repo_root_path,
    dry_run=False,
    validate_only=False,
)
```

Expected arguments:

- `workspace: pathlib.Path` — project workspace containing `project.yaml` and `external_executor/`.
- `repo_root: pathlib.Path | None` — ResearchOS repository root. When omitted, the service may resolve it using repository conventions.
- `dry_run: bool` — build and validate in staging without publication.
- `validate_only: bool` — validate an existing output without rebuilding.

`dry_run` and `validate_only` are mutually exclusive.

The result may be a dataclass or mapping, but it must expose enough information to recover:

```text
status
report_path
context_path
skills_path
required_uncertain_fields
optional_uncertain_fields
errors
```

Allowed status values:

```text
ready
incomplete
failed
```

## CLI API

The repository-level CLI should expose one command:

```bash
python -m researchos.cli specialize-executor-skills \
  --workspace <workspace>
```

Supported modes:

```bash
--dry-run
--validate-only
```

The bundled Skill wrapper imports the Python service directly so that the Skill, CLI, T5 task, and tests share one implementation. It must not reimplement the compiler or invoke a chain of internal scripts.

## Wrapper behavior

`scripts/run_specialization.py`:

1. resolves and validates repository/workspace paths;
2. runs the lightweight Skill preflight;
3. imports the public service;
4. calls it once in the requested mode;
5. normalizes dataclass, mapping, or object results;
6. reads the durable report when available;
7. emits one JSON result;
8. exits `0` for `ready` or `incomplete`, and `1` for `failed`.

A missing public service is a repository implementation error, not permission for the LLM to perform specialization manually.

## No duplicate execution logic

Do not create separate behavior for:

- the T5 task;
- the CLI;
- this Skill;
- tests;
- a legacy customization Skill.

All paths call `specialize_project_skills(...)`.
