# ResearchOS T5 外部执行器使用指南

> [中文](../cn/t5_external_executor.md) | [English](../en/t5_external_executor.md)

本文说明如何启动、调试和接收 T5 外部实验。命令默认在 ResearchOS 仓库根目录执行，示例 workspace 为 `./workspace/project-a`。未写 `workspace/project-a/` 前缀的 artifact 路径均相对于该 workspace。

T5 只在 T4.5 新颖性审计通过并完成正式研究材料后开始。它将研究问题、假设、实验约束和证据边界交给外部执行器，但不把计划、资源线索或未验证假设写成实验结果。

## 最短使用路径

完整 pipeline 会在 T4.5 通过后自动进入 T5，无需单独启动：

```bash
python -m researchos.cli run --workspace ./workspace/project-a
```

已有项目继续运行：

```bash
python -m researchos.cli resume --workspace ./workspace/project-a
```

T5 完成 REBOOST 与项目专属 Skill Suite 后，终端会停在实验材料 Gate。此时：

1. 将数据集、baseline、benchmark、模型权重和仓库等**源资源**放入 `workspace/project-a/resources/`。推荐按 `datasets/`、`baselines/`、`benchmarks/`、`repos/` 分类，但 Phase B 会以实际资源清单和许可审查为准。
2. 仅将已经整理为可直接运行的部署资产放入 `workspace/project-a/external_executor/expr/`。不要把未经审查的下载仓库或原始数据混入该目录。
3. 在终端选择“材料已放置，继续”，再选择 Codex CLI、Claude Code 或人工执行器。`mock dry-run` 仅用于验证本地文件协议；它完成后会回到执行器 Gate，不能进入 T8 或形成论文实验结论。
4. 选择 Codex CLI 时，在 workspace 根目录启动 Codex：

```bash
cd workspace/project-a
codex
```

然后输入：

```text
请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research-execution/SKILL.md。
```

外部执行器完成 Writer Handoff 后，会在同一执行器会话中尝试启动 T8。外部执行器仍在写入时，不要在另一个终端对同一 workspace 运行 `resume`、`run-task T5-*` 或 `run-task T8`，否则可能读到尚未原子写完的结果包、状态或运行清单。

## T5 命令

### 在完整流程中使用

正常情况只使用 `run` 或 `resume`。需要在当前 workspace 中受校验地重新生成 T5 交接时，使用：

```bash
python -m researchos.cli resume \
  --workspace ./workspace/project-a \
  --from-task T5-REBOOST
```

该命令会检查 T4.5 的正式产物，清除旧的 T5 Gate 并记录重入原因。不要通过手工修改 `state.yaml` 跳入 T5。

### 单独调试 T5 三个阶段

`run-task` 只执行指定阶段，不自动推进完整 pipeline。它适用于诊断已存在 workspace 中的一个 T5 artifact 契约。

```bash
# 阶段 1：从 T4.5 正式产物编译外部执行 handoff
python -m researchos.cli run-task T5-REBOOST \
  --workspace ./workspace/project-a

# 阶段 2：生成项目专属 external-executor Skill Suite
python -m researchos.cli run-task T5-SPECIALIZE \
  --workspace ./workspace/project-a

# 阶段 3：在完成专项 Skill 后展示执行器选择 Gate
python -m researchos.cli run-task T5-EXECUTOR-GATE \
  --workspace ./workspace/project-a
```

调试第三阶段前，先准备 `resources/` 中的源材料；只有现成可运行的部署资产才放入 `external_executor/expr/`。

### 何时放置资源

已有资源可以在 workspace 初始化后随时放入，推荐在第一次进入 T5 前准备。最迟应在 `T5-REBOOST-GATE` 和 `T5-SPECIALIZE-EXECUTOR-SKILLS` 完成后的实验材料确认 Gate、选择执行器之前放入。

```text
resources/datasets/      数据集
resources/baselines/     baseline 材料
resources/benchmarks/    benchmark、评测协议或官方脚本
resources/repos/         用户提供的代码仓库或压缩包
```

Phase B 会把经审查的资源进一步整理到以下目录。它们表示不同来源，不表示资源已经成功复现或产生实验结果：

```text
resources/byhand/             研究者提供并完成受控入库的材料
resources/Remote_acquisition/ 经授权获取且完成静态审查的远程资源
resources/reproduction/       经过记录的 baseline 复现或重实现产物
```

实验材料 Gate 只清点 `resources/` 的路径和文件大小，不在此时为大型数据集或权重计算全量 hash。资源身份、版本、许可、安全、协议匹配和完整性核验由 Phase B 完成。

