# Experiment Module Redesign

新版实验模块的原则：ResearchOS 不在默认主链里亲自长时间实现和运行实验；它负责编译协议、移交外部执行器、摄取结果、审计诚信、生成 result-to-claim，并把证据包交给写作链。

## 节点

### T5-HANDOFF

读取：

- `project.yaml`
- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/risks.md`
- `ideation/novelty_audit.md`
- `ideation/idea_scorecard.yaml`
- `literature/synthesis.md`
- `literature/comparison_table.csv`

调用：

```text
build_experiment_handoff_pack
```

写出：

- `external_executor/handoff_pack.json`
- `external_executor/executor_selection.json`
- `external_executor/input_manifest.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/executor_prompt.md`
- `external_executor/codex_prompt.md`
- `external_executor/claude_code_prompt.md`
- `external_executor/manual_instructions.md`

### T5-DRY-RUN

调用：

```text
mock_external_dry_run
```

写出 schema-compatible mock result pack、status、run manifest、heartbeat、raw result、config 和 log。它只验证协议，不产生真实论文证据。

### T7-INGEST

调用：

```text
ingest_external_results
```

把 `external_executor/result_pack.json` 规范化为：

- `experiments/results_summary.json`
- `experiments/run_records.jsonl`
- `experiments/evidence_index.json`
- `experiments/ingest_report.json`

### T7-AUDIT

调用：

```text
audit_experiment_integrity
```

检查 metric source artifact、artifact hash、run manifest、mock 标记和 provenance。输出 `experiments/integrity_audit.json`。

### T7-CLAIMS

调用：

```text
map_results_to_claims
build_experiment_evidence_pack
```

输出：

- `experiments/experimental_claims.json`
- `drafts/result_to_claim.json`
- `drafts/experiment_evidence_pack.json`
- `experiments/iteration_log.md`

## Validator 关注点

- handoff pack 的 `semantics` 必须正确。
- expected schema、allowed paths、executor prompt 必须存在。
- dry-run 必须显式 `mock_only=true`。
- ingest 后的 `results_summary.json` 必须标明 `source=external_executor`。
- audit 必须能追踪 artifact 到磁盘文件和 sha256。
- result-to-claim 必须生成 support status 和 allowed/forbidden wording。

## 最小联调

不要一开始跑真实大实验。最小顺序：

```bash
python -m researchos.cli run-task T5-HANDOFF --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-DRY-RUN --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-INGEST --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-AUDIT --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-CLAIMS --workspace ./workspace/local-test2
```

真实外部执行器在 `T5-HANDOFF` 后接手，写入同一套 `external_executor/result_pack.json` 协议，ResearchOS 从 `T7-INGEST` 继续。

