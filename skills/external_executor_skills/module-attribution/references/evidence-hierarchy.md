# Attribution Evidence Hierarchy

## Evidence classes

### `direct_ablation`

An explicit intervention disables, removes, replaces, or restores a named module while the declared comparison key is held constant.

Minimum requirements:

- explicit module/intervention identity;
- compatible full/reference variant;
- same protocol, data, split, metric, setting and fairness surface;
- paired seed/repeat when the plan supports pairing;
- valid metric evidence and intervention integrity;
- no unresolved blocking confound.

Allowed inference: a bounded local intervention effect in the tested setting. This does not prove the intended mechanism by itself.

### `controlled_diagnostic`

A declared diagnostic intervention targets a mechanism-related quantity or pathway with controlled comparison conditions.

Allowed inference: mechanism-consistent or locally causal evidence when the intervention directly manipulates the proposed mediator and alternatives are addressed. Otherwise remain `consistent`.

### `correlational_hint`

Examples:

- subset performance differences without intervention;
- activation, weight, attention or usage correlation;
- module score correlated with outcome across runs;
- qualitative failure cases associated with module state.

Allowed inference: hypothesis generation only. Causal conclusion is forbidden.

### `implementation_fact`

Code/config evidence that a module, loss, switch or interface exists and is wired.

Allowed inference: method-definition fact only. No empirical contribution claim.

### `unsupported`

Evidence is missing, incomparable, ambiguous, stale, unusable or too confounded.

## Evidence precedence

Higher evidence does not erase lower or contradictory evidence. Preserve:

- direct ablation and diagnostic disagreements;
- setting-specific reversals;
- failed or broken interventions;
- counterevidence;
- implementation facts for untested modules.

## Evidence eligibility

Smoke/toy/synthetic runs may establish intervention plumbing but not scientific module contribution. Small-scale evidence is diagnostic unless the experiment plan explicitly defines its inferential role. Formal-candidate ablations still remain pre-audit.

## Evidence reference

Every substantive attribution cites stable IDs from the snapshot/facts, such as:

```text
RUN-    run evidence
INTV-   normalized intervention observation
EFF-    direct ablation effect
INTER-  supported interaction effect
CONF-   confound record
MOD-    module registry record
MECH-   mechanism registry record
DIAG-   result diagnosis record
CMP-    diagnosis comparison
ANOM-   diagnosis anomaly
```
