# ResearchOS Runtime

## 项目简介

ResearchOS 当前优先交付的是 **runtime 基础设施**，不是已经完成的 T1-T9 研究代理产品。

这个仓库已经具备的核心能力包括：

- `AgentRunner` 主循环：负责消息协议、LLM 调用、tool 执行、finish 校验、budget、trace。
- `StateMachine` 编排层：负责 `state.yaml`、gate、resume、iteration、task override。
- `ToolRegistry` 与 workspace 权限：负责工具工厂注册、按 task 构建工具实例、读写边界控制。
- `cli_runners`：同时支持完整 pipeline 模式和单 task 调试模式。
- `skills` runtime：支持 `SKILL.md`、skill 工具自动发现、独立 skill 执行。
- runtime 关键工具：`search_papers`、`fetch_paper_metadata`、`docker_exec`、`latex_compile`、`extract_paper_sections`、`MCPTool` 适配层。
- 测试基础设施：`MockLLMClient`、`MockHumanInterface`、runtime 测试替身、pytest fixtures。

同时必须明确当前现状：

- 当前默认注册的正式 agent 只有 `HelloAgent`，见 [researchos/agents/registry.py](./researchos/agents/registry.py)。
- 当前默认 [config/state_machine.yaml](./config/state_machine.yaml) 仍是 demo 级 workflow，不是完整 T1-T9 项目编排。
- runtime 已经为后续 agent 开发准备好了接口、抽象和测试底座，但 T1-T9 正式 agent 业务实现还没有全部落地。

这份 README 的目标不是罗列模块，而是帮助你从 0 开始把仓库跑起来、调起来、改起来。

## 5 分钟快速开始

以下命令默认都在 **仓库根目录** 执行。

### 路径 A：只验证 runtime 最小 mock 调试

```bash
cd ResearchOS
conda env create -f environment.yml || conda env update -f environment.yml --prune
conda activate researchos
pip install -e '.[dev]'
python scripts/debug_hello_agent.py --mock --workspace ./workspace/demo_hello
```

### 路径 B：初始化一个标准 workspace

```bash
cd ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "runtime smoke test"
```

如果你没有安装 console script，也可以用：

```bash
python -m researchos.cli init-workspace --workspace ./workspace/demo --project-id demo-project
```

### 路径 C：查看 CLI 能力与当前 runtime 状态

```bash
cd ResearchOS
conda activate researchos
researchos --help
researchos run-task --help
researchos selftest
```

## 环境要求

- Linux 或兼容的类 Unix 环境
- Python 3.11
- Conda
- 可选：Docker
- 可选：真实 LLM provider 的 API key
- 可选：`pdfplumber`，用于 `extract_paper_sections`

本仓库不再依赖任何作者本机绝对路径。共享环境以仓库内这些文件为准：

- [environment.yml](./environment.yml)
- [requirements.txt](./requirements.txt)
- [requirements-dev.txt](./requirements-dev.txt)
- [requirements-llm.txt](./requirements-llm.txt)
- [pyproject.toml](./pyproject.toml)

## 安装步骤

### 方式 A：推荐，Conda + editable install

```bash
cd ResearchOS
conda env create -f environment.yml || conda env update -f environment.yml --prune
conda activate researchos
pip install -e '.[dev]'
```

安装后建议先检查当前 shell 真正命中的解释器：

```bash
which python
which researchos
python -c "import sys; print(sys.executable)"
```

理想情况下，它们都应该指向当前 `researchos` 环境，而不是 `base`。
如果提示符已经显示 `(researchos)`，但这里仍然指向 base，说明你的 shell PATH 顺序有问题。
这种情况下建议先用更稳妥的方式执行：

```bash
conda run -n researchos python -m researchos.cli --help
conda run -n researchos researchos --help
```

如果你需要真实 LLM：

```bash
pip install -e '.[llm]'
```

### 方式 B：requirements 文件驱动

```bash
cd ResearchOS
conda env create -f environment.yml || conda env update -f environment.yml --prune
conda activate researchos
pip install -r requirements-dev.txt
pip install -e . --no-deps
```

如果你需要真实 LLM：

```bash
pip install -r requirements-llm.txt
```

### 可选依赖

- PDF section 提取：

```bash
pip install pdfplumber
```

说明：

