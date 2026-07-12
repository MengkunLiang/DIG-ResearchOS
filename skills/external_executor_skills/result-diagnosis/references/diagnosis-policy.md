# Diagnosis Policy

## Role

`result-diagnosis` is a Phase E1 analyst. It converts one immutable iteration evidence snapshot into descriptive facts, bounded interpretations, claim implications, and evidence requests.

It does not:

- run or repair experiments;
- edit code, config, metrics, logs or protocol;
- perform module-level causal attribution;
- choose the root iteration decision;
- update claim boundaries;
- approve final claims or paper language.

## Preconditions

Required:

- non-blocking context alignment;
- a versioned experiment plan and protocol fingerprint;
- an identifiable iteration;
- at least one terminal experiment run;
- recoverable metric direction and method role;
- durable result-pack and run evidence.

A diagnosis may complete honestly with `partial` or `blocked` even when the scientific evidence is insufficient.

## Workflow

```text
preflight
→ pin evidence snapshot
→ normalize observations
→ aggregate repeats
→ compare methods
→ detect anomalies/confounds
→ compile deterministic facts
→ interpret settings and claim implications
→ compute gate
→ validate and narrow apply
```

## Evidence-first sequence

Do not begin with a narrative. First establish:

1. which runs exist;
2. which are usable for which purpose;
3. whether comparable groups share protocol and fairness fingerprints;
4. which metrics and directions are authoritative;
5. how many repeats/seeds exist;
6. which baselines are mandatory and present;
7. which anomalies limit interpretation.

Only then write conclusions.

## Per-iteration history

Each iteration receives one stable `diagnosis_id`. Re-running with the same semantic inputs may replace that same ID; a changed evidence fingerprint produces a new diagnosis version or marks the old one stale through the root workflow. Never delete earlier diagnosis records automatically.

## Resume

Reuse deterministic artifacts only when their input fingerprints match:

- snapshot depends on current run records, plan, claims, reviews and baseline reproduction;
- observations depend on snapshot;
- aggregates depend on observations and comparability policy;
- comparisons depend on aggregates/observations and practical thresholds;
- anomalies depend on the whole deterministic evidence surface;
- the report depends on all above.

A new run, changed metric value, changed protocol/fairness fingerprint, changed baseline status or changed claim mapping invalidates the diagnosis.

## No-op behavior

When all terminal runs for the iteration are excluded, still produce:

- a snapshot listing exclusions;
- a blocked report;
- blocking issues;
- recovery recommendations.

Do not fabricate an empty “successful” diagnosis.
