# writer-handoff file manifest

## Root

- `SKILL.md`: final fact compilation, report rendering, validation, ownership, and return contract.
- `MANIFEST.md`: concise file-purpose index.

## References

- `handoff-policy.md`: ownership, source priority, staleness, and partial outcomes.
- `research-report-contract.md`: eight report sections and required factual fields.
- `academic-writing-policy.md`: prose, quantitative, formula, terminology, and citation constraints.
- `final-validation-contract.md`: status, result pack, manifest, report, figure, and table checks.
- `output-contract.md`: final and process output paths and schemas.

## Scripts

- `_common.py`: workspace paths, atomic writes, JSON, hashing, status, and traversal helpers.
- `preflight_handoff.py`: validate controls, final inputs, directories, and write paths.
- `build_handoff_snapshot.py`: pin final core documents and all figures/tables.
- `build_research_report_facts.py`: normalize project, implementation, experiment, result, Claim, literature, limitation, and artifact facts.
- `render_executor_research_report.py`: render the eight-section Markdown report.
- `validate_writer_handoff.py`: validate the complete final handoff package.

## Tests

- `test_writer_handoff_scripts.py`: complete pipeline, comprehensive coverage, manifest and status enforcement, stale-input detection, and language/authority checks.
