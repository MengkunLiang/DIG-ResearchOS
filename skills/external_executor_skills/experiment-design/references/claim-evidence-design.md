# Claim–Evidence Design

## Core rule

Start from the claim, not from a conventional list of tables. A valid design chain is:

```text
claim
→ reviewer question
→ evidence needed
→ controlled experiment
→ expected artifact
→ predeclared interpretation
```

The chain is incomplete when an experiment has no named claim, a claim has no reviewer question, or the expected result cannot distinguish support, refutation, and inconclusive evidence.

## Claim records

Each claim record contains:

- `claim_id` and exact statement;
- whether the claim is required for the paper's central contribution;
- target strength and analysis role;
- one or more reviewer questions;
- evidence needed;
- planned experiment IDs;
- interpretation constraints and must-not-claim boundaries;
- source references;
- `planned` or `unsupported` status.

A required claim may be declared unsupported. This is preferable to adding weak, unrelated experiments. Unsupported claims must carry a reason and be propagated to T7/T8 claim restrictions.

## Reviewer-question families

Use project-specific questions. Common families are navigation aids, not automatic content:

- **Effectiveness:** Does the method improve the primary outcome under the stated benchmark?
- **Mechanism:** Is the effect caused by the proposed mechanism rather than capacity, tuning, preprocessing, or extra data?
- **Fairness:** Are baselines compared with the same task, split, metric, budget, information, and tuning opportunity?
- **Generalization:** Does the effect survive the distributions and settings named by the claim?
- **Robustness:** Does it survive perturbation, sensitivity, scale, or deployment variation relevant to the claim?
- **Failure boundary:** Where does the method fail, and does that contradict or narrow the claim?
- **Efficiency:** Is a claimed cost, latency, memory, or resource advantage measured under a controlled protocol?
- **Reproducibility:** Can a reviewer trace the claim to config, code, raw log, metric output, seed, split, and version?

Do not add a family that the claim does not require.

## Evidence roles

### Confirmatory

Predeclared before the relevant result is observed. It tests a central or required claim under a locked protocol. Primary metrics, direction, aggregation, seeds/repeats, comparisons, and interpretation rules are fixed.

### Diagnostic

Used to understand implementation behavior, anomalies, failure modes, or mechanisms. Diagnostic findings may motivate a later versioned confirmatory experiment, but cannot be retroactively promoted.

### Exploratory

Searches for patterns, promising settings, or new hypotheses. It must be labeled exploratory and cannot support the original confirmatory claim without a new protocol and independent evidence.

## Main comparisons

A main comparison must specify:

- required baseline identities and resource references;
- ours and baseline variants;
- shared dataset, split, preprocessing, evaluation, and budget rules;
- primary metric and direction;
- seed/repeat and uncertainty policy;
- what result supports, weakens, refutes, or leaves the claim inconclusive.

A main table is an output form, not an experiment definition.

## Ablation design

Ablations test mechanisms. Record:

```text
mechanism or assumption
→ intervention target
→ action: REMOVE | REPLACE | ADD | FREEZE | MATCH_CAPACITY | CONTROL
→ controlled replacement/value set
→ metric and subset affected
→ expected observation if supported
→ expected observation if not supported
→ confounds held fixed
```

Useful intervention types include:

- remove or neutralize a module;
- replace a specialized component with a generic or matched-capacity alternative;
- vary a mechanism-defining parameter over predeclared values;
- isolate extra data, pretraining, capacity, compute, or preprocessing advantages;
- test a subset/distribution where the mechanism predicts a different effect;
- compare ours module to the closest baseline module under a shared wrapper.

Deleting a component without controlling capacity, compute, or interface changes may not isolate the mechanism.

## Failure, robustness, and sensitivity

Add these only when they answer a claim-linked question. Define in advance:

- perturbation or setting;
- severity/value grid;
- expected invariant or failure boundary;
- metric and aggregation;
- whether the experiment is confirmatory or diagnostic;
- how a failed robustness test changes the claim.

## No-fabrication and no-post-hoc rules

- Never infer a result, effect size, ranking, variance, or significance while designing.
- Never choose a primary metric because it is expected to favor ours.
- Never add a subset after observing that it looks favorable and call it confirmatory.
- Never drop a failed seed or run without a predeclared, evidence-backed failure rule.
- Never use an ablation to test a module that is not in `method_intent` or an approved implementation delta.
