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
 -> T4 假设与实验计划生成
 -> T4.5 新颖性预审
 -> T5 Pilot
 -> T6 基于 Pilot 的新颖性复核
 -> T7 完整实验
 -> T7.5 PI 评估
 -> Human Gate
 -> T8 写作 / 审稿 / 修订
 -> T9 投稿包构建、编译、修复与收尾
```

其中：

- `StateMachine` 负责“下一步该跑谁”
- `AgentRunner` 负责“把某个 Agent 真正跑起来”
- `ToolRegistry` 负责“这个 Agent 能调用哪些工具”
- `workspace` 负责“所有输入输出都落盘到哪里”
- `validator` 负责“这个阶段到底算不算真正完成”

---

## 2. 当前真实阶段图

当前真实状态定义在 [config/state_machine.yaml](../config/state_machine.yaml)。

主链如下：

```text
T1
 -> T2
 -> T3
 -> T3.5
 -> T4
 -> T4.5
 -> T5
 -> T6
 -> T7
 -> T7.5
 -> human gate
 -> T8-WRITE
 -> T8-DRAFT
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T9
 -> done
```

几个最容易记错的点：

- `HELLO` 是显式运行的 smoke task，不是主链起点；主链的 `initial_state` 是 `T1`
- `T8` 不是一个节点，而是 6 个节点
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
- 会完整体现 `T7 -> T7.5 -> ask_human -> T8` 这样的链条

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
researchos run-task T8-WRITE \
  --workspace ./workspace/scratch-write \
  --from ./workspace/local-test2
```

这会：

