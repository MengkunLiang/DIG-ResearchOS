# ResearchOS

ResearchOS 是一个面向研究工作流的 artifact-first runtime。它的目标不是“做一个能聊天的 Agent”，而是把一个研究项目从 idea 一路推进到：

- 文献检索
- 深度阅读
- 文献综合
- 假设生成
- 新颖性审计
- 试点实验
- 完整实验
- PI 评估
- 论文写作 / 审稿 / 修订
- 投稿包构建与编译

如果只记一句话，可以记成：

```text
想法
 -> 文献池
 -> 精读与综述
 -> 假设与实验计划
 -> Pilot
 -> Full Experiment
 -> PI Evaluate
 -> Writing / Review / Revise
 -> Submission Bundle
```

## 当前系统能做什么

当前主链路是：

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

已经接好的核心能力包括：

- `run` / `resume` 完整流水线
- `run-task` 单阶段调试
- 多阶段断点恢复
- artifact 校验
- T4 假设生成会同时落盘 `ideation/idea_scorecard.yaml`、`ideation/rejected_ideas.md`、`ideation/gate_decisions.json` 和 `ideation/idea_rationales.json`，记录每个 idea 的证据链和决策链
- T3 论文阅读会在每篇 `paper_notes/*.md` 中记录 `## 12. Reading Coverage`；PDF 可用时必须覆盖到最后一页，只有完整页码覆盖且最终无截断时才能标记 `[FULL-TEXT]`，分块重读覆盖全篇是合法完成方式
- T3.5 文献综合会先通过 `build_synthesis_workbench` 从 `paper_notes/` 生成 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`，再产出 `synthesis.md`，避免完全依赖单次 prompt
- CLI 人工输入现在会区分真实回答和无输入；预算扩限 gate 支持 `1/2`、`继续/停止`、`确认/stop` 等输入
- LLM profile / tier / fallback / retry
- human gate
- skill 发现与 `run-skill`
- MCP server 加载与工具注册
- Docker 模式实验与 LaTeX 编译
- trace / logs / resume 快照

## 三个最重要的概念

### 1. Workspace 是唯一事实源

ResearchOS 不靠“模型记住上次说了什么”来恢复进度，而是靠 workspace 中已经落盘的文件。

典型目录：

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

### 2. `run/resume` 和 `run-task` 不是一回事

- `run` / `resume`
  会推进完整状态机，会处理 gate，会自动进入下一阶段
- `run-task`
  只跑一个任务，不推进整个工作流

### 3. Agent 只是系统的一层

ResearchOS 不是“一个 Agent 做完所有事”，而是由这些东西共同组成：

- `StateMachine`
- `AgentRunner`
- 多个 task-specific agent
- `ToolRegistry`
- workspace artifact
- validator

例如 T4 不只产出 `ideation/hypotheses.md`、`ideation/exp_plan.yaml` 和 `ideation/risks.md`，还会产出 `ideation/idea_scorecard.yaml`、`ideation/rejected_ideas.md`、`ideation/gate_decisions.json` 和 `ideation/idea_rationales.json`。这些文件用于追踪每个 idea 从哪里来、解决什么 gap、和哪些工作最像、为什么不同、为什么被选中或淘汰，以及后续什么时候应该继续或停止。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `researchos/agents/` | 各阶段 agent |
| `researchos/runtime/` | runner、LLM client、trace、logger、config |
| `researchos/orchestration/` | 状态机、gate、任务 I/O 契约 |
| `researchos/tools/` | 内置工具、MCP adapter、filesystem、paper tools |
| `researchos/skills/` | skill loader、alias、runner |
| `config/` | 状态机、路由、agent 参数、runtime 配置 |
| `docs/` | 详细文档 |
| `infra/docker/` | Docker 构建与运行脚本 |
| `tests/` | 单测与真实环境测试 |
| `workspace/` | 默认本地 workspace |

## 安装方式

### 方式 A：宿主机安装

适合：

- 本地开发
- 单阶段调试
- 配置排查

```bash
git clone <your-repo-url> ResearchOS
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
```

如果你还需要额外 PDF 能力：

```bash
pip install -r requirements-optional-pdf.txt
```

如果你发现 `researchos` 命令和当前源码行为不一致，优先用：

```bash
PYTHONPATH=/绝对路径/ResearchOS python -m researchos.cli ...
```

### 方式 B：Docker 安装

适合：

- T5 / T7 实验执行
- T9 LaTeX 编译
- 避免宿主机依赖漂移
- 追求更稳定的复现

```bash
cd ResearchOS
bash infra/docker/build.sh
```

然后通过 wrapper 运行：

```bash
bash infra/docker/run.sh selftest
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

完整说明见 [docs/docker.md](./docs/docker.md)。

## 环境变量

先复制模板：

```bash
cp .env.example .env
```

最常用的变量：

