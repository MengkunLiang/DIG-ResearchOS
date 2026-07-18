# Environment and Execution Safety

## Execution boundary

The runner is a recorder and limiter, not a full security sandbox. Prefer a project-provided container, VM, or restricted worker when third-party code is executed. Static review from Phase B remains required.

## Command rules

- Use an argv array; never interpolate an untrusted command into a shell string.
- The executable must appear in `allowed_executables` or resolve inside the controlled source/deployment directory.
- Default safe executables are Python interpreters and explicitly declared ML launchers.
- A shell, package manager, build tool, notebook runner, container engine, remote scheduler, or downloader requires exact authorization in the current plan.
- Do not execute repository hooks, lifecycle scripts, or hidden bootstrap code.
- Working directory must resolve inside the deployment directory under `external_executor/expr/`.
- Run records, environment captures, normalized metric reports, and diagnostics must be written to the paired evidence directory under `external_executor/report/baseline_reproduction/`. Stdout/stderr logs and original outputs produced by the baseline command must be written under `external_executor/raw_results/baseline_reproduction/`.

## Environment variables and secrets

The runner builds a small environment from safe process variables and declared names. It drops values whose names contain patterns such as:

```text
TOKEN KEY SECRET PASSWORD PASSWD CREDENTIAL COOKIE AUTH PRIVATE
```

A secret required for a legitimate private service is not passed by this Skill unless the root and security policy explicitly authorize that exact variable and endpoint. Never persist secret values in plan, logs, command arguments, config snapshots, or environment records.

## Network

`network_required=false` is the default. The generic runner cannot prove kernel-level network isolation; use a sandbox/container policy when network denial must be enforced. If a baseline unexpectedly downloads data or weights, stop and return a Phase B/resource blocker rather than allowing ad hoc acquisition.

## Resource limits

Use timeout and, on supported Unix systems, memory and CPU limits. Record limits and termination cause. A timeout or OOM is evidence about the attempt, not proof that the baseline is invalid.

## Process control

- start a new process group/session;
- on timeout, terminate the group, then force-kill if needed;
- stream stdout/stderr to files to preserve partial evidence;
- record exit code and signal;
- check expected outputs after the process ends;
- never delete partial outputs automatically.

## Reproducible environment record

Capture:

- OS/platform/architecture;
- Python/runtime executable and version;
- installed package names and versions;
- CPU count and memory facts;
- GPU information only through an explicitly requested, non-mutating probe;
- Git commit/tree/dirty state for the controlled source;
- declared environment-variable names, with values redacted.

Environment capture is descriptive, not proof that dependency resolution was correct.
