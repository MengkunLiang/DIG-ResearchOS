# Module Attribution Output Contract

## Child-owned artifacts

```text
external_executor/report/module_attribution_preflight.json
external_executor/report/module_attribution_snapshot.json
external_executor/report/module_attribution_facts.json
external_executor/module_attribution_report.json
external_executor/report/module_attribution/**
```

The root owns manifest registration, executor status, scope gates and iteration decisions.

## Report envelope

```json
{
  "schema_version": "module_attribution_report.v1",
  "child_skill": "module-attribution",
  "status": "complete|partial|blocked|failed",
  "generated_at": "",
  "attribution_id": "",
  "iteration_id": "",
  "diagnosis_id": "",
  "input_fingerprint": "",
  "evidence_snapshot": {"status": "complete|partial|blocked|stale", "ref": ""},
  "module_registry": {"status": "complete|partial|blocked|stale", "items": []},
  "mechanism_registry": {"status": "complete|partial|blocked|stale", "items": []},
  "intervention_effects": {"status": "complete|partial|blocked|stale", "items": []},
  "interaction_effects": {"status": "complete|partial|blocked|stale", "items": []},
  "module_attributions": {"status": "complete|partial|blocked|stale", "items": []},
  "mechanism_attributions": {"status": "complete|partial|blocked|stale", "items": []},
  "baseline_module_attributions": {"status": "complete|partial|blocked|stale", "items": []},
  "confounds": {"status": "complete|partial|blocked|stale", "items": []},
  "recommendations": {"status": "complete|partial|blocked|stale", "items": []},
  "unsupported_questions": {"status": "complete|partial|blocked|stale", "items": []},
  "risks": {"status": "complete|partial|blocked|stale", "items": []},
  "attribution_gate": {
    "status": "ready_for_iteration_decision|partial|blocked",
    "evidence_sufficiency": "sufficient|limited|insufficient",
    "beneficial_module_ids": [],
    "harmful_module_ids": [],
    "unsupported_mechanism_ids": [],
    "material_confound_ids": [],
    "blocking_issue_ids": [],
    "direct_evidence_module_ids": [],
    "uncovered_required_module_ids": [],
    "recommendation_counts": {},
    "next_action": "return_for_iteration_decision|add_controlled_evidence|repair_or_rerun|human_review|stop_and_report"
  },
  "artifact_refs": [],
  "notes": []
}
```

Required sections remain present for blocked/failed outcomes.

## Module attribution item

```json
{
  "module_attribution_id": "MAT-...",
  "module_id": "M1",
  "owner_method_id": "ours",
  "empirical_status": "beneficial|neutral|harmful|mixed|implementation_only|unsupported",
  "evidence_type": "direct_ablation|controlled_diagnostic|correlational_hint|implementation_fact|unsupported",
  "tested_settings": [],
  "summary": "",
  "effect_refs": [],
  "evidence_refs": [],
  "counterevidence_refs": [],
  "confound_ids": [],
  "interaction_ids": [],
  "causal_status": "local_intervention_effect|mechanism_consistent|correlational_only|implementation_only|unsupported",
  "confidence": "high|medium|low|insufficient",
  "limitations": []
}
```

## Mechanism attribution item

```json
{
  "mechanism_attribution_id": "MECHATTR-...",
  "mechanism_id": "MECH-...",
  "status": "supported|consistent|weakened|contradicted|unresolved",
  "linked_module_ids": [],
  "evidence_type": "direct_ablation|controlled_diagnostic|correlational_hint|implementation_fact|unsupported",
  "summary": "",
  "alternative_explanations": [],
  "evidence_refs": [],
  "counterevidence_refs": [],
  "confidence": "high|medium|low|insufficient",
  "causal_status": "local_intervention_effect|mechanism_consistent|correlational_only|implementation_only|unsupported",
  "required_evidence": []
}
```

## Gate consistency

### `ready_for_iteration_decision`

Requires:

- at least one relevant module attribution;
- at least one final-method module supported by direct ablation or controlled diagnostic evidence;
- no required implemented module left uncovered;
- every substantive attribution has known evidence refs and confidence;
- direct causal language appears only with eligible intervention evidence;
- material/blocking confounds are propagated;
- central untested mechanisms are listed as unsupported;
- recommendations use the bounded vocabulary.

### `partial`

Useful evidence exists, but central modules, interactions, settings, repeats or mechanism discrimination remain limited.

### `blocked`

No valid intervention/attribution surface exists, intervention identity is ambiguous, or a blocking protocol/fairness/integrity issue remains.

## Result-pack mapping

```text
result_pack.module_attributions = {
  status,
  items: append-or-replace by attribution_id,
  current_by_iteration: {iteration_id: attribution_id}
}
```

Do not write `iteration_decisions`, claim boundaries or realized method fields.
