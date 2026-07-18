# Output Contract

## Final downstream document

`external_executor/executor_research_report.md` is the primary ResearchOS and T8 handoff. It contains the eight required sections in `research-report-contract.md` and refers to original workspace-relative artifacts.

## Process and validation files

```text
external_executor/report/writer_handoff_preflight.json
external_executor/report/writer_handoff_snapshot.json
external_executor/report/writer_handoff_facts.json
external_executor/report/writer_handoff_validation.json
```

`writer_handoff_snapshot.v2` stores complete core documents, core file hashes, final figure/table hashes, `handoff_id`, and `input_fingerprint`.

`writer_handoff_facts.v1` stores project, implementation, experiment, comprehensive-result, preliminary Claim, verified-literature, limitation, and artifact-index records derived from the snapshot.

`writer_handoff_validation.v2` stores `ready | partial | blocked`, the six validated surfaces, hashes, coverage checks, errors, warnings, and the recommended next action.

## Prohibited legacy outputs

Do not produce:

```text
writer_handoff_inventory.json
writer_handoff_claim_map.json
writer_handoff_t7_index.json
writer_handoff_integrity.json
writer_handoff_report.json
result_pack.json#writer_handoff
```

These parallel indexes are no longer the downstream contract. Their necessary facts are consolidated into `writer_handoff_facts.json` and the final Markdown report.
