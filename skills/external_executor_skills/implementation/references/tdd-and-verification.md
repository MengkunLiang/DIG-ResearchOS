# TDD and Verification

## Principle

Fresh, durable verification evidence is required before the Builder reports `ready_for_review`. A plausible implementation or a remembered passing command is not evidence.

## Test seam first

Before editing behavior, identify the smallest observable seam:

- pure function or loss term;
- module input/output and shape;
- config parsing and default semantics;
- adapter translation;
- entrypoint argument and output contract;
- serialization/checkpoint round trip;
- metric/log schema;
- ablation on/off behavior;
- deterministic seed behavior.

Prefer stable behavioral tests over implementation-detail tests.

## Vertical slices

Implement one thin path through interface, implementation, config, and test rather than building all internals first. A slice should be small enough that a failing check identifies one approved behavior.

## Red–green evidence

For behavior-changing work:

1. run the focused verification before implementation;
2. require failure for the intended reason;
3. preserve stdout, stderr, exit status, command, timestamp, and worktree fingerprint;
4. implement the minimum change;
5. rerun the same behavior check and require success;
6. record a linked TDD cycle;
7. run final regression checks against the final worktree fingerprint.

A red command that fails because of a missing interpreter, unrelated import error, or broken fixture is not valid red evidence.

## TDD exceptions

An exception must be documented with one of:

```text
pure_declarative_config
scaffold_only
existing_test_already_red
non_behavioral_metadata
emergency_bounded_repair
```

The exception states why a new red test is not meaningful and what alternative fresh verification is used. Exceptions never waive final verification.

## Allowed verification classes

```text
unit
interface
config
import
shape
serialization
type
lint
build
integration
```

Bounded integration means small fixtures or synthetic tensors and no research-scale run.

## Forbidden commands

Verification must not:

- train or fine-tune a model;
- run formal evaluation or benchmark comparison;
- download data, checkpoints, packages, containers, or code;
- install dependencies;
- use network services;
- launch distributed workers or schedulers;
- modify system configuration;
- use package-manager lifecycle hooks;
- execute arbitrary shell strings;
- write outside the implementation root except declared temporary directories.

## Freshness

A verification is fresh only when its `worktree_manifest_sha256` equals the final worktree hash. Any code/config/test change after verification makes the record stale.

## Mandatory checks

All `mandatory=true` verification items must pass. Optional checks may fail only when their limitation and downstream risk are recorded. A mandatory check cannot be relabeled optional after failure without root approval.

## Do not game green status

Forbidden responses to a failure include:

- weakening or deleting assertions;
- marking tests skipped or xfailed without authorization;
- changing expected output to match incorrect code;
- excluding changed files from test discovery;
- catching and ignoring errors;
- returning a constant solely to satisfy a fixture;
- changing protocol semantics in a test fixture;
- running a narrower command than the contract requires.

## Verification record

Each record contains:

```json
{
  "schema_version": "implementation_verification.v1",
  "implementation_id": "",
  "verification_id": "",
  "phase": "red|green|final",
  "expectation": "failure|success",
  "status": "passed|failed|timed_out|blocked|error",
  "command": [],
  "working_directory": "",
  "started_at": "",
  "ended_at": "",
  "duration_seconds": 0,
  "exit_code": 0,
  "stdout_path": "",
  "stderr_path": "",
  "worktree_manifest_sha256": "",
  "expected_outputs": [],
  "output_artifacts": [],
  "environment": {},
  "notes": []
}
```
