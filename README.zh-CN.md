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

- 当前已实现的正式 agent：
  - `HelloAgent`：调试用agent
  - `PIAgent`（T1/T7.5）：项目初始化与评估agent，支持init和evaluate两种模式
  - `ScoutAgent`（T2）：文献检索agent，支持多源论文搜索
  - `ReaderAgent`（T3/T3.5）：深度阅读与文献综合agent，支持read和synthesize两种模式
  - `IdeationAgent`（T4）：假设生成agent，通过两轮Gate交互生成研究假设和实验计划
  - `ExperimenterAgent`（T6）：实验执行agent，执行实验计划并收集结果
  - 见 [researchos/agents/registry.py](./researchos/agents/registry.py)
- 当前默认 [config/state_machine.yaml](./config/state_machine.yaml) 仍是 demo 级 workflow，不是完整 T1-T9 项目编排。
- runtime 已经为后续 agent 开发准备好了接口、抽象和测试底座，T3-T9 正式 agent 业务实现还在开发中。

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

- 当前真正注册的 task：`HELLO`、`T1`、`T7.5`、`T2`、`T3`、`T3.5`、`T4`。
- `task_io_contract.py` 已经为 T1-T9 预留了契约，T1-T4已实现，T5-T9正在开发中。

#### T1 PI Agent 使用示例

T1 PI Agent 是项目初始化agent，通过三轮对话引导用户明确研究方向：

```bash
# 初始化新项目
researchos run-task T1 \
  --workspace ./workspace/my-research \
  --topic "discrete diffusion language models"
```

执行后会产出：
- `project.yaml`：项目配置文件
- `user_seeds/seed_papers.jsonl`：种子论文
- `user_seeds/seed_ideas.md`：初步想法
- `user_seeds/seed_constraints.md`：约束清单

T7.5 模式用于评估实验结果：

```bash
# 评估实验结果（需要先有实验数据）
researchos run-task T7.5 \
  --workspace ./workspace/my-research \
  --mode evaluate
```

详细文档见 [docs/agents/T1_PI_AGENT.md](./docs/agents/T1_PI_AGENT.md)。

#### T2 Scout Agent 使用示例

T2 Scout Agent 是文献检索agent，支持多源论文搜索：

```bash
# 文献检索（需要先运行T1）
researchos run-task T2 \
  --workspace ./workspace/my-research
```

执行后会产出：
- `literature/papers_raw.jsonl`：原始论文（30-80篇）
- `literature/papers_dedup.jsonl`：去重后论文
- `literature/search_log.md`：搜索日志
- `literature/missing_areas.md`：缺口分析

#### T3/T3.5 Reader Agent 使用示例

T3 Reader Agent 负责深度阅读和文献综合：

```bash
# T3: 深度阅读（需要先运行T2）
researchos run-task T3 \
  --workspace ./workspace/my-research

# T3.5: 文献综合（需要先运行T3）
researchos run-task T3.5 \
  --workspace ./workspace/my-research
```

T3执行后会产出：
- `literature/paper_notes/*.md`：每篇论文的结构化笔记（11项checklist）
- `literature/comparison_table.csv`：论文对比表
- `literature/related_work.bib`：BibTeX引用库

T3.5执行后会产出：
- `literature/synthesis.md`：文献综述（5个必需章节）

详细文档见 [docs/T3_T4_IMPLEMENTATION_REPORT.md](./docs/T3_T4_IMPLEMENTATION_REPORT.md)。

#### T4 Ideation Agent 使用示例

T4 Ideation Agent 负责假设生成，通过两轮Gate交互确认：

```bash
# T4: 假设生成（需要先运行T3.5）
researchos run-task T4 \
  --workspace ./workspace/my-research
```

执行后会产出：
- `ideation/hypotheses.md`：研究假设（带H1/H2等anchor）
- `ideation/exp_plan.yaml`：实验计划（符合schema）
- `ideation/risks.md`：Top 3风险分析

注意：T4需要人工交互（两轮Gate），无法完全自动化。

详细文档见 [docs/T3_T4_IMPLEMENTATION_REPORT.md](./docs/T3_T4_IMPLEMENTATION_REPORT.md)。

#### T6 Experimenter Agent 使用示例

T6 Experimenter Agent 负责实验执行和结果收集：

```bash
# T6: 实验执行（需要先运行T4）
researchos run-task T6 \
  --workspace ./workspace/my-research
```

