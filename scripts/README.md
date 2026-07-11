# Scripts

`scripts/` contains maintained utility entry points. It is not the automated
test suite and it is not the place for ad hoc local debugging scripts.

Use `tests/` for checked, repeatable pytest coverage:

```bash
python -m pytest tests/unit -q
```

Use `scripts/` for small reusable tools that are safe to keep in git. Put local
manual probes under ignored `tests/manual/`.

By default, pytest only collects `tests/unit/`. Real-environment tests under
`tests/real/` must be selected explicitly. Local manual probes under
`tests/manual/` are ignored by git and are not part of the shared test suite.

## Directory Contract

| Location | Purpose | Runs In CI by default |
| --- | --- | --- |
| `tests/unit/` | Deterministic pytest coverage for runtime, tools, agents, validators, and CLI behavior | Yes |
| `tests/real/` | Real-environment pytest checks that may need local tools, credentials, or larger fixtures | No, unless explicitly selected |
| `tests/manual/` | Local-only manual debug/probe scripts | No; ignored by git |
| `scripts/` | Maintained utility scripts such as validation and recovery helpers | No |

## Common Scripts

| Script | Purpose |
| --- | --- |
| `_script_env.py` | Shared path/bootstrap helper for manual scripts |
| `validate_artifact.py` | Validate one artifact file while debugging a workspace |
| `validate_llm_model.py` | Probe a model routing profile/tier without running the full pipeline |
| `recover_t2_papers_raw_from_trace.py` | Recovery helper for reconstructing T2 raw paper data from traces |

New files in this directory should be maintained utilities. If a check is
repeatable and should be part of the supported contract, put it under
`tests/unit/` or `tests/real/`. If it is an operator-facing diagnostic, keep it
local under ignored `tests/manual/`.

When adding new automation, prefer one of these patterns:

- deterministic behavior check: add `tests/unit/test_*.py`
- real API or Docker integration check: add `tests/real/test_*.py`
- one-off manual probe: add `tests/manual/probe_*.py` or `tests/manual/debug_*.py`
- recovery/maintenance utility: add `scripts/recover_*.py`, `scripts/validate_*.py`, or a focused `tests/unit/` check

## Before Running

1. Work from the repository root.
2. Activate the local environment.
3. Prefer a temporary workspace under `/mnt/data/tmp` or `./tmp`.
4. Check whether the script uses real LLM/API calls before running it.
5. Do not commit generated workspaces, traces, logs, PDFs, or recovered data.

Examples:

```bash
python scripts/validate_llm_model.py --profile deepseek --tier medium
python scripts/validate_artifact.py --workspace ./workspace/dev --task T2
python scripts/recover_t2_papers_raw_from_trace.py --help
```
