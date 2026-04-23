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

### 已实现的 Agent

- `HelloAgent`：调试用 agent
- `PIAgent`（T1/T7.5）：项目初始化与评估 agent，支持 init 和 evaluate 两种模式
- `ScoutAgent`（T2）：文献检索 agent，支持多源论文搜索
- `ReaderAgent`（T3/T3.5）：深度阅读与文献综合 agent，支持 read 和 synthesize 两种模式
- `IdeationAgent`（T4）：假设生成 agent，通过两轮 Gate 交互生成研究假设和实验计划
- `NoveltyAuditorAgent`（T4.5）：新颖性审计 agent，评估研究假设的新颖性和可行性
- `ExperimenterAgent`（T5/T7）：实验执行 agent，T5 为试点实验（pilot），T7 为完整实验（full）
- `NoveltyAgent`（T6）：新颖性最终验证 agent，基于 pilot 结果验证新颖性
- `WriterAgent`（T8-WRITE/T8-DRAFT/T8-REVISE-*）：论文写作 agent，支持大纲、初稿、自查、修订多种 phase
- `ReviewerAgent`（T8-REVIEW-*）：论文审稿 agent，支持多轮审稿
- `SubmissionAgent`（T9）：投稿准备 agent，处理模板迁移、匿名化检查、编译验证

详见 [researchos/agents/registry.py](./researchos/agents/registry.py)。

### LLM 路由支持

支持多 provider：SiliconFlow、OpenRouter、OpenAI、Anthropic

- 配置文件：`config/model_routing.yaml`
- 默认使用 SiliconFlow 的 DeepSeek
- 可通过环境变量配置不同 provider 的 API key

### 鲁棒性增强功能

根据 `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md`，已实现以下鲁棒性增强项：

1. **T4 Hypothesis Pre-mortem（§4.1）**：在 Gate1 和 Gate2 之间添加反常识验证
2. **Runtime Budget Drift Warning（§7.1）**：预算漂移预警（70%/90% 阈值）
3. **T1 Ethical Screening（§8.1）**：敏感方向拦截
4. **T1 External Resources Management（§10.1-10.2）**：外部资源管理
5. **迭代死锁检测（Phase 2.3）**：防止无限循环

所有功能均有对应的单元测试，见 `tests/unit/test_robustness_enhancements.py`。

## 5 分钟快速开始

以下命令默认都在 **仓库根目录** 执行。

### 路径 A：Docker 模式（推荐）

**适用场景**：生产部署、论文复现、快速体验

Docker 模式使用**统一镜像**，一个镜像同时支持：
- T5/T7 实验执行
- T9 TeX 编译与 PDF 生成

```bash
# 1. 构建镜像
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh

# 2. 设置环境变量
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.example.com"

# 3. 初始化 workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# 4. 运行任务
bash infra/docker/run.sh run-task T1 --workspace /workspace --topic "your research topic"
```

**详细文档**：[Docker 使用指南](docs/docker-usage.md)

### 路径 B：宿主机模式（开发调试）

**适用场景**：开发调试、修改代码

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
pip install -e '.[dev]'
python scripts/debug_hello_agent.py --mock --workspace ./workspace/demo_hello
```

### 路径 C：初始化一个标准 workspace

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "runtime smoke test"
```

## 环境要求

- Linux 或兼容的类 Unix 环境
- Python 3.11
- Conda
- 可选：Docker（用于实验执行和 TeX 编译）
- 可选：真实 LLM provider 的 API key
- 可选：`pdfplumber`，用于 `extract_paper_sections`

本仓库不再依赖任何作者本机绝对路径。共享环境以仓库内这些文件为准：

- [environment.yml](./environment.yml)
- [requirements.txt](./requirements.txt)
- [requirements-dev.txt](./requirements-dev.txt)
- [requirements-llm.txt](./requirements-llm.txt)
- [pyproject.toml](./pyproject.toml)

## Docker 使用

### 统一镜像概念

ResearchOS 使用**统一镜像** `researchos/system:latest`：

