# ResearchOS Agent Pipeline

本文档是当前 ResearchOS 工作流的**唯一主说明文档**。

它整合了原先分散的 agent 说明、实验协议、快速开始片段和部分配置说明。

如果你只想看一份文档来理解：

- 整个系统怎么流动
- 每个 Agent 负责什么
- 每个输入/输出文件是什么意思
- 单独跑某个 Agent 时到底会做什么
- 完整 pipeline 和单阶段调试的区别
- 恢复、预算、fallback、MCP、skills 在流程里扮演什么角色

那么优先读这份文档。

---

## 1. 先用一句话理解系统

ResearchOS 不是“一个大 Agent 自己想办法做完所有事情”，而是一个 **state-machine-driven, artifact-first** 的研究运行时：

```text
T1 项目初始化
 -> T2 文献检索 / 去重 / 验证 / 精读队列生成
 -> T3 精读与结构化笔记
 -> T3.5 文献综合
 -> T3.6 可选综述论文支线（runtime gate：是否撰写 survey）
 -> T4 候选池生成
 -> T4-GATE1 候选方向人工选择 / 合并 / 重分析
 -> T4 最终假设与实验计划生成
 -> T4.5 新颖性预审（非通过 verdict 进入人工决策 gate）
 -> T5-HANDOFF 外部实验协议编译
 -> T5-EXECUTOR-GATE 外部执行器选择
    -> mock_dry_run: T5-DRY-RUN 外部执行器协议 dry-run
    -> codex_cli / claude_code_window / manual: T5-EXTERNAL-WAIT 等待外部结果
 -> T7-INGEST 外部结果摄取
 -> T7-AUDIT 实验诚信审计
 -> T7-POST-NOVELTY 实验后 novelty/collision 复核
 -> T7-CLAIMS result-to-claim 与 evidence pack
 -> T7.5 PI 评估
 -> Human Gate
 -> T8 资源索引 / 分章节写作 / 拼装审计 / 审稿 / 修订
 -> T9 投稿包构建、编译、修复与收尾
```

当前主链已经废弃“ResearchOS 自己在 T5-T7 内部实现并长时间跑实验”的默认语义。ResearchOS 负责研究协议、执行器选择、handoff prompt、文件契约、结果摄取、诚信审计、实验后 novelty 复核、result-to-claim 和写作证据闭环；Codex CLI、Claude Code 窗口、人工外部执行器或 mock dry-run 负责在隔离路径中实现和运行实验。旧 `T5` Pilot、`T6` Pilot 后 novelty 复核和旧 `T7` 内部完整实验仍保留为 `LEGACY-T5-PILOT` / `LEGACY-T6-NOVELTY` / `LEGACY-T7-FULL` 兼容入口；普通 `run-task T5/T6/T7` 会报 retired 并提示新版入口，只有旧 workspace 中的 `next_task: T7` 这类状态机恢复语义会安全映射到 `T5-HANDOFF`。

其中：

- `StateMachine` 负责“下一步该跑谁”
- `AgentRunner` 负责“把某个 Agent 真正跑起来”
- `ToolRegistry` 负责“这个 Agent 能调用哪些工具”
- `workspace` 负责“所有输入输出都落盘到哪里”
- `validator` 负责“这个阶段到底算不算真正完成”

---

## 2. 当前真实阶段图

当前真实状态定义在 [config/system_config/state_machine.yaml](../config/system_config/state_machine.yaml)。

主链如下：

```text
T1
 -> T2
 -> T3
 -> T3.5
 -> T3.6-GATE-SURVEY
    -> no: T4
    -> yes: T3.6-PLAN -> T3.6-GATE-OUTLINE -> T3.6-GATE-CORPUS
            -> optional T3.6-EXPAND
            -> T3.6-STATE
            -> T3.6-SEC-BACKGROUND -> T3.6-SEC-TAXONOMY
            -> T3.6-SEC-THEME-1 -> T3.6-SEC-THEME-2 -> T3.6-SEC-THEME-3 -> T3.6-SEC-THEME-4
            -> T3.6-SEC-COMPARISON -> T3.6-SEC-CHALLENGES -> T3.6-SEC-FUTURE
            -> T3.6-SEC-INTRO -> T3.6-SEC-CONCLUSION -> T3.6-SEC-ABSTRACT
            -> T3.6-ASSEMBLE -> T3.6-REVIEW -> T3.6-COMPILE -> T3.6-FEED -> T4
 -> T4
    -> candidate pool ready: T4-GATE1 -> user chooses/selects/merges/reanalyzes -> T4
    -> final hypotheses ready: T4.5
    -> pass*: T5-HANDOFF
    -> reframe/drop/reject/collision: T4.5-HUMAN-REVIEW -> user chooses T5-HANDOFF/T4/done
 -> T5-HANDOFF
 -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> claude_code_window/codex_cli/manual: T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
 -> T7.5
 -> human gate
 -> T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-METHOD
 -> T8-SEC-EXPERIMENTS
 -> T8-SEC-RELATED
 -> T8-SEC-ANALYSIS
 -> T8-SEC-INTRO
 -> T8-SEC-CONCLUSION
 -> T8-SEC-ABSTRACT
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
 -> T9
 -> done
```

几个最容易记错的点：

- `HELLO` 是显式运行的 smoke task，不是主链起点；主链的 `initial_state` 是 `T1`
- 当前主链只有在 `T4.5` 的 `Final Gate Verdict` 明确写成 `pass_to_experiment` / `pass_with_required_baselines` 等通过枚举时才自动进入 `T5-HANDOFF`；`return_to_T4_reframe`、`drop_due_to_collision`、`reject`、`collision`、`fail`、缺失 verdict 或无法识别的 verdict 都会进入 `T4.5-HUMAN-REVIEW`，由用户选择继续外部实验链、回 T4 重构或结束项目。系统不再自动拒绝、自动回退或默认放行，避免 T4.5-T4 死循环，也避免模型在新颖性不确定时替用户做价值裁决。旧内部实验阶段保留为 legacy 兼容入口，但主链和普通 `run-task T7` 不再进入旧内部完整实验
- `T8` 不是一个节点，而是风格确认、资源索引、对齐矩阵、大纲、逐章节写作、拼装、自查、审稿、修订组成的多节点链；旧报告或旧 gate 中的 `next_task: T8` / `next_task: T8-WRITE` 会被状态机安全映射到 `T8-STYLE-GATE`，只有合法的 `drafts/writing_style.json` 已存在时才直接进入 `T8-RESOURCE`
- `T8-REVIEW` 不是当前真实状态名，当前真实状态名是：
  - `T8-REVIEW-1`
  - `T8-REVIEW-2`
- `T7.5` 已经接入主链，不再只是“设计上想加”
- `T9` 不只是打包，它现在是“构建 bundle -> 编译 -> 失败则修复并重试 -> 成功后验收”

---

## 3. 运行方式：完整 pipeline vs 单阶段调试

### 3.1 完整 pipeline

完整 pipeline 命令：

```bash
cd ResearchOS
researchos run --workspace ./workspace/local-test2
```

或者显式用当前源码入口：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli run --workspace ./workspace/local-test2
```

它的特点：

- 会推进整个状态机
- 会进入和恢复 human gate
- 会从一个 task 自动跳到下一个 task
- 会完整体现 `T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-DRY-RUN/T5-EXTERNAL-WAIT -> T7-INGEST -> T7-AUDIT -> T7-POST-NOVELTY -> T7-CLAIMS -> T7.5 -> ask_human -> T8` 这样的链条

### 3.2 恢复完整 pipeline

```bash
cd ResearchOS
researchos resume --workspace ./workspace/local-test2
```

或者：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli resume --workspace ./workspace/local-test2
```

适用场景：

- 之前跑到 gate 停住
- 预算扩限时选择了暂停
- 任务中途中断，但 workspace 已经落盘了阶段产物

### 3.3 单独跑某一个 task

```bash
cd ResearchOS
researchos run-task T3 --workspace ./workspace/local-test2
```

或者：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli run-task T3 --workspace ./workspace/local-test2
```

它的特点：

- 只跑一个 task
- 不会自动跳到下一个 task
- 仍然会做输入校验
- 仍然会做产物校验
- 仍然会注入恢复语义

### 3.4 从另一个 workspace 复制前置产物来调试

```bash
cd ResearchOS
researchos run-task T8-RESOURCE \
  --workspace ./workspace/scratch-write \
  --from ./workspace/local-test2
```

这会：

- 按 `T8-RESOURCE` 的 I/O 契约找到前置输入
- 从 `local-test2` 拷到 `scratch-write`
- 然后只跑 `T8-RESOURCE`

如果要保留来源 workspace 的 T1 和 seed，但从 T2 重新开始完整状态机，不要用 `run-task`：

```bash
researchos run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

这会按 `T2` 输入契约复制 `project.yaml`、`user_seeds/seed_papers.jsonl`、`user_seeds/pdfs/`、seed 约束/想法/外部资源和 `literature/bridge_domain_plan.json`，然后初始化 `state.yaml` 为 `current_task: T2`。旧的 T2 输出如 `papers_raw.jsonl`、`papers_verified.jsonl`、`deep_read_queue.jsonl` 不会被复制。

新写作链的推荐调试入口是 `T8-STYLE-GATE`；如果已经有合法 `drafts/writing_style.json`，也可以直接跑 `T8-RESOURCE`。如果你还使用旧命令 `researchos run-task T8 --workspace ...`，单任务运行器会把它视为 `T8-STYLE-GATE`，避免绕过风格确认、资源索引和章节计划。

### 3.5 两者到底差在哪

| 维度 | `run` / `resume` | `run-task` |
| --- | --- | --- |
| 是否推进 FSM | 会 | 不会 |
| 是否处理 human gate | 会 | 只执行当前 task，不推进后续 |
| 是否适合复现单阶段 bug | 一般 | 最适合 |
| 是否适合完整交付 | 最适合 | 不适合 |
| 是否适合测 `T7.5 -> ask_human -> T8` | 是 | 否 |

---

## 4. Workspace 是这条链的事实源

ResearchOS 的核心设计是：**进度靠文件恢复，不靠模型记忆恢复**。

因此理解 pipeline 的第一步，是理解 workspace 目录。

以 `./workspace/local-test2` 为例，典型目录包括：

- `project.yaml`
- `state.yaml`
- `user_seeds/`
- `literature/`
- `resources/`
- `ideation/`
- `novelty/`
- `external_executor/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`

其中：

- `project.yaml` 是研究对象和方向
- `state.yaml` 是状态机状态
- `_runtime/` 是运行时信息
  - `resume/`
  - `logs/`
  - `traces/`

`init-workspace`、`run`、`resume` 和 `run-task` 会幂等刷新标准目录树，并为 workspace 根目录和每个标准子目录生成 `_DIR_GUIDE.md`。这些 guide 不是论文内容，而是目录协议说明；当前采用两张表：目录协议表说明目录由哪个阶段生成、被哪个阶段消费、人工/agent 可编辑范围、禁止放入内容和校验规则；关键文件表列出核心文件/子目录及用途。

新 workspace 默认只创建当前主链目录。legacy `pilot/`、顶层 `reviews/` 和 workspace-local `skills/` 不再默认创建；旧 workspace 如果已经存在这些目录，runtime 会补 legacy/optional guide，但不会删除或移动已有产物。`external_executor/workdir`、`resources/repos`、PDF/figure 等可能包含外部代码或资产的子树不会被递归污染。已有自定义 `_DIR_GUIDE.md` 会保留，只有 ResearchOS 生成式 guide 会被刷新。

### 4.1 task I/O 契约是什么

每个 task 的输入输出契约定义在：

- [researchos/orchestration/task_io_contract.py](../researchos/orchestration/task_io_contract.py)

它决定：

- 单任务运行前要检查哪些输入
- `--from` 该复制哪些前置文件
- validator 至少期待哪些输出

### 4.2 为什么很多阶段支持“续跑”

因为这些阶段会显式读取已有 artifact，例如：

- `T3` 会读取已有 `paper_notes/`
- `T3.6` 会读取已有 `survey_plan.json`、`survey_state.json`、`sections/*.tex`、`survey_audit.json` 和编译日志，按 section 续写/续编译
- 外部实验链会读取已有 handoff/result_pack/ingest/audit/result-to-claim 产物并补缺；旧 `T5/T7` 兼容节点才会读取已有代码和内部结果目录
- `T7.5` 会读取已有 `evaluation_decision.md`
- `T9` 会读取已有 `submission/bundle/` 和编译痕迹

所以 ResearchOS 的恢复语义本质上是：

- 不是“从模型内部会话上下文恢复”
- 而是“从 workspace 已经写出来的事实恢复”

---

## 5. 整体阶段速览

| Task | Agent | 模式 | 核心目标 | 主要输出 |
| --- | --- | --- | --- | --- |
| `HELLO` | `HelloAgent` | - | 显式 smoke test，不在主链中自动执行 | `hello.txt` |
| `T1` | `PIAgent` | `init` | 初始化研究项目与种子信息 | `project.yaml`, `state.yaml` |
| `T2` | `ScoutAgent` | - | 检索、保留候选/backlog 分层、验证、构建引用图领域地图和精读队列 | `papers_raw`, `papers_dedup`, `papers_verified`, `papers_backlog`, `citation_edges`, `domain_map`, `deep_read_queue` |
| `T3` | `ReaderAgent` | `read` | 逐篇精读并形成结构化证据（含 §13 Mechanism Claim、§14-§19 CDR 和 abstract A/B 桥接字段） | `paper_notes/`, `paper_notes_abstract/`, `metadata_triage.md`, `comparison_table.csv`, `related_work.bib` |
| `T3.5` | `ReaderAgent` | `synthesize` | 以引用图为骨架、叠加 LLM 综述判断，生成领域综合和邻接迁移素材 | `synthesis_workbench.json`, `synthesis_outline.md`, `synthesis_draft.md`, `synthesis.md` |
| `T3.6-GATE-SURVEY` | runtime gate | `survey_gate` | 状态机级 immediate gate；询问是否撰写 taxonomy-driven survey；否直接进入 T4，不启动 LLM | `drafts/survey/decision.json` |
| `T3.6-PLAN` 到 `T3.6-FEED` | `SurveyWriterAgent` | survey 系列 | 可选综述论文支线：taxonomy 规划、人工确认、逐 section 写作、拼装、综述模式 review、编译、导出 T4 idea fuel | `drafts/survey/survey_plan.json`, `survey_state.json`, `sections/*.tex`, `survey.tex`, `survey_review.md`, `survey.pdf`, `ideation/survey_insights.json` |
| `T4` | `IdeationAgent` | - | 先生成 Pass1/Pass2/Gate1 候选池并停到 T4-GATE1；用户选择后再生成最终假设、实验计划、决策链和风险评估；可消费 `survey_insights.json` | `_pass1_forward_candidates.json`, `_pass2_grounding_review.json`, `_candidate_directions.json`, `_gate1_selection_brief.md`, `hypotheses.md`, `exp_plan.yaml`, `idea_scorecard.yaml`, `rejected_ideas.md`, `gate_decisions.json`, `idea_rationales.json`, `risks.md`, `_family_distribution.md` |
| `T4-GATE1` | runtime gate | - | 状态机级 immediate gate；展示全量候选池、Pass2 风险、bridge 覆盖和合并建议，由用户选择/合并/新增/要求重分析 | `ideation/_gate1_user_selection.json` |
| `T4.5` | `NoveltyAuditorAgent` | - | 对假设做新颖性预审（含 mechanism tuple 碰撞检测）；非通过 verdict 进入人工决策 gate | `novelty_audit.md`, `_mechanism_tuples/`; 有撞车时另写 `collision_cases.md` |
| `T5-HANDOFF` | `ExperimenterAgent` | `handoff` | 编译外部实验执行协议、allowed paths、Codex/Claude/manual prompt、AGENTS/CLAUDE 指南和 required baselines；不运行真实实验 | `external_executor/handoff_pack.json`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `executor_prompt.md`, `codex_prompt.md`, `claude_code_prompt.md`, `expected_outputs_schema.json`, `allowed_paths.txt`, `job_state.json` |
| `T5-EXECUTOR-GATE` | `ExperimenterAgent` | `executor_gate` | 状态机级 immediate gate；用户选择 mock/Claude Code/Codex CLI/manual，并确定性 patch 执行器说明文件 | `external_executor/executor_selection.json` |
| `T5-EXTERNAL-WAIT` | `ExperimenterAgent` | `external_wait` | 无 LLM 的等待/恢复边界；检查外部执行器是否写回 result pack/status，缺失则 PAUSED，resume 后继续 | `external_executor/wait_acceptance_report.json` |
| `T5-DRY-RUN` | `ExperimenterAgent` | `dry_run` | 用 mock executor 跑通 result_pack/status/manifest/raw/config/log 文件协议；显式 `mock_only=true` | `external_executor/result_pack.json`, `executor_status.json`, `run_manifest.json`, `heartbeat.json`, `raw_results/`, `configs/`, `logs/` |
| `T7-INGEST` | `ExperimenterAgent` | `result_ingest` | 摄取外部 result pack，规范化为 ResearchOS 下游可读结果 | `experiments/results_summary.json`, `run_records.jsonl`, `evidence_index.json`, `ingest_report.json` |
| `T7-AUDIT` | `ExperimenterAgent` | `integrity_audit` | 审计 provenance、hash、metric source、mock_only、run_manifest 和 required baseline 覆盖；不相信执行器总结 | `experiments/integrity_audit.json`, `experiments/experiment_fairness_review.md` |
| `T7-POST-NOVELTY` | `ExperimenterAgent` | `post_novelty` | 基于实现/结果状态和 required baseline 覆盖复核 novelty/collision 与 claim 降级边界 | `novelty/post_experiment_novelty_check.json`, `novelty/post_experiment_collision_cases.md` |
| `T7-CLAIMS` | `ExperimenterAgent` | `result_to_claim` | 把审计后的实验数字转成保守 claim mapping，并生成 T8 evidence pack、must-not-claim 和 support matrix | `experiments/experimental_claims.json`, `drafts/result_to_claim.json`, `drafts/experiment_evidence_pack.json`, `drafts/must_not_claim.md`, `drafts/claim_support_matrix.csv`, `experiments/iteration_log.md` |
| `LEGACY-T5-PILOT` | `ExperimenterAgent` | `pilot` | legacy 兼容节点：显式旧内部小规模实验 | `pilot_plan.yaml`, `pilot_code/`, `pilot_results.json`, `motivation_validation.md` |
| `LEGACY-T6-NOVELTY` | `NoveltyAgent` | - | legacy 兼容节点：基于 Pilot 做增量 novelty 复核 | `novelty_report.md`, `collision_cases.md`, `must_add_baselines.md` |
| `LEGACY-T7-FULL` | `ExperimenterAgent` | `full` | legacy 兼容节点：旧内部完整实验；普通 `run-task T7` 不再进入这里 | `results_summary.json`, `runs/`, `configs/`, `iteration_log.md`, `ablations.csv` |
| `T7.5` | `PIAgent` | `evaluate` | 评估外部实验审计/result-to-claim 是否足以写论文 | `evaluation_decision.md` |
| `T8-STYLE-GATE` | `WriterAgent` | `style_gate` | 用 `ask_human` 确认 IS / CCF-A / both 写作风格，并记录人工交互 provenance | `drafts/writing_style.json` |
| `T8-RESOURCE` | `WriterAgent` | `resource_index` | 索引写作资源，消费 Pre-T5 材料和外部实验 evidence pack/result-to-claim，并生成章节/证据/图表计划和对齐矩阵 seed | `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, `drafts/figure_table_plan.json`, `drafts/alignment_matrix.json` |
| `T8-WRITE` | `WriterAgent` | `outline` | 基于资源索引和 alignment matrix 写论证大纲 | `drafts/outline.md` |
| `T8-SECTION-PLAN` | `WriterAgent` | `section_plan` | 初始化逐章节共享状态和每章作业单 | `drafts/paper_state.json`, `drafts/section_outlines/*.md` |
| `T8-SEC-METHOD` | `WriterAgent` | `section_draft` | 只写 Method 单章 | `drafts/sections/methodology.tex` |
| `T8-SEC-EXPERIMENTS` | `WriterAgent` | `section_draft` | 只写 Experiments 单章 | `drafts/sections/experiments.tex` |
| `T8-SEC-RELATED` | `WriterAgent` | `section_draft` | 只写 Related Work 单章 | `drafts/sections/related_work.tex` |
| `T8-SEC-ANALYSIS` | `WriterAgent` | `section_draft` | 只写 Analysis/Discussion 单章 | `drafts/sections/analysis.tex` |
| `T8-SEC-INTRO` | `WriterAgent` | `section_draft` | 在 Method/Experiments 后只写 Introduction | `drafts/sections/introduction.tex` |
| `T8-SEC-CONCLUSION` | `WriterAgent` | `section_draft` | 只写 Conclusion，并在其中写 `\subsection{Limitations}` | `drafts/sections/conclusion.tex` |
| `T8-SEC-ABSTRACT` | `WriterAgent` | `section_draft` | 最后只写 Abstract | `drafts/sections/abstract.tex` |
| `T8-DRAFT` | `WriterAgent` | `draft` | 用 tool 拼装章节、spot-check、claim audit、craft/alignment audit | `drafts/paper.tex`, `drafts/manuscript_audit.md`, `drafts/craft_audit.md` |
| `T8-SELF-CHECK` | `WriterAgent` | `self_check` | 作者自查数字、引用、图表、论证链、craft/alignment FAIL/WARN | `drafts/self_check.md` |
| `T8-REVIEW-1` | `ReviewerAgent` | round 1 | 第一轮逐章节审稿和综合审稿 | `drafts/review_rounds/round_1_sections/*.md`, `drafts/review_rounds/round_1.md` |
| `T8-REVISE-1` | `WriterAgent` | `revise` | 第一轮按 section patch 修订 | `drafts/patches/round_1_patches.json`, `drafts/revision_response_round_1.md`, `drafts/paper.tex` |
| `T8-REVIEW-2` | `ReviewerAgent` | round 2 | 第二轮逐章节审稿，检查上一轮闭环 | `drafts/review_rounds/round_2_sections/*.md`, `drafts/review_rounds/round_2.md` |
| `T8-REVISE-2` | `WriterAgent` | `revise` | 第二轮按 section patch 修订 | `drafts/patches/round_2_patches.json`, `drafts/revision_response_round_2.md`, `drafts/paper.tex` |
| `T8-PAPER-CLAIM-AUDIT` | `WriterAgent` | `paper_claim_audit` | T9 前最终检查 paper.tex 的数字/claim 是否能追溯到 result-to-claim/evidence pack | `drafts/paper_claim_audit.md`, `drafts/paper_claim_audit.json` |
| `T9` | `SubmissionAgent` | - | 投稿包构建、编译、修复、验收 | `submission/bundle/`, `migration_report.md` |

---

## 6. 每个 Agent 的详细逻辑

下面开始按 task 详细展开。这里会尽量说明：

- 读什么文件
- 写什么文件
- 文件字段到底什么意思
- 工具怎么调用
- 单独运行和完整运行时的语义区别
- 恢复机制怎么生效

建议你把每个阶段都按下面这 5 类代码锚点一起看：

1. `researchos/agents/*.py`
   - Agent 类、`initial_user_message()`、`validate_outputs()`
2. `researchos/prompts/*.j2`
   - prompt 里的工作指令、阶段约束、输出结构
3. `config/system_config/state_machine.yaml`
   - 这个阶段在完整 pipeline 里的真实节点名、分支和后继
4. `researchos/orchestration/task_io_contract.py`
   - 输入输出 artifact 契约
5. `config/agent_params.yaml`
   - 模型、预算、工具、读写权限、恢复相关参数

### 运行时的 tool + prompt 分工

当前前 T5 链路不再把所有关键动作都压给 prompt，也不把所有事情都强行交给 LLM。原则是：

- 可机械复现、重复、可验证、不依赖领域知识的步骤放到 tool / runtime：T2 raw 自动落盘与收尾、去重、metadata verification、deep-read queue、PDF 覆盖校验、T3.5 synthesis workbench、schema validation。
- 需要领域知识、论文理解、语义消歧、机制判断或写作取舍的部分交给 LLM prompt / guidance：query 语义设计、domain profile、论文重要性解释、方法家族、共同假设、趋势、研究问题、假设选择、新颖性风险判断。
- 可以让 tool 先产出 hint / provenance / coverage telemetry，再由 LLM 审阅补充；但 tool hint 不能被写成最终学术结论。
- prompt 里写到的结构化输出，如果在 `structured_outputs` 或 task contract 中声明，会由 runtime/validator 再检查，不只相信模型自述。

这也是当前补强方向：优先把“可机械复现、可测试、容易出错”的步骤工具化，让 LLM 专注在判断和解释上。反过来，凡是需要领域知识、论文理解、机制判断或写作取舍的内容，不应该写死在 Python 模板里。

典型例子是 T3.5 的 `LLMInsights` 机制：Reader LLM 先生成方法家族分类、共同假设、趋势和研究问题（LLM-first），然后通过 `build_synthesis_workbench(llm_insights={...})` 传入工具做结构化组装；工具不再硬编码关键词匹配或模板分类，而是忠实使用 LLM 提供的洞察。如果 LLM 未提供某类洞察，工具回退到 `LLM_REVIEW_REQUIRED` 占位，等待 LLM 后续审阅补充。

### LLM-first guidance 与 hard rule 边界

当前内置 Agent 使用 `researchos/agent_guidance/*/SKILL.md` 作为轻量 Skill 式指导块注入 prompt。这类 guidance 只告诉 LLM 如何思考和如何使用工具，不直接替代模型判断。

硬规则包括：

- workspace 路径、读写权限、schema、JSONL/CSV/BibTeX 格式
- PDF 页码覆盖、截断记录、FULL-TEXT / PARTIAL-TEXT / ABSTRACT-ONLY 判定
- metadata verification、去重、真实 API 来源、不能编造论文
- 输出文件是否存在、章节/字段是否完整
- 论文 ID 与文件路径规范化：正文引用可以保留真实 ID，如 `[arxiv:2301.12345]`；文件名和路径必须使用安全 ID，如 `arxiv_2301.12345.md`

LLM 判断包括：

- `domain_profile`：研究领域、歧义词、include/exclude concepts、相关子领域
- source_type / method_family / why_relevant 等学术解释
- T3.5 的方法家族、共同假设、技术趋势、研究问题（通过 `LLMInsights` 传入工具）
- T4/T4.5 的机制差异、新颖性等级、baseline 是否必须加入
- T4.5 的 mechanism keyword 提取（结构化模式匹配 + LLM 上下文调整）

工具只能整理证据、保留 provenance、做可解释 hint；最终学术结论必须由对应 Agent 的 LLM 审阅后写出。反过来，像去重、schema、页码覆盖、ID 规范化、JSONL/CSV/BibTeX 写入这类机械工作，不应反复调用 LLM 处理。

需要特别区分两类“看起来像规则”的内容：

- `informs_search` 确实是 INFORMS 期刊记录检索，适合 OR/MS、management science、supply chain、queueing、optimization 等 INFORMS 覆盖强的方向；这里保留的是数据源覆盖特性说明，不是把主题相关性判断写进工具。当前 T2 默认启用它作为补充检索源，返回为空或失败时记录并继续，不让它阻塞主检索。
- `[arxiv:2301.12345]` 这类正文引用是合法的，因为它保留了真实论文 ID；只有写文件路径时才必须规范化成 `arxiv_2301.12345.md` / `arxiv_2301.12345.pdf`。validator 应兼容正文中的原始 ID 和规范化 ID，但不能容忍编造不存在的 ID。

### `updataPreT5.md` / Pre-T5 + T8 落地对照

`/mnt/data/reference/updataPreT5.md` 强调的 T2/T3/T3.5/T4/T4.5/T8 不是只停留在文档层。当前已经落到 prompt、tool、validator 和状态契约中，但要区分“已经强制校验”和“仍由 LLM 学术判断完成”的部分。

| updataPreT5.md 项 | 当前落点 | 状态 |
| --- | --- | --- |
| CDR 单一事实源 | `config/system_config/cdr_schema.yaml` 定义 `problem_frame`、`design_rationale`、`artifact`、`data_view`、`evaluation_mode`、`contribution_type`、`boundary_conditions`、`cross_paper_tension`，并明确 provenance 不是质量门 | 已落地 |
| T2 检索广度和跨域召回 | `researchos/prompts/scout.j2` 默认启用 `informs_search`，允许 `query_bucket=adjacent_field/theory_bridge` 和 `bridge_id` 作为召回意图；Scout LLM 优先对 seed 邻域、bridge/must_explore、高优先级主线候选输出 `semantic_screen`，`apply_semantic_screening` 只合并判定；`build_domain_map` 只让 LLM-screened 论文进入 core/theory/adjacent，`build_deep_read_queue` 则要求 verified 池 100% 保留为 deep_read 或 shallow_read/backlog，不把 bucket/retrieval_intent 当语义准入 | 已落地 |
| T2 引用图主轴 | `fetch_outgoing_citations` 读取 OpenAlex outgoing references + related works，并解析少量一跳候选论文；OpenAlex/Crossref/seed 记录保留 `canonical_id`、`referenced_works`、`related_works`、`refs_unavailable`，runtime 会把 `data.papers` 自动追加进 `papers_raw.jsonl`，同时把 `source_id -> referenced_works/related_works` 独立追加到 `literature/citation_edges.json`；`build_domain_map` 生成含 `core/theory_bridge/adjacent/boundary/audit` 的 `domain_map.json` | 已落地 |
| T3 note schema 扩展 | `researchos/prompts/reader.j2` read 模式从 13 节扩为 19 节，新增 `§14 Design Rationale` 到 `§19 Cross-Paper Tension`；`researchos/agents/reader.py::_validate_cdr_note_fields` 校验字段和 `contribution_type` 枚举 | 已落地 |
| T3 abstract-only 桥接字段 | `abstract_sweep.py` 和 `reader.j2` 要求 abstract-only note 写 `## A. 核心做法/视角` 与 `## B. 桥接点`；`reader.py` 对 `paper_notes_abstract/` 以及 `paper_notes/` 中 `[ABSTRACT-ONLY]` note 都做结构校验 | 已落地 |
| T3 FULL-TEXT / 截断校验 | `reader.j2` 要求分块重读覆盖全部页码；`reader.py` 校验 `Reading Coverage`、页码范围、最终 `Truncation` 状态和 Key Results evidence anchor | 已落地 |
| T3 resume 防重读 | Reader 进入时优先 `deep_read_queue_pending.jsonl`，runtime 会刷新 `notes_manifest.json` 和 pending queue/meta；按 queue rank 记录 complete/incomplete/missing，多 key 匹配 `normalized_id`、原始 ID、标题、DOI，避免 resume 后把已读论文重写 | 已落地 |
| T3.5 贡献空间综合 | `reader.j2` synthesize 模式改为 LLM 先分析，再把 `LLMInsights` 传给 `build_synthesis_workbench`；`literature_synthesis.py` 生成 `contribution_space` 与 `cross_paper_tensions` | 已落地 |
| T3.5 邻接/理论桥接迁移 | `build_synthesis_workbench` 读取 `domain_map.json`，输出 `citation_graph_context`、`domain_map_bucket_summary`、`adjacent_transfers` 和 `bridge_transfer_drafts`；`synthesis.md` 必须包含“邻接领域可迁移机制”章节或说明语料邻接覆盖不足 | 已落地 |
| T3.5 不硬编码知识 | `build_synthesis_workbench` 只结构化证据和 LLM 洞察；方法家族、共同假设、趋势、研究问题由 LLM 提供，缺失时写 `LLM_REVIEW_REQUIRED`，不把工具 hint 当最终综述 | 已落地 |
| T4 两段式 ideation | `researchos/prompts/ideation.j2` 明确 Pass 1 前向生成和 Pass 2 文献接地；Pass 1 主线包含 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`evidence_driven` | 已落地 |
| T4 四类约束降级为补充 | `ideation.j2` 把【机制质疑型、反向操作、子群失败、缺口探索】放在 Step 2.5，定义为 coverage supplements；`_candidate_directions.json` 必须区分 `constraint_status=mainline/supplement/bridge/not_supported_by_current_evidence` | 已落地 |
| T4 provenance 不再 gate | `supporting_papers`、`closest_baselines`、`from_synthesis_section` 为 optional 文档字段；`prior_art: none` 合法且表示高新颖/高风险，不因无 baseline 被降分 | 已落地 |
| T4 anti-incrementalism gate | `ideation.py` 对 selected / hypothesis-linked idea 校验 `design_rationale`、`contribution_type`、`contribution_character`、`contribution_strength`；`routine` 不能作为 selected idea 通过 | 已落地 |
| T4 多样性与主线来源 | `ideation.py` 要求 `_candidate_directions.json` 和 `idea_scorecard.yaml` 记录 `idea_origin`、`constraint_status`；validator 会拒绝候选池只由四类补充通道构成 | 已落地 |
| T4 软 novelty/集中度诊断 | `ideation_tools.py` 提供 `analyze_idea_concentration` 和 `compute_idea_novelty_signal`；`idea_scorecard.yaml` 必须记录 `counterfactual_check`、`nearest_prior_work`、`novelty_signal`，Gate1 brief 必须显示集中度提示、Origin 分布和 Novelty-Utility 谱系。字段存在性用于防止跳过审阅，不按好坏 gate；材料不足时允许 `insufficient_evidence`、`not_computed`、`domain_map_unavailable` 并要求说明原因，避免硬编三分类 | 已落地 |
| T4.5 collision + ambition | `novelty_auditor.j2` 要求同时写 Collision Axis 和 Ambition Axis；`mechanism_tools.py` 增加 `extract_design_rationale_tuple` / `compare_design_rationale_tuples`；`novelty_auditor.py` 校验 `_design_rationale_tuples/` 与 routine reframe 要求 | 已落地 |
| T4.5 非通过 verdict 人工决策 | `novelty_auditor.j2` 要求写 `Final Gate Verdict`；状态机对 T4.5 使用 `__parse_from_output__`：只有 `pass_to_experiment` / `pass_with_required_baselines` 等明确通过枚举进入 `T5-HANDOFF`，`return_to_T4_reframe` / `drop_due_to_collision` / `reject` / `collision` / `fail`、缺失 verdict 和未知 verdict 都进入 `T4.5-HUMAN-REVIEW`。该节点是 `immediate_gate`，不启动 LLM，不自动拒绝、自动回 T4 或默认放行；用户查看 `novelty_audit.md`、Gate1 brief、scorecard 后选择继续外部实验链、回 T4 或结束，决策落盘到 `ideation/novelty_human_review.json` | 已落地 |
| T7.5/T8 消费 CDR 与 Pre-T5 新产物 | T8-RESOURCE 生成 `cdr_claim_ledger.json` 和 `alignment_matrix.json`，并通过 task contract 复制 `domain_map.json`、`synthesis_workbench.json`、`idea_scorecard.yaml`、`writing_style.json`；这些文件在 `T8-RESOURCE`、`T8-WRITE`、`T8-SECTION-PLAN` 与 `T8-SEC-RELATED` 中是单任务强前置，避免 Related Work 静默降级；Related Work 消费 `adjacent_transfers`、`bridge_transfer_drafts`、`domain_map.theory_bridge`、`cross_domain_sources` 和 `nearest_prior_work`，alignment rows 消费 `counterfactual` / `novelty_signal`；`audit_writing_craft` 会 WARN 检查 Related Work 是否可见地使用最近工作、邻接迁移或 cross-paper tension 信号；Reviewer 增加 `CDR Contribution Verdict` | 已落地 |
| T9 编译校验 | T9 生成 `compile_report.json`，记录 LaTeX 构建、hash/mtime、错误日志和 PDF artifact；不是只看 `paper.pdf` 是否存在 | 已落地 |

