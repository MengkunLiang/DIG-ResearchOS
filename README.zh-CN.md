# ResearchOS Runtime

## 项目简介

ResearchOS 是面向 AI / ML / Data Mining 科研流程的多智能体系统。本仓库当前优先实现其 runtime：负责消息协议、LLM 路由、工具执行、workspace 权限、trace、预算控制、状态机推进与最小 CLI，而不是先实现全部研究 agent 业务。

当前版本已经提供：
- 可运行的 runtime 主循环 `AgentRunner`
- 可扩展的工具注册与 workspace 权限系统
- `HelloAgent` 调试链路
- 最小状态机与 `state.yaml` 持久化
- Mock LLM / Human 接口与测试基础设施

## 环境要求

- Linux
- Python 3.11
- Conda 环境：`/home/liangmengkun/.conda/envs/researchos`

建议使用仓库约定环境：

```bash
conda activate /home/liangmengkun/.conda/envs/researchos
```

## 安装步骤

安装基础 runtime 与测试依赖：

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m pip install -e '.[dev]'
```

如果需要真实 LLM provider 支持，再安装可选依赖：

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m pip install -e '.[llm]'
```

## 配置说明

当前最小配置文件如下：

- `config/model_routing.yaml`
  - 定义 `endpoints + profiles + truncation`
  - 真实 LLM 运行前需要让其中引用的环境变量存在
- `config/state_machine.yaml`
  - 定义最小状态机节点、agent、输出和成功/失败转移
- `config/runtime.yaml`
  - 放置运行目录、日志、人机接口等基础默认值

运行时 workspace 中会生成：

- `state.yaml`
- `_runtime/traces/*.jsonl`
- `_runtime/logs/`

## 运行方式

1. Mock 调试 `HelloAgent`

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python scripts/debug_hello_agent.py --mock --workspace /home/liangmengkun/tmp/researchos_hello
```

2. 查看 CLI 帮助

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli --help
```

3. 使用最小 CLI

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli run --workspace ./workspace
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli status --workspace ./workspace
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli trace <run_id> --workspace ./workspace
```

说明：
- 当前 `run/resume` 走真实 `LLMClient`，因此需要先安装 `.[llm]` 并准备模型 API key。
- 当前最稳定的验收入口仍是 `scripts/debug_hello_agent.py --mock`。

## 测试方式

运行全部测试：

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m pytest -q
```

当前测试覆盖：
- budget 与消息协议契约
- workspace policy 与文件工具
- AgentRunner 基本闭环
- 多 tool call 顺序与 trace
- context truncation
- HelloAgent mock 集成链路

## 目录结构

```text
ResearchOS/
├── config/                    # runtime / routing / state machine 配置
├── logs/                      # 任务过程日志与决策记录
├── researchos/
│   ├── agents/                # 当前包含 HelloAgent
│   ├── orchestration/         # 最小状态机
│   ├── runtime/               # runtime 主干
│   ├── schemas/               # state schema
│   ├── testing/               # mock 与 pytest fixtures
│   └── tools/                 # 内置工具与权限系统
├── scripts/                   # 调试入口
└── tests/                     # unit / integration
```

## 常见问题

### 为什么 `python` 指向了 base Anaconda，而不是 `researchos` 环境？

当前终端环境变量可能显示为 `researchos`，但实际 shell 解析到的解释器未必正确。建议始终显式使用：

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python
```

### 为什么安装 `.[dev]` 不会安装 `litellm`？

因为当前仓库优先保证 Mock runtime 和测试可运行，真实 LLM 依赖拆分到了 `.[llm]`，避免在无 Rust 编译器环境里被 `tiktoken` 构建阻塞。

### `researchos.cli run` 为什么可能失败？

如果没有安装 `.[llm]`，或者 `config/model_routing.yaml` 所需环境变量未设置，`LLMClient` 会在启动或调用时失败。

## 已知限制

- 当前只实现了 `HelloAgent` 和面向后续扩展的 runtime 主干，没有实现 T1-T9 全部研究 agent。
- `CLI run/resume` 目前依赖真实 LLM，没有内置 mock 模式。
- MCP、skill loader、bash/web/grep/glob 等扩展工具尚未实现。
- 状态机当前是最小版本，只覆盖线性推进和失败终止，不包含完整 human gate/iteration/resume 分支恢复。
- 还没有引入 lint/type-check 工具链；本轮主要通过 `pytest` 与 `compileall` 验证。

## runtime 架构概览

- `researchos/runtime`
  - `agent.py`: Agent 规范、执行上下文、override 合并、结果模型
  - `message.py`: OpenAI 兼容消息协议与 tool call/message 契约
  - `llm_client.py`: `Endpoint + Profile + ModelBinding` 路由抽象
  - `orchestrator.py`: `AgentRunner` 主循环，负责 budget、tool、finish 校验、truncation、trace
  - `trace.py` / `logger.py`: 结构化追踪与日志支持
- `researchos/tools`
  - `workspace_policy.py`: 路径解析与前缀访问控制
  - `registry.py`: 工厂型工具注册表
  - `filesystem.py` / `finish_task.py` / `ask_human.py` / `echo.py`: 当前内置工具
- `researchos/orchestration`
  - `state_machine.py`: 最小状态机解释器、`state.yaml` 读写与推进

## 已实现模块

- runtime: errors, message, budget, prompts, logger, trace, retry, agent, llm_client, orchestrator
- tools: workspace policy, tool base, registry, builtin tools
- orchestration: 最小 state machine
- testing: MockLLMClient, MockHumanInterface, pytest fixtures
- agent/debug: HelloAgent, `scripts/debug_hello_agent.py`
- CLI: `run`, `resume`, `status`, `trace`

## 如何运行与测试

- 最小调试：
  - `python scripts/debug_hello_agent.py --mock --workspace /path/to/ws`
- 全量测试：
  - `python -m pytest -q`
- CLI 帮助：
  - `python -m researchos.cli --help`

## 下一步计划

- 为 CLI 增加 `--mock` 运行模式，打通状态机级的无 API 调试
- 实现更多内置工具：`bash_run`、`grep_search`、`glob_files`、`web_fetch`
- 完善 `state_machine.yaml` 节点 override、resume、iteration 和 human gate
- 引入更多 schema 与真实 T-stage agent 骨架
- 补充类型检查与更细的边界测试

