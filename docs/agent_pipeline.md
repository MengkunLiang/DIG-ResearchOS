# ResearchOS Agent Pipeline

本文档面向两类读者：

- 使用者：想知道系统从 T1 到 T9 到底在做什么、每个阶段会产出什么、什么时候应该 `run`、什么时候应该 `run-task`
- 开发者：想知道每个 Agent 的真实职责、输入输出、状态跳转、恢复机制、预算机制、Gate 机制、MCP/skill/工具权限是怎么接入的

本文档描述的是 **当前仓库实现出来的真实 pipeline**，不是抽象愿景图。所有说明均以当前代码为准。

---

## 1. 一句话总览

ResearchOS 当前的完整研究流水线可以概括为：

`T1 项目初始化 -> T2 文献检索与验证 -> T3 精读 -> T3.5 综合 -> T4 假设生成 -> T4.5 新颖性预审 -> T5 Pilot -> T6 基于 Pilot 的新颖性复核 -> T7 完整实验 -> T7.5 PI 评估 -> Human Gate -> T8 多轮写作/审稿/修订 -> T9 投稿打包`

它不是一个“单 agent 包打天下”的系统，而是一个由：

- `StateMachine` 负责推进
- 多个 `Agent` 负责阶段任务
- `ToolRegistry` 提供工具
- `Workspace` 作为唯一落盘事实源
- `AgentRunner` 执行 LLM + tool 循环

共同组成的 **artifact-first research runtime**。

---

## 2. 当前真实阶段图

当前真实状态定义在 [config/state_machine.yaml](../config/state_machine.yaml)。

主链如下：

```text
HELLO

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

几个容易混淆的点：

- `T8` 不是单节点，而是拆成写作、初稿、两轮审稿、两轮修订
- `T8-REVIEW` 不是当前真实状态名；真实状态名是 `T8-REVIEW-1` 和 `T8-REVIEW-2`
- `T9` 不是继续写论文，而是投稿包构建、编译收敛和提交前检查

---

## 3. Pipeline 的三个基本对象

### 3.1 Workspace 是唯一事实源

所有阶段都围绕 workspace 工作。默认目录在 [config/runtime.yaml](../config/runtime.yaml) 中由 `workspace.default_root` 控制，标准目录结构由 [researchos/runtime/workspace.py](../researchos/runtime/workspace.py) 初始化。

核心原则：

- 所有输入输出都落盘到 workspace
- Agent 不依赖“上一轮对话还记得什么”
- 续跑、恢复、单任务调试都通过读取已有 artifact 来实现

典型目录包括：

- `user_seeds/`
- `literature/`
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`（trace、logs、resume 快照等）

### 3.2 Task I/O Contract

每个 task 的输入输出契约定义在 [researchos/orchestration/task_io_contract.py](../researchos/orchestration/task_io_contract.py)。

它的作用不是“文档注释”，而是 runtime 真正会读取的约束：

- 单任务运行前校验前置输入
- 解析 `inputs` / `outputs_expected`
- `--from <other-workspace>` 时复制前置 artifact
- 输出校验器据此判断产物是否齐全

### 3.3 State Machine

状态推进由 [researchos/orchestration/state_machine.py](../researchos/orchestration/state_machine.py) 解释 [config/state_machine.yaml](../config/state_machine.yaml)。

它负责：

- 初始状态
- 当前任务
- 成功/失败后的下一跳
- Gate 暂停与恢复
- resume 语义
- 迭代计数
- task 级 `llm/budget/tools` 覆盖

---

## 4. 两种运行模式

ResearchOS 有两套非常重要的运行语义。

### 4.1 完整流水线模式

命令入口：

- `researchos run --workspace ...`
- `researchos resume --workspace ...`

实现入口：

- [researchos/cli_runners/complete_pipeline.py](../researchos/cli_runners/complete_pipeline.py)

特征：

- 真正推进 FSM
- 会处理 `pending_gate`
- 会从一个 task 自动跳到下一个 task
- `T7.5 -> ask_human -> T8` 这种链条只有这里才完整生效

适合：

- 正式跑项目
- 测试完整状态机
- 测试 human gate、branch、resume

### 4.2 单任务调试模式

命令入口：

- `researchos run-task T3 --workspace ...`
- `researchos run-task T9 --workspace ...`

实现入口：

- [researchos/cli_runners/single_task.py](../researchos/cli_runners/single_task.py)

