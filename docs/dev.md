# ResearchOS Developer Guide

本文档面向第一次接手 ResearchOS 代码库、需要本地开发、调试、扩展和排障的开发者。

如果你更关心“这个系统整体怎么工作”，先看：

- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/runtime.md](./runtime.md)
- [docs/config.md](./config.md)
- [docs/docker.md](./docker.md)

如果你更关心“作为用户怎么部署和使用”，看：

- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)

---

## 1. 开发者先建立的心智模型

ResearchOS 不是一个“写几个 prompt 然后调用 LLM”的轻量脚本仓库，而是一个：

- 以 `workspace` 为事实源
- 以 `state_machine` 为流程骨架
- 以 `agent + tool` 为执行主体
- 以 `artifact validation` 为收敛机制
- 以 `trace / logs / resume` 为可调试基础

的研究流程运行时。

理解它时，建议从这五层看：

1. CLI 层
2. Runtime 层
3. Orchestration 层
4. Agent / Prompt 层
5. Tool / Workspace 层

典型调用链：

```text
researchos run-task T3
 -> cli.py
 -> SingleTaskRunner
 -> StateMachine / Task I-O Contract
 -> AgentRunner
 -> ReaderAgent
 -> Tools
 -> workspace artifacts
 -> validator
```

---

## 2. 本地开发环境

### 2.1 推荐环境

- Linux / WSL / 容器化 Linux
- Python 3.11
- Conda
- 可选：Docker
- 可选：GPU

这个仓库在本机开发时，推荐使用专门的 conda 环境。

如果你沿用当前维护环境，可直接：

```bash
conda activate researchos
```

如果你是新机器，推荐自己创建一个独立环境：

```bash
conda create -n researchos python=3.11 -y
conda activate researchos
```

### 2.2 安装依赖

最常用的开发安装方式：

```bash
cd ResearchOS
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
```

如果你需要额外的 PDF 处理依赖：

```bash
pip install -r requirements-optional-pdf.txt
```

说明：

- `requirements.txt`：基础运行依赖
- `requirements-dev.txt`：pytest、调试和开发辅助依赖
- `requirements-optional-pdf.txt`：额外 PDF 处理能力
- `pip install -e .`：确保 `researchos` 命令和当前源码目录绑定

### 2.3 环境变量

复制模板：

```bash
cp .env.example .env
```

至少建议配置：

```bash
SILICONFLOW_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...
S2_API_KEY=...
RESEARCHER_EMAIL=your@email
```

注意：

- `.env` 应主要放密钥和身份信息
- 运行参数应尽量放在 `config/*.yaml`
- 如果 `researchos` 和 `python -m researchos.cli` 表现不一致，优先检查是不是环境错配

---

## 3. 第一次拉起项目时的检查顺序

建议严格按这个顺序做，而不是一上来直接跑 T9。

### 3.1 配置校验

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

预期：

- 输出 `ok: true`

如果这里都过不了，不要继续跑 task。

### 3.2 模型连通性检查

```bash
python -m researchos.cli selftest
```

建议重点看：

- SiliconFlow 是否可用
- OpenRouter / OpenAI 是否可用
- latency 是否异常高
- `pdfplumber` 等关键 PDF 解析依赖是否就绪（影响 T3 / T9）

### 3.3 创建一个最小 workspace

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/dev-smoke \
  --project-id dev-smoke \
  --topic "runtime smoke test"
```

### 3.4 跑 HELLO

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/dev-smoke
```

预期：

- `hello.txt` 生成
- 任务成功结束

### 3.5 看 trace 和状态

```bash
python -m researchos.cli status --workspace ./workspace/dev-smoke
python -m researchos.cli trace <run_id> --workspace ./workspace/dev-smoke
```

如果 `HELLO` 都不稳定，不要继续往上排查 agent 逻辑。

---

## 4. 开发时最常用的 CLI 命令

### 4.1 `init-workspace`

