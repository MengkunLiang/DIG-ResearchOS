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
 -> T7 完整实验
 -> T7.5 PI 评估
 -> Human Gate
 -> T8 资源索引 / 分章节写作 / 拼装审计 / 审稿 / 修订
 -> T9 投稿包构建、编译、修复与收尾
```

当前主链暂时采用 `T4.5 -> T7` 的 direct-full 入口：`T5` Pilot 和 `T6` 基于 Pilot 的新颖性复核仍保留为可单独运行的增强节点，但完整 pipeline 默认不再强制等待它们。`T7` 会把已有的 `pilot/` 和 `novelty/` 产物作为可选增强输入；若不存在，则从 `ideation/novelty_audit.md`、`literature/synthesis.md`、`literature/comparison_table.csv` 和 `ideation/idea_scorecard.yaml` 推导完整实验需要补的 baseline 与风险边界。

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
 -> T7
 -> T7.5
 -> human gate
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-METHOD
 -> T8-SEC-EXPERIMENTS
 -> T8-SEC-RELATED
 -> T8-SEC-ANALYSIS
 -> T8-SEC-INTRO
 -> T8-SEC-LIMITATIONS
 -> T8-SEC-CONCLUSION
 -> T8-SEC-ABSTRACT
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T9
 -> done
```

几个最容易记错的点：

- `HELLO` 是显式运行的 smoke task，不是主链起点；主链的 `initial_state` 是 `T1`
- 当前主链暂时跳过 `T5` / `T6`，直接从 `T4.5` 进入 `T7`；`T5` / `T6` 不是删除，而是可用 `run-task` 单独执行的可选增强阶段
- `T8` 不是一个节点，而是资源索引、大纲、逐章节写作、拼装、自查、审稿、修订组成的多节点链；旧报告或旧 gate 中的 `next_task: T8` / `next_task: T8-WRITE` 会被状态机安全映射到新的入口 `T8-RESOURCE`
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
researchos run-task T8-RESOURCE \
  --workspace ./workspace/scratch-write \
  --from ./workspace/local-test2
```

这会：

- 按 `T8-RESOURCE` 的 I/O 契约找到前置输入
- 从 `local-test2` 拷到 `scratch-write`
- 然后只跑 `T8-RESOURCE`

新写作链的推荐调试入口是 `T8-RESOURCE`。如果你还使用旧命令 `researchos run-task T8 --workspace ...`，单任务运行器会把它视为 `T8-RESOURCE`，避免绕过资源索引和章节计划。

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
| `T3` | `ReaderAgent` | `read` | 逐篇精读并形成结构化证据（含 §13 Mechanism Claim） | `paper_notes/`, `comparison_table.csv`, `related_work.bib` |
| `T3.5` | `ReaderAgent` | `synthesize` | 从单篇证据分阶段压缩成领域综合 | `synthesis_workbench.json`, `synthesis_outline.md`, `synthesis_draft.md`, `synthesis.md` |
| `T4` | `IdeationAgent` | - | 生成假设、实验计划、idea证据链、Gate决策链、风险评估 | `hypotheses.md`, `exp_plan.yaml`, `idea_scorecard.yaml`, `rejected_ideas.md`, `gate_decisions.json`, `idea_rationales.json`, `risks.md`, `_family_distribution.md` |
| `T4.5` | `NoveltyAuditorAgent` | - | 对假设做新颖性预审（含 mechanism tuple 碰撞检测） | `novelty_audit.md`, `_mechanism_tuples/`; 有撞车时另写 `collision_cases.md` |
| `T5` | `ExperimenterAgent` | `pilot` | 可选增强：用小规模实验验证方向值不值得继续 | `pilot_plan.yaml`, `pilot_code/`, `pilot_results.json`, `motivation_validation.md` |
| `T6` | `NoveltyAgent` | - | 可选增强：基于 Pilot 做增量 novelty 复核 | `novelty_report.md`, `collision_cases.md`, `must_add_baselines.md` |
| `T7` | `ExperimenterAgent` | `full` | 完整实验与主结果、ablation、multi-seed；当前主链从 T4.5 direct-full 进入 | `results_summary.json`, `runs/`, `configs/`, `iteration_log.md`, `ablations.csv` |
| `T7.5` | `PIAgent` | `evaluate` | 评估实验结果是否足以写论文 | `evaluation_decision.md` |
| `T8-RESOURCE` | `WriterAgent` | `resource_index` | 索引写作资源并生成章节/证据/图表计划 | `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, `drafts/figure_table_plan.json` |
| `T8-WRITE` | `WriterAgent` | `outline` | 基于资源索引写论证大纲 | `drafts/outline.md` |
| `T8-SECTION-PLAN` | `WriterAgent` | `section_plan` | 初始化逐章节共享状态和每章作业单 | `drafts/paper_state.json`, `drafts/section_outlines/*.md` |
| `T8-SEC-METHOD` | `WriterAgent` | `section_draft` | 只写 Method 单章 | `drafts/sections/methodology.tex` |
| `T8-SEC-EXPERIMENTS` | `WriterAgent` | `section_draft` | 只写 Experiments 单章 | `drafts/sections/experiments.tex` |
| `T8-SEC-RELATED` | `WriterAgent` | `section_draft` | 只写 Related Work 单章 | `drafts/sections/related_work.tex` |
| `T8-SEC-ANALYSIS` | `WriterAgent` | `section_draft` | 只写 Analysis/Discussion 单章 | `drafts/sections/analysis.tex` |
| `T8-SEC-INTRO` | `WriterAgent` | `section_draft` | 在 Method/Experiments 后只写 Introduction | `drafts/sections/introduction.tex` |
| `T8-SEC-LIMITATIONS` | `WriterAgent` | `section_draft` | 只写 Limitations 单章 | `drafts/sections/limitations.tex` |
| `T8-SEC-CONCLUSION` | `WriterAgent` | `section_draft` | 只写 Conclusion 单章 | `drafts/sections/conclusion.tex` |
| `T8-SEC-ABSTRACT` | `WriterAgent` | `section_draft` | 最后只写 Abstract | `drafts/sections/abstract.tex` |
| `T8-DRAFT` | `WriterAgent` | `draft` | 用 tool 拼装章节、spot-check、机械审计 | `drafts/paper.tex`, `drafts/manuscript_audit.md` |
| `T8-SELF-CHECK` | `WriterAgent` | `self_check` | 作者自查数字、引用、图表、论证链 | `drafts/self_check.md` |
| `T8-REVIEW-1` | `ReviewerAgent` | round 1 | 第一轮逐章节审稿和综合审稿 | `drafts/review_rounds/round_1_sections/*.md`, `drafts/review_rounds/round_1.md` |
| `T8-REVISE-1` | `WriterAgent` | `revise` | 第一轮按 section patch 修订 | `drafts/patches/round_1_patches.json`, `drafts/revision_response_round_1.md`, `drafts/paper.tex` |
| `T8-REVIEW-2` | `ReviewerAgent` | round 2 | 第二轮逐章节审稿，检查上一轮闭环 | `drafts/review_rounds/round_2_sections/*.md`, `drafts/review_rounds/round_2.md` |
| `T8-REVISE-2` | `WriterAgent` | `revise` | 第二轮按 section patch 修订 | `drafts/patches/round_2_patches.json`, `drafts/revision_response_round_2.md`, `drafts/paper.tex` |
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

### 实际执行过程

`ScoutAgent` 运行时先读 `project.yaml` 获取 `research_direction`、`keywords`、`target_venue` 和约束；再读 `user_seeds/seed_papers.jsonl`、`seed_ideas.md`、`seed_constraints.md`、`seed_external_resources.jsonl`，如果存在 `seeds/T2_scout/papers/*.pdf` 还会先用 `scan_seed_papers` 抽取本地 PDF seed metadata。随后 prompt 会注入 `literature-scout` guidance，要求 LLM 先归纳 `domain_profile`：目标领域、include/exclude concepts、歧义词、相关子领域、候选 venue/category 和多角度 query。`expand_queries` 只负责把 LLM 设计的 query、seed 标题短语和时间窗口合并去重，不再内置“memory/retrieval/agent 属于 AI/CS”这类学科判断；如果需要领域过滤，`filter_by_domain` 也必须接收 LLM 提供的 `domain_profile`，否则不做过滤。之后用 `detect_duplicate_queries` 检查 query 是否只是同义重复。

真正抓论文时默认调用 `openalex_search`、`crossref_search`、`arxiv_search`、`semantic_scholar_search`、`elsevier_scopus_search` 和 `informs_search`。其中 `informs_search` 是通过 Crossref DOI prefix `10.1287` 检索 INFORMS 论文元数据，适合 OR/MS、management science、supply chain、queueing、optimization 等方向，也可以作为低成本补充源默认启用；如果某个主题不在 INFORMS 强覆盖范围内，它通常只会返回 0 篇或少量噪声，T2 记录后继续。`domain_profile` 的作用是解释和过滤结果，不是决定是否完全跳过 INFORMS。

