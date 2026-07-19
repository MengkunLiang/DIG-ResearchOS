# ResearchOS T5 External Executor Guide

> [English](../en/t5_external_executor.md) | [中文](../cn/t5_external_executor.md)

This guide explains how to start, debug, and hand back a T5 external experiment. Commands run from the ResearchOS repository root. The examples use `./workspace/project-a`; artifact paths without that prefix are relative to the workspace.

T5 starts only after T4.5 passes its novelty audit and formalizes the research package. It gives an external executor the research question, hypotheses, experiment constraints, and evidence boundaries. It does not turn plans, resource leads, or unverified hypotheses into results.

## Short Path

The complete pipeline enters T5 automatically after a passing T4.5 verdict:

```bash
python -m researchos.cli run --workspace ./workspace/project-a
```

Resume an existing project with:

```bash
python -m researchos.cli resume --workspace ./workspace/project-a
```

After REBOOST and project-Skill specialization, T5 stops at the experiment-material gate.

1. Put source datasets, baselines, benchmarks, model weights, and repositories under `workspace/project-a/resources/`. `datasets/`, `baselines/`, `benchmarks/`, and `repos/` are recommended organizing directories.
2. Put only already runnable deployment assets under `workspace/project-a/external_executor/expr/`.
3. Select “materials ready”, then choose Codex CLI, Claude Code, or a manual executor. `mock dry-run` only validates the local file protocol; it returns to the executor Gate and cannot enter T8 or support paper claims.
4. For Codex CLI, start Codex from the workspace root:

```bash
cd workspace/project-a
codex
```

Then send:

```text
Please read external_executor/AGENTS.md and execute external_executor/skills/research-execution/SKILL.md.
```

The root external Skill attempts to start T8 from the same executor session after Writer Handoff succeeds. While it writes, do not run `resume`, `run-task T5-*`, or `run-task T8` for the same workspace in another terminal.

## Commands

Normal use needs only `run` or `resume`. To re-enter and rebuild the T5 handoff from validated T4.5 artifacts in an existing workspace:

```bash
python -m researchos.cli resume \
  --workspace ./workspace/project-a \
  --from-task T5-REBOOST
```

Do not edit `state.yaml` to jump into T5.

`run-task` diagnoses one stage and does not advance the complete pipeline:

```bash
# Compile the deterministic T4.5-to-T5 handoff.
python -m researchos.cli run-task T5-REBOOST \
  --workspace ./workspace/project-a

# Publish the project-specific external-executor Skill Suite.
python -m researchos.cli run-task T5-SPECIALIZE \
  --workspace ./workspace/project-a

# Show the executor-selection gate after specialization is complete.
python -m researchos.cli run-task T5-EXECUTOR-GATE \
  --workspace ./workspace/project-a
```

Resources may be added as soon as the workspace exists and should be ready before executor selection. Phase B classifies reviewed resources under `resources/byhand/`, `resources/Remote_acquisition/`, or `resources/reproduction/`; those labels describe provenance, not completed reproduction or experimental evidence.

The material gate inventories paths and sizes under `resources/` but does not hash large datasets or weights. Phase B owns identity, revision, license, security, protocol-fit, and integrity verification.

If the executor has stopped, all four return artifacts are ready, and it explicitly reported that it could not start T8, run:

```bash
python -m researchos.cli run-task T8 \
  --workspace ./workspace/project-a
```

## T4.5 Inputs and T8 Outputs

T5 consumes `project.yaml`, the selected Candidate, `ideation/hypotheses.md`, `ideation/exp_plan.yaml`, contribution and validation maps, kill criteria, the novelty audit, the post-novelty formalization manifest, the full proposal and proposal manifest, literature synthesis/comparison artifacts, reading notes/manifests, and resource-catalog leads. The catalog is Phase B discovery context only. A link is not proof that a resource was downloaded, is runnable, has an acceptable license, or produced a result.

T8 receives the primary fact report:

```text
external_executor/executor_research_report.md
```

It also requires these durable companions:

```text
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

`result_pack.json` is structured state and references. It never replaces the real files under `external_executor/raw_results/`, `evidence_package/`, `figure/`, `table/`, or `expr/`. Once accepted, T8 produces `drafts/t5_t8_handoff.json`, `drafts/experiment_evidence_pack.json`, and `drafts/result_to_claim.json`.

## Before Execution

`T5-REBOOST-GATE` deterministically publishes these control artifacts:

| Artifact | Purpose |
| --- | --- |
| `external_executor/handoff_pack.json` | Research scope, claim boundary, experiment constraints, and source manifest |
| `external_executor/paper_card_evidence_index.json` | Paper-note evidence index |
| `external_executor/expected_outputs_schema.json` | Executor output contract |
| `external_executor/allowed_paths.txt` | Authoritative writable-path policy |
| `external_executor/AGENTS.md`, `external_executor/CLAUDE.md` | Executor instructions |
| `external_executor/report/reboost_report.json`, `external_executor/report/reboost_validation_report.json` | Compilation and independent validation receipts |

`T5-SPECIALIZE-EXECUTOR-SKILLS` then publishes `external_executor/project_skill_context.yaml`, its schema, `external_executor/skills/`, and specialization report/execution records. Do not edit the project-specific blocks by hand. Rebuild from REBOOST if upstream formal artifacts change.

## External Execution A to F

The executor maintains `result_pack.json`, `executor_status.json`, and `report/run_manifest.json` across all phases.

| Phase | Skills | Durable artifacts consumed downstream |
| --- | --- | --- |
| A Context alignment | `context-alignment` | `result_pack.json#context_alignment` |
| B Resource and baseline preparation | `resource-and-baseline-preparation` | reviewed `resources/` materials, `external_executor/resource_requirement_matrix.json`, and resource/baseline/dataset/gap/readiness entries in `result_pack.json` |
| C Experiment design | `experiment-design` | `external_executor/experiment_plan.json`, `report/phase_C/claim_evidence_matrix.json`, and corresponding result-pack sections |
| D Build, reproduce, review, run | `baseline-reproduction`, `method-refinement`, `implementation`, `code-and-protocol-review`, `experiment-run` | method specification, iteration plans, deployed baselines in `expr/baselines/`, method worktrees in `expr/implementation/<ITER-ID>/worktree/`, and raw results/logs/checkpoints in `raw_results/` |
| E Diagnosis and attribution | `result-diagnosis`, `module-attribution` | diagnosis and attribution reports/directories plus result-pack entries |
| F Evidence packaging and writer handoff | `evidence-packaging`, `writer-handoff` | realized method package, figures, tables, Phase F inventories/mappings/manifests, final report, and the three cross-phase JSON files |

The resulting flow is:

```text
REBOOST controls + specialized Skills
  -> result pack / status / manifest
  -> reviewed resources + runnable expr assets
  -> raw results
  -> diagnosis and attribution
  -> evidence package, figures, and tables
  -> executor_research_report.md
  -> T8
```

## Completion Check

Before the external executor exits, verify that these files exist:

```text
external_executor/executor_research_report.md
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

Also verify that the report's referenced files under `expr/`, `raw_results/`, `evidence_package/`, `figure/`, and `table/` still exist and agree with the manifest hashes. If an artifact is missing, a hash differs, or Writer Handoff failed, repair the external executor output and then run `run-task T8`. Never fabricate a terminal status, result pack, or manifest, and never bypass the checks by editing `state.yaml`.

While T5 is at `T5-EXTERNAL-WAIT`, `resume` only validates the four return artifacts. It does not rerun T4.5, REBOOST, or the external executor, and should be used only after the executor has stopped writing.
