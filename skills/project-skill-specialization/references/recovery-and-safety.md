# Recovery and Safety

## Active Suite protection

If `external_executor/executor_status.json` exists and its active status is `running`, do not replace the published Suite. Return:

```text
active_executor_suite_cannot_be_replaced
```

Pause the external executor through the existing ResearchOS control flow before rebuilding. Do not add a normal force flag that bypasses this rule.

## Failure ownership

### Missing or malformed project sources

Examples:

```text
handoff_missing
handoff_parse_error
expected_outputs_missing
allowed_paths_missing
```

Repair or regenerate the owning upstream T5/control artifact. Do not write a substitute inside the Skill.

### Invalid repository Schema or mapping

Examples:

```text
schema_invalid
mapping_invalid
schema_path_missing
display_field_invalid
```

Repair the repository asset under `skills/external_executor_skills/`, run its unit tests, then rerun dry-run. Do not patch the workspace copy as the source of truth.

### Template or marker failure

Examples:

```text
template_skill_missing
template_marker_missing
template_marker_duplicate
template_integrity_error
```

Repair the root template. Do not manually edit the generated Skill to pass validation.

### Context uncertainty

`incomplete` and uncertain fields are not compiler exceptions. Preserve them and route them through runtime alignment or an upstream source correction.

### Staging or publication failure

Examples:

```text
staging_copy_failed
publish_failed
rollback_failed
```

- confirm whether the prior published Suite remains intact;
- preserve the error report and logs;
- do not publish individual files by hand;
- repair filesystem permissions, path policy, or publication logic;
- rerun dry-run before the next build.

A rollback failure requires manual repository/workspace inspection before any retry.

## Safe retry sequence

1. Read the preflight or durable report.
2. Repair the named source, repository asset, or filesystem condition.
3. Run dry-run.
4. Require dry-run validation success.
5. Run build.
6. Summarize the durable report.

Do not repeatedly rerun a failed build without changing the reported cause.

## Path and privacy rules

- Use repository-relative and workspace-relative paths in reports and examples.
- Never commit machine usernames, expanded home paths, secrets, or private tokens into the Skill, mapping, Context, Guidance, or report.
- Do not print full project Context to terminal output.
- Do not follow symlinks that escape the template root or workspace output root.
- Do not expose unrelated environment variables to scripts.