每次工具返回的 `data.papers` 会被 `AgentRunner` 自动追加到 `literature/papers_raw.jsonl`，不需要模型手工复制 JSON。Scout 可以在搜索工具调用中附带 `query_bucket`，例如 `core`、`baseline`、`evaluation`、`adjacent_field`、`theory_bridge`；runtime 只保存这个显式标签，不根据关键词猜学科。raw 达到阈值后，runtime 会确定性调用去重、metadata priority hint、enrich、metadata verification、access audit 和 deep-read queue 构建逻辑，依次产出 `papers_dedup.jsonl`、`papers_verified.jsonl`、`verification_failures.jsonl`、`deep_read_queue.jsonl`、`access_audit.md`、`search_log.md` 和 `missing_areas.md`；最后 `ScoutAgent.validate_outputs()` 再检查数量、schema、`dedup <= raw`、queue 是否来自 verified 池，以及 seed paper 是否进入队列。

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

最终得到 `relevance_score`、`priority_score_hint`、`relevance_score_semantics=metadata_priority_hint_requires_llm_review`、`relevance_score_components`。T2 恢复路径不会再用 `relevance_score >= 0.5` 硬过滤论文；如果池子超过 120 篇，只做保守排序裁剪。

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

为支持 CDR 的跨域类比和理论桥接，`build_deep_read_queue` 会为显式标注 `search_bucket=adjacent_field` 或 `search_bucket=theory_bridge` 的论文保留少量名额（约 deep-read target 的 15%，至少 1 篇，受队列大小限制）。这是机械的 queue protection：标签必须来自 Scout LLM 或上游 metadata，工具不硬编码“哪些主题属于相邻领域”，也不把这类论文自动判定为高质量。

### T2 怎样做 access audit

工具：

- `build_access_audit`

它会给出：

- 本地 PDF 数
- seed PDF 数
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

### T2 的成功标准

当前 validator 会检查：

- `papers_dedup.jsonl` 数量在 `10-120`（最低阈值 10，注释写"10篇高质量论文优于15篇低质量论文"）
- 关键字段存在
- `dedup <= raw`
- `papers_verified.jsonl` 存在且数量合理
- `verification_failures.jsonl` schema 正确
- `deep_read_queue.jsonl` 必须来自 verified 池
- 如果存在 seed papers，queue 中必须保留 seed

#### `missing_areas.md` 结构化校验

如果 `missing_areas.md` 存在且包含 `## Retrieval Coverage Hints`，validator 会：

1. 按 `### 提示 \d+` 正则拆分每个提示段落
2. 对每个提示检查四个必需加粗字段：`**覆盖缺口**`、`**为什么需要复核**`、`**建议动作**`、`**难度**`
3. 如果缺少任一字段，validation 失败

同时会拒绝旧版模板：如果内容包含 `## 可探索缺口`、`为什么是缺口`、`可探索方向`，validation 失败。这意味着旧格式的 `missing_areas.md` 必须迁移为新结构化格式。

### T2 的确定性终结（Orchestrator Hook）

T2 不一定需要 LLM 跑满所有步数才结束。Orchestrator 有两条确定性终结路径：

#### 路径 1：正常运行中自动终结

在每个 tool batch 执行后，orchestrator 检查 `_maybe_finalize_t2_after_tool_batch`：

- **触发条件**：T2 的 search 工具（`multi_source_search`、`search_papers`、`arxiv_search`、`openalex_search`、`crossref_search`、`semantic_scholar_search`）或 `append_papers_raw` / `process_papers_raw` / `save_papers_raw` 成功执行
- **阈值**：`papers_raw.jsonl` 行数达到 `T2_AUTO_FINALIZE_MIN_RAW`（默认 100），下限 10
- **行为**：调用 `_finalize_t2_from_raw`，从 `papers_raw.jsonl` 确定性产出所有 T2 产物（dedup、verified、queue、access audit、missing_areas 等），跳过 LLM 继续搜索

#### 路径 2：恢复时预终结

在 runner 启动时，`_maybe_finalize_t2_before_llm` 检查：

- **触发条件**：T2 恢复运行，且 `papers_raw.jsonl` 已存在
- **阈值**：`min_raw_count=1`（只要有 raw 数据就尝试终结）
- **行为**：同路径 1，但从更低阈值开始

两条路径都调用 `_finalize_t2_from_raw`，内部流程：

1. 检查 `papers_raw.jsonl` 存在且行数满足阈值
2. 如果任何非 `papers_raw` 的期望产物缺失或校验失败，调用 `finalize_t2_outputs()` 从 raw 确定性产出全部产物
3. 验证完整输出集

这意味着：**只要 Scout 搜到足够多的 raw 论文，后续的 dedup/verified/queue/audit/missing_areas 全部由工具确定性完成，不需要 LLM 反复调用工具。** LLM 的搜索策略仍然重要（决定搜什么、怎么扩展 query），但产物组装不再依赖 LLM。

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
| `missing_areas` | `literature/missing_areas.md` | 否 | 后续综合时的检索覆盖提示 |

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper_notes_dir` | `literature/paper_notes/` | 每篇论文一份结构化笔记；包含 `Reading Coverage` 阅读覆盖记录 |
| - | `literature/paper_notes_abstract/` | abstract sweep 补读的精简笔记（可选，由 orchestrator hook 自动生成） |
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

`ReaderAgent(read)` 进入时会先列出 `literature/paper_notes/`，再读取 `literature/deep_read_queue_pending.jsonl`；如果没有 pending queue，就读 `deep_read_queue.jsonl`，再回退到 `papers_verified.jsonl`，最后才回退到 `papers_dedup.jsonl`。处理每篇论文前，它用 `lookup_paper_record` 或小范围 `read_file` 取该论文 metadata，规范化论文 ID 后检查是否已有同名 note；已有 note 只有在结构、Evidence 锚点和 `Reading Coverage` 都合格时才算完成。这里的“规范化”只用于文件路径：例如真实 ID `arxiv:2301.12345` 写文件时用 `literature/paper_notes/arxiv_2301.12345.md`、PDF 用 `literature/pdfs/arxiv_2301.12345.pdf`；正文引用可以写 `[arxiv:2301.12345]` 或 `[arxiv_2301.12345]`，只要能对应到真实 note。若 PDF 不在本地，它会调用 `fetch_paper_pdf(paper_id=..., save_path="literature/pdfs/{normalized_id}.pdf")` 尝试下载；拿到 PDF 后用 `extract_pdf_text` 按页读取，如果返回的 metadata 显示 `preview_truncated_by_max_chars=true` 或 runtime 上下文裁剪，就继续用更小的 `start_page/max_pages` 分块重读，直到 `Pages read` 覆盖 `1-total_pages`。写出时用 `write_file("literature/paper_notes/{normalized_id}.md", ...)` 生成 13 节结构化 note，并用 `append_file` 或重写方式维护 `comparison_table.csv` 和 `related_work.bib`。收尾时 validator 会逐篇扫描 note，检查 `- **Status**:`、核心章节、数字证据 `[Evidence: ...]`、全文页码覆盖、最终截断状态、queue 覆盖率和 seed paper 覆盖情况。

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

### paper note 的 Mechanism Claim

每篇 note 现在还必须包含第 13 节，用于后续 T4/T4.5 的 mechanism-aware 假设生成和碰撞检测：

```markdown
## 13. Mechanism Claim
- **Stated mechanism**: {一句话，作者声称是什么导致 improvement；实在抽不出来写 "not clearly stated"}
- **Evidence type**: {ablation_supported | theoretically_justified | empirical_correlation | claimed_untested}
- **Supporting artifact**: {Fig X / Table Y / Section Z / "none"}
```

这个字段会被 T4 的四类来源约束（机制质疑型）和 T4.5 的 `extract_mechanism_tuple` 工具消费。如果 §13 缺失或字段为空，validator 会拒绝通过该 note。

### T3 当前的恢复机制

这是当前最成熟的恢复阶段之一。

核心行为：

- 根据已有且通过结构校验的 `paper_notes/` 自动裁出 `deep_read_queue_pending.jsonl`
- 重新跑时优先读取 pending queue
- `deep_read_queue_pending_meta.json` 是恢复快照，不是逐 token 实时进度；现在 T3 在成功、预算/步数暂停、校验修复暂停和失败退出时都会 best-effort 刷新该快照，避免用户看到旧的 `completed_note_count`
- pending 裁剪不只看 note 文件名，还会解析 note 头部的 `ID`、`DOI/arXiv` 和标题，并与 queue 的 `normalized_id`、`paper_id`、`title`、`doi`、`externalIds` 做多 key 匹配，避免 `arxiv:2605.17641` / `arxiv_2605.17641_Title.md` 这类写法差异导致重复阅读
- 缺少 `Reading Coverage`、`Mechanism Claim`、`[FULL-TEXT]` 页码不完整、或最终截断状态未说明为 `none` / `无` / 已解决的旧 note，不会被视为已完成
- `paper_notes/` 中历史重复 stub 或坏 note 不再直接拖死整体验证；只有结构合格 note 会计入完成数，若合格数量/queue 覆盖不足，validator 会把坏 note 示例写入错误，指导后续定向修复
- 不再默认把整个 `papers_dedup` 当作“必须重读”的任务池

`deep_read_queue_pending_meta.json` 当前会记录 `completed_note_count`、`completed_note_key_count`、`pending_queue_count`、`valid_note_file_count`、`invalid_note_file_count` 和 `refresh_reason`。其中 `completed_note_count` 指结构合格的 note 文件数，`completed_note_key_count` 是为了跨 ID/标题/DOI 匹配而生成的内部 key 数，二者不应该混用。

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
- note 结构合理，包含全部 13 个章节
- 每篇 note 包含 `## 12. Reading Coverage`
- 每篇 note 包含 `## 13. Mechanism Claim`，三个 bullet（Stated mechanism / Evidence type / Supporting artifact）均非空
- `[FULL-TEXT]` note 必须记录完整页码覆盖和最终无截断；分块重读覆盖全篇是合法的
- 如果 queue 存在，则至少完成 queue 中的 `deep_read_min`
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