- **镜像大小**：9.08GB
- **包含内容**：
  - Python 3.11
  - ML 依赖（PyTorch, CUDA 12.4）
  - LaTeX 环境（用于 T9 论文编译）
  - MCP 支持
- **用途**：一个镜像同时支持 T5/T7 实验执行和 T9 TeX 编译

### 构建镜像

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

### 运行命令

```bash
# 基本运行
bash infra/docker/run.sh [command]

# 示例：初始化 workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# 示例：运行 T1
bash infra/docker/run.sh run-task T1 --workspace /workspace --topic "your topic"

# 示例：完整 pipeline
bash infra/docker/run.sh run --workspace /workspace
```

### 文件保存机制

Docker 容器内的 `/workspace` 目录会挂载到宿主机，确保运行结果持久化：

- 所有 agent 产出文件保存在 workspace 目录
- trace 和日志文件也保存在 workspace 的 `_runtime/` 子目录
- 容器退出后文件不会丢失

## 配置说明

### 运行前最重要的配置文件

| 文件 | 作用 | 当前是否真实生效 |
| --- | --- | --- |
| [config/model_routing.yaml](./config/model_routing.yaml) | 定义 endpoint、profile、model routing、context/truncation、rate limit | 是 |
| [config/state_machine.yaml](./config/state_machine.yaml) | 定义 workflow 节点、agent、输入输出、成功/失败转移 | 是 |
| [config/gates.yaml](./config/gates.yaml) | 定义 gate 选项和展示内容 | 是 |
| [config/runtime.yaml](./config/runtime.yaml) | runtime 共享默认值 | 是 |
| [config/mcp.example.yaml](./config/mcp.example.yaml) | MCP server 配置模板 | 模板文件 |

### 环境变量与密钥

- 复制 [`.env.example`](./.env.example) 为 `.env`，然后补上你实际要用的变量。
- 具体变量名以 [config/model_routing.yaml](./config/model_routing.yaml) 中引用的环境变量为准。
- `search_papers` / `fetch_paper_metadata` 可使用 `S2_API_KEY`。

### 能力矩阵

| 能力 | 额外依赖/服务 | 相关文件 | 当前状态 |
| --- | --- | --- | --- |
| Mock 调试 | 无 | [scripts/debug_hello_agent.py](./scripts/debug_hello_agent.py) | 可直接使用 |
| 真实 LLM | `litellm` + `.env` | [config/model_routing.yaml](./config/model_routing.yaml) | 可使用 |
| Docker 执行 | Docker daemon | [researchos/tools/docker_exec.py](./researchos/tools/docker_exec.py) | runtime 已支持 |
| PDF section 提取 | `pdfplumber` | [researchos/tools/paper_processing.py](./researchos/tools/paper_processing.py) | 已支持 |
| Semantic Scholar 搜索 | `httpx` + 可选 `S2_API_KEY` | [researchos/tools/search_papers.py](./researchos/tools/search_papers.py) | 已支持 |
| MCP | MCP server + connector | [researchos/tools/mcp_adapter.py](./researchos/tools/mcp_adapter.py) | 接口层已支持 |

## 仓库结构

```text
ResearchOS/
|-- config/                 # 配置：state machine / gates / model routing / runtime / MCP 模板
|-- docs/                   # 详细文档
|-- infra/                  # 基础设施
|   |-- docker/             # Docker 构建和运行脚本
|-- researchos/
|   |-- agents/             # 正式 agent 类与 registry
|   |-- cli_runners/        # 完整 pipeline / 单 task 两种运行模式
|   |-- orchestration/      # state machine / gate presenter / task I/O 契约
|   |-- prompts/           # Agent prompt 模板
|   |-- runtime/            # AgentRunner、LLMClient、trace、logger、workspace helper 等
|   |-- schemas/            # state schema、artifact validator
|   |-- skills/             # skill loader / skill runner / tool aliases
|   |-- testing/            # MockLLMClient、MockHumanInterface、fixtures
|   `-- tools/              # builtin tool、MCP adapter、paper processing 等
|-- scripts/                # 调试脚本和开发辅助脚本
|-- tests/
|   |-- unit/               # 单元测试（336 个）
|   |-- integration/        # 集成测试（已移除，为空目录）
|   |-- e2e/               # 端到端测试（已移除，为空目录）
|   `-- real/              # 真实测试（113 个）
|-- workspace/              # 默认 workspace 目录
|-- environment.yml
|-- requirements.txt
|-- requirements-dev.txt
|-- requirements-llm.txt
`-- pyproject.toml
```

重要说明：

- 根目录 `logs/` 不是运行时唯一日志源。
- 真正的 runtime trace 和运行日志都在 `workspace/<runtime_dir>/` 下，默认是 `workspace/_runtime/`。
- 推荐所有运行命令都先 `cd` 到仓库根目录执行。

## 运行方式

安装完成后，以下两种调用方式都可以：

- `researchos ...`
- `python -m researchos.cli ...`

### 1. 初始化标准 workspace

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "test topic"
```

