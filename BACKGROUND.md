# ResearchOS Background

> 用途：在新的 Codex 窗口开始工作时，先阅读本文件，快速恢复 ResearchOS 当前代码库的背景、实际目录结构和已接线流程。若本文件与代码冲突，以 `config/system_config/state_machine.yaml`、`config/system_config/gates.yaml` 和当前源码为准。

## 项目定位

ResearchOS 是一个 artifact-first 的自动化科研运行时，不是单一聊天 Agent。它把科研项目拆成状态机阶段，用 workspace 中的文件作为唯一事实源，覆盖想法形成、文献检索、精读、综述/综合、假设生成、新颖性审计、外部实验 handoff、结果摄取、论文写作、审稿修订和投稿包编译。

核心设计：

- `StateMachine` 决定任务顺序、分支和 gate。
- `AgentRunner` 调用 task-specific agent 与工具。
- `workspace/` 里的 artifact 决定进度、恢复和下游输入。
- validator、fingerprint、trace、logs、resume snapshot 用来保证可恢复、可审计。
- 真实长实验不在 ResearchOS 主进程内跑；当前主链通过外部执行器协议交给 Codex CLI、Claude Code、人工或 mock dry-run。

## 当前主链路

当前代码已超过早期 T1-T8 简化描述，实际主链包含 T9 投稿包阶段，并在 T3.6 与 T5 增加了多个人工 gate；其中 T5 context re-boost 已改为 ResearchOS 直接调用 LLM API 的自动节点：

```text
T1
 -> T2-PARAM-GATE -> T2-PARAM-CONFIRM-GATE -> T2 -> T2-COVERAGE-GATE
 -> T3 -> T3.5
 -> T3.6-GATE-SURVEY
    -> no: T4
    -> yes: T3.6-TEMPLATE-GATE -> T3.6-PLAN -> T3.6-GATE-OUTLINE
            -> T3.6-GATE-CORPUS -> optional T3.6-EXPAND
            -> T3.6-STATE -> T3.6-SEC-* -> T3.6-ASSEMBLE
            -> T3.6-REVIEW -> T3.6-COMPILE -> T3.6-FEED
            -> T3.6-POST-SURVEY-GATE
 -> T4 -> T4-GATE1 -> T4 -> T4.5
    -> pass*: T5-REBOOST-GATE
    -> otherwise: T4.5-HUMAN-REVIEW -> T5-REBOOST-GATE/T4/done
 -> T5-REBOOST-GATE -> T5-HANDOFF -> T5-SKILL-CUSTOMIZATION-GATE
 -> T5-EXPR-MATERIAL-GATE -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> codex_cli/claude_code_window/manual: T5-EXTERNAL-WAIT
 -> T7-INGEST -> T7-AUDIT -> T7-POST-NOVELTY -> T7-CLAIMS
 -> T7.5 -> human gate -> T8-STYLE-GATE -> T8-RESOURCE
 -> T8-WRITE -> T8-SECTION-PLAN -> T8-SEC-* -> T8-DRAFT
 -> T8-SELF-CHECK -> T8-REVIEW-1 -> T8-REVISE-1
 -> T8-REVIEW-2 -> T8-REVISE-2 -> T8-PAPER-CLAIM-AUDIT
 -> T9 -> done
```

Legacy `T5` / `T6` / `T7` 内部实验节点仍保留在状态机中，但普通 `run-task T5/T6/T7` 会报 retired。只有显式使用 `LEGACY-T5-PILOT`、`LEGACY-T6-NOVELTY`、`LEGACY-T7-FULL --allow-legacy` 才用于旧链路调试。

## 仓库结构

```text
researchos/                Python 包主体
  agents/                  T1/T2/T3/T4/T4.5/T5-T7/T8/T9 agent
  cli_runners/             完整 pipeline 与单 task runner
  orchestration/           state machine、gate presenter、task I/O contract
  runtime/                 LLM client、config、budget、trace、resume、workspace
  schemas/                 artifact / claim / number validators
  skills/                  runtime skill loader、runner、tool aliases
  tools/                   文献检索、PDF、BibTeX、LaTeX、外部实验、MCP、文件工具
  testing/                 测试 fixture 和 mock
config/
  user_settings.yaml       日常 LLM、预算、timeout/retry 入口
  model_routing.yaml       endpoint/profile/fallback 候选链
  agent_params.yaml        agent 工具、权限、prompt/schema、behavior 阈值
  runtime.yaml             workspace、日志、UI、Docker、web_fetch 设置
  system_config/           状态机、human gates、CDR schema、venue style map
docs/                      设计、运行时、配置、Docker、外部执行器、写作等文档
skills/                    仓库级 SKILL.md 模板与共享引用
tests/unit/                pytest 单元测试
tests/real/                真实环境/真实 API 测试
scripts/                   调试脚本与实验性测试脚本
infra/docker/              统一 Docker 镜像构建与运行 wrapper
latex_templete/            中文/英文基础模板、CCF/UTD 等 LaTeX 模板
workspace/                 本地项目 workspace，通常是生成物，不要当源码改
tmp/                       临时输出
```