初始化标准目录结构。

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/demo \
  --project-id demo \
  --topic "memory systems for llm agents"
```

典型 case：

- 新建一个最小调试工程 `./workspace/demo`
- 后续先跑 `HELLO`、再跑 `T1`

### 4.2 `run`

从当前状态推进完整 pipeline。

```bash
python -m researchos.cli run --workspace ./workspace/demo
```

典型 case：

- 你已经准备好 `project.yaml` 和必要 seeds
- 想让系统从当前状态一路往下推进，而不是手工一个个敲 task

### 4.3 `resume`

恢复一个被 gate 暂停、预算中断或人工中断后的 workspace。

```bash
python -m researchos.cli resume --workspace ./workspace/demo
```

典型 case：

- `T7.5` 已经生成 `evaluation/evaluation_decision.md`，现在要继续
- `T9` 编译前半段已经写出 bundle，想继续收敛
- provider 超时后，你想基于现有产物接着跑

### 4.4 `run-task`

单独跑某一个 task，用于本地调试。

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/demo
python -m researchos.cli run-task T7.5 --workspace ./workspace/demo
python -m researchos.cli run-task T9 --workspace ./workspace/demo
```

典型 case：

- `T3`：调 PDF 获取、全文覆盖、Reading Coverage 和续跑逻辑
- `T7.5`：调 PI evaluate 和 `next_task`
- `T9`：调投稿包编译、修复与验收

### 4.5 `validate`

校验某个 workspace 当前产物是否符合约定。

```bash
python -m researchos.cli validate --workspace ./workspace/demo --task T7
```

典型 case：

- 你怀疑 `T7` 结果已经写出来了，但状态机仍说失败
- 想单独验证 `results_summary.json`、`ablations.csv` 和相关输出是否满足规则

### 4.6 `status`

看当前状态机状态。

```bash
python -m researchos.cli status --workspace ./workspace/demo
```

典型 case：

- 想确认现在停在 `T6` 还是 `T7.5`
- 想看是否存在 `pending_gate`

### 4.7 `trace`

查看某一次运行的 JSONL trace。

```bash
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/demo
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/demo --raw
```

典型 case：

- 想确认 agent 到底调用了哪些 tool
- 想看 validator 为什么失败
- 想复盘某次 run 的逐步行为

### 4.8 `list-skills` / `run-skill`

