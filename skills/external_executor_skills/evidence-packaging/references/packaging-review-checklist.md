# Evidence Packaging Review Checklist

## Snapshot

- One snapshot ID/fingerprint across all package files.
- Snapshot source sections unchanged since pinning.
- Manifest checksums verified.
- Active evidence uses one compatible protocol or explicitly separated protocols.
- Stale, failed, smoke, small-scale, and excluded evidence preserved but not promoted.

## Realized method

- Final method is reconstructed from actual code/config, not copied from intent.
- Method name, mechanism, algorithm flow, and losses are actual.
- Implemented/dropped/added/modified modules are complete.
- Every implemented module has code and config mapping.
- Method definition is separate from empirical support.
- Controlled evidence is separate from correlation or implementation facts.
- Delta from intent exposes contribution drift.
- Claim boundary remains pre-T7.

## Framework figure

- Nodes correspond only to implemented final modules.
- Edges correspond to actual algorithm/data/control relations.
- Visual emphasis reflects support status honestly.
- Dropped or unsupported content appears in `must_not_show` or neutral form.
- Caption is bounded and does not approve claims.
- Editable source and rendered file exist when status is ready.
- Render matches the current spec and snapshot.

## Result figures/tables

- The numeric chain is inspectable from raw result to normalized long table, aggregate table, plotting script, SVG, and caption.
- Every consumed raw result is present in the pinned manifest artifact set and still matches its snapshot checksum.
- Every ready visual has source result, source data, config, log, metric output, plot script, protocol, and render.
- Values, metric direction, aggregation, uncertainty, units, and seed count match sources.
- Different metrics, directions, and protocol fingerprints are never mixed on one comparison axis.
- Reruns remove obsolete generated tables/figures rather than leaving stale final assets.
- Required missing visuals are explicit.
- Stale visuals are not active.
- Captions do not overstate evidence.

## Mapping and manifest

- Module, visual, and claim candidate mappings are bidirectional.
- Package files have identities/checksums and explicit relationships.
- Missing files are not represented by fabricated paths.
- Large raw artifacts remain referenced rather than silently copied.

## Boundary

- No T7 claim approval.
- No T8 prose or manuscript edits.
- No code/config/result modification.
- No executor status, global manifest, route, budget, or iteration decision modification.

## Verdict

- `pass`: package can continue to writer handoff/T7 pre-audit assembly.
- `needs_fix`: package is coherent but has repairable mapping/render/provenance defects.
- `blocked`: snapshot or core evidence is incoherent, unavailable, fabricated, or cross-protocol.
