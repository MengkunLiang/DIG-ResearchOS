# Evidence Level and Claim Boundary Policy

## Core separation

Use these levels without collapsing them:

| Level | What it establishes | What it does not establish |
| --- | --- | --- |
| `method_definition` | The final code/config contains a module or procedure | The module improves outcomes |
| `implementation_fact` | A concrete path, config, switch, or behavior exists | Empirical efficacy or causality |
| `direct_ablation` | A controlled intervention changes measured outcomes under the declared protocol | Universal causality outside tested settings |
| `controlled_diagnostic` | A targeted controlled test supports a mechanism interpretation | Broad confirmatory superiority |
| `correlational_hint` | Results co-vary with a module, subset, or condition | Causal mechanism |
| `confirmatory_result` | A predeclared formal experiment answers a mapped reviewer question | Claims outside its protocol and boundary |
| `diagnostic_result` | A run diagnoses failure, sensitivity, or mechanism | Primary claim support unless predeclared and reviewed |
| `exploratory_result` | A pattern or hypothesis was discovered | Retrospective confirmatory evidence |
| `unsupported` | Evidence is insufficient, contradictory, unusable, or absent | Falsehood unless evidence directly refutes it |
| `claim_candidate` | A possible statement for T7 to audit | T7 approval or paper-ready claim |

## Support vocabulary

For modules/mechanisms use:

```text
supported
definition_or_hint_only
unsupported
unassessed
```

For package claims use only:

```text
claim_candidate
constrained_candidate
unsupported_candidate
not_audited_by_T7
```

This Skill must never emit `paper_approved`, `publishable`, `T7_pass`, or equivalent semantics.

## Causal language

Causal or mechanism language requires:

- a defined intervention;
- controlled comparison;
- fixed non-target protocol factors;
- appropriate metric/statistical interpretation;
- direct evidence reference;
- explicit tested boundary.

Even direct ablation may show that a component matters without proving the proposed internal causal story. Caption and method text should distinguish “component contribution” from “causal explanation.”

## Negative and null evidence

Preserve:

- no improvement;
- degradation;
- high variance;
- instability;
- failed reproduction;
- unsupported mechanism;
- subset-limited effect;
- fairness or protocol caveat.

These can narrow claims or define limitations. Do not remove them because they weaken the narrative.

## Strongest allowed statement

The strongest package statement is bounded by the weakest relevant link among:

```text
resource fidelity
protocol validity
formal provenance
statistical support
module attribution quality
claim boundary
```

The package may be complete as an artifact even when the research evidence supports only a weak or negative conclusion.
