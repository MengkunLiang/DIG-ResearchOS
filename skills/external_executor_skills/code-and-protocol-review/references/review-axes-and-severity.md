# Review Axes and Severity

## Review axes

| Axis | Core question |
| --- | --- |
| `spec_alignment` | Does the implementation faithfully realize the approved method/spec without missing or extra behavior? |
| `code_correctness` | Is the code technically credible, testable, and correctly wired? |
| `protocol_fairness` | Are ours and baselines compared under a fair, declared protocol? |
| `data_integrity` | Is leakage or contamination prevented? |
| `reproducibility` | Can commands, configs, states, logs, versions, and outputs be recovered? |
| `security_and_paths` | Are paths, secrets, third-party code, subprocesses, and destructive operations controlled? |
| `contribution_drift` | Did implementation materially change the research contribution or novelty boundary? |

## Severity

```text
info      observation with no required action
warning   bounded risk; may allow smoke/small-scale with a recorded constraint
major     fixable defect that blocks the requested approval level
blocking  authority, safety, material drift, or fairness defect that cannot be repaired inside current scope
```

Severity describes gate impact, not rhetorical intensity.

## Finding status

```text
open | fixed_and_verified | accepted_constraint | false_positive | deferred_by_human
```

Only `fixed_and_verified`, evidence-backed `false_positive`, or explicitly authorized `deferred_by_human` close a major/blocking finding. A warning may be `accepted_constraint` when downstream limits are explicit.

## Repair owner

| Finding type | Owner |
| --- | --- |
| Baseline reproduction/config defect | `baseline-reproduction` |
| Method specification or allowed refinement | `method-refinement` |
| Code, config, tests, adapter, logging | `implementation` |
| Experiment matrix, seed policy, metric plan | `experiment-design` |
| Scope, authority, major drift, human gate | `research-execution` |

Reviewers diagnose and route; they do not repair.

## Gate effects

- Open blocking finding -> `review_status=blocked`, `approved_for=none`.
- Open major finding -> `review_status=needs_fix`; approval cannot include the affected run level.
- Warning may allow a lower run level only when the report records the constraint.
- Formal approval requires no open warning that affects fairness, leakage, metric validity, provenance, or claim interpretation.
- Missing evidence lowers approval; confidence alone cannot raise it.