这里的 CDR schema 和 tool 输出都是“结构化职责”和“证据脚手架”，不是 deterministic 学术模板。需要知识、判断和写作的地方仍由 LLM 完成：T3 的 design rationale 判断、T3.5 的方法家族/张力综合、T4 的前向生成、T4.5 的 novelty/ambition 解释、T8 的最终论文叙事，都不能由工具硬编码替代。

---

## 6.1 HELLO

### 角色

- Agent：`HelloAgent`
- 代码： [researchos/agents/hello.py](../researchos/agents/hello.py)

### 输入

无。

### 输出

| 文件 | 含义 |
| --- | --- |
| `hello.txt` | runtime 最小成功信号 |

### 内部逻辑

1. 调用简单 shell / echo
2. 写入 `hello.txt`
3. 调用 `finish_task`

### 实际执行过程

`HelloAgent` 是最小 runtime smoke test。它不会读取研究上下文，只根据 `hello.j2` 提示调用 `echo` 或直接组织固定内容，然后用 `write_file(path="hello.txt", content="Hello, Runtime!")` 写入 workspace 根目录。写完后它会用 `read_file` 或 validator 侧读取确认文件内容，最后调用 `finish_task`。`validate_outputs()` 不只检查文件存在，还要求 `hello.txt` 的内容精确等于 `Hello, Runtime!`，所以它能同时验证工具注册、写权限、输出契约和 finish-task 收尾链路。

### 什么时候用

- 新环境 smoke test
- 验证：
  - LLM 能不能调用
  - tool 能不能调用
  - workspace 能不能写
  - validator 能不能收尾

### 命令例子

```bash
cd ResearchOS
researchos run-task HELLO --workspace ./workspace/dev-smoke
```

---

## 6.2 T1：PIAgent（init）

### 角色

- Agent：`PIAgent`
- mode：`init`
- 代码： [researchos/agents/pi.py](../researchos/agents/pi.py)
- Prompt： [researchos/prompts/pi.j2](../researchos/prompts/pi.j2)

### 当前默认配置

- model tier：`heavy`
- 主要工具：
  - `read_file`
  - `write_file`
  - `ask_human`
  - `finish_task`
  - `process_seed_paper`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| - | `project.yaml` | 否 | T1 之前通常还没有 |
| - | `user_seeds/seed_papers.jsonl` | 否 | 用户手工提供的种子论文 |
| - | `user_seeds/seed_outline_profile.json` | 否 | 用户 Markdown 综述/研究提纲规范化后的结构化 profile；由 `normalize_seed_outline` 或 runtime helper 生成 |
| - | `user_seeds/seed_ideas.md` | 否 | 用户已有的想法 |
| - | `user_seeds/seed_constraints.md` | 否 | 预算、硬件、目标 venue 等约束 |
| - | `user_seeds/seed_external_resources.jsonl` | 否 | 数据集、模型、代码库、repo、法规、标准、治理框架等外部资源 |
| - | `user_seeds/bridge_domains.yaml` | 否 | 可选预置桥接领域；T1 会展示给用户确认，不直接升级为 must_explore |

除了文件输入，T1 还会接收 CLI 层的主题参数，例如：

- `--topic`
- `--project-id`

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `project` | `project.yaml` | 项目总配置，后续几乎所有 task 都依赖它 |
| `state` | `state.yaml` | 初始状态机状态 |
| - | `user_seeds/seed_papers.jsonl` | 如用户在 T1 阶段补充了 seed papers |
| - | `user_seeds/seed_ideas.md` | 如用户在 T1 阶段明确了想法 |
| - | `user_seeds/seed_constraints.md` | 如用户给出硬约束 |
| - | `user_seeds/seed_external_resources.jsonl` | 如用户给出额外资源 |
| `bridge_domain_plan` | `literature/bridge_domain_plan.json` | T2 跨领域召回计划；可以为空计划 |

### `project.yaml` 到底存什么

通常包含：

- `project_id`
- `research_direction`
- `keywords`
- `target_venue`
- `constraints`
- `submission` 或其他扩展字段

它的作用是让后续阶段不再靠用户聊天内容理解项目，而是靠结构化配置理解项目。

### 内部逻辑

T1 的本质不是“自由聊天”，而是一个结构化初始化阶段：

1. runtime 先执行启动补充 gate，让用户在扫描 `user_seeds/` 前补充或确认材料入口
2. 读取已有 seeds
3. 通过多轮交互明确研究方向和边界
4. 收集用户已有资源
5. 形成 `project.yaml`
6. 生成或确认 `literature/bridge_domain_plan.json`
7. 对 `project.yaml` 和 bridge plan 做 schema/语义校验
8. 进行伦理/风险 screening
9. 写入 `state.yaml`

### 实际执行过程

`PIAgent(init)` 启动时，`AgentRunner` 会先于第一次 LLM 调用执行一次 runtime 级 `T1 启动补充 gate`：调用 `ask_human` 询问用户是否还要补充 seed PDFs、arXiv/DOI、初步想法、硬约束、目标 venue、预算/GPU 或外部资源。这个 gate 的目的，是在系统扫描 `user_seeds/` 之前给用户一次明确补充/确认机会，避免后续 T2/T3/T4 基于过期或缺失材料启动。回答会写入 `_runtime/t1_startup_gate.json` 和 `_runtime/human_interactions.jsonl`；resume 时如果该文件已经存在，runtime 会复用回答并注入上下文，不会重复弹窗。若输入不可用或回答为空，run 会进入可恢复暂停，不会继续让模型假装用户已经确认。

启动 gate 完成后，`PIAgent(init)` 才从 CLI / `ExecutionContext.extra` 读取用户主题，并调用 `inspect_user_seeds`、`list_files` 和 `read_file` 检查 workspace 中已经存在的 `user_seeds/seed_papers.jsonl`、`user_seeds/seed_ideas.md`、`user_seeds/seed_constraints.md`、`user_seeds/seed_external_resources.jsonl` 和用户 Markdown 提纲。若发现类似 `/mnt/data/reference/算法风险综述_种子提纲.md` 这类 seed outline，必须先规范化为 `user_seeds/seed_outline_profile.json`；runtime 也有 deterministic helper 兜底。规范化只派生 seed ideas、constraints 和 external resources，不会把 `representative_literature_directions` 写成 `seed_papers.jsonl`。这一步只是收集上下文，不应该再额外弹输入框；如果日志里出现“我来检查已有材料”，那只是状态说明。任何真正需要用户选择、确认或补充的地方，仍必须调用 `ask_human`，不能只在普通文本里提问然后继续执行。

随后它会通过 `ask_human` 分轮访谈。每个 `ask_human.question` 必须说明三件事：当前处于 T1 第几轮、为什么需要用户回答、用户应该补哪些字段。草案确认和 Bridge Domain Plan 选择必须把 `project.yaml` 草案或候选方向清单直接写进 `question`，不能只写“请确认以上”。如果模型仍写了依赖前文的短问题，runner 会把同一轮 Agent 正文自动并入输入问题，避免用户只看到输入框却看不到草案/候选。典型轮次是：

| 轮次 | 为什么需要问用户 | 需要回答什么 |
| --- | --- | --- |
| 第1轮 | `project.yaml` 需要明确研究边界，否则 T2/T4 会检索和构思过宽 | 研究问题、范围、不做什么、预算/GPU/venue/截止日期 |
| 第2轮 | 后续文献检索和 idea generation 需要 seed 作为偏好和约束 | 种子论文、已有想法、硬约束；已在 `user_seeds/` 中发现的内容可直接确认 |
| 第2.5轮 | T5 外部实验 handoff 需要可复用资源 | 数据集、代码仓库、benchmark、baseline、预训练模型 |
| 第3轮 | 写入 `project.yaml` 前需要用户确认 | 草案是否正确、是否需要修改 |
| 桥接轮 | T2 需要知道是否重点探索跨领域迁移素材 | LLM 提出候选 bridge domains；用户可选择重点交叉、删除、手动新增或全部跳过 |

如果用户给出论文条目，T1 优先用 `process_seed_paper` 规范化后写入 seed 文件。桥接轮由 LLM 先根据 `project.yaml` 草案、seed papers 和 seed ideas 生成候选方向，再通过 `ask_human` 让用户确认。这个 gate 必须允许四类选择：重点交叉（`priority=must_explore`）、普通交叉（`priority=should_explore`）、删除某些方向、或“不交叉/全部跳过”。正式 `literature/bridge_domain_plan.json` 只包含用户确认后的清单；一旦写入正式清单，条目就是 confirmed bridge，条目内 `source=user|auto` 只记录候选最初来自用户还是 LLM 建议，不再决定是否 confirmed。用户选择不交叉时必须写合法空计划：`{"semantics":"bridge_domain_plan","source":"none","bridge_domains":[]}`；此时 T2 不做 bridge 专属 query，T3 不强制读 bridge 论文，T4 也不强制生成 `bridge_synthesis` idea。最终它用 `write_structured_file` 生成 `project.yaml` 和 `literature/bridge_domain_plan.json`，必要时写 `state.yaml` 和 seed artifacts。`bridge_domain_plan.json` 不能写在 workspace 根目录：`write_file` 会拒绝这类结构化产物，`write_structured_file(schema_name="bridge_domain_plan")` 也只接受 `literature/bridge_domain_plan.json`，因为 T2 只读取这个正式路径。收尾时 `validate_outputs()` 会用 `project` 与 `bridge_domain_plan` schema 检查结构，并检查 source=none 时清单必须为空、非空清单必须有 `bridge_id` 和专属 `queries`；如果发现敏感研究方向，伦理 screening 会阻止完成。

T1 可能显得比普通聊天久，原因是它不是一次问答，而是要把人类偏好、已有材料、外部资源和约束整理成可被 T2-T9 复用的结构化事实源。若 workspace 已经有完整 `project.yaml` 和 seed 文件，可以直接从 T2 或后续节点恢复/调试；否则 T1 必须先问清楚，避免后面大量 LLM/检索/实验资源浪费在错误方向上。

### 单独运行 vs 完整运行

- 单独运行 `run-task T1`
  只会完成初始化文件，不会自动进入 T2
- 完整运行 `run`
  T1 成功后会自动进入 T2

### 命令例子

完整初始化：

```bash
cd ResearchOS
researchos init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"

researchos run-task T1 --workspace ./workspace/local-test2
```

或者从头开始完整链路：

```bash
cd ResearchOS
researchos run --workspace ./workspace/local-test2
```

---

## 6.3 T2：ScoutAgent

### 角色

- Agent：`ScoutAgent`
- 代码： [researchos/agents/scout.py](../researchos/agents/scout.py)
- Prompt： [researchos/prompts/scout.j2](../researchos/prompts/scout.j2)
- 契约： [researchos/orchestration/task_io_contract.py](../researchos/orchestration/task_io_contract.py)

T2 不是“随便搜几篇论文”，而是完整的：

1. query 扩展
2. 多源检索
3. 原始结果保存
4. 去重
5. relevance 打分
6. metadata verification
7. access triage
8. deep-read queue 构建

### 当前默认配置

T2 的日常预算和模型路由以 `config/user_settings.yaml` 为入口；checked-in 默认
`budget.defaults.unlimited_budget: true` 时不会因 step/token/wall 触发预算暂停。
`config/agent_params.yaml` 只声明 Scout 的工具、权限、prompt 和 behavior 默认值，不是日常预算配置表。

主要工具包括：

- `multi_source_search`
- `search_papers`
- `semantic_scholar_search`
- `arxiv_search`
- `openalex_search`
- `crossref_search`
- `fetch_paper_metadata`
- `openalex_get_work`
- `crossref_get_work`
- `expand_queries`
- `detect_duplicate_queries`
- `deduplicate_papers`
- `score_papers`
- `enrich_papers`
- `build_verified_papers`
- `build_deep_read_queue`
- `fetch_outgoing_citations`
- `build_domain_map`
- `elsevier_scopus_search`
- `informs_search`
- `log_scout_progress`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 研究方向、关键词、目标 venue、预算等 |
| `seed_papers` | `user_seeds/seed_papers.jsonl` | 否 | 用户强相关 seed papers，优先级最高 |
| `seed_constraints` | `user_seeds/seed_constraints.md` | 否 | 检索范围、年份、 venue 或其他限制 |
| `seed_ideas` | `user_seeds/seed_ideas.md` | 否 | 用户已有方向，应该转成 query 语义 |
| `seed_external_resources` | `user_seeds/seed_external_resources.jsonl` | 否 | 数据集、repo、模型名，可作为检索锚点 |
| `bridge_domain_plan` | `literature/bridge_domain_plan.json` | 否但完整链路会由 T1 写出 | 跨领域召回计划；`must_explore` 只来自用户确认，空计划合法 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `papers_raw` | `literature/papers_raw.jsonl` | 原始检索命中结果，去重前；保留 `canonical_id`、`referenced_works`、`retrieval_intent`、`bridge_id` 和可选 `semantic_screen` |
| `papers_dedup` | `literature/papers_dedup.jsonl` | 去重、打分和 enrich 后的保留候选集；关联主键是 `canonical_id`，不是标题 |
| `papers_verified` | `literature/papers_verified.jsonl` | 保留候选集中通过 metadata verification 的可信论文池；T3 队列只从这里取 |
| `papers_backlog` | `literature/papers_backlog.jsonl` | 保留候选集之外的 backlog 候选；默认不自动进入 T3 abstract sweep，用于覆盖审计、人工回捞和排障，不算 T3 必读 |
| `verification_failures` | `literature/verification_failures.jsonl` | verification 失败或元数据不一致的样本 |
| `citation_edges` | `literature/citation_edges.json` | T2 收集到的一跳出引 / related works 边；恢复路径只使用已落盘 metadata，不额外联网 |
| `domain_map` | `literature/domain_map.json` | 引用图领域地图：core / theory_bridge / adjacent / boundary、citation_edges、bucket_assignments 和 audit；不是最终研究缺口 |
| `deep_read_queue` | `literature/deep_read_queue.jsonl` | 给 T3 用的精读队列，不再等于全量候选池 |
| `access_audit` | `literature/access_audit.md` | 资料可用性审计，告诉你哪些论文值不值得继续探测 |
| `search_log` | `literature/search_log.md` | 检索日志、检索式和结果说明 |
| `missing_areas` | `literature/missing_areas.md` | 当前检索覆盖仍然不足的方向；不是最终研究缺口结论 |

### 这些输出文件分别有什么意义

#### `papers_raw.jsonl`

这是最原始的命中结果池。

特点：

- 允许重复
- 不一定字段完整
- 不一定都可信
- 主要用于审计“到底搜到了什么”

它的价值在于：

- 便于回溯检索范围
- 便于分析 dedup 和 verification 后丢掉了哪些样本

#### `papers_dedup.jsonl`

这是候选池，不是精读池。

特点：

- 已去重
- 已 relevance 打分
- 已 enrich
- 但并不意味着每篇都适合送入 T3

#### `papers_verified.jsonl`

这是 T3 更可信的输入池。

`build_verified_papers` 会按“最佳可用标识”回查真实元数据：

- `arxiv:` -> arXiv metadata
- DOI -> CrossRef metadata
- `W...` -> OpenAlex metadata
- 其他 -> Semantic Scholar metadata

校验时会检查：

- 标题相似度
- 年份匹配
- 本地 PDF 是否已存在

并写入：

- `verification_status`
  - `metadata_verified`
  - `pdf_verified`
  - `failed_verification`
- `verification_method`
- `verification_confidence`
- `verification_title_similarity`
- `verification_year_match`

#### `verification_failures.jsonl`

用于记录失败原因，而不是静默丢弃。

这样后续能知道：

- 是 API 请求失败
- 还是 metadata mismatch
- 还是完全找不到 reference metadata

#### `deep_read_queue.jsonl`

这是 T3 最重要的输入之一。

它的目的就是把：

- “搜到的论文”
- “值得精读的论文”

分开。

#### `citation_edges.json` 和 `domain_map.json`

这是 `updataPreT5.md` 要求的 T2 引用图主轴。Scout 在关键词检索之外，会对 seed papers 和少数高信号命中调用 `fetch_outgoing_citations(openalex_id_or_doi=..., max_refs=60)`。这个工具只取 OpenAlex 的 outgoing `referenced_works` 和 `related_works`，不取 `cited_by`，避免入引分页带来的成本和噪声。当前实现还会解析少量一跳 neighbor metadata 到 `data.papers`；由于 `fetch_outgoing_citations` 已加入 T2 自动落盘工具集合，runtime 会把这些一跳候选追加进 `papers_raw.jsonl`，让 snowball/adjacent 候选真正进入 dedup、verification 和 deep-read queue，而不是只停留在 prompt 文本里。

同时，runtime 会在每次成功调用 `fetch_outgoing_citations` 后，把 `source_id -> referenced_works/related_works` 直接追加到 `literature/citation_edges.json`。这一步独立于 neighbor paper metadata 是否解析成功：即使 `max_candidate_papers=0` 或 OpenAlex neighbor 详情拉取失败，引用边仍会保留下来。确定性收尾会再从已落盘记录中的 `referenced_works` / `related_works` 补充边，并调用 `build_domain_map` 生成：

- `core`：只有 `semantic_screen.can_enter_core=true` 的非 seed 论文才能进入；`seed` 只表示用户或上游显式提供的优先阅读信号，不会无条件塞入 core
- `theory_bridge`：`semantic_screen.role=theory_bridge` 的桥接素材，记录 `bridge_id`、`relation_to_project` 和 `why_theory_bridge`
- `adjacent`：已经被 `semantic_screen` 复核、且通过引用/related/snowball 与 core/seed 有连接的邻接材料；缺 `semantic_screen` 的非 seed 不会只凭 degree 或 bucket 进入 adjacent
- `boundary`：未筛选、筛选后不能进入 core/theory_bridge/adjacent，或连接稀疏的候选
- `citation_edges`：当前可用的一跳边
- `bucket_assignments`：每个 `canonical_id` 的 review bucket
- `audit`：`edges_total`、`papers_with_refs`、`papers_refs_unavailable`、`papers_no_openalex_id`、`screened_papers`、`theory_bridge_ids`

`domain_map.json.semantics` 固定为 `domain_map_for_synthesis_and_ideation_not_final_gaps`。它是 T3.5/T4/T8 的结构化脚手架，不是“工具判断出的最终领域结构”或“真实研究缺口”。如果边为空，会在 `warnings` 中记录；这表示引用图信号有限，下游 LLM 必须降低对图谱的依赖，而不是硬编 adjacent transfer。

`domain_map` 的准入边界是严格的：`core` 必须同时满足 `semantic_screen.can_enter_core=true`、`role=core`、且 `relation_to_project` 是机制/方法/评价/baseline-dataset 这类可解释关系；`theory_bridge` 必须满足 `role=theory_bridge` 且 relation 属于同一组可迁移关系；`adjacent` 必须由 `semantic_screen` 明确允许进入 deep read，并且 relation 不是 `shared_keyword_only` / `unrelated`。因此，有 citation edge、来自 `query_bucket=theory_bridge`、或带 `retrieval_intent=cross_domain_bridge` 都不能让未筛选论文进入 core/theory_bridge/adjacent。

引用边使用 `canonical_id` 对齐：OpenAlex `W...` 优先，其次 DOI/arXiv 可回查到的 OpenAlex ID；查不到时用 `noopenalex::<sha1>` 稳定占位并标记 `no_openalex_id=true`。标题可保留为展示字段，但不能作为 citation edge 的关联主键；`build_domain_map` 也不会再用标题字符串解析 citation edge 的两端。OpenAlex 返回的 `referenced_works` / `related_works` 会穿透 raw、dedup、verified 和 recovery；Crossref/seed 论文能反查到 OpenAlex 时也可参与边，反查不到则保留但不伪造边。

#### `access_audit.md`

这是对文献可读性的审计报告。

它会统计：

- 本地 PDF 数
- seed PDF 数
- Reader 最终 `evidence_level` 数量（已有字段或保守占位）
- metadata `access_level_hint` 数量（如 `LIKELY_FULL_TEXT` / `POSSIBLE_FULL_TEXT`）
- Top candidates 列表

#### `missing_areas.md`

这是 T2 对”当前检索仍然没覆盖好什么”给出的覆盖提示。

它是 T3.5、T4 会继续用到的重要输入，不是可有可无的附带产物。
但它不是人工精读后的“真实研究空白”结论，只能作为补检和复核线索。

文件包含：
- 覆盖概况（论文数、年份分布、来源分布）
- 覆盖较好的主题 / 覆盖不足的主题
- Retrieval Coverage Hints
- **Retrieval Coverage Hints（不是研究缺口结论）**（结构化）：每个提示用 `### 提示 N` 标记，包含 4 个字段：
  - **覆盖缺口**：具体缺什么
  - **为什么需要复核**：为什么不能直接当研究结论
  - **建议动作**：T2/T3/T4 可以如何补检或复核
  - **难度**：Low / Medium / High
- 建议

结构化覆盖提示可以触发 T4 的【缺口探索型】补充候选，但不能直接作为“领域存在空白”的证据。T4 必须结合 `synthesis.md`、paper notes 和 LLM 自身判断确认。

### T2 的检索逻辑到底怎么做

#### Step 1：读取项目配置和 seed 信息

T2 首先会读取：

- `project.yaml`
- `seed_papers.jsonl`
- `seed_outline_profile.json`
- `seed_constraints.md`
- `seed_ideas.md`
- `seed_external_resources.jsonl`

并把这些信息转成：

- topic
- keywords
- query anchors
- constraint hints

`seed_outline_profile.json` 来自用户 Markdown 种子提纲。它的 `framework`、`sections`、
`query_profile` 和 `representative_literature_directions` 只作为 query/taxonomy prior；
`representative_literature_directions` 不是已验证 citation，不能写入 `seed_papers.jsonl`，
也不能在 T3.6/T8 中直接引用。

#### Step 2：query 扩展

当前确定性 query 扩展函数是：

- [researchos/tools/paper_utils.py](../researchos/tools/paper_utils.py) 里的 `expand_queries`

它的核心逻辑是：

1. 把 `topic` 本身作为第一条 query
2. 从前 3 篇 seed paper 标题里提取关键短语
3. 合并 LLM 提供的 `domain_profile` / `llm_queries` / `domain_hints`，包括 include/exclude concepts、query variants、相关子领域和歧义限定
4. 加时间限定词：
   - `topic {当年-2}-{当年}`
   - `topic {当年-3}-{当年-1}`
5. 去重后最多保留 `max_queries=10`

注意：`expand_queries` 不再内置“memory/retrieval/agent 属于 AI/CS”这类学科判断。歧义消解由 Scout LLM 在 `domain_profile` 中写明。

Prompt 层还要求：

- 实际设计 `6-10` 条多样化 query
- 不能只是换个词重复表达同一件事

#### Step 3：检测 query 是否过度重复

工具：

- `detect_duplicate_queries`

它会计算 query 两两相似度，并给出：

- `duplicate_pairs`
- `avg_similarity`
- `is_high_duplicate`

当前规则：

- 平均相似度 > 60% 时，说明 query 设计太重复，需要重做

#### Step 4：多源搜索

当前 prompt 推荐的搜索工具策略是：

优先用直接 API 工具：

- `openalex_search`
- `crossref_search`
- `arxiv_search`
- `semantic_scholar_search`
- `elsevier_scopus_search`
- `informs_search`

备选：

- `multi_source_search`

`multi_source_search` 的默认 sources 也包含 `informs`，因此即便 Scout 走聚合工具 fallback，也会默认尝试 INFORMS/Crossref prefix 检索。

当前 prompt 中的推荐规模是：

- 使用 `6-10` 条检索式
- 每条检索式在每个数据源上抓取 `10-20` 篇
- 总 raw 结果控制在 `100-200` 篇
- 至少要搜到 `20` 篇，否则说明 coverage 太差，需要扩展 query

#### Step 4.5：引用图一跳滚雪球

关键词检索得到初步候选后，Scout 要对 seed papers 和 top 约 10 篇高信号命中调用：

```text
fetch_outgoing_citations(openalex_id_or_doi=<OpenAlex ID 或 DOI>, max_refs=60)
```

返回内容包括：

- `source_id`
- `referenced_works`
- `related_works`
- `papers`：少量解析出的 neighbor metadata，用于自动追加到 `papers_raw.jsonl`
- `query_bucket=snowball`

这一步的目标是边界探索和真实引用关系补充，不是把核心主题堆得更密。`referenced_works` 表示该论文站在哪些工作上，`related_works` 表示 OpenAlex 算法近邻；二者都便宜且边界清楚。系统明确不抓 `cited_by`，因为入引可能成千上万且需要分页，容易烧资源和引入噪声。

滚雪球命中的 candidate 会携带 `search_bucket=snowball` 或 `source_bucket=adjacent/snowball`，后续 `enrich_papers`、`build_deep_read_queue` 和 `build_domain_map` 会保留这些显式标签。标签来自 LLM/tool metadata，不由工具按关键词硬判学科。

### 实际执行过程

`ScoutAgent` 运行时先读 `project.yaml` 获取 `research_direction`、`keywords`、`target_venue` 和约束；随后必须调用 `inspect_user_seeds(path="user_seeds")`。这个工具只把 `kind=user_material` 和 `kind=pdf` 计为真实用户 seed，`README.md`、`_DIR_GUIDE.md`、`*.example`、空文件、只有“暂无”的 `seed_ideas.md` / `seed_constraints.md` 都只是初始化占位，不能说成“发现实际文件”。普通 `list_files` 只用于列目录，不能替代 seed inspection。之后才读取 `user_seeds/seed_papers.jsonl`、`seed_ideas.md`、`seed_constraints.md`、`seed_external_resources.jsonl`；如果存在 `seeds/T2_scout/papers/*.pdf` 或旧路径 `user_seeds/pdfs/*.pdf`，也会用 `scan_seed_papers` 抽取本地 PDF seed metadata，并与 JSONL seed 按 DOI/arXiv/title/id 去重合并。

如果 inspection 发现真实 Markdown seed outline，而 `user_seeds/seed_outline_profile.json`
尚不存在，Scout 会调用 `normalize_seed_outline`，runtime 也会在 prompt 渲染前做一次兜底规范化。
综述 profile 下，query 设计必须覆盖中英文检索、管理/IS/OR、human-AI decision-making、
AI governance/model risk management、XAI/fairness/accountability 和中文“智能算法风险/
管理决策/算法治理”等角度。当前没有 CNKI/万方官方 API；中文文献覆盖不足时应写入
`search_log.md`/`missing_areas.md`，并要求用户提供中文 PDF、DOI 或题录作为 seed。
EU AI Act、NIST AI RMF、ISO/IEC 标准和中国算法治理法规属于 external resources /
official-source verification 线索，不是 scholarly paper，不应进入 `papers_dedup.jsonl`。

随后 prompt 会注入 `literature-scout` guidance，要求 LLM 先归纳 `domain_profile`：目标领域、include/exclude concepts、歧义词、相关子领域、候选 venue/category 和多角度 query。`expand_queries` 只负责把 LLM 设计的 query、seed 标题短语和时间窗口合并去重，不再内置“memory/retrieval/agent 属于 AI/CS”这类学科判断；如果需要领域过滤，`filter_by_domain` 也必须接收 LLM 提供的 `domain_profile`，否则不做过滤。之后用 `detect_duplicate_queries` 检查 query 是否只是同义重复。

真正抓论文时默认调用 `openalex_search`、`crossref_search`、`arxiv_search`、`semantic_scholar_search`、`elsevier_scopus_search` 和 `informs_search`。其中 `informs_search` 是通过 Crossref DOI prefix `10.1287` 检索 INFORMS 论文元数据，适合 OR/MS、management science、supply chain、queueing、optimization 等方向，也可以作为低成本补充源默认启用；如果某个主题不在 INFORMS 强覆盖范围内，它通常只会返回 0 篇或少量噪声，T2 记录后继续。`domain_profile` 的作用是解释和过滤结果，不是决定是否完全跳过 INFORMS。

每次工具返回的 `data.papers` 会被 `AgentRunner` 自动追加到 `literature/papers_raw.jsonl`，不需要模型手工复制 JSON。自动落盘工具包括常规检索工具和 `fetch_outgoing_citations`；因此引用图滚雪球解析出的 neighbor papers 也会进入 raw 池。Scout 可以在搜索工具调用中附带 `query_bucket`，例如 `core`、`baseline`、`evaluation`、`adjacent_field`、`theory_bridge`，也可以按 `bridge_domain_plan` 传入 `bridge_id`。runtime 只保存这些显式召回标签，并据此写 `retrieval_intent=primary|cross_domain_bridge`；它不根据关键词猜学科，也不把 `retrieval_intent` 当成 core/theory_bridge/target 准入。

空 query 是硬错误，不是普通 0 结果。旧运行里出现的 `检索 '' -> 0 篇 (来源: )`，根因不是 OpenAlex/Crossref/arXiv 真的执行了空检索，而是 Scout 把一段“切换到更有针对性的检索”的普通状态说明误写成 `log_scout_progress(action="search_result", detail="...")`；旧版进度工具没有强制 `query/source/count`，于是把缺失参数默认成 `query=""、source=""、count=0` 写入 `scout_progress.md`。现在分三层处理：

1. `expand_queries` 如果无法从 `project.yaml`、真实 seed 标题、`llm_queries` 或 `domain_profile` 生成任何非空检索式，会返回 `error=empty_query_plan`，要求 Scout 重新设计 query 或调用 `ask_human` 补充研究边界。
2. `detect_duplicate_queries` 会清洗列表；如果列表全空，也返回 `error=empty_query_plan`，避免空列表通过检索前检查。
3. `multi_source_search`、`search_papers`、`openalex_search`、`crossref_search`、`arxiv_search`、`semantic_scholar_search`、`elsevier_scopus_search` 和 `informs_search` 都会在工具边界清洗 query；清洗后为空时返回 `error=empty_query`。`log_scout_progress(action="search_result")` 也必须显式提供非空 `query`、非空 `source` 和 `count`，否则返回 `invalid_progress_event`，不会再记录 `检索 '' -> 0 篇`。

raw 覆盖足够后，Scout 必须先执行一次机械摘要回填，再执行 `semantic_screen` 三步式：

1. **回填**：调用 `backfill_paper_abstracts(papers_path="literature/papers_raw.jsonl")`。这个工具只清洗已有摘要、重建 OpenAlex `abstract_inverted_index`、去掉 Crossref/JATS 标签，并按 Semantic Scholar batch、arXiv、OpenAlex、Crossref、Semantic Scholar、Europe PMC、标题匹配等机械来源补 `abstract` 和 `_abstract_backfilled_from`。它不判断论文相关性、证据强度或是否应进入 deep-read。
2. **判断**：Scout LLM 分批读取回填后的候选标题、摘要、source_query、citation context，对可能进入 core、theory_bridge 或 deep-read target 的非 seed 论文输出结构化 `semantic_screen`。字段包括 `relation_to_project`、`role`、`confidence`、`bridge_id`、`can_enter_core`、`can_enter_deep_read`、`rationale`、`evidence_fields_used`。
3. **合并**：调用 `apply_semantic_screening(papers_path="literature/papers_raw.jsonl", screenings=[...])`。该工具只按 `paper_id/id/canonical_id/doi/title` 匹配并把 LLM 判定合并回论文池；不在工具内部调用 LLM，不重判是否 core/bridge。
4. **读取/处置**：finish 后的 deterministic 收尾会把 `semantic_screen` 作为唯一语义判定来源。runtime 先把全量 raw 去重为候选池，再按 `agents.scout.behavior.t2_finalize.active_pool_max` 分成 `papers_dedup.jsonl`/`papers_verified.jsonl` 保留候选集和 `papers_backlog.jsonl` backlog。`build_domain_map` 只允许保留候选集中 LLM-screened 论文进入 `core/theory_bridge/adjacent`，缺 screen 的非 seed 论文只能进入 boundary/backlog；`build_deep_read_queue` 只对已核验保留候选做 100% deep/shallow 处置，不能只凭分数、bucket 或 degree 宣称它属于 core/target。

