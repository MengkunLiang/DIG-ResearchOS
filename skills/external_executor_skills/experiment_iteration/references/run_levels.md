# Run Levels

Run in this order:

1. `smoke`
2. `small_scale`
3. `formal`
4. `ablation`
5. `robustness`
6. `diagnostic`

Smoke verifies code loads, data loads, loss is finite, metrics execute, and logs
are written.

Small-scale validation checks whether the method has any signal and whether
baseline/ours protocols are comparable.

Formal runs are used for audited comparison, ablation, robustness, and final
tables. Each formal run needs command, config, seed, split, raw result, log,
metric output, and patch or commit id.

Stop after plateau, blocker, missing required baseline, or claim narrowing.