如果外部执行器已经完成、四项回传文件也已齐备，但其根 Skill 明确报告未能启动 T8，才在外部执行器停止写入后手动运行：

```bash
python -m researchos.cli run-task T8 \
  --workspace ./workspace/project-a
```

## T5 的上游输入与下游输出

### 从 T4.5 接收的核心输入

| 内容 | 位置 | T5 中的角色 |
| --- | --- | --- |
| 项目研究范围与约束 | `project.yaml` | 研究对象、边界和项目身份 |
| 已选 Research Idea | `ideation/selected/selected_candidate.json` | 固定进入审计后的候选与谱系 |
| 正式研究假设 | `ideation/hypotheses.md` | 待验证命题，不是已观察结果 |
| 完整研究方案 | `ideation/proposal/research_proposal.md` | 计划语境、理论贡献、现实含义、风险与研究设计 |
| Proposal 清单 | `ideation/proposal/proposal_manifest.json` | Proposal 章节来源与 freshness 锚点 |
| 实验计划 | `ideation/exp_plan.yaml` | 实验、指标、必需 baseline 与评价边界 |
| 假设、贡献与验证关系 | `ideation/contribution_hypothesis_map.yaml`、`ideation/validation_map.yaml` | 主张可由什么验证或反驳 |
| 停止条件 | `ideation/kill_criteria.yaml` | 何时收窄、停止或拒绝主张 |
| 新颖性审计 | `ideation/novelty_audit.md` | collision 边界和必需 baseline |
| 正式化清单 | `ideation/post_novelty_formalization.json` | T4.5 已通过的生命周期证明 |
| 文献综合与方法比较 | `literature/synthesis.md`、`literature/synthesis_workbench.json`、`literature/comparison_table.csv` | 科研语境与比较依据 |
| 论文笔记与文献清单 | `literature/deep_read_notes/`、`literature/shallow_read_notes/`、`literature/bridge_notes/`、`literature/literature_manifest.json` | 可追溯证据和阅读等级 |
| 已发现的资源线索 | `literature/resource_catalog.jsonl`、`literature/resource_catalog_summary.json` | Phase B 待核验线索，不是已下载或许可合格资源 |

### 传给 T8 的核心输出

T8 的主输入是：

```text
external_executor/executor_research_report.md
```

完整交接还必须保留以下三个文件：

```text
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

- `result_pack.json` 保存各阶段可消费的结构化状态和对真实 artifact 的引用。
- `executor_status.json` 保存外部执行终态与接受状态。
- `run_manifest.json` 登记实验资源、代码、原始结果、图表等文件的路径、大小和校验信息。

`result_pack.json` 不替代真实文件。T8 会沿引用读取 `external_executor/raw_results/`、`external_executor/evidence_package/`、`external_executor/figure/`、`external_executor/table/` 与 `external_executor/expr/` 中的产物。

T8 接收成功后会生成：

```text
drafts/t5_t8_handoff.json
drafts/experiment_evidence_pack.json
drafts/result_to_claim.json
```

## 启动外部执行器前的两组核心产物

### REBOOST

`T5-REBOOST-GATE` 从 T4.5 的正式材料确定性编译外部执行器控制文件。它不执行实验、不选择执行器，也不会把资源链接改写成已可运行资源。

| 核心产物 | 位置与用途 |
| --- | --- |
| 外部执行 handoff | `external_executor/handoff_pack.json`，研究范围、主张边界、实验约束和来源清单 |
| 论文笔记证据索引 | `external_executor/paper_card_evidence_index.json` |
| 外部执行结果契约 | `external_executor/expected_outputs_schema.json` |
| 可写路径边界 | `external_executor/allowed_paths.txt` |
| Codex 和通用执行说明 | `external_executor/AGENTS.md` |
| Claude Code 执行说明 | `external_executor/CLAUDE.md` |
| 编译与独立验证报告 | `external_executor/report/reboost_report.json`、`external_executor/report/reboost_validation_report.json` |

### SPECIALIZE-EXECUTOR-SKILLS

`T5-SPECIALIZE-EXECUTOR-SKILLS` 根据当前项目发布执行器实际运行的项目专属 Skill Suite。

| 核心产物 | 位置与用途 |
| --- | --- |
| 项目专属执行上下文 | `external_executor/project_skill_context.yaml` |
| 上下文 schema | `external_executor/schemas/project_skill_context.schema.json` |
| 项目专属 Skill Suite | `external_executor/skills/` |
| 专项 Skill 发布与执行记录 | `external_executor/report/skill_specialization_report.json`、`external_executor/report/skill_specialization_execution.json` |

外部执行器同时消费 REBOOST 控制文件与 SPECIALIZE 生成的 Skill Suite。不要手工改写 `project_skill_context.yaml` 或 Skill 的项目专属区块。上游正式材料变化后，应从 REBOOST 重新生成。

## 外部执行 A 到 F 阶段

外部执行期间，以下三个跨阶段文件持续更新：

```text
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

