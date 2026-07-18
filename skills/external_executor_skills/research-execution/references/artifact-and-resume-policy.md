# Artifact and Resume Policy

Use this reference before initialization, checkpoint reuse, stale propagation, or any write to core external-executor JSON.

## Contents

1. Artifact-first rule
2. Ownership
3. Atomic updates
4. Fingerprints and staleness
5. Resume algorithm
6. Manifest references

## Artifact-first rule

Durable files are the only workflow memory. Conversation state may help navigate, but it cannot prove that work completed or that evidence is current.

Core files:

```text
external_executor/executor_status.json
external_executor/report/run_manifest.json
external_executor/result_pack.json
external_executor/executor_research_report.md
external_executor/report/phase_A/input_fingerprint.json
```

## Phase report layout

Store every external Skill process, validation, and checkpoint file under its owning phase directory:

```text
external_executor/report/phase_A/  # context alignment and root input fingerprint
external_executor/report/phase_B/  # resource and baseline preparation
external_executor/report/phase_C/  # experiment design and protocol validation
external_executor/report/phase_D/  # baseline reproduction, method iteration, implementation, review/run control
external_executor/report/phase_E/  # result diagnosis and module attribution
external_executor/report/phase_F/  # evidence packaging and Writer Handoff
```

Keep `external_executor/report/run_manifest.json` directly under `report/`. It is the one global cross-phase external-execution file. T5 reboost, Skill specialization, executor selection, and executor capability receipts are produced before external Phase A and retain their existing root-level report paths.

## Ownership

Read broadly and write narrowly:

- Root owns executor status, global manifest metadata, iteration plans/decisions, budgets, blockers, and the intended terminal outcome.
- Each child owns only the result-pack sections and files declared in its contract.
- Reviewers write review records; they do not rewrite Builder outputs.
- Packaging skills read evidence and create packages; they do not alter raw results.
- Writer Handoff owns the final research report and validates the frozen status/result/manifest/figure/table package.
- T8 consumes `external_executor/executor_research_report.md` plus supporting `external_executor/` artifacts. After Writer Handoff, the root runs the routed `run-task T8` command; ResearchOS performs its own acceptance/ingest pass without rewriting the external package.

Never replace the entire result pack with a child-local view. Merge only the owned top-level section and preserve unknown fields for forward compatibility.

## Atomic updates

For core JSON:

1. Read and validate the current file.
2. Apply the narrow update in memory.
3. Write a temporary file in the same directory.
4. Flush and replace the target atomically.
5. Register its checksum after replacement.

Do not mark a checkpoint complete before its output file is durable and validated.

## Fingerprints and staleness

An input fingerprint is derived from normalized workspace-relative paths, file sizes, and SHA-256 values. Do not include timestamps in the digest.

A checkpoint is reusable only when:

- its output Schema is valid;
- its recorded input fingerprint matches;
- all referenced artifacts exist and match their checksums;
- its code, resource, dataset, split, metric, config, and protocol dependencies remain compatible;
- it was not interrupted during a write.

Staleness propagates through declared dependencies. Examples:

- changed handoff/core controls -> alignment and all semantic descendants stale;
- changed acquisition policy -> resource readiness and dependent plans/runs stale;
- changed split/metric/protocol -> affected baseline and formal runs stale;
- changed method implementation -> code review and affected ours runs stale;
- changed plot script only -> figure package stale, raw runs remain valid;
- changed narrative summary only -> handoff package stale, evidence remains valid.

Preserve stale and failed records with status; never delete them to make the active view look clean.

The T5-to-T8 bridge writes `drafts/t5_t8_handoff.json`, `drafts/experiment_evidence_pack.json`, and `drafts/result_to_claim.json` as ResearchOS-owned derived artifacts. Their presence does not authorize an external child Skill to write elsewhere under `drafts/`.

## Resume algorithm

1. Validate core controls and allowed paths.
2. Calculate the current input fingerprint.
3. Load state, manifest, and result pack independently; report malformed files explicitly.
4. Verify manifest paths and checksums.
5. Compare checkpoint fingerprints and dependencies.
6. Mark invalid checkpoints/runs stale.
7. Find the earliest unmet prerequisite in the routing table.
8. Resume there and preserve later stale artifacts as historical evidence.

If a state file says a phase completed but the corresponding artifact is missing or invalid, trust the artifact check and downgrade the state.

## Manifest references

Every registered artifact should include:

```json
{
  "artifact_id": "sha256:<digest>",
  "path": "workspace-relative/path",
  "sha256": "<digest>",
  "size_bytes": 0,
  "producer": "skill-name",
  "phase": "A|B|C|D|E|F|root",
  "evidence_level": "method_definition|raw_result|diagnostic_hint|audited_candidate|unsupported",
  "created_at": "RFC3339 timestamp"
}
```

Formal run records additionally bind config, raw log, metric output, split, seed/repeat, code version/patch, resource versions, environment, hardware, and protocol fingerprint.