执行后会产出：
- `experiments/results_summary.json`：实验结果汇总
- `experiments/iteration_log.md`：实验迭代日志
- `experiments/runs/{run_id}/`：每个实验的详细结果

注意：T6需要较长时间运行（最多4小时），且可能需要GPU资源。

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
- T1-T6 agents 单元测试

## 从0开始调试T1/T2 Agent

本节提供完整的T1（项目初始化）和T2（文献检索）agent调试指南，帮助用户从零开始验证runtime和agents功能。

### 前置准备

确保已完成环境安装：

```bash
cd ResearchOS
conda activate researchos
pip install -e '.[dev]'
```

验证安装：

```bash
python -c "from researchos.agents.pi import PIAgent; from researchos.agents.scout import ScoutAgent; print('Agents imported successfully')"
```

### T1 PIAgent 调试（Mock模式）

T1 PIAgent负责项目初始化，通过三轮对话引导用户明确研究方向。

#### 步骤1：运行mock调试脚本

```bash
python scripts/debug_t1_agent.py --mock --workspace ./workspace/debug_t1
```

#### 步骤2：检查输出

成功运行后应该看到：

```
============================================================
T1 PIAgent 调试结果:
============================================================
成功: True
停止原因: finished
步数: 6
Token输入: 650
Token输出: 240
成本: $0.0000

产出文件:
  project: /path/to/workspace/debug_t1/project.yaml ✓
  seed_papers: /path/to/workspace/debug_t1/user_seeds/seed_papers.jsonl ✓
  seed_ideas: /path/to/workspace/debug_t1/user_seeds/seed_ideas.md ✓
  seed_constraints: /path/to/workspace/debug_t1/user_seeds/seed_constraints.md ✓

Trace文件: /path/to/workspace/debug_t1/_runtime/traces/t1_debug_run.jsonl
============================================================
```

#### 步骤3：验证产出文件

检查project.yaml是否符合schema：

```bash
cat workspace/debug_t1/project.yaml
```

应该包含必需字段：`project_id`、`research_direction`、`created_at`、`keywords`。

验证schema：

```bash
python -c "
from researchos.schemas.validator import validate_record, load_schema
import yaml
data = yaml.safe_load(open('workspace/debug_t1/project.yaml'))
ok, err = validate_record(data, 'project')
print(f'Schema validation: {ok}')
if err:
    print(f'Error: {err}')
"
```

#### 步骤4：查看trace日志

```bash
researchos trace t1_debug_run --workspace ./workspace/debug_t1
```

或查看原始JSONL：

```bash
researchos trace t1_debug_run --workspace ./workspace/debug_t1 --raw
```

### T2 ScoutAgent 调试（Mock模式）

T2 ScoutAgent负责文献检索和去重，产出论文池。

#### 步骤1：运行mock调试脚本

```bash
python scripts/debug_t2_agent.py --mock --workspace ./workspace/debug_t2
```

注意：脚本会自动创建前置输入文件（project.yaml）。

#### 步骤2：检查输出

成功运行后应该看到：

```
============================================================
T2 ScoutAgent 调试结果:
============================================================
成功: True
停止原因: finished
步数: 6
Token输入: 1100
Token输出: 410
成本: $0.0000

产出文件:
  papers_raw: /path/to/workspace/debug_t2/literature/papers_raw.jsonl ✓
  papers_dedup: /path/to/workspace/debug_t2/literature/papers_dedup.jsonl ✓
  search_log: /path/to/workspace/debug_t2/literature/search_log.md ✓
  missing_areas: /path/to/workspace/debug_t2/literature/missing_areas.md ✓

Trace文件: /path/to/workspace/debug_t2/_runtime/traces/t2_debug_run.jsonl
============================================================
```

#### 步骤3：验证产出文件

检查论文数量：

```bash
wc -l workspace/debug_t2/literature/papers_dedup.jsonl
```

应该在15-120篇之间（mock模式产出20篇）。

查看论文格式：

```bash
head -2 workspace/debug_t2/literature/papers_dedup.jsonl | python -m json.tool
```

验证schema：

```bash
python -c "
from researchos.schemas.validator import validate_task_artifacts
ok, err = validate_task_artifacts('workspace/debug_t2', 'T2')
print(f'Task artifacts validation: {ok}')
if err:
    print(f'Error: {err}')
"
```

