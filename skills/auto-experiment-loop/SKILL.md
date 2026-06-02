---
name: auto-experiment-loop
description: Guide bounded external edit-run-eval loops with objective metrics, logs, and keep/discard decisions.
allowed_tools:
  - read_file
  - finish_task
max_steps: 10
max_tokens_total: 80000
temperature: 0.3
---

# Auto Experiment Loop

Use a bounded edit-run-eval loop:

1. propose one scoped implementation change
2. run a fixed-budget sanity or mini experiment externally
3. parse objective metrics from raw files
4. keep, discard, debug, or escalate to full run
5. record logs, config, patch summary, and decision

ResearchOS consumes the resulting artifact protocol; it does not trust executor prose.