### 2. 完整 pipeline 模式

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
researchos run --workspace ./workspace/demo
```

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

当前真正注册的 task：`HELLO`、`T1`、`T1.5`、`T2`、`T3`、`T3.5`、`T4`、`T4.5`、`T5`、`T6`、`T7`、`T8-WRITE`、`T8-DRAFT`、`T8-REVIEW-1`、`T8-REVISE-1`、`T8-REVIEW-2`、`T8-REVISE-2`、`T9`。

### 5. 各 Agent 使用示例

#### T1 PI Agent

```bash
researchos run-task T1 \
  --workspace ./workspace/my-research \
  --topic "discrete diffusion language models"
```

#### T2 Scout Agent

```bash
researchos run-task T2 --workspace ./workspace/my-research
```

#### T3/T3.5 Reader Agent

```bash
researchos run-task T3 --workspace ./workspace/my-research
researchos run-task T3.5 --workspace ./workspace/my-research
```

#### T4 Ideation Agent

```bash
researchos run-task T4 --workspace ./workspace/my-research
```

注意：T4 需要人工交互（两轮 Gate）。

#### T5/T7 Experimenter Agent

```bash
# T5: 试点实验
researchos run-task T5 --workspace ./workspace/my-research

# T7: 完整实验
researchos run-task T7 --workspace ./workspace/my-research
```

注意：需要 Docker 支持，可能需要 GPU 资源。

#### T9 Submission Agent

```bash
researchos run-task T9 --workspace ./workspace/my-research
```

注意：需要 Docker 支持（用于 TeX 编译）。

### 6. 查看状态、trace、artifact 校验

```bash
researchos status --workspace ./workspace/demo
researchos trace hello_debug_run --workspace ./workspace/demo
researchos validate --workspace ./workspace/demo --task HELLO
researchos validate-config
```

## 测试方式

### 全量测试

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
python -m pytest -q
```

### 定向测试

```bash
# 单元测试（336 个）
python -m pytest -q tests/unit/

# 真实测试（113 个）
python -m pytest -q tests/real/

# 特定文件
python -m pytest -q tests/unit/test_cli_runners.py
python -m pytest -q tests/unit/test_mcp_adapter.py
```

### 字节码编译检查

```bash
python -m compileall researchos scripts
```

### 测试覆盖重点

**单元测试 (tests/unit/)**：
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
- T1-T9 agents 单元测试
- 边界和失败路径测试
- 鲁棒性增强功能测试

**真实测试 (tests/real/)**：
- 各 Agent 的 `validate_outputs` 输出契约验证
- 多 Agent 数据流集成
- pipeline 端到端流程
- Agent 输出完整性检查

### 测试文档

详细的测试指南和评测报告：

- [Docker 测试报告](docs/docker-test-report.md) - Docker 环境验证结果

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

## 常见问题

### 为什么 README 里总是先要求 `cd ResearchOS`？

因为 CLI 默认配置路径如 `config/state_machine.yaml`、`config/model_routing.yaml`、默认 `./workspace` 都依赖当前工作目录。

### 为什么某些 task 现在跑不起来？

runtime 已经为 T1-T9 预留了 task I/O 契约，请确认相关 agent 已实现且已注册到 `registry.py`。