Deep read 完成后，orchestrator 自动运行 abstract sweep，从 verified/dedup 池中再扫一批论文，只基于 abstract 生成精简 note。

配置在 `config/agent_params.yaml` 的 `reader.modes.read.abstract_sweep`：

```yaml
abstract_sweep:
  enabled: true
  lite_paper_num: 40      # 最多扫多少篇
  min_relevance: 0.4      # relevance_score 阈值
  sources: [papers_verified, papers_dedup]
  exclude_already_read: true
```

产出：
- `literature/paper_notes_abstract/` — 精简 note（5 节 + §13 Mechanism Claim）
- `comparison_table.csv` 追加行（`evidence_level=ABSTRACT_ONLY`）
- `related_work.bib` 追加条目

Abstract note 结构：
- §1 Problem & Motivation（abstract opening snippet，标记 LLM_REVIEW_REQUIRED）
- §2 Method Summary（abstract middle snippet，标记 LLM_REVIEW_REQUIRED）
- §3 Key Claimed Results（abstract closing snippet，标记 LLM_REVIEW_REQUIRED）
- Raw Abstract（原始摘要全文）
- §13 Mechanism Claim（Evidence type 固定为 `abstract_claim_hint`）
- Source（标注 abstract only）

全确定性，不调 LLM。它只做覆盖扩展和片段保存，不做论文理解；后续 Reader/Ideation LLM 必须复核这些片段。失败不影响 T3 完成状态。

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
- 方法对比表（`comparison_table.csv`）
- T2 发现的检索覆盖提示（`missing_areas.md`）

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

Reader 加载 `literature-synthesis` guidance 后，先用自己的 LLM 能力逐篇分析 `paper_notes/` 和 `paper_notes_abstract/` 中的笔记，生成四类洞察：

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
1. 解析 `paper_notes/*.md` 和 `paper_notes_abstract/*.md`，提取每篇 note 的标题、年份、方法概述、关键结果、局限、问题和 §13 Mechanism Claim
2. 读取 `comparison_table.csv` 提取指标和效率线索
3. 如果存在 `missing_areas.md`，纳入候选问题（只保留为 coverage hints，不写成已验证研究缺口）
4. **用 LLM 提供的 `family_classifications` 做方法家族聚类**（不再使用硬编码关键词匹配）；如果 LLM 未提供某篇论文的分类，回退到 `LLM_REVIEW_REQUIRED: {method_text}` 占位
5. **用 LLM 提供的 `shared_assumptions` 生成共同假设候选**；如果未提供，回退到从 note 的 Limitations/Gaps 段落提取 review hint
6. **用 LLM 提供的 `trends` 生成趋势候选**；如果未提供，回退到按年份分组的 chronological evidence
7. **用 LLM 提供的 `research_questions` 生成研究问题候选**；如果未提供，回退到从 note 的 questions/gaps 段落提取
8. 构建 `mechanism_claim_clusters`（从 §13 Mechanism Claim 按关键词相似度聚类）
9. 写 `synthesis_workbench.json`、`synthesis_outline.md` 和 guidance 型 `synthesis_draft.md`

工具只写这三类 staged artifacts，不写最终 `synthesis.md`（`write_final` 默认为 `false`）。

#### 阶段 3：LLM 审阅与写作