## Workspace 事实源

每个项目在一个 workspace 中运行。典型目录包括：

- `user_seeds/`：用户 seed idea、seed paper、seed PDF、外部资源。
- `literature/`：T2/T3/T3.5 文献池、精读队列、笔记、综合、BibTeX。
- `ideation/`：T4 候选 idea、Gate1 选择、假设、实验计划、风险、T4.5 审计。
- `external_executor/`：T5 handoff、外部执行器 prompt、skills、result pack、status。
- `experiments/`：T7 摄取后的结果摘要、evidence index、integrity audit。
- `drafts/`：T8 写作资源、章节、paper.tex、claim audit、review/revision。
- `submission/`：T9 投稿 bundle、main.tex、references.bib、PDF、compile report。
- `_runtime/`：logs、traces、resume snapshot、运行状态。

`init-workspace`、`run`、`resume`、`run-task` 会幂等刷新标准目录树和 `_DIR_GUIDE.md`，但不会递归污染 `external_executor/workdir`、`resources/repos`、PDF、figures 等外部资产目录。

## 阶段要点

- T1 初始化项目，生成 `project.yaml`、`state.yaml` 和可为空的 `literature/bridge_domain_plan.json`。
- T2 先经过参数 gate 和最终确认 gate，再检索、去重、metadata/PDF hint 回填、citation snowball、语言/来源策略筛选，输出 retained pool、backlog、deep-read queue、domain map、access audit 和 coverage decision。
- T3 以 `queue_rank` 为单位保存结构化精读笔记；`notes_manifest.json` 绑定队列、seed、PDF 和配置 fingerprint。PDF 可用时必须证明完整页码覆盖才可标记 `[FULL-TEXT]`。
- T3.5 通过 `build_synthesis_workbench` 生成 workbench、outline、draft，再生成 `synthesis.md`。abstract-only 与 metadata-only 只能作为弱证据/补资源提示。
- T3.6 是可选 taxonomy-driven survey paper 支线，不是把 `synthesis.md` 直接转 TeX。它有模板 gate、outline gate、corpus gate、逐 section 写作、审计、编译和 post-survey 去向 gate。
- T4 先生成候选池与 Markdown 候选卡片，进入状态机级 `T4-GATE1`。用户可选择、合并、补充新 idea 或要求重新分析，之后才生成最终假设和实验计划。
- T4.5 做 novelty/collision 与 mechanism tuple 审计。非通过 verdict 进入人工决策 gate，不自动拒绝或死循环回 T4。
- T5 现在是外部执行链：LLM API context re-boost、handoff pack、LLM API skill customization、实验材料放置、执行器选择。`T5-REBOOST-GATE` 会自动写 `external_executor/handoff_pack.json#context_reboost` 和 `external_executor/reboost_report.json`，`T5-SKILL-CUSTOMIZATION-GATE` 会自动写 `external_executor/skills/customization_report.json`；re-boost 和 skill 定制都不再要求用户手动拉起 Codex。真实实验由外部执行器写回 `result_pack.json`、`executor_status.json`、`run_manifest.json`。
- T7 摄取外部结果，做 integrity audit、post-experiment novelty、result-to-claim，生成写作 evidence pack 和 must-not-claim 边界。
- T7.5 由 PI 评估实验结果并通过 human gate 分流到写作、补实验、回 T4 或结束。
- T8 先确认写作风格/模板，再生成资源索引、证据计划、章节状态，逐章写作、拼装、自查、两轮审稿修订，最后做 paper claim audit。
- T9 迁移到投稿 bundle，复制/重写可允许的 figures，规范 bibliography，编译并记录 compile report；claim/craft/placeholder 检查不通过时不得放行。

## 常用命令

