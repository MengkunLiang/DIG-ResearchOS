---
name: paper-claim-audit
description: Audit manuscript numeric claims against result-to-claim and normalized experiment evidence pack.
allowed_tools:
  - read_file
  - audit_paper_claims
  - finish_task
allowed_read_prefixes:
  - drafts/
  - experiments/
  - literature/
allowed_write_prefixes:
  - drafts/
max_steps: 12
max_tokens_total: 100000
temperature: 0.2
---

# Paper Claim Audit

Run `audit_paper_claims` after a manuscript draft or revision. Treat its JSON as a mechanical audit signal, then let the writer/reviewer decide how to revise wording and limitations.

Dry-run evidence should produce a visible failure instead of silently entering the paper.

