# ResearchOS Runtime

## 项目简介

ResearchOS 是面向 AI / ML / Data Mining 科研流程的多智能体系统。本仓库当前优先实现其 runtime：负责消息协议、LLM 路由、工具执行、workspace 权限、trace、预算控制、状态机推进与最小 CLI，而不是先实现全部研究 agent 业务。

当前版本已经提供：
- 可运行的 runtime 主循环 `AgentRunner`
- 可扩展的工具注册与 workspace 权限系统
- `HelloAgent` 调试链路
- 带 `gate / resume / iteration_count` 语义的 `state.yaml` 持久化
- `SkillAgent` 与外部 skill 适配层
- skill 自带 `tools/*.py` 自动发现与注册
- `bash_run / grep_search / glob_files / web_fetch` 内置扩展工具
- `search_papers / fetch_paper_metadata / docker_exec / latex_compile` 业务关键工具
- JSON Schema 校验基础设施
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
  - 定义 `endpoints + profiles + truncation + 可选 rate_limit`
  - 真实 LLM 运行前需要让其中引用的环境变量存在
- `config/state_machine.yaml`
  - 定义状态机节点、agent、输出和成功/失败转移
- `config/gates.yaml`
  - 定义 FSM gate 的选项和展示内容
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
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli run-skill research-lit --workspace ./workspace
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli status --workspace ./workspace
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli selftest
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli validate --workspace ./workspace --task HELLO
/home/liangmengkun/.conda/envs/researchos/bin/python -m researchos.cli trace <run_id> --workspace ./workspace
```

说明：
- 当前 `run/resume` 走真实 `LLMClient`，因此需要先安装 `.[llm]` 并准备模型 API key。
- `selftest` 会对默认 profile 涉及的 endpoint 做最小连通性检查。
- 当前 CLI 已支持 `PAUSED` 恢复和 `WAITING_HUMAN` gate 继续执行。
- `run-skill` 已支持按名称或路径运行 skill，并自动加载 skill 自带工具。
- `validate` 会优先调用 task 专用 checker，没有专用 checker 时退回到 state machine 声明输出校验。
- 当前最稳定的验收入口仍是 `scripts/debug_hello_agent.py --mock`。

## 测试方式

运行全部测试：

```bash
/home/liangmengkun/.conda/envs/researchos/bin/python -m pytest -q
```

当前测试覆盖：
- budget 与消息协议契约
- workspace policy 与文件工具
- `bash_run / grep_search / glob_files / web_fetch`
- `search_papers / docker_exec / latex_compile`
- AgentRunner 基本闭环
- 多 tool call 顺序与 trace
- context truncation
- skill loader / skill runner
- skill tool 自动发现
- schema validator
- CLI validate
- gate presenter
- state machine 的 gate / resume / iteration 语义
- LLM `.env` 加载、rate limiter 接入与 selftest
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
│   ├── skills/                # skill 适配层
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

### 为什么 `resume` 后不会自动恢复上一次对话历史？

当前 runtime 的 resume 语义是基于 workspace artifact 恢复，而不是 replay 先前 LLM 对话。`StateMachine` 会在 `ctx.extra` 中注入 `is_resume/resumed_from`，agent 需要自己根据 artifact 决定如何续跑。

### skill 已经能跑了吗？

可以。当前既支持 `run_skill(...)` Python API，也支持 `researchos run-skill <skill-name>` CLI 入口；如果 skill 目录下存在 `tools/*.py`，runtime 也会自动发现并注册里面导出的 `TOOL` 实例。

## 已知限制

- 当前只实现了 `HelloAgent` 和面向后续扩展的 runtime 主干，没有实现 T1-T9 全部研究 agent。
- CLI 还没有补 single-task runner、complete-pipeline runner 等更高层入口。
- MCP adapter、`extract_paper_sections` 等剩余 runtime 扩展尚未实现。
- artifact 级 task checker 目前只内置了 `HELLO` / 基础 `T1` 示例，其余 task 仍主要依赖 state machine 声明输出校验。
- `gate presentation` 已拆分为独立 `gate_presenter.py`，但目前只支持 `literal / from_state / from_file / from_dir` 这几类基础规则。
- 还没有引入 lint/type-check 工具链；本轮主要通过 `pytest` 与 `compileall` 验证。

## runtime 架构概览

- `researchos/runtime`
  - `agent.py`: Agent 规范、执行上下文、override 合并、结果模型
  - `message.py`: OpenAI 兼容消息协议与 tool call/message 契约
  - `llm_client.py`: `Endpoint + Profile + ModelBinding` 路由抽象
  - `rate_limiter.py`: per-endpoint token bucket，避免因本地速率打满而错误触发 fallback
  - `orchestrator.py`: `AgentRunner` 主循环，负责 budget、tool、finish 校验、truncation、trace
  - `trace.py` / `logger.py`: 结构化追踪与日志支持
- `researchos/tools`
  - `workspace_policy.py`: 路径解析与前缀访问控制
  - `registry.py`: 工厂型工具注册表
  - `filesystem.py` / `finish_task.py` / `ask_human.py` / `echo.py`: 基础内置工具
  - `bash_run.py` / `grep_search.py` / `glob_files.py` / `web_fetch.py`: runtime 通用扩展工具
  - `search_papers.py` / `docker_exec.py` / `latex_compile.py`: 面向后续 research agent 的关键 runtime tool
- `researchos/orchestration`
  - `state_machine.py`: 状态机解释器、`state.yaml` 读写、gate/resume/iteration 推进
  - `gate_presenter.py`: gate 展示内容的声明式拼装
- `researchos/skills`
  - `loader.py` / `tool_aliases.py` / `agent.py` / `runner.py`: skill 加载、工具别名翻译、tool 自动发现与轻量运行入口
- `researchos/schemas`
  - `state.py` / `validator.py`: runtime 状态模型和 JSON Schema 校验基础设施

## 已实现模块

- runtime: errors, message, budget, prompts, logger, trace, retry, agent, llm_client, rate_limiter, orchestrator
- tools: workspace policy, tool base, registry, filesystem, ask_human, finish_task, echo, bash_run, grep_search, glob_files, web_fetch, search_papers, docker_exec, latex_compile
- orchestration: 带 gate / resume / iteration 语义的 state machine、独立 gate presenter
- schemas: state model, validator, json schema 目录约定、validate CLI/script
- skills: skill loader, tool alias translation, SkillAgent, `run_skill` API、skill tool 自动发现
- testing: MockLLMClient, MockHumanInterface, pytest fixtures, runtime 扩展工具/状态机/skill/validator/CLI 单测
- agent/debug: HelloAgent, `scripts/debug_hello_agent.py`
- CLI: `run`, `resume`, `run-skill`, `status`, `trace`, `selftest`, `validate`

## 如何运行与测试

- 最小调试：
  - `python scripts/debug_hello_agent.py --mock --workspace /path/to/ws`
- 全量测试：
  - `python -m pytest -q`
- 字节码编译检查：
  - `python -m compileall researchos`
- CLI 帮助：
  - `python -m researchos.cli --help`
- LLM 端点自检：
  - `python -m researchos.cli selftest`
- artifact 校验：
  - `python -m researchos.cli validate --workspace /path/to/ws --task HELLO`
  - `python scripts/validate_artifact.py /path/to/ws --task HELLO`

## 下一步计划

- 为 CLI 增加 `--mock` 运行模式，打通状态机级的无 API 调试
- 暴露 single-task runner、complete-pipeline runner 等更高层入口
- 实现 MCP adapter、`extract_paper_sections`
- 补齐真实 T-stage 所需 JSON Schema 与 per-task artifact checker
- 补充类型检查与更细的边界测试
