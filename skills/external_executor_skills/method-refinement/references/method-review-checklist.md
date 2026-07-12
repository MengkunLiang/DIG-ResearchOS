# Method Refinement Review Checklist

## Intent alignment

- Central hypothesis and contribution type match the confirmed scope.
- Core mechanism is unchanged unless an approved scope decision exists.
- Every must-preserve component is represented and functional.
- Candidate components are not silently promoted to core contribution.
- Claim boundary is preserved or narrowed, never broadened silently.

## Implementability

- Modules have stable IDs, purposes, inputs, outputs, invariants, config keys, tests, and failure modes.
- Losses/objectives have operational definitions and coefficient controls.
- Training and inference flows are ordered and complete.
- Data/preprocessing/evaluation interfaces bind to the protocol.
- Implementation targets are specific enough for the next Skill.
- Unresolved decisions are explicit and classified.

## Evidence readiness

- Claim–mechanism–module–control–experiment traceability is complete.
- Core modules expose controlled ablation or a justified alternative.
- Diagnostic hooks do not alter the formal method silently.
- Expected logs, configs, checkpoints, and raw outputs are specified.

## Fairness and contribution hygiene

- Non-contribution tricks are separately labeled.
- Extra data, pretraining, compute, tuning, and preprocessing advantages are controlled.
- Baseline adapters and ours-specific wrappers do not create hidden asymmetry.
- Protocol fingerprint and plan version match the active experiment plan.

## Scope and authorization

- Every change has a delta class and rationale.
- Minor claim effects reference a root iteration decision.
- Major drift produces a pending scope-change request.
- No implementation approval is issued while major drift or missing authority remains.

## Verdicts

- `pass`: complete, traceable, protocol-compatible, no major drift; `approved_for=implementation`.
- `needs_fix`: repairable omissions, ambiguity, or minor authorization/plan alignment issue; `approved_for=none`.
- `blocked`: major drift, missing authority, incompatible protocol, or absent core intent; `approved_for=none`.
