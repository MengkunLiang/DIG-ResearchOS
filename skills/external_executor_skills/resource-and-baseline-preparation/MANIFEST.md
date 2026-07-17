# Package Manifest

## Core

- `SKILL.md` — Phase B operational workflow and boundaries.

## References

- `references/acquisition-policy.md` — authority and ordered acquisition paths.
- `references/resource-requirement-contract.md` — requirement schema and blocking semantics.
- `references/source-ranking-and-search.md` — source tiers and search records.
- `references/baseline-reimplementation.md` — fallback implementation contract.
- `references/resource-review-checklist.md` — independent review dimensions and approvals.
- `references/repository-static-review.md` — non-execution risk review policy.
- `references/output-contract.md` — report and result-pack mapping.

## Scripts

- `_common.py` — path, JSON, hashing, manifest, ID, and schema helpers.
- `preflight_resources.py` — validate prerequisites and acquisition authority.
- `build_requirement_matrix.py` — create deterministic requirement scaffold.
- `inventory_local_resources.py` — inventory local resource candidates.
- `initialize_resource_report.py` — initialize/resume the report envelope.
- `stage_local_resource.py` — controlled local staging with provenance.
- `acquire_github_resource.py` — immutable public Git acquisition into `resource/Remote_acquisition/`.
- `static_review_repository.py` — static repository risk review.
- `scaffold_reimplementation.py` — provenance-first baseline reimplementation scaffold.
- `validate_reimplementation_package.py` — reimplementation candidate validation.
- `build_resource_source_report.py` — source classification report for `resources/` by-hand products and `resource/` acquired/reimplemented products.
- `compute_resource_readiness.py` — deterministic readiness gate.
- `validate_resource_report.py` — report consistency validator.
- `apply_resource_report.py` — narrow result-pack update.

## Tests

- `tests/test_resource_skill_scripts.py` — deterministic unit/integration-style script tests.