- `environment.yml` 只负责共享的基础 Python 解释器与 pip 环境。
- Python 依赖版本以 `pyproject.toml` 和 `requirements*.txt` 为准。
- 安装 `-e .` 的目的是获得 `researchos` console script，以及让本地代码修改能立即生效。

## 配置说明

### 运行前最重要的配置文件

| 文件 | 作用 | 当前是否真实生效 |
| --- | --- | --- |
| [config/model_routing.yaml](./config/model_routing.yaml) | 定义 endpoint、profile、model routing、context/truncation、rate limit | 是 |
| [config/state_machine.yaml](./config/state_machine.yaml) | 定义 workflow 节点、agent、输入输出、成功/失败转移 | 是 |
| [config/gates.yaml](./config/gates.yaml) | 定义 gate 选项和展示内容 | 是 |
| [config/runtime.yaml](./config/runtime.yaml) | runtime 共享默认值 | 是，目前用于 `workspace.default_root`、`workspace.runtime_dir`、`logging.level/json`、`human_interface.backend` |
| [config/mcp.example.yaml](./config/mcp.example.yaml) | MCP server 配置模板 | 模板文件，不会自动启用 |

### 环境变量与密钥

- 复制 [`.env.example`](./.env.example) 为 `.env`，然后补上你实际要用的变量。
- 具体变量名以 [config/model_routing.yaml](./config/model_routing.yaml) 中引用的环境变量为准。
- `search_papers` / `fetch_paper_metadata` 可使用 `S2_API_KEY`。
- 如果未来要启用 MCP GitHub / Semantic Scholar / arXiv，也需要对应 token 或 server 环境变量。

### 能力矩阵

| 能力 | 额外依赖/服务 | 相关文件 | 当前状态 |
| --- | --- | --- | --- |
| Mock 调试 | 无 | [scripts/debug_hello_agent.py](./scripts/debug_hello_agent.py) | 可直接使用 |
| 真实 LLM | `litellm` + `.env` | [config/model_routing.yaml](./config/model_routing.yaml) | 可使用 |
| Docker 执行 | Docker daemon | [researchos/tools/docker_exec.py](./researchos/tools/docker_exec.py) | runtime 已支持 |
| PDF section 提取 | `pdfplumber` | [researchos/tools/paper_processing.py](./researchos/tools/paper_processing.py) | runtime 已支持，默认需额外安装依赖 |
| Semantic Scholar 搜索 | `httpx` + 可选 `S2_API_KEY` | [researchos/tools/search_papers.py](./researchos/tools/search_papers.py) | runtime 已支持 |
| MCP | MCP server + connector | [researchos/tools/mcp_adapter.py](./researchos/tools/mcp_adapter.py) | runtime 已支持接口层，默认未开箱接通 |

## 仓库结构