#### 步骤4：查看检索日志

```bash
cat workspace/debug_t2/literature/search_log.md
cat workspace/debug_t2/literature/missing_areas.md
```

### 使用真实LLM调试

如果要使用真实LLM而非mock，需要：

#### 步骤1：配置环境变量

复制`.env.example`为`.env`，填入API密钥：

```bash
cp .env.example .env
# 编辑.env，添加：
# ANTHROPIC_API_KEY=your_key_here
# 或其他provider的密钥
```

#### 步骤2：安装LLM依赖

```bash
pip install -e '.[llm]'
```

#### 步骤3：使用CLI运行（不使用mock脚本）

T1真实运行：

```bash
researchos run-task T1 \
  --workspace ./workspace/real_t1 \
  --topic "discrete diffusion language models"
```

T2真实运行（需要先有T1产出）：

```bash
# 先运行T1
researchos run-task T1 --workspace ./workspace/real_t2 --topic "your research topic"

# 再运行T2
researchos run-task T2 --workspace ./workspace/real_t2
```

或使用`--from`复制前置输入：

```bash
researchos run-task T2 \
  --workspace ./workspace/real_t2 \
  --from ./workspace/real_t1
```

### 常见调试问题

#### 问题1：Schema验证失败

**症状**：`project.yaml不符合schema: Validation error: 'research_direction' is a required property`

**原因**：project.yaml缺少必需字段或字段名错误。

**解决**：检查project.yaml是否包含：
- `project_id`（字符串）
- `research_direction`（字符串，至少10字符）
- `created_at`（ISO 8601格式时间戳）

#### 问题2：Mock LLM响应不足

**症状**：`错误: LLM failed: No mock responses left`

**原因**：Agent需要的轮次超过mock LLM预设的响应数量。

**解决**：在debug脚本的`build_mock_llm_for_*`函数中添加更多`FakeRawCompletion`响应。

#### 问题3：工具未注册

**症状**：`ValueError: Tool 'xxx' not registered`

**原因**：Agent spec中声明的工具未在registry中注册。

**解决**：
- 检查`researchos/tools/builtin.py`中是否注册了该工具
- 或在debug脚本中修改AgentSpec，移除未注册的工具

#### 问题4：前置输入缺失

**症状**：T2运行时提示`Missing required input: project`

**原因**：T2需要T1的输出作为输入。

**解决**：
- 先运行T1产出project.yaml
- 或使用`--from`参数从其他workspace复制
- 或在debug脚本中手动创建前置文件（如debug_t2_agent.py所示）

### 调试技巧

1. **查看trace了解执行流程**：
   ```bash
   researchos trace <run_id> --workspace <workspace>
   ```

2. **使用--raw查看原始事件**：
   ```bash
   researchos trace <run_id> --workspace <workspace> --raw
   ```

3. **验证单个artifact**：
   ```bash
   researchos validate --workspace <workspace> --task <task_id>
   ```

4. **检查配置是否正确**：
   ```bash
   researchos validate-config
   ```

5. **查看workspace状态**：
   ```bash
   researchos status --workspace <workspace>
   ```

6. **逐步调试**：
   - 先用mock模式验证agent逻辑
   - 再用真实LLM验证完整流程
   - 最后集成到完整pipeline

### 下一步

完成T1/T2调试后，可以：

1. 修改mock响应，测试不同的用户输入场景
2. 尝试真实LLM运行，验证实际效果
3. 查看`researchos/agents/`目录，了解agent实现细节
4. 参考`RUNTIME_FIXES_SUMMARY.md`了解runtime改进历史
5. 开始开发T3-T9 agents

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

### 为什么某些 task 现在跑不起来？

因为 runtime 已经为 T1-T9 预留了 task I/O 契约，但当前真正注册到 [researchos/agents/registry.py](./researchos/agents/registry.py) 的正式 task 只有 `HELLO`、`T1`、`T7.5`、`T2`、`T3`、`T3.5`、`T4`、`T6`。T5、T7-T9 还在开发中。

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

- 当前已实现的 agent：`HelloAgent`、`PIAgent`（T1/T7.5）、`ScoutAgent`（T2）、`ReaderAgent`（T3/T3.5）、`IdeationAgent`（T4）、`ExperimenterAgent`（T6），T5、T7-T9 正式 agent 还在开发中。
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
