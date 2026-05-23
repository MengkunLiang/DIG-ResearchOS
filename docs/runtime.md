# ResearchOS Runtime

本文档从实现角度解释 ResearchOS 当前 runtime 的结构、能力边界和扩展方式。

如果 [docs/agent_pipeline.md](./agent_pipeline.md) 回答的是“系统按什么研究流程工作”，那么本文档回答的是：

- runtime 是怎么把一个 task 跑起来的
- agent、tool、skill、MCP 是怎么接到一起的
- 状态机、trace、日志、恢复、budget、fallback 是如何协同工作的
- 开发者应该从哪里注册新工具、接入新 agent、扩展新 skill

---

## 1. Runtime 的核心定位

ResearchOS 的 runtime 不是一个“prompt 拼接脚本”，而是一套可重入、可恢复、可审计的多阶段执行基础设施。

它的设计关键词是：

- `artifact-first`
- `state-machine-driven`
- `tool-mediated`
- `workspace-bounded`
- `LLM-provider-decoupled`

换句话说：

- 任务靠 workspace 内的文件推进
- 状态靠 `state.yaml` 推进
- LLM 不直接读写宿主机，而是通过 tool
- 工具读写都受 workspace policy 限制
- 模型供应商和任务逻辑解耦

---

## 2. Runtime 总体架构

当前主链大致是：

```text
CLI
 -> RuntimeSettings / LLMClient / ToolRegistry / HumanInterface
 -> CompletePipelineRunner 或 SingleTaskRunner 或 run_skill
 -> StateMachine 构造 ExecutionContext
 -> AgentRunner
 -> Agent / SkillAgent
 -> Tools
 -> workspace artifacts + state.yaml + traces/logs
```

核心模块位置：

- CLI： [researchos/cli.py](../researchos/cli.py)
- 完整流水线 runner： [researchos/cli_runners/complete_pipeline.py](../researchos/cli_runners/complete_pipeline.py)
- 单任务 runner： [researchos/cli_runners/single_task.py](../researchos/cli_runners/single_task.py)
- 状态机： [researchos/orchestration/state_machine.py](../researchos/orchestration/state_machine.py)
- AgentRunner： [researchos/runtime/orchestrator.py](../researchos/runtime/orchestrator.py)
- Agent 抽象： [researchos/runtime/agent.py](../researchos/runtime/agent.py)
- ToolRegistry： [researchos/tools/registry.py](../researchos/tools/registry.py)
- 内置工具注册： [researchos/tools/builtin.py](../researchos/tools/builtin.py)
- Skills： [researchos/skills/](../researchos/skills)
- MCP 适配层： [researchos/tools/mcp_adapter.py](../researchos/tools/mcp_adapter.py)
- Runtime 共享配置： [researchos/runtime/config.py](../researchos/runtime/config.py)

---

## 3. CLI 层：命令入口与调用链

当前可用命令：

- `init-workspace`
- `run`
- `resume`
- `run-task`
- `status`
- `selftest`
- `trace`
- `validate`
- `validate-config`
- `run-skill`
- `list-skills`

### 3.1 `run`

用途：

- 从当前 `state.yaml` 或初始状态开始推进完整 pipeline

链路：

1. 解析 CLI 参数
2. 解析 runtime settings
3. 解析 skill roots
4. 构建 ToolRegistry
5. 构建 LLMClient
6. 构建 StateMachine
7. 启动 `CompletePipelineRunner.run()`

### 3.2 `resume`

用途：

- 恢复一个 `PAUSED` / `WAITING_HUMAN` 的 workspace

区别：

- 和 `run` 走同一条 runner 主链
- 但要求 `state.yaml` 当前处于可恢复状态

### 3.3 `run-task`

用途：

- 只执行一个 task，不推进到下一个节点

特点：

- 会做 I/O 契约校验
- 会做恢复语义注入
- 适合调试单个 stage

### 3.4 `run-skill`