### 为什么 `resume` 不会恢复历史对话文本？

因为当前 runtime 的 resume 语义是 artifact-first，恢复依据是 workspace 中已经产出的文件与状态。

### 为什么我在别的目录运行 `researchos` 会找不到配置？

因为默认配置路径是相对路径。解决方法是：
- 先 `cd` 到仓库根目录
- 或显式传入 `--state-machine`、`--gates`、`--model-routing`

### 为什么明明显示 `(researchos)`，实际跑的还是 base Python？

请先检查：

```bash
which python
which researchos
python -c "import sys; print(sys.executable)"
```

如果它们仍然指向 base，请优先使用：

```bash
conda run -n researchos python -m researchos.cli ...
```

### Docker 运行失败怎么办？

1. 确认 Docker daemon 正在运行：`docker info`
2. 检查镜像是否已构建：`docker images | grep researchos`
3. 如果需要 GPU 支持，确认已安装 nvidia-docker

### 如何切换 LLM provider？

编辑 `config/model_routing.yaml` 或设置环境变量：

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://api.example.com"
```

## 已知限制

- 默认 [config/state_machine.yaml](./config/state_machine.yaml) 的 `initial_state: HELLO`，需手动改为 `T1` 启用完整流程。
- Docker 实验（T5/T7）和 TeX 编译（T9）需要 GPU 环境支持。
- MCP runtime 接口已具备，但仓库未默认提供真实 `config/mcp.yaml` 与 connector。
- `extract_paper_sections` 已接入 builtin tools，但依赖 `pdfplumber`，默认环境未强装。
- 当前还没有正式的 `new-agent` scaffold 命令；新增 agent 需要按本文档手工修改相关文件。

## 测试统计

**当前测试总数：449 个**

| 测试类型 | 目录 | 数量 |
|---------|------|------|
| 单元测试 | `tests/unit/` | 336 个 |
| 真实测试 | `tests/real/` | 113 个 |
| 集成测试 | `tests/integration/` | 已移除（原为空） |
| 端到端测试 | `tests/e2e/` | 已移除（原为空） |

所有测试均通过，覆盖以下方面：

- AgentRunner 主循环
- Tool 执行与并行 tool call
- context truncation
- workspace policy
- schema validator
- gate / resume / iteration
- CLI `run-task` / `trace` / `validate`
- MCP adapter
- PDF section 提取
- skill runtime
- T1-T9 agents 单元测试和真实测试
- 边界和失败路径
- 错误层级和重试行为
- 鲁棒性增强功能

## 配置系统与优先级

### 改不同层面的行为，应该去哪里

| 你要改的东西 | 主要文件 |
| --- | --- |
| 任务顺序、节点属性、成功/失败转移 | [config/state_machine.yaml](./config/state_machine.yaml) |
| 人工 gate 选项与展示内容 | [config/gates.yaml](./config/gates.yaml) |
| 模型/端点/profile/rate limit | [config/model_routing.yaml](./config/model_routing.yaml) |
| CLI 默认 workspace / runtime_dir / log-level | [config/runtime.yaml](./config/runtime.yaml) |
| task 输入输出契约 | [researchos/orchestration/task_io_contract.py](./researchos/orchestration/task_io_contract.py) |
| task 到 agent 的绑定 | [researchos/agents/registry.py](./researchos/agents/registry.py) |
| builtin tool 注册 | [researchos/tools/builtin.py](./researchos/tools/builtin.py) |
| MCP 配置模板 | [config/mcp.example.yaml](./config/mcp.example.yaml) |
| artifact validator | [researchos/schemas/validator.py](./researchos/schemas/validator.py) |

## 下一步建议

- 启用完整的 T1-T9 工作流（将 `initial_state` 从 `HELLO` 改为 `T1`）
- 使用真实 LLM 对关键 agent（T4/T5/T8）进行端到端测试
- 为 MCP 提供仓库内置 connector 或官方推荐 connector 包
- 继续完善 `config/runtime.yaml` 作为 runtime 配置中心
