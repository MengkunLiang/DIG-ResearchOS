---
name: experiment-contract-builder
description: Guide conversion from selected idea to claim-driven experiment contract before external execution.
allowed_tools:
  - read_file
  - finish_task
max_steps: 10
max_tokens_total: 80000
temperature: 0.3
---

# Experiment Contract Builder

Build a claim-led experiment contract:

- claim or hypothesis under test
- dataset/benchmark candidates
- baseline requirements
- primary and secondary metrics
- seeds and minimum run scope
- ablations and falsification conditions
- raw artifact outputs required for audit

Do not assume an experiment is valid because it ran. Validity comes after ingest, integrity audit, and result-to-claim.

