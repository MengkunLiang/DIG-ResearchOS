# Sanity and Comparability

## Separate dimensions

Judge at least these dimensions independently:

1. **Executability** — process completed and expected outputs exist.
2. **Identity fidelity** — executed code is the approved baseline candidate and variant.
3. **Mechanism fidelity** — repairs/adapters did not change defining behavior.
4. **Protocol fidelity** — dataset, split, preprocessing, metric, aggregation, seeds/repeats, budget, and evaluator match the locked protocol.
5. **Provenance completeness** — command, config, logs, normalized metrics, per-dataset/per-metric raw metric CSV files, environment, versions, and hashes exist.
6. **Reference agreement** — result satisfies the predeclared comparison rule, when a trustworthy reference exists.
7. **Statistical sufficiency** — planned repeats/seeds and aggregation are complete.
8. **Fairness** — no extra data, tuning, pretraining, compute, or favorable evaluation mismatch.

A close number cannot compensate for method or protocol mismatch.

## Acceptance rules

Record before judging:

- `absolute_tolerance`: `abs(observed-reference) <= tolerance`;
- `relative_tolerance`: `abs(observed-reference)/max(abs(reference), epsilon) <= tolerance`;
- `range`: lower <= observed <= upper;
- `minimum`: observed >= value;
- `maximum`: observed <= value;
- `directional`: expected order/trend is present;
- `none`: no numeric paper/reference value; use only documented sanity and protocol checks.

Do not invent precision absent from the source. Paper values may be rounded. Stochastic methods should generally use repeat-aware comparisons rather than exact equality.

## Technical outcomes

- `reproduced_within_tolerance`: complete protocol/provenance and primary metric meets declared numeric/range rule.
- `reproduced_directionally`: complete protocol/provenance and declared structural/directional rule is met, but exact numeric fidelity is not established.
- `partially_reproduced`: some required settings/metrics/repeats are incomplete or a documented approximation remains.
- `executable_only`: command runs and emits outputs, but evidence is not protocol-comparable.
- `failed`: bounded attempt failed technically.
- `unavailable`: required material/access/compute cannot be obtained under authority.
- `blocked`: safety, license, scope, or protocol authority blocks work.

## Comparability states

- `formal_review_candidate`: all required provenance and locked protocol fields are complete; independent reproduction review passes. The later code/protocol reviewer still decides formal approval.
- `conditional_comparison_only`: result may be shown with explicit constraints, but cannot support an unqualified main comparison.
- `smoke_only`: engineering evidence only.
- `not_comparable`: no valid comparison.

## Claim risk

Examples:

- required strongest baseline unavailable;
- only approximate reimplementation is executable;
- paper result cannot be matched under recoverable settings;
- fewer repeats/seeds than protocol;
- hardware or library difference plausibly changes result;
- evaluation metric/split ambiguity;
- repair may alter mechanism;
- reference value comes from a non-equivalent setup.

A claim risk constrains later interpretation; it does not authorize removing the baseline.
