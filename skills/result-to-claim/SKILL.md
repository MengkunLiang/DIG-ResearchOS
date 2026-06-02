---
name: result-to-claim
description: Convert audited experiment metrics into conservative claim mappings and manuscript evidence pack.
allowed_tools:
  - read_file
  - map_results_to_claims
  - build_experiment_evidence_pack
  - finish_task
allowed_read_prefixes:
  - experiments/
  - drafts/
allowed_write_prefixes:
  - experiments/
  - drafts/
max_steps: 12
max_tokens_total: 100000
temperature: 0.2
---

# Result To Claim

Use this skill after external results have been ingested and integrity-audited.

The output is a conservative support map. Unsupported or mock-only metrics may be used for debugging narrative, but must not become real paper claims.

