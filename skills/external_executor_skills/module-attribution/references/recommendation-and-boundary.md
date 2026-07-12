# Recommendation and Boundary Contract

## Recommendation vocabulary

### `keep`

Evidence supports retaining the module in the next local implementation surface. This is not a final method decision.

### `modify`

Evidence suggests the module may matter, but its implementation, weighting, stability, interaction or confounds need correction.

### `drop`

Valid intervention evidence indicates the module is harmful or consistently negligible, and dropping it would not be a major unapproved scope change. Otherwise request root review.

### `narrow`

The module/mechanism works only under a bounded setting, subset, distribution or protocol. Suggest narrowing the empirical boundary, not editing it directly.

### `collect_evidence`

Current evidence cannot discriminate module effect, mechanism or interaction.

## Required fields

```json
{
  "recommendation_id": "REC-...",
  "target_type": "module|mechanism|claim_boundary",
  "target_id": "",
  "action": "keep|modify|drop|narrow|collect_evidence",
  "summary": "",
  "conditions": [],
  "evidence_refs": [],
  "counterevidence_refs": [],
  "confidence": "high|medium|low|insufficient",
  "root_review_required": false
}
```

## Scope boundary

Set `root_review_required=true` when the recommendation would:

- remove or replace the core mechanism;
- change task, benchmark or contribution type;
- invalidate a required claim or required baseline relation;
- turn the method into an existing baseline variant;
- require new permissions, data or resources;
- materially change the novelty surface.

The recommendation remains advisory until the root records an iteration decision or scope-change request.