Reader 读取工具生成的 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`，但不把工具草稿当作最终文献判断。LLM 必须检查：

1. 方法家族分类是否准确反映了论文实际内容
2. 共同假设是否有足够的论文支持
3. 研究问题是否可操作
4. 引用是否来自真实 paper_notes

然后由 LLM 直接写 `literature/synthesis.md`。如果工具产物中的候选分类、假设或问题不合理，必须改写。

最后 `finish_task` 触发 validator 检查章节、长度和 paper note 引用数量。

### 期望章节

当前实现和文档都要求至少覆盖：

1. 方法家族分类
2. 共同假设
3. 贡献空间地图（Contribution-Space Map）
4. 跨论文矛盾/张力（Trends & Cross-Paper Contradictions）
5. 技术趋势
6. 可操作研究问题

### `synthesis_workbench.json` 的结构

这个文件是 T3.5 最重要的结构化产物，包含以下字段：

| 字段 | 含义 |
| --- | --- |
| `note_count` | full-text note 数量 |
| `abstract_note_count` | abstract-only note 数量 |
| `total_note_count` | 总 note 数量 |
| `paper_ids` | 所有论文 ID 列表 |
| `method_families` | 方法家族聚类（最多 5 个），每个包含 `name`、`paper_ids`、`representative_titles`、`core_observations`、`result_observations`、`_abstract_count`。当 LLM 提供 `llm_insights.family_classifications` 时，家族名称和成员分配由 LLM 决定；否则回退为 `LLM_REVIEW_REQUIRED: {method_text}` 占位 |
| `shared_assumption_candidates` | 共同假设候选，每个包含 `assumption`、`why_questionable`、`supporting_papers`。当 LLM 提供 `llm_insights.shared_assumptions` 时直接使用 LLM 分析；否则从 note 的 Limitations/Gaps 段落提取 review hint |
| `mechanism_claim_clusters` | 机械聚合的机制 claim cluster hint（见下文） |
| `domain_consensus` | 兼容旧代码的 alias；语义同 `mechanism_claim_clusters`，不要当成已验证领域共识 |
| `metric_landscape_hints` | 指标/效率上下文 hint，不是 opportunity map；T4 机会生成应优先消费 contribution_space 和 tensions |
| `contribution_space` | CDR 贡献空间 hint：按 contribution_type、artifact 类型、design_rationale snippets 组织，供 LLM 复核 |
| `cross_paper_tensions` | 跨论文矛盾/设计论证竞争素材；是 T4 Pass 1 的生成燃料，不是 provenance gate |
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
- 必须覆盖约定的核心章节（方法家族、共同假设、贡献空间地图、跨论文矛盾/张力、技术趋势、可操作研究问题）
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
| `missing_areas` | `literature/missing_areas.md` | 否 | T2 的检索覆盖提示 |
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
| `family_distribution` | `ideation/_family_distribution.md` | mechanism family 分布统计 |
| - | `ideation/_candidate_directions.json` | 中间候选方向 |
| - | `ideation/_premortem.md` | pre-mortem 质疑结果 |

### T4 的流程不是“一次生成完”

当前 prompt 明确把它设计成了多阶段思考：

1. 读取 `synthesis.md`
2. 生成 `3-5` 个主线候选方向：free reasoning / seed refinement / evidence-driven
3. 再用四类补充通道做 coverage supplement；有证据才生成候选，没有证据则记录 unsupported
4. 对每个候选写七维评分、机制、prediction、counterfactual、最低实验和 kill criteria
5. 用 `ask_human` 做 Gate1
6. 对选定方向做 pre-mortem
7. 写 `_family_distribution.md` 统计 mechanism family 分布
8. 最终产出：
   - `hypotheses.md`
   - `exp_plan.yaml`
   - `idea_scorecard.yaml`
   - `rejected_ideas.md`
   - `gate_decisions.json`
   - `idea_rationales.json`
   - `risks.md`

### 实际执行过程

`IdeationAgent` 的执行分为两个阶段（Gate1 + Gate2），中间有 pre-mortem 检查。

#### 阶段A：发散候选方向 + Gate1

1. **读取输入**：`project.yaml`、`literature/synthesis.md`、`literature/comparison_table.csv`、`literature/missing_areas.md`、`user_seeds/seed_ideas.md`、`user_seeds/seed_constraints.md`

2. **深度分析 synthesis**：用 `read_file` 读取完整的 `literature/synthesis.md`，理解 Q1-QN、方法家族、共同假设、贡献空间地图、跨论文矛盾/张力、趋势和可操作问题

3. **读取 workbench**：用 `read_file` 读取 `literature/synthesis_workbench.json`，获取 `method_families` 和 `mechanism_claim_clusters`（旧产物可能叫 `domain_consensus`）。这些 cluster 是工具 hint，T4 必须先复核，不能直接当领域共识。

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
   - `idea_origin`（如 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`evidence_driven`、`mechanism_challenge`）
   - `constraint_status`（`mainline` / `supplement` / `not_supported_by_current_evidence`）
   - `closest_baselines`（可空；无相近工作时 `prior_art: none` 是合法高新颖信号，但要标风险）
   - `scores`（七维评分：novelty/feasibility/impact/evaluability/differentiation/cost/contribution_strength）
   - `minimum_experiment`（最低可行实验）
   - `key_risks`（风险和 kill criteria）
   - `source.seed_alignment`（与用户 seed 的对齐程度：`direct`/`partial`/`none`）

5. **写 `_family_distribution.md`**：统计 mechanism family 分布，标注同 family 的候选

6. **写 `ideation/_candidate_directions.json`**：中间候选方向

7. **Gate1**：用 `ask_human` 呈现候选方向，暴露 mechanism/counterfactual 是否为占位词，让用户选择、合并、补充或重新分析

8. **更新决策链**：每次 Gate1 后更新 `idea_scorecard.yaml`、`rejected_ideas.md`、`gate_decisions.json`

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
- `_candidate_directions.json` 顶层必须使用 `candidates`，不能使用旧字段 `directions`；每个候选必须有 `idea_origin`、`constraint_status` 和足够长的 `basis_summary`
- `idea_scorecard.yaml` 的每个 `source` 必须显式包含 `idea_origin` 和 `constraint_status`；origin mix 至少要包含 CDR schema 中的主线 origins（如 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`evidence_driven`），不能全部由四类补充候选构成
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
  - `idea_origin`：`free_reasoning` / `seed_refinement` / `evidence_driven` / 四类 supplement
  - `constraint_status`：`mainline` / `supplement` / `not_supported_by_current_evidence`
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

最终报告必须分开写 `Collision Axis` 与 `Ambition Axis`，并给出 `Contribution Distance` 和 `Final Gate Verdict`。没有 close baseline 不能被惩罚为低 novelty；应写成高新颖/高风险。`contribution_type=routine` 或 `routine_risk` 不能无条件进入 T7/T8，必须建议回到 T4 重新 framing 或放弃。

同时，它加载 `novelty-audit` guidance，用 LLM 从每个假设中提取机制因果断言、操作对象和预期效果，再调用 `extract_mechanism_tuple` 保存 tuple；如果领域里需要更细的标签，可以把 `normalized_input_signal` 一并传给工具，而不是被工具枚举限制。对每篇疑似撞车论文，LLM 先阅读摘要/metadata 提取机制 tuple，再调用 `compare_mechanism_tuples` 获取 mechanical similarity hint。该工具只返回 `possible_true_collision` / `possible_mechanism_collision` / `possible_explanatory_competition` / `likely_distinct` 这类待审提示，不能直接给最终新颖性结论。

审计时它把搜索结果、synthesis、comparison table 和 mechanism hint 对齐，由 LLM 判断相似点、差异点、证据强度、是否需要补 baseline，以及最终标签 `true_collision / mechanism_collision / explanatory_competition / safe`。只有 LLM 确认机制、任务边界和贡献点都高度一致时，才把对应假设降为 Level 0。最后用 `write_file(“ideation/novelty_audit.md”, ...)` 写每个假设的 Level 0-3 判定。如果报告中出现真实 High/Medium Overlap，它还必须写 `ideation/collision_cases.md`，记录论文、相似点、差异点和处理建议；validator 会区分”High Overlap: none”这种空标题和真实案例，只有真实案例才强制 collision 文件。

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

T7 进入 LLM 前会先执行 `run_experimenter_preflight()`：读取 `exp_plan.yaml`、项目预算、
direct-full 必需输入和 `agent_params.yaml` 的 `docker_required/gpu_required`。如果 Docker
命令、daemon 或统一镜像不可用，会以 `WAITING_ENVIRONMENT` 暂停；如果计划需要 GPU
但 Docker 没有 nvidia runtime，除非 `project.yaml` 明确设置
`compute_budget.allow_cpu_fallback: true`，也会暂停等待环境。这一步的目标是把“无
Docker/无 GPU”前移到 LLM 调用之前。

### 输入文件

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 预算、资源、方向 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设 |
| `exp_plan` | `ideation/exp_plan.yaml` | 是 | 全实验计划 |
| `pilot_results` | `pilot/pilot_results.json` | 否 | Pilot 结果；direct-full 下可选 |
| `pilot_code` | `pilot/pilot_code/` | 否 | 可复用的前一阶段代码 |
| `novelty_report` | `novelty/novelty_report.md` | 否 | T6 的结论；direct-full 下可选 |
| `must_add_baselines` | `novelty/must_add_baselines.md` | 否 | T7 可补基线；direct-full 下可选 |

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

`ExperimenterAgent(full)` 先读 `ideation/exp_plan.yaml`、`ideation/hypotheses.md`、`ideation/novelty_audit.md`、`literature/synthesis.md`、`literature/comparison_table.csv` 和 `ideation/idea_scorecard.yaml`；如果 `pilot/` 或 `novelty/` 已存在，再把 `pilot/pilot_results.json`、`pilot/pilot_code/`、`novelty/novelty_report.md` 和 `novelty/must_add_baselines.md` 作为增强输入。它会优先复用 T5 的 pilot 代码，把可复用部分迁移到 `experiments/code/run_exp.py` 或 `experiments/runs/*`，再根据 full mode 要求扩展数据规模、seed ensemble、ablation 和必须补的 baseline。执行实验时，它用 `docker_exec` 运行主实验、baseline 和 ablation；每个实验应有独立 `experiments/runs/{run_id}/`，包含配置、日志、指标和必要输出。运行过程中用 `append_file` 维护 `experiments/iteration_log.md`，记录每轮修改、失败、修复和指标变化；用 `write_structured_file` 或 `write_file` 写 `experiments/results_summary.json`，其中每个实验要有 `experiment_id`、`tier`、`seed_runs`/`seeds`、metrics 和 `quality_status`。收尾时还要写 `experiments/ablations.csv`、`experiments/seed_ensemble_summary.json`、`experiments/iteration_diversity_check.md` 和 `experiments/docker_digests.txt`。validator 会检查 headline 至少 3 seeds、final_method 至少 2 seeds、ablation 至少 3 条、真实 docker digest 和成功 Docker 执行证据，并运行 failure mode 检查识别 nan loss、离谱指标或消融不足。

### T7 的 validator 真正在卡什么

当前 T7 最常见的校验失败点包括：

- `results_summary.json` 结构不符合预期
- headline 实验没有足够 seed
- `ablations.csv` 不足 3 条
- `iteration_log.md` 缺失
- `seed_ensemble_summary.json` 缺失
- `iteration_diversity_check.md` 缺失
- `docker_digests.txt` 缺失
- `docker_digests.txt` 不是 `sha256:` / `@sha256` 形式的真实 digest，或写成 `local build / no remote digest / 未使用 Docker`
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

`PIAgent(evaluate)` 启动后读取 `experiments/results_summary.json`、`experiments/iteration_log.md` 和 `ideation/exp_plan.yaml`。它会把实验结果按原计划对齐：哪些 hypothesis 被验证，哪些实验失败或缺失，主指标是否达到目标，ablation 和多 seed 证据是否足够，是否还有 T6 要求的 baseline 没补。然后它用 `write_file("evaluation/evaluation_decision.md", ...)` 写一份决策报告，必须包含 `Situation`、`Options` 和至少一个 `next_task`。典型 next_task 可以是 `T8-RESOURCE`（证据足够，进入新版写作入口）、`T7`（继续补实验）、`T4`（回到假设重构）或其他状态机允许的节点；旧报告写 `T8` / `T8-WRITE` 时会映射到 `T8-RESOURCE`。完整 pipeline 中，StateMachine 会从 `evaluation_decision.md` 反向解析 `next_task`，再把 PI 推荐交给 human gate；用户可以接受推荐，也可以在 gate 里选择其它路径。

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

详细写作设计、官方写作规范参考、claim/evidence/figure 计划和后续路线见 [docs/manuscript.md](manuscript.md)。本节保留 pipeline 运行视角。

T8 当前不是“一次写完整篇论文”的单节点，也不是“一个 Writer 一口气写多个 section 后拆文件”。真实主链按资源、大纲、逐 section、拼装、自查、审稿、修订拆成以下节点：

- `T8-RESOURCE`
- `T8-WRITE`
- `T8-SECTION-PLAN`
- `T8-SEC-METHOD`
- `T8-SEC-EXPERIMENTS`
- `T8-SEC-RELATED`
- `T8-SEC-ANALYSIS`
- `T8-SEC-INTRO`
- `T8-SEC-LIMITATIONS`
- `T8-SEC-CONCLUSION`
- `T8-SEC-ABSTRACT`
- `T8-DRAFT`
- `T8-SELF-CHECK`
- `T8-REVIEW-1`
- `T8-REVISE-1`
- `T8-REVIEW-2`
- `T8-REVISE-2`

旧版本或旧 `evaluation_decision.md` 里写 `next_task: T8` 或 `next_task: T8-WRITE` 时，状态机会把它们映射到 `T8-RESOURCE`。旧 `T8-SECTIONS` 单任务入口也会映射到 `T8-SECTION-PLAN`，避免绕回一次调用写多个章节的旧路径。

### 章节写作顺序与跨章节依赖

T8 的 8 个 section 按以下顺序写作（`SECTION_WRITING_SEQUENCE`，`manuscript.py` line 33）：

```
methodology → experiments → related_work → analysis → introduction → limitations → conclusion → abstract
```

**为什么是这个顺序**：
- `methodology` 和 `experiments` 先写，因为它们是技术核心，后续章节都要引用
- `related_work` 和 `analysis` 跟随，因为它们需要定位和解释核心贡献
- `introduction` 较晚写，因为"漏斗"和贡献声明需要引用实际结果
- `limitations`、`conclusion` 和 `abstract` 最后写；`abstract` 必须在所有主要章节完成后才能写（`manuscript.py` line 706: "Write last after all main sections exist"）

**跨章节依赖**（`plan_manuscript_sections` 的 `required_inputs`）：
- `conclusion` 需要 `introduction.tex` 和 `experiments.tex`
- `analysis` 需要 `methodology.tex` 和 `experiments.tex`
- `introduction` 需要 `methodology.tex`、`experiments.tex`、`related_work.tex`
- `abstract` 需要所有其他章节

**前一章节尾部注入**（`_previous_section_tail`，`writer.py` line 85）：
- 每个 section 写作时，Writer 会读取前一个章节 `.tex` 文件的最后 1200 字符作为上下文
- 这保证章节间的叙述连贯性
- `methodology`（index 0）没有前一章节，返回空

### 6.12.0 这一整段链到底由谁实现

这里是两类 agent 与一组 mechanical manuscript tools 协同：

- `WriterAgent`
  - 代码： [researchos/agents/writer.py](../researchos/agents/writer.py)
  - Prompt： [researchos/prompts/writer.j2](../researchos/prompts/writer.j2)
  - Guidance： [researchos/agent_guidance/manuscript-writing/SKILL.md](../researchos/agent_guidance/manuscript-writing/SKILL.md)
- `ReviewerAgent`
  - 代码： [researchos/agents/reviewer.py](../researchos/agents/reviewer.py)
  - Prompt： [researchos/prompts/reviewer.j2](../researchos/prompts/reviewer.j2)
- manuscript tools
  - 代码： [researchos/tools/manuscript.py](../researchos/tools/manuscript.py)
- 工具：`build_manuscript_resource_index`, `plan_manuscript_sections`, `plan_manuscript_evidence`, `initialize_manuscript_state`, `update_manuscript_section_state`, `assemble_manuscript`, `audit_manuscript_claims`, `build_manuscript_revision_patches`

Writer 默认工具包括 `read_file`, `write_file`, `list_files`, `build_manuscript_resource_index`, `plan_manuscript_sections`, `plan_manuscript_evidence`, `initialize_manuscript_state`, `update_manuscript_section_state`, `assemble_manuscript`, `audit_manuscript_claims`, `build_manuscript_revision_patches`, `finish_task`。这些工具只负责可复现的机械工作：索引资源、规划章节输入、规划 claim/evidence slots、规划图表/表格 slots、维护 `paper_state.json`、拼装章节、抽取 citation key、抽取数字审计提示、把 reviewer 意见定位成 patch list。问题定义、贡献取舍、机制解释、related work 论证、局限性判断、正文写法和修订取舍仍由 LLM 完成。

这条链的核心思路不是“一次成稿”，而是：

```text
resource index
 -> section plan
 -> evidence/figure plan
 -> outline
 -> paper_state + section outlines
 -> one-section-per-run drafts
 -> assembly + mechanical audit
 -> self check
 -> section-aware review
 -> section patch revision
 -> section-aware review
 -> section patch revision
```

### 6.12.1 T8-RESOURCE：WriterAgent（resource_index）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 方向、venue、约束 |
| `results_summary` | `experiments/results_summary.json` | 是 | 实验结果 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |
| `related_work_bib` | `literature/related_work.bib` | 是 | 引用库 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 研究假设 |
| `exp_plan` | `ideation/exp_plan.yaml` | 否但推荐 | 实验设计和方法上下文 |
| `novelty_audit` | `ideation/novelty_audit.md` | 否但推荐 | T4.5 新颖性边界 |
| `comparison_table` | `literature/comparison_table.csv` | 否但推荐 | prior methods / baseline 对照 |
| `ablations` | `experiments/ablations.csv` | 否但推荐 | 消融证据 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `manuscript_resource_index` | `drafts/manuscript_resource_index.json` | 写作资源索引 |
| `section_plan` | `drafts/section_plan.json` | 章节级写作计划 |
| `evidence_plan` | `drafts/evidence_plan.json` | claim slot 与证据计划 |
| `figure_table_plan` | `drafts/figure_table_plan.json` | 图表/表格计划 |
| `cdr_claim_ledger` | `drafts/cdr_claim_ledger.json` | CDR contribution claim seed |
| `claim_ledger` | `drafts/claim_ledger.json` | 普通 claim slot seed |
| `figure_registry` | `drafts/figure_registry.json` | 图表/表格 registry seed |

#### 实际执行过程

`WriterAgent(resource_index)` 启动后先渲染 `writer.j2` 的 Phase 0 指令，然后调用 `build_manuscript_resource_index(output_path="drafts/manuscript_resource_index.json")`。该工具扫描 `project.yaml`、`literature/synthesis.md`、`literature/paper_notes/*.md`、`literature/related_work.bib`、`ideation/hypotheses.md`、`ideation/idea_scorecard.yaml`、`ideation/novelty_audit.md`、`experiments/results_summary.json`、`experiments/ablations.csv`、`experiments/runs/**/*`、`experiments/configs/**/*`、已有 `figures/tables` 等资源，只记录路径、大小、短预览、BibTeX keys、结果数字和图表清单，不生成学术结论。

随后它调用 `plan_manuscript_sections(resource_index_path="drafts/manuscript_resource_index.json", output_path="drafts/section_plan.json", target_venue=<target_venue>)`。该工具把资源映射到 `abstract/introduction/related_work/methodology/experiments/analysis/limitations/conclusion` 八类章节，列出每章 expected outputs、required inputs、available inputs、missing inputs 和 LLM 需要完成的知识性任务。

然后它调用 `plan_manuscript_evidence(resource_index_path="drafts/manuscript_resource_index.json", evidence_output_path="drafts/evidence_plan.json", figure_output_path="drafts/figure_table_plan.json", target_venue=<target_venue>)`。该工具只生成 claim slots、候选证据文件、citation pool、result metric candidates 和图表/表格 slots，例如 `experiments_main_result`、`fig:main_results`、`tab:main_results`。它不决定论文最终 claim。

最后调用 `build_manuscript_registries(resource_index_path="drafts/manuscript_resource_index.json", evidence_plan_path="drafts/evidence_plan.json", figure_table_plan_path="drafts/figure_table_plan.json", cdr_output_path="drafts/cdr_claim_ledger.json", claim_output_path="drafts/claim_ledger.json", figure_output_path="drafts/figure_registry.json")`。这个工具生成三个 seed：CDR claim ledger 记录 `cdr_tuple` 和 contribution claim slots，generic claim ledger 记录普通 claim/evidence/citation slots，figure registry 记录 planned visual、label、source artifact 和 caption/file_path 空位。三者都是机械 seed，不是最终学术判断。validator 会检查七个 JSON 存在、非空，并至少包含 `introduction/related_work/methodology/experiments`、`claim_slots`、`experiments_main_result`、`planned_visuals`、`fig:main_results`、`tab:main_results`、CDR semantics、claim 列表和 visual 列表。

### 6.12.2 T8-WRITE：WriterAgent（outline）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `project` | `project.yaml` | 是 | 方向、venue |
| `results_summary` | `experiments/results_summary.json` | 是 | 实验结果 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |
| `related_work_bib` | `literature/related_work.bib` | 是 | 引用库 |
| `hypotheses` | `ideation/hypotheses.md` | 是 | 假设 |
| `manuscript_resource_index` | `drafts/manuscript_resource_index.json` | 是 | 写作资源地图 |
| `section_plan` | `drafts/section_plan.json` | 是 | 章节计划 |
| `evidence_plan` | `drafts/evidence_plan.json` | 是 | claim slot 与证据计划 |
| `figure_table_plan` | `drafts/figure_table_plan.json` | 是 | 图表/表格计划 |
| `cdr_claim_ledger` | `drafts/cdr_claim_ledger.json` | 是 | CDR contribution claim seed |
| `claim_ledger` | `drafts/claim_ledger.json` | 是 | 普通 claim ledger seed |
| `figure_registry` | `drafts/figure_registry.json` | 是 | 图表 registry seed |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `outline` | `drafts/outline.md` | 论文论证大纲 |

#### 实际执行过程

`WriterAgent(outline)` 在 prompt 中注入 resource index、section plan、evidence plan、figure/table plan、三个 registry、synthesis、hypotheses、results 和 bibliography 预览。它应该用 `read_file` 补看关键文件，然后写 `drafts/outline.md`。大纲不是标题列表，而是论文论证蓝图：标题候选、paper thesis、contribution map、section-by-section argument、figure/table plan、claim ledger 和 CDR narrative plan。每条核心贡献都要绑定 CDR 字段、证据文件和图表/表格计划，例如 `experiments/results_summary.json` 的主结果、`experiments/ablations.csv` 的消融、`literature/synthesis.md` 的 gap、`comparison_table.csv` 的 baseline，并引用 `cdr_claim_ledger.json` / `evidence_plan.json` 中相应 claim slot。validator 会检查 `outline.md` 长度、`##` 结构，以及 Introduction / Related Work / Method / Experiments 等必要章节。

