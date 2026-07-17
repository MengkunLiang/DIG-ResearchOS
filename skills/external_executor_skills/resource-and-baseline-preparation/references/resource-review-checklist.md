# Resource Review Checklist

## Review principle

Review the candidate against the requirement and source evidence. Do not inherit the discoverer's conclusion.

## Identity and provenance

- Is the candidate's identity unambiguous?
- Is it official, author-recognized, third-party, or executor-reimplemented?
- Is the paper/project/benchmark link evidenced?
- Is an immutable revision/version recorded?
- Are local path and checksums recorded?
- Are source and acquisition records complete?

## Mechanism fidelity

For baselines:

- Are the defining modules/objective/algorithm present?
- Are model scale and architectural variants identified?
- Are losses and optimization semantics consistent?
- Are training and inference flows aligned?
- Are simplifications or omitted components explicit?

For benchmark/data:

- Is the canonical dataset/benchmark version correct?
- Are label/schema semantics correct?
- Is the official split preserved?
- Is preprocessing compatible?

## Protocol fidelity

- task;
- dataset and version;
- split;
- preprocessing/tokenization/features;
- primary and secondary metrics;
- metric direction;
- averaging/aggregation;
- evaluation script/version;
- seed/repeat expectations;
- checkpoint/pretraining assumptions;
- permitted external data.

A mismatch in a claim-critical field is not a minor documentation issue.

## Fairness

Check:

- extra training data;
- stronger pretraining or checkpoint;
- extra tuning budget;
- different early stopping;
- different evaluation split;
- metric implementation differences;
- parameter/compute mismatch;
- data leakage;
- test-set tuning;
- adapter behavior that benefits one method;
- unavailable baseline defaults replaced with favorable guesses.

## License and access

- license/terms identified;
- intended research use allowed;
- redistribution allowed or prohibited;
- attribution requirements known;
- restricted/sensitive data obligations met;
- checkpoint model license compatible;
- no access control was bypassed.

## Security

- static review completed;
- no unresolved critical finding;
- symlink/submodule/LFS behavior known;
- install and lifecycle hooks identified;
- download commands and external endpoints identified;
- secrets are not embedded;
- later execution constraints are explicit.

## Adapter and patch impact

- patch is stored separately or traceable;
- engineering compatibility change is distinguished from algorithm change;
- changed files and rationale are recorded;
- protocol/fairness impact is assessed;
- patched resource receives a new fingerprint.

## Verdict

```json
{
  "review_id": "REV-...",
  "candidate_id": "CAND-...",
  "requirement_ids": [],
  "verdict": "pass|needs_fix|blocked",
  "identity_fidelity": "exact|high|moderate|low|unknown",
  "mechanism_fidelity": "exact|high|moderate|low|not_applicable|unknown",
  "protocol_fidelity": "exact|high|moderate|low|unknown",
  "fairness_risk": "low|medium|high|blocking|unknown",
  "security_risk": "low|medium|high|blocking|unknown",
  "license_risk": "low|medium|high|blocking|unknown",
  "access_risk": "low|medium|high|blocking|unknown",
  "approximation_level": "none|minor|material|unknown",
  "findings": [],
  "required_fixes": [],
  "evidence_refs": [],
  "executable_baseline_criteria": {
    "accessible_code_or_model": {"status": "pass|missing|unknown", "evidence_refs": []},
    "revision_locked": {"status": "pass|missing|unknown", "evidence_refs": []},
    "license_clear": {"status": "pass|missing|unknown", "evidence_refs": []},
    "environment_or_dependencies": {"status": "pass|missing|unknown", "evidence_refs": []},
    "dataset_version_and_split": {"status": "pass|missing|unknown", "evidence_refs": []},
    "metric_implementation": {"status": "pass|missing|unknown", "evidence_refs": []},
    "traceable_result_record": {"status": "pass|missing|unknown", "evidence_refs": []}
  },
  "approved_for": []
}
```

Allowed `approved_for` values:

```text
static_inspection
smoke_preparation
experiment_design
baseline_reproduction
formal_comparison
dataset_use
metric_use
preprocessing_use
checkpoint_use
none
```

`formal_comparison` requires passing identity, protocol, fairness, license/access, and security review. Phase B approval does not prove executed reproducibility.

For baseline requirements, `baseline_reproduction` and `formal_comparison` also require every `executable_baseline_criteria` field to pass. If any criterion is missing or unknown, the candidate can still be documented, but it is not an executable baseline.
