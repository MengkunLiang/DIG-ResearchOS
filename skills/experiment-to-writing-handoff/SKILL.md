---
name: experiment-to-writing-handoff
description: Prepare T8 writing inputs from audited external experiment results and claim support artifacts.
allowed_tools:
  - read_file
  - list_files
  - finish_task
allowed_read_prefixes:
  - drafts/
  - experiments/
  - ideation/
  - literature/
allowed_write_prefixes:
  - drafts/
max_steps: 10
max_tokens_total: 80000
temperature: 0.2
---

# Experiment To Writing Handoff

Use this as a prompt-level checklist before T8 writing:

- `drafts/result_to_claim.json` exists.
- `drafts/experiment_evidence_pack.json` exists.
- `experiments/integrity_audit.json` exists.
- Mock-only evidence is visible and not phrased as a real empirical finding.
- Figure/table planning uses audited artifacts, not executor summary prose.