特征：

- 只跑一个 task
- 不推进到下一状态
- 仍会做输入校验、artifact 校验、resume 语义注入
- 可以配 `--from` 复制前置产物

适合：

- 本地调试某个 agent
- 复现某一个阶段的问题
- 修 prompt / 工具 / validator 后做快速验证

### 4.3 两者差异总结

| 维度 | `run/resume` | `run-task` |
| --- | --- | --- |
| FSM 跳转 | 会 | 不会 |
| Gate 处理 | 会 | 不会推进后续链 |
| 单阶段快速验证 | 一般 | 最适合 |
| 完整 workflow 体验 | 最适合 | 不适合 |
| `T7.5 -> human gate -> T8` | 完整支持 | 只能单独测 `T7.5` 本身 |

---

## 5. 各阶段详细说明

下面按当前真实实现说明每个阶段。

### 5.1 HELLO

- Agent：`HelloAgent`
- 代码： [researchos/agents/hello.py](../researchos/agents/hello.py)
- 目标：验证 runtime 最小闭环
- 输入：无
- 输出：`hello.txt`

实现逻辑：

1. 调用 `echo`
2. 调用 `write_file`
3. 写入 `Hello, Runtime!`
4. 调用 `finish_task`

成功标准：

- `hello.txt` 存在
- 文件内容精确为 `Hello, Runtime!`

用途：

- 新环境 smoke test
- 测 LLM、tool 调用、finish_task、validator

### 5.2 T1：项目初始化

- Agent：`PIAgent(mode="init")`
- 代码： [researchos/agents/pi.py](../researchos/agents/pi.py)
- Prompt： [researchos/prompts/pi.j2](../researchos/prompts/pi.j2)
- 输出：`project.yaml`、`state.yaml`，以及可选 seed 文件

主要输入：

- workspace 初始状态
- 可选 `user_seeds/*`
- CLI 传入的 `topic` / `research_direction`

主要输出：

- `project.yaml`
- `state.yaml`
- 可选的 `user_seeds/seed_papers.jsonl`
- 可选的 `user_seeds/seed_ideas.md`
- 可选的 `user_seeds/seed_constraints.md`
- 可选的 `user_seeds/seed_external_resources.jsonl`

实现逻辑：

- `PIAgent` 在 `init` 模式下按多轮结构收集：
  - 研究方向
  - 关键约束
  - 种子论文
  - 初始想法
  - 外部资源
- 输出 `project.yaml`
- 对 `project.yaml` 做 schema 校验
- 对 `seed_ensemble` 做格式检查
- 做伦理风险 screening

当前特性：

- T1 是强结构化阶段，不是自由聊天
- 输出必须通过 schema
- 可读取 `user_seeds/` 中已有内容

### 5.3 T2：文献检索、验证与精读队列生成

- Agent：`ScoutAgent`
- 代码： [researchos/agents/scout.py](../researchos/agents/scout.py)
- Prompt： [researchos/prompts/scout.j2](../researchos/prompts/scout.j2)

主要输入：

- `project.yaml`
- 可选 `user_seeds/seed_papers.jsonl`
- 可选 `user_seeds/seed_constraints.md`
- 可选 `user_seeds/seed_ideas.md`
- 可选 `user_seeds/seed_external_resources.jsonl`

主要输出：

- `literature/papers_raw.jsonl`
- `literature/papers_dedup.jsonl`
- `literature/papers_verified.jsonl`
- `literature/verification_failures.jsonl`
- `literature/deep_read_queue.jsonl`
- `literature/access_audit.md`
- `literature/search_log.md`
- `literature/missing_areas.md`

T2 当前不是单纯“搜论文”，而是 4 层流程：

1. query 扩展与去重检查
2. 多源检索
3. 元数据去重与 enrich
4. verification + access triage + deep-read queue 构建

当前可用数据源/工具包括：

- `multi_source_search`
- `semantic_scholar_search`
- `arxiv_search`
- `openalex_search`
- `crossref_search`
- 对应的 metadata/get_work 工具

T2 的关键实现点：

- `papers_raw` 和 `papers_dedup` 分开
- 先 dedup，再 metadata verification
- 生成 `papers_verified`
- 生成 `verification_failures`
- 再基于 verified 池构建 `deep_read_queue`
- 再输出 `access_audit`

种子论文策略：