技能运行时调试：

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill paper-compile "compile the paper in ./workspace/local-test2/drafts"
```

典型 case：

- 验证 `SKILL.md` 是否被发现
- 验证 paper 相关 skill 当前能否真实执行

---

## 5. Workspace 目录怎么读

开发时不要只盯着 stdout。真正重要的是 workspace。

### 5.1 研究产物目录

- `user_seeds/`
- `literature/`
- `ideation/`
- `pilot/`
- `novelty/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`

### 5.2 Runtime 目录

默认在：

- `workspace/<name>/_runtime/`

重点看：

- `_runtime/logs/researchos.log`
- `_runtime/traces/*.jsonl`
- `_runtime/resume/*.json`

### 5.3 典型排障顺序

1. 先看 CLI 最后的错误摘要
2. 再看 `_runtime/logs/researchos.log`
3. 再看具体 `trace`
4. 最后看 workspace 里哪些 artifact 实际落了盘

---

## 6. 单任务调试的推荐方式

### 6.1 原则

调某个阶段时，尽量做到：

- 固定 workspace
- 固定输入产物
- 只改一类东西
- 每次改完立刻 `run-task`
- 必要时用 `validate`

### 6.2 `--from` 的用法

如果你要在另一个 workspace 上只复用上游产物：

```bash
python -m researchos.cli run-task T8-RESOURCE \
  --workspace ./workspace/scratch \
  --from ./workspace/local-test2
```

这会把当前 task 的前置 artifact 复制过来，再执行本 task。

适合：

- 单独复现某阶段 bug
- 不污染主 workspace

### 6.3 推荐单任务调试顺序

- `HELLO`：验证 runtime 最小闭环
- `T2`：验证搜索、去重、verification 和队列生成
- `T3`：验证 PDF 获取、全文覆盖、Reading Coverage 和续跑
- `T5/T7`：验证实验恢复、预算 gate、Docker
- `T7.5`：验证 PI 评估与 `next_task`
- `T8-RESOURCE`：验证资源索引、证据计划和图表计划
- `T8-SECTION-PLAN`：验证 `paper_state.json` 和每章局部大纲
- `T8-SEC-*`：逐个验证单章节草稿；每次只写一个 section
- `T8-DRAFT`：验证章节拼装和 manuscript audit
- `T8-REVIEW-1/2`：验证 reviewer 逻辑
- `T9`：验证 bundle 生成、编译、修复重试

---

## 7. 各任务成功后应该检查什么

这部分对开发者非常重要。不要只看“exit code = 0”。

| Task | 关键成功目标 | 最先检查的文件 |
| --- | --- | --- |
| `HELLO` | runtime 最小闭环 | `hello.txt` |
| `T1` | `project.yaml` 合法且信息完整 | `project.yaml`, `state.yaml` |
| `T2` | 候选池、verified 池、deep-read 队列都落盘 | `papers_dedup.jsonl`, `papers_verified.jsonl`, `deep_read_queue.jsonl`, `access_audit.md` |
| `T3` | note/table/bib 同步增长、PDF 可用时全文覆盖、且支持续跑 | `paper_notes/`, `comparison_table.csv`, `related_work.bib`, `deep_read_queue_pending.jsonl` |
| `T3.5` | synthesis 分阶段产物和最终综合结构完整 | `literature/synthesis_workbench.json`, `literature/synthesis_outline.md`, `literature/synthesis_draft.md`, `literature/synthesis.md` |
| `T4` | hypotheses / exp_plan / idea scorecard / gate decisions / risks 成对齐 | `ideation/hypotheses.md`, `ideation/exp_plan.yaml`, `ideation/idea_scorecard.yaml`, `ideation/rejected_ideas.md`, `ideation/gate_decisions.json`, `ideation/idea_rationales.json`, `ideation/risks.md` |
| `T4.5` | novelty audit 生成；如有 High/Medium Overlap 则归档 collision cases | `ideation/novelty_audit.md`, `ideation/collision_cases.md`（条件产物） |
| `T5` | pilot plan/code/results、动机判断、smoke marker 和环境摘要完整 | `pilot/pilot_plan.yaml`, `pilot/pilot_code/`, `pilot/pilot_results.json`, `pilot/motivation_validation.md`, `pilot/smoke_test_passed.marker`, `pilot/docker_digests.txt` |
| `T6` | novelty report / collision / baselines 三件套完整 | `novelty/novelty_report.md`, `novelty/collision_cases.md`, `novelty/must_add_baselines.md` |
| `T7` | summary / runs / configs / ablations / log / seed ensemble / diversity / 环境摘要齐全 | `experiments/results_summary.json`, `experiments/runs/`, `experiments/configs/`, `experiments/ablations.csv`, `experiments/iteration_log.md`, `experiments/seed_ensemble_summary.json`, `experiments/iteration_diversity_check.md`, `experiments/docker_digests.txt` |
| `T7.5` | evaluation decision 能给出 `next_task` | `evaluation/evaluation_decision.md` |
| `T8-RESOURCE` | 写作资源、章节、证据和图表计划生成 | `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, `drafts/figure_table_plan.json` |
| `T8-WRITE` | 论文论证大纲生成 | `drafts/outline.md` |
| `T8-SECTION-PLAN` | 逐章节写作共享状态和局部大纲生成 | `drafts/paper_state.json`, `drafts/section_outlines/*.md` |
| `T8-SEC-*` | 单章节草稿完成；每个节点只写一个 section | `drafts/sections/<section>.tex` |
| `T8-DRAFT` | 章节拼装、全局融合、机械审计完成 | `drafts/paper.tex`, `drafts/manuscript_audit.md` |
| `T8-SELF-CHECK` | 作者自查完成 | `drafts/self_check.md` |
| `T8-REVIEW-1/2` | 审稿意见生成 | `drafts/review_rounds/round_1.md`, `round_2.md` |
| `T8-REVISE-1/2` | 主稿按审稿意见修订 | `drafts/paper.tex` |
| `T9` | bundle 生成且编译成功 | `submission/bundle/main.tex`, `main.pdf`, `migration_report.md` |

### 7.1 T2 重点看什么

- `papers_raw` 和 `papers_dedup` 是否分离
- `papers_verified` 是否生成
- `verification_failures` 是否合理
- `deep_read_queue` 是否确实优先 seed 和高可读性论文

### 7.2 T3 重点看什么

- 是否只复用结构合格的已有 `paper_notes`
- 是否正确生成 `deep_read_queue_pending.jsonl`
- PDF 可用的 note 是否包含 `## 12. Reading Coverage`
- `[FULL-TEXT]` note 的 `Pages read` 是否类似 `1-N / N` 或 `1-4, 5-8, 9-N / N`，且 `Truncation` 明确最终无截断
- `comparison_table.csv` 是否持续可追加
- `related_work.bib` 是否没有粘连/损坏

### 7.3 T5/T7 重点看什么

- 是否存在 resume state
- 是否复用了已有代码和运行目录
- 如果预算触顶，是否先进入 gate，而不是直接硬停

### 7.4 T7.5 重点看什么

- `evaluation_decision.md` 是否含 `Situation`、`Options`、`next_task`
- `next_task` 能否被状态机解析

### 7.5 T9 重点看什么

- 是否真正尝试编译
- 编译失败后是否修复并重试
- 最终是否产出 `main.pdf`
- `migration_report.md` 是否明确记录编译结果和修复过程

---

## 8. 本地调试的几种典型路径

### 8.1 调 prompt

推荐步骤：

1. 找到对应 prompt
2. 改 prompt
3. 用固定 workspace `run-task`
4. 看 trace 中 tool 调用和最终 validator

常见文件：

- `researchos/prompts/*.j2`

### 8.2 调 validator

推荐步骤：

1. 找 agent 的 `validate_outputs`
2. 用现有 workspace 直接复现
3. 补单测
4. 再跑 `run-task`

常见文件：

- `researchos/agents/*.py`
- `tests/unit/test_*`

### 8.3 调工具

推荐步骤：

1. 先单测工具
2. 再让 agent 间接调用
3. 观察 tool trace 是否符合预期

常见文件：

- `researchos/tools/*.py`
- `researchos/tools/builtin.py`

### 8.4 调状态机

推荐步骤：

1. 改 `config/state_machine.yaml`
2. 跑 `validate-config`
3. 必要时单测 `StateMachine`
4. 再用 `run` / `resume` 验证完整链

常见文件：

- `config/state_machine.yaml`
- `researchos/orchestration/state_machine.py`
- `researchos/orchestration/task_io_contract.py`

---

## 9. 技能（skills）开发与调试

### 9.1 当前支持什么

ResearchOS 当前支持：

- `SKILL.md` frontmatter 发现
- `list-skills`
- `run-skill`
- Claude 风格工具别名到 runtime 工具的映射

当前仓库内的 paper 类 skill：

- `skills/paper-compile`
- `skills/paper-write`
- `skills/deepxiv`

### 9.2 如何验证 skill runtime

```bash
python -m researchos.cli list-skills --skills-root ./skills
```

再用某个具体 skill：

```bash
python -m researchos.cli run-skill deepxiv "summarize recent papers about memory for llm agents"
```

### 9.3 skill 调试时常见问题

- `No skills found`
  原因通常是 `skills-root` 错了，或者没有 `SKILL.md`

- 工具别名无法解析
  看 `researchos/skills/tool_aliases.py`

- skill 声明了 runtime 没实现的高级工具
  例如 `Agent`、`WebSearch`、某些 MCP tool

这时 skill 可能会降级，而不是完全不可用。

---

## 10. MCP 开发与调试

### 10.1 配置入口

- `config/mcp.example.yaml`
- `config/mcp.yaml`

### 10.2 调试顺序

1. 确认 server 配置能被加载
2. 跑启动自检
3. 看 startup summary 里 `mcp_servers` / `mcp_tools`
4. 再验证具体 agent 能否使用 MCP tool

### 10.3 常见问题

- server 启动了但没注册 tool
- skill 里声明了 MCP 工具，但当前 runtime 没这个 tool
- 容器环境和宿主机环境的 MCP 配置不一致

---

## 11. Docker 开发调试

当问题与以下内容相关时，优先用 Docker 复现：

- T5/T7 代码运行
- GPU 可用性
- LaTeX 编译
- 宿主机依赖漂移

基本命令：

```bash
cd ResearchOS
bash infra/docker/build.sh
bash infra/docker/run.sh selftest
bash infra/docker/run.sh run-task T9 --workspace /workspace
```

重点排查：

- Docker image 是否是最新
- `/workspace` 是否正确挂载
- `.env` 是否透传
- 容器内是否真在跑当前仓库代码

---

## 12. 测试命令建议

### 12.1 跑单个测试文件

```bash
cd ResearchOS
python -m pytest tests/unit/test_writer_reviewer_submission.py -q
```

### 12.2 跑某一类测试

```bash
python -m pytest tests/unit -q
```

### 12.3 改完配置或状态机后建议至少做的事

```bash
python -m researchos.cli validate-config
python -m pytest tests/unit/test_state_machine_runtime_features.py -q
```

### 12.4 改完 skill runtime 后建议做的事

```bash
python -m pytest tests/unit/test_list_skills.py tests/unit/test_skills_runtime.py tests/unit/test_skill_tool_discovery.py -q
python -m researchos.cli list-skills --skills-root ./skills
```

---

## 13. 常见问题与处理建议

### 13.1 `researchos` 命令和源码行为不一致

优先用：

```bash
PYTHONPATH=. python -m researchos.cli ...
```

然后重新：

```bash
pip install -e .
```

### 13.2 provider 一直超时

检查：

- `config/model_routing.yaml` 是否有 fallback
- `agent_params.yaml` 顶层 `retry_policy.llm_retries` 是否过大
- 是否误用了只含一个候选的 profile

### 13.3 任务中断后从头跑

检查：

- 是否用的是同一个 workspace
- 对应 task 是否已接入 resume 逻辑
- `_runtime/resume/*.json` 是否生成
- 关键 artifact 是否真的落盘

### 13.4 `run-task` 能过，`run/resume` 不对

常见原因：

- 状态机下一跳有问题
- gate 配置与状态机不一致
- `run-task` 不推进 FSM，但 `run/resume` 会推进

### 13.5 tool 看起来“存在”，但 agent 不会用

先检查：

- `agent_params.yaml` 的 `agents.<agent>.tools.tool_names` 是否把工具暴露给了该 agent
- tool 是否注册在 `builtin.py`
- prompt 是否明确告诉 agent 该怎么用

---

## 14. 推荐阅读顺序

对于新开发者，推荐按这个顺序读：

1. [README.zh-CN.md](../README.zh-CN.md) 或 [README.md](../README.md)
2. [docs/agent_pipeline.md](./agent_pipeline.md)
3. [docs/runtime.md](./runtime.md)
4. [docs/config.md](./config.md)
5. [docs/docker.md](./docker.md)
6. [docs/logging.md](./logging.md)

如果你在本地维护当前仓库，建议再结合：

- [../tmp/researchos-local-debug-guide.md](../tmp/researchos-local-debug-guide.md)

一起使用。
