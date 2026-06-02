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
 -> T3.6 可选综述论文支线（ask_human：是否撰写 survey）
 -> T4 假设与实验计划生成
 -> T4.5 新颖性预审（非通过 verdict 进入人工决策 gate）
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
 -> T4.5
    -> pass*: T7
    -> reframe/drop/reject/collision: T4.5-HUMAN-REVIEW -> user chooses T7/T4/done
 -> T7
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
 -> T9
 -> done
```

几个最容易记错的点：

- `HELLO` 是显式运行的 smoke task，不是主链起点；主链的 `initial_state` 是 `T1`
- 当前主链暂时跳过 `T5` / `T6`，但只有 `T4.5` 的 `Final Gate Verdict` 明确写成 `pass_to_experiment` / `pass_with_required_baselines` 等通过枚举时才自动进入 `T7`；`return_to_T4_reframe`、`drop_due_to_collision`、`reject`、`collision`、`fail`、缺失 verdict 或无法识别的 verdict 都会进入 `T4.5-HUMAN-REVIEW`，由用户选择继续 T7、回 T4 重构或结束项目。系统不再自动拒绝、自动回退或默认放行，避免 T4.5-T4 死循环，也避免模型在新颖性不确定时替用户做价值裁决。`T5` / `T6` 不是删除，而是可用 `run-task` 单独执行的可选增强阶段
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
- `T3.6` 会读取已有 `survey_plan.json`、`survey_state.json`、`sections/*.tex`、`survey_audit.json` 和编译日志，按 section 续写/续编译
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
| `T2` | `ScoutAgent` | - | 检索、去重、验证、构建引用图领域地图和精读队列 | `papers_raw`, `papers_dedup`, `papers_verified`, `citation_edges`, `domain_map`, `deep_read_queue` |
| `T3` | `ReaderAgent` | `read` | 逐篇精读并形成结构化证据（含 §13 Mechanism Claim、§14-§19 CDR 和 abstract A/B 桥接字段） | `paper_notes/`, `paper_notes_abstract/`, `comparison_table.csv`, `related_work.bib` |
| `T3.5` | `ReaderAgent` | `synthesize` | 以引用图为骨架、叠加 LLM 综述判断，生成领域综合和邻接迁移素材 | `synthesis_workbench.json`, `synthesis_outline.md`, `synthesis_draft.md`, `synthesis.md` |
| `T3.6-GATE-SURVEY` | `SurveyWriterAgent` | `survey_gate` | 询问是否撰写 taxonomy-driven survey；否直接进入 T4 | `drafts/survey/decision.json` |
| `T3.6-PLAN` 到 `T3.6-FEED` | `SurveyWriterAgent` | survey 系列 | 可选综述论文支线：taxonomy 规划、人工确认、逐 section 写作、拼装、综述模式 review、编译、导出 T4 idea fuel | `drafts/survey/survey_plan.json`, `survey_state.json`, `sections/*.tex`, `survey.tex`, `survey_review.md`, `survey.pdf`, `ideation/survey_insights.json` |
| `T4` | `IdeationAgent` | - | 两段式生成假设、软 novelty/concentration 诊断、实验计划、Gate决策链和风险评估；可消费 `survey_insights.json` | `hypotheses.md`, `exp_plan.yaml`, `idea_scorecard.yaml`, `rejected_ideas.md`, `gate_decisions.json`, `idea_rationales.json`, `risks.md`, `_family_distribution.md` |
| `T4.5` | `NoveltyAuditorAgent` | - | 对假设做新颖性预审（含 mechanism tuple 碰撞检测）；非通过 verdict 进入人工决策 gate | `novelty_audit.md`, `_mechanism_tuples/`; 有撞车时另写 `collision_cases.md` |
| `T5` | `ExperimenterAgent` | `pilot` | 可选增强：用小规模实验验证方向值不值得继续 | `pilot_plan.yaml`, `pilot_code/`, `pilot_results.json`, `motivation_validation.md` |
| `T6` | `NoveltyAgent` | - | 可选增强：基于 Pilot 做增量 novelty 复核 | `novelty_report.md`, `collision_cases.md`, `must_add_baselines.md` |
| `T7` | `ExperimenterAgent` | `full` | 完整实验与主结果、ablation、multi-seed；当前主链从 T4.5 direct-full 进入 | `results_summary.json`, `runs/`, `configs/`, `iteration_log.md`, `ablations.csv` |
| `T7.5` | `PIAgent` | `evaluate` | 评估实验结果是否足以写论文 | `evaluation_decision.md` |
| `T8-STYLE-GATE` | `WriterAgent` | `style_gate` | 确认 IS / CCF-A / both 写作风格 | `drafts/writing_style.json` |
| `T8-RESOURCE` | `WriterAgent` | `resource_index` | 索引写作资源，消费 `domain_map`/`adjacent_transfers`/idea 软信号，并生成章节/证据/图表计划和对齐矩阵 seed | `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, `drafts/figure_table_plan.json`, `drafts/alignment_matrix.json` |
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

### `updataPreT5.md` / Pre-T5 + T8 落地对照

`/mnt/data/reference/updataPreT5.md` 强调的 T2/T3/T3.5/T4/T4.5/T8 不是只停留在文档层。当前已经落到 prompt、tool、validator 和状态契约中，但要区分“已经强制校验”和“仍由 LLM 学术判断完成”的部分。

| updataPreT5.md 项 | 当前落点 | 状态 |
| --- | --- | --- |
| CDR 单一事实源 | `config/cdr_schema.yaml` 定义 `problem_frame`、`design_rationale`、`artifact`、`data_view`、`evaluation_mode`、`contribution_type`、`boundary_conditions`、`cross_paper_tension`，并明确 provenance 不是质量门 | 已落地 |
| T2 检索广度和跨域保护 | `researchos/prompts/scout.j2` 默认启用 `informs_search`，允许 `query_bucket=adjacent_field/theory_bridge`；`researchos/tools/paper_enrichment.py` 只保护 LLM 标注的跨域/理论 bucket，不硬编码学科归属 | 已落地 |
| T2 引用图主轴 | `fetch_outgoing_citations` 读取 OpenAlex outgoing references + related works，并解析少量一跳候选论文；runtime 会把这些 `data.papers` 自动追加进 `papers_raw.jsonl`，同时把 `source_id -> referenced_works/related_works` 独立追加到 `literature/citation_edges.json`，即使 neighbor metadata 没解析出来也不丢边；`build_domain_map` 生成 `domain_map.json` 供 T3.5/T4/T8 复用；`citation_edges` 已列入 T2 I/O contract 和 state machine outputs | 已落地 |
| T3 note schema 扩展 | `researchos/prompts/reader.j2` read 模式从 13 节扩为 19 节，新增 `§14 Design Rationale` 到 `§19 Cross-Paper Tension`；`researchos/agents/reader.py::_validate_cdr_note_fields` 校验字段和 `contribution_type` 枚举 | 已落地 |
| T3 abstract-only 桥接字段 | `abstract_sweep.py` 和 `reader.j2` 要求 abstract-only note 写 `## A. 核心做法/视角` 与 `## B. 桥接点`；`reader.py` 对 `paper_notes_abstract/` 以及 `paper_notes/` 中 `[ABSTRACT-ONLY]` note 都做结构校验 | 已落地 |
| T3 FULL-TEXT / 截断校验 | `reader.j2` 要求分块重读覆盖全部页码；`reader.py` 校验 `Reading Coverage`、页码范围、最终 `Truncation` 状态和 Key Results evidence anchor | 已落地 |
| T3 resume 防重读 | Reader 进入时优先 `deep_read_queue_pending.jsonl`，runtime 会按结构合格 note 刷新 pending queue/meta；多 key 匹配 `normalized_id`、原始 ID、标题、DOI，避免 resume 后把已读论文重写 | 已落地 |
| T3.5 贡献空间综合 | `reader.j2` synthesize 模式改为 LLM 先分析，再把 `LLMInsights` 传给 `build_synthesis_workbench`；`literature_synthesis.py` 生成 `contribution_space` 与 `cross_paper_tensions` | 已落地 |
| T3.5 邻接迁移 | `build_synthesis_workbench` 读取 `domain_map.json`，输出 `citation_graph_context`、`domain_map_bucket_summary`、`adjacent_transfers`；`synthesis.md` 必须包含“邻接领域可迁移机制”章节或说明语料邻接覆盖不足 | 已落地 |
| T3.5 不硬编码知识 | `build_synthesis_workbench` 只结构化证据和 LLM 洞察；方法家族、共同假设、趋势、研究问题由 LLM 提供，缺失时写 `LLM_REVIEW_REQUIRED`，不把工具 hint 当最终综述 | 已落地 |
| T4 两段式 ideation | `researchos/prompts/ideation.j2` 明确 Pass 1 前向生成和 Pass 2 文献接地；Pass 1 主线包含 `synthesis_gestalt`、`problem_reframing`、`design_rationale_derivation`、`cross_domain_analogy`、`free_reasoning`、`seed_refinement`、`evidence_driven` | 已落地 |
| T4 四类约束降级为补充 | `ideation.j2` 把【机制质疑型、反向操作、子群失败、缺口探索】放在 Step 2.5，定义为 coverage supplements；`_candidate_directions.json` 必须区分 `constraint_status=mainline/supplement` | 已落地 |
| T4 provenance 不再 gate | `supporting_papers`、`closest_baselines`、`from_synthesis_section` 为 optional 文档字段；`prior_art: none` 合法且表示高新颖/高风险，不因无 baseline 被降分 | 已落地 |
| T4 anti-incrementalism gate | `ideation.py` 对 selected / hypothesis-linked idea 校验 `design_rationale`、`contribution_type`、`contribution_character`、`contribution_strength`；`routine` 不能作为 selected idea 通过 | 已落地 |
| T4 多样性与主线来源 | `ideation.py` 要求 `_candidate_directions.json` 和 `idea_scorecard.yaml` 记录 `idea_origin`、`constraint_status`；validator 会拒绝候选池只由四类补充通道构成 | 已落地 |
| T4 软 novelty/集中度诊断 | `ideation_tools.py` 提供 `analyze_idea_concentration` 和 `compute_idea_novelty_signal`；`idea_scorecard.yaml` 必须记录 `counterfactual_check`、`nearest_prior_work`、`novelty_signal`，Gate1 brief 必须显示集中度提示、Origin 分布和 Novelty-Utility 谱系。字段存在性用于防止跳过审阅，不按好坏 gate；材料不足时允许 `insufficient_evidence`、`not_computed`、`domain_map_unavailable` 并要求说明原因，避免硬编三分类 | 已落地 |
| T4.5 collision + ambition | `novelty_auditor.j2` 要求同时写 Collision Axis 和 Ambition Axis；`mechanism_tools.py` 增加 `extract_design_rationale_tuple` / `compare_design_rationale_tuples`；`novelty_auditor.py` 校验 `_design_rationale_tuples/` 与 routine reframe 要求 | 已落地 |
| T4.5 非通过 verdict 人工决策 | `novelty_auditor.j2` 要求写 `Final Gate Verdict`；状态机对 T4.5 使用 `__parse_from_output__`：只有 `pass_to_experiment` / `pass_with_required_baselines` 等明确通过枚举进入 T7，`return_to_T4_reframe` / `drop_due_to_collision` / `reject` / `collision` / `fail`、缺失 verdict 和未知 verdict 都进入 `T4.5-HUMAN-REVIEW`。该节点是 `immediate_gate`，不启动 LLM，不自动拒绝、自动回 T4 或默认放行；用户查看 `novelty_audit.md`、Gate1 brief、scorecard 后选择继续 T7、回 T4 或结束，决策落盘到 `ideation/novelty_human_review.json` | 已落地 |
| T7.5/T8 消费 CDR 与 Pre-T5 新产物 | T8-RESOURCE 生成 `cdr_claim_ledger.json` 和 `alignment_matrix.json`，并通过 task contract 复制 `domain_map.json`、`synthesis_workbench.json`、`idea_scorecard.yaml`、`writing_style.json`；这些文件在 `T8-RESOURCE`、`T8-WRITE`、`T8-SECTION-PLAN` 与 `T8-SEC-RELATED` 中是单任务强前置，避免 Related Work 静默降级；Related Work 消费 `adjacent_transfers` / `nearest_prior_work`，alignment rows 消费 `counterfactual` / `novelty_signal`；`audit_writing_craft` 会 WARN 检查 Related Work 是否可见地使用最近工作、邻接迁移或 cross-paper tension 信号；Reviewer 增加 `CDR Contribution Verdict` | 已落地 |
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

### 输出文件

| 输出 key | 文件 | 含义 |
| --- | --- | --- |
| `papers_raw` | `literature/papers_raw.jsonl` | 原始检索命中结果，去重前 |
| `papers_dedup` | `literature/papers_dedup.jsonl` | 去重、打分和 enrich 后的候选池 |
| `papers_verified` | `literature/papers_verified.jsonl` | 通过 metadata verification 的可信论文池 |
| `verification_failures` | `literature/verification_failures.jsonl` | verification 失败或元数据不一致的样本 |
| `citation_edges` | `literature/citation_edges.json` | T2 收集到的一跳出引 / related works 边；恢复路径只使用已落盘 metadata，不额外联网 |
| `domain_map` | `literature/domain_map.json` | 引用图领域地图：core / adjacent / boundary 三区、citation_edges 和 bucket_assignments；不是最终研究缺口 |
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

- `core`：high-degree / core bucket 节点；`seed` 只表示用户或上游显式提供的优先阅读信号，不会无条件塞入 core
- `adjacent`：LLM 标注的 `adjacent_field` / `theory_bridge`、OpenAlex related/snowball 或与核心相连的邻接节点
- `boundary`：当前检索图中连接稀疏的方向
- `citation_edges`：当前可用的一跳边
- `bucket_assignments`：每个论文 ID 的机械 bucket

`domain_map.json.semantics` 固定为 `domain_map_for_synthesis_and_ideation_not_final_gaps`。它是 T3.5/T4/T8 的结构化脚手架，不是“工具判断出的最终领域结构”或“真实研究缺口”。如果边为空，会在 `warnings` 中记录；这表示引用图信号有限，下游 LLM 必须降低对图谱的依赖，而不是硬编 adjacent transfer。

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

`ScoutAgent` 运行时先读 `project.yaml` 获取 `research_direction`、`keywords`、`target_venue` 和约束；再读 `user_seeds/seed_papers.jsonl`、`seed_ideas.md`、`seed_constraints.md`、`seed_external_resources.jsonl`，如果存在 `seeds/T2_scout/papers/*.pdf` 还会先用 `scan_seed_papers` 抽取本地 PDF seed metadata。随后 prompt 会注入 `literature-scout` guidance，要求 LLM 先归纳 `domain_profile`：目标领域、include/exclude concepts、歧义词、相关子领域、候选 venue/category 和多角度 query。`expand_queries` 只负责把 LLM 设计的 query、seed 标题短语和时间窗口合并去重，不再内置“memory/retrieval/agent 属于 AI/CS”这类学科判断；如果需要领域过滤，`filter_by_domain` 也必须接收 LLM 提供的 `domain_profile`，否则不做过滤。之后用 `detect_duplicate_queries` 检查 query 是否只是同义重复。

真正抓论文时默认调用 `openalex_search`、`crossref_search`、`arxiv_search`、`semantic_scholar_search`、`elsevier_scopus_search` 和 `informs_search`。其中 `informs_search` 是通过 Crossref DOI prefix `10.1287` 检索 INFORMS 论文元数据，适合 OR/MS、management science、supply chain、queueing、optimization 等方向，也可以作为低成本补充源默认启用；如果某个主题不在 INFORMS 强覆盖范围内，它通常只会返回 0 篇或少量噪声，T2 记录后继续。`domain_profile` 的作用是解释和过滤结果，不是决定是否完全跳过 INFORMS。

每次工具返回的 `data.papers` 会被 `AgentRunner` 自动追加到 `literature/papers_raw.jsonl`，不需要模型手工复制 JSON。自动落盘工具包括常规检索工具和 `fetch_outgoing_citations`；因此引用图滚雪球解析出的 neighbor papers 也会进入 raw 池。Scout 可以在搜索工具调用中附带 `query_bucket`，例如 `core`、`baseline`、`evaluation`、`adjacent_field`、`theory_bridge`；runtime 只保存这个显式标签，不根据关键词猜学科。raw 达到阈值后，runtime 会确定性调用去重、metadata priority hint、enrich、metadata verification、引用边/domain map、access audit 和 deep-read queue 构建逻辑，依次产出 `papers_dedup.jsonl`、`papers_verified.jsonl`、`verification_failures.jsonl`、`citation_edges.json`、`domain_map.json`、`deep_read_queue.jsonl`、`access_audit.md`、`search_log.md` 和 `missing_areas.md`；最后 `ScoutAgent.validate_outputs()` 再检查数量、schema、`dedup <= raw`、queue 是否来自 verified 池、seed paper 是否进入队列，以及若 verified 池里已有 adjacent/theory/snowball 候选，queue 至少保留一个跨域/桥接候选并放入 target/seed 阅读区，避免该类素材落到 overflow 后被 T3 跳过。

### T2 怎样保存 raw 结果

当前默认是 runtime 自动保存，不要求 LLM 手动 `append_papers_raw`。

实际行为：

1. T2 调用任一搜索工具并返回 `data.papers`
2. `AgentRunner` 自动把这些结果追加进 `literature/papers_raw.jsonl`
3. raw 数量达到阈值，或 T2 因预算/错误提前停止但 raw 已落盘时，runtime 会尝试确定性收尾
4. 收尾路径会从 raw 生成 `papers_dedup`、`papers_verified`、`citation_edges`、`domain_map`、`deep_read_queue`、`access_audit`、`search_log`、`missing_areas`

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

protected bucket 会在 seed 后优先占用 target slots，再填中心论文；它不会只靠加权排序被高分 core 论文挤到 `overflow`。`deep_read_queue` metadata 会记录 `protected_bucket_target`、`protected_bucket_in_queue` 和 `protected_bucket_in_target`。如果 verified 池中已经有 `adjacent_field`、`theory_bridge`、`source_bucket=adjacent` 或 `source_bucket=snowball` 的候选，`ScoutAgent.validate_outputs()` 会检查 `deep_read_queue` 中至少保留一个这类跨域/桥接候选；`ReaderAgent.validate_outputs()` 还会检查这些非 overflow 的 protected queue 论文是否真正完成 note。这个校验防止系统表面生成了 `domain_map`，但精读队列仍全部被中心论文占满。它仍然不是质量门：进入队列只表示应被 Reader 复核，不表示论文一定重要。

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
- `domain_map.json` 必须存在，包含 `core`、`adjacent`、`boundary`、`citation_edges`、`bucket_assignments`，且 semantics 为 `domain_map_for_synthesis_and_ideation_not_final_gaps`
- 如果 verified 池有 adjacent/theory/snowball 候选，deep-read queue 至少保留一个这类候选

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

- **触发条件**：T2 的 search / snowball 工具（`multi_source_search`、`search_papers`、`arxiv_search`、`openalex_search`、`crossref_search`、`semantic_scholar_search`、`elsevier_scopus_search`、`informs_search`、`fetch_outgoing_citations`）或 `append_papers_raw` / `process_papers_raw` / `save_papers_raw` 成功执行
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

这意味着：**只要 Scout 搜到足够多的 raw 论文，后续的 dedup/verified/citation_edges/domain_map/queue/audit/missing_areas 全部由工具确定性完成，不需要 LLM 反复调用工具。** LLM 的搜索策略仍然重要（决定搜什么、怎么扩展 query、哪些 query 属于 adjacent/theory），但产物组装不再依赖 LLM。

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
| `domain_map` | `literature/domain_map.json` | 否但强烈推荐 | T2 引用图领域地图；用于识别高桥接邻接论文 |
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

`ReaderAgent(read)` 进入时会先列出 `literature/paper_notes/`，再读取 `literature/deep_read_queue_pending.jsonl`；如果没有 pending queue，就读 `deep_read_queue.jsonl`，再回退到 `papers_verified.jsonl`，最后才回退到 `papers_dedup.jsonl`。处理每篇论文前，它用 `lookup_paper_record` 或小范围 `read_file` 取该论文 metadata，规范化论文 ID 后检查是否已有同名 note；已有 note 只有在结构、Evidence 锚点和 `Reading Coverage` 都合格时才算完成。这里的“规范化”只用于文件路径：例如真实 ID `arxiv:2301.12345` 写文件时用 `literature/paper_notes/arxiv_2301.12345.md`、PDF 用 `literature/pdfs/arxiv_2301.12345.pdf`；正文引用可以写 `[arxiv:2301.12345]` 或 `[arxiv_2301.12345]`，只要能对应到真实 note。若 PDF 不在本地，它会调用 `fetch_paper_pdf(paper_id=..., save_path="literature/pdfs/{normalized_id}.pdf")` 尝试下载；拿到 PDF 后用 `extract_pdf_text` 按页读取，如果返回的 metadata 显示 `preview_truncated_by_max_chars=true` 或 runtime 上下文裁剪，就继续用更小的 `start_page/max_pages` 分块重读，直到 `Pages read` 覆盖 `1-total_pages`。写出时用 `write_file("literature/paper_notes/{normalized_id}.md", ...)` 生成 19 节结构化 note，并用 `append_file` 或重写方式维护 `comparison_table.csv` 和 `related_work.bib`。如果这篇只能基于摘要，note 必须标 `[ABSTRACT-ONLY]`，并写 `## A. 核心做法/视角` 与 `## B. 桥接点` 两个轻字段。收尾时 validator 会逐篇扫描 note，检查 `- **Status**:`、核心章节、数字证据 `[Evidence: ...]`、全文页码覆盖、最终截断状态、CDR §14-§19、abstract A/B 字段、queue 覆盖率和 seed paper 覆盖情况。

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
- note 结构合理，包含核心章节、`## 13. Mechanism Claim`、`## 14`-`## 19` CDR 字段
- 每篇 note 包含 `## 12. Reading Coverage`
- 每篇 note 包含 `## 13. Mechanism Claim`，三个 bullet（Stated mechanism / Evidence type / Supporting artifact）均非空
- `[ABSTRACT-ONLY]` note 必须包含 `## A. 核心做法/视角` 和 `## B. 桥接点`
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

配置在 `config/agent_params.yaml` 的 `reader.modes.read.behavior.abstract_sweep`：

```yaml
reader:
  modes:
    read:
      behavior:
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
- §A 核心做法/视角（abstract-level 方法或理论视角）
- §B 桥接点（与主线、邻接领域或 theory bridge 的连接点）
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
| `domain_map` | `literature/domain_map.json` | 是 | T2 的引用图领域地图，提供 `citation_edges`、core/adjacent/boundary 三区 |

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
- 方法对比表（`comparison_table.csv`）
- T2 发现的检索覆盖提示（`missing_areas.md`）
- T2 引用图领域地图（`domain_map.json`）

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
1. 解析 `paper_notes/*.md` 和 `paper_notes_abstract/*.md`，提取每篇 note 的标题、年份、方法概述、关键结果、局限、问题、§13 Mechanism Claim、abstract A/B 桥接字段
2. 读取 `comparison_table.csv` 提取指标和效率线索
3. 如果存在 `missing_areas.md`，纳入候选问题（只保留为 coverage hints，不写成已验证研究缺口）
4. 如果存在 `domain_map.json`，读取 `citation_edges` 和 core/adjacent/boundary 三区，写入 `citation_graph_context`、`domain_map_bucket_summary`，并从 `domain_map.adjacent` + note 的 A/B 字段生成 `adjacent_transfers`
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
5. `domain_map` 中的 core/adjacent/boundary 是否只作为综述骨架和迁移素材，而不是被工具当成最终研究缺口

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
| `paper_ids` | 所有论文 ID 列表 |
| `method_families` | 方法家族聚类（最多 5 个），每个包含 `name`、`paper_ids`、`representative_titles`、`core_observations`、`result_observations`、`_abstract_count`。当 LLM 提供 `llm_insights.family_classifications` 时，家族名称和成员分配由 LLM 决定；否则回退为 `LLM_REVIEW_REQUIRED: {method_text}` 占位 |
| `shared_assumption_candidates` | 共同假设候选，每个包含 `assumption`、`why_questionable`、`supporting_papers`。当 LLM 提供 `llm_insights.shared_assumptions` 时直接使用 LLM 分析；否则从 note 的 Limitations/Gaps 段落提取 review hint |
| `mechanism_claim_clusters` | 机械聚合的机制 claim cluster hint（见下文） |
| `domain_consensus` | 兼容旧代码的 alias；语义同 `mechanism_claim_clusters`，不要当成已验证领域共识 |
| `metric_landscape_hints` | 指标/效率上下文 hint，不是 opportunity map；T4 机会生成应优先消费 contribution_space 和 tensions |
| `contribution_space` | CDR 贡献空间 hint：按 contribution_type、artifact 类型、design_rationale snippets 组织，供 LLM 复核 |
| `cross_paper_tensions` | 跨论文矛盾/设计论证竞争素材；是 T4 Pass 1 的生成燃料，不是 provenance gate |
| `citation_graph_context` | T2 domain_map 的引用图上下文，包含 citation_edges、core/adjacent/boundary ID 和 warnings；是客观骨架 hint，不是最终综述结构 |
| `domain_map_bucket_summary` | core/adjacent/boundary 数量和 edge_count，供 LLM 判断语料是否有足够邻接覆盖 |
| `adjacent_transfers` | 从 `domain_map.adjacent` 与 note A/B 字段生成的邻接迁移 seed，每项含 mechanism/source_papers/why_unused_in_target/transfer_hypothesis_hint；必须由 LLM 复核，不是成型 idea |
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
| `synthesis_workbench` | `literature/synthesis_workbench.json` | contribution_space、cross_paper_tensions、adjacent_transfers、mechanism clusters |
| `domain_map` | `literature/domain_map.json` | core/adjacent/boundary 与 citation_edges，用于 taxonomy/evolution |
| `comparison_table` | `literature/comparison_table.csv` | 横向比较和 comparative analysis 证据 |
| `paper_notes_dir` | `literature/paper_notes/` | deep-read 证据 |
| `paper_notes_abstract_dir` | `literature/paper_notes_abstract/` | abstract-only 桥接提示，不能当 FULL-TEXT 证据 |
| `related_work_bib` | `literature/related_work.bib` | survey section 引用 key 来源 |

### 输出文件

| 输出 | 文件 | 含义 |
| --- | --- | --- |
| `survey_decision` | `drafts/survey/decision.json` | 用户是否撰写 survey |
| `survey_plan` | `drafts/survey/survey_plan.json` | LLM 规划的 taxonomy、evolution narrative、outline、coverage selfcheck |
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

`SurveyWriterAgent(mode=survey_gate)` 调用 `ask_human`，问题是“是否额外撰写 taxonomy-driven professional survey paper”。用户选 no 时写：

```json
{"write_survey": false, "user_answer": "...", "note": "skip survey branch and continue T4"}
```

状态机解析 `decision.json` 后直接进入 T4。用户选 yes 时写 `{"write_survey": true, ...}`，进入 `T3.6-PLAN`。如果运行环境没有输入，`ask_human` 会让 runtime 暂停等待 resume，Agent 不允许伪造默认 yes/no。

#### `T3.6-PLAN`

LLM 读取 `synthesis.md`、`synthesis_workbench.json`、`domain_map.json`、`comparison_table.csv`、`paper_notes/`、`paper_notes_abstract/` 和 `.bib`。它要选择 taxonomy 主轴，构建 2-4 层 taxonomy tree，给每个 class 绑定 paper IDs，并写 evolution narrative。

这里需要 LLM 学术判断，不能由 tool 硬编码 taxonomy。`domain_map` 只提供 citation/evolution hint，`adjacent_transfers` 只提供邻接迁移素材，不能被当成最终分类结论。

输出 `survey_plan.json` 至少包含：

- `taxonomy.dimension`
- `taxonomy.rationale`
- `taxonomy.tree`
- `evolution_narrative`
- `outline`
- `coverage_selfcheck`

validator 会要求 taxonomy tree 非空、outline 至少包含 background/taxonomy/comparison 等核心 section、coverage_selfcheck 存在。

#### `T3.6-GATE-OUTLINE`

LLM 读取 `survey_plan.json`，把 taxonomy tree、outline、unclassified_papers、empty_classes 和 corpus_sufficiency 展示给用户。用户可以 approve 或 adjust。

如果 approve，只写 `outline_decision.json`，不改 plan。如果 adjust，LLM 根据用户意见就地修订 `survey_plan.json` 的相关 taxonomy/outline，并把 `user_adjustments` 和 `applied_adjustments` 记录下来。这里不重跑整个 PLAN，避免在 taxonomy gate 上无限循环。

#### `T3.6-GATE-CORPUS`

LLM 询问用户素材范围：

- `conservative`：只用现有 T2/T3/T3.5 语料，速度快；Scope 中必须诚实声明覆盖边界。
- `complete`：触发一次性定向补检计划，主要补 taxonomy 空类或弱类。

状态机解析 `corpus_decision.json.scope`：`complete` 进入 `T3.6-EXPAND`，`conservative` 进入 `T3.6-STATE`。

#### `T3.6-EXPAND`

Agent 调用 `expand_corpus_for_survey`。这个工具读取 `survey_plan.json`、`domain_map.json` 和 `papers_verified.jsonl`，为 `coverage_selfcheck.classes_needing_more_lit` / `empty_classes` 生成 query hints，输出 `survey_expansion.json`。

这一步不是 T4 -> T2 回路，也不自动宣称“领域缺口”。它只是一次性组织补检计划：哪些 taxonomy class 需要更多文献、建议查什么关键词、哪些 neighbor 只能作为邻接提示。LLM 可以在工具输出后补 `llm_review`，但不能循环补检。

#### `T3.6-STATE`

Agent 调用 `build_survey_state`。工具把 `survey_plan.json` 机械转换为：

- `survey_state.json`
- `section_outlines/background.md`
- `section_outlines/taxonomy.md`
- `section_outlines/theme_1.md` 到 `theme_4.md`
- `section_outlines/comparison.md`
- `section_outlines/challenges.md`
- `section_outlines/future.md`
- `section_outlines/introduction.md`
- `section_outlines/conclusion.md`
- `section_outlines/abstract.md`

主题章最多映射到 4 个固定 state-machine 节点。若 taxonomy 只有 2 个主题，`theme_3` 和 `theme_4` 在 `survey_state.json` 中标记为 `skipped`。这样每个主题仍是单独节点，避免一个 agent 一次写多个主题章。

#### `T3.6-SEC-*`

每个 `survey_section` 节点只写一个文件：

- `T3.6-SEC-BACKGROUND` -> `drafts/survey/sections/background.tex`
- `T3.6-SEC-TAXONOMY` -> `drafts/survey/sections/taxonomy.tex`
- `T3.6-SEC-THEME-1` -> `drafts/survey/sections/theme_1.tex`
- `T3.6-SEC-THEME-2` -> `drafts/survey/sections/theme_2.tex`
- `T3.6-SEC-THEME-3` -> `drafts/survey/sections/theme_3.tex`
- `T3.6-SEC-THEME-4` -> `drafts/survey/sections/theme_4.tex`
- `T3.6-SEC-COMPARISON` -> `drafts/survey/sections/comparison.tex`
- `T3.6-SEC-CHALLENGES` -> `drafts/survey/sections/challenges.tex`
- `T3.6-SEC-FUTURE` -> `drafts/survey/sections/future.tex`
- `T3.6-SEC-INTRO` -> `drafts/survey/sections/introduction.tex`
- `T3.6-SEC-CONCLUSION` -> `drafts/survey/sections/conclusion.tex`
- `T3.6-SEC-ABSTRACT` -> `drafts/survey/sections/abstract.tex`

每次调用输入只包含 `survey_state.json`、当前 `section_outline`、该节需要的证据文件和必要的相邻 section。Writer 不允许生成 `\documentclass`、`\begin{document}` 或其它 section 标题。写完后必须调用 `update_survey_section_state(section_id=..., status="written")`。

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

`SurveyWriterAgent(mode=survey_review)` 读取 `survey.tex`、`survey_audit.md/json`、`survey_plan.json`、`survey_state.json`、所有 `sections/*.tex`、`synthesis_workbench.json`、`domain_map.json`、`comparison_table.csv` 和 `.bib`。这一步用 LLM 的学术判断做综述模式审阅，不把 taxonomy 质量硬编码成 tool 规则。

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

`latex_compile` 会自动把编译报告落盘到 `drafts/survey/survey_compile_report.json`。Agent 不需要、也不允许手抄 `data.compile_report` 来伪造进度。validator 会同时检查 `survey.pdf`、`survey.log` 和 `survey_compile_report.json` 的 `semantics`、`tex_path` 与 `success=true`，因此“只有 PDF、没有 report”的旧产物不会通过。

如果当前环境缺少 TeX/Docker，`latex_compile` 会返回 `waiting_environment_*`，runtime 会暂停；修复环境后可 resume。如果是 citation 或 LaTeX 语法错误，应读 log 后修对应 section，再重新 assemble/compile，而不是一口气重写整篇 survey。

#### `T3.6-FEED`

Agent 调用 `export_survey_for_ideation`，导出：

- `ideation/survey_insights.json`
- `drafts/survey/survey_summary.md`

`survey_insights.json` 的语义是 `survey_insights_optional_ideation_fuel_not_gate`。T4 可以读取 taxonomy、challenge_hints、future_direction_hints，生成 `idea_origin=survey_driven` 候选或补强主线推理；但 survey insights 不是 gate，不强迫 T4 只按 survey 生成 idea。

### 续跑语义

T3.6 是 artifact-first 支线。每个 section 都是单独文件，`survey_state.json` 记录每节 status。中断后 resume 时：

- 如果 `decision.json` 已存在，survey gate 可直接完成。
- 如果 `survey_plan.json` 已存在，PLAN 不必重写。
- 如果某个 section 已写且 `survey_state` 标记 written/revised，validator 会接受，后续节点继续。
- 如果 `theme_3` / `theme_4` 是 skipped，section validator 不要求对应 tex 文件。
- 如果 review 失败，resume 会回到 `T3.6-REVIEW`，读取 `survey_review.md` 和 `survey_review_actions.json` 定位 section patch，不会重写整篇 survey。
- 如果 `survey.tex` 已拼装且 review 通过但 compile 失败，resume 会回到 `T3.6-COMPILE` 或当前状态，读取 log 修复。

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
| `exp_plan` | `ideation/exp_plan.yaml` | 实验计划，后续 T5/T7 会直接执行 |
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
8. 用 `ask_human` 做 Gate1，支持选择、选择并重构、合并多个候选、新想法和重新分析
9. 对选定方向做 pre-mortem
10. 写 `_family_distribution.md` 统计 mechanism family 分布
11. 最终产出：
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

1. **读取输入**：`project.yaml`、`literature/synthesis.md`、`literature/comparison_table.csv`、`literature/missing_areas.md`、`ideation/survey_insights.json`（如果 T3.6 写过 survey）、`user_seeds/seed_ideas.md`、`user_seeds/seed_constraints.md`

2. **深度分析 synthesis**：用 `read_file` 读取完整的 `literature/synthesis.md`，理解 Q1-QN、方法家族、共同假设、贡献空间地图、跨论文矛盾/张力、趋势和可操作问题

3. **读取 workbench 和 domain map**：用 `read_file` 读取 `literature/synthesis_workbench.json`，获取 `method_families`、`mechanism_claim_clusters`、`contribution_space`、`cross_paper_tensions` 和 `adjacent_transfers`（旧产物可能叫 `domain_consensus`）。再读取 `literature/domain_map.json`，把 citation graph 近邻作为 novelty 软信号来源。这些都是工具 hint，T4 必须先复核，不能直接当领域共识或研究缺口。

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

10. **Gate1**：用 `ask_human` 呈现候选方向，暴露 mechanism/counterfactual 是否为占位词、Pass 2 为什么不推荐、是否需要重构，让用户选择、选择并重构、合并、补充或重新分析。

11. **更新决策链**：每次 Gate1 后更新 `idea_scorecard.yaml`、`rejected_ideas.md`、`gate_decisions.json`。

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

最终报告必须分开写 `Collision Axis` 与 `Ambition Axis`，并给出 `Contribution Distance` 和 `Final Gate Verdict`。没有 close baseline 不能被惩罚为低 novelty；应写成高新颖/高风险。`contribution_type=routine` 或 `routine_risk` 不能无条件进入 T7/T8，必须建议回到 T4 重新 framing 或放弃。

同时，它加载 `novelty-audit` guidance，用 LLM 从每个假设中提取机制因果断言、操作对象和预期效果，再调用 `extract_mechanism_tuple` 保存 tuple；如果领域里需要更细的标签，可以把 `normalized_input_signal` 一并传给工具，而不是被工具枚举限制。对每篇疑似撞车论文，LLM 先阅读摘要/metadata 提取机制 tuple，再调用 `compare_mechanism_tuples` 获取 mechanical similarity hint。该工具只返回 `possible_true_collision` / `possible_mechanism_collision` / `possible_explanatory_competition` / `likely_distinct` 这类待审提示，不能直接给最终新颖性结论。

审计时它把搜索结果、synthesis、comparison table 和 mechanism hint 对齐，由 LLM 判断相似点、差异点、证据强度、是否需要补 baseline，以及最终标签 `true_collision / mechanism_collision / explanatory_competition / safe`。只有 LLM 确认机制、任务边界和贡献点都高度一致时，才把对应假设降为 Level 0。最后用 `write_file(“ideation/novelty_audit.md”, ...)` 写每个假设的 Level 0-3 判定。如果报告中出现真实 High/Medium Overlap，它还必须写 `ideation/collision_cases.md`，记录论文、相似点、差异点和处理建议；validator 会区分”High Overlap: none”这种空标题和真实案例，只有真实案例才强制 collision 文件。

### T4.5 的新颖性等级

- `Level 3`：高度新颖
- `Level 2`：中度新颖
- `Level 1`：低度新颖
- `Level 0`：无新颖性 / 明确撞车

### T4.5 非通过 verdict 的人工决策

T4.5 不再在 `return_to_T4_reframe` 或 `drop_due_to_collision` 时自动回退/失败。现在的分支语义是：

- `pass_to_experiment` / `pass_with_required_baselines`：直接进入 `T7`。
- `return_to_T4_reframe`、`drop_due_to_collision`、`reject`、`collision`、`fail`：进入 `T4.5-HUMAN-REVIEW`。

`T4.5-HUMAN-REVIEW` 是 gate-only 节点，`state_machine` 会直接进入 `WAITING_HUMAN`，不会再启动一次 `NoveltyAuditorAgent`。gate 展示：

- `ideation/novelty_audit.md`
- `ideation/_gate1_selection_brief.md`
- `ideation/idea_scorecard.yaml`
- `ideation/rejected_ideas.md`

用户可以选择：

- `continue_to_t7`：接受风险，继续进入完整实验。
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

## 6.9 T5：ExperimenterAgent（pilot）

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

## 6.10 T6：NoveltyAgent

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

## 6.11 T7：ExperimenterAgent（full）

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
  - `behavior.docker_required: true`
  - `behavior.gpu_required: true`
  - `ablation_min: 3`
  - `seed_ensemble_min: 3`

T7 进入 LLM 前会先执行 `run_experimenter_preflight()`：读取 `exp_plan.yaml`、项目预算、
direct-full 必需输入和 `agent_params.yaml` 的
`experimenter.modes.full.behavior.docker_required` /
`experimenter.modes.full.behavior.gpu_required`。如果 Docker
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

`PIAgent(evaluate)` 启动后读取 `experiments/results_summary.json`、`experiments/iteration_log.md` 和 `ideation/exp_plan.yaml`。它会把实验结果按原计划对齐：哪些 hypothesis 被验证，哪些实验失败或缺失，主指标是否达到目标，ablation 和多 seed 证据是否足够，是否还有 T6 要求的 baseline 没补。然后它用 `write_file("evaluation/evaluation_decision.md", ...)` 写一份决策报告，必须包含 `Situation`、`Options` 和至少一个 `next_task`。典型 next_task 可以是 `T8-STYLE-GATE` 或 `T8-RESOURCE`（证据足够，进入新版写作入口）、`T7`（继续补实验）、`T4`（回到假设重构）或其他状态机允许的节点；旧报告写 `T8` / `T8-WRITE` 时会映射到 `T8-STYLE-GATE`，只有合法 `drafts/writing_style.json` 已存在时才直接进入 `T8-RESOURCE`。完整 pipeline 中，StateMachine 会从 `evaluation_decision.md` 反向解析 `next_task`，再把 PI 推荐交给 human gate；用户可以接受推荐，也可以在 gate 里选择其它路径。

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
```

旧 `next_task: T8` / `next_task: T8-WRITE` 会先映射到 `T8-STYLE-GATE`；只有 workspace 已有合法 JSON 且 `venue_style` 为 `is` / `ccf_a` / `both` 的 `drafts/writing_style.json` 时，状态机才会跳过风格 gate 并进入 `T8-RESOURCE`。旧 `T8-SECTIONS` 单任务入口仍映射到 `T8-SECTION-PLAN`。旧 `T8-SEC-LIMITATIONS` 单任务入口映射到 `T8-SEC-CONCLUSION`；Limitations 不再是独立 section，而是 Conclusion 内的 `\subsection{Limitations}`。

### 章节写作顺序与对齐契约

`SECTION_WRITING_SEQUENCE` 当前为 7 个正文作业：

```text
methodology -> experiments -> related_work -> analysis -> introduction -> conclusion -> abstract
```

这个顺序先稳定方法和结果，再写定位、解释和引言，最后写 Conclusion/Limitations 和 Abstract。每个 section 写作时 Writer 会读取前一个 section 文件尾部约 1200 字符作为局部衔接上下文，但不会把整篇论文塞进一次 prompt。

T8 的核心跨章节契约是 `drafts/alignment_matrix.json`。它把每个 cid 贯通为：

```text
motivation -> contribution -> related_gap -> design_choice -> experiment -> analysis
```

`build_alignment_matrix` 的行来自 `cdr_claim_ledger.json` 中的 `contribution_chains`，不是原始 CDR evidence slots。`contribution_chains` 是 3-4 条最终 contribution bullet 的机械 lane，用来避免把 7 个左右的 evidence/section slot 误当成论文贡献。工具只生成 seed 和审计 hint；motivation、gap、contribution wording、设计解释和分析判断必须由 LLM 阅读 artifact 后完成。若某段明确兑现 cid，可以用 LaTeX 注释标注，例如 `% [C1]` 或 `% [C1,C3]`，供 `audit_writing_craft` 做 traceability hint；这不是正文格式硬约束。

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
- `audit_writing_craft`：检查 alignment/craft 机械问题，如独立 Limitations、Abstract 正式引用、贡献条数提示、CID traceability hint、实验 table/metric 锚点、数字可追溯、AI 套话等；当 `venue_style=both` 时也会审计两套风格变体。Abstract 不放正式引用；citation key 真实性由 `audit_manuscript_claims` 负责。
- `build_manuscript_revision_patches`：把 reviewer issue 定位成 section patch list。

这些工具只处理机械重复、可解析、可校验的工作；论文贡献判断、理论定位、gap 表达、section prose 和修订取舍仍由 LLM 完成。

### 6.12.1 T8-STYLE-GATE：WriterAgent（style_gate）

`T8-STYLE-GATE` 读取 `project.yaml` 的 `target_venue`，再根据 [config/venue_style_map.yaml](../config/venue_style_map.yaml) 给出默认建议：IS 顶刊风格或 CCF-A 会议风格。Writer 调用 `ask_human`，让用户选择 `is`、`ccf_a` 或 `both`，并写 `drafts/writing_style.json`。

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

本章先定义 artifact 和 design rationale，再讲整体架构、组件、notation、算法和实现。每个设计选择都要解释 why，不用实验结果证明方法有效；必要时用 `% [Cn]` 注释帮助追踪对应贡献。写完必须调用 `update_manuscript_section_state(section_id="methodology", status="written")`。

### 6.12.6 T8-SEC-EXPERIMENTS：WriterAgent（section_draft, section_id=experiments）

`T8-SEC-EXPERIMENTS` 只写 `drafts/sections/experiments.tex`。Writer 读取 `alignment_matrix.json` 的 experiment 列、`results_summary.json`、`ablations.csv`、runs/configs、seed ensemble、`exp_plan.yaml` 和 `figure_table_plan.json`。

本章要把 RQ 前置，每个 RQ 标明验证哪些 cid；随后写 setup、datasets、baselines、metrics、seeds、compute、main results 和 ablations。所有数字必须来自结果 artifact；缺 seed/error bar/baseline 时写 TODO 或转入 Conclusion 的 Limitations 子段，不能编造。写完更新 `paper_state.json` 中 `experiments.status`。

### 6.12.7 T8-SEC-RELATED：WriterAgent（section_draft, section_id=related_work）

`T8-SEC-RELATED` 只写 `drafts/sections/related_work.tex`。Writer 读取 `alignment_matrix.json` 的 related_gap、`literature/synthesis.md`、`literature/synthesis_workbench.json`、`literature/domain_map.json`、`ideation/idea_scorecard.yaml`、`comparison_table.csv`、paper notes 和 `related_work.bib`。在 task contract 中，`synthesis_workbench`、`domain_map`、`idea_scorecard` 和 `alignment_matrix` 都是强前置；缺失时应先回到 T3.5/T4/T8-RESOURCE 修复。

Related Work 按 competing design rationale 组织，不按论文流水账。每个主题 subsection 应说明该流派共同 rationale、代表工作、共同局限或 tension，然后落到本文某条贡献 `% [Ci]`。`synthesis_workbench.adjacent_transfers` 用来识别邻接领域的可迁移机制，`domain_map.core/adjacent/citation_edges` 用来提示主干与邻接结构，`idea_scorecard.nearest_prior_work` 和 alignment matrix 的 `nearest_prior_work` 用来做最近工作差异化定位，`counterfactual` / `novelty_signal` 只作为 marginal 风险提示。citation key 必须存在于 `.bib`；工具只给 citation pool 和结构化 hint，prior-work positioning 由 LLM 判断。

`T8-DRAFT` 的 `audit_writing_craft` 会做一个非阻断的 `related_work_pre_t5_signal_consumption` 检查：如果 Related Work 完全看不到 nearest-prior-work、adjacent-transfer、cross-paper tension 或对应文本片段，就给 WARN。它不会替代 LLM 判断某篇工作是否相关，只提示“上游花资源生成的 Pre-T5 素材可能没被写作消费”。

### 6.12.8 T8-SEC-ANALYSIS：WriterAgent（section_draft, section_id=analysis）

`T8-SEC-ANALYSIS` 只写 `drafts/sections/analysis.tex`。Writer 读取 `alignment_matrix.json` 的 analysis 列、已写 Method/Experiments、`ablations.csv`、`iteration_log.md` 和 `novelty_audit.md`。

本章解释实验和消融如何支持、削弱或仅部分支持 design rationale；至少提出并排除一个 alternative explanation，呈现 failure case 和 sensitivity。不能把未做的 T5/T6 或额外实验写成已完成。必要时用 `% [Cn]` 注释帮助 Reviewer 和 tool 追踪核心 cid。

### 6.12.9 T8-SEC-INTRO：WriterAgent（section_draft, section_id=introduction）

`T8-SEC-INTRO` 在 Method、Experiments、Related Work 和 Analysis 之后运行，只写 `drafts/sections/introduction.tex`。Writer 读取 `alignment_matrix.json` 的 motivation/contribution、CDR ledger、synthesis、hypotheses、results，以及已写 Method/Experiments/Related Work。

Introduction 采用 5-move：Problem、Gap、Approach、numbered Contributions、venue-specific closing。gap/motivation 通常不超过 3 个，contribution 3-4 条，并应和 alignment matrix 的 cid 形成清晰逻辑对应；可用 `% [Ci]` 作为追踪提示，但不要求机械一一等数。`ccf_a` 风格需要量化 results headline；`is` 风格需要理论或 reference anchor。Intro 不能超过已有 evidence。

### 6.12.10 T8-SEC-CONCLUSION：WriterAgent（section_draft, section_id=conclusion）

`T8-SEC-CONCLUSION` 只写 `drafts/sections/conclusion.tex`，同时承担 Limitations。Writer 读取 `alignment_matrix.json`、Introduction、Experiments、`ideation/risks.md`、`novelty_audit.md`、`iteration_log.md` 和 `paper_state.json`。

Conclusion 先收束本文证明了什么和可迁移 design knowledge，然后必须写 `\subsection{Limitations}`。Limitations 子段要具体说明 direct-full/T5/T6 evidence boundary、baseline 覆盖、数据规模、外部有效性、compute/seed 和复现风险。Conclusion 不允许引入新 claim、新数字或新引用；如果需要新信息，应回到对应章节和 artifact。

### 6.12.11 T8-SEC-ABSTRACT：WriterAgent（section_draft, section_id=abstract）

`T8-SEC-ABSTRACT` 最后运行，只写 `drafts/sections/abstract.tex`。Writer 读取 `paper_state.json`、`section_outlines/abstract.md`、`alignment_matrix.json` 和已写的 introduction/methodology/experiments/analysis/conclusion。

Abstract 用 5 句骨架压缩全文：Problem、Gap、Approach、Key result、Contribution type。它不放正式引用：不使用 LaTeX citation command，不写作者-年份括号引用，也不写数字引用；具体 prior work citation 放到 Introduction 或 Related Work。它也不能引入正文没有的数字、claim 或术语。`ccf_a` 风格通常 150-300 词，`is` 风格通常 200-300 词。

### 6.12.12 T8-DRAFT：WriterAgent（draft）

`T8-DRAFT` 先调用 `assemble_manuscript(section_dir="drafts/sections", output_path="drafts/paper.tex", outline_path="drafts/outline.md", target_venue=<target_venue>, venue_style=<venue_style>)`。该工具按 Introduction、Related Work、Method、Experiments、Analysis、Conclusion 顺序拼装正文，把 Abstract 放入 `abstract` 环境，并自动加入 `\documentclass`、基础 package、title 和 `\bibliography{related_work}`。如果旧 workspace 残留 `drafts/sections/limitations.tex`，assemble 会把它合并到 Conclusion 的 `\subsection{Limitations}`，不会生成独立 `\section{Limitations}`。如果 `venue_style=both`，assemble 还会派生 `drafts/is/paper.tex` 和 `drafts/ccf_a/paper.tex`，两者共享同一 alignment matrix 和 section source，作为后续风格化 revision 的入口；随后 Writer 需要分别改写这两个文件，使 IS 稿更强调 theory/design knowledge/validity，使 CCF-A 稿更强调紧凑 problem framing、量化结果和可复现实验。

随后 Writer 做全局 spot-check：术语、变量名、baseline 名称、章节衔接、Intro/Conclusion 呼应、Method/Experiment setup 一致性。需要改正文时先改对应 `drafts/sections/<section>.tex`，再重新 assemble。

最后必须调用两个审计工具：

- `audit_manuscript_claims(paper_path="drafts/paper.tex", output_path="drafts/manuscript_audit.md")`：检查 citation key、数字、figure/table refs 和核心章节。
- `audit_writing_craft(paper_path="drafts/paper.tex", sections_dir="drafts/sections", paper_state_path="drafts/paper_state.json", alignment_matrix_path="drafts/alignment_matrix.json", venue_style=<venue_style>, output_path="drafts/craft_audit.md")`：检查独立 Limitations、Abstract 正式引用、CID traceability hint、每个 cid 的 experiment table/metric/ablation 锚点、related-work orphan/laundry-list、AI 套话、贡献条数和数字可追溯，并同时写 `drafts/craft_audit.json`。`abstract_no_cite`、`number_traceability`、独立 Limitations、缺 cid experiment artifact 等机械可查问题是 FAIL；贡献条数、CID 注释覆盖和 abstract wordcount 是 WARN。

Validator 要求 `paper.tex`、`manuscript_audit.md`、`craft_audit.md` 和 `craft_audit.json` 存在，并检查 LaTeX wrapper、必要章节、BibTeX key、关键 craft check 是否存在且没有 FAIL。如果 `writing_style.json` 选择 `both`，还要求 `drafts/is/paper.tex`、`drafts/is/craft_audit.json`、`drafts/is/style_revision_notes.md`、`drafts/ccf_a/paper.tex`、`drafts/ccf_a/craft_audit.json` 和 `drafts/ccf_a/style_revision_notes.md` 存在；去掉 ResearchOS 注释后，两个变体不能与主稿正文完全相同。

### 6.12.13 T8-SELF-CHECK：WriterAgent（self_check）

`T8-SELF-CHECK` 读取 `paper.tex`、`manuscript_audit.md`、`craft_audit.md`、`alignment_matrix.json`、`results_summary.json` 和 `related_work.bib`，写 `drafts/self_check.md`。

自查包括 argument chain、number audit、citation audit、figure/table audit、reproducibility audit、direct-full/T5/T6 boundary 和 revision TODO。`craft_audit.md` 的 FAIL 必须进入 High TODO，WARN 进入 Medium TODO，并说明是否已在正文处理。

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
- `T8-DRAFT` 从 section files 重建 `paper.tex`，如需改正文先回改 section；`venue_style=both` 会同步重建两套风格变体和对应 craft audit。
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

**匿名化 precheck 默认关闭**（`submission.py` line 83: `enforce_anonymization_precheck` 默认 `False`）。只有当 `agent_params.yaml` 中 `submission.behavior.enforce_anonymization_precheck` 设为 `true` 时，才会在进入 LLM 前拦截检查邮箱、URL、GitHub 等匿名化问题。这便于本地调试或非匿名投稿场景直接产出投稿包。

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
- 最多进行 `submission.behavior.max_compile_attempts` 轮

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
- 当前 checked-in `config/agent_params.yaml` 已在每个基础 agent 的 `budget` 下设置
  `unlimited_budget: true`，因此默认不会因为 agent runtime 的
  `max_steps`、`max_tokens_total`、`max_wall_seconds` 暂停，也不会触发预算扩限 gate；
  但 step/token/cost 仍会记录，LLM 单次超时、工具超时、Docker/TeX 专用超时、
  workspace 权限、输出校验和项目级实验预算检查仍然生效。若需要从上层默认恢复有限预算，
  在 task 或 mode 中写 `budget.unlimited_budget: false`。
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