- seed papers 不是普通候选，而是高优先级对象
- deep-read queue 构建时，seed 优先级最高

### 5.4 T3：深度阅读

- Agent：`ReaderAgent(mode="read")`
- 代码： [researchos/agents/reader.py](../researchos/agents/reader.py)
- Prompt： [researchos/prompts/reader.j2](../researchos/prompts/reader.j2)

主要输入：

- `project.yaml`
- `papers_dedup.jsonl`
- `papers_verified.jsonl`
- `deep_read_queue_pending.jsonl`（若恢复运行已生成）
- `deep_read_queue.jsonl`
- `access_audit.md`
- `missing_areas.md`

主要输出：

- `literature/paper_notes/`
- `literature/comparison_table.csv`
- `literature/related_work.bib`

当前 T3 的真实逻辑：

1. 优先读取 `deep_read_queue_pending.jsonl`
2. 否则读取 `deep_read_queue.jsonl`
3. 检查已有 `paper_notes`
4. 对未完成论文逐篇处理
5. 优先读本地 PDF
6. 本地没有时尝试 `fetch_paper_pdf`
7. 优先 `extract_paper_sections`
8. section 质量差时回退 `extract_pdf_text`
9. 再退化到 `ABSTRACT-ONLY`

产物三件套：

- 每篇一份 note
- comparison table 累积行
- BibTeX 累积条目

当前 T3 最重要的恢复能力：

- 会根据现有 `paper_notes/` 自动生成 `deep_read_queue_pending.jsonl`
- 可以在预算中断后继续读剩余论文
- 不再默认从头扫完整 dedup 池

### 5.5 T3.5：文献综合

- Agent：`ReaderAgent(mode="synthesize")`
- 输入：
  - `paper_notes/`
  - `comparison_table.csv`
  - `missing_areas.md`
- 输出：
  - `literature/synthesis.md`

这一步是把逐篇笔记压缩成领域综述，主要回答：

- 现有方法如何分族
- 共同假设是什么
- 哪些 gap 真实存在
- 哪些地方值得继续做假设生成

### 5.6 T4：假设生成

- Agent：`IdeationAgent`
- 代码： [researchos/agents/ideation.py](../researchos/agents/ideation.py)
- Prompt： [researchos/prompts/ideation.j2](../researchos/prompts/ideation.j2)

主要输入：

- `project.yaml`
- `synthesis.md`
- `comparison_table.csv`
- `missing_areas.md`
- 可选 `seed_ideas.md`
- 可选 `seed_constraints.md`

主要输出：

- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/risks.md`

T4 的真实工作流比“写几个 idea”复杂得多：

1. 从 synthesis 中抽核心问题
2. 生成 3-5 个候选方向
3. 用多视角 lenses 审视
4. 通过 Gate 让用户选择/合并/重发散
5. 做 pre-mortem
6. 产出正式 hypotheses、实验计划、风险评估

### 5.7 T4.5：新颖性预审

- Agent：`NoveltyAuditorAgent`
- Prompt： [researchos/prompts/novelty_auditor.j2](../researchos/prompts/novelty_auditor.j2)
- 输出：`ideation/novelty_audit.md`

作用：

- 在真正做实验前，先判断假设是否明显撞车
- 基于 hypothesis + synthesis + comparison table 做近年相关工作搜索
- 为后续 T6 留下新颖性基线结论

### 5.8 T5：Pilot 实验

- Agent：`ExperimenterAgent(mode="pilot")`
- Prompt： [researchos/prompts/experimenter.j2](../researchos/prompts/experimenter.j2)
- 输出：
  - `pilot/pilot_plan.yaml`
  - `pilot/pilot_code/`
  - `pilot/pilot_results.json`
  - `pilot/motivation_validation.md`

Pilot 的重点不是“把所有实验都做完”，而是：

- 最小实现
- smoke test
- 小规模验证方向是否值得继续

当前特性：

- 支持 `pilot_resume_state.json`
- 已有代码会优先复用
- 预算到顶时可进入 budget gate

### 5.9 T6：基于 Pilot 的新颖性复核

- Agent：`NoveltyAgent`
- Prompt： [researchos/prompts/novelty.j2](../researchos/prompts/novelty.j2)

主要输入：

- `hypotheses.md`
- `exp_plan.yaml`
- `pilot_results.json`
- `motivation_validation.md`
- `novelty_audit.md`
- `comparison_table.csv`
- `synthesis.md`

主要输出：

- `novelty/novelty_report.md`
- `novelty/collision_cases.md`
- `novelty/must_add_baselines.md`

T6 当前已经改成“增量复核”，不是重新全量跑一次 T4.5：

- 优先继承 `novelty_audit.md`
- 只对高风险或不确定假设补搜
- 只补“新出现工作”和“缺失 baseline”
- 搜索范围明显收缩

### 5.10 T7：完整实验

- Agent：`ExperimenterAgent(mode="full")`
- 输出：
  - `experiments/results_summary.json`
  - `experiments/runs/`
  - `experiments/configs/`
  - `experiments/iteration_log.md`
  - `experiments/ablations.csv`

与 T5 的区别：

- T5 是试点
- T7 是正式实验

T7 的关键强约束：

- headline 实验要跑多 seed
- 要做 ablation
- 要记录 iteration log
- 要做 failure mode 检查
- 要生成结果摘要，供后续写作消费

当前特性：

- 有 `full_resume_state.json`
- 会尽量复用已有 code / runs
- 不应无脑从零重跑整个实验目录

### 5.11 T7.5：PI 评估与分流

- Agent：`PIAgent(mode="evaluate")`
- 输出：`evaluation/evaluation_decision.md`

这是当前主链中的重要新增层。

作用：

- 在完整实验结束后，不立即进入写作
- 先由 PI 视角判断实验是否足够支撑论文写作
- 生成 `Situation / Options / next_task`
- 再进入 human gate 让人确认是否按 PI 推荐推进

当前逻辑：

- `StateMachine` 会解析 `evaluation_decision.md` 中的 `next_task`
- human gate 可选择按推荐走，也可人工改去 `T7`、`T4`、`T8` 或结束

### 5.12 T8：写作、审稿、修订

T8 当前真实拆分为 6 个节点：

- `T8-WRITE`
- `T8-DRAFT`
- `T8-REVIEW-1`
- `T8-REVISE-1`
- `T8-REVIEW-2`
- `T8-REVISE-2`

#### T8-WRITE

- Agent：`WriterAgent(phase=outline)`
- 产物：`drafts/outline.md`

#### T8-DRAFT

- Agent：`WriterAgent(phase=draft)`
- 产物：`drafts/paper.tex`

#### T8-REVIEW-1 / T8-REVIEW-2

- Agent：`ReviewerAgent`
- 产物：
  - `drafts/review_rounds/round_1.md`
  - `drafts/review_rounds/round_2.md`

#### T8-REVISE-1 / T8-REVISE-2

- Agent：`WriterAgent(phase=revise)`
- 产物：修订后的 `drafts/paper.tex`

当前 T8 特性：

- reviewer 现在可正确使用 `list_files`
- writer 的 `project.name` 渲染兼容 `project_id`
- 两轮 review / revise 是真实独立节点

### 5.13 T9：投稿打包与编译收敛

- Agent：`SubmissionAgent`
- Prompt： [researchos/prompts/submission.j2](../researchos/prompts/submission.j2)
- 输出：
  - `submission/bundle/`
  - `submission/migration_report.md`

T9 当前已经不是“简单复制文件”，而是：

1. 读取 `drafts/paper.tex`
2. 迁移到目标 venue 模板
3. 组织 bundle
4. 尝试编译
5. 如果失败，读 `.log` / 命令输出
6. 修复并重试
7. 编译成功后检查 bundle 是否齐全
8. 生成 migration report
9. 进入 submission gate

当前 validator 已收紧，要求：

- `submission/bundle/main.tex` 存在
- `submission/bundle/references.bib` 存在
- `submission/bundle/main.pdf` 存在
- `migration_report.md` 存在
- 报告里必须显式写 `编译状态: 成功`
- 若存在 `main.log`，不得包含 fatal error

匿名化前置检查：

- 当前可配置
- 默认关闭 `enforce_anonymization_precheck`
- 这样本地调试或非匿名投稿场景不会被 pre-hook 直接拦下

---

## 6. Pipeline 的附加能力

### 6.1 Resume / 恢复机制

恢复能力不是只做在某一个 Agent 里，而是三层叠加：

1. `task_recovery.py` 为所有 task 生成恢复快照
2. 单任务模式会自动注入 `resume` 语义
3. 特定阶段有专项恢复逻辑

当前重点恢复点：

- T3：基于已有 `paper_notes` 生成 `deep_read_queue_pending.jsonl`
- T5：基于已有 `pilot_*` 产物补缺
- T6：可读取已有 novelty 输出继续
- T7：基于已有 experiment 目录补缺
- T9：当前主要依赖 artifact-first 续跑语义

### 6.2 Budget 与扩限 Gate

Budget 由 `AgentRunner` 和相关 runtime 配置共同控制。

当前支持限制：

- `max_steps`
- `max_tokens_total`
- `max_wall_seconds`

当前增强：

- 超预算时可以走 budget escalation gate
- 已扩限次数会展示给用户
- 当前已有输出也会展示
- 现在默认可以无限次扩限，只要每次人工确认继续

### 6.3 LLM Routing / Fallback

模型选择不是写死在 agent 代码里，而是多层覆盖：

1. `state_machine.yaml` task 级覆盖
2. `agent_params.yaml` agent 默认值
3. `model_routing.yaml` profile / tier / fallback

当前真实策略：

- 以 SiliconFlow 为主
- 默认 profile 下已配置 MiniMax fallback
- fallback 现在会先尝试所有候选，再进入下一轮重试
- `max_context_override` 不再错误地压扁 fallback 链

### 6.4 Human Gate

当前 gate 不只是“暂停一下”，而是正式状态机分支点。

典型场景：

- T6 novelty gate
- T7.5 human review gate
- T9 submission gate

gate 会写入 `state.yaml` 的 `pending_gate`，之后通过 `resume` 继续。

### 6.5 MCP

MCP 不是强依赖，但 runtime 已有适配层。

作用：

- 把外部 MCP server 包装成 runtime tool
- 在 tool registry 中注册成 `mcp_<server>_<tool>`

注意：

- 当前很多任务不是强依赖 MCP 才能跑
- 没有加载对应 MCP server 时，相关 skill/agent 只会降级，不一定直接崩

### 6.6 Skills

ResearchOS 现在既支持 agent，也支持独立 skill。

当前 skill 能力：

- `list-skills`
- `run-skill`
- 从 `SKILL.md` frontmatter 读取 metadata
- 自动注册 `skills/*/tools/*.py`
- 把 skill 包装成 `SkillAgent`

当前仓库内置 skill：

- `deepxiv`
- `paper-compile`
- `paper-write`

其中：

- `paper-compile` 可直接用
- `deepxiv` 可直接用
- `paper-write` 可运行，但会跳过当前 runtime 没注册的高级工具

---

## 7. 单独使用某个 Agent 的逻辑

很多使用者最关心的是：单独跑某个 task 时，和整链跑时到底差在哪里。

### 7.1 单独跑不会自动推进下一阶段

例如：

```bash
researchos run-task T7.5 --workspace ./workspace/local-test2
```

这只会生成 `evaluation/evaluation_decision.md`，不会自动进入后面的 human gate 和 `T8-WRITE`。

### 7.2 单独跑依然会做恢复与 artifact 校验

例如：

- `run-task T3` 会自动看已有 `paper_notes`
- `run-task T7` 会看已有 `experiments/`
- `run-task T9` 会看已有 `submission/bundle`

### 7.3 推荐场景

- 调一个阶段的问题：用 `run-task`
- 测完整链路：用 `run` / `resume`

---

## 8. 当前 pipeline 的使用建议

### 8.1 想从零跑完整项目

```bash
researchos init-workspace --workspace ./workspace/demo --project-id demo --topic "your topic"
researchos run --workspace ./workspace/demo
```

### 8.2 想继续一个中断项目

```bash
researchos resume --workspace ./workspace/local-test2
```

### 8.3 想只测某个阶段

```bash
researchos run-task T3 --workspace ./workspace/local-test2
researchos run-task T7.5 --workspace ./workspace/local-test2
researchos run-task T9 --workspace ./workspace/local-test2
```

---

## 9. 当前已知现实限制

1. pipeline 已经可跑，但仍受外部 provider 稳定性影响
2. `paper-write` skill 会因未注册高级工具而降级
3. 部分 config 字段是“有文档、部分接线”，不代表全部 runtime 都已消费
4. LaTeX 编译闭环已加强，但复杂论文仍可能需要多轮修复

---

## 10. 建议联读

建议把本文与以下文档配合使用：

- [docs/runtime.md](./runtime.md)
- [docs/docker.md](./docker.md)
- [docs/config.md](./config.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
