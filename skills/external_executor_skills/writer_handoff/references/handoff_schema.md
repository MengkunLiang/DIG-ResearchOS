# Writer Handoff Schema

`writer_handoff` is not paper prose. It is audited material for T7 and T8.

Include:

- method summary
- implementation summary
- realized method package ref
- framework figure ref
- main results
- ablation results
- diagnostic findings
- figures
- tables
- claim candidates
- must-not-claim items
- limitations
- open risks
- recommended storyline update

Rules:

- Reference raw evidence instead of executor prose.
- Separate supported, weak, unsupported, and diagnostic-only findings.
- Keep `executor_status.accepted=false`.
- Write `result_pack.json` last.
