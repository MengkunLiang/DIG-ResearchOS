# Budget and Stop Policy

The root owns global budget and stop decisions. This Skill enforces the supplied snapshot for one attempt and reports actual use.

## Preflight reservation

Require non-negative numeric `remaining` and `estimated` values for:

```text
runs
wall_clock_seconds
gpu_hours
cost
```

The estimated amount must not exceed the remaining amount. `estimated.runs` must be exactly one. `timeout_seconds` must not exceed remaining wall-clock time.

Preflight requires a numeric remaining and estimated cost bound. If cost cannot be bounded, the root must explicitly revise the budget policy or block the run; do not coerce an unknown estimate to zero.

## Actual accounting

The terminal record reports:

```json
{
  "runs": 1,
  "wall_clock_seconds": 0.0,
  "gpu_hours": 0.0,
  "cost": null
}
```

GPU hours are elapsed hours multiplied by the declared GPU count. This is allocation time, not measured utilization. Cost remains `null` unless an authoritative meter or scheduler artifact supplies it.

## Runtime stop conditions

Stop the child process and record the actual state when:

- the timeout expires;
- the executor delivers an authorized cancellation;
- a scheduler terminates the process;
- a hard safety or path violation is detected by the executor;
- the root revokes the dispatch.

The runner does not implement scientific early stopping unless the reviewed command/config already does so. It must not infer a plateau or modify training behavior from logs.

## Post-run handoff

Return actual consumption and any uncertainty to the root. The root subtracts it from the global budget, decides whether another attempt is allowed, and updates `executor_status.json`. A failed run still consumes its actual resources.