raw 数量只是完成 T2 的必要条件，不是充分条件；Scout 必须先判断 query/source/bucket 覆盖是否足够，完成摘要回填和必要 semantic screening，随后调用 `finish_task`。runtime 才会确定性调用去重、保留候选/backlog 分层、metadata priority hint、enrich、metadata verification、引用边/domain map、access audit 和 deep-read queue 构建逻辑，依次产出 `papers_dedup.jsonl`、`papers_verified.jsonl`、`papers_backlog.jsonl`、`verification_failures.jsonl`、`citation_edges.json`、`domain_map.json`、`deep_read_queue.jsonl`、`access_audit.md`、`search_log.md` 和 `missing_areas.md`。最后 `ScoutAgent.validate_outputs()` 再检查数量、schema、`dedup <= raw`、queue 是否来自已核验保留候选、已核验保留候选是否 100% 进入 deep/shallow 阅读处置链、seed paper 是否进入队列，以及若已核验保留候选里已有 `semantic_screen` 允许的跨域/理论桥接候选，queue 至少保留一个并放入非 `triaged_out` 的 `seed/mainline_deep/bridge_deep` 阅读区，避免该类素材只留在 screened backlog 后被 T3 跳过。

### T2 怎样保存 raw 结果

当前默认是 runtime 自动保存，不要求 LLM 手动 `append_papers_raw`。

实际行为：

1. T2 调用任一搜索工具并返回 `data.papers`
2. `AgentRunner` 自动把这些结果追加进 `literature/papers_raw.jsonl`
3. Scout 判断 raw 数量、query 角度和 source 覆盖足够后调用 `backfill_paper_abstracts`
4. Scout LLM 基于补全后的标题/摘要/source/citation context 输出并合并 `semantic_screen`
5. Scout 调用 `finish_task`
6. runtime 在 `finish_task` 后从 raw 生成保留候选 `papers_dedup`、已核验保留候选 `papers_verified`、`papers_backlog`、`citation_edges`、`domain_map`、`deep_read_queue`、`access_audit`、`search_log`、`missing_areas`

真实 resume 或人工显式 recovery 也可以从已有 raw 补齐下游文件；普通冷启动、普通失败重试和 `retry_after_failure` 不会仅凭 raw_count 自动完成 T2。

`append_papers_raw` / `process_papers_raw` 仍保留为兼容和补救工具，但正常流程不应该在每次搜索后手动重复追加，否则容易造成 raw 双写和恢复噪声。

### T2 怎样去重

确定性去重函数：

- `deduplicate_papers`

规则：

1. DOI 精确去重
2. 标题相似度去重
   - 当前实现阈值默认 `0.95`
   - 比较前会做 loose title normalization，合并大小写、标点、Unicode dash、HTML 等差异；例如同一题名来自 Crossref/OpenAlex/arXiv 时应合并 provenance，而不是重复进入后续阅读。

### T2 怎样控制候选池规模

`papers_raw.jsonl` 是全量检索审计池，可以超过保留候选数上限；`papers_dedup.jsonl` 不是全量 raw 的简单去重结果，而是本轮保留候选集。默认保留候选数上限是 `config/agent_params.yaml -> agents.scout.behavior.t2_finalize.active_pool_max = 120`，超出保留候选集的候选写入 `literature/papers_backlog.jsonl`，带 `t2_pool_role=backlog`、`triaged_out=true` 和 `triaged_reason=t2_active_pool_cap_exceeded`，用于覆盖审计、人工回捞或排障，不会被静默丢弃，也不会被普通 T3 abstract sweep 自动读回。

T2 deterministic finalize 中影响候选规模和 API 消耗的机械阈值都在 `agents.scout.behavior.t2_finalize`：包括 `dedup_title_threshold`、`metadata_backfill_max_concurrency`、`abstract_backfill_title_match_threshold`、`snowball_max_sources`、`snowball_refs_per_source`、`snowball_max_candidates`、`snowball_title_match_threshold` 和 `access_audit_top_n`。这些字段会写进 `search_log.md` 的配置来源说明，方便排障时确认本轮用的是哪组参数。

保留候选集默认选择顺序：

- 用户 seed
- `semantic_screen.can_enter_deep_read=true` 的高置信候选
- confirmed bridge 的召回候选会按人工确认的 priority 分配保留名额：`must_explore` 默认每个 bridge 最多 `must_bridge_active_pool_cap_per_bridge` 篇，`should_explore` 默认最多 `should_bridge_active_pool_cap_per_bridge` 篇；`no_cross` / `skip` / `source=none` 的 bridge 只保留为决策记录，不强制进入 T3/T4
- citation snowball 候选，默认最多 `snowball_active_pool_cap` 篇
- 其余按 `metadata/search priority hint` 补足到 `active_pool_max`

Bridge cap 是候选级硬边界：同一 confirmed bridge 的候选即使同时命中 `semantic_screen.can_enter_deep_read=true`、citation snowball 或 metadata priority fill，也不能绕过 `must/should_bridge_active_pool_cap_per_bridge`。`no_cross/skip` bridge 的召回记录只留在 raw/backlog 审计链，不会靠后续 fill 自动进入保留候选集。若 `project.yaml` 启用了 `domain_profile`，被 profile 排除的候选也会写入 `papers_backlog.jsonl` 并标记 `triaged_reason=domain_profile_filtered`，而不是从 raw 到保留候选/backlog 之间静默消失。

`literature/temp/scout_progress.md` 会由 runtime 在搜索工具自动落盘 raw、deterministic finalize 开始、保留候选/backlog 切分、完成/失败时自动追加进度；它不再只依赖 Scout LLM 主动调用 `log_scout_progress`。`search_log.md` 会写 `T2 保留候选集: input=..., retained=..., backlog=..., selection_reasons=...`。如果看到 raw 很大但 `papers_dedup=active_pool_max`，这是正常分层；如果 `papers_dedup > active_pool_max`，才是 finalize/validator 错误。爆量排障顺序是先看 `## Bucket 覆盖` 和 `## Source/Tool 覆盖`：若 core/theory_bridge/adjacent 的 Query Calls 很高，通常是 query 或 bridge 扩展重复；若 `OpenAlex/Crossref citation snowball` 的 `raw_persisted` 很高，才说明 citation 扩张过多。

### T2 怎样生成 metadata priority hint

确定性打分函数：

- `score_papers`

历史字段名仍叫 `relevance_score`，但当前语义已经收窄为 `metadata/search priority hint`，不是最终学术相关性。它用于排序和裁剪过大候选池，不应用作“论文是否相关”的硬结论。

默认权重（6 维）：

| 维度 | 权重 | 含义 |
| --- | --- | --- |
| `source_type` | `0.15` | 来源类型 hint；未知 source_type 不会被伪装成顶会 |
| `year` | `0.25` | 年份新鲜度 |
| `citation` | `0.10` | 引用数 |
| `keyword` | `0.40` | 项目关键词匹配度 |
| `methodological_signal` | `0.00` | 方法论信号只输出 hint，默认不参与排序 |
| `venue_diversity_bonus` | `0.10` | 场地多样性奖励（动态调整） |

各维度细节：

- `source_type`
  - 使用已有 metadata 或 LLM annotation；未知时保留 `unknown` 并标记 `_needs_llm_source_type`
  - `enrich_papers` 不再凭固定 AI 顶会列表把 venue 判成 `top_conference`
- `year`
  - 运行时 UTC 当年最高
  - 越旧越递减
  - 如果上游没有可靠年份，保持 `null` / unknown
  - 不再把未知年份伪装成固定年份
- `citation_count`
  - `>=100 -> 1.0`
  - `>=50 -> 0.8`
  - `>=10 -> 0.6`
  - `<10 -> 0.4`
- `keyword`
  - 标题和摘要命中项目关键词的比例
- `methodological_signal`
  - 基于标题和摘要中的方法论关键词（rethinking、limitation、ablation、without 等）
  - 0 个命中 → `0.0`，1 个命中 → `0.5`，2+ 个命中 → `1.0`
  - 这是通用文本 hint，默认不参与 ranking，避免把 T4 四类补充方向提前写进 T2 候选池
- `venue_diversity_bonus`
  - 默认 `0.5`，在 `build_deep_read_queue` 中动态调整
  - 同一 venue 过多时，后续同 venue 论文的 bonus 递减（`max(0.0, 1.0 - same_venue * 0.3)`）
  - 目的是避免精读队列被单一 venue 垄断

最终得到 `relevance_score`、`priority_score_hint`、`relevance_score_semantics=metadata_priority_hint_requires_llm_review`、`relevance_score_components`。T2 恢复路径不会再用 `relevance_score >= 0.5` 硬过滤论文；如果候选池超过 `active_pool_max`，会按保留候选/backlog 分层保留本轮候选，其余写入 `papers_backlog.jsonl`。

### T2 怎样 enrich

工具：

- `enrich_papers`

它会补全：

- `authors`
- `source_type`（优先使用 LLM annotation；未知时写 `unknown` 并标记需复核）
- `why_relevant`（优先使用 LLM annotation；缺少证据时只写保守说明）
- `_missing_abstract`
- `access_score_estimate`
- `access_score`
- `evidence_level`
- `url`
- `venue`
- `citation_count`

注意：`enrich_papers` 不再把未知 venue 默认判成 preprint，也不再用固定 AI/LLM 关键词编写相关性理由。它可以接收 `llm_annotations`，由 Scout LLM 传入 `source_type`、`why_relevant`、`method_family`、`domain_tags` 等字段；工具只负责应用标注、补齐 schema 和标记 `_needs_llm_*` 复核项。

其中可读性相关字段的意义是：

- `access_score_estimate`
  基于 metadata 估计可读性
- `access_level_hint`
  基于 arXiv、PDF URL、DOI、abstract、本地 PDF 等可验证 metadata 生成的可读性 hint，例如 `LIKELY_FULL_TEXT` / `POSSIBLE_FULL_TEXT`
- `evidence_level`
  Reader 最终阅读状态。T2 阶段不再根据 access score 推断 `FULL_TEXT` / `PARTIAL_TEXT`；没有 Reader 覆盖记录时只保守写 `ABSTRACT_ONLY` 或 `METADATA_ONLY`，并标记 `_needs_reader_evidence_level`

### T2 怎样做 metadata verification

工具：

- `build_verified_papers`

逻辑：

1. 优先使用最强标识做回查
2. 拿到 reference metadata
3. 比较标题相似度
4. 检查年份是否匹配
5. 若通过则写入 verified
6. 对已通过 verification 的记录做机械摘要回填：优先保留原始检索结果已有 `abstract`；若缺失，则从 reference metadata 回填；DOI/Crossref 路径如果 Crossref 没有摘要，会继续用同一 DOI 查询 OpenAlex / Semantic Scholar，只补 `abstract` 和 `_abstract_backfilled_from`，不改变论文相关性判断
7. 否则写入 failure

这里的关键不是“让 LLM 自己相信这论文存在”，而是**尽量用真实 API 做回查**。

### T2 怎样构建 deep-read queue

工具：

- `build_deep_read_queue`

当前核心参数优先来自 workspace-local `literature/literature_params.json`，该文件由 `T2-PARAM-GATE` 写入；没有该文件时才回退到 `config/agent_params.yaml -> agents.reader.modes.read.behavior`。Gate 面向用户使用三层语义：保留候选数、精读篇数、摘要轻读篇数。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `deep_read_min` | `35` | 最低精读线；预算/资源异常时的最低可接受结构化 deep-read note 数 |
| `deep_read_target` | `35` | 精读目标；`require_deep_read_target=true` 时 T3 必须读满该目标才进入 T3.5 |
| `deep_read_max` | `45` | 精读目标硬上限，保护位也在其中计数 |
| `probe_pool` | `45` | T3 优先 probe 的候选池大小 |
| `mainline_screened_cap` | `90` | 主线 shallow/screened backlog 保留上限 |
| `bridge_deep_floor` | `3` | 每个 must_explore bridge 通过 screen 后的精读保底 |
| `bridge_screened_cap` | `7` | 每个 bridge 的 shallow/screened backlog 保留上限 |
| `bridge_pool_cap` | `15` | 每个 bridge 在 queue 中默认保留的候选总上限；超额不删除，标为 deferred 并保留覆盖账本 |
| `citation_hub_slots` | `3` | citation graph 枢纽保护槽 |

`active_pool_max` 在 gate 中称为“保留候选数”：T2 从检索结果里保留多少篇进入后续阅读处置。它不是精读篇数，也不是最终引用篇数；超出部分仍写入 `papers_backlog.jsonl`，可追溯和回捞。`deep_read_target` 是正常精读目标，`deep_read_max` 是保护位和高优先级 seed/bridge/citation hub 合并后的精读上限；保护项会占用精读名额，而不是无限额外追加。`deep_read_queue.jsonl` 同时是保留候选的阅读处置账本：deep-read 记录由 T3 精读，shallow 记录由 abstract sweep 生成 abstract-only 轻量笔记；超过 `bridge_pool_cap` 的 bridge 记录标为 `read_disposition=deferred` 与 `triaged_reason=bridge_pool_cap_exceeded`，默认不进入轻读证据，但保留覆盖账本和人工回捞路径。缺摘要的 metadata-only 候选进入 `metadata_triage.md` 批量报告；它们不算 abstract note 证据，并应尽量由 backlog 中有摘要/PDF 的候选补足可读覆盖。无 `deep_read_queue` 的旧 workspace fallback 仍使用 `expected_notes_ratio=1.0`，默认要求输入池 100% 覆盖。

当前排序和处置的核心思想是：

- seed papers 最高优先级
- `semantic_screen.can_enter_deep_read=true` 的非 seed 优先进入精读目标
- 未 screen 的主线 verified 论文会标为 `metadata_fallback_candidate`，可填充精读预算并由 Reader 复核
- 未 screen 的 bridge/cross-domain 命中会标为 `unscreened_bridge_backlog_candidate`，进入 `bridge_screened` shallow/backlog，不得仅凭 bridge_id 作为精读 bridge 证据
- 显式 `shared_keyword_only/unrelated` 或 `can_enter_deep_read=false` 的论文会保留为 shallow/read-disposition 线索，不进入 `domain_map.core/theory_bridge/adjacent`
- 再看 `relevance_score` / `priority_score_hint`
- 再看 `access_score` / 本地 PDF 可读性
- 再看 `verification_confidence`

排序中的 `read_priority` 大致是：

- seed priority 大额加权
- priority hint `0.50`
- access `0.20`
- verification confidence `0.15`
- verification status bonus

`methodological_signal` 只作为 `methodological_signal_hint` 写入，不再默认参与 queue 排序。`read_priority` 也只是队列优先级，不是最终论文重要性。

为支持 CDR 的跨域类比和理论桥接，`build_deep_read_queue` 会给通过 `semantic_screen` 的跨域/theory 材料保留整体名额。默认 `cross_domain_slots=4`，也可以通过工具参数覆盖；它不按每个 bridge domain 平均分配，避免因为用户给了多个桥接方向而把队列塞满低质量候选。

semantic-screened protected slot 的条件是三者同时满足：

- `semantic_screen.can_enter_deep_read=true`
- `semantic_screen.relation_to_project` 属于 `mechanism_bridge`、`method_transfer`、`evaluation_or_metric_bridge`、`baseline_or_dataset_relevance`
- `semantic_screen.role=theory_bridge` 或 `retrieval_intent=cross_domain_bridge`

因此 `search_bucket=adjacent_field/theory_bridge`、`source_bucket=adjacent/snowball` 和 `retrieval_intent=cross_domain_bridge` 都只是召回意图和 provenance；它们不能绕过 `semantic_screen`。`shared_keyword_only` / `unrelated`、缺 screen 的 bridge 命中、或只共享宽泛词汇但无法说明下游用途的论文不会进入 bridge 精读目标，但仍会留在 shallow/backlog 处置链，避免 verified 论文静默消失。

semantic-screened protected slot 会在 seed 后优先占用精读名额，再填中心论文；它不会只靠加权排序被高分 core 论文挤到 screened backlog。`deep_read_queue` metadata 会记录 `active_target_limit`、`protected_slot_target`、`cross_domain_slots`、`protected_slot_in_queue`、`protected_slot_in_target`、`screened_deep_read_candidates`、`verified_disposition_count`、`verified_disposition_coverage`、`metadata_fallback_in_queue` 和 `shallow_read_backlog_count`；旧的 `protected_bucket_*` 字段只作为兼容 alias 保留，不再表达准入依据。`ScoutAgent.validate_outputs()` 会检查队列来自已核验保留候选、已核验保留候选 100% 有 deep/shallow 去向，以及 `semantic_screen` 允许的跨域/桥接候选是否被保留；不会再把 bucket 标签本身当作保护依据。`ReaderAgent.validate_outputs()` 还会检查这些非 `triaged_out` 的 protected queue 论文是否真正完成 note。这个校验防止系统表面生成了 `domain_map`，但精读队列仍全部被中心论文占满。它仍然不是质量门：进入队列只表示应被 Reader 复核，不表示论文一定重要。

对 confirmed/must_explore bridge，`deep_read_queue_meta.json` 还会写 `must_explore_bridge_diagnostics` 和 `must_explore_bridge_warnings`。这些字段按 bridge 统计 `recalled_or_contributed`、`bridge_deep_active`、`bridge_screened_backlog`、`missing_semantic_screen`、`semantic_screen_excluded`、`has_abstract` 和 `has_pdf_url_hint`。如果某个 must_explore 已召回但没有 active bridge deep-read，系统会把原因显式暴露出来，而不是把未 screen 论文硬塞进深读或静默丢弃。这里仍遵守 tool/prompt 边界：tool 做记账和告警，是否补 screen、补读或放弃由 Scout/Reader LLM 和用户决定。

引用图也会转化为真实 T3 证据，而不是只停留在 `domain_map`。`identify_citation_hubs` 会基于 pool 内部的 `citation_edges`、record 自带 `referenced_works/related_works` 和 canonical id 计算结构节点，标出：

- `seed_neighbor`：与 seed 论文直接相连，最高优先级
- `bridge_node`：连接两个以上结构簇或角色桶
- `high_inbound`：在候选池内部被多篇论文引用

该工具不调 LLM，也不判断论文是否学术相关。hub 的准入语义是“小额保护 + Reader 复核”：seed hub 直接进入；已有 `semantic_screen.can_enter_deep_read=true` 且 `relation_to_project` 不是 `shared_keyword_only/unrelated` 的 hub 可以进入；缺少 semantic screen 的结构 hub 也可以占用最多 `citation_hub_slots` 个保护槽，并写入 `citation_hub_needs_reader_screening=true`，由 T3 Reader 决定是否真正有机制/方法/评价价值。已有 LLM screen 明确排除的 hub 不会被强制 deep-read。`citation_hub_slots` 是保底保护槽，不是所有 hub 的硬上限；少数 hub 仍可能因为普通 mainline ranking 自然进入精读目标，但不会绕过 Reader 审查。进入保护配额的记录会写入 `is_citation_hub`、`hub_type`、`hub_score`、`citation_hub_protected_slot=true`。排序语义是：`seed > seed_neighbor hub > citation protected hub > semantic-screened must_explore bridge > should_explore bridge > primary high priority > screened backlog`。`ReaderAgent.validate_outputs()` 会把这些保留候选集内 citation hub 当作 protected record 检查，防止引用图识别出的关键节点从未被 T3 阅读。

T2 deterministic finalize 还会把 citation graph 转成可读证据链，而不是只生成图：

- `multi_source_search` 默认启用 `openalex`、`crossref`、`arxiv`、`informs`、`europepmc`。OpenAlex 命中会保留 OpenAlex ID、`referenced_works`、`related_works`、`best_oa_location`、`primary_location`、`locations`、`open_access` 和派生 `pdf_url/open_access_pdf_url`。
- `papers_raw.jsonl` 追加去重时不再静默跳过重复记录，而是合并 `source_queries`、`search_buckets`、`recalled_by_bridges`、references、OA/PDF hints 和 citation snowball 来源。
- finalize 会先对 raw 去重并选出保留候选集，再对保留候选集做 OpenAlex 标题兜底、OpenAlex DOI/OA 详情补全、Crossref DOI 详情补全和多源摘要回填，只补 OpenAlex ID、abstract、references/related works、OA/PDF locations，不做相关性判断。OpenAlex/Crossref citation snowball 是一次性 bounded one-hop 补充：只从 seed 或 `semantic_screen.can_enter_deep_read=true` 的高置信来源扩展；如果 raw 中已存在对应 snowball 记录，resume/finalize 不会继续滚动扩展。日志会分别写 `skipped_existing_snowball_records`、`skipped_by_refs_per_source_cap`、`skipped_by_max_candidates_cap`、`raw_persisted/raw_merged`。
- `citation_edges.json` 会同时包含 active record 自带引用边和 OpenAlex/Crossref snowball 的 `source paper -> snowball candidate` 边。这样 `domain_map`、`identify_citation_hubs`、`deep_read_queue` 和 T3/T3.5/T4 都能看到 citation 补充，而不是只在 raw 里堆元数据。
- `search_log.md` 的 `## 检索式` 表会显示 Query、Bucket、Bridge、Tool/Source、Calls、Results、Persisted；重复 query 会按 normalized query + bucket + bridge + tool 合并，并用 Calls 展示重复调用次数。`## Bridge Domain Query 覆盖` 和 `## Bridge Domain Plan 覆盖` 会明确列出 b1/b2/... 的实际召回和是否 missing/covered。即使 trace 缺失，finalize 也会从 `papers_raw.jsonl` 的 provenance 重建这些表，但不会把错位的 `recalled_by_bridges` 硬配给 core query。`deep_read_queue.jsonl` 本身仍是薄记录，不塞长摘要，但会保留短 provenance：`source_query/source_queries`、`source_tool/source_tools`、`search_buckets`、`openalex_id`、citation snowball 来源和 PDF/reference/abstract hint 计数；Reader 需要正文摘要时用 `lookup_paper_record(queue_rank=...)` 合并 verified/raw metadata。

#### Bridge domain 主链与截断语义

Bridge domain 是一条独立于主线文献池的补强链路，不等于“把所有跨域材料都强行写进论文”。当前实现按以下契约运行：

1. **T1 来源契约**：`literature/bridge_domain_plan.json` 是唯一正式清单。`source=none + bridge_domains=[]` 表示用户选择不交叉，T2/T3/T4 不强制 bridge；非空清单表示用户已确认这些 bridge，条目内 `source=user|auto` 只记录候选来源，不决定 confirmed 身份。
2. **T2 专属召回**：Scout 读取正式清单后，为 `must_explore` 设计至少 3 条带 `bridge_id` 的专属 query，为 `should_explore` 至少设计 1 条 query。检索结果保留 `bridge_id` / `recalled_by_bridges`，去重时同一论文可同时保留多个 bridge 来源。
3. **两道召回门**：Scout validator 分开检查 recall 层和 screen 层。`must_explore` 若 raw 层完全无命中，会在 T2 直接报告召回层断裂；若有命中但没有任何 `semantic_screen.can_enter_deep_read=true` 的候选，会报告 screen 层断裂。`should_explore` 不足只作为覆盖提示，不强行阻断。
4. **身份冲突归一**：如果论文通过 core screen（`can_enter_core=true`、`role=core`、关系属于可解释机制/方法/评价/baseline-dataset），它归入 `paper_notes/` 主线，`bridge_id` 被剥离；原 bridge 来源写入 `contributed_bridges`，供 T3.5/T4 仍能追溯迁移线索。未通过 core screen 但通过 bridge screen 的跨域论文进入 bridge 桶。
5. **配额和桶**：新队列桶是 `seed`、`mainline_deep`、`bridge_deep`、`mainline_screened`、`bridge_screened`。精读目标、硬上限、主线筛读 backlog、bridge pool、screened backlog 和 must_explore bridge 保底都由 `agents.reader.modes.read.behavior` 配置；默认值见上表。候选仍必须先通过 LLM semantic screen，不能只靠 bridge_id 强制阅读。
6. **`triaged_out` 语义**：`triaged_out=true` 的记录是 coverage/resume backlog，不是 T3 必读。它保留 `queue_rank`、`target_bucket` 和 `triaged_reason`，用于解释为什么候选被截断，并允许后续补检或恢复时回捞；Reader、notes manifest 和 validators 只把非 `triaged_out` 且非 legacy `overflow` 的记录计入完成目标。
7. **T3 bridge 笔记路径**：`save_paper_note` 会把未通过 core screen 的 `bridge_deep` 论文写入 `literature/paper_notes_bridge/{bridge_id}/{id}.md`；通过 core screen 的论文仍写入 `literature/paper_notes/{id}.md`，避免同一论文在主线和 bridge 目录重复阅读。T3 manifest、T3 recovery、T3.5 workbench、T8 resource index 都会同时扫描 `paper_notes/` 与 `paper_notes_bridge/`，并统一过滤 `_DIR_GUIDE.md`、README、template、example、空占位文件。
8. **T4 上桌和逃生舱**：有 confirmed bridge 且 `source!=none` 时，T4 必须检查 `paper_notes_bridge/{bridge_id}/`、主线 note 的 `contributed_bridges`、`synthesis_workbench.bridge_transfer_drafts`。有足够证据时生成 `idea_origin=bridge_synthesis`、`constraint_status=bridge`、`cross_domain_sources=[...]` 的候选，并放入 Gate1 可见池；没有足够证据时不能硬编 idea，必须在 `ideation/bridge_coverage_review.json` 写 `escape_hatch.status=no_candidate_available`、证据缺口和回访条件。
9. **条件输出语义**：`bridge_coverage_review.json` 是 T4 的 `optional_outputs` 条件产物，不是普通无条件输出。runtime 的基础 artifact 校验不会在 `source=none` 或无 confirmed bridge 时强迫它存在；`IdeationAgent.validate_outputs()` 和 T4 runtime checker 会在有 confirmed bridge 时把它升级为必需审计文件。
10. **Gate1 语义**：上桌不等于进入 `hypotheses.md`。`bridge_synthesis` 候选和被 Pass 2 不推荐的候选都必须在 `_candidate_directions.json` 与 `_gate1_selection_brief.md` 可见；用户可以选择、合并、重构、延后或拒绝。只有 Gate1/Gate2 后被用户选中的方向才进入 hypothesis/experiment plan。

### T2 怎样做 access audit

工具：

- `build_access_audit`

它会给出：

- 本地 PDF 数
- seed PDF 数
- `FULL_TEXT_LOCAL` access hint 数
- evidence level 分布（Reader 状态或保守占位）
- access level hint 分布（metadata 可读性提示）
- 每篇论文推荐动作：
  - `verify_metadata`
  - `exclude_from_t3`
  - `read_local_pdf`
  - `read_seed_pdf`
  - `probe_pdf`
  - `abstract_only`
  - `metadata_backlog`

`user_seeds/pdfs/` 是一等本地全文来源。T2 会用 `literature_identity.find_matching_seed_pdf()` 做 seed title 与 PDF 文件名的 fuzzy matching，容忍作者/年份前缀、中文“等”、截断、标点和大小写差异。命中后写入 `has_seed_pdf=true`、`seed_pdf_path=user_seeds/pdfs/...`、`has_local_pdf=true`、`access_score=1.0`、`evidence_level=FULL_TEXT`、`access_level_hint=FULL_TEXT_LOCAL`，推荐动作优先为 `read_seed_pdf`。这一步只做身份匹配，不判断论文是否学术相关；T3 仍必须用 `extract_pdf_text` 的完整页码覆盖来证明最终 note 可以标 `[FULL-TEXT]`。旧中文 PDF seed 如果把期刊页眉或期号识别成标题，T2 recovery 会在 title 明显是 masthead/page header 时从 PDF 文件名修复标题，并保留 `title_repair_reason` 审计字段。

### T2 的成功标准

当前 validator 会检查：

- `papers_dedup.jsonl` 数量达到最低可用数量 10，且不超过 `agents.scout.behavior.t2_finalize.active_pool_max`
- 关键字段存在
- `dedup <= raw`
- 已核验保留候选 `papers_verified.jsonl` 存在且数量合理
- `verification_failures.jsonl` schema 正确
- `deep_read_queue.jsonl` 必须来自 verified 池
- 已核验保留候选 `papers_verified.jsonl` 中每篇论文都必须在 `deep_read_queue.jsonl` 中有 deep_read 或 shallow_read/backlog 处置，不能因为缺 `semantic_screen` 被静默丢弃；保留候选集之外的候选应在 `papers_backlog.jsonl` 可见
- 如果存在 seed papers，queue 中必须保留 seed
- `domain_map.json` 必须存在，包含 `core`、`theory_bridge`、`adjacent`、`boundary`、`citation_edges`、`bucket_assignments`，且 semantics 为 `domain_map_for_synthesis_and_ideation_not_final_gaps`
- 如果 verified 池有 `semantic_screen` 允许的跨域/理论桥接候选，deep-read queue 至少保留一个这类候选并放入非 `triaged_out` 的 `seed/mainline_deep/bridge_deep` 阅读区

#### `missing_areas.md` 结构化校验

如果 `missing_areas.md` 存在且包含 `## Retrieval Coverage Hints`，validator 会：

1. 按 `### 提示 \d+` 正则拆分每个提示段落
2. 对每个提示检查四个必需加粗字段：`**覆盖缺口**`、`**为什么需要复核**`、`**建议动作**`、`**难度**`
3. 如果缺少任一字段，validation 失败

同时会拒绝旧版模板：如果内容包含 `## 可探索缺口`、`为什么是缺口`、`可探索方向`，validation 失败。这意味着旧格式的 `missing_areas.md` 必须迁移为新结构化格式。

T2 校验通过后会进入 `T2-COVERAGE-GATE`。这个 gate 让用户决定是否接受当前覆盖、是否按 `missing_areas.md` 的建议追加检索，或先暂停补充 seed/query。`missing_areas.md` 里的内容只是 coverage hint，不会自动变成研究缺口，也不会让 runtime 在没有用户确认时重新检索。这样保留 Scout LLM 的覆盖判断，同时把“要不要继续补检”的最终决策交给用户。`T2-COVERAGE-GATE` 是 immediate gate，完整 `run/resume` 中会在 T2 校验通过后的同一次运行里直接展示 gate；只有 stdin 不可用时才会写入 `PAUSED`，避免出现“任务完成后无解释地等待 resume”的体验。

### T2 的确定性收尾（受控 Orchestrator Hook）

T2 的 raw -> dedup/verified/queue/audit/missing_areas 是机械转换，应该由 runtime 工具完成；但“检索覆盖是否足够”是学术判断，必须由 Scout LLM 根据主题、seed、core/baseline/evaluation/adjacent/theory 覆盖来判断。因此当前不再支持“只凭 raw_count 自动结束 T2”。

Orchestrator 现在只在三类窄口调用 `_finalize_t2_from_raw`：

#### 路径 1：Scout 明确调用 `finish_task`

Scout 完成覆盖判断后调用 `finish_task`，runner 才执行 `t2_finish_finalize`：

- **触发条件**：当前 task 为 `T2`，且 `finish_task` 成功返回
- **阈值**：`agents.scout.behavior.t2_finalize.finish_finalize_min_raw`，默认 30，下限 10；`ctx.extra.t2_finish_finalize_min_raw` 只作为临时覆盖
- **行为**：从 `papers_raw.jsonl` 确定性产出 dedup、verified、deep_read_queue、domain_map、access_audit、search_log、missing_areas 等文件，然后再运行 T2 validator

这条路径避免 LLM 手工解析大文件，同时保留 LLM 对“还要不要继续检索”的判断权。

#### 路径 2：真实 resume 预收尾

在 runner 启动时，`_maybe_finalize_t2_before_llm` 只在真实恢复场景中尝试补齐：

- **触发条件**：`is_resume/resumed_from/resumed_from_run_id` 存在，或 `resume_reason` 为 `interrupted` / `iteration`
- **阈值**：同 `agents.scout.behavior.t2_finalize.finish_finalize_min_raw`
- **行为**：如果已有完整 T2 产物且校验通过，直接跳过 LLM；如果只有 raw 和部分缺失产物，先尝试确定性补齐

普通冷启动、普通 LLM error、或 `retry_after_failure` 不会仅凭 raw 文件自动完成 T2，除非 ctx.extra 显式设置 `allow_t2_failure_recovery: true`。

#### 路径 3：显式 recovery

少数测试或人工确认过的恢复场景可以显式设置 `allow_t2_failure_recovery: true`。这用于“已经确认 Scout 覆盖判断完成，但运行在收尾前异常退出”的情况，不作为默认路径。

三条路径都调用 `_finalize_t2_from_raw`，内部流程：

1. 检查 `papers_raw.jsonl` 存在且行数满足阈值
2. 如果任何非 `papers_raw` 的期望产物缺失或校验失败，调用 `finalize_t2_outputs()` 从 raw 确定性产出全部产物
3. 验证完整输出集

这意味着：**工具负责机械收尾，LLM 负责覆盖判断。** 检索工具会自动把搜索结果追加到 `literature/papers_raw.jsonl`，但不会因为 raw_count 很大就结束 T2；只有 Scout 调用 `finish_task` 或真实 resume/recovery 才会触发确定性收尾。

### T2 的单任务/完整运行例子

单独跑 T2：

```bash
cd ResearchOS
researchos run-task T2 --workspace ./workspace/local-test2
```

如果想恢复完整链路里的 T2 之后阶段：

```bash
cd ResearchOS
researchos resume --workspace ./workspace/local-test2
```

---

## 6.4 T3：ReaderAgent（read）

### 角色

- Agent：`ReaderAgent`
- mode：`read`
- 代码： [researchos/agents/reader.py](../researchos/agents/reader.py)
- Prompt： [researchos/prompts/reader.j2](../researchos/prompts/reader.j2)

### 当前默认配置

