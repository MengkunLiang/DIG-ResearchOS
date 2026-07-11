# Evidence Rules

Raw artifacts outrank prose. Prefer evidence in this order:

1. raw result files with hashes
2. exact configs and dataset split records
3. command logs and run ids
4. patch summaries or code paths
5. executor notes

Rules:

- Do not invent datasets, baselines, metrics, logs, seeds, or results.
- Mark dry-run and mock-only outputs as protocol evidence only.
- A metric without raw result, config, log, and run id cannot support an empirical claim.
- A missing required baseline creates claim risk even if a replacement is available.
- A diagnostic hint may guide iteration, but it is not an audited claim.
- If evidence conflicts, record the conflict and keep the weaker claim boundary.
- Do not cherry-pick failed runs out of the manifest.
