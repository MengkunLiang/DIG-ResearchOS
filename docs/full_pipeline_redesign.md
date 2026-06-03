# Full Pipeline Redesign

ResearchOS 的新主链目标是把文献、idea、benchmark/实验、claim 和写作闭环接通，而不是让一个大 Agent 自己在内部完成所有实验。

## 新主链

```text
T1
 -> T2
 -> T3
 -> T3.5
 -> optional T3.6 survey
 -> T4
 -> T4.5
 -> T5-HANDOFF
 -> T5-DRY-RUN
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-CLAIMS
 -> T7.5
 -> T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-*
 -> T8-DRAFT / SELF-CHECK / REVIEW / REVISE
 -> T9
```

## 分层职责

| 层 | 职责 |
| --- | --- |
| Core runtime | 状态机、workspace artifact、resume、budget、tool registry、validator。 |
| Research intelligence | 文献检索、引用图、深读、synthesis workbench、idea fanout/jury、novelty audit。 |
| External experiment | 编译实验协议、选择执行器、生成 Codex/Claude/manual prompt、dry-run、摄取、审计。 |
| Evidence writing | result-to-claim、experiment evidence pack、section-by-section 写作、paper claim audit、T9 编译。 |

## Pre-T5 到实验链

Pre-T5 产物不直接变成论文 claim：

- `literature/domain_map.json` 给 T3.5/T4/T8 提供领域地图和邻接迁移线索。
- `literature/synthesis_workbench.json` 保存 contribution space、cross-paper tensions 和 adjacent transfers。
- `ideation/idea_scorecard.yaml` 保存 idea origin、counterfactual check、nearest prior work 和 novelty signal。
- `ideation/exp_plan.yaml` 是 T5-HANDOFF 的实验契约来源。
- `ideation/novelty_audit.md` 是 T5-HANDOFF 和 T7.5 判断风险边界的输入。

LLM 负责从这些材料中做研究判断；tool 只负责结构化、去重、校验和 provenance。

## 实验链到写作链

T7-CLAIMS 输出两个写作关键文件：

- `drafts/result_to_claim.json`
- `drafts/experiment_evidence_pack.json`

T8-RESOURCE 把它们合并进 `manuscript_resource_index.json`、`evidence_plan.json` 和 `paper_state.json`。T8-SEC-EXPERIMENTS 只能使用这些证据或 indexed artifacts 中的数字。T8-DRAFT / revise 后必须刷新 `paper_claim_audit.md/json`。

## Legacy 节点

旧 `T5`、`T6`、`T7` 仍保留为 legacy 节点，但普通 `run-task T5/T6/T7` 会报 retired；需要旧内部实验调试时必须使用 `LEGACY-* --allow-legacy`。完整主链和 T7.5 旧推荐 `next_task: T7` 都会进入 `T5-HANDOFF`，避免 resume 误回内部实验模式。
