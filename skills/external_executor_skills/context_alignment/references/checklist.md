# Context Alignment Checklist

Read:

- `external_executor/AGENTS.md`
- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- source files under `ideation/`, `literature/`, `novelty/`, `resources/`, and `user_seeds/`

Check:

- project goal is clear
- central hypothesis is clear
- `method_intent` exists and is marked draft-only
- required baselines are clear
- minimum experiment loop is clear
- allowed paths are clear
- result pack required fields are clear
- `context_reboost` matches source artifacts

If `context_reboost` or `handoff_pack.json` conflicts with source files, record
`context_mismatch` and prefer the source artifact or novelty audit for required
baselines and claim boundaries.

Output shape:

```json
{
  "context_alignment": {
    "status": "pass | mismatch | blocked",
    "source_files_checked": [],
    "mismatches": [],
    "resolution": []
  }
}
```
