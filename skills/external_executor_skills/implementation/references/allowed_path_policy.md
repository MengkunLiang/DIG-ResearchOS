# Allowed Path Policy

Implementation may write only under allowed external executor paths, normally:

- `external_executor/workdir/`
- `external_executor/configs/`
- `external_executor/logs/`
- `external_executor/raw_results/`
- `external_executor/patches/`
- `external_executor/figures/`
- `external_executor/tables/`

Do not write:

- ResearchOS runtime code
- `config/`
- `drafts/`
- `submission/`
- Pre-T5 source artifacts under `ideation/`, `literature/`, `novelty/`, `resources/`, or `user_seeds/`

Every implementation change should produce a patch note with purpose, affected
code paths, config keys, and expected reviewer checks.
