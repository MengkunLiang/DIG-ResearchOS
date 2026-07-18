# Final Evidence Snapshot and Staleness Policy

## Purpose

F1-F3 products must not independently choose their preferred iteration, protocol, or result subset. A single pinned snapshot is the package's source-of-truth boundary.

## Snapshot contents

The snapshot records:

- hashes of relevant `result_pack` sections;
- executor state and final loop decision context;
- active formal records and protocol fingerprint;
- diagnostic/exploratory records used only for bounded interpretation;
- stale, failed, superseded, excluded, smoke, and small-scale history;
- run-manifest artifact identities, paths, expected checksums, actual checksums, and existence;
- source control-file hashes.
- the selected active implementation and final iteration;
- the matching method-refinement record and immutable method-spec path, parsed value, fingerprint, and file hash;
- the selected implementation review, diagnosis, attribution, decision, and runs bound to the active implementation.

The snapshot does not duplicate large raw artifacts. It identifies and verifies them.

## Active evidence

A record is active formal evidence only when:

1. it belongs to a formal, ablation, robustness, diagnostic, or efficiency run explicitly eligible for the active package;
2. it is not stale, superseded, invalid, unusable, excluded, or failed;
3. its protocol fingerprint is compatible with the package protocol;
4. required config, split, metric, seed/repeat, code/resource version, log, and metric-output provenance exists;
5. root/owning Skill has not excluded it.

Diagnostic runs may inform limitations or mechanism analysis but do not automatically become confirmatory evidence.

## Historical evidence

Preserve but do not promote:

```text
smoke
small_scale
dry_run
toy
synthetic-only
failed
stale
superseded
invalid
unusable
excluded
```

History is useful for recovery, diagnosis, limitations, and audit. Its presence must not imply active claim support.

## Snapshot immutability

After snapshot creation:

- all package components carry the snapshot ID and fingerprint;
- source section hashes are rechecked before final validation;
- the selected method-spec file hash is rechecked before final validation;
- an artifact checksum mismatch blocks the package;
- a changed result-pack section requires a new snapshot;
- adding or replacing a run, config, attribution, figure, or method record requires rebuilding all dependent F1-F3 outputs.

Do not patch a package component to a new evidence state while retaining the old snapshot ID.

Package builders consume the values embedded in this snapshot. They may inspect live files only to verify existence and immutability; they must not silently replace pinned values with newer live result-pack content.

## Protocol consistency

Active evidence must not mix materially different:

- dataset/version/split;
- preprocessing;
- primary metric or direction;
- aggregation/statistical policy;
- baseline identity/config/fairness budget;
- ours config policy;
- evaluation script;
- seed/repeat policy;
- material code or resource version.

When different protocol versions are intentionally shown, they must be separated into clearly labeled non-comparable inventory entries. They cannot be merged into one main comparison.

## Best-effort packaging

Under `partial`, `blocked`, or `failed` executor states:

- freeze whatever valid evidence remains;
- preserve failures, risks, and unavailable components;
- generate a partial method package when definition facts are recoverable;
- mark framework/results missing rather than fabricate;
- provide recovery instructions through constraints and blockers.

Best effort changes completeness, not truth standards.
