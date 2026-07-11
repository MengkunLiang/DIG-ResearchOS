# Stop Conditions

Stop the external loop when any condition is met:

- `budget_exhausted`
- `improvement_plateau`
- `required_baseline_unavailable`
- `audited_target_reached`
- `implementation_blocked`
- `claim_must_be_narrowed`
- `scope_change_requires_human_review`

Stopping does not mean failure. When stopping, write:

- current executor status
- completed and missing stages
- blocker or stop reason
- available raw artifacts and logs
- claim risks and must-not-claim notes
- next action required by ResearchOS or the human user

Do not continue tuning solely to chase SOTA. If results are weak, diagnose the
mechanism and narrow claims honestly.
