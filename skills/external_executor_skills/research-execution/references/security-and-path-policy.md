# Security and Path Policy

Use the stricter rule when this reference, `AGENTS.md`, handoff policy, and `allowed_paths.txt` differ.

## Path controls

- Resolve the workspace root before any write.
- Normalize every path and resolve existing symlinks.
- Reject absolute targets outside the workspace and relative paths containing an escape.
- Reject symlink-based escape from an allowed directory.
- Do not search `external_executor/expr/` for baseline, benchmark, dataset, checkpoint, or evaluation resources. Treat it as the formal execution area after resources, baselines, and the method are prepared; write there only when the owning execution/build step explicitly authorizes it.
- Place by-hand local resources under `resources/`.
- Place public remote acquisitions and baseline reimplementations under `resource/`.
- Place deployable baseline code, method code, adapters, and runnable configs under authorized subdirectories of `external_executor/expr/`.
- Place raw run evidence, including logs, metrics, run records, checkpoints, environment snapshots, and produced experiment outputs, under `external_executor/raw_results/`.
- `external_executor/workdir/` is a legacy workspace path only. Current execution starts from the workspace root, deploys runnable method/baseline code under `external_executor/expr/`, and writes child analysis artifacts only to paths explicitly listed in `external_executor/allowed_paths.txt`.
- Do not modify ResearchOS runtime, config, drafts, or submission paths unless a precise path and operation is explicitly authorized.

## Resource acquisition

Use only the compiled mode:

```text
local_only
github_allowed
github_and_reimplementation
```

For ResearchOS T5 external execution, missing policy in a legacy handoff resolves to the default policy: public GitHub access, public dataset download, and baseline reimplementation are allowed inside `allowed_paths.txt`, with license/security review. Contradictory policy still blocks; ask rather than broaden beyond the explicit default.

Remote acquisition must pin repository and commit/tag, record source and license, and pass static/security review before execution. An easy-to-run third-party implementation is not automatically an equivalent baseline.

Do not fabricate datasets, benchmark splits, checkpoints, official results, or unavailable source material. Synthetic/toy data is smoke-only.

## Third-party code

Before running third-party code, inspect install/download scripts, subprocesses, credential access, filesystem writes, network behavior, and system modification. Use wrappers, patches, or derived configs; do not make untracked edits to source copies.

Never expose unrelated API keys, tokens, environment variables, SSH material, or user data to third-party scripts or logs.

## Scope controls

Require human review before changing the central hypothesis, core mechanism, task, benchmark, contribution type, required baseline, or novelty boundary. Also pause for new credentials, restricted data, material compute expansion, license uncertainty, or a new writable path.

## Destructive operations

Do not delete original materials, valid historical runs, or unrelated user work. Prefer append-only records, versioned plans, and reversible patches. Cleanup may remove only clearly identified temporary files created by the current operation and only inside allowed paths.