| 变量 | 作用 |
| --- | --- |
| `SILICONFLOW_API_KEY` | SiliconFlow 模型 |
| `SILICONFLOW_BASE_URL` | SiliconFlow 兼容基地址 |
| `OPENROUTER_API_KEY` | OpenRouter provider |
| `OPENAI_API_KEY` | OpenAI 官方或兼容接口 |
| `OPENAI_BASE_URL` | OpenAI 兼容基地址 |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `S2_API_KEY` | Semantic Scholar |
| `ELSEVIER_API_KEY` | Elsevier Scopus 搜索 |
| `ELSEVIER_INSTTOKEN` | Elsevier 机构授权令牌，可选 |
| `RESEARCHER_EMAIL` | 文献 API 身份邮箱 |
| `GITHUB_TOKEN` | 可选，MCP / GitHub 相关能力 |

原则：

- 密钥写 `.env`
- 运行参数写 `config/*.yaml`

完整配置说明见 [docs/config.md](./docs/config.md)。

## 5 分钟快速开始

### 1. 校验配置

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

### 2. 跑 provider 自检

```bash
python -m researchos.cli selftest
```

现在这条命令除了检查 provider 连通性，也会检查关键 PDF 解析依赖是否就绪。

### 3. 初始化 workspace

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 4. 跑一个最小 smoke task

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/local-test2
```

### 5. 跑完整流水线

```bash
python -m researchos.cli run --workspace ./workspace/local-test2
```

### 6. 恢复中断任务

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

## 常见使用方式

### 场景 1：完整跑一个项目

最适合正式使用，能够走完整状态机和 gate。

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory for long-horizon llm agents"

python -m researchos.cli run --workspace ./workspace/local-test2

"LightGCN作为一个轻量化的图推荐框架，其最大问题是在稀疏数据上的鲁棒性不足，能否通过引入嵌入空间中的对比学习改善其在稀疏数据上的泛化能力和鲁棒性"
```

如果过程中因为 gate、预算扩限或人工中断暂停：

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

### 场景 2：单独调某个阶段

最适合开发调试。

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/local-test2
python -m researchos.cli run-task T7.5 --workspace ./workspace/local-test2
python -m researchos.cli run-task T9 --workspace ./workspace/local-test2
```

也可以从另一个 workspace 复制上游产物：

```bash
python -m researchos.cli run-task T8-WRITE \
  --workspace ./workspace/scratch \
  --from ./workspace/local-test2
```

说明：

- `run/resume` 用来推进完整状态机
- `run-task` 只跑当前阶段
- 但在同一个 workspace 上重跑 `run-task` 时，很多阶段会优先基于已有 artifact 继续

### 场景 3：查看状态和 trace

```bash
python -m researchos.cli status --workspace ./workspace/local-test2
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/local-test2
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T7
```

## 测试方式

常用快速回归：

```bash
python -m py_compile researchos/tools/human_gate.py researchos/tools/ask_human.py researchos/agents/reader.py researchos/tools/literature_synthesis.py researchos/runtime/orchestrator.py

pytest -q \
  tests/unit/test_reader_agent.py \
  tests/unit/test_t3_recovery.py \
  tests/unit/test_ask_human_tool.py \
  tests/unit/test_human_gate.py \
  tests/unit/test_runner_basic.py
```

T5 之前链路相关回归：

```bash
pytest -q \
  tests/unit/test_scout_agent.py \
  tests/unit/test_paper_save_tools.py \
  tests/unit/test_ideation_agent.py \
  tests/unit/test_novelty_auditor_agent.py \
  tests/unit/test_schema_validator.py \
  tests/unit/test_cli_runners.py
```

如果当前沙箱禁止绑定本地端口，`test_runtime_extended_tools.py` 中的 `web_fetch` 本地 HTTP server 测试会因为 socket 权限失败；在普通本机或允许 loopback socket 的 CI 中再跑完整文件。

## Skills

ResearchOS 现在支持独立 skill 运行，基于 `SKILL.md`。

常用命令：

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill deepxiv "summarize recent memory papers for llm agents"
```

当前仓库自带的 paper 相关 skill 包括：

- `paper-compile`
- `paper-write`
- `deepxiv`

当前状态说明：

- skill 发现已经基于 `SKILL.md` frontmatter
- `Bash(*)`、`Glob(*)`、`Grep(*)` 这类别名会被翻译成 runtime tool
- 如果某个 skill 依赖当前 runtime 没注册的高级工具，它可能会降级，而不是完全不可用

更多说明见：

- [docs/runtime.md](./docs/runtime.md)
- [docs/dev.md](./docs/dev.md)

## MCP

ResearchOS 可以加载 MCP server 配置，并把 MCP tool 暴露给 agent。

关键文件：

- `config/mcp.example.yaml`
- `config/mcp.yaml`

启动时 CLI summary 会显示：

- `mcp_servers`
- `mcp_tools`

