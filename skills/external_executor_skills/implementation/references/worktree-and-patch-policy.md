# Worktree and Patch Policy

## Isolation model

Each implementation uses:

```text
<implementation-root>/
  before/       immutable comparison snapshot
  worktree/     only editable code tree
  verification/ durable command evidence
  mappings/     research traceability
  patches/      structured patch and scope evidence
```

The package is an artifact worktree; it need not be a Git worktree and does not imply a commit.

## Base source

The base source must be an authorized workspace-relative path. It may be:

- a previous approved implementation snapshot;
- a controlled project source copy;
- a staged baseline adapter source;
- an approved repair package.

Do not use an unreviewed remote checkout or original resource directory directly.

After the first method implementation, the only valid method base is the immediately preceding implementation package's `worktree/`, as recorded by the root iteration plan. Every debug or refinement receives a new implementation ID and a new `before/worktree` package; prior worktrees remain immutable historical evidence.

## Snapshot rules

- copy regular files only;
- reject symlinks unless project policy explicitly allows and pins them;
- exclude VCS metadata, virtual environments, caches, node modules, previous results, large checkpoints, raw datasets, logs, and temporary outputs by default;
- record every exclusion;
- compute a manifest hash for source, `before/`, and `worktree/`;
- make `before/` read-only after copying;
- never repair by editing `before/`.

## Generated outputs

Generated experiment outputs do not belong in implementation patches. Keep out:

```text
raw_results/
logs/
checkpoints/
figures/
tables/
wandb/
tensorboard/
__pycache__/
.pytest_cache/
```

Small deterministic test fixtures may be included when the contract explicitly permits them.

## Patch bundle

The patch bundle contains:

```json
{
  "schema_version": "implementation_patch_bundle.v1",
  "implementation_id": "",
  "before_manifest_sha256": "",
  "after_manifest_sha256": "",
  "changed_files": [],
  "summary": {},
  "unified_diff_path": "patches/implementation.patch",
  "binary_change_manifest": [],
  "generated_at": ""
}
```

Each changed file records operation, before/after hashes, sizes, text/binary type, line additions/deletions when available, and sensitive-category hints.

## Deletions

Deletion must be explicitly allowed by a change item. Deleting tests, protocol checks, baseline code, or provenance is always review-sensitive and usually blocking unless directly authorized.

## Patch reproducibility

The structured manifest and unified diff are evidence. The system is not required to apply the patch automatically. When applying a patch elsewhere, verify the expected base fingerprint first.

## Git policy

Git may be used read-only to inspect source identity when available. This skill must not commit, push, rewrite history, modify remotes, or open a pull request unless a separate explicit instruction grants that authority.
