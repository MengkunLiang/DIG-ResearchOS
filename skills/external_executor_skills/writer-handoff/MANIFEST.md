# writer-handoff file manifest

## Root

- `SKILL.md`: workflow, ownership, gates, commands, evidence rules, and return contract.
- `MANIFEST.md`: file-purpose index.

## References

- `handoff-policy.md`: pre-audit semantics, ownership, partial/blocked behavior, resume.
- `t7-consumer-contract.md`: machine-readable requirements for T7 ingest/audit/method audit/claims.
- `evidence-and-claim-boundary.md`: evidence ceilings, counterevidence, must-not-claim, forbidden promotion.
- `artifact-reference-and-integrity.md`: paths, checksums, manifest, fingerprints.
- `limitations-risks-and-negative-results.md`: propagation and coverage of failures/risks/limits.
- `storyline-and-summary-policy.md`: evidence-bound navigation summaries.
- `final-validation-checklist.md`: F5 handoff validation checklist.
- `output-contract.md`: JSON shapes, enums, gate, and narrow apply.

## Scripts

- `_common.py`: safe paths, atomic JSON, hashing, schema and traversal helpers.
- `preflight_handoff.py`: control/prerequisite checks.
- `build_handoff_snapshot.py`: pin final upstream evidence state.
- `inventory_handoff_materials.py`: classify method/result/figure/table/risk/failure materials.
- `validate_artifact_refs.py`: file, checksum, path, and reference integrity.
- `build_claim_evidence_map.py`: pre-audit claim-support/counterevidence map.
- `build_t7_ingest_index.py`: T7 stage-oriented navigation index.
- `initialize_writer_handoff.py`: report envelope and deterministic risk/limit propagation.
- `compute_handoff_gate.py`: audit readiness calculation.
- `validate_writer_handoff.py`: schema, evidence, authority, risk coverage, and gate validation.
- `apply_writer_handoff.py`: narrow update of `result_pack.writer_handoff` only.

## Tests

- `test_writer_handoff_scripts.py`: snapshot/index, integrity, claim map, ready gate, narrow apply, authority rejection, and risk propagation.
