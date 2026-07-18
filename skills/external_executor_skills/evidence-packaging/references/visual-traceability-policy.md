# Visual and Numeric Traceability Policy

## Required lineage

For every result number shown in a figure or table:

```text
formal/eligible run record
→ raw log or metric output
→ normalized long-form table (`external_executor/table/all_results.csv`)
→ protocol-preserving aggregate table
→ deterministic plotting or table-generation script
→ editable visual/table source
→ rendered artifact
→ caption and claim candidate
```

Each link must be inspectable through paths, stable IDs, checksums, or structured record references.
Only raw result files pinned in `final_evidence_snapshot.json` may contribute numbers. If a current checksum differs from the pinned checksum, skip the source and regenerate the snapshot only through the root workflow.

## No manual numeric edits

Forbidden:

- editing a rendered SVG/PDF/PNG number without changing the source data and rerunning the script;
- copying values from a screenshot;
- correcting a table cell only in LaTeX;
- hiding failed seeds without a predeclared exclusion rule;
- changing units, signs, metric direction, or aggregation during formatting;
- reporting the best seed as the main aggregate unless predeclared;
- combining incompatible protocols in one comparable row/series.

A style-only edit is allowed when it does not change data geometry, labels, values, uncertainty, ordering semantics, or scientific meaning. Preserve the editable source and rerender.

## Source data

Prefer a small immutable source table for each visual. It should contain or reference:

- row/series identity;
- dataset/split/subset;
- method/config;
- seed/repeat;
- metric and direction;
- raw values and aggregation;
- uncertainty/statistics;
- run IDs;
- protocol fingerprint.

## Plot scripts

Plot scripts must:

- read source data rather than embed unexplained final numbers;
- write deterministic output paths;
- state required dependencies;
- preserve units and metric direction;
- expose sorting/filtering/exclusion logic;
- group comparisons by dataset, split, metric, metric direction, and protocol fingerprint;
- render different metrics, directions, or protocol fingerprints on separate axes/files;
- avoid network access during rendering unless explicitly authorized;
- not mutate the source result artifact.

The filenames `main_*.svg`, `ablation_*.svg`, and `other_*.svg` are reserved for the deterministic result renderer. On rerun, replace or remove its prior outputs so unsupported stale visuals cannot remain active.

## Captions

A caption is part of the evidence boundary. It should state what is compared, setting/split, metric/direction, aggregation/uncertainty, and any limitation needed to avoid overinterpretation. It must not repair missing provenance with prose.