```bash
# 安装
conda create -n researchos python=3.11 -y
conda activate researchos
pip install -r requirements.txt
pip install -e .

# 配置/自检
python -m researchos.cli validate-config
python -m researchos.cli selftest

# 初始化和最小 smoke
python -m researchos.cli init-workspace --workspace ./workspace/dev --project-id dev --topic "memory systems for llm agents"
python -m researchos.cli run-task HELLO --workspace ./workspace/dev

# 完整运行/恢复
python -m researchos.cli run --workspace ./workspace/dev
python -m researchos.cli resume --workspace ./workspace/dev

# 快速真实链路联调
python -m researchos.cli run_smoke --workspace ./workspace/smoke --from ./workspace/dev --active-pool-max 20 --deep-read-target 3 --abstract-sweep 5 --skip-startup-selftest

# 单阶段调试
python -m researchos.cli run-task T3 --workspace ./workspace/dev
python -m researchos.cli run-task T3.6-PLAN --workspace ./workspace/dev
python -m researchos.cli run-task T5-REBOOST-GATE --workspace ./workspace/dev
python -m researchos.cli run-task T5-HANDOFF --workspace ./workspace/dev
python -m researchos.cli run-task T5-SKILL-CUSTOMIZATION-GATE --workspace ./workspace/dev
python -m researchos.cli run-task T7-AUDIT --workspace ./workspace/dev
python -m researchos.cli run-task T8-RESOURCE --workspace ./workspace/dev
python -m researchos.cli run-task T9 --workspace ./workspace/dev

# 状态/trace/校验
python -m researchos.cli status --workspace ./workspace/dev
python -m researchos.cli trace <run_id> --workspace ./workspace/dev
python -m researchos.cli validate --workspace ./workspace/dev --task T7-AUDIT
```

## 配置与密钥

- 密钥放 `.env`，模板见 `.env.example`。常用变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`SILICONFLOW_API_KEY`、`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`S2_API_KEY`、`ELSEVIER_API_KEY`、`ELSEVIER_INSTTOKEN`、`RESEARCHER_EMAIL`、`GITHUB_TOKEN`。
- 日常模型、预算、timeout/retry 改 `config/user_settings.yaml`。
- endpoint/profile/fallback 改 `config/model_routing.yaml`。
- T2/T3 机械阈值、工具权限、prompt/schema 能力注册改 `config/agent_params.yaml`。
- 状态机拓扑和 gate 文案在 `config/system_config/`，属于系统契约，普通调参不要改。
- Docker 统一镜像为 `researchos/system:latest`，构建与运行入口在 `infra/docker/`。

## Skills 和外部执行器

仓库根 `skills/` 下有通用 skill 模板，例如 `skills_customization`、`paper-write`、`paper-compile`、`result-to-claim` 等；正式外部执行器的 13 个模板 skill 放在 `skills/external_executor_skills/` 下。ResearchOS runtime 也支持 `run-skill`：

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill deepxiv "summarize recent memory papers for llm agents"
```

`T5-REBOOST-GATE` 会直接调用 LLM API 生成 re-boost 上下文；`T5-HANDOFF` 会把 `skills/external_executor_skills/` 下的 13 个模板复制到 `external_executor/skills/`；`T5-SKILL-CUSTOMIZATION-GATE` 随后直接调用当前配置的 LLM provider，读取 `external_executor/skills/skills_customization/SKILL.md` 和 manifest，把通用模板原地改写成项目专属执行 skills，并写出 `external_executor/skills/customization_report.json`。真实执行时 Codex/Claude/manual executor 应读取 `external_executor/AGENTS.md` 和项目定制 skills，不应修改 ResearchOS runtime/config/drafts/submission。

## 测试与验证

测试框架是 `pytest`，配置在 `pyproject.toml`。常用回归：

```bash
python -m pytest tests/unit -q
python -m pytest tests/unit/test_workspace_initialization.py -q
python -m pytest tests/unit/test_state_machine_runtime_features.py -q
python -m pytest tests/unit/test_external_executor_skill_templates.py -q
```

真实 API 或本地端口/Docker/LaTeX 相关测试可能受环境限制。长链路开发时，优先用 `validate-config`、针对性 unit tests、`run-task HELLO`、`run_smoke` 和实际 workspace artifact 检查组合验证。

## 新窗口工作建议

1. 先读本文件，再按需要读 `README.md`、`README.zh-CN.md`、`AGENTS.md`、`config/README.md`。
2. 涉及流程分支时，以 `config/system_config/state_machine.yaml` 和 `gates.yaml` 为准。
3. 涉及 artifact 输入输出时，查 `researchos/orchestration/task_io_contract.py`、`researchos/schemas/validator.py` 和对应 agent/tool。
4. 不要把生成的 workspace、trace、PDF、实验输出当源码重构；除非任务明确要求处理某个 workspace artifact。
5. 保持 resume/fingerprint 语义：已有产物能否复用必须由输入 hash、manifest、validator 或状态机逻辑证明。
6. README 是面向用户的入口；新增流程或 gate 后，需要同步更新中英文 README、本背景文件和必要的 docs。