完整说明见：

- [docs/runtime.md](./docs/runtime.md)
- [docs/config.md](./docs/config.md)

## 预算、Fallback、恢复、Human Gate

这些是当前 runtime 最重要的几个增强点。

### 预算

每个 task 都有预算，包括：

- 最大步数
- token 预算
- wall time 预算

达到预算上限时，runtime 可以弹出 gate，询问是否扩限继续。

### Fallback

`config/model_routing.yaml` 支持 profile 内多候选模型。

典型行为：

1. 先尝试主模型
2. 主模型失败后立即尝试 fallback
3. 一轮候选都失败后才进入下一轮 retry

### 恢复

当前多个关键阶段都有恢复逻辑。例如：

- T3 会基于已有且结构合格的 note 重建 pending deep-read queue；缺少 `Reading Coverage` 或 `[FULL-TEXT]` 页码不完整的旧 note 会继续留在待处理队列中
- T5 / T7 会基于已有实验产物重建 resume state
- T7.5 / T8 / T9 会优先复用现有产物，而不是假装它们不存在

### Human Gate

状态机里支持人工确认节点。当前典型场景包括：

- T7.5 的 PI 评估后分流
- 提交前 / 最终决策类阶段

注意：

- 只有 `run` / `resume` 才会完整体现这些 gate
- `run-task` 只能单独执行某个阶段，不会继续推进完整状态机
- 如果 `ask_human` 在非交互环境中拿不到输入，runtime 会暂停任务并写入 `state.yaml`，不会把空回答当作用户确认继续执行
- 预算扩限 gate 支持数字序号，也支持 `继续`、`确认`、`停止`、`stop` 等常用输入

## 文档导航

建议按角色阅读：

- 系统流程总览：[docs/agent_pipeline.md](./docs/agent_pipeline.md)
- Runtime 实现：[docs/runtime.md](./docs/runtime.md)
- Docker 使用：[docs/docker.md](./docs/docker.md)
- 配置说明：[docs/config.md](./docs/config.md)
- 开发者手册：[docs/dev.md](./docs/dev.md)
- 各阶段与各 Agent 细节：[docs/agent_pipeline.md](./docs/agent_pipeline.md)

## 当前实现状态

当前代码库已经能跑，但它仍然是一个持续演进的研究运行时。

对当前状态，最准确的预期是：

- pipeline 基本可运行
- 关键阶段已具备断点恢复
- T2 正常路径由检索工具返回值触发 runtime 自动保存 raw，并由 runtime 确定性完成 dedup、verified、deep-read queue 和审计文件
- T3 `[FULL-TEXT]` 校验支持分块重读覆盖全篇，例如 `1-4, 5-8, 9-10 / 10`，并要求 `Truncation` 明确说明最终无截断
- T3.5 已具备分阶段 synthesis workbench，而不是只靠一次 LLM prompt 直接写完整综述
- T9 已经改成“编译失败则修复并重试”的投稿包阶段
- provider 稳定性仍会影响长任务
- 部分配置字段是真正接线的，部分只是声明或部分接线
- 某些 skills 如果依赖未注册能力，会以降级模式运行

## 已知限制

- T4 的两轮 idea gate 目前仍主要通过 `ask_human` 和 artifact 记录完成，尚未完全拆成状态机级正式 gate。
- T4.5 novelty 审计仍依赖 LLM 生成搜索策略，后续应进一步工具化为结构化 novelty audit。
- 长任务仍受 provider 稳定性、速率限制和 PDF 解析质量影响。
- Docker / LaTeX / 本地 HTTP 测试依赖宿主环境权限；沙箱环境可能无法覆盖全部集成路径。

## 常见问题

### 为什么 `researchos` 和 `python -m researchos.cli` 行为不一致？

通常是因为 shell 里命中的 console script 与当前源码目录不一致。

优先用：

```bash
PYTHONPATH=/绝对路径/ResearchOS python -m researchos.cli ...
```

或者重新：

```bash
pip install -e .
```

### 为什么中断后会从头跑？

常见原因：

- 你换了 workspace
- 中断前关键 artifact 还没落盘
- 该阶段有恢复逻辑，但预期文件缺失或损坏

### 为什么 `run-task` 和 `run` 表现不一样？

因为 `run-task` 只跑一个阶段，不推进整个状态机。

如果你想测：

- gate
- 自动下一阶段
- `T7 -> T7.5 -> human gate -> T8`

应该用 `run` 或 `resume`。

### 出错后先看哪里？

推荐顺序：

1. CLI 最后的错误摘要
2. `workspace/<name>/_runtime/logs/researchos.log`
3. `workspace/<name>/_runtime/traces/*.jsonl`
4. 对应 task 产物目录

## 进一步阅读

如果你准备继续扩展这个项目，建议继续阅读：

- `CLAUDE.md`
- `config/README.md`
- `docs/agent_pipeline.md`
