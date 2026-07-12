# method-refinement file manifest

## Purpose

This Skill compiles ResearchOS `method_intent` plus a root-authorized iteration change into an implementation-ready, versioned method contract. It detects contribution drift before code is written and hands a validated specification to `implementation`.

## Files

| Path | Responsibility |
| --- | --- |
| `SKILL.md` | Runtime workflow, ownership, gates, commands, and return contract. |
| `references/method-intent-contract.md` | Normalized source-of-truth and authority rules. |
| `references/implementation-spec-contract.md` | Complete implementation-spec schema and semantics. |
| `references/refinement-and-delta-policy.md` | Allowed refinement classes, versioning, and delta requirements. |
| `references/scope-drift-and-escalation.md` | Major drift triggers and human-review request contract. |
| `references/evidence-traceability.md` | Mapping from research claims to executable and testable controls. |
| `references/method-review-checklist.md` | Independent review axes and verdict semantics. |
| `references/output-contract.md` | Standalone artifacts and narrow result-pack ownership. |
| `scripts/_common.py` | Stdlib-only filesystem, JSON, fingerprint, and extraction helpers. |
| `scripts/preflight_method_refinement.py` | Phase prerequisites, active iteration authorization, and write-boundary checks. |
| `scripts/normalize_method_intent.py` | Normalize method intent and confirmed scope into a stable contract. |
| `scripts/build_method_implementation_spec.py` | Build the implementation specification and initial change log. |
| `scripts/fingerprint_method_spec.py` | Canonical fingerprint and immutable versioned snapshot. |
| `scripts/compare_method_specs.py` | Compute field/module/flow/config deltas and preliminary severity. |
| `scripts/assess_scope_change.py` | Apply drift policy and generate scope-change request data. |
| `scripts/validate_method_implementation_spec.py` | Validate schema, module contracts, traceability, and protocol compatibility. |
| `scripts/review_method_refinement.py` | Produce pre-implementation pass/needs-fix/blocked verdict. |
| `scripts/render_implementation_brief.py` | Render a concise implementation navigation artifact. |
| `scripts/assemble_method_refinement_report.py` | Assemble validated artifacts into the child report. |
| `scripts/validate_method_refinement_report.py` | Enforce report/verdict/scope consistency. |
| `scripts/apply_method_refinement_report.py` | Narrowly update `method_refinements` and owned scope requests. |
| `tests/test_method_refinement_scripts.py` | End-to-end, drift, protocol, versioning, and narrow-apply regression tests. |

## Runtime dependencies

All scripts use only the Python standard library and support Python 3.10+.
