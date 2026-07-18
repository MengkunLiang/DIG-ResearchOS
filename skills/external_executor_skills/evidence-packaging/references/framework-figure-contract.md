# Final Framework Figure Contract

## Source hierarchy

The figure is derived from:

```text
realized_method_package
→ final code/config structure
→ module attribution
→ current claim boundary
```

The initial framework sketch in `method_intent` is only a comparison input. It must not override the realized method.

The final rendered asset is `external_executor/figure/framework_figure.svg`. The machine-readable spec and editable Mermaid source are process artifacts under `external_executor/report/phase_F/`.

## Required fields

```json
{
  "schema_version": "framework_figure_spec.v1",
  "snapshot_id": "",
  "snapshot_fingerprint": "",
  "status": "ready_for_T7_audit|missing|blocked",
  "figure_id": "",
  "main_message": "",
  "panels": [],
  "nodes": [],
  "edges": [],
  "caption_draft": null,
  "editable_source": null,
  "rendered_files": [],
  "must_not_show": [],
  "evidence_mapping": {},
  "unresolved_fields": []
}
```

## Node rules

Every method node must identify:

- realized module ID;
- label and actual role;
- code refs;
- config keys;
- definition status;
- empirical-support status;
- evidence refs;
- visual emphasis;
- `must_not_imply` boundary.
- linked pre-T7 claim candidate IDs.

A node may be visually central because it is structurally central. It may be highlighted as empirically supported only when controlled evidence exists.

## Edge rules

Every edge states an actual relation:

```text
data_flow
control_flow
training_only
inference_only
shared_state
loss_dependency
conditioning
aggregation
declared_algorithm_order
```

Do not invent an edge to make the diagram aesthetically complete. If relationships cannot be recovered, use `blocked` and list the missing mapping.

## Panel rules

Each panel answers one purpose:

- actual method architecture;
- training/inference distinction;
- evidence-supported mechanism;
- optional implementation detail needed to understand the method.

Do not add a result panel merely because the page has space. Result visuals belong in the result inventory.

## `must_not_show`

Include:

- dropped modules;
- unimplemented intent components;
- stale architecture;
- unsupported mechanism presented as validated;
- unauthorized scope changes;
- hidden baseline components represented as ours;
- qualitative examples implying unsupported generality.

## Caption

The caption may state:

- what the implemented modules do;
- actual data/control flow;
- which visual notation denotes definition versus controlled support;
- tested boundary and audit status.

It must not state un-audited superiority, novelty, causal mechanism, or universal benefit.

## Status

- `ready_for_T7_audit`: spec, mapping, editable source, and rendered file exist and match the snapshot.
- `missing`: no truthful figure can be specified from available method evidence.
- `blocked`: a partial figure exists, but material node/edge/mapping or forbidden-content issue prevents safe use.
