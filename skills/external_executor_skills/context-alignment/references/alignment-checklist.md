# Alignment Checklist

Use this checklist before assigning the final gate.

## Preflight

- Required control files exist and parse.
- Handoff and expected-output major versions are supported by `preflight_context.py`.
- `result_pack.json` exists before `apply_alignment_report.py`; checkpoint/final result-pack schema validation is handled by the root `research-execution` validators, not by context preflight.
- Allowed paths contain usable workspace-local entries.
- `context_reboost`, `method_intent`, acquisition policy, minimum loop, and claim boundary structures are present.
- Required baseline names are internally consistent with the baseline matrix.
- Requested acquisition mode is authorized and compatible with declared capabilities.

## Control plane

- Read/write boundaries are explicit.
- Forbidden paths remain forbidden.
- Result-pack required keys and status enums are understood.
- Network, domain, dataset-download, reimplementation, replacement, credential, and license policies are explicit.
- Iteration budget and stop conditions are explicit or recorded as missing.
- Installed/available child skills and critical tools are sufficient for the next phase.

## Research semantics

- Project goal is executable rather than merely topical.
- Central hypothesis is one testable statement or a clearly enumerated set.
- Core mechanism and must-preserve components are explicit.
- Candidate components are not mislabeled as mandatory contributions.
- Allowed refinements and forbidden silent changes are distinct.
- Method intent remains `draft_intent_only`, not a final-method source.

## Experiment contract

- Required and optional baselines are distinguishable.
- Baseline identity and replacement policy are clear.
- Benchmark, dataset, split, preprocessing, metric direction, and protocol are known enough for resource planning.
- Minimum experiment loop is feasible in principle.
- Claim-evidence matrix covers each required claim or marks it unsupported.
- Formal, diagnostic, exploratory, smoke, and toy evidence cannot be confused.

## Claim and novelty boundary

- Strong, moderate, weak, and unsupported claim conditions are understood.
- Must-not-claim items are explicit.
- Novelty-required baselines are not omitted.
- Writer handoff contract is present.
- Any source gap that caps confidence is recorded.

## Capability fit

- Network-dependent modes match actual network authority.
- Dataset access matches legal/technical access.
- Reimplementation mode matches explicit permission.
- Required output formats and schema versions are supported.
- Write paths and storage are sufficient.
- Compute/time budget can support at least the minimum loop, or the gap is blocking.

## Gate self-check

Assign `pass` only with no mismatch records. Assign `mismatch` only when every mismatch is non-blocking and resolved with a documented constraint. Assign `blocked` when any material/blocking issue is unresolved or minimum-loop authority/feasibility is missing.