- 按 `T8-WRITE` 的 I/O 契约找到前置输入
- 从 `local-test2` 拷到 `scratch-write`
- 然后只跑 `T8-WRITE`

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
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`

其中：

- `project.yaml` 是研究对象和方向
- `state.yaml` 是状态机状态
- `_runtime/` 是运行时信息
  - `logs/`
  - `traces/`
  - `resume/`

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
- `T5/T7` 会读取已有代码和结果目录
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
| `T2` | `ScoutAgent` | - | 检索、去重、验证、构建精读队列 | `papers_raw`, `papers_dedup`, `papers_verified`, `deep_read_queue` |
| `T3` | `ReaderAgent` | `read` | 逐篇精读并形成结构化证据 | `paper_notes/`, `comparison_table.csv`, `related_work.bib` |
| `T3.5` | `ReaderAgent` | `synthesize` | 从单篇证据分阶段压缩成领域综合 | `synthesis_workbench.json`, `synthesis_outline.md`, `synthesis_draft.md`, `synthesis.md` |
| `T4` | `IdeationAgent` | - | 生成假设、实验计划、idea证据链、Gate决策链、风险评估 | `hypotheses.md`, `exp_plan.yaml`, `idea_scorecard.yaml`, `rejected_ideas.md`, `gate_decisions.json`, `idea_rationales.json`, `risks.md` |
| `T4.5` | `NoveltyAuditorAgent` | - | 对假设做新颖性预审 | `novelty_audit.md`; 有撞车时另写 `collision_cases.md` |
| `T5` | `ExperimenterAgent` | `pilot` | 用小规模实验验证方向值不值得继续 | `pilot_plan.yaml`, `pilot_code/`, `pilot_results.json`, `motivation_validation.md` |
| `T6` | `NoveltyAgent` | - | 基于 Pilot 做增量 novelty 复核 | `novelty_report.md`, `collision_cases.md`, `must_add_baselines.md` |
| `T7` | `ExperimenterAgent` | `full` | 完整实验与主结果、ablation、multi-seed | `results_summary.json`, `runs/`, `configs/`, `iteration_log.md`, `ablations.csv` |
| `T7.5` | `PIAgent` | `evaluate` | 评估实验结果是否足以写论文 | `evaluation_decision.md` |
| `T8-WRITE` | `WriterAgent` | `outline` | 写大纲 | `drafts/outline.md` |
| `T8-DRAFT` | `WriterAgent` | `draft` | 写初稿 | `drafts/paper.tex` |
| `T8-REVIEW-1` | `ReviewerAgent` | round 1 | 第一轮审稿 | `drafts/review_rounds/round_1.md` |
| `T8-REVISE-1` | `WriterAgent` | `revise` | 第一轮修订 | `drafts/paper.tex` |
| `T8-REVIEW-2` | `ReviewerAgent` | round 2 | 第二轮审稿 | `drafts/review_rounds/round_2.md` |
| `T8-REVISE-2` | `WriterAgent` | `revise` | 第二轮修订 | `drafts/paper.tex` |
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
3. `config/state_machine.yaml`
   - 这个阶段在完整 pipeline 里的真实节点名、分支和后继
4. `researchos/orchestration/task_io_contract.py`
   - 输入输出 artifact 契约
5. `config/agent_params.yaml`
   - 模型、预算、工具、读写权限、恢复相关参数

### 运行时的 tool + prompt 分工

当前前 T5 链路不再把所有关键动作都压给 prompt。原则是：

- 可确定的结构化处理放到 tool / runtime：T2 raw 自动落盘与收尾、去重、评分、metadata verification、deep-read queue、T3.5 synthesis workbench、schema validation。
- 需要研究判断的部分交给 prompt：query 语义设计、论文重要性解释、假设选择、新颖性风险判断。
- prompt 里写到的结构化输出，如果在 `structured_outputs` 或 task contract 中声明，会由 runtime/validator 再检查，不只相信模型自述。

这也是当前补强方向：优先把“可机械复现、可测试、容易出错”的步骤工具化，让 LLM 专注在判断和解释上。

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
| - | `user_seeds/seed_ideas.md` | 否 | 用户已有的想法 |
| - | `user_seeds/seed_constraints.md` | 否 | 预算、硬件、目标 venue 等约束 |
| - | `user_seeds/seed_external_resources.jsonl` | 否 | 数据集、模型、代码库、repo 等外部资源 |

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

1. 读取已有 seeds
2. 通过多轮交互明确研究方向和边界
3. 收集用户已有资源
4. 形成 `project.yaml`
5. 对 `project.yaml` 做 schema 校验
6. 进行伦理/风险 screening
7. 写入 `state.yaml`

### 实际执行过程

`PIAgent(init)` 启动时先从 CLI / `ExecutionContext.extra` 读取用户主题，再读取 workspace 中已经存在的 `user_seeds/seed_papers.jsonl`、`user_seeds/seed_ideas.md`、`user_seeds/seed_constraints.md` 和 `user_seeds/seed_external_resources.jsonl`。它会通过 `ask_human` 分轮询问研究边界、已有论文/想法、外部资源和最终确认；如果用户给出论文条目，优先用 `process_seed_paper` 规范化后写入 seed 文件。最终它用 `write_structured_file` 或 `write_file` 生成 `project.yaml`，必要时写 `state.yaml` 和 seed artifacts。收尾时 `validate_outputs()` 会用 `project` schema 检查 `project_id`、`research_direction`、`keywords`、`constraints`、`seed_ensemble` 等字段，并检查 seed_ensemble 不混入论文 metadata；如果发现敏感研究方向，伦理 screening 会阻止完成。

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

- model tier：`medium`
- max steps：`50`
- max tokens：`150000`
- max wall seconds：`600`

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
- `log_scout_progress`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 研究方向、关键词、目标 venue、预算等 |
| `seed_papers` | `user_seeds/seed_papers.jsonl` | 否 | 用户强相关 seed papers，优先级最高 |
| `seed_constraints` | `user_seeds/seed_constraints.md` | 否 | 检索范围、年份、 venue 或其他限制 |
| `seed_ideas` | `user_seeds/seed_ideas.md` | 否 | 用户已有方向，应该转成 query 语义 |
| `seed_external_resources` | `user_seeds/seed_external_resources.jsonl` | 否 | 数据集、repo、模型名，可作为检索锚点 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `papers_raw` | `literature/papers_raw.jsonl` | 原始检索命中结果，去重前 |
| `papers_dedup` | `literature/papers_dedup.jsonl` | 去重、打分和 enrich 后的候选池 |
| `papers_verified` | `literature/papers_verified.jsonl` | 通过 metadata verification 的可信论文池 |
| `verification_failures` | `literature/verification_failures.jsonl` | verification 失败或元数据不一致的样本 |
| `deep_read_queue` | `literature/deep_read_queue.jsonl` | 给 T3 用的精读队列，不再等于全量候选池 |
| `access_audit` | `literature/access_audit.md` | 资料可用性审计，告诉你哪些论文值不值得继续探测 |
| `search_log` | `literature/search_log.md` | 检索日志、检索式和结果说明 |
| `missing_areas` | `literature/missing_areas.md` | 当前文献覆盖仍然不足的方向 |

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

#### `access_audit.md`

这是对文献可读性的审计报告。

它会统计：

- 本地 PDF 数
- seed PDF 数
- `FULL_TEXT / PARTIAL_TEXT / ABSTRACT_ONLY / METADATA_ONLY` 数量
- Top candidates 列表

#### `missing_areas.md`

这是 T2 对“当前检索仍然没覆盖好什么”给出的分析。

它是 T3.5、T4 会继续用到的重要输入，不是可有可无的附带产物。

### T2 的检索逻辑到底怎么做

#### Step 1：读取项目配置和 seed 信息

T2 首先会读取：

- `project.yaml`
- `seed_papers.jsonl`
- `seed_constraints.md`
- `seed_ideas.md`
- `seed_external_resources.jsonl`

并把这些信息转成：

- topic
- keywords
- query anchors
- constraint hints

#### Step 2：query 扩展

当前确定性 query 扩展函数是：

- [researchos/tools/paper_utils.py](../researchos/tools/paper_utils.py) 里的 `expand_queries`

它的核心逻辑是：

1. 把 `topic` 本身作为第一条 query
2. 从前 3 篇 seed paper 标题里提取关键短语
3. 针对含 `memory` / `retrieval` / `agent` 的主题补领域限定词，减少歧义
4. 加时间限定词：
   - `topic {当年-2}-{当年}`
   - `topic {当年-3}-{当年-1}`
5. 去重后最多保留 `max_queries=10`

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

当前 prompt 中的推荐规模是：

- 使用 `6-10` 条检索式
- 每条检索式在每个数据源上抓取 `10-20` 篇
- 总 raw 结果控制在 `100-200` 篇
- 至少要搜到 `20` 篇，否则说明 coverage 太差，需要扩展 query

### 实际执行过程

`ScoutAgent` 运行时先读 `project.yaml` 获取 `research_direction`、`keywords`、`target_venue` 和约束；再读 `user_seeds/seed_papers.jsonl`、`seed_ideas.md`、`seed_constraints.md`、`seed_external_resources.jsonl`，如果存在 `seeds/T2_scout/papers/*.pdf` 还会先用 `scan_seed_papers` 抽取本地 PDF seed metadata。随后它调用 `expand_queries` 生成基础 query，再由 LLM 根据 seed paper 标题、用户想法、数据集/repo 名称补出多样化 query，并用 `detect_duplicate_queries` 检查是否只是同义重复。真正抓论文时优先调用 `openalex_search`、`crossref_search`、`arxiv_search`、`semantic_scholar_search`、`elsevier_scopus_search`、`informs_search`，每次工具返回的 `data.papers` 会被 `AgentRunner` 自动追加到 `literature/papers_raw.jsonl`，不需要模型手工复制 JSON。raw 达到阈值后，runtime 会确定性调用去重、评分、enrich、metadata verification、access audit 和 deep-read queue 构建逻辑，依次产出 `papers_dedup.jsonl`、`papers_verified.jsonl`、`verification_failures.jsonl`、`deep_read_queue.jsonl`、`access_audit.md`、`search_log.md` 和 `missing_areas.md`；最后 `ScoutAgent.validate_outputs()` 再检查数量、schema、`dedup <= raw`、queue 是否来自 verified 池，以及 seed paper 是否进入队列。

### T2 怎样保存 raw 结果

当前默认是 runtime 自动保存，不要求 LLM 手动 `append_papers_raw`。

实际行为：

1. T2 调用任一搜索工具并返回 `data.papers`
2. `AgentRunner` 自动把这些结果追加进 `literature/papers_raw.jsonl`
3. raw 数量达到阈值，或 T2 因预算/错误提前停止但 raw 已落盘时，runtime 会尝试确定性收尾
4. 收尾路径会从 raw 生成 `papers_dedup`、`papers_verified`、`deep_read_queue`、`access_audit`、`search_log`、`missing_areas`

`append_papers_raw` / `process_papers_raw` 仍保留为兼容和补救工具，但正常流程不应该在每次搜索后手动重复追加，否则容易造成 raw 双写和恢复噪声。

### T2 怎样去重

确定性去重函数：

- `deduplicate_papers`

规则：

1. DOI 精确去重
2. 标题相似度去重
   - 当前实现阈值默认 `0.95`

### T2 怎样打 relevance 分

确定性打分函数：

- `score_papers`

当前权重：

- `source_type`: `0.2`
- `year`: `0.3`
- `citation`: `0.2`
- `keyword`: `0.3`

其中：

- `source_type`
  - `top_conference = 1.0`
  - `journal = 0.8`
  - `preprint = 0.6`
  - `workshop = 0.5`
  - `blog = 0.3`
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

最终得到 `relevance_score`。

### T2 怎样 enrich

工具：

- `enrich_papers`

它会补全：

- `authors`
- `source_type`
- `why_relevant`
- `_missing_abstract`
- `access_score_estimate`
- `access_score`
- `evidence_level`
- `url`
- `venue`
- `citation_count`

其中可读性相关字段的意义是：

- `access_score_estimate`
  基于 metadata 估计可读性
- `evidence_level`
  粗粒度证据等级：
  - `FULL_TEXT`
  - `PARTIAL_TEXT`
  - `ABSTRACT_ONLY`
  - `METADATA_ONLY`

### T2 怎样做 metadata verification

工具：

- `build_verified_papers`

逻辑：

1. 优先使用最强标识做回查
2. 拿到 reference metadata
3. 比较标题相似度
4. 检查年份是否匹配
5. 若通过则写入 verified
6. 否则写入 failure

这里的关键不是“让 LLM 自己相信这论文存在”，而是**尽量用真实 API 做回查**。

### T2 怎样构建 deep-read queue

工具：

- `build_deep_read_queue`

当前核心参数：

- `deep_read_min = 18`
- `deep_read_target = 24`
- `deep_read_max = 30`
- `probe_pool = 45`

为什么 `probe_pool` 大于 `target`：

- 因为后续一定会有一部分论文拿不到 PDF、拿不到 abstract、或 verification/解析失败

当前排序的核心思想是：

- seed papers 最高优先级
- 再看 `relevance_score`
- 再看 `access_score`
- 再看 `verification_confidence`

排序中的 `read_priority` 大致是：

- seed priority 大额加权
- relevance `0.55`
- access `0.25`
- verification confidence `0.20`

### T2 怎样做 access audit

工具：

- `build_access_audit`

它会给出：

- 本地 PDF 数
- seed PDF 数
- evidence level 分布
- 每篇论文推荐动作：
  - `verify_metadata`
  - `exclude_from_t3`
  - `read_local_pdf`
  - `read_seed_pdf`
  - `probe_pdf`
  - `abstract_only`
  - `metadata_backlog`

### T2 的成功标准

当前 validator 会检查：

- `papers_dedup.jsonl` 数量在 `10-120`
- 关键字段存在
- `dedup <= raw`
- `papers_verified.jsonl` 存在且数量合理
- `verification_failures.jsonl` schema 正确
- `deep_read_queue.jsonl` 必须来自 verified 池
- 如果存在 seed papers，queue 中必须保留 seed

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

- model tier：`medium`
- max steps：`100`
- max tokens：`300000`
- max wall seconds：`1200`

主要工具：

- `read_file`
- `write_file`
- `append_file`
- `list_files`
- `fetch_paper_pdf`
- `extract_paper_sections`
- `extract_pdf_text`
- `build_synthesis_workbench`
- `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 项目方向和上下文 |
| `papers_dedup` | `literature/papers_dedup.jsonl` | 是 | 最宽泛候选池，回退时使用 |
| `papers_verified` | `literature/papers_verified.jsonl` | 否但强烈推荐 | 更可信的候选池 |
| `deep_read_queue` | `literature/deep_read_queue.jsonl` | 否但强烈推荐 | T2 筛好的精读队列 |
| `deep_read_queue_pending` | `literature/deep_read_queue_pending.jsonl` | 恢复时优先 | T3 恢复运行时真正还没读完的队列 |
| `access_audit` | `literature/access_audit.md` | 否 | 辅助判断 PDF / abstract 可用性 |
| `missing_areas` | `literature/missing_areas.md` | 否 | 后续综合时仍有价值 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper_notes_dir` | `literature/paper_notes/` | 每篇论文一份结构化笔记；包含 `Reading Coverage` 阅读覆盖记录 |
| `comparison_table` | `literature/comparison_table.csv` | 方法、指标、结论的横向对比表 |
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
- `comparison_table.csv`
- `related_work.bib`

如果已经存在一部分产物，它的目标不是重写，而是：

1. 识别已完成论文
2. 补齐已有 note 对应的 table / bib 记录
3. 只处理剩余论文

### T3 对 seed papers 的态度

seed papers 是最高优先级，不是普通候选。

具体表现为：

- queue 中的 seed 必须优先读
- 如果 queue 中有 seed 但没生成 note，validator 会认为未完成

### T3 逐篇阅读的真正流程

对每篇论文，典型流程是：

1. 看是否已有 note
2. 若已有且结构、证据锚点、`Reading Coverage` 都合格，才跳过或只补 table/bib
3. 若无本地 PDF，尝试 `fetch_paper_pdf`
4. 只要本地存在 PDF 或下载成功，就用 `extract_pdf_text` 读到 `total_pages` 的最后一页
5. 如果一次读取被 `max_chars` 或 runtime 截断，就缩小页码范围分块重读，直到覆盖 `1-total_pages`
6. 如果 PDF 可得但只完成部分页面，note 必须标为 `[PARTIAL-TEXT]`
7. 如果连全文都拿不到，再退化成 `[ABSTRACT-ONLY]`

### 实际执行过程

`ReaderAgent(read)` 进入时会先列出 `literature/paper_notes/`，再读取 `literature/deep_read_queue_pending.jsonl`；如果没有 pending queue，就读 `deep_read_queue.jsonl`，再回退到 `papers_verified.jsonl`，最后才回退到 `papers_dedup.jsonl`。处理每篇论文前，它用 `lookup_paper_record` 或小范围 `read_file` 取该论文 metadata，规范化论文 ID 后检查是否已有同名 note；已有 note 只有在结构、Evidence 锚点和 `Reading Coverage` 都合格时才算完成。若 PDF 不在本地，它会调用 `fetch_paper_pdf(paper_id=..., save_path="literature/pdfs/{id}.pdf")` 尝试下载；拿到 PDF 后用 `extract_pdf_text` 按页读取，如果返回的 metadata 显示 `preview_truncated_by_max_chars=true` 或 runtime 上下文裁剪，就继续用更小的 `start_page/max_pages` 分块重读，直到 `Pages read` 覆盖 `1-total_pages`。写出时用 `write_file("literature/paper_notes/{id}.md", ...)` 生成 12 节结构化 note，并用 `append_file` 或重写方式维护 `comparison_table.csv` 和 `related_work.bib`。收尾时 validator 会逐篇扫描 note，检查 `- **Status**:`、核心章节、数字证据 `[Evidence: ...]`、全文页码覆盖、最终截断状态、queue 覆盖率和 seed paper 覆盖情况。

### T3 如何判定 FULL-TEXT

`[FULL-TEXT]` 不是“拿到过 PDF”或“读了足够多内容”的意思。

当前判定标准是：

- `extract_pdf_text` 的页码覆盖必须到最后一页
- note 的 `## 12. Reading Coverage` 必须写清楚 `Pages read`，可以是连续范围，例如 `1-12 / 12`
- 也可以是分块重读覆盖全篇，例如 `1-4, 5-8, 9-12 / 12`
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

### T3 当前的恢复机制

这是当前最成熟的恢复阶段之一。

核心行为：

- 根据已有且通过结构校验的 `paper_notes/` 自动裁出 `deep_read_queue_pending.jsonl`
- 重新跑时优先读取 pending queue
- 缺少 `Reading Coverage`、`[FULL-TEXT]` 页码不完整、或最终截断状态未说明为 `none` / `无` / 已解决的旧 note，不会被视为已完成
- 不再默认把整个 `papers_dedup` 当作“必须重读”的任务池

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
- note 结构合理
- 每篇 note 包含 `## 12. Reading Coverage`
- `[FULL-TEXT]` note 必须记录完整页码覆盖和最终无截断；分块重读覆盖全篇是合法的
- 如果 queue 存在，则至少完成 queue 中的 `deep_read_min`
- queue 中 seed papers 必须覆盖
- `comparison_table.csv` 存在
- `related_work.bib` 存在

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
| `missing_areas` | `literature/missing_areas.md` | 否 | T2 识别到的不足覆盖 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `synthesis` | `literature/synthesis.md` | 文献综合结果，供 T4/T6/T8 消费 |
| `synthesis_workbench` | `literature/synthesis_workbench.json` | 从 paper_notes 抽取的结构化证据、方法家族、frontier、趋势和问题候选 |
| `synthesis_outline` | `literature/synthesis_outline.md` | 分阶段大纲 |
| `synthesis_draft` | `literature/synthesis_draft.md` | 工具生成的初稿，供审阅和修订 |

### 这一步到底做什么

T3.5 不是继续搜论文，也不是继续逐篇读，而是把：

- 单篇笔记
- 方法对比表
- T2 发现的缺口

压缩成一个面向研究决策的领域综合。

当前实现不是单次 prompt 直接写完。运行时会先尝试调用 `build_synthesis_workbench(write_final=true)`：

1. 解析 `paper_notes/*.md`
2. 生成 `synthesis_workbench.json`
3. 渲染 `synthesis_outline.md`
4. 渲染 `synthesis_draft.md`
5. 写出初版 `synthesis.md`
6. 立即用 Reader validator 校验

如果这条确定性路径已经通过校验，`AgentRunner` 会跳过一次性 LLM 综合，避免把几十篇 note 压进一个大 prompt 后失控；如果校验失败，才继续让 Reader Agent 基于这些 staged artifacts 审阅和修订。

### 实际执行过程

`ReaderAgent(synthesize)` 的执行先由 runtime 抢跑一条确定性路径：在进入 LLM 前，`AgentRunner._maybe_finalize_t35_before_llm()` 会实例化 `BuildSynthesisWorkbenchTool` 并调用 `build_synthesis_workbench(write_final=true)`。这个工具读取 `literature/paper_notes/*.md`，解析每篇 note 的标题、年份、方法概述、关键结果、局限、问题和论文 ID 引用；再读取 `literature/comparison_table.csv` 提取方法家族、数据集、指标和效率信号；如果存在 `literature/missing_areas.md`，也会纳入 research question 候选。工具会先写 `literature/synthesis_workbench.json`，再渲染 `synthesis_outline.md` 和 `synthesis_draft.md`，最后生成初版 `synthesis.md`。生成后立即调用 `ReaderAgent.validate_outputs()`：如果章节、长度和 paper note 引用数量都合格，任务直接以 deterministic completion 完成，不再消耗一次大 prompt；如果不合格，才让 LLM 读取这些 staged artifacts，用 `read_file` 审阅问题，再用 `write_file` 修订最终 `synthesis.md`。

### 期望章节

当前实现和文档都要求至少覆盖：

1. 方法家族分类
2. 共同假设
3. 性能-效率前沿
4. 技术趋势
5. 可操作研究问题

### 为什么它重要

`T4` 不应该直接从几十篇 note 发散 idea。

更合理的方式是：

- 先形成领域结构化理解
- 再基于综合后的 gap 发散研究假设

### T3.5 的成功标准

当前 validator 关心的不是“有没有生成一个文件”这么简单，而是：

- `literature/synthesis.md` 必须存在
- `synthesis_workbench.json`、`synthesis_outline.md`、`synthesis_draft.md` 应随 T3.5 一起产出
- 不能太短
- 必须覆盖约定的核心章节
- 结果要足够支撑后续 `T4`、`T6`、`T8`

### 单独运行示例

```bash
cd ResearchOS
researchos run-task T3.5 --workspace ./workspace/local-test2
```

### 恢复语义

T3.5 没有像 T3/T5/T7 那样复杂的专门恢复文件，但它天然是 artifact-first 的：

- 只要 `paper_notes/`、`comparison_table.csv` 还在
- 重跑 T3.5 的成本主要是重新综合
- staged workbench 让重跑可以从结构化证据、outline 或 draft 开始检查
- 不需要重新跑 T2/T3 才能继续

---

## 6.6 T4：IdeationAgent

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
  - `ask_human`
  - `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 方向、预算、硬件等约束 |
| `synthesis` | `literature/synthesis.md` | 是 | T3.5 的综合结论 |
| `comparison_table` | `literature/comparison_table.csv` | 否但强烈推荐 | 现有方法和基线结构 |
| `missing_areas` | `literature/missing_areas.md` | 否 | T2 的缺口分析 |
| `seed_ideas` | `user_seeds/seed_ideas.md` | 否 | 用户已有想法 |
| `seed_constraints` | `user_seeds/seed_constraints.md` | 否 | 额外约束 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `hypotheses` | `ideation/hypotheses.md` | 3-6 个假设及其锚点 |
| `exp_plan` | `ideation/exp_plan.yaml` | 实验计划，后续 T5/T7 会直接执行 |
| `idea_scorecard` | `ideation/idea_scorecard.yaml` | 所有候选 idea 的来源、核心内容、评分、baseline、决策、风险和最低实验 |
| `rejected_ideas` | `ideation/rejected_ideas.md` | 被淘汰/暂缓/合并 idea 的人类可读原因 |
| `gate_decisions` | `ideation/gate_decisions.json` | Gate1/Gate2 的用户反馈、选择/淘汰和决策理由 |
| `idea_rationales` | `ideation/idea_rationales.json` | 每个 idea / hypothesis 的生成依据和来源追踪 |
| `risks` | `ideation/risks.md` | 风险评估 |
| - | `ideation/_candidate_directions.json` | 中间候选方向 |
| - | `ideation/_lens_analysis.json` | 多视角审视结果 |
| - | `ideation/_premortem.md` | pre-mortem 质疑结果 |

### T4 的流程不是“一次生成完”

当前 prompt 明确把它设计成了多阶段思考：

1. 读取 `synthesis.md`
2. 生成 `3-5` 个候选方向
3. 对每个方向打 3 维分：
   - Novelty
   - Feasibility
   - Impact
4. 用 10 个 ideation lenses 审视
5. 用 `ask_human` 做 Gate1
6. 对选定方向做 pre-mortem
7. 最终产出：
   - `hypotheses.md`
   - `exp_plan.yaml`
   - `idea_scorecard.yaml`
   - `rejected_ideas.md`
   - `gate_decisions.json`
   - `idea_rationales.json`
   - `risks.md`

### 实际执行过程

`IdeationAgent` 启动后先读 `project.yaml`、`literature/synthesis.md`、`literature/comparison_table.csv`、`literature/missing_areas.md`，再读用户侧的 `seed_ideas.md` 和 `seed_constraints.md`。它先把 synthesis 里的方法家族、共同假设、frontier、趋势和可操作问题压缩成候选机会，再结合 comparison table 中的 baseline/metric 信号生成 `3-5` 个候选方向，通常会把中间结果写到 `ideation/_candidate_directions.json`。随后它用 prompt 中的 10 个 lenses 做二次审视，形成 `ideation/_lens_analysis.json`，并用 `ask_human` 让用户在候选方向中选择、合并或补充新想法；用户的选择会写入 `gate_decisions.json`。选定方向后，它做 pre-mortem，把高风险写到 `_premortem.md` 和最终 `risks.md`，再用 `write_structured_file` 生成 `idea_scorecard.yaml`、`idea_rationales.json` 和 `exp_plan.yaml`，用 `write_file` 生成 `hypotheses.md`、`rejected_ideas.md`。最后 validator 检查 hypotheses anchor、实验计划 schema、预算、scorecard selected/rejected 覆盖、gate 决策链和 rationale 是否能追溯到文献/缺口/seed/lens。

### 10 个 ideation lenses 是什么

prompt 中显式列了 10 个视角，例如：

- contrastive
- first-principles
- analogical
- constraint-based
- multi-scale
- temporal
- causal
- uncertainty
- resource
- stakeholder

这一步的目的不是让输出更花哨，而是减少：

- 伪创新
- 资源上不可做
- 物理/数学上站不住
- 只是 baseline 拼接

### `hypotheses.md` 为什么要有 `H1/H2/...`

因为：

- `exp_plan.yaml` 里的 `hypothesis_ref` 要引用它们
- `idea_rationales.json` 里的 `hypothesis_refs` 要覆盖它们，记录每个 idea 来自哪些文献观察、缺口、seed idea 或 lens insight
- 后续 T4.5 / T6 / T7 都会继续按这些 anchor 追踪假设

### T4 如何记录 idea 的依据

`T4` 现在会把依据拆成四层：

- 在 `hypotheses.md` 里给每个 `H1/H2/...` 写人可读的“生成依据”
- 在 `ideation/idea_scorecard.yaml` 里记录所有候选 idea 的来源、核心内容、选择依据、closest baselines、七维评分、决策状态、风险、kill criteria 和最低可行实验
- 在 `ideation/rejected_ideas.md` 里用人能读懂的方式说明为什么 pass 掉其他方向，以及什么条件下可以重访
- 在 `ideation/gate_decisions.json` 里记录 `T4-DECIDE-1` / `T4-DECIDE-2` 的用户反馈、selected/rejected idea ids 和决策理由
- 在 `ideation/idea_rationales.json` 里给最终 hypothesis 写机器可读依据索引，包含 `source_questions`、`literature_observations`、`missing_area_links`、`comparison_table_signals`、`seed_idea_links`、`lens_insights`、`reasoning` 和 `confidence`

validator 会要求 `idea_scorecard.yaml`、`gate_decisions.json` 和 `idea_rationales.json` 都符合 schema；其中 scorecard 必须至少包含一个 selected idea 和一个 rejected/deferred/merged idea，selected idea 的 `hypothesis_refs` 必须覆盖所有最终假设 anchor。这样 T4.5 新颖性审计或 T5 实验计划都能回看“为什么会有这个 idea”，也能知道其他方向为什么被淘汰。

### T4 的成功标准

validator 会检查：

- `hypotheses.md` 不能太短
- 必须有 `H1/H2/...` 锚点
- `exp_plan.yaml` 必须过 schema
- `idea_scorecard.yaml` 必须过 schema，记录选中和淘汰/暂缓/合并的候选 idea
- `rejected_ideas.md` 必须解释非选中 idea 的淘汰原因
- `gate_decisions.json` 必须过 schema，并记录两轮 Gate
- `idea_rationales.json` 必须过 schema，并覆盖所有假设 anchor
- 每个实验必须正确引用 `hypothesis_ref`
- `risks.md` 至少有 3 条风险
- 粗略预算不能超项目预算 85%

### T4 单独运行示例

```bash
cd ResearchOS
researchos run-task T4 --workspace ./workspace/local-test2
```

---

## 6.7 T4.5：NoveltyAuditorAgent

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
  - `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 研究方向和关键词 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | T4 产出的假设 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |
| `comparison_table` | `literature/comparison_table.csv` | 否 | 已有方法对比表 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `novelty_audit` | `ideation/novelty_audit.md` | 针对每个假设的新颖性预审 |
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
2. 设计 3 类 query
   - 核心方法 + 应用场景
   - 核心技术术语
   - 问题描述
3. 用 `search_papers`
4. 搜近 12 个月的相关工作；`year_from` 按运行日期动态计算，不写死年份
5. 每个 query `max_results=30`

然后按相似度分成：

- `High Overlap`
- `Medium Overlap`
- `Low Overlap`
- `No Overlap`

### 实际执行过程

`NoveltyAuditorAgent` 先读 `ideation/hypotheses.md`，用 `H1/H2/...` anchor 切分每个假设；再读 `literature/synthesis.md` 和 `literature/comparison_table.csv`，拿到已有方法家族、baseline 和指标上下文。对每个假设，它从假设标题、方法关键词、目标场景和预期机制中抽出 2-3 组 query，然后调用 `search_papers(query=..., sources=["semantic_scholar", "arxiv"], max_results=30, year_from=<运行时近一年起始年>)` 搜近期工作；必要时用 `fetch_paper_metadata` 回查疑似撞车论文。审计时它把搜索结果和已有 synthesis 对齐，判断相似点、差异点、证据强度和是否需要补 baseline，最后用 `write_file("ideation/novelty_audit.md", ...)` 写每个假设的 Level 0-3 判定。如果报告中出现真实 High/Medium Overlap，它还必须写 `ideation/collision_cases.md`，记录论文、相似点、差异点和处理建议；validator 会区分“High Overlap: none”这种空标题和真实案例，只有真实案例才强制 collision 文件。

### T4.5 的新颖性等级

- `Level 3`：高度新颖
- `Level 2`：中度新颖
- `Level 1`：低度新颖
- `Level 0`：无新颖性 / 明确撞车

### T4.5 的成功标准

validator 会检查：

- `novelty_audit.md` 存在且不太短
- 必须出现 `Level 0-3`
- 每个 `H1/H2/...` 都必须被审计
- 如果 audit 中列出 High/Medium Overlap，`collision_cases.md` 必须存在并归档对应案例；没有 High/Medium Overlap 时可以不生成该文件

### 它和 T6 的区别

非常重要：

- T4.5：**没有 Pilot 证据时**的预审
- T6：**有 Pilot 证据后**的增量复核

T6 不应该从零重跑一次 T4.5，这个逻辑现在已经明确分开了。

---

## 6.8 T5：ExperimenterAgent（pilot）

### 角色

- Agent：`ExperimenterAgent`
- mode：`pilot`
- 代码： [researchos/agents/experimenter.py](../researchos/agents/experimenter.py)
- Prompt： [researchos/prompts/experimenter.j2](../researchos/prompts/experimenter.j2)

### 当前默认配置

- model tier：`medium`
- 主要工具：
  - `read_file`
  - `write_file`
  - `write_structured_file`
  - `list_files`
  - `append_file`
  - `bash_run`
  - `docker_exec`
  - `finish_task`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 项目预算和资源约束 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设锚点 |
| `exp_plan` | `ideation/exp_plan.yaml` | 是 | 要跑的实验计划 |
| `risks` | `ideation/risks.md` | 否 | 风险参考 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `pilot_plan` | `pilot/pilot_plan.yaml` | 落到 pilot 目录下的实验计划 |
| `pilot_code` | `pilot/pilot_code/` | 可执行的试点代码 |
| `pilot_results` | `pilot/pilot_results.json` | 试点实验结果 |
| `motivation_validation` | `pilot/motivation_validation.md` | 是否值得继续的判断 |
| - | `pilot/smoke_test_passed.marker` | smoke test 通过标记 |
| - | `pilot/docker_digests.txt` | 使用的镜像摘要 |
| - | `pilot/pilot_resume_state.json` | 恢复运行状态 |

### T5 的关键原则

T5 不是正式实验，而是：

- 用小规模数据做 smoke validation
- 确认方向值得继续
- 快速暴露实现 / 资源 / 数据问题

### T5 的硬性要求

当前 prompt 对 Pilot 的硬要求包括：

- 代码必须支持 `--smoke_test`
- smoke test 要能完成：
  - forward
  - backward
  - optimizer.step()
- 使用小规模数据
- 固定 `seed=42`
- 产出 `motivation_validation.md`
- 记录 docker digest

### 实际执行过程

`ExperimenterAgent(pilot)` 在进入 LLM 前先跑 `run_experimenter_preflight()`：读取 `ideation/hypotheses.md`、`ideation/novelty_audit.md` 和 `ideation/exp_plan.yaml`，确认 Integrity Gate 通过，并检查 `exp_plan.yaml` 的预算没有超过 `project.yaml` 里的 `constraints.max_budget_usd`。真正执行时，它先用 `read_file` 理解假设和实验计划，再把全量计划裁剪成 `pilot/pilot_plan.yaml`，只保留 5-10% 数据、小规模配置和固定 `seed=42`。随后它用 `write_file` 创建或复用 `pilot/pilot_code/run_pilot.py`，代码必须支持 `--smoke_test` 和 `--seed`；再通过 `docker_exec` 在受控环境里跑 smoke test，至少验证 forward/backward/optimizer step，成功后写 `pilot/smoke_test_passed.marker`。试点实验完成后，它把结果整理成符合 schema 的 `pilot/pilot_results.json`，写 `pilot/motivation_validation.md` 给出 `PASS/REVISE/FAIL`，并写 `pilot/docker_digests.txt` 记录真实镜像 digest。validator 会再次检查 seed=42、schema、smoke marker、motivation 判定、docker digest 和代码参数，防止只写报告但没真正跑。

### T5 内部还有 Integrity Gate

在真正执行前，代码里还有一层 `run_integrity_gate`，会检查：

- `hypotheses.md`
- `novelty_audit.md`
- `exp_plan.yaml`

这层的目的，是不让一个明显不完整的实验计划直接往下跑。

### T5 的恢复逻辑

当前 T5 会优先复用：

- 已有 `pilot_code/`
- 已有 `pilot_plan.yaml`
- 已有结果和 marker

恢复时倾向于：

- 不重写已有代码
- 优先补缺失产物

### T5 的成功标准

validator 主要看：

- `pilot_results.json`
- `pilot_plan.yaml`
- `motivation_validation.md`

同时运行时还会依赖：

- smoke test 通过
- 代码真正可执行
- `pilot/docker_digests.txt` 记录真实镜像 digest

### T5 单独运行示例

```bash
cd ResearchOS
researchos run-task T5 --workspace ./workspace/local-test2
```

---

## 6.9 T6：NoveltyAgent

### 角色

- Agent：`NoveltyAgent`
- 代码： [researchos/agents/novelty.py](../researchos/agents/novelty.py)
- Prompt： [researchos/prompts/novelty.j2](../researchos/prompts/novelty.j2)

### 当前默认配置

- model tier：`medium`
- routing profile：`siliconflow_only`
- 主要工具：
  - `read_file`
  - `write_file`
  - `list_files`
  - `search_papers`
  - `ask_human`
  - `finish_task`
- 读写特点：
  - 会读取 `novelty/` 目录中自己上次生成的内容
  - 因此它现在支持真正意义上的续跑，而不是每次都重写整份报告

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 研究方向 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设 |
| `exp_plan` | `ideation/exp_plan.yaml` | 是 | 实验计划 |
| `pilot_results` | `pilot/pilot_results.json` | 是 | Pilot 结果 |
| `motivation_validation` | `pilot/motivation_validation.md` | 是 | Pilot 阶段的继续/修改/失败判断 |
| `novelty_audit` | `ideation/novelty_audit.md` | 是 | T4.5 的预审结果 |
| `comparison_table` | `literature/comparison_table.csv` | 否 | baseline / 现有方法对比 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `novelty_report` | `novelty/novelty_report.md` | 有 Pilot 证据后的最终新颖性复核 |
| `collision_cases` | `novelty/collision_cases.md` | 潜在撞车案例 |
| `must_add_baselines` | `novelty/must_add_baselines.md` | T7 必须补做的基线 |

### T6 为什么不能重复 T4.5

当前系统已经明确把 T6 改成“增量复核”。

也就是说：

- 先继承 `novelty_audit.md`
- 再看 Pilot 有没有改变风险判断
- 只补搜高风险或不确定假设

而不是重新把每个假设再全量搜一次。

### T6 的搜索策略

当前 prompt 强约束：

- 最多只补搜 `1-2` 个假设
- 每个假设最多 `1-2` 个 query
- 只搜：
  - 新出现工作
  - 缺失 baseline
- `max_results=8`

优先补搜的情况：

1. T4.5 中是 `Level 0/1`
2. T4.5 中有 `High/Medium Overlap`
3. Pilot 和 T4.5 结论矛盾
4. T4.5 没覆盖到现在必须补的 baseline

### 实际执行过程

`NoveltyAgent` 不是从零做一次全量 novelty search。它先读取 `pilot/pilot_results.json`、`pilot/motivation_validation.md`、`ideation/novelty_audit.md`、`ideation/hypotheses.md`、`ideation/exp_plan.yaml`、`literature/synthesis.md` 和 `literature/comparison_table.csv`，再检查 `novelty/` 下是否已有 `novelty_report.md`、`collision_cases.md`、`must_add_baselines.md`，以便续跑时只补缺。它优先比较 Pilot 结果和 T4.5 预审：如果某个假设 Pilot 表现强但 T4.5 判为低新颖，或 T4.5 有 High/Medium Overlap，就只对这些高风险假设调用 `search_papers` 补搜近期工作和缺失 baseline；正常情况下最多补搜 1-2 个假设，每个假设 1-2 个 query，每个 query `max_results=8`。最终它写 `novelty/novelty_report.md`，逐个假设给出实验后 Level 0-3 判定；写 `novelty/collision_cases.md` 归档仍存在的撞车风险；写 `novelty/must_add_baselines.md` 明确 T7 必须补的 baseline。validator 会检查每个假设 anchor 是否出现、Level 是否明确、baseline 文件是否非空。

### T6 的结果要回答什么

每个假设最终要回答：

- Pilot 是否支撑这个创新点
- 是否仍然有撞车风险
- 新颖性最终是什么 level
- T7 时必须加哪些 baseline

### T6 当前真正依赖哪些证据

按强度从高到低，大致是：

1. `pilot/pilot_results.json`
2. `pilot/motivation_validation.md`
3. `ideation/novelty_audit.md`
4. `literature/synthesis.md`
5. `literature/comparison_table.csv`

这也是为什么 T6 的判断应该比 T4.5 更“实验化”，而不是只基于文献做抽象比较。

### T6 的成功标准

validator 会检查：

- `novelty_report.md` 存在并足够长
- 有 `Level 0-3`
- 每个假设 anchor 都必须出现
- `must_add_baselines.md` 必须存在且不能太空

### T6 的恢复逻辑

当前 T6 的恢复机制分两层：

1. 通用恢复
   - runtime 会把已有产物扫描进恢复上下文
2. agent 自身恢复
   - `allowed_read_prefixes` 已允许读取 `novelty/`
   - prompt 也明确要求先读已有 `novelty_report/collision_cases/must_add_baselines`
   - 所以重跑 T6 时，应该优先补缺，不是把已有结论当不存在

### T6 与 gate

`T6` 后面当前有 novelty gate。它的业务语义是：

- `PASS`：继续进入 T7
- `REVISE`：需要修改
- `FAIL`：回到更早阶段

---

## 6.10 T7：ExperimenterAgent（full）

### 角色

- Agent：`ExperimenterAgent`
- mode：`full`
- 代码： [researchos/agents/experimenter.py](../researchos/agents/experimenter.py)
- Prompt： [researchos/prompts/experimenter.j2](../researchos/prompts/experimenter.j2)

### 当前默认配置

- model tier：`medium`
- routing profile：`siliconflow_only`
- 主要工具：
  - `read_file`
  - `write_file`
  - `write_structured_file`
  - `list_files`
  - `append_file`
  - `bash_run`
  - `docker_exec`
  - `finish_task`
- mode `full` 额外强调：
  - `docker_required: true`
  - `gpu_required: true`
  - `ablation_min: 3`
  - `seed_ensemble_min: 3`

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 预算、资源、方向 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设 |
| `exp_plan` | `ideation/exp_plan.yaml` | 是 | 全实验计划 |
| `pilot_results` | `pilot/pilot_results.json` | 是 | Pilot 结果 |
| `pilot_code` | `pilot/pilot_code/` | 是 | 可复用的前一阶段代码 |
| `novelty_report` | `novelty/novelty_report.md` | 是 | T6 的结论 |
| `must_add_baselines` | `novelty/must_add_baselines.md` | 是 | T7 必补基线 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `results_summary` | `experiments/results_summary.json` | 全实验结果摘要 |
| `runs_dir` | `experiments/runs/` | 每个实验的运行目录 |
| `configs_dir` | `experiments/configs/` | 实验配置目录 |
| `iteration_log` | `experiments/iteration_log.md` | 迭代日志 |
| `ablations` | `experiments/ablations.csv` | 消融结果 |
| - | `experiments/seed_ensemble_summary.json` | 多 seed 汇总 |
| - | `experiments/iteration_diversity_check.md` | 迭代多样性检查 |
| - | `experiments/docker_digests.txt` | 实验环境镜像摘要 |
| - | `experiments/full_resume_state.json` | 恢复状态 |

### T7 和 T5 的本质区别

| 方面 | T5 | T7 |
| --- | --- | --- |
| 目标 | 验证值不值得继续 | 形成论文级证据 |
| 数据规模 | 小规模 | 完整规模 |
| seed | 固定 42 | 分层 seed ensemble |
| ablation | 非重点 | 硬要求 |
| 迭代 | 少 | 最多 5 轮 |

### T7 的硬性要求

#### 1. headline 实验至少 3 个 seed

当前 prompt 和 validator 都强调：

- headline 实验必须做多 seed

当前推荐分层大致是：

- headline：`[42, 43, 44]`
- final_method：`[42, 43]`
- ablation：`[42]`

#### 2. Ablation 至少 3 条

输出：

- `experiments/ablations.csv`

#### 3. 迭代多样性

不能每轮都只是小修小补同一组参数。

#### 4. failure mode 检查

代码里还内置了 7 类常见 AI research failure mode 检查。

例如：

- loss 发散
- 结果异常
- 伪提升
- 消融不充分

### 实际执行过程

`ExperimenterAgent(full)` 先读 `ideation/exp_plan.yaml`、`ideation/hypotheses.md`、`pilot/pilot_results.json`、`pilot/pilot_code/`、`novelty/novelty_report.md` 和 `novelty/must_add_baselines.md`。它会优先复用 T5 的 pilot 代码，把可复用部分迁移到 `experiments/code/run_exp.py` 或 `experiments/runs/*`，再根据 full mode 要求扩展数据规模、seed ensemble、ablation 和必须补的 baseline。执行实验时，它用 `docker_exec` 运行主实验、baseline 和 ablation；每个实验应有独立 `experiments/runs/{run_id}/`，包含配置、日志、指标和必要输出。运行过程中用 `append_file` 维护 `experiments/iteration_log.md`，记录每轮修改、失败、修复和指标变化；用 `write_structured_file` 或 `write_file` 写 `experiments/results_summary.json`，其中每个实验要有 `experiment_id`、`tier`、`seed_runs`/`seeds`、metrics 和 `quality_status`。收尾时还要写 `experiments/ablations.csv`、`experiments/seed_ensemble_summary.json`、`experiments/iteration_diversity_check.md` 和 `experiments/docker_digests.txt`。validator 会检查 headline 至少 3 seeds、final_method 至少 2 seeds、ablation 至少 3 条、docker digest 存在，并运行 failure mode 检查识别 nan loss、离谱指标或消融不足。

### T7 的 validator 真正在卡什么

当前 T7 最常见的校验失败点包括：

- `results_summary.json` 结构不符合预期
- headline 实验没有足够 seed
- `ablations.csv` 不足 3 条
- `iteration_log.md` 缺失
- `seed_ensemble_summary.json` 缺失
- `iteration_diversity_check.md` 缺失
- `docker_digests.txt` 缺失
- 结果文件存在，但关键字段为空或不自洽

所以 T7 的难点不只是“把实验跑起来”，还包括“把结果整理成 validator 能确认的证据结构”。

### T7 的恢复逻辑

当前 T7 会尽量复用：

- `experiments/code/`
- `experiments/runs/`
- `results_summary.json`
- `iteration_log.md`
- `ablations.csv`

也就是说，它应该补缺，不应该无脑从零全删重跑。

### T7 单独运行 vs 完整运行

- 单独 `run-task T7`
  - 适合只调完整实验阶段
  - 不会自动进入 `T7.5`
- 完整 `run/resume`
  - `T7` 成功后会继续进入 `T7.5`
  - 然后再经过人类 gate 决定是否进入写作

### T7 单独运行示例

```bash
cd ResearchOS
researchos run-task T7 --workspace ./workspace/local-test2
```

---

## 6.11 T7.5：PIAgent（evaluate）

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
| `results_summary` | `experiments/results_summary.json` | 是 | 关键实验结果摘要 |
| `iteration_log` | `experiments/iteration_log.md` | 否但强烈推荐 | 迭代过程 |
| `exp_plan` | `ideation/exp_plan.yaml` | 否但强烈推荐 | 原始实验计划 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `evaluation_decision` | `evaluation/evaluation_decision.md` | PI 视角的阶段评估与下一步建议 |

### 它在完整 pipeline 中的作用

T7.5 的意义是：

- 完整实验结束后
- 不立刻写论文
- 先由 PI 视角判断：
  - 结果是否足够
  - 要不要回 T7 继续补
  - 要不要回 T4 改假设
  - 还是可以进 T8

### 实际执行过程

`PIAgent(evaluate)` 启动后读取 `experiments/results_summary.json`、`experiments/iteration_log.md` 和 `ideation/exp_plan.yaml`。它会把实验结果按原计划对齐：哪些 hypothesis 被验证，哪些实验失败或缺失，主指标是否达到目标，ablation 和多 seed 证据是否足够，是否还有 T6 要求的 baseline 没补。然后它用 `write_file("evaluation/evaluation_decision.md", ...)` 写一份决策报告，必须包含 `Situation`、`Options` 和至少一个 `next_task`。典型 next_task 可以是 `T8-WRITE`（证据足够，进入写作）、`T7`（继续补实验）、`T4`（回到假设重构）或其他状态机允许的节点。完整 pipeline 中，StateMachine 会从 `evaluation_decision.md` 反向解析 `next_task`，再把 PI 推荐交给 human gate；用户可以接受推荐，也可以在 gate 里选择其它路径。

### `evaluation_decision.md` 里面最关键的字段

- `Situation`
- `Options`
- `next_task`

当前状态机支持从这里解析 `next_task`。

你可以把这一步理解成：

- `T7` 负责把证据跑出来
- `T7.5` 负责决定“证据是否已经足够支撑写论文”

### 完整链路中的行为

完整链路不是 `T7.5 -> T8` 直接跳，而是：

```text
T7
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

## 6.12 T8：Writer / Reviewer 多阶段写作链

T8 当前分成 6 个真实节点：

- `T8-WRITE`
- `T8-DRAFT`
- `T8-REVIEW-1`
- `T8-REVISE-1`
- `T8-REVIEW-2`
- `T8-REVISE-2`

### 6.12.0 这一整段链到底由谁实现

这里实际上不是一个 agent，而是两类 agent 交替工作：

- `WriterAgent`
  - 代码： [researchos/agents/writer.py](../researchos/agents/writer.py)
  - Prompt： [researchos/prompts/writer.j2](../researchos/prompts/writer.j2)
- `ReviewerAgent`
  - 代码： [researchos/agents/reviewer.py](../researchos/agents/reviewer.py)
  - Prompt： [researchos/prompts/reviewer.j2](../researchos/prompts/reviewer.j2)

当前默认配置大致是：

- Writer
  - model tier：`heavy`
  - 主要工具：`read_file`, `write_file`, `list_files`, `finish_task`
- Reviewer
  - model tier：`heavy`
  - 主要工具：`read_file`, `list_files`, `write_file`, `finish_task`

这条链的核心思路不是“一次成稿”，而是：

- Writer 生成结构和正文
- Reviewer 站在审稿人视角提问题
- Writer 再按问题修订

因此它更像一个 mini peer-review loop，而不是单次文案生成。

### 6.12.1 T8-WRITE：WriterAgent（outline）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 方向、venue |
| `results_summary` | `experiments/results_summary.json` | 是 | 实验结果 |
| `synthesis` | `literature/synthesis.md` | 是 | 综述 |
| `related_work_bib` | `literature/related_work.bib` | 是 | 引用库 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `outline` | `drafts/outline.md` | 论文大纲 |

#### 作用

把实验和综述信息组织成：

- 标题候选
- abstract 要点
- intro / related work / method / experiments / conclusion 结构

当前这一阶段的价值在于：

- 把后面 `T8-DRAFT` 的写作结构固定下来
- 避免初稿阶段一边写一边改整体叙事框架

#### 实际执行过程

`WriterAgent(outline)` 会在 system prompt 渲染前读取 `project.yaml`、`experiments/results_summary.json`、`literature/synthesis.md`、`literature/related_work.bib`、`ideation/hypotheses.md`、`novelty/novelty_report.md` 和 `experiments/ablations.csv` 的预览。执行时它通常先用 `read_file` 补看完整 `results_summary.json` 和 `synthesis.md`，确认主贡献、方法家族、实验设置和可引用文献，再用 `write_file("drafts/outline.md", ...)` 生成大纲。大纲必须把实验结果和论文结构绑定起来，例如 Introduction 里要出现研究问题和贡献链，Experiments 里要对应主实验、baseline、ablation 和多 seed 证据。validator 会检查 `outline.md` 长度和 `##` 章节结构，不接受只有标题列表的空壳大纲。

### 6.12.2 T8-DRAFT：WriterAgent（draft）

#### 输入

会在 `outline` 基础上继续读取：

- `drafts/outline.md`
- `experiments/results_summary.json`
- `literature/related_work.bib`

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper` | `drafts/paper.tex` | 论文主稿 |

#### 作用

生成 LaTeX 初稿。

当前 Writer validator 会检查：

- `\documentclass`
- `\begin{document}`
- `\end{document}`
- 至少一个 `\section`
- 引用 key 是否都存在于 `.bib`

也就是说，`T8-DRAFT` 当前已经不仅仅看“有没有文本”，还会看是否像一篇最低限度可继续修订的 LaTeX 稿件。

#### 实际执行过程

`WriterAgent(draft)` 会读取 `drafts/outline.md` 作为骨架，再读取 `experiments/results_summary.json` 和 `experiments/ablations.csv` 取实验数字，读取 `literature/synthesis.md` 和 `literature/related_work.bib` 组织 related work 和引用。它用 `write_file("drafts/paper.tex", ...)` 写完整 LaTeX 初稿，必须包含 `\documentclass`、`\begin{document}`、abstract、Introduction、Related Work、Method、Experiments、Conclusion 和 bibliography。写作时所有数值必须来自 `results_summary.json` 或 ablation 表，所有 `\cite{...}` 的 key 必须存在于 `related_work.bib`。validator 会解析 `paper.tex` 的 LaTeX 基本结构，并从 bib 文件提取 key 检查引用是否悬空；因此这一步不能只写 markdown，也不能编造不存在的 citation。

### 6.12.3 T8-REVIEW-1 / T8-REVIEW-2：ReviewerAgent

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| - | `drafts/paper.tex` | 是 | 当前稿件 |
| - | `experiments/results_summary.json` | 否但强烈推荐 | 审核数字是否自洽 |
| - | `literature/related_work.bib` | 否但强烈推荐 | 审核引用 |

#### 输出

| 节点 | 输出 |
| --- | --- |
| `T8-REVIEW-1` | `drafts/review_rounds/round_1.md` |
| `T8-REVIEW-2` | `drafts/review_rounds/round_2.md` |

#### 审稿报告要求

当前 validator 要求至少有：

- `## 总体评价`
- `## 主要问题`
- `## 次要问题`

此外，Reviewer 现在已经明确支持先 `list_files` 再 `read_file`，不会再把目录当文件硬读。

#### 实际执行过程

`ReviewerAgent` 会先用 `list_files` 查看 `drafts/`、`experiments/` 和 `literature/` 中可用文件，避免把目录直接传给 `read_file`。随后它读取 `drafts/paper.tex`，必要时读取 `experiments/results_summary.json` 对照论文中的数字，读取 `literature/related_work.bib` 检查引用 key，第二轮还可以参考上一轮修订后的稿件状态。它不会修改论文正文，只用 `write_file("drafts/review_rounds/round_{N}.md", ...)` 写结构化审稿报告，至少包含总体评价、主要问题、次要问题、数字验证、引用验证和格式检查。validator 要求报告包含 `## 总体评价`、`## 主要问题`、`## 次要问题`，因此 Reviewer 必须输出可被 Writer 定位和执行的具体问题，而不是泛泛评价。

### 6.12.4 T8-REVISE-1 / T8-REVISE-2：WriterAgent（revise）

#### 输入

- `drafts/paper.tex`
- `drafts/review_rounds/round_1.md` 或 `round_2.md`

#### 输出

- 修订后的 `drafts/paper.tex`

#### 作用

根据对应轮次审稿意见修订主稿。

这一步的重点不是“重写一篇新论文”，而是：

- 针对上一轮 review 的主要问题做定向修补
- 保留已有可用段落和结构
- 让第二轮 review 能看到可比较的改进

#### 实际执行过程

`WriterAgent(revise)` 会读取当前 `drafts/paper.tex` 和对应的 `drafts/review_rounds/round_{N}.md`，再按需读取 `results_summary.json`、`ablations.csv`、`related_work.bib` 和 `synthesis.md` 来补证据。它应该逐条处理 review 中的 High/Medium 问题：例如数字不一致就回到 `results_summary.json` 修正，引用缺失就改用 bib 中真实 key，实验描述不足就补设置和 ablation，related work 漏项就从 synthesis 和 bib 中补段落。输出仍然是覆盖写回 `drafts/paper.tex`，而不是另起一个新文件。validator 与 draft 阶段相同，会重新检查 LaTeX 基本结构和 citation key；所以 revise 阶段要保持文档可编译、引用不悬空，并避免把审稿意见原文直接粘进论文。

### T8 的整体语义

它不是“生成一稿就结束”，而是一个：

```text
outline
 -> draft
 -> review
 -> revise
 -> review
 -> revise
```

的多轮写作链。

### T8 的恢复语义

T8 的恢复主要依赖已有 artifact：

- 如果 `outline.md` 已存在，`T8-WRITE` 重跑时应倾向于更新而不是重建
- 如果 `paper.tex` 已存在，`T8-DRAFT` / `T8-REVISE-*` 应基于现稿继续
- 如果 `round_1.md` / `round_2.md` 已存在，Reviewer 重跑时应把它们当已有审稿历史

也就是说，T8 的“恢复”更像文稿迭代，而不是实验那种结构化 resume state。

### T8 单独运行 vs 完整运行

- 单独 `run-task T8-DRAFT`
  - 适合只调写作 prompt 或 validator
- 完整 `run/resume`
  - 会按状态机依次走完 `T8-WRITE -> T8-DRAFT -> T8-REVIEW-1 -> ...`
  - 更适合真实写作流程

### T8 常用单任务示例

```bash
cd ResearchOS
researchos run-task T8-WRITE --workspace ./workspace/local-test2
researchos run-task T8-DRAFT --workspace ./workspace/local-test2
researchos run-task T8-REVIEW-1 --workspace ./workspace/local-test2
researchos run-task T8-REVISE-1 --workspace ./workspace/local-test2
```

---

## 6.13 T9：SubmissionAgent

### 角色

- Agent：`SubmissionAgent`
- 代码： [researchos/agents/submission.py](../researchos/agents/submission.py)
- Prompt： [researchos/prompts/submission.j2](../researchos/prompts/submission.j2)

### 当前默认配置

- model tier：`medium`
- max steps：`40`
- max tokens：`80000`
- max wall seconds：`300`
- 可选匿名化 pre-hook：由配置控制

主要工具：

- `read_file`
- `write_file`
- `list_files`
- `bash_run`
- `docker_exec`
- `finish_task`

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

### T9 现在到底做什么

当前 T9 已经不是“试着打包一下就结束”，而是：

1. 读取主稿与项目配置
2. 迁移到目标会议模板
3. 生成 `submission/bundle/`
4. 尝试编译
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
- 最多进行 `max_compile_attempts` 轮

当前配置默认：

- `max_compile_attempts = 4`

### T9 当前的 validator 有多严格

现在 validator 强制要求：

- `submission/bundle/` 存在
- `main.tex` 存在
- `references.bib` 存在
- `main.pdf` 存在
- `migration_report.md` 存在
- 报告里必须明确写出：
  - `迁移状态`
  - `编译状态`
  - `匿名化检查`
- 报告里必须出现：
  - `编译状态: 成功`
- 如果 `main.log` 存在，不能还有 fatal error 标记

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
- T5：
  - `pilot_resume_state.json`
- T7：
  - `full_resume_state.json`
- T7.5 / T8 / T9：
  - 读取已有产物并补缺

### 真实含义

恢复不是“从上次模型上下文继续”，而是：

- 扫描已有 workspace
- 识别已完成与未完成的 artifact
- 只补剩余工作

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
- `ask_human` 是 agent 工具级的人类输入；如果当前 stdin 不可交互、已关闭或收到空回答，runtime 会暂停任务，而不是把空输入当成用户选择继续喂给 LLM
- 预算 gate 的等待时间会从 wall-clock budget 中扣除，避免“等用户输入”本身把任务预算耗尽

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