下表只列后续 Skill 或 T8 会继续消费的科研产物、资源和结果，不列各 Skill 自用的 preflight、validation 或普通执行报告。

| 阶段 | 相关 Skill | 跨阶段核心产物 |
| --- | --- | --- |
| A 上下文确认 | `context-alignment` | `result_pack.json#context_alignment`，确认后的研究范围、执行边界和可用输入 |
| B 资源与 baseline 准备 | `resource-and-baseline-preparation` | `resources/` 中的源资源和经审查的 `byhand/`、`Remote_acquisition/`、`reproduction/`；`external_executor/resource_requirement_matrix.json`；`result_pack.json` 中的资源、baseline candidates、dataset inventory、material gaps 与 resource readiness |
| C 实验设计 | `experiment-design` | `external_executor/experiment_plan.json`；`external_executor/report/phase_C/claim_evidence_matrix.json`；`result_pack.json#claim_evidence_matrix` 与 `#experiment_plan` |
| D 实现、复现、审查与运行 | `baseline-reproduction`、`method-refinement`、`implementation`、`code-and-protocol-review`、`experiment-run` | `external_executor/method_implementation_spec.json`；`external_executor/report/phase_D/iteration_plans/`；baseline 部署 `external_executor/expr/baselines/`；方法实现 `external_executor/expr/implementation/<ITER-ID>/worktree/`；日志、指标、run record、checkpoint 与原始输出 `external_executor/raw_results/` |
| E 结果诊断与模块归因 | `result-diagnosis`、`module-attribution` | `external_executor/result_diagnosis_report.json`、`external_executor/result_diagnosis/<iteration>/`；`external_executor/module_attribution_report.json`、`external_executor/report/phase_E/module_attribution/<iteration>/`；相应的 `result_pack.json#result_diagnoses` 与 `#module_attributions` |
| F 证据打包与写作交接 | `evidence-packaging`、`writer-handoff` | `external_executor/evidence_package/realized_method_package.json`；`external_executor/figure/`；`external_executor/table/`；`external_executor/report/phase_F/figure_table_inventory.json`、`evidence_mapping.json`、`evidence_package_manifest.json`；最终研究报告和三个跨阶段 JSON |

数据流可以概括为：

```text
REBOOST 控制文件 + 项目专属 Skills
  -> result_pack / status / manifest
  -> resources 中经过核验的资源 + expr 中可运行代码
  -> raw_results 中原始实验结果
  -> diagnosis / attribution
  -> evidence_package + figure + table
  -> executor_research_report.md
  -> T8
```

### 外部执行的核心产品

| 内容 | 存放位置 | 说明 |
| --- | --- | --- |
| baseline | `external_executor/expr/baselines/` | 可运行部署资产，不替代 Phase B 的资源审查记录 |
| method | `external_executor/expr/implementation/<ITER-ID>/worktree/` | 每轮方法迭代以独立 `<ITER-ID>` 保存 |
| 原始实验结果 | `external_executor/raw_results/` | CSV、JSON、日志、checkpoint 和模型输出 |
| 实验结果汇总表 | `external_executor/table/` | CSV 或 Markdown 表格，必须可回查原始结果 |
| 框架图和实验结果图 | `external_executor/figure/` | PNG 或 SVG 图表 |
| 方法说明 | `external_executor/evidence_package/realized_method_package.json` | 介绍经细化后实际形成的方法，不得描述未实现版本 |

## 完成检查与恢复边界

在外部执行器退出前，确认至少存在：

```text
external_executor/executor_research_report.md
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

还必须确认报告引用的 `external_executor/expr/`、`external_executor/raw_results/`、`external_executor/evidence_package/`、`external_executor/figure/` 和 `external_executor/table/` 文件仍存在，并与 `run_manifest.json` 的校验信息一致。缺失、hash 不一致或 Writer Handoff 未通过时，修复外部执行产物后再运行 `run-task T8`。不要手工伪造终态、`result_pack.json` 或 manifest，也不要通过修改 `state.yaml` 跳过校验。

在 `T5-EXTERNAL-WAIT` 期间，`resume` 的作用是检查四项回传文件是否齐备和合法，不会重跑 T4.5、REBOOST 或执行器。只有外部执行已经停止写入时才应使用它。
