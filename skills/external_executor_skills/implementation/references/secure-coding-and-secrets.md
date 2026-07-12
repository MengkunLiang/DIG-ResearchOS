# Secure Coding and Secrets

## Commands

- use argv arrays, not shell strings;
- do not use `shell=True`;
- execute only verification commands declared in the contract;
- use bounded timeout and process-group termination;
- sanitize environment variables;
- do not pass credentials unless a declared local verification genuinely requires a non-secret placeholder;
- do not execute downloaded or third-party setup scripts.

## Dependencies

Dependency manifest changes are sensitive. They require an approved change item and must record:

- package and version constraint;
- why the existing environment is insufficient;
- license/security considerations;
- whether installation or network access is needed later;
- protocol/fairness impact;
- reproducibility impact.

This skill does not install the dependency. An unapproved dependency change is blocking.

## Secrets

Never place in source, config, tests, fixtures, logs, patch, or report:

- API keys or access tokens;
- passwords;
- private keys;
- signed URLs;
- cloud credentials;
- private repository credentials;
- restricted dataset credentials;
- real `.env` contents.

Use placeholder names and environment-variable lookup. Scope scanning is heuristic; a clean scan does not prove absence of secrets.

## Dangerous patterns

Review-sensitive or blocking patterns include:

- destructive filesystem operations;
- privilege escalation;
- arbitrary `eval`/`exec` of external content;
- subprocess shell execution;
- dynamic remote code loading;
- hidden network requests;
- package installation or self-update;
- disabled TLS verification;
- writing outside declared output roots;
- broad exception swallowing around scientific computation;
- deletion of tests or provenance.

## Symlinks and paths

Reject symlinked source/worktree entries by default. Normalize paths before checking them against the workspace and allowed roots. Do not rely on string-prefix checks alone.

## Binary and generated files

New binary files are review-sensitive and usually disallowed unless they are tiny approved test fixtures. Checkpoints, datasets, archives, compiled libraries, and generated experiment outputs do not belong in the patch.

## Data

Implementation tests use synthetic or explicitly approved tiny fixtures. Do not access restricted datasets, download resources, or serialize real records into test artifacts.

## Network

This skill has no need for network access during implementation or verification. Network-backed behavior in the code must be disabled, mocked, or deferred to a separately reviewed runtime path.
