# Budget and Execution DAG Policy

## Budget dimensions

Declare every dimension provided by the project:

- max refinement rounds;
- max total runs and trials;
- max wall-clock hours;
- max GPU/accelerator hours;
- max monetary cost and currency;
- per-experiment estimates;
- reserve for reruns, failures, and mandatory evidence completion.

Do not treat an unspecified budget as unlimited. Missing mandatory limits block approval.

## Estimation

For each experiment estimate, when known:

```text
number of variants × seeds × repeats × datasets/splits
```

Then estimate compute/time/cost using available resource knowledge. Unknown estimates remain explicit and normally produce a warning or blocker depending on budget criticality.

Do not hide baseline runs, failed-run reserve, hyperparameter tuning, evaluation, preprocessing, or plotting costs outside the plan.

## Prioritization

Recommended order:

1. prerequisites that determine feasibility;
2. required baseline reproduction;
3. ours smoke and implementation validity;
4. central confirmatory comparison;
5. mechanism evidence;
6. robustness/failure evidence required by the claim;
7. diagnostic or exploratory additions.

When budget is insufficient, narrow or mark claims unsupported before spending on optional experiments.

## DAG semantics

A DAG edge means the target experiment cannot be meaningfully approved or interpreted before the source condition is satisfied. Typical dependencies:

- baseline formal comparison depends on valid reproduction;
- ours formal run depends on smoke and code/protocol review;
- ablation depends on a validated implementation and working control switches;
- module attribution depends on main and ablation evidence;
- a confirmatory follow-up depends on a versioned diagnostic decision.

The DAG is not the Skill call graph.

## Parallel groups

Experiments may be parallel only when:

- no dependency exists between them;
- they use a locked shared protocol;
- they do not require a shared unresolved choice;
- compute, storage, data access, and rate limits permit it;
- parallel execution does not create fairness differences.

## Early stop

Predeclare experiment-level rules such as invalid loss, resource exhaustion, convergence, or safety. Also preserve root-level stop conditions such as budget exhaustion, plateau, unavailable baseline, implementation block, claim narrowing, and human review.

Early stop cannot be introduced after observing an unfavorable result unless the affected run is marked diagnostic/unusable and rerun under a new protocol.