### 6.12.3 T8-SECTION-PLAN：WriterAgent（section_plan）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `outline` | `drafts/outline.md` | 是 | 全局论证大纲 |
| `manuscript_resource_index` | `drafts/manuscript_resource_index.json` | 是 | 写作资源地图 |
| `section_plan` | `drafts/section_plan.json` | 是 | 章节计划 |
| `evidence_plan` | `drafts/evidence_plan.json` | 是 | claim slot 与证据计划 |
| `figure_table_plan` | `drafts/figure_table_plan.json` | 是 | 图表/表格计划 |
| `cdr_claim_ledger` | `drafts/cdr_claim_ledger.json` | 是 | CDR claim ledger seed |
| `claim_ledger` | `drafts/claim_ledger.json` | 是 | 普通 claim ledger seed |
| `figure_registry` | `drafts/figure_registry.json` | 是 | 图表 registry seed |
| `results_summary` | `experiments/results_summary.json` | 是 | 结果候选事实 |
| `synthesis` | `literature/synthesis.md` | 是 | 文献综合 |
| `related_work_bib` | `literature/related_work.bib` | 是 | 引用库 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper_state` | `drafts/paper_state.json` | 逐章节共享状态 |
| `section_outlines_dir` | `drafts/section_outlines/` | 每章局部作业单 |

#### 实际执行过程

`WriterAgent(section_plan)` 只做写作状态初始化，不写正文。它调用 `initialize_manuscript_state(outline_path="drafts/outline.md", resource_index_path="drafts/manuscript_resource_index.json", section_plan_path="drafts/section_plan.json", evidence_plan_path="drafts/evidence_plan.json", figure_table_plan_path="drafts/figure_table_plan.json", state_output_path="drafts/paper_state.json", section_outline_dir="drafts/section_outlines", target_venue=<target_venue>)`。

该工具把全局大纲拆成八个独立 section job，写出 `paper_state.json` 和 `drafts/section_outlines/*.md`。`paper_state.json` 包含 `semantics=shared_state_for_section_by_section_writing_not_final_claims`、`section_order`、每章目标文件、每章 required/available/missing inputs、`shared_facts.bib_keys`、`shared_facts.result_metrics`、claim slots、planned visuals 和 revision log。这里的 `shared_facts` 是机械候选事实，不是最终学术结论；Writer 在后续单章写作时必须读取原始 artifact 验证，不允许凭记忆补数字或引用。validator 会检查 `paper_state.json` 语义字段、八个 section 状态项和八个局部 outline。

如果旧 workspace 已有 `outline/resource_index/section_plan/evidence_plan/figure_table_plan`，但 `paper_state.json` 语义字段错误或 section outlines 缺失，orchestrator 会在进入 LLM 前调用 `initialize_manuscript_state` 确定性修复，然后以 `completion_mode=t8_section_plan_prefinalize` 完成。这避免 T8-SECTION-PLAN resume 时再次让 LLM 手写状态 JSON。`researchos validate --task T8-SECTION-PLAN` 也会先尝试这一步安全修复再校验，因此可作为续跑前的状态校准命令。

### 6.12.4 T8-SEC-METHOD：WriterAgent（section_draft, section_id=methodology）

`T8-SEC-METHOD` 是一次独立 WriterAgent 调用，只写 `drafts/sections/methodology.tex`。它读取 `drafts/paper_state.json`、`drafts/section_outlines/methodology.md`、`ideation/hypotheses.md`、`ideation/exp_plan.yaml`、实验配置和可用代码 artifact，说明方法名、机制、输入输出、算法/流程、实现细节和资源假设。CDR 职责是解释 artifact 为什么这样设计，写清 design rationale 和 design principles。这个节点不能写实验结果证明方法有效，也不能写 Introduction、Experiments 或整篇 `paper.tex`。

写完后必须调用 `update_manuscript_section_state(section_id="methodology", state_path="drafts/paper_state.json", section_path="drafts/sections/methodology.tex", status="written")`，然后 `finish_task`。validator 只检查 `methodology.tex`，要求它非空、没有 `\documentclass` / `\begin{document}` / `\end{document}`，并且 `paper_state.json` 中 `methodology.status` 已经是 `written` 或 `revised`。

### 6.12.5 T8-SEC-EXPERIMENTS：WriterAgent（section_draft, section_id=experiments）

`T8-SEC-EXPERIMENTS` 只写 `drafts/sections/experiments.tex`。它读取 `paper_state.json`、`section_outlines/experiments.md`、`experiments/results_summary.json`、`experiments/ablations.csv`、`experiments/runs/**/*`、`experiments/configs/**/*`、`ideation/exp_plan.yaml` 和 `figure_table_plan.json`。正文应覆盖 setup、datasets、baselines、metrics、seed、compute、main results、ablations 和必要的 result table/figure slot。CDR 职责是把 data_view 和 evaluation_mode 变成可复核证据。

所有数字必须来自 `results_summary.json`、`ablations.csv` 或 run artifact；如果缺 seed 统计、误差线、baseline 或图表数据，就写 TODO 或 limitations，不编造。写完同样调用 `update_manuscript_section_state(section_id="experiments", ...)`。

### 6.12.6 T8-SEC-RELATED：WriterAgent（section_draft, section_id=related_work）

`T8-SEC-RELATED` 只写 `drafts/sections/related_work.tex`。它读取 `paper_state.json`、`section_outlines/related_work.md`、`literature/synthesis.md`、`literature/comparison_table.csv`、`literature/paper_notes/*.md` 和 `literature/related_work.bib`。Related Work 必须按 taxonomy、方法家族、问题维度、机制差异和 competing design rationales 组织，而不是逐篇流水账；每个 citation key 必须存在于 `.bib`，具体方法或工具要引用 origin/instance paper，不能用泛泛概念论文替代。

这个节点需要 LLM 做学术定位和差异表达，但 tool 只提供候选证据和 citation pool，不硬编码“本文 gap 是什么”。写完更新 `paper_state.json` 中 `related_work.status`。

### 6.12.7 T8-SEC-ANALYSIS：WriterAgent（section_draft, section_id=analysis）

`T8-SEC-ANALYSIS` 只写 `drafts/sections/analysis.tex`。它读取 `experiments/ablations.csv`、`experiments/iteration_log.md`、`ideation/novelty_audit.md`、`paper_state.json` 和已写的 Method/Experiments 章节。这里负责解释为什么实验或消融支持/削弱机制，讨论替代解释、failure cases、sensitivity 和 novelty audit 留下的风险。CDR 职责是判断证据支持、削弱还是仅部分支持 design_rationale。

Analysis 可以做解释性推理，但每个解释必须指回实验 artifact、ablation、run log 或 novelty audit。不能把没有做过的 T5/T6 或额外实验写成已完成。

### 6.12.8 T8-SEC-INTRO：WriterAgent（section_draft, section_id=introduction）

`T8-SEC-INTRO` 在 Method、Experiments、Related Work 和 Analysis 之后运行，只写 `drafts/sections/introduction.tex`。它读取 `paper_state.json`、`section_outlines/introduction.md`、`literature/synthesis.md`、`ideation/hypotheses.md`、`experiments/results_summary.json`、`drafts/sections/methodology.tex` 和 `drafts/sections/experiments.tex`。

Introduction 的执行顺序靠后是有意设计：先让方法、实验和文献定位稳定，再写 motivation funnel、精确定义的 gap、insight、contribution bullets 和 result headline。CDR 职责是回答“如果本文成立，领域会怎样不同”，并把 contribution_type 写成后文能兑现的承诺。Intro 不能超过已有 evidence，不能把 planned visual、未完成 baseline 或跳过的 T5/T6 写成已发生事实。

### 6.12.9 T8-SEC-LIMITATIONS：WriterAgent（section_draft, section_id=limitations）

`T8-SEC-LIMITATIONS` 只写 `drafts/sections/limitations.tex`。它读取 `ideation/risks.md`、`ideation/novelty_audit.md`、`experiments/results_summary.json`、`experiments/iteration_log.md` 和 `paper_state.json`，明确说明 direct-full evidence boundary、跳过 T5/T6 的边界、baseline 覆盖、数据规模、外部有效性、compute/seed 限制、boundary_conditions 和复现风险。

这个章节不是自我削弱模板，而是对证据边界的学术说明。它应把 unsupported slots 和 missing inputs 转化为清楚的 threat-to-validity 描述。

### 6.12.10 T8-SEC-CONCLUSION：WriterAgent（section_draft, section_id=conclusion）

`T8-SEC-CONCLUSION` 只写 `drafts/sections/conclusion.tex`。它读取 Introduction、Experiments、Limitations 和 `paper_state.json`，简洁收束问题、方法、证据、contribution_type 和可迁移 design knowledge，并给出 future work。Conclusion 不能引入新 claim、新数字或新引用；如果需要新信息，应回到对应章节和 artifact。

### 6.12.11 T8-SEC-ABSTRACT：WriterAgent（section_draft, section_id=abstract）

`T8-SEC-ABSTRACT` 最后运行，只写 `drafts/sections/abstract.tex`。它读取 `paper_state.json`、`section_outlines/abstract.md`、Introduction、Method、Experiments、Analysis、Limitations 和 Conclusion，把全文压缩成 problem_frame、design_rationale、artifact/evidence、result、contribution_type。Abstract 不能出现正文没有的数字、引用或 claim，也不能引入新术语。

### 6.12.12 T8-DRAFT：WriterAgent（draft）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `outline` | `drafts/outline.md` | 是 | 大纲 |
| `sections_dir` | `drafts/sections/` | 是 | 分章节草稿 |
| `manuscript_resource_index` | `drafts/manuscript_resource_index.json` | 否但推荐 | 审计索引；缺失时 audit 工具会临时重建 |
| `section_plan` | `drafts/section_plan.json` | 否但推荐 | 章节计划 |
| `evidence_plan` | `drafts/evidence_plan.json` | 否但推荐 | claim slot 与证据计划 |
| `figure_table_plan` | `drafts/figure_table_plan.json` | 否但推荐 | 图表/表格计划 |
| `related_work_bib` | `literature/related_work.bib` | 否但推荐 | 引用校验 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `paper` | `drafts/paper.tex` | 拼装融合后的 LaTeX 初稿 |
| `manuscript_audit` | `drafts/manuscript_audit.md` | 机械审计报告 |

#### 实际执行过程

`WriterAgent(draft)` 必须先调用 `assemble_manuscript(section_dir="drafts/sections", output_path="drafts/paper.tex", outline_path="drafts/outline.md", target_venue=<target_venue>)`。该工具按 `abstract/introduction/related_work/methodology/experiments/analysis/limitations/conclusion` 顺序拼装章节，自动加 `\documentclass`、基础 package、`\title`、abstract、正文和 `\bibliography{related_work}`。随后 Writer 读取 `drafts/paper.tex` 做全局融合：统一术语、变量名、baseline 名称，修复章节重复或断裂，确保 Method 与 Experiment setup 不冲突，Introduction 和 Conclusion 能呼应。

最后它调用 `audit_manuscript_claims(paper_path="drafts/paper.tex", output_path="drafts/manuscript_audit.md")`。该工具检查 citation key 是否存在、数字是否能在结果索引中找到、figure/table reference 是否有资源、核心章节是否存在。它是 mechanical hint，不替代 LLM 的学术判断；若发现缺引用、可疑数字或缺图表，Writer 能修则修，不能修则在 audit 或 Limitations 中保留 TODO/边界。Writer validator 会检查 LaTeX 基本结构、必要章节、BibTeX key，并要求 draft 阶段生成 `manuscript_audit.md`。

### 6.12.13 T8-SELF-CHECK：WriterAgent（self_check）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `paper` | `drafts/paper.tex` | 是 | 当前论文 |
| `results_summary` | `experiments/results_summary.json` | 是 | 结果来源 |
| `related_work_bib` | `literature/related_work.bib` | 是 | 引用来源 |
| `manuscript_audit` | `drafts/manuscript_audit.md` | 否但推荐 | 机械审计 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `self_check` | `drafts/self_check.md` | 作者自查报告 |

#### 实际执行过程

`WriterAgent(self_check)` 读取 `drafts/paper.tex`、`drafts/manuscript_audit.md`、`experiments/results_summary.json` 和 `literature/related_work.bib`，写 `drafts/self_check.md`。自查必须覆盖 argument chain、number audit、citation audit、figure/table audit、reproducibility audit、direct-full/T5/T6 boundary 和 High/Medium/Low 修订 TODO。它不应该重写论文正文，而是把进入 reviewer 前必须修的风险显式列出来。

### 6.12.14 T8-REVIEW-1 / T8-REVIEW-2：ReviewerAgent

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `paper` | `drafts/paper.tex` | 是 | 当前稿件 |
| `results_summary` | `experiments/results_summary.json` | 否但强烈推荐 | 数字核对 |
| `related_work_bib` | `literature/related_work.bib` | 否但强烈推荐 | 引用核对 |
| `manuscript_audit` | `drafts/manuscript_audit.md` | 否但强烈推荐 | 机械审计问题 |
| `cdr_claim_ledger` | `drafts/cdr_claim_ledger.json` | 是 | CDR contribution / design-rationale 对齐审查 |
| `claim_ledger` | `drafts/claim_ledger.json` | 推荐 | 普通 claim slot 和证据来源 |
| `figure_registry` | `drafts/figure_registry.json` | 推荐 | 图表/表格 planned visual registry |
| `self_check` | `drafts/self_check.md` | 第一轮推荐 | 作者自查 |
| `previous_review` | `drafts/review_rounds/round_1.md` | 第二轮推荐 | 上一轮问题 |

#### 输出

| 节点 | 输出 |
| --- | --- |
| `T8-REVIEW-1` | `drafts/review_rounds/round_1_sections/*.md`, `drafts/review_rounds/round_1.md` |
| `T8-REVIEW-2` | `drafts/review_rounds/round_2_sections/*.md`, `drafts/review_rounds/round_2.md` |

#### 实际执行过程

`ReviewerAgent` 会先用 `list_files` 查看 `drafts/`、`experiments/` 和 `literature/` 可用文件，避免把目录传给 `read_file`。随后先读取 `drafts/paper_state.json` 和 `drafts/sections/*.tex`，对 abstract、introduction、related_work、methodology、experiments、analysis、limitations、conclusion 逐章生成 `round_N_sections/<section>.md`。

**逐章 review 格式**（`reviewer.py` line 131）：
- 每章独立文件：`drafts/review_rounds/round_N_sections/{section_id}.md`
- 每个文件必须 >= 80 字符，包含 `##` 标题
- 五个审查维度：
  1. **内容完整性**：section purpose 是否达成
  2. **技术准确性**：证据/数字/引用是否正确
  3. **CDR Alignment Check**：本章是否兑现 `problem_frame`、`design_rationale`、`contribution_type`、`evaluation_mode` 或 `boundary_conditions` 中对应职责
  4. **写作质量**：内部逻辑、可读性
  5. **学术规范**：格式、引用、术语一致性
- 每个逐章 review 必须检查 section purpose、证据/数字/引用、内部逻辑、可执行修复项

逐章 review 完成后，它再读取 `drafts/paper.tex`、`results_summary.json`、`.bib`、`manuscript_audit.md`、`self_check.md` 和三个 registry 生成综合 `round_N.md`。第二轮还会读取上一轮 review，明确检查 High/Medium issue 是否已经闭环。它不会修改论文正文，只写结构化 review，综合报告至少包含 `## 总体评价`、`## 主要问题`、`## 次要问题` 和 `## CDR Contribution Verdict`，并给出可定位、可执行的修订建议。CDR verdict 必须覆盖 problem frame clarity、design rationale support、contribution type credibility、evidence alignment 和 boundary condition honesty；如果只是 routine contribution risk，必须列为 Major Issue。

### 6.12.15 T8-REVISE-1 / T8-REVISE-2：WriterAgent（revise）

#### 输入

| 输入 key | 文件 | 必需 | 含义 |
| --- | --- | --- | --- |
| `paper` | `drafts/paper.tex` | 是 | 当前稿件 |
| `paper_state` | `drafts/paper_state.json` | 是 | 逐章节共享状态 |
| `review_report` | `drafts/review_rounds/round_1.md` 或 `round_2.md` | 是 | 本轮审稿意见 |
| `section_review_dir` | `drafts/review_rounds/round_N_sections/` | 是 | 本轮逐章节审稿 |
| `sections_dir` | `drafts/sections/` | 是 | 对应章节草稿 |
| `results_summary` | `experiments/results_summary.json` | 否但推荐 | 修数字 |
| `synthesis` | `literature/synthesis.md` | 否但推荐 | 修 related work / framing |
| `related_work_bib` | `literature/related_work.bib` | 否但推荐 | 修引用 |
| `manuscript_audit` | `drafts/manuscript_audit.md` | 否但推荐 | 机械审计 |

#### 输出

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `revision_patches` | `drafts/patches/round_N_patches.json` | 本轮 review issue 的机械定位 |
| `revision_response` | `drafts/revision_response_round_N.md` | resolved/unresolved/deferred 说明 |
| `paper` | `drafts/paper.tex` | 修订后的论文 |
| `manuscript_audit` | `drafts/manuscript_audit.md` | 更新后的机械审计 |

#### 实际执行过程

`WriterAgent(revise)` 先调用 `build_manuscript_revision_patches(round_num=N)`。该工具读取综合 review 和 `round_N_sections/*.md`，把 High/Medium/Low issue 机械定位成 `drafts/patches/round_N_patches.json`，字段包括 `target_section`、`target_file`、`issue_type`、`severity`、`specific_issue` 和来源行。这个工具不决定怎么改，只防止 Writer 读完整篇后整体重写。

随后 Writer 按 severity 顺序处理 patch。能定位到章节的问题，只读取对应 `drafts/sections/<section>.tex`、`paper_state.json` 和必要证据文件，修改该 section 后调用 `update_manuscript_section_state(status="revised")`。global patch 要优先拆成多个 section 修改；只有无法定位时才做最小范围全文检查。所有 patch 完成后调用 `assemble_manuscript` 重新生成 `paper.tex`，再调用 `audit_manuscript_claims` 更新 `manuscript_audit.md`。最后写 `drafts/revision_response_round_N.md`，逐条记录 resolved、unresolved 或 deferred。validator 会要求 patch list、revision response、完整 section 状态、`paper.tex` 和 `manuscript_audit.md` 都存在。

### T8 的恢复语义

T8 的恢复主要依赖已有 artifact：

- `T8-RESOURCE` 重跑时应更新资源索引，不删除已有章节
- `T8-WRITE` 如果已有 `outline.md`，应基于最新 resource index 修订大纲
- `T8-SECTION-PLAN` 会根据 `paper_state.json` 和 section outlines 恢复逐章节作业单
- 每个 `T8-SEC-*` 只根据自己的 `section_id` 补写或修订对应 section，不应改其它 section
- `T8-DRAFT` 使用 `assemble_manuscript` 从章节草稿重建 `paper.tex`，如需改正文先回改 section 文件
- `T8-REVISE-*` 按 `round_N_patches.json` 定位修 `sections/*.tex`，再刷新 `paper.tex` 与 `manuscript_audit.md`

### T8 单独运行 vs 完整运行

- 单独 `run-task T8-RESOURCE`
  - 适合检查资源索引和章节计划
- 单独 `run-task T8-SECTION-PLAN`
  - 适合检查 `paper_state.json` 和每章局部大纲
- 单独 `run-task T8-SEC-METHOD` / `T8-SEC-EXPERIMENTS` / `T8-SEC-RELATED` / ...
  - 适合调单章节写作；每次只写一个 section
- 单独 `run-task T8-DRAFT`
  - 适合调拼装、LaTeX、citation 和 audit
- 完整 `run/resume`
  - 会按状态机依次走完 `T8-RESOURCE -> T8-WRITE -> T8-SECTION-PLAN -> T8-SEC-* -> T8-DRAFT -> T8-SELF-CHECK -> ...`

旧 `researchos run-task T8-SECTIONS --workspace ...` 仍被 CLI 接受，但会映射到 `T8-SECTION-PLAN`，不会再触发一次调用写多个 section 的旧行为。

### T8 常用单任务示例

```bash
cd ResearchOS
researchos run-task T8-RESOURCE --workspace ./workspace/local-test2
researchos run-task T8-WRITE --workspace ./workspace/local-test2
researchos run-task T8-SECTION-PLAN --workspace ./workspace/local-test2
researchos run-task T8-SEC-METHOD --workspace ./workspace/local-test2
researchos run-task T8-SEC-EXPERIMENTS --workspace ./workspace/local-test2
researchos run-task T8-SEC-RELATED --workspace ./workspace/local-test2
researchos run-task T8-SEC-ANALYSIS --workspace ./workspace/local-test2
researchos run-task T8-SEC-INTRO --workspace ./workspace/local-test2
researchos run-task T8-SEC-LIMITATIONS --workspace ./workspace/local-test2
researchos run-task T8-SEC-CONCLUSION --workspace ./workspace/local-test2
researchos run-task T8-SEC-ABSTRACT --workspace ./workspace/local-test2
researchos run-task T8-DRAFT --workspace ./workspace/local-test2
researchos run-task T8-SELF-CHECK --workspace ./workspace/local-test2
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
- max steps：`3000`
- max tokens：`5000000`
- max wall seconds：`8000`
- 可选匿名化 pre-hook：由配置控制

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

当前机器的 Docker root 已迁移到 `/mnt/data/Docker`；宿主机无 `latexmk` 时，T9 会默认走 Docker 编译。可用 `docker info --format '{{.DockerRootDir}}'` 确认路径，用 `docker image inspect researchos/system:latest` 确认统一镜像是否存在。

**匿名化 precheck 默认关闭**（`submission.py` line 83: `enforce_anonymization_precheck` 默认 `False`）。只有当 `agent_params.yaml` 中 `submission.enforce_anonymization_precheck` 设为 `true` 时，才会在进入 LLM 前拦截检查邮箱、URL、GitHub 等匿名化问题。这便于本地调试或非匿名投稿场景直接产出投稿包。

**Venue 模板支持**：T9 从 `project.yaml` 的 `target_venue` 字段（默认 `neurips2026`）读取目标会议格式，迁移主稿到对应模板。

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
2. 迁移到目标会议模板
3. 生成 `submission/bundle/`
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
- 最多进行 `max_compile_attempts` 轮

当前配置默认：

- `max_compile_attempts = 4`

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
- `compile_report.main_tex_sha256`、`pdf_sha256`、`log_sha256`、`pdf_mtime`、`log_mtime`、`pdf_size`、`log_size` 必须与当前文件一致，避免旧 PDF 或伪造报告通过
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

状态机中 T9 配置了 `next_on_failure: T8-WRITE`。当 T9 编译失败且 `max_compile_attempts` 耗尽时：

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
- `max_steps` 在循环尾部触顶时也会进入同一套扩限 gate；用户选择停止或无法继续输入时，状态会写成 `PAUSED`，history 中本轮 run 标记为 `INTERRUPTED`，后续可以 `researchos resume --workspace ...`
- 如果进程异常退出导致 `state.yaml` 停在 `RUNNING`，`resume` 会把最近一次 run 标为
  `INTERRUPTED` 并自动转回 `PAUSED` 后继续，避免“当前状态不是 PAUSED/WAITING_HUMAN，
  无法 resume”的死状态。
- `resume` 后不会恢复模型内部上下文，而是通过 `_runtime/resume/*.json`、T3 pending queue、已有输出文件和 task-specific recovery artifact 注入 `resume_mode`，让 agent 从已落盘事实继续
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