```text
ResearchOS/
|-- config/                 # 配置：state machine / gates / model routing / runtime / MCP 模板
|-- researchos/
|   |-- agents/             # 正式 agent 类与 registry
|   |-- cli_runners/        # 完整 pipeline / 单 task 两种运行模式
|   |-- orchestration/      # state machine / gate presenter / task I/O 契约
|   |-- runtime/            # AgentRunner、LLMClient、trace、logger、workspace helper 等
|   |-- schemas/            # state schema、artifact validator
|   |-- skills/             # skill loader / skill runner / tool aliases
|   |-- testing/            # MockLLMClient、MockHumanInterface、fixtures
|   `-- tools/              # builtin tool、MCP adapter、paper processing 等
|-- scripts/                # 调试脚本和开发辅助脚本
|-- tests/
|   |-- integration/
|   `-- unit/
|-- logs/                   # 仓库级日志或手工调试残留，不等于 workspace 运行日志
|-- environment.yml
|-- requirements.txt
|-- requirements-dev.txt
|-- requirements-llm.txt
|-- README.md
`-- README.zh-CN.md
```

重要说明：

- 根目录 `logs/` 不是运行时唯一日志源。
- 真正的 runtime trace 和运行日志都在 `workspace/<runtime_dir>/` 下，默认是 `workspace/_runtime/`。
- 推荐所有运行命令都先 `cd` 到仓库根目录执行，因为默认配置路径如 `config/...` 和默认 workspace 路径都依赖当前工作目录。

## Runtime 架构概览

### 数据流

1. CLI 读取配置，初始化 workspace，注册 builtin / skill / 可选 MCP tools。
2. `cli_runners` 选择运行模式：
   - `CompletePipelineRunner`：完整状态机模式
   - `SingleTaskRunner`：单 task 模式
3. `StateMachine` 生成 `ExecutionContext`，决定当前 task 的 LLM override、budget override、tool policy override。
4. `AgentRunner` 驱动一次 agent run：
   - 渲染 prompt
   - 调用 `LLMClient`
   - 执行 tool
   - 写 trace
   - 预算控制
   - finish 后做输出校验
5. `validator` 负责 task 级 artifact 检查。
6. 结果回写 `state.yaml` 和 `<runtime_dir>/traces/*.jsonl`。

### 模块职责

- [researchos/runtime](./researchos/runtime)
  - `agent.py`：AgentSpec、ExecutionContext、AgentResult、override 合并
  - `orchestrator.py`：`AgentRunner`
  - `llm_client.py`：`Endpoint + Profile + ModelBinding`
  - `message.py`：统一消息协议
  - `trace.py`：trace 写入与 human-readable 渲染
  - `logger.py`：console/file logging
  - `workspace.py`：标准 workspace 树初始化
  - `cli_ui.py`：CLI 启动动画与摘要
- [researchos/orchestration](./researchos/orchestration)
  - `state_machine.py`：状态推进、gate、resume、iteration
  - `gate_presenter.py`：声明式 gate 展示拼装
  - `task_io_contract.py`：task 输入输出契约
- [researchos/tools](./researchos/tools)
  - `base.py` / `registry.py` / `workspace_policy.py`
  - `builtin.py`
  - `mcp_adapter.py`
  - `paper_processing.py`
- [researchos/skills](./researchos/skills)
  - `loader.py` / `agent.py` / `runner.py`
- [researchos/schemas](./researchos/schemas)
  - `state.py` / `validator.py`

## Workspace 是什么

`workspace` 是 ResearchOS 的 **共享状态目录**。

它不是临时缓存目录，而是 runtime、agent、artifact、trace、日志共同读写的一份项目状态。

### 标准 workspace 树

```text
workspace/
|-- project.yaml
|-- state.yaml
|-- user_seeds/
|-- literature/
|   |-- pdfs/
|   `-- paper_notes/
|-- ideation/
|-- pilot/
|   `-- pilot_code/
|-- novelty/
|-- experiments/
|   |-- runs/
|   `-- configs/
|-- evaluation/
|-- drafts/
|-- reviews/
|   `-- review_rounds/
|-- submission/
|   `-- bundle/
|-- skills/
`-- _runtime/              # 默认 runtime 目录名，可在 config/runtime.yaml 修改
    |-- traces/
    `-- logs/
```

### 哪些文件是 runtime 管的

- `state.yaml`
- `<runtime_dir>/traces/*.jsonl`
- `<runtime_dir>/logs/*.log`

### 哪些内容是 agent 或 tool 产出的

- `literature/`
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `reviews/`
- `submission/`

### `run-task --from` 会做什么

`run-task --from <other_workspace>` 会根据 [researchos/orchestration/task_io_contract.py](./researchos/orchestration/task_io_contract.py) 中当前 task 的 `inputs` 定义，从另一个 workspace 复制前置 artifact。

它不会盲目复制整个 workspace。

## 运行方式

安装完成后，以下两种调用方式都可以：

- `researchos ...`
- `python -m researchos.cli ...`

如果你还没有安装 editable package，请先用第二种。

### 1. 初始化标准 workspace

```bash
cd ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "test topic"
```

共享参数如 `--workspace`、`--project-id`、`--state-machine` 现在可以放在子命令前，也可以放在子命令后：

```bash
researchos --workspace ./workspace/demo init-workspace --project-id demo-project
researchos init-workspace --workspace ./workspace/demo --project-id demo-project
```

说明：

- `DIG` 不是命令，而是 CLI 启动时显示的 ASCII banner。
- 如果你传了 `--no-banner`，就不会显示这个启动动画。
- 如果当前输出不是 TTY，runtime 会退化为打印静态 DIG banner，而不是逐帧动画。

可选参数：

- `--no-project-file`：只建目录，不写 `project.yaml`
- `--force-project-file`：覆盖已有 `project.yaml`

### 2. 完整 pipeline 模式

```bash
cd ResearchOS
conda activate researchos
researchos run --workspace ./workspace/demo
```

说明：

- 当前 runtime 会显示 `DIG` 启动动画；如不需要，可加 `--no-banner`。
- 当前 `run` 默认会做启动自检；如你明确要跳过，可加 `--skip-startup-selftest`。
- 当前仓库默认 workflow 仍是 demo 级，主要用于验证 runtime 主链，不代表 T1-T9 已完全可跑。

### 3. 恢复已暂停的 pipeline

```bash
researchos resume --workspace ./workspace/demo
```

### 4. 单 task 调试模式

```bash
researchos run-task HELLO --workspace ./workspace/demo
```

从另一个 workspace 复制前置 artifact：

```bash
researchos run-task T4 --workspace ./workspace/t4_debug --from ./workspace/upstream
```

覆盖 profile：

```bash
researchos run-task HELLO --workspace ./workspace/demo --profile audit
```

重要说明：

- 当前真正注册的 task 只有 `HELLO`。
- `task_io_contract.py` 已经为 T1-T9 预留了契约，但这不代表对应 agent 已经实现。

### 5. 独立运行一个 skill

```bash
researchos run-skill my-skill --workspace ./workspace/demo
```

附加 skill 根目录：

```bash
researchos --skills-root ./skills --skills-root ./external_skills run-skill my-skill --workspace ./workspace/demo
```

### 6. 查看状态、trace、artifact 校验

```bash
researchos status --workspace ./workspace/demo
researchos trace hello_debug_run --workspace ./workspace/demo
researchos trace hello_debug_run --workspace ./workspace/demo --raw
researchos validate --workspace ./workspace/demo --task HELLO
researchos validate-config
```

### 7. LLM endpoint 自检

```bash
researchos selftest
```

## 测试方式

### 全量测试

```bash
cd ResearchOS
conda activate researchos
python -m pytest -q
```

### 定向测试

```bash
python -m pytest -q tests/unit/test_cli_runners.py
python -m pytest -q tests/unit/test_mcp_adapter.py
python -m pytest -q tests/unit/test_paper_processing_tool.py
python -m pytest -q tests/integration/test_debug_hello_agent_mock.py
```

### 字节码编译检查

```bash
python -m compileall researchos scripts
```

### 最小 mock 调试

```bash
python scripts/debug_hello_agent.py --mock --workspace ./workspace/demo_hello
```

### 当前测试覆盖重点

- AgentRunner 主循环
- Tool 执行与并行 tool call
- context truncation
- workspace policy
- schema validator
- gate / resume / iteration
- `CompletePipelineRunner` / `SingleTaskRunner`
- CLI `run-task` / `trace` / `validate`
- MCP adapter
- PDF section 提取
- skill runtime
- Hello mock 集成链路

## 调试与排障

建议按这个顺序排查：

1. `researchos selftest`
2. `python scripts/debug_hello_agent.py --mock --workspace ...`
3. `researchos run-task HELLO --workspace ...`
4. `researchos trace <run_id> --workspace ...`
5. `researchos validate --workspace ... --task ...`
6. `researchos validate-config`
7. `python -m pytest -q`
8. `python -m compileall researchos scripts`

排障时最重要的几个位置：

- `workspace/state.yaml`
- `workspace/<runtime_dir>/traces/*.jsonl`
- `workspace/<runtime_dir>/logs/researchos.log`
- `project.yaml`

## 配置系统与优先级

### 改不同层面的行为，应该去哪里

| 你要改的东西 | 主要文件 |
| --- | --- |
| 任务顺序、节点属性、成功/失败转移 | [config/state_machine.yaml](./config/state_machine.yaml) |
| 人工 gate 选项与展示内容 | [config/gates.yaml](./config/gates.yaml) |
| 模型/端点/profile/rate limit | [config/model_routing.yaml](./config/model_routing.yaml) |
| CLI 默认 workspace / runtime_dir / log-level / 日志格式 / human backend | [config/runtime.yaml](./config/runtime.yaml) |
| task 输入输出契约 | [researchos/orchestration/task_io_contract.py](./researchos/orchestration/task_io_contract.py) |
| task 到 agent 的绑定 | [researchos/agents/registry.py](./researchos/agents/registry.py) |
| builtin tool 注册 | [researchos/tools/builtin.py](./researchos/tools/builtin.py) |
| MCP 配置模板 | [config/mcp.example.yaml](./config/mcp.example.yaml) |
| artifact validator | [researchos/schemas/validator.py](./researchos/schemas/validator.py) |

### 生效优先级

大致优先级是：

1. CLI 参数
2. FSM 节点 override
3. `AgentSpec`
4. `config/runtime.yaml` 中的 CLI 默认值

说明：

- `config/runtime.yaml` 目前只是部分生效，不是完整配置中心。
- `state.yaml` 是当前 runtime 的持久化状态文件。开发文档早期片段中若出现 `state.json`，请以当前实现和 Runtime Spec 的 `state.yaml` 为准。

## 如何新增一个 Agent

新增一个正式 agent，最少要改这些地方：

1. 新建 `researchos/agents/<name>.py`
2. 在该文件里定义 `AgentSpec`
3. 新建对应 prompt 模板，例如 `researchos/prompts/<name>.j2`
4. 在 [researchos/agents/registry.py](./researchos/agents/registry.py) 注册：
   - `AGENT_REGISTRY`
   - 如需要单 task 调试，再补 `TASK_TO_AGENT_MAP`
5. 在 [config/state_machine.yaml](./config/state_machine.yaml) 给出节点定义
6. 在 [researchos/orchestration/task_io_contract.py](./researchos/orchestration/task_io_contract.py) 补输入输出契约
7. 在 [researchos/schemas/validator.py](./researchos/schemas/validator.py) 补 task checker
8. 补单测与集成测试

推荐最小开发顺序：

1. 先让 agent 在 mock LLM 下跑通
2. 再补 validator
3. 再接入 state machine
4. 最后再接真实 LLM / 真实外部工具

## 如何新增一个 Skill

### skill 目录结构

```text
skills/my-skill/
|-- SKILL.md
|-- tools/
|-- templates/
|-- scripts/
`-- examples/
```

### 需要关注的实现文件

- [researchos/skills/loader.py](./researchos/skills/loader.py)
- [researchos/skills/agent.py](./researchos/skills/agent.py)
- [researchos/skills/runner.py](./researchos/skills/runner.py)
- [researchos/skills/tool_aliases.py](./researchos/skills/tool_aliases.py)

### skill 搜索根规则

- 默认尝试：
  - 当前工作目录下的 `skills/`
  - workspace 下的 `skills/`
- 也可通过 `--skills-root` 追加。

## 如何新增一个 Tool / MCP / Validator

### 新增 builtin tool

1. 在 `researchos/tools/` 新建实现文件
2. 在 [researchos/tools/builtin.py](./researchos/tools/builtin.py) 注册
3. 根据需要设计 `WorkspaceAccessPolicy` 前缀
4. 补单测

### 新增 MCP tool

当前 runtime 已经提供：

- [researchos/tools/mcp_adapter.py](./researchos/tools/mcp_adapter.py)
- [config/mcp.example.yaml](./config/mcp.example.yaml)
- CLI 参数：
  - `--mcp-config`
  - `--mcp-connector`

但当前仓库 **没有默认附带真实 connector**。这意味着：

- 你可以复用 runtime 的注册逻辑
- 但需要自己提供一个 connector 函数，供 CLI 动态导入

典型调用形式：

```bash
researchos --mcp-config ./config/mcp.yaml --mcp-connector your_package.mcp:connect run-task HELLO --workspace ./workspace/demo
```

### 新增 validator

在 [researchos/schemas/validator.py](./researchos/schemas/validator.py)：

1. 写 checker 函数
2. 在 `register_builtin_task_checkers()` 中注册
3. 补测试

## 如何调整整体 workflow

如果你要改完整流程，而不是单个 agent，通常至少要同时检查这些位置：

| 变更目标 | 需要检查的文件 |
| --- | --- |
| 新增一个 task | `agents/registry.py`、`config/state_machine.yaml`、`task_io_contract.py`、`validator.py` |
| 改任务顺序 | `config/state_machine.yaml` |
| 改人工分支逻辑 | `config/gates.yaml`、`state_machine.py`、必要时 `gate_presenter.py` |
| 改模型路由 | `config/model_routing.yaml` |
| 改 task 的输入输出 | `task_io_contract.py`、agent `validate_outputs()`、`validator.py` |
| 改 skill 搜索策略 | CLI `--skills-root`、`skills/loader.py` |
| 改启动默认值 | `config/runtime.yaml`、`cli.py` |

## Gate、Resume、Iteration 的运行语义

当前 runtime 的语义是：

- `WAITING_HUMAN`
  - task 运行成功后进入 gate，等待人工选择
- `PAUSED`
  - 运行中被中断，后续可 `resume`
- `resume`
  - 恢复的是 **workspace 语义**，不是 replay 完整 LLM 历史消息
- `task_context -> ctx.extra`
  - gate 选项里的 `extra` 会进入下一个 task 的 `ctx.extra`
- `iteration_count`
  - 状态机回流到已完成节点时递增

如果你要理解这些细节，直接看：

- [researchos/orchestration/state_machine.py](./researchos/orchestration/state_machine.py)
- [researchos/schemas/state.py](./researchos/schemas/state.py)

## 常见问题

### 为什么 README 里总是先要求 `cd ResearchOS`？

因为 CLI 默认配置路径如 `config/state_machine.yaml`、`config/model_routing.yaml`、默认 `./workspace` 都依赖当前工作目录。

### 为什么 `run-task T4` 现在大概率跑不起来？

因为 runtime 已经为 T1-T9 预留了 task I/O 契约，但当前真正注册到 [researchos/agents/registry.py](./researchos/agents/registry.py) 的正式 task 只有 `HELLO`。

### 为什么 `resume` 不会恢复历史对话文本？

因为当前 runtime 的 resume 语义是 artifact-first，恢复依据是 workspace 中已经产出的文件与状态，而不是把上次所有 LLM 消息完整 replay 回模型。

### 为什么我改了 `config/runtime.yaml`，有些项没生效？

当前已经接入的主要字段包括：`workspace.default_root`、`workspace.runtime_dir`、`logging.level`、`logging.json`、`human_interface.backend`。如果你改的是其他未来字段，当前版本可能还不会生效。

### 为什么 `trace` 默认输出不是原始 JSONL？

因为 CLI 现在默认走 human-readable 模式，便于直接调试。若你需要原始事件流，请加 `--raw`。

### 为什么我在别的目录运行 `researchos` 会找不到配置？

因为默认配置路径是相对路径。解决方法是：

- 先 `cd` 到仓库根目录
- 或显式传入 `--state-machine`、`--gates`、`--model-routing`

### 为什么明明显示 `(researchos)`，实际跑的还是 base Python？

这是 shell 初始化或 PATH 顺序问题。请先检查：

```bash
which python
which researchos
python -c "import sys; print(sys.executable)"
```

如果它们仍然指向 base，请优先使用：

```bash
conda run -n researchos python -m researchos.cli ...
conda run -n researchos researchos ...
```

当前版本的 CLI 也会在启动时主动检查这类错配，并打印 `env-warning` 到 stderr。

### skill 会从哪里自动发现？

默认是：

- 当前工作目录的 `skills/`
- workspace 下的 `skills/`

也可以用 `--skills-root` 扩展。

### `state.yaml` 和开发文档里的 `state.json` 到底以谁为准？

以当前实现和 Runtime Dev Spec 为准：**现在用的是 `state.yaml`**。

## 已知限制

- 当前只内置 `HelloAgent`，T1-T9 正式 agent 还没有全部落地。
- 默认 [config/state_machine.yaml](./config/state_machine.yaml) 仍是 demo workflow，不是完整 ResearchOS pipeline。
- `task_io_contract.py` 里虽然已经有 T1-T9 契约，但这不代表所有 task 现在都可运行。
- MCP runtime 接口已具备，但仓库未默认提供真实 `config/mcp.yaml` 与 connector。
- `extract_paper_sections` 已接入 builtin tools，但依赖 `pdfplumber`，默认环境未强装。
- task 级 validator 目前仍以 `HELLO` / 基础 `T1` 为主，其余 T-stage 还需要继续深化。
- `config/runtime.yaml` 目前只是部分生效，不是完整共享配置中心。
- 当前还没有正式的 `new-agent` scaffold 命令；新增 agent 需要按本文档手工修改相关文件。

## 下一步建议

- 把默认 workflow 从 Hello demo 扩展到真实 T1-T9
- 为 MCP 提供仓库内置 connector 或官方推荐 connector 包
- 为 T2-T9 补齐更严格的 artifact checker
- 为更多 task 补充 mock workspace fixture 和端到端测试
- 继续把 `config/runtime.yaml` 接成真正的 runtime 配置中心