T3 的模型、预算和 resume 行为以 `config/user_settings.yaml` 的运行时覆盖为准。
checked-in 默认启用 `budget.defaults.unlimited_budget: true` 时，Reader 不会因为
step/token/wall 预算自动暂停；T3 仍会受 PDF 工具超时、输出 validator、workspace
权限和 LLM 单次调用超时约束。

主要工具：

- `read_file`
- `write_file`
- `append_file`
- `list_files`
- `fetch_paper_pdf`
- `extract_paper_sections`
- `extract_pdf_text`
- `lookup_paper_record`
- `save_paper_note`
- `build_synthesis_workbench`
- `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 项目方向和上下文 |
| `papers_dedup` | `literature/papers_dedup.jsonl` | 是 | 保留候选集，旧 workspace 回退时使用 |
| `papers_verified` | `literature/papers_verified.jsonl` | 否但强烈推荐 | 已核验保留候选 |
| `papers_backlog` | `literature/papers_backlog.jsonl` | 否 | 保留候选集之外的轻量补读候选 |
| `deep_read_queue` | `literature/deep_read_queue.jsonl` | 否但强烈推荐 | T2 筛好的精读队列 |
| `deep_read_queue_pending` | `literature/deep_read_queue_pending.jsonl` | 恢复时优先 | T3 恢复运行时真正还没读完的队列 |
| `domain_map` | `literature/domain_map.json` | 否但强烈推荐 | T2 引用图领域地图；用于识别高桥接邻接论文 |
| `access_audit` | `literature/access_audit.md` | 否 | 辅助判断 PDF / abstract 可用性 |
| `missing_areas` | `literature/missing_areas.md` | 否 | 后续综合时的检索覆盖提示 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper_notes_dir` | `literature/paper_notes/` | 每篇论文一份结构化笔记；包含 `Reading Coverage` 阅读覆盖记录 |
| - | `literature/paper_notes_abstract/` | abstract sweep 补读的精简笔记（可选，由 orchestrator hook 自动生成） |
| - | `literature/metadata_triage.md` | metadata-only 候选的批量 LLM triage；只作资源补取/升级阅读线索，不作 claim 证据 |
| `notes_manifest` | `literature/notes_manifest.json` | T3 完成度账本；记录每个 queue rank 对应 note 是 complete / incomplete / missing |
| `comparison_table` | `literature/comparison_table.csv` | 方法、指标、结论的横向对比表（含 `evidence_level` 列） |
| `related_work_bib` | `literature/related_work.bib` | BibTeX 引用库 |

### `paper_notes/*.md` 到底是什么

它是 T3 的核心证据单元。

每篇 note 不是随便写摘要，而是后续：

- `T3.5` 文献综合
- `T4` 假设生成
- `T8` 写论文

都会继续消费的结构化研究笔记。

### T3 真正按什么顺序选论文

Reader 当前优先级是：

1. `deep_read_queue_pending.jsonl`
2. `deep_read_queue.jsonl`
3. `papers_verified.jsonl`
4. `papers_dedup.jsonl`

这意味着：

- 如果有恢复队列，就优先恢复队列
- 如果没有恢复队列，就优先精读队列
- 如果 T2 没跑出 queue，才退回 verified 或 dedup

### T3 如何处理已有进度

Reader 会扫描：

- `literature/paper_notes/`
- `literature/notes_manifest.json`
- `comparison_table.csv`
- `related_work.bib`

如果已经存在一部分产物，它的目标不是重写，而是：

1. 刷新 `notes_manifest.json`，识别每个 queue rank 的 `complete` / `incomplete` / `missing`
2. 补齐已有 note 对应的 table / bib 记录
3. 只处理 pending queue 中尚未 complete 的论文

### T3 对 seed papers 的态度

seed papers 是最高优先级，不是普通候选。

具体表现为：

- queue 中的 seed 必须优先读
- 如果 queue 中有 seed 但没生成 note，validator 会认为未完成

### T3 逐篇阅读的真正流程

对每篇论文，典型流程是：

1. 以 queue rank 为工作单位；优先从 `deep_read_queue_pending.jsonl` 读取 rank
2. 用 `lookup_paper_record(queue_rank=...)` 获取单篇 metadata，避免整池读入上下文
3. 看 `notes_manifest.json` 是否已有 complete note
4. 若已有且结构、证据锚点、`Reading Coverage` 都合格，才跳过或只补 table/bib
5. 若无本地 PDF，尝试 `fetch_paper_pdf`
6. 只要本地存在 PDF 或下载成功，就用 `extract_pdf_text` 读到 `total_pages` 的最后一页
7. 如果一次读取被 `max_chars` 或 runtime 截断，就缩小页码范围分块重读，直到覆盖 `1-total_pages`
8. 如果 PDF 可得但只完成部分页面，note 必须标为 `[PARTIAL-TEXT]`
9. 如果连全文都拿不到，再退化成 `[ABSTRACT-ONLY]`
10. 用 `save_paper_note(queue_rank=..., content=...)` 保存完整 markdown；工具自动生成 `paper_notes/{id}.md`、即时校验并刷新 `notes_manifest.json`

### 实际执行过程

`ReaderAgent(read)` 进入时会先列出 `literature/paper_notes/`，再读取或刷新 `literature/notes_manifest.json`；如果存在 `literature/deep_read_queue_pending.jsonl`，就按 pending queue 的 queue rank 继续，否则读 `deep_read_queue.jsonl`，再回退到 `papers_verified.jsonl`，最后才回退到 `papers_dedup.jsonl`。处理每篇论文前，它优先用 `lookup_paper_record(queue_rank=...)` 取该论文 metadata；只有没有 queue rank 的旧 workspace 才用 `paper_id` / `title` 查找。已有 note 只有在结构、Evidence 锚点和 `Reading Coverage` 都合格时才算完成。`notes_manifest.json` 会把每条队列记录标为 `complete`、`incomplete` 或 `missing`：同名 note 存在但缺章节时是 `incomplete`，不会再被误报成“没读”。

PDF 获取顺序是：先读 queue/paper record 里的 `seed_pdf_path`，再查 `literature/pdfs/{normalized_id}.pdf` 或 `literature/pdfs/{paper_id}.pdf`，最后才调用 `fetch_paper_pdf(paper_id=..., save_path="literature/pdfs/{normalized_id}.pdf")` 尝试下载。这里的 `normalized_id` 只用于 PDF 路径；note 文件名由 `save_paper_note` 从队列记录自动生成，Reader 不需要手抄 `noopenalex__...` 或其它十六进制 ID。这样用户已经上传到 `user_seeds/pdfs/` 的本地全文不会被重复下载或误判为 abstract-only。

`fetch_paper_pdf` 不是只靠传入的 ID 猜 URL。它会先回查 `literature/deep_read_queue.jsonl`、`papers_verified.jsonl`、`papers_dedup.jsonl` 和 `papers_raw.jsonl` 中同一论文的 metadata，把 `pdf_url`、OpenAlex OA location、arXiv ID、DOI 和 landing page 转成候选 PDF URL。这样即使 Reader 传入的是 canonical OpenAlex ID 或 `noopenalex::*` fallback ID，也能利用上游搜索阶段已经抓到的 PDF 线索。下载成功仍不等于 FULL-TEXT；只有 `extract_pdf_text` 覆盖全部页码且最终无截断，note 才能写 `[FULL-TEXT]`。

拿到 PDF 后用 `extract_pdf_text` 按页读取，如果返回的 metadata 显示 `preview_truncated_by_max_chars=true` 或 runtime 上下文裁剪，就继续用更小的 `start_page/max_pages` 分块重读，直到 `Pages read` 覆盖 `1-total_pages`。写出时先组织完整 19 节 markdown，再调用 `save_paper_note(queue_rank=..., content=...)` 保存；工具会即时运行 note 结构校验，若返回 `note_incomplete`，Reader 必须按缺失字段修补同一 queue rank，而不是另建 alias 文件。`comparison_table.csv` 和 `related_work.bib` 仍由 `append_file` 或重写方式维护。如果这篇只能基于摘要，note 必须标 `[ABSTRACT-ONLY]`，并写 `## A. 核心做法/视角` 与 `## B. 桥接点` 两个轻字段。收尾时 validator 会读取 `notes_manifest.json` 和实际 note，检查 `- **Status**:`、核心章节、数字证据 `[Evidence: ...]`、全文页码覆盖、最终截断状态、CDR §14-§19、abstract A/B 字段、queue 覆盖率和 seed paper 覆盖情况。

### T3 如何判定 FULL-TEXT

`[FULL-TEXT]` 不是“拿到过 PDF”或“读了足够多内容”的意思。

当前判定标准是：

- `extract_pdf_text` 的页码覆盖必须到最后一页
- note 的 `## 12. Reading Coverage` 必须写清楚 `Pages read`，可以是连续范围，例如 `1-12 / 12`
- 也可以是分块重读覆盖全篇，例如 `1-4, 5-8, 9-12 / 12`
- `all pages` / `complete` 这类没有总页数和范围的泛化描述不能通过 FULL-TEXT 校验
- `Truncation` 必须明确最终状态为 `none` / `无` / `final no truncation` / `最终未截断`
- 如果第一次读取被 50000 字符上限截断，但后续分块重读已经覆盖全部页码，`Truncation` 要写明“初次截断已通过分块重读解决；最终未截断”
- 若只读了 `1-8 / 20`，或者仍有未解决的分块截断，只能标为 `[PARTIAL-TEXT]`

`extract_paper_sections` 仍然可以作为定位方法、实验、结论的辅助工具，但不能代替全文覆盖。

### paper note 的 Reading Coverage

每篇 note 现在必须包含：

```markdown
## 12. Reading Coverage
- **PDF source**: literature/pdfs/{id}.pdf
- **Pages read**: 1-12 / 12
- **Extraction calls**: extract_pdf_text pages 1-4, 5-8, 9-12
- **Truncation**: first full call truncated by runtime context cap; resolved by chunked rereads; final no truncation
- **Status rationale**: All PDF pages were read without truncation.
```

如果没有 PDF：

```markdown
## 12. Reading Coverage
- **PDF source**: not available
- **Pages read**: 0 / unknown
- **Extraction calls**: none
- **Truncation**: none
- **Status rationale**: PDF was not available; note is based on abstract and metadata.
```

### paper note 的 Mechanism Claim

每篇 note 现在还必须包含第 13 节，用于后续 T4/T4.5 的 mechanism-aware 假设生成和碰撞检测：

```markdown
## 13. Mechanism Claim
- **Stated mechanism**: {一句话，作者声称是什么导致 improvement；实在抽不出来写 "not clearly stated"}
- **Evidence type**: {ablation_supported | theoretically_justified | empirical_correlation | claimed_untested}
- **Supporting artifact**: {Fig X / Table Y / Section Z / "none"}
```

这个字段会被 T4 的四类来源约束（机制质疑型）和 T4.5 的 `extract_mechanism_tuple` 工具消费。如果 §13 缺失或字段为空，validator 会拒绝通过该 note。

### paper note 的 CDR 与 abstract A/B 字段

Full/partial note 必须继续包含 §14-§19：

- `## 14. Design Rationale`
- `## 15. Artifact & Design Principles`
- `## 16. Data View & Evaluation Mode`
- `## 17. Contribution Type`
- `## 18. Boundary Conditions`
- `## 19. Cross-Paper Tension`

这些字段是后续 T3.5/T4/T8 的 CDR 素材，不是工具硬编码结论。Reader LLM 要基于论文内容判断，不确定时写明证据边界和 `LLM_REVIEW_REQUIRED`。

任何 `[ABSTRACT-ONLY]` note（无论在 `paper_notes/` 还是 `paper_notes_abstract/`）还必须包含：

```markdown
## A. 核心做法/视角
- {abstract-level 方法、理论视角或设计视角}

## B. 桥接点
- {它与主线领域、邻接领域或 theory bridge 的连接点；没有则写 no obvious bridge}
```

A/B 是轻量桥接提示，目的是让 abstract-only 和邻接论文在 synthesis 中可见。它不能替代 full-text 证据，也不能被写成已验证机制。

### T3 当前的恢复机制

这是当前最成熟的恢复阶段之一。

核心行为：

- 根据已有且通过结构校验的 `paper_notes/` 自动裁出 `deep_read_queue_pending.jsonl`
- 同步刷新 `literature/notes_manifest.json`，它是 T3 queue 覆盖的权威账本
- 重新跑时优先读取 pending queue
- `deep_read_queue_pending_meta.json` 是恢复快照，不是逐 token 实时进度；现在 T3 在成功、预算/步数暂停、校验修复暂停和失败退出时都会 best-effort 刷新该快照，避免用户看到旧的 `completed_note_count`
- pending 裁剪不只看 note 文件名，还会解析 note 头部的 `ID`、`Normalized ID`、`DOI/arXiv` 和标题，并与 queue 的 `normalized_id`、`paper_id`、`title`、`doi`、`externalIds` 做多 key 匹配，避免 `arxiv:2605.17641` / `arxiv_2605.17641_Title.md`、`noopenalex::...` / `noopenalex__...` 这类写法差异导致重复阅读
- 缺少 `Reading Coverage`、`Mechanism Claim`、`[FULL-TEXT]` 页码不完整、或最终截断状态未说明为 `none` / `无` / 已解决的旧 note，不会被视为已完成
- `paper_notes/` 中历史重复 stub 或坏 note 不再直接拖死整体验证；只有结构合格 note 会计入完成数。若合格数量/queue 覆盖不足，validator 会区分 `missing` 与 `incomplete`：如果 note 已匹配但缺 `Claims vs Evidence`、`Key Quotes`、CDR 等结构，会直接列出文件名和缺失章节
- 不再默认把整个 `papers_dedup` 当作“必须重读”的任务池

`notes_manifest.json` 的每个 entry 包含 `queue_rank`、`paper_id`、`canonical_id`、`note_path`、`status`、`validation_error`、`sections_missing`、`seed_priority` 和 `target_bucket`。resume 时，`deep_read_queue_pending.jsonl.queue_rank` 是当前待读队列的临时 rank；每条 pending 记录会保留 `original_queue_rank` 指向完整 `deep_read_queue.jsonl`，并保留 `pending_queue_rank` 供日志显示。`save_paper_note` 会按实际消费的 pending/full queue 刷新 manifest，避免 pending rank 1 与 full queue rank 1 混淆。`deep_read_queue_pending_meta.json` 当前会记录 `completed_note_count`、`completed_note_key_count`、`pending_queue_count`、`valid_note_file_count`、`invalid_note_file_count`、`manifest_complete_count`、`manifest_incomplete_count`、`manifest_missing_count` 和 `refresh_reason`。其中 `completed_note_count` 指结构合格的 note 文件数，`completed_note_key_count` 是为了跨 ID/标题/DOI 匹配而生成的内部 key 数，二者不应该混用。

### `comparison_table.csv` 有什么用

它是把逐篇 note 结构化成横向表格，便于后续：

- 发现 baseline
- 对比指标
- 看方法家族分布
- 在写作阶段生成表格和 related work 叙述

### `related_work.bib` 有什么用

这是写论文和投稿时真正要用的引用库。

T8/T9 会直接消费它，而不是重新从 note 手工抽引用。

### T3 成功标准

当前 validator 会检查：

- `paper_notes/` 存在
- note 结构合理，包含核心章节、`## 13. Mechanism Claim`、`## 14`-`## 19` CDR 字段
- 每篇 note 包含 `## 12. Reading Coverage`
- 每篇 note 包含 `## 13. Mechanism Claim`，三个 bullet（Stated mechanism / Evidence type / Supporting artifact）均非空
- `[ABSTRACT-ONLY]` note 必须包含 `## A. 核心做法/视角` 和 `## B. 桥接点`
- `[FULL-TEXT]` note 必须记录完整页码覆盖和最终无截断；分块重读覆盖全篇是合法的
- 如果 queue 存在，默认按 `deep_read_target` 校验；只有 `literature/literature_params.json` 或配置中 `require_deep_read_target=false` 时，才允许达到 `deep_read_min` 后放行
- queue 中 seed papers 必须覆盖
- `comparison_table.csv` 存在，含 `evidence_level` 列
- `related_work.bib` 存在
- 如果 `paper_notes_abstract/` 存在，每篇 abstract note 必须通过结构校验（5 节 + §13，Evidence type = `abstract_claim_hint`；旧产物的 `claimed_untested` 兼容但不推荐）

#### Key Results 证据锚点校验

`_validate_key_results_evidence`（`reader.py` line 774）会逐行解析 `## 3. Key Results` 段落：

- 任何包含数值的行（百分比、小数、倍数等）**必须**在同一行末尾包含 `[Evidence: ...]`
- 模型版本号（如 `GPT-4`、`Llama-3.1`）不触发数值检查
- 如果一行有数字但没有 `[Evidence: ...]`，该 note 被判为不合格
- 这意味着"恢复时先修质量"不只是 prompt 要求，validator 也会强制执行

#### Reading Coverage 校验

`_validate_reading_coverage`（`reader.py` line 512）检查 `## 12. Reading Coverage` 下的 5 个子字段：

1. `PDF source`
2. `Pages read`
3. `Extraction calls`
4. `Truncation`
5. `Status rationale`

对于 `[FULL-TEXT]` 标记的 note：
- `Pages read` 必须覆盖全部页面（如 `1-12 / 12`）
- `Truncation` 必须说明最终无截断（如 `none`、`final no truncation`、`已解决`）
- 分块重读后覆盖全篇是合法的，但必须在 `Truncation` 中说明

#### Mechanism Claim 校验

`_validate_mechanism_claim`（`reader.py` line 563）检查 `## 13. Mechanism Claim` 下的 3 个字段：

1. `Stated mechanism` — 非空
2. `Evidence type` — 非空，且为合法值（`ablation_supported`、`theoretically_justified`、`empirical_correlation`、`claimed_untested`）
3. `Supporting artifact` — 非空

### T3 的 Abstract Sweep（轻量补读）

Deep read 完成后，orchestrator 自动运行 abstract sweep，默认补读尚未被 `paper_notes/` 或 `paper_notes_abstract/` 覆盖的候选。它只基于 title/abstract/metadata 做轻量补读，不是全文证据。含 abstract 的论文写入 `literature/paper_notes_abstract/`；只有 title/year/venue/DOI 等 metadata 的候选不再逐篇伪装成 note，而是批量写入 `literature/metadata_triage.md`，作为资源补取和升级阅读线索。若 `T2-PARAM-GATE` 选择综述均衡/强覆盖，`abstract_sweep.lite_paper_num=all_readable`，并允许从 `papers_backlog.jsonl` 中回捞有摘要/PDF 的候选补足可读覆盖。

配置在 `config/agent_params.yaml` 的 `reader.modes.read.behavior.abstract_sweep`：

```yaml
reader:
  modes:
    read:
      behavior:
        abstract_sweep:
          enabled: true
          lite_paper_num: 120     # 研究论文默认轻读上限；综述 gate 可设 all_readable
          min_relevance: 0.0      # 默认不按 metadata hint 丢弃
          sources: [papers_verified, papers_dedup]
          exclude_already_read: true
          include_metadata_only: true
          exclude_semantic_excluded: true
          metadata_triage_report: literature/metadata_triage.md
          priority_weights:
            relevance: 0.70
            resource: 0.20
            year: 0.10
```

候选过滤：

- 跳过 `paper_notes/` 已覆盖的论文，匹配使用 ID、canonical_id、DOI、arXiv、title overlap 等多 key，不只看文件名
- 跳过 `paper_notes_abstract/` 已覆盖的论文
- 默认跳过 `deep_read_queue.jsonl` 或候选自身已标为 `read_disposition=deferred/backlog`、`triaged_reason=bridge_pool_cap_exceeded/t2_active_pool_cap_exceeded/domain_profile_filtered` 的记录；但 `metadata_replacement_policy=replace_metadata_only_with_readable_backlog_when_available` 时，允许从 backlog 回捞有摘要/PDF 的候选补足综述可读覆盖
- 跳过 explicit duplicate；默认跳过 semantic exclude / `shared_keyword_only/unrelated` / `can_enter_deep_read=false`，避免已排除论文重新进入 BibTeX、comparison table 和 T8 写作语料。需要排除线索复核时可显式设为 `exclude_semantic_excluded: false`
- 保留缺摘要但有 title 的 metadata-only 候选，但只进入批量 triage report，不写入 per-paper note / BibTeX / comparison table
- 如果 T3 已经完成全文/部分全文 note，abstract sweep 不再重复写一个 abstract note
- 候选预算 `lite_paper_num` 是 abstract note 与 metadata triage 的总候选 cap；`all_readable` 表示不设用户可见轻读篇数上限。排序使用 `abstract_sweep_score = relevance/resource/year` 加权，默认权重是 `0.70/0.20/0.10`

执行方式：

1. 有 abstract 的候选默认调用 Reader LLM 读取单篇 title/abstract，写出 5 节 + §13 的 abstract-only 轻量 note
2. 缺 abstract 的 metadata-only 候选进入一次批量 Reader LLM triage；报告必须声明 metadata-only，不得声称读过摘要/全文，不得输出机制或实验 claim。LLM 失败或中断恢复路径才使用确定性 fallback report
3. LLM note 必须保留行级 `## A. 核心做法/视角` 和 `## B. 桥接点`，供 T3.5/T4/T8 复用；runtime 会把常见的 `### A/B` heading 漂移确定性规范回 `## A/B`
4. `## 13. Mechanism Claim` 必须含 `Stated mechanism / Evidence type / Supporting artifact`；如果 LLM 漏字段，runtime 只补保守占位，不提升证据强度
5. Reader LLM 调用失败时使用确定性 fallback 生成保守 note
6. abstract sweep 的格式问题会在 T3 校验前确定性修复；若仍失败，完整 pipeline 会 `PAUSED` 并在控制台显示 `Pause reason`

产出：
- `literature/paper_notes_abstract/` — 含 abstract 候选的精简 note（5 节 + §13 Mechanism Claim）
- `literature/metadata_triage.md` — metadata-only 候选的批量 triage report；不进入 claim evidence
- `comparison_table.csv` 仅为有 abstract note 的论文追加行（`evidence_level=ABSTRACT_ONLY`）
- `related_work.bib` 仅为有 abstract note 的论文追加条目
- `literature/access_audit.md` 追加 abstract sweep 摘要

Abstract note 结构：
- §1 Problem & Motivation（abstract opening snippet，标记 LLM_REVIEW_REQUIRED）
- §2 Method Summary（abstract middle snippet，标记 LLM_REVIEW_REQUIRED）
- §A 核心做法/视角（abstract-level 方法或理论视角）
- §B 桥接点（与主线、邻接领域或 theory bridge 的连接点）
- §3 Key Claimed Results（abstract closing snippet，标记 LLM_REVIEW_REQUIRED）
- Raw Abstract（原始摘要全文）
- §13 Mechanism Claim（Evidence type 固定为 `abstract_claim_hint`）
- Source（标注 abstract only）

abstract sweep 的目标是让未进入 deep-read target 但有摘要的论文也形成可读的低成本证据提示。它仍然必须标注 abstract-only，不能被 T8 当作 FULL-TEXT 证据；metadata-only triage 只能提示“值得补 abstract/PDF/DOI 查证”，不能被 T8 当作论文证据。如果轻量阅读发现某篇论文对主线非常关键，应在后续 resume/人工补读中升级为 deep read，而不是在 abstract note 或 metadata report 里伪装全文阅读。

### T3 的运行例子

单独跑：

```bash
cd ResearchOS
researchos run-task T3 --workspace ./workspace/local-test2
```

恢复完整流程：

```bash
cd ResearchOS
researchos resume --workspace ./workspace/local-test2
```

---

## 6.5 T3.5：ReaderAgent（synthesize）

### 角色

- Agent：`ReaderAgent`
- mode：`synthesize`
- 代码： [researchos/agents/reader.py](../researchos/agents/reader.py)
- Prompt： [researchos/prompts/reader.j2](../researchos/prompts/reader.j2)

### 当前默认配置

- model tier：`medium`
- 主要工具：
  - `read_file`
  - `write_file`
  - `list_files`
  - `build_synthesis_workbench`
  - `finish_task`
- 读写特点：
  - 读 `literature/paper_notes/`
  - 读 `comparison_table.csv`
  - 写 workbench / outline / draft / final synthesis

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 项目方向 |
| `paper_notes_dir` | `literature/paper_notes/` | 是 | T3 的逐篇笔记 |
| `comparison_table` | `literature/comparison_table.csv` | 是 | 横向比较表 |
| `missing_areas` | `literature/missing_areas.md` | 否 | T2 识别到的不足覆盖；只作为 coverage hint |
| `domain_map` | `literature/domain_map.json` | 是 | T2 的引用图领域地图，提供 `citation_edges`、core/theory_bridge/adjacent/boundary 分桶和 audit |

`domain_map.json` 在 T3 读取阶段仍可作为恢复/旧 workspace 的可选 hint，但在 T3.5 synthesis 阶段是单任务强前置。原因是 `updataPreT5.md` 把引用图/领域地图定义为贯穿主轴：T3.5 需要它作为客观综述骨架和 `adjacent_transfers` 的来源；缺失时应回到 T2/T3 修复，而不是让 synthesis 静默退回纯 prompt 聚类。

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `synthesis` | `literature/synthesis.md` | 文献综合结果，供 T4/T6/T8 消费 |
| `synthesis_workbench` | `literature/synthesis_workbench.json` | 从 paper_notes 抽取的结构化证据、方法家族、frontier、趋势和问题候选 |
| `synthesis_outline` | `literature/synthesis_outline.md` | 分阶段大纲 |
| `synthesis_draft` | `literature/synthesis_draft.md` | 工具生成的写作指导和证据脚手架，不是最终学术结论 |

### 这一步到底做什么

T3.5 不是继续搜论文，也不是继续逐篇读，而是把：

- 单篇笔记（`paper_notes/*.md` + `paper_notes_abstract/*.md`）
- metadata-only 批量排查报告（`metadata_triage.md`，只能作为补资源/升级阅读线索）
- 方法对比表（`comparison_table.csv`）
- T2 发现的检索覆盖提示（`missing_areas.md`）
- T2 引用图领域地图（`domain_map.json`，含 `theory_bridge`）

压缩成一个面向研究决策的领域综合。

核心原则是 **LLM-first**：Reader LLM 负责智能分析（方法家族分类、共同假设提取、趋势识别、研究问题生成），工具负责结构化组装（证据片段提取、workbench JSON 生成、outline 渲染）。工具不做领域判断，LLM 不做机械搬运。

当前实现采用 3 阶段流程：

**阶段 1：LLM 分析** — Reader 先用自身理解逐篇分析 paper_notes，生成四类 `llm_insights`（见下文 `LLMInsights` 模型），不调用任何工具。

**阶段 2：工具组装** — Reader 调用 `build_synthesis_workbench(write_final=false, render_draft=false, llm_insights={...})`，把 LLM 洞察传入工具。工具读取 paper_notes 提取证据片段，用 LLM 提供的分类/假设/趋势/问题组装 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`。

**阶段 3：LLM 审阅与写作** — Reader 读取工具产物，审阅方法家族分类是否准确、假设是否有论文支持、问题是否可操作，然后亲自写 `literature/synthesis.md`。工具草稿不是最终综述，LLM 必须修正不合理的候选结论。

工具产物不能让运行时 deterministic completion，也不能替代方法家族、共同假设、趋势和研究问题的学术判断。

### 实际执行过程

`ReaderAgent(synthesize)` 的执行分为三个阶段：

#### 阶段 1：LLM 分析（不调用工具）

Reader 加载 `literature-synthesis` guidance 后，先用自己的 LLM 能力逐篇分析 `paper_notes/` 和 `paper_notes_abstract/` 中的笔记，必要时查看 `metadata_triage.md` 识别需要补资源或升级阅读的候选；`metadata_triage.md` 不能作为 family/trend/claim 的证据来源。随后生成四类洞察：

- **family_classifications**：每篇论文的方法家族分类（基于 method_overview、Technical Details 等实际内容，禁止基于标题宽泛关键词做表面分类）
- **shared_assumptions**：2-4 个跨论文的共同假设（从 Limitations、Weaknesses/Gaps、Claims vs Evidence 中提取）
- **trends**：2-3 个技术趋势（对比近期和早期论文的 method_overview 和 Key Results 变化）
- **research_questions**：3-6 个可操作研究问题（基于 Gaps、Missing Areas coverage hints 和 LLM 分析）

这四类洞察封装为 `LLMInsights` Pydantic 模型（定义在 `researchos/tools/literature_synthesis.py`）：

```python
class LLMInsights(BaseModel):
    family_classifications: list[FamilyClassification]  # paper_id + family + confidence
    shared_assumptions: list[SharedAssumption]          # assumption + why_questionable + supporting_papers
    trends: list[Trend]                                 # trend + recent_papers + contrast_papers
    research_questions: list[ResearchQuestion]           # id + question + why_unsolved + related_papers
