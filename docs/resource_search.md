# 资源检索与 Baseline 闭环

ResearchOS v3 把代码仓库、数据集、benchmark 和 baseline 资源提升为一等 artifact。当前实现已经创建 `resources/` 目录和 `_DIR_GUIDE.md`，并在外部实验链中闭环 `pass_with_required_baselines`。

## 资源目录

推荐文件：

- `resources/baseline_candidates.jsonl`
- `resources/datasets_raw.jsonl`
- `resources/datasets_verified.jsonl`
- `resources/benchmarks.jsonl`
- `resources/evaluation_protocols.json`
- `resources/reproducibility_matrix.csv`
- `resources/license_audit.md`
- `resources/resource_search_log.md`

这些文件服务于 T3.5/T4/T4.5/T5-HANDOFF/T8，不应放下载数据或 cloned repo。真实执行器工作目录是 `external_executor/workdir/`。

## Required Baselines 闭环

`T4.5` 如果给出 `pass_with_required_baselines`，应在 `ideation/novelty_audit.md` 写：

```markdown
## Required Baselines

- Baseline: SimGCL
  Reason: canonical contrastive baseline.
  Acceptable substitute: XSimGCL.
  Claims blocked if missing: outperforms prior work, state-of-the-art
```

`T5-HANDOFF` 会把该段解析为：

- `novelty/required_baselines.json`
- `handoff_pack.experiment_contract.required_baselines`

`T7-AUDIT` 会生成 `integrity_audit.required_baseline_coverage`。`T7-CLAIMS` 会根据 coverage 写：

- `drafts/must_not_claim.md`
- `drafts/claim_support_matrix.csv`
- `drafts/limitations_from_experiments.md`
- `drafts/figure_table_evidence_map.json`

## 当前边界

当前实现不会在工具内部调用 LLM 自动判断“哪个 baseline 最公平”。工具只抽取已写入 artifact 的 baseline 要求、检查是否覆盖、生成 claim block。baseline 选择、替代 baseline 是否合理、metric 是否公平，仍需要 LLM/人工在 T4.5、T7.5、Reviewer 或后续资源检索工具中显式写成 artifact。
