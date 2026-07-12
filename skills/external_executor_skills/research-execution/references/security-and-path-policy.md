# Security and Path Policy

Use the stricter rule when this reference, `AGENTS.md`, handoff policy, and `allowed_paths.txt` differ.

## Path controls

- Resolve the workspace root before any write.
- Normalize every path and resolve existing symlinks.
- Reject absolute targets outside the workspace and relative paths containing an escape.
- Reject symlink-based escape from an allowed directory.
- Treat `external_executor/expr/` as original input and read-only unless explicitly authorized.
- Place derived resources, adapters, code, configs, and runs under authorized `external_executor/workdir/` or output directories.
- Do not modify ResearchOS runtime, config, drafts, or submission paths unless a precise path and operation is explicitly authorized.

## Resource acquisition

Use only the compiled mode:

```text
local_only
github_allowed
github_and_reimplementation
```

Missing or contradictory policy means no network, dataset download, replacement, or reimplementation authority. Ask rather than broaden.

Remote acquisition must pin repository and commit/tag, record source and license, and pass static/security review before execution. An easy-to-run third-party implementation is not automatically an equivalent baseline.

Do not fabricate datasets, benchmark splits, checkpoints, official results, or unavailable source material. Synthetic/toy data is smoke-only.

## Third-party code

Before running third-party code, inspect install/download scripts, subprocesses, credential access, filesystem writes, network behavior, and system modification. Use wrappers, patches, or derived configs; do not make untracked edits to source copies.

Never expose unrelated API keys, tokens, environment variables, SSH material, or user data to third-party scripts or logs.

## Scope controls

Require human review before changing the central hypothesis, core mechanism, task, benchmark, contribution type, required baseline, or novelty boundary. Also pause for new credentials, restricted data, material compute expansion, license uncertainty, or a new writable path.

## Destructive operations

Do not delete original materials, valid historical runs, or unrelated user work. Prefer append-only records, versioned plans, and reversible patches. Cleanup may remove only clearly identified temporary files created by the current operation and only inside allowed paths.
