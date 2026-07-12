# Figure and Table Inventory Contract

## Purpose

The inventory is a traceability ledger, not a list of attractive images. It includes ready, partial, missing, blocked, and stale artifacts.

## Item fields

```json
{
  "artifact_id": "",
  "kind": "framework_figure|figure|table",
  "title": "",
  "status": "ready_for_T7_audit|partial|missing|blocked|stale",
  "evidence_layer": "method_definition|main|mechanism|robustness|limitation|diagnostic|exploratory",
  "claim_ids": [],
  "source_result_refs": [],
  "source_data_refs": [],
  "config_refs": [],
  "log_refs": [],
  "metric_output_refs": [],
  "plot_script_refs": [],
  "protocol_fingerprint": "",
  "evidence_level": "",
  "numeric_traceability": false,
  "editable_source": null,
  "rendered_files": [],
  "caption_draft": null,
  "must_not_imply": [],
  "notes": []
}
```

## Ready result visual

A non-framework result figure/table is ready only when it has:

- active non-stale source result;
- structured source data or source table;
- config reference;
- raw log reference;
- metric output reference;
- plot/render script;
- protocol fingerprint;
- rendered file with checksum;
- bounded caption;
- numeric traceability.

A LaTeX table manually copied from notes is not ready unless its numeric source is traceable.

## Evidence layers

- `main`: central formal comparison;
- `mechanism`: controlled ablation/diagnostic;
- `robustness`: settings, seeds, datasets, perturbations, sensitivity;
- `limitation`: failure cases, negative results, boundary;
- `diagnostic`: engineering or research diagnosis;
- `exploratory`: hypothesis-generating only;
- `method_definition`: architecture/framework only.

## Missing entries

When a required experiment/claim has no traceable visual, add an inventory item with:

```text
status=missing
numeric_traceability=false
must_not_imply=[claim support until source/result/render lineage exists]
```

Do not omit the entry and do not create a fake output path.

## Stale entries

Retain stale visuals with their old snapshot/protocol and provenance. They cannot appear in the active main package or support current claim candidates.

## Numeric precision

Precision, units, metric direction, uncertainty, sample/seed count, and aggregation must match the structured source. Formatting changes are allowed; value changes require regeneration from the source.
