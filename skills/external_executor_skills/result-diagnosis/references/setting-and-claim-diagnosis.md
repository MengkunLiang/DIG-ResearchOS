# Setting and Claim Diagnosis

## Strongest baseline

Identify the strongest eligible baseline separately for every comparable setting and primary metric. Record:

- baseline ID and candidate/reproduction reference;
- aggregate and repeat count;
- ranking rule and metric direction;
- margin to the next baseline;
- stability and anomaly status;
- confidence and evidence refs.

Do not report one global strongest baseline when settings or metrics differ materially.

## Setting diagnosis

Each setting record contains:

```json
{
  "setting_diagnosis_id": "SETDIAG-...",
  "setting_key": {},
  "primary_metric": "",
  "finding": "ours_wins|ours_loses|tie_or_practical_equivalence|mixed|inconclusive|incomparable",
  "comparison_ids": [],
  "summary": "",
  "interpretation_level": "observed_fact|descriptive_inference|plausible_hypothesis|unsupported",
  "confidence": "high|medium|low|insufficient",
  "evidence_refs": [],
  "limitations": []
}
```

A single favorable setting does not imply general superiority. A failure setting is preserved even when the average is favorable.

## Claim implications

Map only to existing claim IDs from the claim–evidence matrix.

```json
{
  "claim_implication_id": "CLIMP-...",
  "claim_id": "",
  "status": "supported|weakened|contradicted|unresolved|not_tested",
  "pre_audit_strength": "strong|moderate|weak|none",
  "summary": "",
  "evidence_refs": [],
  "counterevidence_refs": [],
  "confidence": "high|medium|low|insufficient",
  "conditions": [],
  "must_not_infer": [],
  "required_evidence": []
}
```

“Supported” means supported by the current pre-audit evidence under stated conditions. T7 may still reject or downgrade it.

## Claim status guidance

- `supported`: planned evidence is directionally and practically consistent, required comparison exists, and no material contradiction remains;
- `weakened`: some support exists but effect, coverage, stability or fairness is limited;
- `contradicted`: valid evidence directly conflicts with the claim under its stated scope;
- `unresolved`: evidence is mixed, insufficient or materially confounded;
- `not_tested`: the necessary experiment was not run or unusable.

## Evidence requests

Requests should be concrete:

- missing baseline/setting/seed;
- controlled capacity match;
- repeated run;
- subset diagnostic;
- metric implementation check;
- protocol repair;
- targeted ablation for the later attribution stage.

Do not use a request as a hidden decision to change the task or benchmark.
