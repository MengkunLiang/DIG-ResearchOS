# Builder-Reviewer Loop

Use this loop whenever code, configs, baselines, or protocols change.

Builder responsibilities:

- implement or adapt code only inside allowed external executor paths
- keep baseline and ours configs comparable
- save commands, configs, logs, raw outputs, and patch notes
- expose ablation switches for claimed mechanisms

Reviewer responsibilities:

- verify method intent is implemented, not merely described
- verify baseline fairness, metric direction, seed policy, and dataset split
- check for leakage, mock-only evidence, missing logs, and cherry-picking
- decide `review_status`: `pass`, `needs_fix`, or `blocked`

Formal experiments may start only after reviewer status is `pass`. If the
status is `needs_fix`, return to Builder and record the fix. If `blocked`,
write partial status and the blocker instead of fabricating completion.
