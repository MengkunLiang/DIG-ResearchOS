# Baseline Inventory Schema

For each baseline in `baseline_matrix`, record:

- baseline name
- why included
- official repo
- unofficial repo
- paper or source
- dataset compatibility
- metric compatibility
- runnability
- license
- dependency risk
- compute cost
- status

If a required baseline cannot be found or run, record:

- `baseline_unavailable_reason`
- `replacement_candidate`
- `claim_risk`

Do not silently replace a hard baseline with an easier or weaker one. Replacement
candidates are risks for T7, not automatic substitutes.