```

#### 阶段 2：工具组装

Reader 调用 `build_synthesis_workbench(write_final=false, render_draft=false, llm_insights={...})`，将阶段 1 的分析结果通过 `llm_insights` 参数传入工具。

工具内部逻辑：
1. 解析 `paper_notes/*.md` 和 `paper_notes_abstract/*.md`，提取每篇 note 的标题、年份、方法概述、关键结果、局限、问题、§13 Mechanism Claim、abstract A/B 桥接字段
2. 读取 `comparison_table.csv` 提取指标和效率线索
3. 如果存在 `missing_areas.md`，纳入候选问题（只保留为 coverage hints，不写成已验证研究缺口）
4. 如果存在 `domain_map.json`，读取 `citation_edges` 和 core/theory_bridge/adjacent/boundary 分桶，写入 `citation_graph_context`、`domain_map_bucket_summary`，并从 `domain_map.adjacent`、`domain_map.theory_bridge` 与 note 的 A/B 字段生成 `adjacent_transfers`
5. **用 LLM 提供的 `family_classifications` 做方法家族聚类**（不再使用硬编码关键词匹配）；如果 LLM 未提供某篇论文的分类，回退到 `LLM_REVIEW_REQUIRED: {method_text}` 占位
6. **用 LLM 提供的 `shared_assumptions` 生成共同假设候选**；如果未提供，回退到从 note 的 Limitations/Gaps 段落提取 review hint
7. **用 LLM 提供的 `trends` 生成趋势候选**；如果未提供，回退到按年份分组的 chronological evidence
8. **用 LLM 提供的 `research_questions` 生成研究问题候选**；如果未提供，回退到从 note 的 questions/gaps 段落提取
9. 构建 `mechanism_claim_clusters`（从 §13 Mechanism Claim 按关键词相似度聚类）
10. 写 `synthesis_workbench.json`、`synthesis_outline.md` 和 guidance 型 `synthesis_draft.md`

工具只写这三类 staged artifacts，不写最终 `synthesis.md`（`write_final` 默认为 `false`）。

#### 阶段 3：LLM 审阅与写作

Reader 读取工具生成的 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`，但不把工具草稿当作最终文献判断。LLM 必须检查：

1. 方法家族分类是否准确反映了论文实际内容
2. 共同假设是否有足够的论文支持
3. 研究问题是否可操作
4. 引用是否来自真实 paper_notes
5. `domain_map` 中的 core/theory_bridge/adjacent/boundary 是否只作为综述骨架和迁移素材，而不是被工具当成最终研究缺口

然后由 LLM 直接写 `literature/synthesis.md`。如果工具产物中的候选分类、假设或问题不合理，必须改写。

最后 `finish_task` 触发 validator 检查章节、长度和 paper note 引用数量。

### 期望章节

当前实现和文档都要求至少覆盖：

1. 方法家族分类
2. 共同假设
3. 贡献空间地图（Contribution-Space Map）
4. 跨论文矛盾/张力（Trends & Cross-Paper Contradictions）
5. 邻接领域可迁移机制（Adjacent-Domain Transferable Mechanisms）
6. 技术趋势
7. 可操作研究问题

### `synthesis_workbench.json` 的结构

这个文件是 T3.5 最重要的结构化产物，包含以下字段：

| 字段 | 含义 |
| --- | --- |
| `note_count` | full-text note 数量 |
| `abstract_note_count` | abstract-only note 数量 |
| `total_note_count` | 总 note 数量 |
| `weak_evidence_summary` | 靠前展示的弱证据摘要，确保 T4 prompt 截断时仍能看到 `abstract-only/metadata-only` 只能作补资源或弱 idea fuel |
| `paper_ids` | 所有论文 ID 列表 |
| `method_families` | 方法家族聚类（最多 5 个），每个包含 `name`、`paper_ids`、`full_or_partial_paper_ids`、`abstract_only_paper_ids`、`representative_titles`、`core_observations`、`result_observations`、`evidence_levels`、`allowed_use` 和 `_abstract_count`。当 LLM 提供 `llm_insights.family_classifications` 时，家族名称和成员分配由 LLM 决定；否则回退为 `LLM_REVIEW_REQUIRED: {method_text}` 占位 |
| `shared_assumption_candidates` | 共同假设候选，每个包含 `assumption`、`why_questionable`、`supporting_papers`。当 LLM 提供 `llm_insights.shared_assumptions` 时直接使用 LLM 分析；否则从 note 的 Limitations/Gaps 段落提取 review hint |
| `mechanism_claim_clusters` | 机械聚合的机制 claim cluster hint（见下文） |
| `domain_consensus` | 兼容旧代码的 alias；语义同 `mechanism_claim_clusters`，不要当成已验证领域共识 |
| `metric_landscape_hints` | 指标/效率上下文 hint，不是 opportunity map；T4 机会生成应优先消费 contribution_space 和 tensions |
| `contribution_space` | CDR 贡献空间 hint：按 contribution_type、artifact 类型、design_rationale snippets 组织，snippet 会带 `evidence_level/allowed_use`，供 LLM 复核 |
| `cross_paper_tensions` | 跨论文矛盾/设计论证竞争素材；是 T4 Pass 1 的生成燃料，不是 provenance gate |
| `citation_graph_context` | T2 domain_map 的引用图上下文，包含 citation_edges、core/theory_bridge/adjacent/boundary ID 和 warnings；是客观骨架 hint，不是最终综述结构 |
| `domain_map_bucket_summary` | core/theory_bridge/adjacent/boundary 数量和 edge_count，供 LLM 判断语料是否有足够邻接覆盖 |
| `adjacent_transfers` | 从 `domain_map.adjacent` 与 note A/B 字段生成的邻接迁移 seed，每项含 mechanism/source_papers/why_unused_in_target/transfer_hypothesis_hint/evidence_level/allowed_use；必须由 LLM 复核，不是成型 idea |
| `bridge_transfer_drafts` | 从 `domain_map.theory_bridge` 生成的半成型交叉 seed，每项含 `bridge_id`、`bridge_name`、`transferable_mechanism`、`how_it_maps_to_project`、`why_potentially_novel`、`risk`、`evidence_level/allowed_use`；这是 T4 的 idea fuel，不是最终 idea |
| `trend_candidates` | 技术趋势候选。当 LLM 提供 `llm_insights.trends` 时直接使用；否则回退为 chronological evidence hint |
| `research_question_candidates` | 可操作研究问题候选。当 LLM 提供 `llm_insights.research_questions` 时直接使用；否则从 note 的 questions/gaps 段落提取 review hint |
| `notes` | 原始 note 解析结果 |

### 机制 Claim Clusters（不是最终领域共识）

`mechanism_claim_clusters` 字段聚合多篇论文中相似的 mechanism claim，用于给 LLM 提示“这里可能有可复核的机制主张”。旧产物可能仍有 `domain_consensus` 字段，但当前语义不是“工具已经判断出的领域共识”。

**构建逻辑**：
1. 从所有 note（含 abstract-only）中提取 §13 Mechanism Claim 的 `stated_mechanism`
2. 按关键词相似度（`_mechanism_similar`）聚类相似 mechanism
3. 对每个聚类计算：
   - `paper_count`：支持该机制的论文数
   - `evidence_types`：各 note 的 evidence type 原值
   - `evidence_strength_hint = llm_review_required`
   - `has_untested_claims`：是否存在 `claimed_untested` 或 `empirical_correlation`
   - `challengeable_hint`：弱证据或单篇 cluster 的复核提示，不是最终判断
   - `abstract_only_count`：其中有多少篇来自 abstract-only 证据
   - `requires_llm_judgment = true`
4. 按 `challengeable_hint` 优先、`paper_count` 降序排列，最多保留 10 个

**下游用途**：
- T4 【机制质疑型】补充候选优先查看 `challengeable_hint: true` 的 cluster，但必须由 LLM 复核它是否真是领域共识、证据是否弱、是否值得做 falsification/disambiguation
- T4.5 新颖性审计会用 `extract_mechanism_tuple` 和 `compare_mechanism_tuples` 进一步分析

### 邻接领域可迁移机制

`adjacent_transfers` 是 T3.5 新增的关键中间产物。它来自：

- `domain_map.adjacent`：T2 从 query bucket、snowball、related works 和引用连接得到的邻接候选
- `paper_notes/*.md` / `paper_notes_abstract/*.md` 的 `## A. 核心做法/视角`
- `## B. 桥接点`

每个条目通常包含：

- `mechanism`
- `source_field`
- `source_papers`
- `bridges_to_core`
- `why_unused_in_target`
- `transfer_hypothesis_hint`
- `evidence_level`

这不是 provenance gate，也不是“工具生成的 idea”。它只是把邻接领域的可迁移机制先摆到桌面上，供 T4 的 `cross_domain_analogy` 和 T8 Related Work 的差异化定位使用。若语料没有足够邻接论文，`synthesis.md` 应明确写 `current corpus has limited adjacent-domain coverage`，而不是硬编迁移机制。

### 为什么它重要

`T4` 不应该直接从几十篇 note 发散 idea。

更合理的方式是：

- 先形成领域结构化理解
- 再基于综合后的 gap 发散研究假设

### T3.5 的成功标准

当前 validator 关心的不是”有没有生成一个文件”这么简单，而是：

- `literature/synthesis.md` 必须存在
- `synthesis_workbench.json`、`synthesis_outline.md`、`synthesis_draft.md` 应随 T3.5 一起产出
- `synthesis.md` 最短 2000 字符（`reader.py` line 356）
- 必须覆盖约定的核心章节（方法家族、共同假设、贡献空间地图、跨论文矛盾/张力、邻接领域可迁移机制、技术趋势、可操作研究问题）
- 如果存在 `domain_map.json` 或 workbench 中有 citation graph 上下文，`synthesis.md` 必须有“邻接领域可迁移机制”相关章节；`synthesis_workbench.json` 必须含 `adjacent_transfers` 数组
- 结果要足够支撑后续 `T4`、`T6`、`T8`

### T3.5 的 Orchestrator 预构建 Hook

在 T3.5 正式启动 LLM 之前，orchestrator 的 `_maybe_prepare_t35_before_llm`（`orchestrator.py` line 743）会检查是否可以预构建 workbench：

**触发条件**：
- 当前任务是 T3.5
- `paper_notes/` 目录存在且有 note
- `synthesis_workbench.json` 尚不存在，或其时间戳早于最新 note

**行为**：
- 调用 `BuildSynthesisWorkbenchTool.execute(write_final=false, render_draft=true, llm_insights=None)`
- 不传 `llm_insights`，所以 workbench 中的方法家族、假设、趋势、问题全部回退为 `LLM_REVIEW_REQUIRED` 占位
- 写出 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`

**目的**：
- 让 T3.5 的 LLM 启动时就有结构化证据脚手架，而不是从零开始
- LLM 仍然会运行完整的 3 阶段流程（分析 → 调用工具传入 insights → 审阅写作）
- 如果 LLM 重新调用 `build_synthesis_workbench` 并传入 `llm_insights`，会覆盖预构建产物
- 如果 LLM 直接审阅预构建产物并写 `synthesis.md`，也是合法路径

**注意**：预构建产物不含 LLM 洞察，所以方法家族名称可能是 `LLM_REVIEW_REQUIRED: ...`，共同假设/趋势/问题可能只是从 note 文本提取的 review hint。LLM 必须审阅并修正这些占位结论。

### 单独运行示例

```bash
cd ResearchOS
researchos run-task T3.5 --workspace ./workspace/local-test2
```

### 恢复语义

T3.5 没有像 T3/T5/T7 那样复杂的专门恢复文件，但它天然是 artifact-first 的：

- 只要 `paper_notes/`、`comparison_table.csv` 还在
- 重跑 T3.5 的成本主要是重新综合
- staged workbench 让重跑可以从结构化证据、outline 或 draft 开始检查；如果 `synthesis_workbench.json`、`synthesis_outline.md`、`synthesis_draft.md` 都存在且不早于最新 note，runtime 会复用它们，不再重复调用 workbench 工具
- 不需要重新跑 T2/T3 才能继续

---

## 6.6 T3.6：SurveyWriterAgent（optional survey branch）

### 角色

- Agent：`SurveyWriterAgent`
- 代码： [researchos/agents/survey_writer.py](../researchos/agents/survey_writer.py)
- Prompt： [researchos/prompts/survey_writer.j2](../researchos/prompts/survey_writer.j2)
- Tools： [researchos/tools/survey_tools.py](../researchos/tools/survey_tools.py)
- 状态机：`T3.6-GATE-SURVEY` 到 `T3.6-FEED`

T3.6 是 T3.5 后的可选综述论文支线。它的核心前提是：

`synthesis.md` 不是 survey paper 草稿。`synthesis.md` 服务 T4 idea generation，组织逻辑是 design rationale 谱系、cross-paper tensions 和 adjacent transfers；survey paper 服务读者，组织逻辑是 taxonomy、领域演进、系统比较、open challenges 和 future directions。

因此 T3.6 不允许把 `synthesis.md` 直接转成 TeX。它必须重新规划 taxonomy，并按 section-by-section 写作。

### 主流程

```text
T3.5
 -> T3.6-GATE-SURVEY
    -> no: T4
    -> yes:
       T3.6-PLAN
       -> T3.6-GATE-OUTLINE
       -> T3.6-GATE-CORPUS
          -> complete: T3.6-EXPAND
          -> conservative: T3.6-STATE
       -> T3.6-STATE
       -> T3.6-SEC-BACKGROUND
       -> T3.6-SEC-TAXONOMY
       -> T3.6-SEC-THEME-1
       -> T3.6-SEC-THEME-2
       -> T3.6-SEC-THEME-3
       -> T3.6-SEC-THEME-4
       -> T3.6-SEC-COMPARISON
       -> T3.6-SEC-CHALLENGES
       -> T3.6-SEC-FUTURE
       -> T3.6-SEC-INTRO
       -> T3.6-SEC-CONCLUSION
       -> T3.6-SEC-ABSTRACT
       -> T3.6-ASSEMBLE
       -> T3.6-REVIEW
       -> T3.6-COMPILE
       -> T3.6-FEED
       -> T4
```

### 输入文件

| 输入 | 文件 | 用途 |
| --- | --- | --- |
| `project` | `project.yaml` | 研究方向、target venue、约束 |
| `synthesis` | `literature/synthesis.md` | idea fuel 和领域综合，不作为 survey 正文模板 |
| `synthesis_workbench` | `literature/synthesis_workbench.json` | contribution_space、cross_paper_tensions、adjacent_transfers、bridge_transfer_drafts、mechanism clusters |
| `domain_map` | `literature/domain_map.json` | core/theory_bridge/adjacent/boundary 与 citation_edges，用于 taxonomy/evolution |
| `comparison_table` | `literature/comparison_table.csv` | 横向比较和 comparative analysis 证据 |
| `paper_notes_dir` | `literature/paper_notes/` | deep-read 证据 |
| `paper_notes_abstract_dir` | `literature/paper_notes_abstract/` | abstract-only 桥接提示，不能当 FULL-TEXT 证据 |
| `related_work_bib` | `literature/related_work.bib` | survey section 引用 key 来源 |
| `seed_outline_profile` | `user_seeds/seed_outline_profile.json` | 用户综述提纲规范化 profile；提供 taxonomy/scope/query 先验，不是 citation 来源 |
| `seed_external_resources` | `user_seeds/seed_external_resources.jsonl` | 法规、标准、治理框架、数据集或 repo 等外部资源线索；正文引用前需验证来源 |

### 输出文件

| 输出 | 文件 | 含义 |
| --- | --- | --- |
| `survey_decision` | `drafts/survey/decision.json` | 用户是否撰写 survey |
| `survey_plan` | `drafts/survey/survey_plan.json` | LLM 规划的 taxonomy、evolution narrative、sectioning_policy、outline、coverage selfcheck |
| `outline_decision` | `drafts/survey/outline_decision.json` | 用户确认/调整 taxonomy 大纲的记录 |
| `corpus_decision` | `drafts/survey/corpus_decision.json` | 用户选择 conservative / complete 素材范围 |
| `survey_expansion` | `drafts/survey/survey_expansion.json` | complete 模式的一次性补检计划，不回到 T2/T4 循环 |
| `survey_state` | `drafts/survey/survey_state.json` | 逐 section 写作共享状态 |
| `survey_section_outlines_dir` | `drafts/survey/section_outlines/*.md` | 每节局部作业单 |
| `sections` | `drafts/survey/sections/*.tex` | 每个 section 的独立 TeX 文件 |
| `survey_tex` | `drafts/survey/survey.tex` | 拼装后的综述 LaTeX |
| `references_bib` | `drafts/survey/references.bib` | 从 `related_work.bib` 复制来的编译引用库 |
| `survey_audit` | `drafts/survey/survey_audit.md/json` | taxonomy/section/citation/placeholder 机械审计 |
| `survey_review` | `drafts/survey/survey_review.md` | LLM 综述模式审阅：taxonomy、覆盖、公允性、challenges、future、scope/craft |
| `survey_review_actions` | `drafts/survey/survey_review_actions.json` | 审阅后的 section action list，记录是否仍有 blocking issue |
| `survey_pdf` | `drafts/survey/survey.pdf` | 编译出的 PDF |
| `survey_compile_report` | `drafts/survey/survey_compile_report.json` | LaTeX 编译报告 |
| `survey_insights` | `ideation/survey_insights.json` | 导出给 T4 的 taxonomy/challenge/future idea fuel |

### 各节点如何执行

#### `T3.6-GATE-SURVEY`

这是状态机级 `immediate_gate`，runtime 直接展示 `t36_survey_gate`，不会启动 `SurveyWriterAgent` 或消耗 LLM。问题是“是否额外撰写 taxonomy-driven professional survey paper”。用户选 no 时 runtime 写：

```json
{"write_survey": false, "selected_option": "no", "note": "skip survey branch and continue T4"}
```

状态机根据 gate 选项直接进入 T4 或 `T3.6-PLAN`，同时持久化 `drafts/survey/decision.json`。如果运行环境没有输入，runtime 会停在 `WAITING_HUMAN/PAUSED`，resume 后仍回到该 gate；缺失或损坏的 `decision.json` 不再被解释为默认 skip，而是路由回 `T3.6-GATE-SURVEY`。

#### `T3.6-PLAN`

LLM 读取 `synthesis.md`、`synthesis_workbench.json`、`domain_map.json`、`comparison_table.csv`、`paper_notes/`、`paper_notes_abstract/`、`.bib` 和可选 `seed_outline_profile.json`。它要选择 taxonomy 主轴，构建 2-4 层 taxonomy tree，给每个 class 绑定 paper IDs，并写 evolution narrative。

这里需要 LLM 学术判断，不能由 tool 硬编码 taxonomy。`domain_map` 只提供 citation/evolution hint，`adjacent_transfers` 只提供邻接迁移素材，不能被当成最终分类结论。

如果存在 seed outline profile，`framework`、`sections`、`query_profile` 和
`manuscript_type=survey` 是强先验：例如“四类视角 × 风险生成链条”应作为默认 taxonomy
候选，除非已读文献证据显示需要调整。profile 中的代表性文献方向未被 T2/T3 具体论文覆盖时，
必须写入 `coverage_selfcheck.classes_needing_more_lit` 或 `resource_upgrade_needs`，不得伪造
paper id 或 citation key。

输出 `survey_plan.json` 至少包含：

- `taxonomy.dimension`
- `taxonomy.rationale`
- `taxonomy.tree`
- `evolution_narrative`
- `sectioning_policy`
- `outline`
- `coverage_selfcheck`

`sectioning_policy` 是写作前置契约，不是 review 后补救项。默认必须是 compact：taxonomy 类、风险链条、治理视角或机制家族写进 `Taxonomy` 与 `Comparative Analysis` 的小节/段落，而不是为每个类膨胀出独立大章。只有用户明确要求长篇综述，且某个主题无法自然并入 taxonomy/comparison 时，才允许 `standalone_theme_sections`，并且必须写明 `rationale` 和很小的 `max_theme_sections`。

validator 会要求 taxonomy tree 非空、outline 至少包含 background/taxonomy/comparison 等核心 section、coverage_selfcheck 存在，并在 PLAN 阶段拒绝缺失 `sectioning_policy` 或 compact 模式下仍输出 `theme_*` 独立章的 plan。这样章节结构问题会在正文写作前暴露，而不是等到 review/compile 后才发现。

#### `T3.6-GATE-OUTLINE`

LLM 读取 `survey_plan.json`，把 taxonomy tree、outline、unclassified_papers、empty_classes 和 corpus_sufficiency 展示给用户。用户可以 approve 或 adjust。

如果 approve，只写 `outline_decision.json`，不改 plan。如果 adjust，LLM 根据用户意见就地修订 `survey_plan.json` 的相关 taxonomy/outline，并把 `user_adjustments` 和 `applied_adjustments` 记录下来。这里不重跑整个 PLAN，避免在 taxonomy gate 上无限循环。

#### `T3.6-GATE-CORPUS`

这是状态机级 `immediate_gate`，runtime 直接询问用户素材范围并写 `drafts/survey/corpus_decision.json`，不会启动 LLM：

- `conservative`：只用现有 T2/T3/T3.5 语料，速度快；Scope 中必须诚实声明覆盖边界。
- `complete`：触发一次性定向补检计划，主要补 taxonomy 空类或弱类。

状态机按 gate 选项跳转：`complete` 进入 `T3.6-EXPAND`，`conservative` 进入 `T3.6-STATE`。resume 时如果 `corpus_decision.json` 缺失或损坏，会回到 `T3.6-GATE-CORPUS`，不会静默走 conservative。

#### `T3.6-EXPAND`

Agent 调用 `expand_corpus_for_survey`。这个工具读取 `survey_plan.json`、`domain_map.json` 和 `papers_verified.jsonl`，为 `coverage_selfcheck.classes_needing_more_lit` / `empty_classes` 生成 query hints，输出 `survey_expansion.json`。

这一步不是 T4 -> T2 回路，也不自动宣称“领域缺口”。它只是一次性组织补检计划：哪些 taxonomy class 需要更多文献、建议查什么关键词、哪些 neighbor 只能作为邻接提示。LLM 可以在工具输出后补 `llm_review`，但不能循环补检。

#### `T3.6-STATE`

Agent 调用 `build_survey_state`。工具把 `survey_plan.json` 机械转换为：

- `survey_state.json`
- `section_outlines/background.md`
- `section_outlines/taxonomy.md`
- `section_outlines/theme_1.md` 到 `theme_4.md`（兼容占位；compact 默认全部 skipped）
- `section_outlines/comparison.md`
- `section_outlines/challenges.md`
- `section_outlines/future.md`
- `section_outlines/introduction.md`
- `section_outlines/conclusion.md`
- `section_outlines/abstract.md`

默认 compact 模式会把 `theme_1` 到 `theme_4` 都标记为 `skipped`，并在 taxonomy/comparison 的 section outline 中写明“taxonomy 类写入本节内部”的规则。`T3.6-SEC-THEME-*` 节点仍保留是为了兼容旧状态机和显式长综述模式；如果 `survey_state` 标记 skipped，该节点只调用 `update_survey_section_state(..., status="skipped")` 后结束，不写正文。

如果 `survey_plan.sectioning_policy.mode=standalone_theme_sections`，工具才会把少量 theme outline 映射到固定槽位；超过 `max_theme_sections` 会失败，要求回到 PLAN/outline gate 合并或删减章节。

#### `T3.6-SEC-*`

每个 `survey_section` 节点只写一个文件：

- `T3.6-SEC-BACKGROUND` -> `drafts/survey/sections/background.tex`
- `T3.6-SEC-TAXONOMY` -> `drafts/survey/sections/taxonomy.tex`
- `T3.6-SEC-THEME-1` -> `drafts/survey/sections/theme_1.tex`（默认 skipped）
- `T3.6-SEC-THEME-2` -> `drafts/survey/sections/theme_2.tex`（默认 skipped）
- `T3.6-SEC-THEME-3` -> `drafts/survey/sections/theme_3.tex`（默认 skipped）
- `T3.6-SEC-THEME-4` -> `drafts/survey/sections/theme_4.tex`（默认 skipped）
- `T3.6-SEC-COMPARISON` -> `drafts/survey/sections/comparison.tex`
- `T3.6-SEC-CHALLENGES` -> `drafts/survey/sections/challenges.tex`
- `T3.6-SEC-FUTURE` -> `drafts/survey/sections/future.tex`
- `T3.6-SEC-INTRO` -> `drafts/survey/sections/introduction.tex`
- `T3.6-SEC-CONCLUSION` -> `drafts/survey/sections/conclusion.tex`
- `T3.6-SEC-ABSTRACT` -> `drafts/survey/sections/abstract.tex`

每次调用输入只包含 `survey_state.json`、当前 `section_outline`、该节需要的证据文件和必要的相邻 section。Writer 不允许生成 `\documentclass`、`\begin{document}` 或其它 section 标题。`abstract.tex` 是摘要源片段，只能包含摘要纯正文，不能写 `\section{Abstract}`、`\section*{Abstract}`、`\begin{abstract}` 或 `\end{abstract}`；`assemble_survey` 会负责放入 abstract 环境。写完后必须调用 `update_survey_section_state(section_id=..., status="written")`。

章节顺序有意安排为：background/taxonomy/theme/comparison/challenges/future 先确定事实密集内容，再写 introduction、conclusion、abstract。这样 abstract 和 introduction 不会在方法、比较和挑战尚未稳定时先行编造。

#### `T3.6-ASSEMBLE`

Agent 调用：

```text
assemble_survey(...)
audit_survey_coverage(...)
```

`assemble_survey` 机械拼接 `survey_state.write_order` 中的 section 文件，写 `drafts/survey/survey.tex`，并把 `literature/related_work.bib` 复制为 `drafts/survey/references.bib`，保证 `\bibliography{references}` 可编译。

`audit_survey_coverage` 做确定性检查：

- 是否有 taxonomy section
- 是否有 comparative analysis
- 是否有 open challenges
- 是否有 future directions
- active sections 是否全部 written/revised
- 是否仍有 TODO/TBD/LLM_REVIEW_REQUIRED/PLACEHOLDER
- citation keys 是否存在于 bib

审计只检查机械一致性，不评价 taxonomy 学术质量。

#### `T3.6-REVIEW`

`SurveyWriterAgent(mode=survey_review)` 读取 `survey.tex`、`survey_audit.md/json`、`survey_plan.json`、`survey_state.json`、所有 `sections/*.tex`、`synthesis_workbench.json`、`domain_map.json`、`comparison_table.csv` 和 `.bib`。这一步用 LLM 的学术判断做综述模式审阅，不把 taxonomy 质量硬编码成 tool 规则。章节结构、内部标号和弱证据滥用已经在 PLAN/SECTION/ASSEMBLE 前置拦截；review 只做最后一轮人工风格的结构和学术质量兜底。

审阅维度固定为六类：

- Taxonomy 合理性：分类维度是否能组织领域，是否 MECE，是否明显错分/空类。
- Coverage 完整性：是否覆盖 plan 中的核心类、代表论文、相邻/边界区域；abstract-only 证据是否诚实标注。
- Comparative fairness：横向比较是否公允，是否比较不可比 setting，是否夸大某类方法。
- Challenges 质量：open challenges 是否来自真实 tensions/gaps，而不是空泛套话。
- Future directions 质量：future 是否具体、可操作，并合理使用 adjacent_transfers / boundary hints。
- Scope and craft：conservative 模式是否声明覆盖边界；是否有流水账、placeholder、引用不可信或 AI 套话。

如果发现 blocking issue，Agent 只修对应 section 文件，例如 `drafts/survey/sections/comparison.tex` 或 `future.tex`，调用 `update_survey_section_state(..., status="revised")` 记录修订，再重新调用 `assemble_survey` 与 `audit_survey_coverage`。它不能整篇重写 `survey.tex`。

输出：

- `drafts/survey/survey_review.md`，必须包含 `Taxonomy Review`、`Coverage Review`、`Comparative Fairness Review`、`Challenges Review`、`Future Directions Review`、`Scope And Craft Review`、`Remaining Risks`。
- `drafts/survey/survey_review_actions.json`，语义为 `llm_survey_review_and_section_revision_plan`，`review_target=taxonomy_driven_survey`，`blocking_issues_remaining=false` 才能进入编译。

#### `T3.6-COMPILE`

Agent 调用：

```text
latex_compile(tex_path="drafts/survey/survey.tex", engine="pdflatex", bibtex=true)
```

`latex_compile` 会自动把编译报告落盘到 `drafts/survey/survey_compile_report.json`。Agent 不需要、也不允许手抄 `data.compile_report` 来伪造进度。validator 会同时检查 `survey.pdf`、`survey.log` 和 `survey_compile_report.json` 的 `semantics`、`tex_path`、`success=true`、`main_tex_sha256`、`pdf_sha256`、`log_sha256`、`pdf_mtime` 和 dependency fingerprint，因此“只有 PDF、没有 report”或“references/section 改了但复用旧 PDF”的旧产物不会通过。进入 compile 前还会校验当前 `survey_audit.json`，编译 log 中仍有 undefined citation/reference、fatal error 或 undefined control sequence 时也会失败。

如果当前环境缺少 TeX/Docker，`latex_compile` 会返回 `waiting_environment_*`，runtime 会暂停；修复环境后可 resume。如果是 citation 或 LaTeX 语法错误，应读 log 后修对应 section，再重新 assemble/compile，而不是一口气重写整篇 survey。

#### `T3.6-FEED`

Agent 调用 `export_survey_for_ideation`，导出：

- `ideation/survey_insights.json`
- `drafts/survey/survey_summary.md`

`survey_insights.json` 的语义是 `survey_insights_optional_ideation_fuel_not_gate`。T4 可以读取 taxonomy、challenge_hints、future_direction_hints，生成 `idea_origin=survey_driven` 候选或补强主线推理；`resource_upgrade_needs` 会从 `survey_plan.json` 和 `survey_state.shared_facts` 合并规范化导出，用于提醒 T4 哪些 abstract-only / metadata-only 材料只能作为补资源或升级阅读任务，不能作为 selected hypothesis 的强证据。但 survey insights 不是 gate，不强迫 T4 只按 survey 生成 idea。T4 validator 允许 weak-only 候选可见上桌并标 `constraint_status=not_supported_by_current_evidence`，但禁止这类候选被 `selected` 或绑定最终 `hypothesis_refs`。

### 续跑语义

T3.6 是 artifact-first 支线。每个 section 都是单独文件，`survey_state.json` 记录每节 status。中断后 resume 时：

- 如果 `decision.json` 已存在，survey gate 可直接完成。
- 如果 `survey_plan.json` 已存在，PLAN 不必重写。
- 如果某个 section 已写且 `survey_state` 标记 written/revised，validator 会接受，后续节点继续。
- compact 默认下 `theme_1` 到 `theme_4` 都是 skipped，section validator 不要求对应 tex 文件。
- 如果 review 失败，resume 会回到 `T3.6-REVIEW`，读取 `survey_review.md` 和 `survey_review_actions.json` 定位 section patch，不会重写整篇 survey。
- 如果 `survey.tex` 已拼装且 review 通过但 compile 失败，resume 会回到 `T3.6-COMPILE` 或当前状态，读取 log 修复。`survey_review_actions.json` 必须由 `bind_survey_review` 写入当前 `survey_plan`、`survey_state`、`survey.tex`、`survey_audit`、sections 和 literature 输入的 fingerprint；旧 review 不会放行新 survey。

### 单独运行示例

```bash
cd ResearchOS
researchos run-task T3.6 --workspace ./workspace/local-test2
researchos run-task T3.6-GATE-SURVEY --workspace ./workspace/local-test2
researchos run-task T3.6-PLAN --workspace ./workspace/local-test2
researchos run-task T3.6-SEC-TAXONOMY --workspace ./workspace/local-test2
researchos run-task T3.6-ASSEMBLE --workspace ./workspace/local-test2
researchos run-task T3.6-REVIEW --workspace ./workspace/local-test2
researchos run-task T3.6-COMPILE --workspace ./workspace/local-test2
```

完整 pipeline 不需要手动跑这些节点；如果用户在 Gate-1 选 no，会直接进入 T4。

## 6.7 T4：IdeationAgent

### 角色

- Agent：`IdeationAgent`
- 代码： [researchos/agents/ideation.py](../researchos/agents/ideation.py)
- Prompt： [researchos/prompts/ideation.j2](../researchos/prompts/ideation.j2)

### 当前默认配置

- model tier：`heavy`
- temperature：`0.75`
- 主要工具：
  - `read_file`
  - `write_file`
  - `write_structured_file`
  - `list_files`
  - `analyze_idea_concentration`
  - `compute_idea_novelty_signal`
  - `ask_human`
  - `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 方向、预算、硬件等约束 |
| `synthesis` | `literature/synthesis.md` | 是 | T3.5 的综合结论 |
| `comparison_table` | `literature/comparison_table.csv` | 否但强烈推荐 | 现有方法和基线结构 |
| `missing_areas` | `literature/missing_areas.md` | 否 | T2 的检索覆盖提示 |
| `domain_map` | `literature/domain_map.json` | 是 | T2 引用图领域地图，用于 novelty 近邻软信号 |
| `synthesis_workbench` | `literature/synthesis_workbench.json` | 是 | T3.5 结构化 workbench，含 `contribution_space`、`cross_paper_tensions`、`adjacent_transfers` |
| `survey_insights` | `ideation/survey_insights.json` | 否 | T3.6 综述支线导出的 taxonomy / challenges / future directions；额外 idea fuel，不是 gate |
| `seed_ideas` | `user_seeds/seed_ideas.md` | 否 | 用户已有想法 |
| `seed_constraints` | `user_seeds/seed_constraints.md` | 否 | 额外约束 |

`domain_map` 与 `synthesis_workbench` 是 T4 单任务强前置。T4 仍然由 LLM 负责 idea 生成和学术判断，但它必须看到 citation-graph novelty signal、contribution space、cross-paper tensions 和 adjacent transfers；缺这些文件时，单独运行 T4 会先失败并提示补齐上游产物，而不是退化成只读 `synthesis.md` 的一次性 prompt。

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `hypotheses` | `ideation/hypotheses.md` | 3-6 个假设及其锚点 |
| `exp_plan` | `ideation/exp_plan.yaml` | 实验计划，后续 `T5-HANDOFF` 会编译成外部执行契约；legacy T5/T7 显式调试时才直接执行 |
| `idea_scorecard` | `ideation/idea_scorecard.yaml` | 所有候选 idea 的来源、核心内容、评分、baseline、决策、风险和最低实验 |
| `rejected_ideas` | `ideation/rejected_ideas.md` | 被淘汰/暂缓/合并 idea 的人类可读原因 |
| `gate_decisions` | `ideation/gate_decisions.json` | Gate1/Gate2 的用户反馈、选择/淘汰和决策理由 |
| `idea_rationales` | `ideation/idea_rationales.json` | 每个 idea / hypothesis 的生成依据和来源追踪 |
| `risks` | `ideation/risks.md` | 风险评估 |
| `family_distribution` | `ideation/_family_distribution.md` | mechanism family 分布统计 |
| `pass1_forward_candidates` | `ideation/_pass1_forward_candidates.json` | Pass 1 原始发散候选池；包含主线和补充候选，不能被 Pass 2 覆盖或删减 |
| `pass2_grounding_review` | `ideation/_pass2_grounding_review.json` | Pass 2 文献接地/风险审阅；只标 `proceed/revise/defer/reject_recommended`，不删除候选 |
| `candidate_directions` | `ideation/_candidate_directions.json` | Gate1 可见候选池；必须覆盖 Pass 1 全部候选并附上 Pass 2 风险 |
| `gate1_selection_brief` | `ideation/_gate1_selection_brief.md` | 给用户看的 Gate1 全量选择简报，包含候选、风险、重构和合并建议 |
| - | `ideation/bridge_coverage_review.json` | 条件输出；仅当 T1 已确认非空 bridge domain plan 时必需，用于记录 bridge 候选上桌、裁决和 escape hatch |
| - | `ideation/_premortem.md` | pre-mortem 质疑结果 |

### T4 的流程不是“一次生成完”

当前 prompt 明确把它设计成了多阶段思考：

1. 读取 `synthesis.md`
2. 生成 `3-5` 个主线候选方向：synthesis gestalt / problem reframing / design-rationale derivation / cross-domain analogy / free reasoning / seed refinement / evidence-driven
3. 再用四类补充通道做 coverage supplement；有证据才生成候选，没有证据则记录 unsupported
4. 写 `ideation/_pass1_forward_candidates.json` 保存原始发散结果，包含不推荐、高风险、routine risk 或证据不足候选
5. 做 Pass 2 文献接地，把每个候选标为 `proceed` / `revise_before_selection` / `defer_recommended` / `reject_recommended`，写 `ideation/_pass2_grounding_review.json`
6. 写 `ideation/_candidate_directions.json` 和 `_gate1_selection_brief.md`，保证所有 Pass 1 候选都对用户可见
7. 对每个候选写七维评分、机制、prediction、counterfactual、最低实验和 kill criteria
8. runtime 在候选池就绪时把 T4 标记为 `t4_gate1_ready`，跳到 `T4-GATE1` 状态机级 immediate gate；用户选择、重构、合并多个候选、新想法或重新分析的结果写入 `ideation/_gate1_user_selection.json`
9. T4 resume 读取 `_gate1_user_selection.json`，再对选定方向做 pre-mortem
10. 写 `_family_distribution.md` 统计 mechanism family 分布
11. 最终产出：
   - `hypotheses.md`
   - `exp_plan.yaml`
   - `idea_scorecard.yaml`
   - `rejected_ideas.md`
   - `gate_decisions.json`
   - `idea_rationales.json`
   - `risks.md`
   - 条件产物：如果 T1 已确认非空 bridge domain plan，还必须写 `bridge_coverage_review.json` 记录 bridge 候选是否上桌、是否进入 hypotheses，以及缺素材时的 escape hatch；如果用户选择“不交叉/全部跳过”，该文件可以不存在。

### 实际执行过程

`IdeationAgent` 的执行分为两个阶段（Gate1 + Gate2），中间有 pre-mortem 检查。

#### 阶段A：发散候选方向 + Gate1

1. **读取输入**：`project.yaml`、`literature/synthesis.md`、`literature/comparison_table.csv`、`literature/missing_areas.md`、`ideation/survey_insights.json`（如果 T3.6 写过 survey）、`user_seeds/seed_ideas.md`、`user_seeds/seed_constraints.md`

2. **深度分析 synthesis**：用 `read_file` 读取完整的 `literature/synthesis.md`，理解 Q1-QN、方法家族、共同假设、贡献空间地图、跨论文矛盾/张力、趋势和可操作问题

3. **读取 workbench 和 domain map**：用 `read_file` 读取 `literature/synthesis_workbench.json`，获取 `method_families`、`mechanism_claim_clusters`、`contribution_space`、`cross_paper_tensions`、`adjacent_transfers` 和 `bridge_transfer_drafts`（旧产物可能叫 `domain_consensus`）。再读取 `literature/domain_map.json`，把 citation graph 近邻和 `theory_bridge` 作为 novelty/transfer 软信号来源。这些都是工具 hint，T4 必须先复核，不能直接当领域共识、研究缺口或已成立 idea。

4. **生成候选方向**：
   - 主线候选：先生成 3-5 个，来源包括 LLM 直接综述推理、用户 seed idea 的修订/挑战、comparison table / missing coverage / paper notes 的 evidence-driven 观察
   - 补充候选：再逐类检查【机制质疑、反向操作、子群失败、缺口探索】四个通道；有证据则生成补充候选，无证据则写 `constraint_status: not_supported_by_current_evidence` 并用新的 evidence-driven 候选替代
   - 最终候选池建议 7-9 个，且主线候选不能被四类补充候选覆盖掉

   每个方向必须包含：
   - `id`、`title`、`pitch`（一句话）
   - `core_claim`、`target_problem`、`mechanism`（因果机制）、`prediction`（可验证预测）、`counterfactual`（反事实检验）、`mechanism_family`（1-3 词标签）
   - CDR tuple：`problem_frame`、`design_rationale`、`artifact`、`data_view`、`evaluation_mode`、`contribution_type`、`boundary_conditions`
   - `corresponds_to`（可以对应 synthesis 中的 Q 编号，也可以是 `none/new_frame`，不强制贴回单个问题）
   - `basis_summary`、`basis_sources`（自由 provenance / reasoning 记录，不作为质量门）
   - `idea_origin`（如 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`evidence_driven`、`bridge_synthesis`、`mechanism_challenge`）
   - `constraint_status`（`mainline` / `supplement` / `bridge` / `not_supported_by_current_evidence`）
   - `closest_baselines`（可空；无相近工作时 `prior_art: none` 是合法高新颖信号，但要标风险）
   - `cross_domain_sources` / `cross_domain_relation`（如果使用 `bridge_transfer_drafts`、`domain_map.theory_bridge`、`paper_notes_bridge/` 或 bridge-domain plan 作为类比素材，必须记录对应 `bridge_id` 数组和关系类型；未使用时为空数组；旧 `cross_domain_source` 单数只作兼容 alias）
   - `counterfactual_check`、`nearest_prior_work`、`novelty_signal` 三个软诊断字段；材料不足时可显式写 `insufficient_evidence`、`not_computed` 或 `domain_map_unavailable`
   - `scores`（七维评分：novelty/feasibility/impact/evaluability/differentiation/cost/contribution_strength）
   - `minimum_experiment`（最低可行实验）
   - `key_risks`（风险和 kill criteria）
   - `source.seed_alignment`（与用户 seed 的对齐程度：`direct`/`partial`/`none`）

5. **写 `ideation/_pass1_forward_candidates.json`**：这是 Pass 1 原始候选池，必须保存全部主线候选和补充候选。T4 不能因为候选高风险、routine risk、证据不足或自己不推荐就删除它；这些候选仍然要给用户看。

6. **Pass 2 文献接地 / 风险审阅**：对 Pass 1 的每个候选做 novelty、feasibility、provenance note 和 contribution check，写 `ideation/_pass2_grounding_review.json`。这里的结果是推荐和警告，不是删除列表：`reject_recommended` 只表示系统不推荐，用户仍可在 Gate1 选择并要求重构。Pass 2 还要为每个候选写三类软诊断：`counterfactual_check`（抽掉最依赖论文后 idea 是否仍成立）、`nearest_prior_work`（最近工作和距离）、`novelty_signal`（由 `compute_idea_novelty_signal` 基于 domain_map 给出的 citation-graph 近邻参考）。这些字段的“存在性”是硬校验，目的是防止 LLM 跳过审阅；字段取值不是质量 gate，且允许 `insufficient_evidence`、`not_computed`、`domain_map_unavailable` 这类不可用状态，要求写明原因，不能为过校验而编造最近工作或图谱信号。

7. **写 `_family_distribution.md`**：统计 mechanism family 分布，标注同 family 的候选。

8. **写 `ideation/_candidate_directions.json`**：这是 Gate1 可见候选池，必须覆盖 `_pass1_forward_candidates.json` 的所有候选，并为每个候选附上 `pass2_screening.visible_to_gate=true`、`screening_recommendation` 和 `selection_warning`。这个文件不能只保留推荐项。

9. **写 `ideation/_gate1_selection_brief.md`**：把所有候选、人类可读风险、Pass 2 推荐、可重构项和合并建议列出来，例如 `合并 D1+D3`。同时调用 `analyze_idea_concentration`，把集中度提示、Origin 分布和 Novelty-Utility 谱系排布写进 brief。这个文件用于 resume 和人工审阅，即使 `ask_human` 输出滚动过去也能回看。

10. **Gate1 暂停**：T4 不在同一次 LLM run 内继续写最终假设。runtime 校验 `_pass1_forward_candidates.json`、`_pass2_grounding_review.json`、`_candidate_directions.json`、`_gate1_selection_brief.md` 和条件性的 `bridge_coverage_review.json` 后，返回 `completion_mode=t4_gate1_ready` 并转入 `T4-GATE1`。

11. **T4-GATE1 用户裁决**：状态机直接展示候选方向、机制/反事实占位风险、Pass 2 不推荐理由、bridge 覆盖/逃生舱和合并建议。用户可选择、选择并重构、合并、补充或要求重新分析。选择结果写入 `ideation/_gate1_user_selection.json`，然后回到 `T4`。

12. **更新决策链**：T4 resume 读取 `_gate1_user_selection.json`，更新 `idea_scorecard.yaml`、`rejected_ideas.md`、`gate_decisions.json` 并继续生成最终 hypotheses/exp plan。

### Pass 1 / Pass 2 可见性与选择语义

T4 的两个阶段都必须可见：

- `ideation/_pass1_forward_candidates.json`：原始发散结果，不做删除。
- `ideation/_pass2_grounding_review.json`：接地审阅结果，只加风险标签和推荐动作。
- `ideation/_candidate_directions.json`：Gate1 真正展示的候选池，必须覆盖 Pass 1 全部 ID。
- `ideation/_gate1_selection_brief.md`：人类可读简报，必须提到每个候选 ID 和合并建议。

因此，被 Pass 2 标为 `reject_recommended`、`defer_recommended`、`revise_before_selection` 的候选不会消失。用户可以：

- 直接选择推荐候选，例如 `选择 D1`
- 选择一个高风险候选并要求重构，例如 `选择 D2 并重构`
- 合并多个候选，例如 `合并 D1+D3` 或 `合并 D1+S1`
- 提出新想法，例如 `新想法: ...`
- 要求重新分析

合并时不要覆盖原始候选。正确做法是新增合并后的 idea，例如 `M1`，并在 `idea_scorecard.yaml` 中保留：

- `M1.decision.status=selected`
- `M1.source.merged_from_idea_ids=["D1", "D3"]`
- `D1.decision.status=merged`、`D1.decision.merged_into="M1"`
- `D3.decision.status=merged`、`D3.decision.merged_into="M1"`

如果用户坚持选择 routine risk 或 `reject_recommended` 候选，T4 不能直接让 routine idea 进入 `hypotheses.md`。它必须先重构 CDR tuple、mechanism、prediction、counterfactual 和 contribution character；原始风险仍写入 `risks.md`、`gate_decisions.json` 和 `rejected_ideas.md`。

#### 阶段A.5：Pre-mortem 检查

用户选定方向后，从四个维度质疑：
- 物理/数学约束检查
- 已知反例检查
- 资源可行性检查
- Contribution character 检查：如果 idea 成立，领域会怎样不同；是 invention / improvement / exaptation 还是 routine；为什么不是 routine

结果写入 `ideation/_premortem.md`。如果存在 High 风险且无缓解方案，用 `ask_human` 提示用户。

#### 阶段B：展开假设与计划 + Gate2

1. **展开选定方向**：生成详细的研究假设和实验计划

2. **产出 7 个文件**：
   - `ideation/hypotheses.md`：研究假设（`## H1`、`## H2` 锚点）
   - `ideation/exp_plan.yaml`：实验计划（schema 校验）
   - `ideation/idea_scorecard.yaml`：所有候选 idea 的完整记录
   - `ideation/rejected_ideas.md`：被淘汰 idea 的可读原因
   - `ideation/gate_decisions.json`：两轮 Gate 的决策日志
   - `ideation/idea_rationales.json`：每个 idea 的机器可读依据
   - `ideation/risks.md`：Top 3 风险

3. **Gate2**：用 `ask_human` 让用户确认计划，允许修改假设/计划/风险

4. **完成**：用户确认后调用 `finish_task`

### Validator 检查项

- `hypotheses.md` 不能太短，必须有 `H1/H2/...` 锚点
- `exp_plan.yaml` 必须过 schema，`hypothesis_ref` 必须指向存在的 anchor
- `idea_scorecard.yaml` 必须过 schema，记录选中和淘汰/暂缓/合并的候选 idea；每个 idea 必须包含 `mechanism`、`prediction`、`counterfactual`、`mechanism_family` 四个非空字段
- `_pass1_forward_candidates.json` 必须存在，且至少包含 4 个原始候选；每个候选必须有稳定 ID 和 `idea_origin`
- `_pass2_grounding_review.json` 必须覆盖 Pass 1 全部候选；每个 review 必须 `visible_to_gate=true`，并给出 `screening_recommendation`
- `_candidate_directions.json` 顶层必须使用 `candidates`，不能使用旧字段 `directions`；每个候选必须有 `idea_origin`、`constraint_status` 和足够长的 `basis_summary`
- `_candidate_directions.json` 必须保留 Pass 1 全部候选，不能因为 Pass 2 标风险而删除；如果写了 `pass2_screening`，`visible_to_gate` 不能是 false
- `_candidate_directions.json`、`_pass1_forward_candidates.json` 和 `idea_scorecard.yaml.source` 如果填写 `cross_domain_sources`，必须同时填写合法 `cross_domain_relation`；`idea_origin=bridge_synthesis` 时 `cross_domain_sources` 必须非空。旧 `cross_domain_source` 单数字段只作兼容读取，不建议继续输出
- `_gate1_selection_brief.md` 必须提到所有候选 ID，并说明可合并多个候选，例如 `合并 D1+D3`
- `idea_scorecard.yaml` 必须记录 Pass 1 全部候选，不能只记录最后 selected 的 idea
- `idea_scorecard.yaml` 的每个 `source` 必须显式包含 `idea_origin` 和 `constraint_status`；origin mix 至少要包含 CDR schema 中的主线 origins（如 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`survey_driven`、`evidence_driven`），不能全部由四类补充候选构成
- `supporting_papers`、`closest_baselines`、`from_synthesis_section` 是 optional provenance 文档字段；validator 不用数量做质量门。质量门看 `design_rationale`、`contribution_type`、`contribution_character` 和 `contribution_strength`
- 对 `decision.status=selected` 或带 `hypothesis_refs` 的 idea，`contribution_type` 不能是 `routine`，`design_rationale` 不能为空，`contribution_character` 必须回答“领域会怎样不同”，`contribution_strength` 不能低于 2
- 对 `decision.status=selected` 或带 `hypothesis_refs` 的 idea，`mechanism`、`prediction`、`counterfactual` 不能是 `see core_claim`、`qualitative: outperforms baseline`、`no clear counterfactual` 等占位语；淘汰/暂缓候选如果机制未成形，必须在 `rejection_reason` 里说明
- `_family_distribution.md` 必须存在且长度 > 100 字符
- `rejected_ideas.md` 必须解释非选中 idea 的淘汰原因
- `gate_decisions.json` 必须过 schema，并记录两轮 Gate
- `idea_rationales.json` 必须过 schema，并覆盖所有假设 anchor
- 每个实验必须正确引用 `hypothesis_ref`
- `risks.md` 至少有 3 条风险
- 粗略预算不能超项目预算 85%（`ideation.py` line 381：单个实验 `exp_cost > max_budget * 0.85` 会被拒绝；总成本也检查不能超 100%）
- `_candidate_directions.json` 至少 4 个候选（`ideation.py` line 413）
- 存在用户 `must_explore` 桥接方向但最终跨域候选少于配置目标时，系统应在 brief/scorecard 中给出 WARN 或说明原因，不把数量不足作为硬失败。原因是桥接质量取决于 LLM 复核和文献证据，不能为了满足数量硬编 idea。

**注意**：`_premortem.md` 虽然在 prompt 中要求产出，但 **validator 不检查其存在或内容**。它是 prompt 驱动的中间产物，不是 I/O contract 的强制输出。

### 四类补充通道是什么

四类通道不是 idea 的唯一来源，也不是硬性模板。T4 先用 LLM 对综述、paper notes、comparison table 和 seed idea 做主线发散；四类通道只作为 coverage supplement，帮助发现常规综述容易漏掉的角度。如果某类缺少证据，必须记录 unsupported，不允许硬编。

#### 1. 【机制质疑型】

**目标**：质疑或验证现有文献中声称的因果机制。

**数据来源**（优先级从高到低）：
1. `literature/synthesis_workbench.json` 的 `mechanism_claim_clusters` 字段：查看 `challengeable_hint: true` 的机制 cluster，并由 LLM 复核它是否真是共享机制主张
2. `literature/paper_notes/*.md` 的 §13 Mechanism Claim：找 `evidence_type` 为 `claimed_untested` 或 `empirical_correlation` 的机制
3. `literature/paper_notes_abstract/*.md` 的 §13：abstract-only 笔记（`abstract_claim_hint`，引用时标注 "based on abstract only"）

**典型产出形式**：
> "X 论文声称 mechanism A 起作用，但其实可能是 mechanism B。通过实验 E 可以区分。"

**为什么有用**：如果多个工作共享未验证的因果解释，质疑它可能带来有价值的研究问题。但是否值得做必须由 LLM 结合全文证据判断。

#### 2. 【反向操作型】

**目标**：对现有方法的某个核心操作做反向实验。

**数据来源**：
- `literature/comparison_table.csv` 中的方法描述
- `literature/paper_notes/*.md` 的 §2 Method Overview

**典型产出形式**：
> "所有方法都在 X 上做加法（加模块/加 loss/加 augmentation），如果去掉/反转/最小化 X，按现有理论应该 Y，但可能实际是 Z。"

**为什么有用**：反向操作能检验某个组件/训练目标/数据处理是否真是机制必要条件，但必须避免写成普通 ablation。

#### 3. 【子群失败型】

**目标**：找到现有 SOTA 方法在某个子群/场景下 underperform 的现象，提出关于"为什么"的假设。

**数据来源**：
- `literature/paper_notes/*.md` 的 §5 Limitations
- `literature/comparison_table.csv` 中的指标对比

**典型产出形式**：
> "在子群 G 上，所有方法都不如简单 baseline。这说明 mechanism M 在 G 上失效，因为……"

**为什么有用**：子群失败可能揭示方法的隐含假设，但只有当 paper notes 或 comparison table 有明确证据时才生成候选。

#### 4. 【缺口探索型】

**目标**：复核 `missing_areas.md` 中的检索覆盖提示，判断它是否能转化为真实研究问题。

**数据来源**：
- `literature/missing_areas.md` 的 "## Retrieval Coverage Hints（不是研究缺口结论）" 段落
- 每个提示包含 4 个结构化字段：覆盖缺口、为什么需要复核、建议动作、难度

**典型产出形式**：
> "当前检索池对主题 X 的覆盖不足（仅 N 篇），先通过补检/精读确认这是否是真实研究缺口；如果确认，再设计实验验证 Z 视角。"

**选择建议**：优先选 `难度: Low` 或 `Medium` 的提示，但不能直接把覆盖不足改写成“领域没人做”。

**为什么有用**：它帮助发现检索盲区和潜在空白，但必须经过 LLM 和后续阅读复核。

#### 这四类约束的目的

不是让输出更花哨，也不是让四类候选取代主线推理，而是减少：

- 伪创新（四类约束从不同角度检验 idea 的原创性）
- 资源上不可做（缺口探索型的难度字段只能作为可行性 hint）
- 物理/数学上站不住（机制质疑型要求明确的因果链条）
- 只是 baseline 拼接（反向操作型和子群失败型要求深入理解机制）

### `hypotheses.md` 为什么要有 `H1/H2/...`

因为：

- `exp_plan.yaml` 里的 `hypothesis_ref` 要引用它们
- `idea_rationales.json` 里的 `hypothesis_refs` 要覆盖它们，记录每个 idea 来自哪些文献观察、缺口、seed idea 或四类来源约束
- 后续 T4.5 / T6 / T7 都会继续按这些 anchor 追踪假设

### T4 如何记录 idea 的依据

`T4` 现在会把依据拆成四层，形成完整的证据链：

#### 层1：`hypotheses.md` — 人可读生成依据

每个 `H1/H2/...` 都有：
- **背景**：为什么这个假设重要
- **生成依据**：来自 synthesis section、missing area、seed idea 或 comparison table 的具体观察
- **核心假设**：可验证的假设陈述
- **预期结果**：如果假设成立，会观察到什么
- **风险**：如果假设不成立，会怎样

#### 层2：`idea_scorecard.yaml` — 完整候选记录

记录所有候选 idea（不只是选中的），每个包含：
- `idea`：核心内容（id、title、pitch、core_claim、target_problem、mechanism、prediction、counterfactual、mechanism_family）
- `source`：来源追溯
  - `from_synthesis_section`：来自 synthesis.md 的哪个 Q 段落
  - `from_missing_area`：来自 missing_areas.md 的哪个覆盖提示或经 LLM 复核后的缺口
  - `from_seed_idea`：是否来自用户 seed idea
  - `supporting_papers`：支持论文列表
  - `trigger_observation`：触发这个 idea 的关键观察
  - `seed_alignment`：与用户原始 seed 的对齐程度（`direct`/`partial`/`none`）
  - `idea_origin`：`free_reasoning` / `seed_refinement` / `evidence_driven` / `bridge_synthesis` / 四类 supplement
  - `constraint_status`：`mainline` / `supplement` / `bridge` / `not_supported_by_current_evidence`
  - `cross_domain_sources`：如果 idea 使用了 `bridge_transfer_drafts`、`domain_map.theory_bridge`、`paper_notes_bridge/` 或 bridge-domain plan，填对应 `bridge_id` 数组；否则为空数组
  - `cross_domain_relation`：`mechanism_bridge` / `method_transfer` / `evaluation_or_metric_bridge` / `baseline_or_dataset_relevance` / `adjacent_application` / `null`
- `selection_rationale`：为什么值得做
- `closest_baselines`：最接近已有工作及差异
- `scores`：七维评分
- `decision`：Gate 后的决策状态（selected/rejected/deferred/merged）
- `risks`：风险和 kill criteria
- `minimum_experiment`：最低可行实验

#### 层3：`rejected_ideas.md` — 淘汰原因

用人能读懂的方式说明：
- 为什么 pass 掉其他方向
- 什么条件下可以重访
- 最接近的已有工作是什么

#### 层4：`gate_decisions.json` + `idea_rationales.json` — 机器可读依据

- `gate_decisions.json`：记录 `T4-DECIDE-1` / `T4-DECIDE-2` 的用户反馈、selected/rejected idea ids 和决策理由
- `idea_rationales.json`：给最终 hypothesis 写机器可读依据索引，包含 `source_questions`、`literature_observations`、`missing_area_links`、`comparison_table_signals`、`seed_idea_links`、`reasoning` 和 `confidence`

这四层证据链确保 T4.5 新颖性审计、T5 实验计划、T8 论文写作都能回看”为什么会有这个 idea”，也能知道其他方向为什么被淘汰。

### T4 的成功标准

详见上方 "Validator 检查项" 小节。核心要求：

- 所有 7 个输出文件必须存在且通过 schema 校验
- 至少 1 个 selected idea，至少 1 个 rejected/deferred/merged idea
- 两轮 Gate 必须有用户交互记录
- 每个 idea 的 `mechanism`/`prediction`/`counterfactual`/`mechanism_family` 非空；进入最终假设的 idea 不能使用占位语替代机制判断
- 预算不能超项目预算 85%

### T4 的 Ideation 覆盖分析

`researchos/tools/ideation_analysis.py` 提供 `analyze_ideation_coverage()` 函数，可确定性分析 T4 候选 idea 对可用证据的覆盖情况：

- 方法家族覆盖：哪些 method_families 被 idea 覆盖（使用 token overlap 匹配 idea 的 `mechanism_family` 字段与 workbench 的 family name）
- 研究问题覆盖：哪些 Q1/Q2/... 被 idea 引用
- mechanism claim cluster 覆盖：有多少 `challengeable_hint` cluster 被 idea 复核/挑战
- missing_areas 覆盖：结构化 coverage hints 被多少 idea 指向
- 来源混合统计：free reasoning / seed refinement / evidence-driven / supplement 各有多少
- 四类补充统计：机制质疑/反向操作/子群失败/缺口探索各有多少；这是覆盖 telemetry，不是质量评分
- seed_alignment 分布：direct/partial/none 各多少

四类补充检测优先使用 `idea_scorecard.yaml` 中的结构化字段（`source.category`、`source.idea_origin`），而非关键词匹配。只有当结构化字段缺失时，才回退到 `trigger_observation` 文本分析作为兜底。

### T4 单独运行示例

```bash
cd ResearchOS
researchos run-task T4 --workspace ./workspace/local-test2
```

---

## 6.8 T4.5：NoveltyAuditorAgent

### 角色

- Agent：`NoveltyAuditorAgent`
- 代码： [researchos/agents/novelty_auditor.py](../researchos/agents/novelty_auditor.py)
- Prompt： [researchos/prompts/novelty_auditor.j2](../researchos/prompts/novelty_auditor.j2)

### 当前默认配置

- model tier：`heavy`
- 主要工具：
  - `read_file`
  - `write_file`
  - `list_files`
- `search_papers`
- `fetch_paper_metadata`
- `extract_mechanism_tuple`
- `compare_mechanism_tuples`
- `extract_design_rationale_tuple`
- `compare_design_rationale_tuples`
- `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 研究方向和关键词 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | T4 产出的假设 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |
| `comparison_table` | `literature/comparison_table.csv` | 否 | 已有方法对比表 |
| `idea_scorecard` | `ideation/idea_scorecard.yaml` | 否但推荐 | T4 的 idea scorecard，含 mechanism/prediction/counterfactual/mechanism_family 字段 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `novelty_audit` | `ideation/novelty_audit.md` | 针对每个假设的新颖性预审 |
| `mechanism_tuples_dir` | `ideation/_mechanism_tuples/` | 每个假设的机制 tuple JSON 文件 |
| `design_rationale_tuples_dir` | `ideation/_design_rationale_tuples/` | 每个假设的 CDR design-rationale tuple JSON 文件 |
| - | `ideation/collision_cases.md` | 可选归档；有 High/Medium Overlap 时必须生成并记录潜在撞车案例 |

### T4.5 到底审计什么

它审计的是：

- 这些假设在“还没做实验之前”看起来是否已经撞车
- 哪些假设只是增量
- 哪些假设可能需要在 T6 前后继续重点复核

### T4.5 怎样搜相关工作

当前 prompt 设定是：

对每个假设：

1. 提取核心概念
2. 设计 4 类 query
   - 核心方法 + 应用场景
   - 核心技术术语
   - 问题描述
   - mechanism 关键词（从 `extract_mechanism_tuple` 提取的 input_signal + mechanism 文本）
3. 用 `search_papers`
4. 搜近 12 个月的相关工作；`year_from` 按运行日期动态计算，不写死年份
5. 每个 query `max_results=30`

然后按相似度分成：

- `High Overlap`
- `Medium Overlap`
- `Low Overlap`
- `No Overlap`

### 实际执行过程

`NoveltyAuditorAgent` 先读 `ideation/hypotheses.md`，用 `H1/H2/...` anchor 切分每个假设；再读 `literature/synthesis.md`、`literature/comparison_table.csv` 和 `ideation/idea_scorecard.yaml`，拿到已有方法家族、baseline、指标上下文和 mechanism 字段。对每个假设，它从假设标题、方法关键词、目标场景和预期机制中抽出 3-4 组 query（包括第 4 类 mechanism 关键词 query），然后调用 `search_papers(query=..., source="auto", max_results=30, year_from=<运行时近一年起始年>)` 搜近期工作；`search_papers` 的 schema 是单数 `source`，不是 `sources`。必要时用 `fetch_paper_metadata` 回查疑似撞车论文。

每个假设还必须分别调用 `extract_mechanism_tuple` 和 `extract_design_rationale_tuple`：前者保存到 `ideation/_mechanism_tuples/`，服务 collision axis；后者保存到 `ideation/_design_rationale_tuples/`，包含 `problem_frame`、`design_rationale`、`artifact`、`contribution_type`、`evaluation_mode` 和 `boundary_conditions` 等字段，服务 ambition / contribution-distance axis。对疑似相近论文，审计员先用 LLM 阅读摘要/metadata 判断关系，再调用 `compare_mechanism_tuples` 和 `compare_design_rationale_tuples` 获取机械相似度 hint；tool hint 不能直接替代最终 novelty 判断。

最终报告必须分开写 `Collision Axis` 与 `Ambition Axis`，并给出 `Contribution Distance` 和 `Final Gate Verdict`。没有 close baseline 不能被惩罚为低 novelty；应写成高新颖/高风险。`contribution_type=routine` 或 `routine_risk` 不能无条件进入外部实验链或 T8 写作，必须建议回到 T4 重新 framing 或放弃；是否继续由 T4.5 人工 gate 裁决。

同时，它加载 `novelty-audit` guidance，用 LLM 从每个假设中提取机制因果断言、操作对象和预期效果，再调用 `extract_mechanism_tuple` 保存 tuple；如果领域里需要更细的标签，可以把 `normalized_input_signal` 一并传给工具，而不是被工具枚举限制。对每篇疑似撞车论文，LLM 先阅读摘要/metadata 提取机制 tuple，再调用 `compare_mechanism_tuples` 获取 mechanical similarity hint。该工具只返回 `possible_true_collision` / `possible_mechanism_collision` / `possible_explanatory_competition` / `likely_distinct` 这类待审提示，不能直接给最终新颖性结论。

审计时它把搜索结果、synthesis、comparison table 和 mechanism hint 对齐，由 LLM 判断相似点、差异点、证据强度、是否需要补 baseline，以及最终标签 `true_collision / mechanism_collision / explanatory_competition / safe`。只有 LLM 确认机制、任务边界和贡献点都高度一致时，才把对应假设降为 Level 0。最后用 `write_file(“ideation/novelty_audit.md”, ...)` 写每个假设的 Level 0-3 判定。如果报告中出现真实 High/Medium Overlap，它还必须写 `ideation/collision_cases.md`，记录论文、相似点、差异点和处理建议；validator 会区分”High Overlap: none”这种空标题和真实案例，只有真实案例才强制 collision 文件。

### T4.5 的新颖性等级

- `Level 3`：高度新颖
- `Level 2`：中度新颖
- `Level 1`：低度新颖
- `Level 0`：无新颖性 / 明确撞车

### T4.5 非通过 verdict 的人工决策

T4.5 不再在 `return_to_T4_reframe` 或 `drop_due_to_collision` 时自动回退/失败。现在的分支语义是：

- `pass_to_experiment` / `pass_with_required_baselines`：进入 `T5-HANDOFF`，由 ResearchOS 编译实验协议、选择外部执行器、生成 handoff prompt；mock dry-run 路径可自动到 `T7-INGEST`，真实 Codex/Claude/manual 路径会先经过 `T5-EXTERNAL-WAIT`。
- `return_to_T4_reframe`、`drop_due_to_collision`、`reject`、`collision`、`fail`：进入 `T4.5-HUMAN-REVIEW`。

`T4.5-HUMAN-REVIEW` 是 gate-only 节点，`state_machine` 会直接进入 `WAITING_HUMAN`，不会再启动一次 `NoveltyAuditorAgent`。gate 展示：

- `ideation/novelty_audit.md`
- `ideation/_gate1_selection_brief.md`
- `ideation/idea_scorecard.yaml`
- `ideation/rejected_ideas.md`

用户可以选择：

- `continue_to_t7`：接受风险，继续进入外部实验链入口 `T5-HANDOFF`。
- `return_to_t4`：回到 T4 重构假设或选择其它候选。
- `stop_project`：结束当前项目。

选择结果会写入 `ideation/novelty_human_review.json`，语义是 `human_decision_over_agent_recommendation`。这样 Novelty Auditor 可以提出 reframe/drop 建议，但最终判断权在用户手里，避免 T4.5 与 T4 之间自动循环，也避免系统自动拒绝仍有价值但需要重新 framing 的 idea。

### T4.5 的成功标准

validator 会检查：

- `novelty_audit.md` 存在且不太短
- 必须出现 `Level 0-3`
- 每个 `H1/H2/...` 都必须被审计
- `_mechanism_tuples/` 目录存在，每个假设至少有一个 tuple 文件
- 如果审计报告明确写出最终确认的 true_collision（high confidence），对应假设必须为 Level 0；工具返回的 `possible_true_collision` 只会触发人工/LLM复核，不会自动降级
- 如果 audit 中列出 High/Medium Overlap，`collision_cases.md` 必须存在并归档对应案例；没有 High/Medium Overlap 时可以不生成该文件。状态机里 `collision_cases` 是 `optional_outputs` 条件产物，不会被基础 outputs 校验无条件强制。
- 恢复运行时，如果 `novelty_audit.md` 与 `_mechanism_tuples/` 已存在并通过 `NoveltyAuditorAgent.validate_outputs()`，runtime 会执行 `t45_resume_prefinalize`，跳过 LLM 续跑；若 audit 提到 High/Medium Overlap，仍必须由 agent validator 要求 `collision_cases.md`

### mechanism tuple 工具

T4.5 引入了两个机制感知工具，用于自动化的碰撞检测。此外，`NoveltyAgent._extract_mechanism_keywords()` 使用结构化模式匹配提取搜索关键词（连字符术语、缩写词、大写技术名词、通用 ML 术语），不依赖硬编码的领域关键词列表；LLM agent 自身会根据假设上下文调整 query。

**`extract_mechanism_tuple`** — tuple 持久化和轻量归一化工具。Agent 从假设文本中提取因果机制描述，调用此工具保存 JSON 文件；如 LLM 已有更合适的领域标签，可传 `normalized_input_signal` / `normalized_evidence_type` 覆盖 fallback hint：

- `input_signal_raw`：机制操作对象的原始自由文本
- `input_signal`：LLM 提供的标签，或工具 fallback hint
- `mechanism`：因果机制描述（单句："X causes Y"）
- `claimed_effect`：机制正确时的预期改善
- `evidence_type`：LLM 提供的证据类型，或工具 fallback hint
- 保存到 `ideation/_mechanism_tuples/{source_id}.json`

**`compare_mechanism_tuples`** — 纯代码 similarity hint，无 LLM 调用，也不做最终判决。工具不内置领域 ontology 或 synonym group；如果领域需要细粒度标签，应由 LLM 在 `extract_mechanism_tuple` 时传入更合适的 normalized label。比较两个 tuple 返回：

- `input_match`：same / related / different（优先比较 LLM 提供的 normalized label；否则只用 raw label 的 token overlap 作为 related hint）
- `mechanism_similarity_hint`：same / related / different（基于 Jaccard token 相似度，阈值 0.6/0.3）
- `heuristic_verdict`：possible_true_collision / possible_mechanism_collision / possible_explanatory_competition / likely_distinct
- `heuristic_confidence`：high / medium / low
- `requires_llm_judgment: true`

hint 矩阵：

| input \ mechanism | same | related | different |
| --- | --- | --- | --- |
| same | possible_true_collision | possible_mechanism_collision | possible_explanatory_competition |
| related | possible_mechanism_collision | possible_mechanism_collision | likely_distinct |
| different | possible_mechanism_collision | likely_distinct | likely_distinct |

代码： [researchos/tools/mechanism_tools.py](../researchos/tools/mechanism_tools.py)

### 它和 T6 的区别

非常重要：

- T4.5：**没有 Pilot 证据时**的预审
- T6：**有 Pilot 证据后**的增量复核

T6 不应该从零重跑一次 T4.5，这个逻辑现在已经明确分开了。

---

## 6.9 T5-HANDOFF：ExperimenterAgent（handoff）

### 角色

- Agent：`ExperimenterAgent`
- mode：`handoff`
- 代码： [researchos/agents/experimenter.py](../researchos/agents/experimenter.py)
- Prompt： [researchos/prompts/experimenter.j2](../researchos/prompts/experimenter.j2)
- Tool：`build_experiment_handoff_pack`

### 语义

`T5-HANDOFF` 是新版实验链入口。它不写实验代码、不运行 Docker、不碰 GPU，也不把 LLM 生成的自然语言当实验结果。它只把 `project.yaml`、`hypotheses.md`、`exp_plan.yaml`、`risks.md`、`novelty_audit.md`、`idea_scorecard.yaml`、`synthesis.md`、`comparison_table.csv` 和 `resources/`/`literature/baseline_map.json` 中已有资源线索编译成外部执行器可执行的 artifact 协议。

### 输出文件

| 输出 | 含义 |
| --- | --- |
| `external_executor/handoff_pack.json` | 外部实验契约：metrics、seeds、实验计划、source artifacts、allowed paths、executor outputs |
| `external_executor/executor_selection.json` | 执行器选择占位；真实选择由 T5-EXECUTOR-GATE 写入 |
| `external_executor/input_manifest.json` | handoff 输入和 required executor outputs 清单 |
| `external_executor/expected_outputs_schema.json` | 外部执行器必须写出的 result/status/manifest/artifact 字段 |
| `external_executor/allowed_paths.txt` | 外部执行器允许写入的路径白名单 |
| `external_executor/AGENTS.md` | Codex/generic coding agent 的最高优先级执行说明 |
| `external_executor/CLAUDE.md` | Claude Code 窗口执行说明 |
| `external_executor/README.md` / `_DIR_GUIDE.md` | 外部执行器目录说明 |
| `external_executor/job_state.json` | 外部执行器生命周期状态 |
| `external_executor/executor_prompt.md` | 当前 executor 的通用 prompt |
| `external_executor/codex_prompt.md` | 给 Codex CLI/外部 Codex 的执行 prompt |
| `external_executor/claude_code_prompt.md` | 给 Claude Code 窗口的执行 prompt |
| `external_executor/manual_instructions.md` | 给人工或其它外部工具的执行说明 |

### 实际执行过程

`ExperimenterAgent(handoff)` 的初始消息要求只调用 `build_experiment_handoff_pack`。该工具读取 `ideation/exp_plan.yaml` 抽取 metric 和实验条目，读取 `project.yaml` 抽取 seed ensemble 和 venue hint，读取 `ideation/hypotheses.md` 做短 preview，并为每个上游 artifact 记录存在性、bytes 和 sha256。它还会从 `ideation/novelty_audit.md` 的 `## Required Baselines` 段抽取用户/LLM 已明确写下的 required baseline，落盘到 `novelty/required_baselines.json`，并写入 `handoff_pack.experiment_contract.required_baselines`。工具只做结构化抽取，不发明 baseline，也不在工具内部调用 LLM 做科学判断。

随后工具写出 handoff pack、schema、allowed paths、AGENTS/CLAUDE 指南、job_state 和多执行器 prompt。此时执行模式尚未选择，AGENTS/CLAUDE/prompt 文件中会保留 `UNSET` 占位；`T5-EXECUTOR-GATE` 选择执行器后，runtime 会确定性 patch 这些占位。validator 会检查这些文件全部存在、`semantics` 正确、executor output schema 包含必需字段、allowed paths 非空，并确认没有把 mock 或自然语言总结写成真实结果。

### 恢复逻辑

如果 handoff 文件已经存在且结构合格，`resume` 不需要让 LLM 重新生成实验协议；外部执行器选择、input manifest 和 prompt 会作为已有 artifact 进入恢复上下文。用户要切换真实执行器时，应回到 `T5-EXECUTOR-GATE` 重新选择，而不是在后续 ingest 阶段改写结果来源。

## 6.10 T5-EXECUTOR-GATE / T5-EXTERNAL-WAIT：执行器选择与外部等待

### T5-EXECUTOR-GATE

- Agent：`ExperimenterAgent`
- mode：`executor_gate`
- 类型：状态机 `immediate_gate`，正常完整 pipeline 不启动 LLM

`T5-EXECUTOR-GATE` 是真实实验启动前的人工控制点。它展示 `handoff_pack.json`、`allowed_paths.txt`、Codex prompt 和 Claude prompt，让用户选择：

- `mock_dry_run`：进入 `T5-DRY-RUN`，只测试文件协议；
- `claude_code_window`：进入 `T5-EXTERNAL-WAIT`，用户把 `claude_code_prompt.md` 复制到 Claude Code；
- `codex_cli`：进入 `T5-EXTERNAL-WAIT`，允许在 `external_executor/workdir` 执行真实实验；
- `manual`：进入 `T5-EXTERNAL-WAIT`，由人工或其它执行器写回结果。

gate 解析后，runtime 写 `external_executor/executor_selection.json`，字段包括 `selected_executor`、`real_experiment_allowed`、`requires_user_copy_paste`、`next_state`、`selected_by`、`selected_at` 和 fallback。随后 runtime 调用确定性 patch，把 AGENTS/CLAUDE/prompt 中的 `dry_run: UNSET`、`mock_only: UNSET`、`real_experiment_allowed: UNSET` 替换成真实值，并同步 `handoff_pack.executor/execution_mode`。

交互细节：CLI 交互式运行时，在 `T5-EXECUTOR-GATE` 直接回车会选择默认的 `mock_dry_run`；选择 `codex_cli` 后必须再输入 `yes` 才允许真实实验，否则会降级为 `claude_code_window` 并把降级原因写入 `executor_selection.json.notes`。非交互 stdin/管道关闭时不会默认选 mock，也不会默认继续，而是把项目暂停为可 `resume` 状态，避免“没有真实用户输入却推进实验”的隐性错误。

### T5-EXTERNAL-WAIT

- Agent：`ExperimenterAgent`
- mode：`external_wait`
- 运行方式：runtime pre-finalizer，优先不调用 LLM

`T5-EXTERNAL-WAIT` 读取 `external_executor/executor_selection.json`、`external_executor/result_pack.json`、`external_executor/executor_status.json` 和 `external_executor/run_manifest.json`。如果缺文件、`semantics` 不正确、result/status/manifest 的 executor 与 selection 不一致、真实 executor 复用了旧 `mock_only/dry_run` result pack、status/current_state 不是 `done`/`COMPLETED`、metric 为空、引用的 raw/config/log 不存在、sha256 不匹配、路径不在 `allowed_paths.txt` 的 `rw` 范围，或真实执行没有非空 run/raw result 记录，runtime 会写 `external_executor/wait_rejection_report.md` 并抛出 recoverable pause，项目状态保持可 `resume`。`PARTIAL_RESULTS_READY` 默认不通过，因为部分结果进入 T7 后容易产生半成品 claim；只有工具参数或未来显式配置 `allow_partial_results=true` 时才允许通过，并且后续 claim audit 必须降级处理。外部 Codex/Claude/manual 执行器修复结果后，用户执行 `researchos resume`，runtime 会重新检查并写 `external_executor/wait_acceptance_report.json`，然后进入 `T7-INGEST`。这避免了“结果还没写完、证据不可审计，或从 mock 切到真实 executor 后误吃旧结果”的隐性错误。

## 6.11 T5-DRY-RUN：ExperimenterAgent（dry_run）

### 角色

- Agent：`ExperimenterAgent`
- mode：`dry_run`
- Tool：`mock_external_dry_run`

### 语义

`T5-DRY-RUN` 是协议联调节点，不是真实实验。它证明外部执行器 handoff、result pack、status、run manifest、raw result、config、log、heartbeat 这些文件协议能端到端跑通。所有输出必须带 `dry_run=true` / `mock_only=true`，后续 claim audit 会阻止它们被写成论文实证 claim。

### 输出文件

| 输出 | 含义 |
| --- | --- |
| `external_executor/result_pack.json` | schema-compatible mock result pack |
| `external_executor/executor_status.json` | 执行器状态；`accepted=false`，ResearchOS 后续 audit 才能决定接收度 |
| `external_executor/run_manifest.json` | raw/config/log artifact 列表及 sha256 |
| `external_executor/heartbeat.json` | 外部执行器心跳/状态文件 |
| `external_executor/raw_results/mock_results.json` | mock raw metrics |
| `external_executor/configs/mock_config.json` | mock config |
| `external_executor/logs/mock_dry_run.log` | mock log |

### 实际执行过程

`ExperimenterAgent(dry_run)` 只调用 `mock_external_dry_run`。工具读取 `handoff_pack.json` 中声明的 metrics 和 seeds，生成 mock metric、raw result、config、log、manifest、status 和 result pack。每个 artifact 都登记路径、role、kind、sha256 和 bytes。validator 会检查 result pack/status/run manifest/heartbeat/raw/config/log 都存在，检查 result pack 的 `semantics=external_executor_result_pack`，并确认 mock 标记没有丢失。

## 6.12 T7-INGEST / T7-AUDIT / T7-POST-NOVELTY / T7-CLAIMS：外部实验结果闭环

新版 T7 不再是单个“完整实验”节点，而是四个互相分离的证据节点。

### T7-INGEST：结果摄取

- Agent：`ExperimenterAgent`
- mode：`result_ingest`
- Tool：`ingest_external_results`

`T7-INGEST` 读取 `external_executor/result_pack.json`、`executor_status.json` 和 `handoff_pack.json`，把外部执行器输出规范化成 ResearchOS 下游兼容 artifact：

| 输出 | 含义 |
| --- | --- |
| `experiments/results_summary.json` | 下游 T7.5/T8 兼容结果摘要，含 `source=external_executor`、`dry_run`、`mock_only`、metrics 和 experiments |
| `experiments/run_records.jsonl` | 原始 result pack 的 run record |
| `experiments/evidence_index.json` | metric、artifact、log、run_manifest 索引 |
| `experiments/ingest_report.json` | 摄取报告 |

这个节点不判断结果是否科学可信，只做 schema 和归一化。validator 会检查 `results_summary`、`run_records`、`evidence_index`、`ingest_report` 都存在且语义正确。

### T7-AUDIT：实验诚信审计

- Agent：`ExperimenterAgent`
- mode：`integrity_audit`
- Tool：`audit_experiment_integrity`

`T7-AUDIT` 读取 `experiments/results_summary.json` 和 `experiments/evidence_index.json`，审计每个 metric 是否有 source artifact、source artifact 是否在 index 中、artifact 是否仍存在、sha256 是否匹配、run manifest 是否存在且语义正确。它还会读取 `novelty/required_baselines.json` 或 `novelty_audit.md` 中抽取出的 required baseline，生成 `required_baseline_coverage`。缺 required baseline 时，真实结果会被标为 `fail` 或 claim block；mock dry-run 会保持 `mock_only`，但同样记录缺口。

输出：

```text
experiments/integrity_audit.json
experiments/experiment_fairness_review.md
```

该文件的 `status` 可以是 `pass`、`mock_only` 或 `fail`。`fail` 表示 provenance、结构或 required baseline 覆盖有硬错误；`mock_only` 表示协议可通但不能作为论文 claim evidence。`experiment_fairness_review.md` 是给 LLM/人工复核 baseline fairness、metric relevance 和 claim overreach 的审计脚手架，不替代科学判断。

### T7-POST-NOVELTY：实验后 novelty/collision 复核

- Agent：`ExperimenterAgent`
- mode：`post_novelty`
- Tool：`build_post_experiment_novelty_check`

`T7-POST-NOVELTY` 位于 `T7-AUDIT` 和 `T7-CLAIMS` 之间。它读取 `ideation/novelty_audit.md`、`novelty/required_baselines.json`、`external_executor/result_pack.json`、`experiments/results_summary.json` 和 `experiments/integrity_audit.json`，输出：

```text
novelty/post_experiment_novelty_check.json
novelty/post_experiment_collision_cases.md
```

当前工具只做确定性证据状态复核：mock-only、integrity fail、required baseline missing 会进入 `claim_downgrades_required`，不会自动拒绝 idea，也不会替 LLM 断言“已经撞车”。后续 T7.5、Writer 和 Reviewer 必须读取该 artifact，再用 LLM/人工判断是否需要补实验、降级 claim 或回 T4。

### T7-CLAIMS：result-to-claim 与 evidence pack

- Agent：`ExperimenterAgent`
- mode：`result_to_claim`
- Tools：`map_results_to_claims`、`build_experiment_evidence_pack`

`T7-CLAIMS` 先把审计后的 metric 转成保守 claim mapping，再把结果、审计、artifact index 和 claim mapping 组装成 T8 写作证据包。

| 输出 | 含义 |
| --- | --- |
| `experiments/experimental_claims.json` | 机械 result-to-claim map，不是最终科学判断 |
| `drafts/result_to_claim.json` | 给 T8/Reviewer 使用的 claim mapping 镜像 |
| `drafts/experiment_evidence_pack.json` | 规范化实验写作证据包 |
| `drafts/must_not_claim.md` | 由 mock/缺 baseline/审计问题导出的禁止措辞 |
| `drafts/claim_support_matrix.csv` | claim、metric、evidence、strength 的矩阵 |
| `drafts/limitations_from_experiments.md` | 由实验审计导出的限制清单 |
| `drafts/figure_table_evidence_map.json` | 图表与 evidence/claim 的关系 |
| `experiments/iteration_log.md` | 外部实验链迭代摘要 |

`result_to_claim.json` 的核心字段是 `claim_mappings[]` 和 `claims[]`。每条 mapping 包含 `support_status`、`claim_strength`、`metric_refs`、`evidence_refs`、`allowed_wording`、`forbidden_wording` 和 `limitations`。如果来源是 mock dry-run，`support_status=unsupported_mock_only` 且 `claim_strength=unsupported`，Writer 只能把它写成协议测试或限制，不能写成实证结果。required baseline missing 时，不允许 strong claim，不允许写 “outperforms prior work / state-of-the-art / strong empirical advantage”。

### Legacy T5/T6/T7 兼容节点

旧 `T5`、`T6`、`T7` 保留是为了已有 workspace 的状态机迁移和显式 legacy 调试，但普通 `run-task` 不再静默进入旧内部实验语义：

- `researchos run-task T5 --workspace ...`：报 retired，提示改用新版 `T5-HANDOFF`。
- `researchos run-task T6 --workspace ...`：报 retired，提示改用新版 `T7-POST-NOVELTY`。
- `researchos run-task T7 --workspace ...`：报 retired，提示改用新版 `T5-HANDOFF` 外部实验链。
- `researchos run-task LEGACY-T5-PILOT --allow-legacy --workspace ...`：显式运行旧 pilot 内部实验。
- `researchos run-task LEGACY-T6-NOVELTY --allow-legacy --workspace ...`：显式运行旧 pilot 后 novelty 复核。
- `researchos run-task LEGACY-T7-FULL --allow-legacy --workspace ...`：显式运行旧内部完整实验。

完整 `run/resume` 默认不会从 T4.5 进入这些 legacy 节点。T4.5 pass 路由到 `T5-HANDOFF`；T7.5 的旧推荐 `next_task: T7` 仍会被状态机安全映射到 `T5-HANDOFF`，用于兼容旧 workspace 的 PI 输出。这样可以保留旧功能，又不会让手动调试或主链误回“ResearchOS 自己长时间跑实验”的旧设计。

---

## 6.12 T7.5：PIAgent（evaluate）

### 角色

- Agent：`PIAgent`
- mode：`evaluate`
- 代码： [researchos/agents/pi.py](../researchos/agents/pi.py)
- Prompt： [researchos/prompts/pi.j2](../researchos/prompts/pi.j2)
- 输出：`evaluation/evaluation_decision.md`

### 当前默认配置

- model tier：`heavy`
- 主要工具：
  - `read_file`
  - `write_file`
  - `list_files`
  - `finish_task`
- 运行特征：
  - 这是一个典型的“决策型 task”
  - 它的价值不在于生成很多新内容，而在于给状态机提供一个明确下一步

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `results_summary` | `experiments/results_summary.json` | 是 | 外部 result pack 摄取后的结果摘要 |
| `integrity_audit` | `experiments/integrity_audit.json` | 是 | provenance、hash、mock_only、metric source 审计 |
| `experimental_claims` | `experiments/experimental_claims.json` | 否但强烈推荐 | 机械 result-to-claim map |
| `result_to_claim` | `drafts/result_to_claim.json` | 是 | T8 写作可消费的 claim mapping |
| `experiment_evidence_pack` | `drafts/experiment_evidence_pack.json` | 是 | T8 写作证据包 |
| `iteration_log` | `experiments/iteration_log.md` | 否但强烈推荐 | 外部实验链迭代过程 |
| `exp_plan` | `ideation/exp_plan.yaml` | 否但强烈推荐 | 原始实验计划 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `evaluation_decision` | `evaluation/evaluation_decision.md` | PI 视角的阶段评估与下一步建议 |

### 它在完整 pipeline 中的作用

T7.5 的意义是：

- 外部执行器结果完成摄取、审计和 result-to-claim 后
- 不立刻写论文
- 先由 PI 视角判断：
  - 审计后的证据是否足够
  - 哪些 claim 只能保守表述或必须降级
  - 要不要回外部实验链继续补
  - 要不要回 T4 改假设
  - 还是可以进 T8

### 实际执行过程

`PIAgent(evaluate)` 启动后读取 `experiments/results_summary.json`、`experiments/integrity_audit.json`、`drafts/result_to_claim.json`、`drafts/experiment_evidence_pack.json`、`experiments/iteration_log.md` 和 `ideation/exp_plan.yaml`。它会把实验结果按原计划和 claim mapping 对齐：哪些 hypothesis 有审计后的 metric 支持，哪些只有 mock/dry-run 协议证据，哪些 claim 需要保守 wording，哪些 baseline/seed/artifact 仍缺失。然后它用 `write_file("evaluation/evaluation_decision.md", ...)` 写一份决策报告，必须包含 `Situation`、`Options` 和至少一个 `next_task`。典型 next_task 可以是 `T8-STYLE-GATE` 或 `T8-RESOURCE`（证据足够，进入新版写作入口）、`T5-HANDOFF`（回到外部实验链补证据）、`T4`（回到假设重构）或 `done`。旧报告写 `T8` / `T8-WRITE` 时会映射到 `T8-STYLE-GATE`，只有合法 `drafts/writing_style.json` 已存在时才直接进入 `T8-RESOURCE`；旧报告写 `T7` 时会安全映射到 `T5-HANDOFF`，避免 resume 误入 legacy 内部实验。完整 pipeline 中，StateMachine 会从 `evaluation_decision.md` 反向解析 `next_task`，再把 PI 推荐交给 human gate；用户可以接受推荐，也可以在 gate 里选择其它路径。

### `evaluation_decision.md` 里面最关键的字段

- `Situation`
- `Options`
- `next_task`
- 对 `integrity_audit` / `result_to_claim` 的证据充足性判断

当前状态机支持从这里解析 `next_task`。

你可以把这一步理解成：

- `T5-HANDOFF` 到 `T7-CLAIMS` 负责把外部实验协议和证据链跑通
- `T7.5` 负责决定“审计后的证据是否已经足够支撑写论文”

### 完整链路中的行为

完整链路不是 `T7.5 -> T8` 直接跳，而是：

```text
T5-HANDOFF -> T5-EXECUTOR-GATE
 -> mock_dry_run: T5-DRY-RUN
 -> external: T5-EXTERNAL-WAIT
 -> T7-INGEST -> T7-AUDIT -> T7-POST-NOVELTY -> T7-CLAIMS
 -> T7.5
 -> ask_human / gate
 -> 按 PI 推荐或人工指定进入下一步
```

### StateMachine 在这里做了什么特殊事

`T7.5` 是当前状态机里少数会从输出文件反向解析分支的节点：

- 先读取 `evaluation/evaluation_decision.md`
- 再提取 `next_task`
- 然后把这个推荐值交给 human gate

所以它不是普通的“成功后固定跳下一个状态”。

### 单独运行示例

```bash
cd ResearchOS
researchos run-task T7.5 --workspace ./workspace/local-test2
```

### 完整恢复示例

```bash
cd ResearchOS
researchos resume --workspace ./workspace/local-test2
```

---

## 6.13 T8：Writer / Reviewer 多阶段写作链

详细写作设计、章节 craft 和后续路线见 [docs/manuscript.md](manuscript.md)。本节只记录当前真实 pipeline 的运行视角。

T8 不是“一次写完整篇论文”的节点，也不是“一个 Writer 一口气写多个 section 后拆文件”。当前主链是：

```text
T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-METHOD
 -> T8-SEC-EXPERIMENTS
 -> T8-SEC-RELATED
 -> T8-SEC-ANALYSIS
 -> T8-SEC-INTRO
 -> T8-SEC-CONCLUSION
 -> T8-SEC-ABSTRACT
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
```

旧 `next_task: T8` / `next_task: T8-WRITE` 会先映射到 `T8-STYLE-GATE`；只有 workspace 已有合法 JSON 且 `venue_style` 为 `is` / `ccf_a` / `both` 的 `drafts/writing_style.json` 时，状态机才会跳过风格 gate 并进入 `T8-RESOURCE`。旧 `T8-SECTIONS` 单任务入口仍映射到 `T8-SECTION-PLAN`。旧 `T8-SEC-LIMITATIONS` 单任务入口映射到 `T8-SEC-CONCLUSION`；Limitations 不再是独立 section，而是 Conclusion 内的 `\subsection{Limitations}`。

### 章节写作顺序与对齐契约

`SECTION_WRITING_SEQUENCE` 当前为 7 个正文作业：

```text
methodology -> experiments -> related_work -> analysis -> introduction -> conclusion -> abstract
```

这个顺序先稳定方法和结果，再写定位、解释和引言，最后写 Conclusion/Limitations 和 Abstract。每个 section 写作时 Writer 会读取前一个 section 文件尾部约 1200 字符作为局部衔接上下文，但不会把整篇论文塞进一次 prompt。

### T8-PAPER-CLAIM-AUDIT：T9 前最终 claim/evidence 审计

`T8-PAPER-CLAIM-AUDIT` 是进入 T9 前的最终零上下文审计节点。runtime 会优先在 LLM 前直接调用 `audit_paper_claims`，读取 `drafts/paper.tex`、`drafts/experiment_evidence_pack.json` 和 `drafts/result_to_claim.json`，生成 `drafts/paper_claim_audit.md/json`。该节点只做审计，不重写正文；如果审计失败，状态机会回到 `T8-REVISE-2`，由 Writer 按具体 claim 问题修订相关 section。未在 evidence pack 中出现的实验数字是 FAIL；mock-only evidence、forbidden wording 和 unsupported strong claim 也会阻断。`paper_claim_audit.json` 带 `input_fingerprints`，validator 会比对当前 paper/evidence/result-to-claim hash，防止 resume 时旧 audit 放行新正文。

T8 的核心跨章节契约是 `drafts/alignment_matrix.json`。它把每个 cid 贯通为：

```text
motivation -> contribution -> related_gap -> design_choice -> experiment -> analysis
```

`build_alignment_matrix` 的行来自 `cdr_claim_ledger.json` 中的 `contribution_chains`，不是原始 CDR evidence slots。`contribution_chains` 是 3-4 条最终 contribution bullet 的机械 lane，用来避免把 7 个左右的 evidence/section slot 误当成论文贡献。工具只生成 seed 和审计 hint；motivation、gap、contribution wording、设计解释和分析判断必须由 LLM 阅读 artifact 后完成。`cid` 只允许作为 `alignment_matrix.json` / `paper_state.json` 的内部追踪 id；最终 TeX 正文和注释都不能出现 `C1`、`[C1]`、`C1:`、`C1 is ...`、`CID C1` 或 `% [C1]` 这类内部编号。

### 6.12.0 实现边界

`WriterAgent` 负责 style gate、resource、outline、section_plan、section_draft、draft、self_check、revise。代码在 [researchos/agents/writer.py](../researchos/agents/writer.py)，prompt 在 [researchos/prompts/writer.j2](../researchos/prompts/writer.j2)，通用写作 skill 在 [researchos/agent_guidance/manuscript-writing/SKILL.md](../researchos/agent_guidance/manuscript-writing/SKILL.md)，跨 section craft 在 [researchos/prompts/paper_craft.j2](../researchos/prompts/paper_craft.j2)。

`ReviewerAgent` 负责两轮逐章节 review 和综合 review。代码在 [researchos/agents/reviewer.py](../researchos/agents/reviewer.py)，prompt 在 [researchos/prompts/reviewer.j2](../researchos/prompts/reviewer.j2)。

主要 manuscript tools 在 [researchos/tools/manuscript.py](../researchos/tools/manuscript.py)：

- `build_manuscript_resource_index`：扫描 workspace 中的文献、实验、假设、图表、代码和日志资源。
- `plan_manuscript_sections`：生成 7 个 section 的 required inputs、expected outputs 和 LLM 任务提示。
- `plan_manuscript_evidence`：生成 claim slots 与 figure/table slots。
- `build_manuscript_registries`：生成 CDR claim ledger、generic claim ledger 和 figure registry seed；其中 `contribution_chains` 是后续 alignment matrix 使用的 3-4 条贡献 lane。
- `build_alignment_matrix`：基于 contribution lanes 生成 alignment matrix seed，不做最终学术判断，也不把机械 evidence slots 当最终贡献。
- `initialize_manuscript_state`：生成 `paper_state.json` 和每章 `section_outlines/*.md`。
- `update_manuscript_section_state`：记录单章 written/revised 状态。
- `assemble_manuscript`：机械拼装 section files 为 `paper.tex`；当 `venue_style=both` 时，同时写 `drafts/is/paper.tex` 和 `drafts/ccf_a/paper.tex` 两个风格变体入口，但真正的 IS/CCF-A 风格化改写由 Writer LLM 完成。
- `audit_manuscript_claims`：检查 citation key、数字、图表引用和核心章节。
- `audit_writing_craft`：检查 alignment/craft 机械问题，如独立 Limitations、Abstract 正式引用、内部编号泄露、正文 placeholder token、实验 table/metric 锚点、数字可追溯、AI 套话等；当 `venue_style=both` 时也会审计两套风格变体。Abstract 不放正式引用；citation key 真实性由 `audit_manuscript_claims` 负责。
- `build_manuscript_revision_patches`：把 reviewer issue 定位成 section patch list。

这些工具只处理机械重复、可解析、可校验的工作；论文贡献判断、理论定位、gap 表达、section prose 和修订取舍仍由 LLM 完成。

### 6.12.1 T8-STYLE-GATE：WriterAgent（style_gate）

`T8-STYLE-GATE` 读取 `project.yaml` 的 `target_venue`，再根据 [config/system_config/venue_style_map.yaml](../config/system_config/venue_style_map.yaml) 给出默认建议：IS 顶刊风格或 CCF-A 会议风格。Writer 调用 `ask_human`，让用户选择 `is`、`ccf_a` 或 `both`，并写 `drafts/writing_style.json`。该 JSON 必须包含 `human_interaction_id`，并且这个 id 必须能在 `_runtime/human_interactions.jsonl` 中找到；这样可以区分“用户真实选择”和“模型自己编造的默认选择”。如果 stdin 不可交互或回答为空，`ask_human` 返回 recoverable pause，runtime 会暂停等待 `researchos resume`，不会继续跑后续工具。

如果当前环境没有可用人工输入，runtime 会暂停在该 run，等待用户 resume 或预置合法 `drafts/writing_style.json`；Writer 不会伪造默认选择。后续 prompt 从 `writing_style.json` 读取 `venue_style`。风格只影响篇幅分配和叙事重心，不改变 alignment matrix 的贡献骨架。

`venue_style=both` 的当前实现语义是：资源索引、CDR/claim/figure registry、alignment matrix、section files 共用一套，`T8-DRAFT` 在拼装主 `drafts/paper.tex` 后额外派生 `drafts/is/paper.tex` 与 `drafts/ccf_a/paper.tex`，并为两套变体分别生成 craft audit。派生文件只是入口，Writer 必须用 LLM 对两套稿件做风格化改写，并分别写 `drafts/is/style_revision_notes.md` 与 `drafts/ccf_a/style_revision_notes.md` 说明改写取舍；validator 会拒绝“两个变体只是主稿加注释”的假双稿。这样既不把学术写作知识硬编码进 tool，也保证双稿件有实际产物和校验入口。

### 6.12.2 T8-RESOURCE：WriterAgent（resource_index）

`T8-RESOURCE` 输入包括 `project.yaml`、`literature/synthesis.md`、`literature/synthesis_workbench.json`、`literature/domain_map.json`、`literature/related_work.bib`、`ideation/hypotheses.md`、`ideation/idea_scorecard.yaml`、`experiments/results_summary.json`，以及可选的 `exp_plan.yaml`、`novelty_audit.md`、`comparison_table.csv`、`ablations.csv`。

其中 `writing_style`、`synthesis_workbench`、`domain_map` 和 `idea_scorecard` 是单任务强前置，不再只是“强烈推荐”。`researchos run-task T8-RESOURCE --from <workspace>` 会复制这些 artifact；如果缺失，前置校验会要求先补齐 T8-STYLE-GATE 或 Pre-T5 产物。这样 Related Work 和 alignment matrix 不会因为单阶段调试而失去 venue 风格、`adjacent_transfers`、`nearest_prior_work`、`counterfactual_check` 和 `novelty_signal`。

实际执行顺序固定：

1. 调用 `build_manuscript_resource_index`，扫描 literature、paper notes、bib、hypotheses、idea scorecard、novelty audit、results summary、ablations、runs、configs、figures、tables 和 code artifacts，写 `drafts/manuscript_resource_index.json`。
2. 调用 `plan_manuscript_sections`，写 `drafts/section_plan.json`。当前主链规划 7 章：abstract、introduction、related_work、methodology、experiments、analysis、conclusion；Conclusion 的 required inputs 合并了原 Limitations 所需的 risks、novelty audit 和 iteration log。
3. 调用 `plan_manuscript_evidence`，写 `drafts/evidence_plan.json` 和 `drafts/figure_table_plan.json`。
4. 调用 `build_manuscript_registries`，写 `drafts/cdr_claim_ledger.json`、`drafts/claim_ledger.json`、`drafts/figure_registry.json`。
5. 调用 `build_alignment_matrix`，从 `cdr_claim_ledger.json` 的 `contribution_chains`、evidence plan、figure/table plan、synthesis、hypotheses 和 idea scorecard 生成 `drafts/alignment_matrix.json`。它会把 `idea_scorecard.yaml` 中的 `counterfactual_check`、`counterfactual_note`、`nearest_prior_work` 和 `novelty_signal` 带入每条 contribution lane，作为 Writer 论证“为什么不是简单增量”和 Related Work 差异化定位的 hint。如果没有 contribution lanes，工具才回退生成 3-4 条 `LLM_REVIEW_REQUIRED` lane；这只是恢复兜底，不是最终贡献判断。

Validator 会要求 resource 阶段产物包含 resource index、section/evidence/figure plans、三个 registry 和 alignment matrix，并检查关键 semantics 和最小结构。Alignment matrix 必须有 rows，后续 outline 阶段还会检查它是 3-5 条贡献 lane，而不是任意数量的 evidence slot。进入正文写作前，`WriterAgent` 会拒绝关键 alignment 字段仍停留在 `LLM_REVIEW_REQUIRED` / `TODO` / `TBD`：这些占位可以作为 T8-RESOURCE 的 seed，但不能作为已确认的跨章节贡献链。

### 6.12.3 T8-WRITE：WriterAgent（outline）

`T8-WRITE` 读取 resource index、section plan、evidence plan、figure/table plan、CDR ledger、claim ledger、figure registry、alignment matrix、synthesis、hypotheses、results 和 bib 预览，写 `drafts/outline.md`。

这一步是 LLM 学术判断阶段。Writer 需要把 `alignment_matrix.json` 中的每条 cid 补成可写作的链路：motivation、contribution、related_gap、design_choice、experiment、analysis。工具只给 seed，例如候选 metric、citation pool、图表 label 和 CDR field；gap 是否成立、贡献如何措辞、IS/CCF-A 风格怎么讲，由 Writer 阅读上游 artifact 后决定。

大纲必须包含标题候选、paper thesis、contribution map、section-by-section argument、figure/table plan、claim ledger 和 alignment matrix refinement。Validator 检查 `outline.md` 不是空文件、包含 `##` 结构，覆盖 Introduction、Related Work、Method、Experiments 等核心章节，并要求 `alignment_matrix.json` 保持合法 semantics、3-5 条 rows、每行具备 cid、motivation、contribution、related_gap、design_choice、experiment 和 analysis 字段。字段可以在 T8-RESOURCE seed 中临时带 `LLM_REVIEW_REQUIRED`，但 Writer 必须在 outline / section-plan 前把关键字段补成可写作的学术判断；否则后续校验会暂停，不允许把占位符带进正文。

### 6.12.4 T8-SECTION-PLAN：WriterAgent（section_plan）

`T8-SECTION-PLAN` 不写正文。Writer 调用：

```text
initialize_manuscript_state(
  outline_path="drafts/outline.md",
  resource_index_path="drafts/manuscript_resource_index.json",
  section_plan_path="drafts/section_plan.json",
  evidence_plan_path="drafts/evidence_plan.json",
  figure_table_plan_path="drafts/figure_table_plan.json",
  alignment_matrix_path="drafts/alignment_matrix.json",
  state_output_path="drafts/paper_state.json",
  section_outline_dir="drafts/section_outlines",
  target_venue=<target_venue>
)
```

该工具写 `drafts/paper_state.json`，其中包括 `section_order`、每章目标文件、required/available/missing inputs、`shared_facts.bib_keys`、`shared_facts.result_metrics`、claim slots、planned visuals 和 `shared_facts.alignment_matrix`。同时它写每章 `drafts/section_outlines/<section>.md`，每个 outline 都包含 Purpose、Required Inputs、Responsible CIDs、Claim Slots、Figure/Table Slots 和 Writing Rules。

Orchestrator 在进入 LLM 前有确定性恢复逻辑：如果 `outline/resource/section/evidence/figure/alignment` 计划文件已存在，但 `paper_state.json` 或 section outlines 不合格，会直接调用 `initialize_manuscript_state` 修复并跳过 LLM。这保证 resume 不会在 T8-SECTION-PLAN 反复消耗模型重写机械状态。

### 6.12.5 T8-SEC-METHOD：WriterAgent（section_draft, section_id=methodology）

`T8-SEC-METHOD` 只写 `drafts/sections/methodology.tex`。Writer 读取 `paper_state.json`、`section_outlines/methodology.md`、`alignment_matrix.json` 的 design_choice 列、`ideation/hypotheses.md`、`ideation/exp_plan.yaml`、`ideation/idea_scorecard.yaml`、novelty/CDR tuples、实验 configs 和可用 code artifacts。

本章先定义 artifact 和 design rationale，再讲整体架构、组件、notation、算法和实现。每个设计选择都要解释 why，不用实验结果证明方法有效；贡献对应关系用自然语言闭环，内部追踪只保存在 `paper_state.json` / `alignment_matrix.json`。写完必须调用 `update_manuscript_section_state(section_id="methodology", status="written")`。

### 6.12.6 T8-SEC-EXPERIMENTS：WriterAgent（section_draft, section_id=experiments）

`T8-SEC-EXPERIMENTS` 只写 `drafts/sections/experiments.tex`。Writer 读取 `alignment_matrix.json` 的 experiment 列、`results_summary.json`、`ablations.csv`、runs/configs、seed ensemble、`exp_plan.yaml` 和 `figure_table_plan.json`。

本章要把 RQ 前置，并用自然语言说明每个 RQ 验证哪类贡献逻辑；随后写 setup、datasets、baselines、metrics、seeds、compute、main results 和 ablations。所有数字必须来自结果 artifact；缺 seed/error bar/baseline 时删除或弱化对应强 claim，或转入 Conclusion 的 Limitations 子段，不能编造，也不能在最终 TeX 中留下 literal TODO。写完更新 `paper_state.json` 中 `experiments.status`。

### 6.12.7 T8-SEC-RELATED：WriterAgent（section_draft, section_id=related_work）

`T8-SEC-RELATED` 只写 `drafts/sections/related_work.tex`。Writer 读取 `alignment_matrix.json` 的 related_gap、`literature/synthesis.md`、`literature/synthesis_workbench.json`、`literature/domain_map.json`、`ideation/idea_scorecard.yaml`、`comparison_table.csv`、paper notes 和 `related_work.bib`。在 task contract 中，`synthesis_workbench`、`domain_map`、`idea_scorecard` 和 `alignment_matrix` 都是强前置；缺失时应先回到 T3.5/T4/T8-RESOURCE 修复。

Related Work 按 competing design rationale 组织，不按论文流水账。每个主题 subsection 应说明该流派共同 rationale、代表工作、共同局限或 tension，然后用自然语言落到本文对应的 gap 或 design choice，不暴露内部 cid。`synthesis_workbench.adjacent_transfers` 和 `bridge_transfer_drafts` 用来识别邻接/理论桥接的可迁移机制，`domain_map.core/theory_bridge/adjacent/citation_edges` 用来提示主干与邻接结构，`idea_scorecard.cross_domain_sources`、`idea_scorecard.nearest_prior_work` 和 alignment matrix 的 `nearest_prior_work` 用来做最近工作差异化定位，`counterfactual` / `novelty_signal` 只作为 marginal 风险提示。citation key 必须存在于 `.bib`；工具只给 citation pool 和结构化 hint，prior-work positioning 由 LLM 判断。

`T8-DRAFT` 的 `audit_writing_craft` 会做一个非阻断的 `related_work_pre_t5_signal_consumption` 检查：如果 Related Work 完全看不到 nearest-prior-work、adjacent-transfer、cross-paper tension 或对应文本片段，就给 WARN。它不会替代 LLM 判断某篇工作是否相关，只提示“上游花资源生成的 Pre-T5 素材可能没被写作消费”。

### 6.12.8 T8-SEC-ANALYSIS：WriterAgent（section_draft, section_id=analysis）

`T8-SEC-ANALYSIS` 只写 `drafts/sections/analysis.tex`。Writer 读取 `alignment_matrix.json` 的 analysis 列、已写 Method/Experiments、`ablations.csv`、`iteration_log.md` 和 `novelty_audit.md`。

本章解释实验和消融如何支持、削弱或仅部分支持 design rationale；至少提出并排除一个 alternative explanation，呈现 failure case 和 sensitivity。不能把未做的 T5/T6 或额外实验写成已完成。分析段落必须用自然语言闭环贡献逻辑，不使用内部 cid 注释。

### 6.12.9 T8-SEC-INTRO：WriterAgent（section_draft, section_id=introduction）

`T8-SEC-INTRO` 在 Method、Experiments、Related Work 和 Analysis 之后运行，只写 `drafts/sections/introduction.tex`。Writer 读取 `alignment_matrix.json` 的 motivation/contribution、CDR ledger、synthesis、hypotheses、results，以及已写 Method/Experiments/Related Work。

Introduction 采用 5-move：Problem、Gap、Approach、numbered Contributions、venue-specific closing。gap/motivation 通常不超过 3 个，contribution 3-4 条，并应和 alignment matrix 的内部 lane 形成清晰逻辑对应；这种对应关系只写在 `paper_state.json` / `alignment_matrix.json`，正文贡献 bullet 使用自然语言。`ccf_a` 风格需要量化 results headline；`is` 风格需要理论或 reference anchor。Intro 不能超过已有 evidence。

### 6.12.10 T8-SEC-CONCLUSION：WriterAgent（section_draft, section_id=conclusion）

`T8-SEC-CONCLUSION` 只写 `drafts/sections/conclusion.tex`，同时承担 Limitations。Writer 读取 `alignment_matrix.json`、Introduction、Experiments、`ideation/risks.md`、`novelty_audit.md`、`iteration_log.md` 和 `paper_state.json`。

Conclusion 先收束本文证明了什么和可迁移 design knowledge，然后必须写 `\subsection{Limitations}`。Limitations 子段要具体说明外部执行器证据边界、mock/dry-run 是否仅为协议测试、baseline 覆盖、数据规模、外部有效性、compute/seed 和复现风险。Conclusion 不允许引入新 claim、新数字或新引用；如果需要新信息，应回到对应章节和 artifact。

### 6.12.11 T8-SEC-ABSTRACT：WriterAgent（section_draft, section_id=abstract）

`T8-SEC-ABSTRACT` 最后运行，只写 `drafts/sections/abstract.tex`。Writer 读取 `paper_state.json`、`section_outlines/abstract.md`、`alignment_matrix.json` 和已写的 introduction/methodology/experiments/analysis/conclusion。

Abstract 用 5 句骨架压缩全文：Problem、Gap、Approach、Key result、Contribution type。`drafts/sections/abstract.tex` 只能写摘要纯正文，不能写 `\section{Abstract}`、`\section*{Abstract}`、`\begin{abstract}` 或 `\end{abstract}`；`assemble_manuscript` 会负责最终 abstract 环境。它不放正式引用：不使用 LaTeX citation command，不写作者-年份括号引用，也不写数字引用；具体 prior work citation 放到 Introduction 或 Related Work。它也不能引入正文没有的数字、claim 或术语。`ccf_a` 风格通常 150-300 词，`is` 风格通常 200-300 词。

### 6.12.12 T8-DRAFT：WriterAgent（draft）

`T8-DRAFT` 先调用 `assemble_manuscript(section_dir="drafts/sections", output_path="drafts/paper.tex", outline_path="drafts/outline.md", target_venue=<target_venue>, venue_style=<venue_style>)`。该工具按 Introduction、Related Work、Method、Experiments、Analysis、Conclusion 顺序拼装正文，把 Abstract 放入 `abstract` 环境，并自动加入 `\documentclass`、基础 package、title 和 `\bibliography{related_work}`。如果旧 workspace 残留 `drafts/sections/limitations.tex`，assemble 会把它合并到 Conclusion 的 `\subsection{Limitations}`，不会生成独立 `\section{Limitations}`。如果 `venue_style=both`，assemble 还会派生 `drafts/is/paper.tex` 和 `drafts/ccf_a/paper.tex`，两者共享同一 alignment matrix 和 section source，作为后续风格化 revision 的入口；随后 Writer 需要分别改写这两个文件，使 IS 稿更强调 theory/design knowledge/validity，使 CCF-A 稿更强调紧凑 problem framing、量化结果和可复现实验。

随后 Writer 做全局 spot-check：术语、变量名、baseline 名称、章节衔接、Intro/Conclusion 呼应、Method/Experiment setup 一致性。需要改正文时先改对应 `drafts/sections/<section>.tex`，再重新 assemble。

最后必须调用两个审计工具：

- `audit_manuscript_claims(paper_path="drafts/paper.tex", output_path="drafts/manuscript_audit.md")`：检查 citation key、数字、figure/table refs 和核心章节。
- `audit_writing_craft(paper_path="drafts/paper.tex", sections_dir="drafts/sections", paper_state_path="drafts/paper_state.json", alignment_matrix_path="drafts/alignment_matrix.json", venue_style=<venue_style>, output_path="drafts/craft_audit.md")`：检查独立 Limitations、Abstract 正式引用、Abstract section heading、内部编号泄露、正文 placeholder token、每个内部 lane 的 experiment table/metric/ablation 锚点、related-work orphan/laundry-list、AI 套话、贡献条数和数字可追溯，并同时写 `drafts/craft_audit.json`。`abstract_no_cite`、`abstract_no_section_heading`、`no_internal_label_leakage`、`no_placeholder_tokens`、`number_traceability`、独立 Limitations、缺 experiment artifact 等机械可查问题是 FAIL；贡献条数和 abstract wordcount 是 WARN。placeholder 检测覆盖 `TODO/TBD/PLACEHOLDER/LLM_REVIEW_REQUIRED` 以及自然语言 `LLM review required`，不是只查大写 token。

Validator 要求 `paper.tex`、`manuscript_audit.md`、`craft_audit.md` 和 `craft_audit.json` 存在，并检查 LaTeX wrapper、必要章节、BibTeX key、关键 craft check 是否存在且没有 FAIL。如果 `writing_style.json` 选择 `both`，还要求 `drafts/is/paper.tex`、`drafts/is/craft_audit.json`、`drafts/is/style_revision_notes.md`、`drafts/ccf_a/paper.tex`、`drafts/ccf_a/craft_audit.json` 和 `drafts/ccf_a/style_revision_notes.md` 存在；去掉 ResearchOS 注释后，两个变体不能与主稿正文完全相同。

### 6.12.13 T8-SELF-CHECK：WriterAgent（self_check）

`T8-SELF-CHECK` 读取 `paper.tex`、`manuscript_audit.md`、`craft_audit.md`、`alignment_matrix.json`、`results_summary.json` 和 `related_work.bib`，写 `drafts/self_check.md`。

自查包括 argument chain、number audit、citation audit、figure/table audit、reproducibility audit、外部执行器证据边界和 revision TODO。`craft_audit.md` / `paper_claim_audit.md` 的 FAIL 必须进入 High TODO，WARN 进入 Medium TODO，并说明是否已在正文处理。

### 6.12.14 T8-REVIEW-1 / T8-REVIEW-2：ReviewerAgent

Reviewer 先读取 `paper_state.json`、`sections/*.tex`、`paper.tex`、`manuscript_audit.md`、`craft_audit.md`、`alignment_matrix.json`、`self_check.md`、`results_summary.json` 和 `.bib`。它对 7 个 section 逐章生成：abstract、introduction、related_work、methodology、experiments、analysis、conclusion。没有独立 `limitations.md` review；Conclusion review 必须检查 Limitations 子段。

每个逐章 review 至少包含 Section Purpose Check、Evidence And Number Check、Logic And Writing Issues、CDR Alignment Check、Alignment Matrix Check、Writing Craft Check 和 Actionable Fixes。综合 `round_N.md` 至少包含 `## 总体评价`、`## 主要问题`、`## 次要问题`、`## 写作范式与对齐核查` 和 `## CDR Contribution Verdict`。第二轮还要检查上一轮 High/Medium 问题是否闭环。

### 6.12.15 T8-REVISE-1 / T8-REVISE-2：WriterAgent（revise）

`WriterAgent(revise)` 先调用 `build_manuscript_revision_patches(round_num=N)`，把综合 review 和逐章节 review 解析成 `drafts/patches/round_N_patches.json`。Patch list 只定位 issue，不替代 LLM 的修订判断。

Writer 按 High -> Medium -> Low 处理 patch。能定位到 section 的问题，只读取并修改对应 `drafts/sections/<section>.tex`、`paper_state.json` 和必要证据文件，然后调用 `update_manuscript_section_state(status="revised")`。global patch 优先拆成多个 section 修改。所有 patch 完成后重新 `assemble_manuscript`，再刷新 `manuscript_audit.md` 和 `craft_audit.md`，最后写 `drafts/revision_response_round_N.md` 记录 resolved、unresolved、deferred。

### T8 的恢复语义

T8 恢复依赖已有 artifact：

- `T8-STYLE-GATE` 只有在已有合法 `writing_style.json` 时可跳过；坏 JSON 或非法 `venue_style` 会回到 gate。
- `T8-RESOURCE` 重跑会更新资源索引、计划、registry 和 alignment matrix，不删除已有章节。
- `T8-SECTION-PLAN` 可用 deterministic recovery 修复 `paper_state.json` 和 section outlines。
- 每个 `T8-SEC-*` 只补写或修订自己的 section，不改其它 section。
- `T8-DRAFT` 从 section files 重建 `paper.tex`，如需改正文先回改 section；`venue_style=both` 会派生两套风格变体入口并生成对应 craft audit，Writer 必须实际改写变体并刷新审计，不能只依赖工具复制。
- `T8-REVISE-*` 按 patch list 修 section，再刷新 `paper.tex`、`manuscript_audit.md`、`craft_audit.md` 和 `craft_audit.json`；如果是 `both`，也刷新两套变体审计。

### T8 单独运行 vs 完整运行

单独调试建议从 `T8-STYLE-GATE` 或 `T8-RESOURCE` 开始；如果已有合法 `writing_style.json`，可直接跑 `T8-RESOURCE`。完整 `run/resume` 会按状态机走完 `T8-STYLE-GATE -> T8-RESOURCE -> T8-WRITE -> T8-SECTION-PLAN -> T8-SEC-* -> T8-DRAFT -> T8-SELF-CHECK -> ...`。

单任务调试时，`--from <upstream-workspace>` 会按 task contract 复制所有声明输入；其中 `T8-RESOURCE` 会复制 `domain_map.json`、`synthesis_workbench.json`、`idea_scorecard.yaml`，`T8-SEC-RELATED` 也会复制这三者和 `alignment_matrix.json`。这些不是装饰性输入，而是 Related Work 和 alignment matrix 的强前置。缺失时前置校验会失败，避免写作链退化成只读 `synthesis.md` 和 `.bib` 的 prompt-only 写法。

旧 `researchos run-task T8-SECTIONS --workspace ...` 仍被 CLI 接受，但会映射到 `T8-SECTION-PLAN`；旧 `T8-SEC-LIMITATIONS` 会映射到 `T8-SEC-CONCLUSION`。

### T8 常用单任务示例

```bash
cd ResearchOS
researchos run-task T8-STYLE-GATE --workspace ./workspace/local-test2
researchos run-task T8-RESOURCE --workspace ./workspace/local-test2
researchos run-task T8-WRITE --workspace ./workspace/local-test2
researchos run-task T8-SECTION-PLAN --workspace ./workspace/local-test2
researchos run-task T8-SEC-METHOD --workspace ./workspace/local-test2
researchos run-task T8-SEC-EXPERIMENTS --workspace ./workspace/local-test2
researchos run-task T8-SEC-RELATED --workspace ./workspace/local-test2
researchos run-task T8-SEC-ANALYSIS --workspace ./workspace/local-test2
researchos run-task T8-SEC-INTRO --workspace ./workspace/local-test2
researchos run-task T8-SEC-CONCLUSION --workspace ./workspace/local-test2
researchos run-task T8-SEC-ABSTRACT --workspace ./workspace/local-test2
researchos run-task T8-DRAFT --workspace ./workspace/local-test2
researchos run-task T8-SELF-CHECK --workspace ./workspace/local-test2
researchos run-task T8-REVIEW-1 --workspace ./workspace/local-test2
researchos run-task T8-REVISE-1 --workspace ./workspace/local-test2
```

---

## 6.14 T9：SubmissionAgent

### 角色

- Agent：`SubmissionAgent`
- 代码： [researchos/agents/submission.py](../researchos/agents/submission.py)
- Prompt： [researchos/prompts/submission.j2](../researchos/prompts/submission.j2)

### 当前默认配置

T9 的日常预算、模型和重试行为以 `config/user_settings.yaml` 为入口；checked-in 默认
`budget.defaults.unlimited_budget: true` 时不会因 step/token/wall 暂停。`config/agent_params.yaml`
中的 `submission.behavior.max_compile_attempts` 是 LaTeX 编译 attempt/cache 的行为参数，
不是普通 LLM 预算。匿名化 pre-hook 由 `submission.behavior.enforce_anonymization_precheck`
显式控制。

主要工具：

- `read_file`
- `write_file`
- `list_files`
- `bash_run`
- `docker_exec`
- `latex_compile`
- `finish_task`

T9 进入 LLM 前会先做编译环境 preflight：如果本机已有 `latexmk`，可以直接走本机
TeX；否则检查 Docker 命令、daemon 和统一镜像 `researchos/system:latest`。如果两者都
不可用，任务会以 `WAITING_ENVIRONMENT` 暂停，`state.yaml` 保持 `PAUSED`，安装 TeX
或配置 Docker 后可直接 `resume`，不会先消耗 LLM 步数再失败。

例外：runtime 会在环境 preflight 和 LLM 前先尝试 `t9_submission_prefinalize`。如果
`submission/bundle/` 已有 `main.tex`、`references.bib`、`main.pdf`、`main.log`、
`bundle_manifest.json`，且 `submission/compile_report.json` 与当前文件 hash/mtime/size
全部一致，`SubmissionAgent.validate_outputs()` 会直接通过并跳过环境检查和 LLM。这个
prefinalize 不是单纯复用旧 PDF：`bundle_manifest.json` 必须证明 bundle 来源仍对应当前
`drafts/paper.tex` 和 `literature/related_work.bib`。

当前机器的 Docker root 已迁移到 `/mnt/data/Docker`；宿主机无 `latexmk` 时，T9 会默认走 Docker 编译。可用 `docker info --format '{{.DockerRootDir}}'` 确认路径，用 `docker image inspect researchos/system:latest` 确认统一镜像是否存在。

**匿名化 precheck 默认关闭**（`submission.py` line 83: `enforce_anonymization_precheck` 默认 `False`）。只有当 `agent_params.yaml` 中 `submission.behavior.enforce_anonymization_precheck` 设为 `true` 时，才会在进入 LLM 前拦截检查邮箱、URL、GitHub 等匿名化问题。这便于本地调试或非匿名投稿场景直接产出投稿包。

**Venue 模板支持**：T9 从 `project.yaml` 的 `target_venue` 字段（默认 `neurips2026`）读取目标会议格式，迁移主稿到对应模板。

`latex_compile` 的缓存也按依赖 fingerprint 判定：除了 `main.tex` hash，还会记录 bundle
内非生成文件（例如 `references.bib`、figures、`.sty/.cls`、`bundle_manifest.json`）
的 dependency fingerprint。成功缓存只有在 PDF/log/hash/mtime/size 和依赖 fingerprint
全部一致时复用；同一 fingerprint 的源级失败不会重复编译，必须修改 TeX 或依赖后再试。

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| - | `drafts/paper.tex` | 是 | 论文主稿 |
| - | `literature/related_work.bib` | 是 | 引用库 |
| - | `project.yaml` | 是 | 目标 venue、匿名化策略等 |
| - | `drafts/figures/` | 否 | 图表 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `bundle_dir` | `submission/bundle/` | 投稿包目录 |
| - | `submission/migration_report.md` | 迁移和编译报告 |
| - | `submission/bundle/main.tex` | 投稿主稿 |
| - | `submission/bundle/references.bib` | 投稿引用库 |
| - | `submission/bundle/main.pdf` | 编译成功后的 PDF |
| - | `submission/bundle/main.log` | 编译日志 |
| - | `submission/compile_report.json` | LaTeX 编译尝试报告，记录 engine、attempts、hash、mtime 和成功状态 |

### T9 现在到底做什么

当前 T9 已经不是“试着打包一下就结束”，而是：

1. 读取主稿与项目配置
2. 首先调用确定性工具 `prepare_submission_bundle(paper_path="drafts/paper.tex", bib_path="literature/related_work.bib")`
   - 把 `drafts/paper.tex` 复制为 `submission/bundle/main.tex`
   - 把 `literature/related_work.bib` 复制为 `submission/bundle/references.bib`
   - 将 `\bibliography{...}` 统一重写为 `\bibliography{references}`
   - 复制被主稿引用、read policy 允许且后缀为 `.pdf/.png/.jpg/.jpeg/.svg` 的图表到 `submission/bundle/figures/`，目标文件名带内容 hash，避免同名覆盖
   - 这一步是机械文件准备，不交给 LLM 手写路径，避免 “paper.tex 引用 related_work.bib 但 bundle 中没有该 bib” 的编译失败
3. 迁移到目标会议模板或在 bundle 内补模板文件
4. 尝试编译
   - 优先调用 `latex_compile(tex_path="submission/bundle/main.tex")`
   - `latex_compile` 在容器内或宿主机有 `latexmk` 时直接本机编译
   - 宿主机无 TeX 时使用统一 Docker 镜像 `researchos/system:latest`
   - 成功或失败都会写 `submission/compile_report.json`
5. 如果编译失败：
   - 读 `.log`
   - 定位错误
   - 修改 `main.tex` / style / bib / figures
   - 重新编译
6. 编译成功后检查文件是否齐全
7. 写 `migration_report.md`

### 编译-修复-重试闭环

当前 prompt 明确要求：

- 编译失败不能直接 `finish_task`
- 必须先诊断并修复
- 最多进行 `submission.behavior.max_compile_attempts` 轮
- `latex_compile` 是工具层护栏：`submission/compile_report.json.attempts` 会保留历史；如果 `main.tex` hash、engine、bibtex、output_dir 都没有变化，已有成功 PDF 会直接复用，已有源级失败会返回 `cached_compile_failure_same_tex`，要求先修改 TeX 再重试，避免 LLM 对同一错误反复编译。

当前配置默认：

- `submission.behavior.max_compile_attempts = 10`

### T9 当前的 validator 有多严格

现在 validator 强制要求：

- `submission/bundle/` 存在
- `main.tex` 存在
- `references.bib` 存在
- `main.pdf` 存在
- `main.pdf` 必须有 `%PDF` 文件头且不是极小占位文件
- `main.pdf` 的 mtime 不能早于 `main.tex`，避免旧 PDF 假通过
- `main.log` 必须存在
- `submission/compile_report.json` 必须存在，`semantics` 必须是 `latex_compile_attempt_report`
- `compile_report` 的最后一次 attempt 必须成功，且 `tex_path/pdf_path/log_path` 分别指向 `submission/bundle/main.tex`、`main.pdf`、`main.log`
- `compile_report.attempts` / `attempt_count` 会记录历史编译尝试；同一 `main_tex_sha256` 的重复失败不会再次执行外部编译，修改 TeX 后才会追加新 attempt
- `compile_report.main_tex_sha256`、`pdf_sha256`、`log_sha256`、`pdf_mtime`、`log_mtime`、`pdf_size`、`log_size` 必须与当前文件一致，避免旧 PDF 或伪造报告通过
- `submission/bundle/main.tex` 会在 T9 validator 中再次扫描 `TODO/TBD/PLACEHOLDER/LLM review required` 和 `C1/CID/internal alignment` 泄露；即使 T8 源稿 craft audit 通过，模板迁移阶段引入这些脏标记也会失败。
- `migration_report.md` 存在
- 报告里必须明确写出：
  - `迁移状态`
  - `编译状态`
  - `匿名化检查`
- 报告里必须出现：
  - `编译状态: 成功`
- `main.log` 不能还有 fatal error 标记
- `main.log` 仍包含未解析引用/未定义 reference 的标记，也不能通过

`validate --task T9` 现在也复用 `SubmissionAgent.validate_outputs()`，不是只看
`bundle/` 和报告文件是否存在。

### T9 的 Submission Gate

T9 编译成功后、validation 之前，状态机会触发一个 `submission_gate`（类型 `submission_gate`），提示用户审核：

- `migration_report.md` 中的迁移详情
- `submission/bundle/` 中的文件
- 匿名化检查结果

这是一个人工确认点，确保投稿包在提交前经过用户审核。

### T9 失败后的回退路径

状态机中 T9 配置了 `next_on_failure: T8-WRITE`。当 T9 编译失败且 `submission.behavior.max_compile_attempts` 耗尽时：

1. T9 的 `finish_task` 会触发 validator 检查
2. runtime 会先弹 `runtime_validation_retry_extension` gate，允许用户增加少量修复轮次继续 T9
3. 如果用户选择暂停、输入不可用或扩展次数耗尽，本轮以 `PAUSED/INTERRUPTED` 结束，保留当前 task 以便 `resume`
4. 只有当任务真正以不可恢复 failure 交给状态机处理时，状态机才按 `next_on_failure` 回到 `T8-WRITE`

这个设计的意图是：短小的 LaTeX 修复留在 T9 继续处理；如果编译问题源于论文内容本身（如缺失引用、格式错误、LaTeX 语法问题），再回到 T8 让 Writer/Reviewer 修复内容比让 SubmissionAgent 无限重试更合理。

### T9 为什么可能慢

因为它不是只写报告，而是可能会：

- 读全文
- 改 bundle
- 跑多轮编译
- 解析日志
- 再修

### T9 单独运行示例

```bash
cd ResearchOS
researchos run-task T9 --workspace ./workspace/local-test2
```

---

## 7. 贯穿所有 Agent 的附加机制

## 7.1 恢复机制

当前恢复已经不是只有某一两个阶段有，而是多处接入：

- 通用恢复：
  - `_runtime/resume/*.json`
- T3：
  - `deep_read_queue_pending.jsonl`
- T5 外部执行器：
  - `external_executor/executor_selection.json`
  - `external_executor/result_pack.json`
  - `external_executor/executor_status.json`
  - `external_executor/run_manifest.json`
  - `external_executor/wait_rejection_report.md`
  - `external_executor/wait_acceptance_report.json`
- legacy T5/T7 内部实验调试：
  - `pilot_resume_state.json`
  - `full_resume_state.json`
- T7.5 / T8 / T9：
  - 读取已有产物并补缺

### 真实含义

恢复不是“从上次模型上下文继续”，而是：

- 扫描已有 workspace
- 识别已完成与未完成的 artifact
- 只补剩余工作

跨 workspace 重跑不是 `resume`。如果旧 workspace 的 T2 出了问题，但 T1、用户 seed 和
bridge plan 仍可信，应创建新 workspace 并从 T2 启动完整主链：

```bash
researchos run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

省略 `--start-task` 时，`run --from` 默认从 `T2` 开始。它只复制目标 task 的前置输入，
不会复制旧 T2 输出；目标 workspace 已有 `state.yaml` 时会拒绝覆盖。单 task 调试才用
`run-task <TASK> --from <workspace>`。

## 7.2 预算扩限 gate

当前系统支持预算触顶时不立即死停，而是先问是否扩限继续。

主要覆盖：

- steps
- tokens
- wall_seconds

这对：

- T5
- T7
- T9

这类长任务尤其重要。

交互细节：

- CLI gate 接受数字选择，也接受常见别名，例如 `继续` / `确认` / `extend`，以及 `停止` / `stop`
- `ask_human` 是 agent 工具级的人类输入；如果当前 stdin 不可交互或已关闭，runtime 会暂停任务，而不是把空输入当成用户选择继续喂给 LLM
- `ask_human` 的 CLI 输入完成后用单独一行 `END` 或 Ctrl+D 提交；如果误触提交空回答，CLI 会最多重试 3 次；连续空回答才会进入可恢复暂停
- 非空回答提交后会立即打印 `已收到输入，继续处理...` 和一整行 `-----` 分隔线，让用户知道输入已经被 runtime 接收
- 每个 task/agent 开始时会输出一整行 `==== <task_id> | <agent_name> ==== ` 风格分隔线，随后输出任务目标、阶段、预期产物、模型层级和最大步数，便于在长 pipeline 输出中判断当前切换到了哪里
- 如果模型明确向用户索取选择/确认/补充信息但忘记调 `ask_human`，runtime 会自动桥接成 `ask_human`，并把“为什么弹出输入框”写在问题开头；状态说明或内部计划不会触发桥接
- 预算 gate 的等待时间会从 wall-clock budget 中扣除，避免“等用户输入”本身把任务预算耗尽
- `max_steps` 在循环尾部触顶时也会进入同一套扩限 gate；用户选择停止或无法继续输入时，状态会写成 `PAUSED`，history 中本轮 run 标记为 `INTERRUPTED`，后续可以 `researchos resume --workspace ...`
- 当前 checked-in `config/user_settings.yaml` 已在 `budget.defaults.unlimited_budget`
  设置 `true`，因此默认不会因为 agent runtime 的 `max_steps`、`max_tokens_total`、
  `max_wall_seconds` 暂停，也不会触发预算扩限 gate；但 step/token/cost 仍会记录，
  LLM 单次超时、工具超时、Docker/TeX 专用超时、workspace 权限、输出校验和项目级实验预算检查仍然生效。
  若需要恢复有限预算，在 `config/user_settings.yaml` 或 task/mode override 中写
  `budget.unlimited_budget: false`。`config/agent_params.yaml` 是工具、权限、prompt 和
  behavior 能力声明，不是日常预算入口。
- 如果进程异常退出导致 `state.yaml` 停在 `RUNNING`，`resume` 会把最近一次 run 标为
  `INTERRUPTED` 并自动转回 `PAUSED` 后继续，避免“当前状态不是 PAUSED/WAITING_HUMAN，
  无法 resume”的死状态。
- `resume` 后不会恢复模型内部上下文，而是通过 `_runtime/resume/*.json`、T3 pending queue、已有输出文件和 task-specific recovery artifact 注入 `resume_mode`，让 agent 从已落盘事实继续
- 对 T8 来说，`resume` 先看 `state.yaml.current_task`：如果当前 task 是 `T8-SECTION-PLAN` 且状态文件已合格，会确定性跳过；如果当前 task 已推进到某个 `T8-SEC-*`，只恢复该单章，不会回退重写 section plan 或其它章节
- agent 调用 `finish_task` 后如果输出校验连续失败到上限，runtime 会先触发 `runtime_validation_retry_extension` gate，询问是否增加少量校验修复轮次继续；用户选择暂停、无交互输入或扩展次数耗尽时，任务会暂停为可恢复状态而不是直接 `FAILED`。错误信息会保留最后一次校验失败原因，后续 `resume` 可从已有 artifact 做定向修复
- 任意退出路径都会刷新通用 `_runtime/resume/<task>_resume_state.json`；T3 还会同步刷新 pending queue/meta，所以暂停或失败后的恢复提示不再依赖旧快照
- `docker_exec` 和 `latex_compile` 不再受普通工具 `max_tool_call=180s` 的小上限截断：
  `docker_exec` 使用 `global_timeout.docker_operation`，`latex_compile` 使用
  `global_timeout.latex_compile`。

## 7.3 LLM fallback

LLM 路由由：

- [config/model_routing.yaml](../config/model_routing.yaml)

控制。

现在的重要行为是：

- 同一轮里先尝试 primary
- primary 失败后立即尝试 fallback
- 不是把 primary 重试满十次后才切换

## 7.4 MCP

MCP 的接入点包括：

- `config/mcp.example.yaml`
- `config/mcp.yaml`
- 启动时的 MCP server 注册

它的作用是给某些 agent 增加外部能力，但不是每个 task 都必须依赖 MCP。

## 7.5 Skills

ResearchOS 当前支持：

- `SKILL.md` frontmatter 发现
- `list-skills`
- `run-skill`
- Claude 风格工具别名翻译

当前 paper 相关 skill：

- `paper-compile`
- `paper-write`
- `deepxiv`

其中：

- `paper-compile`
- `deepxiv`

现在可以直接跑；

- `paper-write`

如果依赖未注册工具，会以降级方式运行。

## 7.6 结构化输出校验

Agent 的完成条件分三层：

1. `outputs_expected` / task contract：文件或目录是否存在
2. `structured_outputs`：指定相对路径是否符合 JSON Schema / YAML Schema
3. Agent 自己的 `validate_outputs()`：阶段专属规则，例如 T3 FULL-TEXT 覆盖、T3.5 引用数量、T4 预算和假设锚点、T4.5 collision cases

这意味着模型调用 `finish_task` 只是“申请收尾”，不是直接成功。runtime 会先执行校验；失败时会把错误反馈给 Agent 重试，超过重试上限才失败。

---

## 8. 从命令角度看“怎么用”

### 8.1 从零跑完整项目

```bash
cd ResearchOS

researchos init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory and retrieval for long-horizon llm agents"

researchos run --workspace ./workspace/local-test2
```

### 8.2 恢复一个中断项目

```bash
cd ResearchOS
researchos resume --workspace ./workspace/local-test2
```

### 8.3 只调文献检索

```bash
cd ResearchOS
researchos run-task T2 --workspace ./workspace/local-test2
```

### 8.4 只调精读恢复

```bash
cd ResearchOS
researchos run-task T3 --workspace ./workspace/local-test2
```

### 8.5 只调 PI 评估

```bash
cd ResearchOS
researchos run-task T7.5 --workspace ./workspace/local-test2
```

### 8.6 只调投稿编译

```bash
cd ResearchOS
researchos run-task T9 --workspace ./workspace/local-test2
```

---

## 9. 建议如何联读其他文档

如果你已经看完本页，建议继续读：

- Runtime 机制： [docs/runtime.md](./runtime.md)
- 配置说明： [docs/config.md](./config.md)
- Docker 运行： [docs/docker.md](./docker.md)
- 开发者本地调试： [docs/dev.md](./dev.md)
- 用户入口： [README.md](../README.md), [README.zh-CN.md](../README.zh-CN.md)

---

## 10. 最后的现实判断

当前这条 pipeline 已经不是纸面设计，而是能跑的实现。

但要对它有一个准确预期：

- 它现在已经具备完整的阶段骨架
- 关键阶段已经支持恢复
- 写作链和投稿链已经接通
- T9 也已经变成真正的 compile-and-repair 阶段
- 但外部 provider 稳定性、实验代码复杂度、论文 LaTeX 质量仍会影响实际运行

所以最好的使用方式是：

- 用 `run` / `resume` 走主链
- 用 `run-task` 定位问题
- 用 workspace artifact 判断进度
- 不要把“模型说它做了”当成事实，始终以落盘产物为准