用途：

- 把一个 `SKILL.md` 作为独立能力执行

特点：

- 先 `resolve_skill`
- 再包装成 `SkillAgent`
- 最终仍然由 `AgentRunner` 跑

这意味着 skill 不是 runtime 之外的旁路，而是 runtime 内的一个特殊 agent 入口。

### 3.5 `list-skills`

用途：

- 扫描并展示可用 skill

当前行为：

- 读取 `SKILL.md` frontmatter
- 不执行 skill
- 只做发现和展示

### 3.6 一组真实命令例子

完整跑一个 workspace：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli run \
  --workspace ./workspace/local-test2
```

恢复一个已经暂停的 workspace：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli resume \
  --workspace ./workspace/local-test2
```

只调一个任务：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli run-task T3 \
  --workspace ./workspace/local-test2
```

只调一个 skill：

```bash
cd ResearchOS
PYTHONPATH=. python -m researchos.cli run-skill deepxiv \
  "summarize recent memory papers for llm agents"
```

---

## 4. Workspace 与目录模型

workspace 初始化由 [researchos/runtime/workspace.py](../researchos/runtime/workspace.py) 完成。

当前标准目录包括：

- `user_seeds/`
- `literature/`
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `reviews/`
- `submission/`
- `skills/`
- `_runtime/traces`
- `_runtime/logs`

### 4.1 为什么是 artifact-first

这样设计的好处是：

- 续跑不依赖上下文记忆
- 单任务调试和整链运行共享同一套产物
- 可以中途人工插手修正文件，再继续跑
- 所有阶段都可以独立验证

### 4.2 `project.yaml` 和 `state.yaml`

- `project.yaml`：项目语义配置与研究对象
- `state.yaml`：状态机运行状态

二者职责不同：

- `project.yaml` 更偏“研究内容”
- `state.yaml` 更偏“运行控制”

---

## 5. StateMachine：流程推进内核

状态机由 [researchos/orchestration/state_machine.py](../researchos/orchestration/state_machine.py) 解释 [config/state_machine.yaml](../config/state_machine.yaml)。

### 5.1 它负责什么

- 解析节点
- 解析初始状态
- 构造 `ExecutionContext`
- 在 `AgentResult` 返回后决定下一跳
- 挂起和恢复 gate
- 在需要时解析 `__parse_from_output__`

### 5.2 一个节点包含什么

一个 `TaskNode` 主要包含：

- `task_id`
- `agent` 或 `skill`
- `mode`
- `inputs`
- `outputs`
- `next_on_success`
- `next_on_failure`
- `llm`
- `budget`
- `tools`
- `gate`
- `branches`
- `extra`

### 5.3 `ExecutionContext`

`StateMachine.build_execution_context()` 会为当前 task 构建 `ExecutionContext`，里面有：

- `workspace_dir`
- `project_id`
- `task_id`
- `run_id`
- `inputs`
- `outputs_expected`
- `mode`
- `extra`
- `llm_override`
- `budget_override`
- `tool_policy_override`

这一步非常关键，因为它是“状态机语义”翻译成“AgentRunner 可执行上下文”的桥。

### 5.4 Gate 语义

状态机级 gate 和工具级 ask_human 是不同概念。

状态机级 gate：

- task 成功后，状态机会进入 `WAITING_HUMAN`
- 在 `state.yaml` 里保存 `pending_gate`
- 下次 `resume` 时通过 `present_gate()` + `resolve_pending_gate()` 恢复

典型例子：

- `T7.5`
- `T9`

### 5.5 特殊跳转 `__parse_from_output__`

当前典型用在 `T7.5`：

- task 成功后不写死下一跳
- 而是从 `evaluation/evaluation_decision.md` 中解析 `next_task`

这让 `PI evaluate` 能真正控制后续流程，而不是只写一份报告。

---

## 6. AgentRunner：单个 Agent 的执行循环

`AgentRunner` 位于 [researchos/runtime/orchestrator.py](../researchos/runtime/orchestrator.py)，是 runtime 的核心执行器。

### 6.1 主要职责

- 计算 effective config
- 创建 budget tracker
- 构建 workspace policy
- 构建工具实例
- 发送 system/user messages 给 LLM
- 处理 tool calls
- 处理 finish_task
- 写 trace
- 处理 budget 超限
- 处理 hook

### 6.2 一次 run 的步骤

1. 解析 `EffectiveConfig`
2. 建立 `BudgetTracker`
3. 准备 trace writer
4. 用 `ToolRegistry.build()` 构建本轮 tool map
5. 生成 system prompt 和 initial user message
6. 进入 `while True` 主循环
7. 调用 LLM
8. 解析 assistant message / tool_calls
9. 执行工具
10. 回填 tool messages
11. 如果 tool 调用了 `finish_task`，则做 `validate_outputs`
12. 成功则退出，否则继续修复

### 6.3 空回复与 nudging

如果模型：

- 连续空回复
- 只输出文本不调用工具

runtime 不会立刻死掉，而是会主动发“继续推进或调用 finish_task”的 nudging message。

这能显著降低模型因为偶发空输出而直接失败的概率。

### 6.4 Hook 支持

当前 `AgentRunner` 已支持：

- 同步 pre-hook
- 异步 pre-hook
- 同步/异步 post-hook
- `(ok, err)` 风格 hook 返回值

这是前面 `T9` pre-hook crash 修复后的行为。

### 6.5 Budget Escalation

当超出：

- `max_steps`
- `max_tokens_total`
- `max_wall_seconds`

时，runner 会先尝试触发 budget extension gate，而不是立刻终止。

当前行为：

- 每次触顶都可以弹 gate
- 默认不限制扩限次数
- 会展示当前预算、已有输出、建议扩容量

---

## 7. ToolRegistry 与工具系统

### 7.1 ToolRegistry 的设计原则

`ToolRegistry` 只保存：

- `name -> factory`

而不是：

- `name -> live tool instance`

原因：

- 不同 task 有不同权限
- 不同 workspace 有不同 skill_dir
- human interface 可能不同

因此真正的 tool 实例是在每次 run 开始时通过 `ToolBuildContext` 现构造出来。

### 7.2 ToolBuildContext

当前包含：

- `policy`
- `human`
- `skill_dir`

这三个字段分别提供：

- 读写边界
- 人工审批/交互入口
- skill 运行时的相对脚本工作目录语义

### 7.3 内置工具

内置工具统一由 [researchos/tools/builtin.py](../researchos/tools/builtin.py) 注册。

当前大类包括：

- 文件系统工具：`read_file`、`write_file`、`list_files`、`append_file`
- shell / docker：`bash_run`、`docker_exec`
- 文献检索与处理：`search_papers`、`multi_source_search`、`fetch_paper_metadata`、`deduplicate_papers` 等
- PDF / section 处理：`extract_paper_sections`、`extract_pdf_text`、`fetch_paper_pdf`
- seed 相关：`upload_seed_*`
- 提交相关：`latex_compile`
- gate：`ask_human`
- completion：`finish_task`

### 7.4 WorkspaceAccessPolicy

每个 run 都有独立的 `WorkspaceAccessPolicy`。

它负责：

- 禁止绝对路径
- 禁止路径逃逸 workspace
- 按 `allowed_read_prefixes`
- 按 `allowed_write_prefixes`

这就是为什么“明明文件存在，Agent 还是说目录不可读”的问题通常是 policy 或 tool 使用方式的问题，而不是 Linux 权限本身。

### 7.5 Tool 与 LLM 的关系

LLM 不直接执行 shell 和文件操作。

它的流程是：

1. 输出 `tool_calls`
2. Runner 执行对应 Python tool
3. tool 返回 `ToolResult`
4. Runner 再把结果回填给模型

所以“agent 调用 tool”的本质是：

- 模型做调用决策
- runtime 执行代码

---

## 8. LLMClient 与模型路由

`LLMClient` 从 [config/model_routing.yaml](../config/model_routing.yaml) 读取：

- `api_keys`
- `endpoints`
- `profiles`
- `truncation`

### 8.1 选择链

模型选择大致优先级是：

1. task 级 override
2. agent 默认 llm 设置
3. profile + tier 解析
4. `default_profile`

### 8.2 Endpoint 与 Provider 解耦

例如：

- `siliconflow` endpoint 底层 provider 仍可以是 `openai`
- `openrouter_main` endpoint 则是 `openrouter`

这样 agent 不需要关心 provider 细节，只需要决定：

- 用哪个 profile
- 或直接指定 model + endpoint

### 8.3 Fallback

当前 fallback 已支持：

- 同一个 profile 下 `primary -> fallback -> fallback...`
- 每轮先尝试所有候选
- 然后再进入下一轮重试

这修复了早期“第一个候选会被重试 10 次才轮到 fallback”的问题。

### 8.4 Context Truncation

当上下文接近模型 `max_context` 时：

- runtime 会按完整 tool-call group 截断旧消息
- 并尽量保留 system prompt 和最近若干轮

这样可以避免 provider 侧因为上下文过长直接报错。

### 8.5 Selftest

`selftest` 和很多命令的 startup selftest 都依赖 `LLMClient.selftest()`。

它的作用是：

- 在真正运行前探测 endpoint 连通性
- 早发现 API key / base URL / provider 问题

---

## 9. Skills Runtime

ResearchOS 当前 skill runtime 是一套独立但复用 `AgentRunner` 的扩展机制。

### 9.1 Skill 的文件格式

一个 skill 至少需要：

- `skills/<skill-name>/SKILL.md`

`SKILL.md` 使用 frontmatter 描述：

- `name`
- `description`
- `tools` / `allowed_tools`
- `tier` / `model_tier`
- `max_steps`
- `use-jinja`

### 9.2 Skill 的发现

由 [researchos/skills/loader.py](../researchos/skills/loader.py) 完成：

- `discover_skills`
- `discover_skills_from_roots`
- `resolve_skill`
- `register_skill_tools`

当前规则：

- 只扫描 immediate 子目录
- 必须有 `SKILL.md`
- skill 名不能重复

### 9.3 Tool Alias 翻译

`SkillAgent` 不要求 skill 作者直接写 runtime 原生工具名。

它会通过 [researchos/skills/tool_aliases.py](../researchos/skills/tool_aliases.py) 把：

- `Read -> read_file`
- `Write -> write_file`
- `Edit -> write_file`
- `Bash(*) -> bash_run`
- `Glob(*) -> glob_files`
- `Grep(*) -> grep_search`
- `WebFetch -> web_fetch`

等写法翻译成 runtime 工具。

### 9.4 `run-skill`

`run-skill` 的执行过程是：

1. `resolve_skill`
2. 创建 `SkillAgent`
3. 构造 `ExecutionContext`
4. 把 `user_request` 放到 `ctx.extra`
5. 交给 `AgentRunner.run()`

### 9.5 当前仓库内置 skill 的真实状态

当前仓库有：

- `deepxiv`
- `paper-compile`
- `paper-write`

实际可用性：

- `paper-compile`：可直接用
- `deepxiv`：可直接用
- `paper-write`：部分降级可用

原因：

- `paper-write` 声明了 `Agent`、`WebSearch`、`mcp__codex__...`
- 当前 runtime 不一定注册了这些能力
- 未注册时会被跳过并附带 warning

### 9.6 Skill 自带工具

如果某个 skill 目录下有：

- `tools/*.py`

并导出了 `TOOL` 实例，`register_skill_tools()` 会把它们注册进 ToolRegistry。

当前仓库的 3 个内置 paper skill 没有自带本地 `tools/`。

---

## 10. MCP Runtime

MCP 在当前架构里是“可插拔外部工具总线”，不是 runtime 的必需核心。

### 10.1 配置文件

- 模板： [config/mcp.example.yaml](../config/mcp.example.yaml)
- 当前实例： [config/mcp.yaml](../config/mcp.yaml)

### 10.2 适配方式

MCP 不是把某个 SDK 硬绑进 runtime，而是通过一个最小协议适配：

- client 有 `name`
- client 有 `call_tool()`
- 若支持发现，还有 `list_tools()`

这样做的好处：

- 测试时可用 fake client
- runtime 不耦合特定 MCP SDK

### 10.3 注册路径

CLI 只有在你显式传入 `--mcp-connector` 并成功加载 connector 时，才会把远端 MCP tools 注册进 registry。

换言之：

- `mcp.yaml` 只是 server 描述
- 真正接线还需要 connector

---

## 11. Resume、Recovery 与 Trace

### 11.1 通用恢复快照

由 [researchos/runtime/task_recovery.py](../researchos/runtime/task_recovery.py) 生成：

- `_runtime/resume/<task>_resume_state.json`

内容包括：

- 当前是恢复运行还是全新运行
- 已有输出
- 缺失输出
- 恢复原因

### 11.2 专项恢复

当前有专项恢复器：

- `T3`： [researchos/runtime/t3_recovery.py](../researchos/runtime/t3_recovery.py)
- `T5/T7`： [researchos/runtime/experiment_recovery.py](../researchos/runtime/experiment_recovery.py)

### 11.3 Trace

如果 `debug.enable_trace` 为真：

- 每次 run 都会生成 `_runtime/traces/<run_id>.jsonl`

trace 记录：

- run start
- messages
- llm responses
- tool results
- finish / error

`researchos trace --workspace ... --run-id ...` 可以直接查看。

### 11.4 Logging

日志目录默认是：

- `_runtime/logs/`

输出格式由：

- `logging.level`
- `logging.json`

控制。

---

## 12. Validation 与 Quality Gates

ResearchOS 当前的校验不是一层，而是多层：

### 12.1 静态配置校验

- `researchos validate-config`

检查：

- 状态机结构
- runtime config
- routing 关键字段

### 12.2 前置条件校验

单任务模式下在开跑前检查：

- 当前 task 所需输入 artifact 是否存在

### 12.3 Agent 自身输出校验

每个 agent 有自己的 `validate_outputs()`：

- T3 看 note / table / bib
- T7 看 results_summary / ablations / seed ensemble
- T9 看 `main.pdf` + `编译状态: 成功`

这些校验不是装饰性的。

实际行为是：

- agent 调用 `finish_task`
- runtime 不会立刻宣布成功
- 先执行 `validate_outputs()`
- 校验失败时，runtime 会把失败原因回灌给 agent
- agent 继续修补，直到成功或达到重试/预算上限

这也是为什么你会在日志里看到：

- `Agent 请求完成任务，开始校验输出`
- `输出校验失败`
- 然后 agent 继续工作

### 12.4 Runtime artifact 校验

runner 或 single-task 在 agent 成功后还会用 task contract 做一轮额外验证。

这可以拦住“agent 说自己完成了，但关键文件没写齐”的情况。

### 12.5 Handoff 校验

除了单个 task 的输出校验，ResearchOS 还隐含地做上游到下游的 handoff 校验。

典型例子：

- `T3 -> T3.5`
  - 必须有 `paper_notes/`、`comparison_table.csv`
- `T4 -> T4.5`
  - 必须有 `hypotheses.md`、`exp_plan.yaml`、`idea_rationales.json`
- `T5 -> T6`
  - 必须有 pilot 结果
- `T7 -> T7.5`
  - 必须有 `experiments/results_summary.json`
- `T7.5 -> T8`
  - 必须先产出 `evaluation/evaluation_decision.md`

这些规则一部分来自：

- `config/state_machine.yaml`
- `researchos/orchestration/task_io_contract.py`
- 各 agent 的 `validate_outputs()`

### 12.6 Integrity Gate / Human Gate

ResearchOS 不只有“文件存在性校验”，还有两类更高层的 gate：

1. 机器 gate
   - 例如 `T7.5` 解析 `evaluation_decision.md`
   - 例如 budget extension gate
   - 例如 `T6` / `T7.5` 这类决策型节点

2. human gate
   - 例如 `T7.5 -> T8` 之间的人类确认
   - 当系统需要问“是否继续写论文 / 是否扩预算 / 是否按 PI 建议推进”时触发

### 12.7 Failure Mode 校验

实验相关 agent 还会带研究质量层面的检查，而不是只看文件有没有写出来。

典型包括：

- seed ensemble 是否达标
- ablation 数量是否足够
- 结果是否明显缺乏多样性
- silent failure 风险
  - `nan`
  - `inf`
  - OOM
  - 不收敛

因此，ResearchOS 的 `validate_outputs()` 实际上经常承担了“质量门控”的作用，而不只是“格式检查”。

---

## 13. 如何注册一个新 Tool

最标准的做法：

1. 在 `researchos/tools/` 下新建实现
2. 继承 `Tool`
3. 定义 `name`、`description`、`parameters_schema`
4. 实现 `execute()`
5. 在 [researchos/tools/builtin.py](../researchos/tools/builtin.py) 里注册
6. 在对应 agent 的 `tool_names` 中启用

如果是 skill 私有工具：

1. 放到 `skills/<skill>/tools/*.py`
2. 导出 `TOOL`
3. 运行时自动发现注册

---

## 14. 如何注册一个新 Agent

标准路径：

1. 在 `researchos/agents/` 下实现 agent 类
2. 继承 `Agent`
3. 用 `build_agent_spec()` 构造 spec
4. 实现：
   - `system_prompt()`
   - `initial_user_message()`
   - `validate_outputs()`
5. 在 [researchos/agents/registry.py](../researchos/agents/registry.py) 中注册
6. 在 [config/agent_params.yaml](../config/agent_params.yaml) 配默认参数
7. 在 [config/state_machine.yaml](../config/state_machine.yaml) 配节点
8. 在 [researchos/orchestration/task_io_contract.py](../researchos/orchestration/task_io_contract.py) 配 I/O

如果这个 agent 需要新 gate、special next、resume 语义，也通常要同步改：

- `config/gates.yaml`
- `state_machine.py`
- `task_recovery.py` 或专项 recovery

---

## 15. 如何注册一个新 Skill

### 15.1 最小 skill

创建目录：

```text
skills/my-skill/
└── SKILL.md
```

最小 frontmatter：

```markdown
---
name: my-skill
description: demo
tools:
  - Read
  - Write
---
```

### 15.2 可选自带工具

```text
skills/my-skill/
├── SKILL.md
└── tools/
    └── my_tool.py
```

其中 `my_tool.py` 导出 `TOOL`。

### 15.3 运行方式

```bash
researchos run-skill my-skill "your request" --workspace ./workspace/demo
```

---

## 16. 当前 runtime 的强项与边界

### 16.1 强项

- 状态机清晰
- 单任务与整链都可跑
- Artifact-first 恢复语义已较成熟
- 多阶段 research pipeline 已接通
- skills runtime 已接通
- docker/latex/experiment 场景都已有基础设施

### 16.2 边界

- 并不是所有 config 字段都已经完全接线
- MCP 需要 connector，不能只写 `mcp.yaml`
- 部分 skill 仍会因高级工具未注册而降级
- 真实 LLM 运行仍受外部 provider 质量影响

---

## 17. 建议联读

- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/docker.md](./docker.md)
- [docs/config.md](./config.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
