# Failure and Recovery Policy

Use this policy after any interrupted or non-complete run and during resume.

## Failure categories

```text
launch_error
nonzero_exit
timeout
operator_cancelled
signal_terminated
environment_issue
resource_exhausted
missing_output
invalid_metric_output
dependency_changed
protocol_mismatch
path_or_security_block
unknown
```

The runner records the directly observable category. Scientific or engineering root-cause diagnosis belongs to later work; do not overstate a guess.

## Preserve first, recover second

For every attempt preserve:

- the immutable request and request fingerprint;
- running and terminal timestamps;
- raw log, even when empty;
- exit code or terminating signal when available;
- partial declared outputs and their checksums;
- actual elapsed resources;
- the failure category and recovery preconditions.

Do not delete a failed attempt, replace its record with a successful retry, or reuse its run ID.

## Resume rules

1. If a completed record has the same request fingerprint and all artifact checksums validate, it may be reused with `--reuse-valid`.
2. If a record says `running` but no managed process remains, preserve it as interrupted history. Create a new attempt only after root authorization.
3. If an input, dependency, config, split, metric, code, resource, or protocol fingerprint changed, the old run is not reusable. The root marks it `stale`.
4. A model/training checkpoint may be resumed only when the experiment plan and review explicitly authorize a resume command and the checkpoint checksum matches.
5. Never infer a resume command or continue from the newest file by filename alone.

## Retry policy

`experiment-run` never retries automatically. The root decides whether a new attempt is:

- an exact rerun under the same logical experiment;
- a resource/environment retry;
- an implementation repair requiring review;
- a protocol change requiring a new plan version;
- a stop or human-review condition.

Every retry receives a new run ID and budget reservation.

## Recovery result

Return:

```json
{
  "recoverable": true,
  "requires_new_run_id": true,
  "requires_review": false,
  "requires_plan_change": false,
  "required_authority": [],
  "preserved_artifacts": [],
  "recommended_next_action": "research-execution"
}
```

This is a recommendation, not a routing decision.
