# Scope and Dispatch Policy

## Fixed review unit

Every review is bound to:

```text
review_id
iteration_id
requested_approval_level
input_snapshot fingerprint
implementation spec/delta
changed paths
affected experiments
protocol fingerprint
verification-evidence bundle
```

If any of these changes after review begins, the old review becomes stale. Do not extend a verdict to unreviewed changes.

The executable code/config snapshot for baseline and ours runs must be under `external_executor/expr/`. Resource inputs are referenced from `resources/` for by-hand local material or `resource/` for acquired/reimplemented material; raw run evidence is referenced from `external_executor/raw_results/`.

## Per-iteration scope

Default to the current iteration's changed files and direct behavioral dependencies. Do not crawl the whole repository merely because it exists.

Expand only when the change crosses a shared seam:

- dataset loader, split generator, preprocessing, sampler;
- metric or evaluation implementation;
- baseline wrapper/adapter or shared model interface;
- seed, config, logging, checkpoint, or environment utilities;
- common training/inference loop;
- shared loss, optimizer, scheduler, or feature pipeline;
- permission, network, filesystem, or subprocess boundary.

Record every expanded path and the reason. Final whole-project review remains a separate broad audit.

## Review baseline

Prefer an explicit previous snapshot or patch base. A VCS diff may be used when a stable commit/ref exists, but the review contract is the content-addressed snapshot, not the branch name.

The snapshot must include added, modified, removed, and unchanged classifications. Removed code matters when a required module, guard, baseline path, or log is deleted.

## Independent reviewer dispatch

When isolated reviewers are available:

### Spec Reviewer

Receives method intent, confirmed scope, implementation spec/delta, changed snapshot, and affected claims. Reviews spec/method alignment and contribution drift. Does not see the Code/Protocol review conclusions.

### Code/Protocol Reviewer

Receives changed snapshot, experiment plan, normalized protocols, configs, verification evidence, and safety controls. Reviews correctness, fairness, data integrity, reproducibility, and security. Does not see the Spec review conclusions.

The parent aggregates without hiding disagreement. Do not instruct reviewers to ignore a suspected issue; adjudicate it afterward with evidence.

## Missing scope

If the Builder supplies no fixed change set, no implementation delta, or no requested approval level, return `blocked`. Guessing the diff creates an unauditable gate.
