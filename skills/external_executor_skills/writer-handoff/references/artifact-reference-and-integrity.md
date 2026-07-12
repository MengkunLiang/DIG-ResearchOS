# Artifact Reference and Integrity

## Canonical artifact reference

Prefer:

```json
{
  "artifact_id": "ART-...",
  "path": "external_executor/...",
  "sha256": "...",
  "size_bytes": 123,
  "producer": "...",
  "created_at": "...",
  "evidence_level": "..."
}
```

Workspace-relative path is mandatory for local files. A reference may also be an upstream stable ID when the underlying artifact path is separately indexed.

## Path rules

Resolve canonical paths under the workspace. Reject path escape and symlink escape. Do not follow an artifact into unapproved directories. Never rewrite missing files with placeholders.

## Integrity rules

When `sha256` or `size_bytes` is declared, verify it. Formal-result and method-package checksum mismatch is blocking for that item. Optional preview/render files can be partial if editable/source data remains valid.

## Manifest relationship

The root manifest is authoritative for registered artifacts. The child may detect absent or conflicting entries but does not mutate the manifest. Root registers handoff artifacts after child validation.

## Fingerprints

The handoff snapshot fingerprint covers relevant upstream JSON content and manifest identity. It does not replace file checksums.
