# Reference Project Review

本页记录 ResearchOS 从三个 reference 自动科研系统中迁移的工程机制。它不是领域知识库，不把任何论文结论写死进 tool；它只抽取可复用的运行时、artifact、skill、审计和写作机制。

## 读取范围

目标 reference：

- `/mnt/data/reference/整体/Auto-claude-code-research-in-sleep-main`
- `/mnt/data/reference/整体/autoresearch-master`
- `/mnt/data/reference/整体/AutoResearchClaw-main`

当前实现新增 `mine_reference_projects` 工具和 `reference-project-miner` skill。运行后会在 workspace 生成：

- `docs/reference_project_review.md`
- `researchos_reference/pattern_cards.jsonl`
- `researchos_reference/transfer_matrix.csv`
- `researchos_reference/skill_import_plan.md`
- `researchos_reference/pipeline_comparison.md`
- `researchos_reference/anti_patterns.md`

如果本地 reference 目录不存在，工具会记录 `reference_missing`，不会臆造源码细节。

## 可迁移 Pattern

| Pattern | 迁移到 ResearchOS |
| --- | --- |
| Skill as methodology | 在 `skills/` 下新增 reference-project-miner、external-executor-bridge、experiment-integrity-audit、result-to-claim、paper-claim-audit 等 SKILL.md。Skill 指导 LLM 判断，不替代科学知识。 |
| Experiment bridge | T5-T7 主链改为 handoff / dry-run / ingest / audit / result-to-claim；外部执行器在隔离路径实现和运行实验。 |
| Result-to-claim | `map_results_to_claims` 生成 `experiments/experimental_claims.json` 和 `drafts/result_to_claim.json`，给 Writer 限定 allowed/forbidden wording。 |
| Paper claim audit | `audit_paper_claims` 对 `drafts/paper.tex` 与 `drafts/experiment_evidence_pack.json` 做零上下文数字/claim 追踪检查。 |
| Resumable run states | 每个阶段只相信落盘 artifact；`accepted` 与 executor `done` 分离。外部执行器写 done，ResearchOS audit 决定证据能否进入写作。 |
| Artifact contract | `task_io_contract.py` 和 `state_machine.yaml` 为 T5-HANDOFF/T5-DRY-RUN/T7-* 新节点声明输入输出。 |
| Minimal dry-run loop | 默认用 `mock_dry_run` 跑通协议，不在最小测试中启动真实大实验。 |

## 反模式

- 不让执行器自我验收：executor summary 不是事实，raw artifacts/config/log/hash 才是事实。
- 不把 mock dry-run 写成实证结果：`mock_only=true` 的 evidence 会在 result-to-claim 和 paper claim audit 中被降级。
- 不把科学判断硬编码到 tool：tool 只做 schema、hash、provenance、数字追踪、路径和状态检查。
- 不让外部执行器污染主 repo：handoff pack 明确 allowed paths，默认写 `external_executor/` 和实验 artifact 目录。

## 当前落地文件

- Tool：`researchos/tools/reference_mining.py`
- Tool：`researchos/tools/external_experiment.py`
- Registry：`researchos/tools/builtin.py`
- State machine：`config/system_config/state_machine.yaml`
- Contracts：`researchos/orchestration/task_io_contract.py`
- Skills：`skills/reference-project-miner/`、`skills/external-executor-bridge/`、`skills/experiment-integrity-audit/`、`skills/result-to-claim/`、`skills/paper-claim-audit/`
- Shared references：`skills/shared-references/*.md`

