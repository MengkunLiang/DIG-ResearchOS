# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS 是以 workspace 产物为事实源的多智能体科研运行时，用于文献检索与阅读、证据约束的 Idea 生成、外部实验交接、论文写作与审阅、真实编译和投稿包生成。系统不依赖模型记忆上一轮对话； 每个阶段都有落盘文件、校验、日志与恢复语义。

```text
T1 研究范围 -> T2 文献 -> T3 阅读 -> T3.5 综合
  -> 可选 T3.6 综述 -> T4 Idea -> T4.5 新颖性
  -> T5 外部执行 -> T8 论文 -> T9 投稿包
```

## 先理解流程，而不是记住内部名称

`T` 是流程阶段标签，不是你日常需要输入的命令。正常情况下只需新项目使用 `run`、暂停后使用 `resume`；系统会在需要你做研究决定时停在 `Gate`，并说明下一步。`T3.5` 是 T3 阅读后的综合，`T3.6` 是可选的综述分支，`T4.5` 是 T4 研究方向后的新颖性审计，不是需要你手动运行的“半个任务”。

带长后缀的名称，例如 `T5-REBOOST-GATE`、`T5-PROTOCOL-GATE` 或 `T3.6-SEC-INTRO`，是终端状态和定点调试使用的内部检查点。首次运行不需要记住它们；看到它们时，可用下面的表理解它们属于哪一段流程。当前主流程没有面向用户的 T6/T7：旧的同名节点仅为历史 workspace 兼容保留，真实实验由 T5 外部执行链完成。

| 你会看到的阶段 | 用通俗语言说 | 什么时候需要你决定 | 优先查看的文件 |
| --- | --- | --- | --- |
| T1 范围 | 明确研究问题、边界、投稿取向和种子材料。 | 项目初始化时确认题目与范围。 | `project.yaml` |
| T2 文献 | 检索、去重、核验并排定阅读优先级。 | 选择阅读覆盖、精读数量、稿件语言和中文文献策略。 | `literature/literature_params.json`、阅读队列 |
| T3 阅读 | 逐篇阅读并记录能回查的页码或段落证据。 | 仅在关键 PDF/证据不足时决定补充材料或暂停。 | `literature/deep_read_notes/`、`comparison_table.csv` |
| T3.5 综合 | 把文献整理成机制、方法差异、张力和研究缺口。 | 决定是否进入可选综述分支。 | `literature/synthesis.md` |
| T3.6 可选综述 | 在当前证据足够时撰写领域综述；不做综述时会跳过。 | 选择跳过、使用当前语料库，或先做一次定向补检。 | `drafts/survey/` |
| T4 研究方向 | 生成、比较和演化多个可选研究方向。 | 选择推进、优化、再探索，或只查看 Candidate。 | `ideation/` 下的 Candidate Card、评分、证据和谱系 |
| T4.5 新颖性审计 | 检查相似工作和机制差异，把选定方向变成正式研究包。 | 复核新颖性审计结论与必需 baseline。 | `ideation/proposal/research_proposal.md`、`hypotheses.md`、`exp_plan.yaml` |
| T5 外部执行准备 | 把 T4.5 的正式研究包变成外部执行器不能擅自改写的交接。 | 明确仍影响研究边界的设置；可放置已有资源，或让执行器自动准备公开资源。 | `external_executor/handoff_pack.json`、`resources/` |
| T8 写作 | 用已经核验的实验事实写作、审稿和修订。 | 选择写作风格或模板。 | `drafts/`、实验 claim/evidence 文件 |
| T9 投稿 | 审阅、真实编译并生成提交包。 | 只在环境或编译恢复时处理问题。 | `submission/`、最终 PDF 与编译报告 |

最重要的两个边界是：T4.5 把“有潜力的想法”变成“有假设、实验计划和风险边界的研究方案”；T5 不自行跑实验，它只准备和核验执行契约，随后由同一 workspace 中的 Codex、Claude 或人工执行器完成真实工作。

T4.5 成功时，终端会显示“重点研究文件”表，直接指出 proposal、假设、实验计划、贡献/验证映射、停止条件和新颖性审计的路径及其后续用途；这些文件会传递给 T5，不需要靠记忆寻找。

## 运行前须知

- 同一个 workspace 同一时刻只允许一个写入者。不要同时用本地和 Docker 写同一项目。
- API key 可以放在本地 `.env` 或被 Git 忽略的 `config/model_settings.yaml`；不要提交这些文件、workspace、PDF、日志或生成的投稿文件。
- `requirements.txt` / `pyproject.toml` 只管理 Python 依赖。TeX、`latexmk` 与字体由宿主机或 Docker 镜像提供。

## 本地安装

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos
pip install -e .
python -m researchos.cli configure-llm
```

不用 Conda 时：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

T3.6 综述和 T9 投稿需要真实 PDF 时，安装宿主机 TeX，或使用后文 Docker fallback。Ubuntu/Debian：

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

开始长任务前检查配置、依赖、实际选择的 TeX 后端和 provider：

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

尚未 editable install 时可从 checkout 运行：

```bash
PYTHONPATH="$PWD" python -m researchos.cli doctor --workspace ./workspace/project-a
```

## Docker Compose

Docker 与本地模式使用同一个 CLI、状态机、validator 和 workspace 格式。宿主机 `workspace/` 挂载为容器内 `/app/workspace`；提供的镜像已包含 matplotlib、TeX Live、`latexmk`、pdfLaTeX、 XeLaTeX、BibTeX 和中文 TeX 支持。

```bash
python -m researchos.cli configure-llm
mkdir -p workspace
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

容器中的示例：

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace --workspace /app/workspace/project-a \
  --project-id project-a --topic "面向 LLM Agent 的记忆系统"

docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a
```

本地 `latex.default_backend: auto` 依次使用本机 `latexmk`、本机 `tectonic`、允许的 Docker TeX 镜像。Compose 不使用 Docker-in-Docker。详见 [Docker 与 TeX](docs/cn/docker.md)。

## 初始化并运行项目

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "面向 LLM Agent 的记忆系统"

python -m researchos.cli run --workspace ./workspace/project-a
```

`run` 会在重要决策处停在 Human Gate。T2 支持一次自然语言输入阅读覆盖、稿件语言与中文文献策略：

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

确认后的参数会在真正检索前写入 `literature/literature_params.json`。

T3 的强证据阅读仍逐篇处理，因为页码覆盖、section 证据和阅读状态必须逐篇可审计。其后的 abstract sweep 会把多篇相互独立的摘要动态装入适配当前 provider 上下文窗口的调用，再分别写出 `ABSTRACT-ONLY` 笔记。你设定的“摘要轻读 N 篇”是实际笔记数，metadata-only triage 不计入；保留候选不足时系统会从有摘要的 backlog 补位，仍不足就暂停并要求补检，而不会带着缺口进入 T3.5。已有 PDF 的浅层论文会写入 `literature/reading_upgrade_queue.jsonl`，可升级为真实的全文或定向部分阅读；下载 PDF 本身不会提升证据等级。书籍、专著和超过 100 页的长资料默认只按研究问题读取相关章节，并记录页码范围。

## 日常操作：按目的选择命令

绝大多数项目只会反复使用 `status`、`run` 和 `resume`。不要通过手工编辑 `state.yaml` 跳过阶段，也不要把 `run-task` 当作完整流程的快捷方式。

| 你现在想做什么 | 首选命令 | 系统会做什么 |
| --- | --- | --- |
| 确认项目停在哪里、是否需要你的选择 | `status --workspace <项目目录>` | 显示当前阶段、Gate、最近可操作信息和下一步命令。 |
| 新建一个研究项目并从头开始 | `init-workspace`，然后 `run` | 创建新的 workspace 并启动完整流程。 |
| 继续被暂停的同一项目 | `resume --workspace <项目目录>` | 复用已确认选择和已合格文件，不重复完成的模型调用。 |
| 有意从 T2/T3/T4 等阶段重新开始 | `resume --from-task T4` | 先校验该阶段需要的前置材料；通过才安全重入。 |
| 用另一个项目的材料开一个新项目 | `run --from <来源目录> --start-task T4` | 创建独立目标 workspace，只复制声明的上游材料，不合并历史。 |
| 只诊断一个阶段 | `run-task T4` | 只运行这个 task，不自动推进完整主流程。 |
| 浏览或运行独立功能 | `browse-skills` / `run-skill` | 在独立、可恢复的 Skill 会话中工作。 |

### 1. 系统状态与诊断

```bash
# 当前项目停在哪里、为什么暂停、下一步是什么？
python -m researchos.cli status --workspace ./workspace/project-a

# 本机有哪些 workspace 正在运行、暂停或疑似失联？
python -m researchos.cli workspace-status --workspace-root ./workspace

# 检查模型连接、本地 PDF/TeX 环境与状态机配置。
python -m researchos.cli selftest
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli validate-config

# 查看一条运行记录，或校验某阶段已经保存的产物。
python -m researchos.cli trace <run-id> --workspace ./workspace/project-a
python -m researchos.cli validate --task T4 --workspace ./workspace/project-a
```

### 2. 运行完整项目或单个阶段

```bash
# 新建并启动完整项目。
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a --project-id project-a --topic "研究主题"
python -m researchos.cli run --workspace ./workspace/project-a

# 单独诊断一个阶段；不会推动后续主流程。
python -m researchos.cli run-task T4 --workspace ./workspace/t4-debug \
  --from ./workspace/project-a
```

### 3. 恢复、重入与迁移

```bash
# 正常继续：复用已确认选择和已完成产物。
python -m researchos.cli resume --workspace ./workspace/project-a

# 有意从已通过前置校验的阶段重新进入。
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T3
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T4

# 明确重开 T2 时，会先展示现有检索范围/语言参数供确认。
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T2

# 用另一项目的已验证上游材料创建独立的新 workspace。
python -m researchos.cli run --workspace ./workspace/project-b \
  --from ./workspace/project-a --start-task T4

# 在当前 workspace 缺少声明输入时，从另一 workspace 补齐后再重入。
python -m researchos.cli resume --workspace ./workspace/t4-debug \
  --from ./workspace/project-a --from-task T4
```

`resume --from-task` 不会合并历史记录，也不要求你修改 `state.yaml`。`T3.6` 是可选综述决策，`T8` 是写作入口。显式重开 T2 会重新展示当前参数，避免新检索静默沿用过期范围；若旧参数记录缺失，会打开完整参数选择。显式重开 T3 会先展示检索覆盖决策；只要阅读队列还在，缺少旧 `search_log.md` 等摘要不会跳过该选择。

### 4. Skill：独立、可恢复的小型工作流

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01
python -m researchos.cli skill-status --workspace ./workspace/project-a
```

### 5. T5 外部执行：从研究方案到真实实验

完整流程在 T4.5 通过后会自动进入 T5。T5 会确定性编译 handoff 并发布 13 个项目专属执行 Skill，不让模型重写这些控制文件；之后先停在“协议就绪”。“材料准备”只是盘点你已经拥有的资源的可选入口；缺少公开资源时，系统会先自动准备，再进入完整执行器选择。

正常使用时不需要手工启动这些 T5 子节点。T5 会读取并保留 T4.5 的完整 proposal、正式假设、实验计划、新颖性审计和停止条件；它不会重做 T4/T4.5，也不会允许执行器静默改变研究任务、核心机制、必需 baseline、benchmark 范围或论文主张。随机种子使用可审计的稳定默认 ensemble，除非项目已明确声明自己的 seed policy。

```text
T4.5 通过
  -> T5 编译研究交接和项目专属 Skill
  -> 协议确认：区分可自动补齐的资源/运行设置与真正的研究边界变更
  -> 可选本地材料盘点，或让受限执行器自动准备公开资源
  -> 选择 Codex / Claude / 人工执行器
  -> 外部执行写回可审计的实验结果
  -> T8 接收结果并开始论文写作
```

“协议确认”页不是报错页。`ready` 表示可以选择完整实验执行器；`protocol_decision_required` **不**表示你必须手工寻找数据、代码、baseline、benchmark 或权重：选择“让外部执行器自动准备资源”即可。它只运行 Phase A/B，从公开来源检索、固定版本下载、许可证/安全/协议审查和来源记录；随后停止执行器，`resume` 会重新编译 T5。对于不改变既有 T4.5 范围的设置，Phase B 会在 operational-settings 回执中记录精确的 package/version/model/scale，完整执行器可直接消费，无需再填一张人工表。未声明随机种子时使用稳定、可审计的默认 ensemble。`blocked` 才表示最小实验定义确实缺失。只有改变 T4.5 已定义的研究任务、核心机制、必需 baseline 集合、benchmark 范围或 claim/贡献边界时才需人工决定；普通公开资源获取不需要。

```bash
# 仅做 T5 定点诊断，不会启动真实实验。
python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a
python -m researchos.cli run-task T5-SPECIALIZE --workspace ./workspace/project-a
python -m researchos.cli run-task T5-PROTOCOL-GATE --workspace ./workspace/project-a
```

如果你已有资源，可在选择执行器前放在下面的位置；这不是必需前提。没有资源时使用“让外部执行器自动准备资源”，不要先手工下载不明来源仓库。原始数据或下载的仓库不要直接放进 `external_executor/expr/`：

```text
resources/datasets/      数据集
resources/baselines/     baseline 材料
resources/benchmarks/    benchmark、官方评测或协议
resources/repos/         用户提供的代码仓库或压缩包

external_executor/expr/  仅放已经部署、可直接运行的 baseline 或方法资产
```

自动资源准备完成后，停止外部执行器并运行 `resume`；ResearchOS 接收 Phase B 报告、重新编译 T5。协议与材料均就绪后，在执行器 Gate 选择 Codex CLI，再从 **workspace 根目录** 启动：

```bash
cd workspace/project-a
codex
```

```text
请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research-execution/SKILL.md。
```

外部执行完成前必须存在以下四个回传文件：

```text
external_executor/executor_research_report.md
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/report/run_manifest.json
```

Writer Handoff 已完成但没有自动进入 T8 时，且外部执行器已经停止写入，运行 `python -m researchos.cli run-task T8 --workspace ./workspace/project-a` 接收已核验的结果。外部执行器仍在写入时，不要在另一终端运行 `resume`、第二个执行器或 `run-task T8`。完整操作契约、A-F 阶段和产物路径见 [T5 外部执行器使用指南](docs/cn/t5_external_executor.md)。

跨项目复用已验证上游材料时，创建新的目标 workspace 后使用 `run --from <source-workspace> --start-task <task>`。它不是两个项目的合并操作。恢复细节见 [快速开始](docs/cn/QUICKSTART.md)。

同一 workspace 的明确重入使用 `resume --from-task <task>`。该命令会先校验目标 task 的前置 artifact，再清除旧 pending gate，并在 `state.yaml` 中记录重入原因；不需要、也不允许用户手工修改状态文件。T2/T3 是面向研究者的例外：重入时会优先恢复参数或覆盖决策，覆盖 Gate 允许用保存的阅读队列检查并修复历史摘要缺失。T3.6 已完成但需要直接进入 Idea 时使用 `--from-task T4`。若 T4 前置材料不完整，命令会在启动模型前退出并说明缺项，不会用“跳过”掩盖缺失证据。

| workspace 情况 | 使用方式 | 系统行为 |
| --- | --- | --- |
| 新目录，没有 `state.yaml` | `run` | 创建并启动新的 pipeline。 |
| Ctrl+C、provider 中断或 Gate 后暂停 | `resume` | 从同一 workspace 的已落盘产物继续。 |
| 当前 workspace 需要从已校验的后续阶段重入 | `resume --from-task T4` | 只有目标 task 的前置校验通过才会重入。 |
| 使用另一项目的上游产物 | `run --from <source> --start-task T4` | 创建独立目标 workspace，只复制声明的前置产物。 |
| 已有 `state.yaml`，却再次输入 `run` | 不会继续 | 命令拒绝覆盖或隐式恢复；应使用 `resume`。 |
| 没有 `state.yaml`，却输入 `resume` | 不会创建 | 命令不会伪造项目；应使用 `run`。 |
| `COMPLETED` workspace | 新建 workspace | `resume` 会被拒绝，避免已完成产物被静默重写。 |

综述单节被中断时，应先执行 `validate --task T3.6-SEC-...` 再 `resume`。校验通过的章节会直接 推进，不会再次让模型改写；单节任务只能写自己的 `.tex` 文件和对应的 `survey_state` 条目。

## 完整 CLI 命令参考

<!-- CLI_COMMAND_REFERENCE_START -->

下表是完整命令表，并由 unit test 与真实 `build_parser()` 子命令集合对照检查。任何新命令若未同时 写入中英文 README，测试会失败。除 help 明确例外外，运行类命令都支持 `--workspace`、`--no-color`、 `--verbose`、`--verbosity`、`--quiet`、`--no-banner` 等共享参数。

| 命令 | 用途 | 首选文档 |
| --- | --- | --- |
| `init-workspace` | 初始化标准 workspace，并可创建 `project.yaml`。 | 本 README、[中文快速开始](docs/cn/QUICKSTART.md) |
| `run` | 运行完整状态机；新 workspace 可使用 `--from`、`--start-task`。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `run_smoke` | 运行缩小规模但真实的 pipeline 联调配置。 | `researchos run_smoke --help` |
| `resume` | 继续暂停 workspace，或以 `--from-task` 经校验安全重入。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `run-task` | 单独诊断/执行一个状态机 task，不推进主链。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `status` | 显示单个 workspace 的简明状态、Gate 与下一步；`--detail` 才输出完整 `state.yaml`。 | [中文日志与排障](docs/cn/logging.md) |
| `workspace-status` | 扫描 workspace 根目录，区分活跃、停止、失联、暂停与孤儿 workspace；`--verbose` 显示错误详情。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `configure-llm` | 保存并检查全部阶段共用的 provider、URL、API key、model 与同模型 retry 策略。 | [配置](docs/cn/config.md) |
| `selftest` | 检查已配置 LLM endpoint 连通性。 | 本 README |
| `doctor` | 检查 Python、native/Docker、TeX 前置条件。 | [Docker 与 TeX](docs/cn/docker.md) |
| `trace` | 渲染指定 run 的 trace；`--raw` 输出 JSONL。 | [中文日志与排障](docs/cn/logging.md) |
| `validate` | 校验指定 task 或当前上下文的声明 artifact。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `audit-survey` | 无模型重建 T3.6 Survey 审计。 | [中文日志与排障](docs/cn/logging.md) |
| `validate-config` | 校验状态机、Gate、runtime 和配置契约。 | [配置](docs/cn/config.md) |
| `specialize-executor-skills` | 生成或校验项目专属 T5 external-executor Skill suite。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `run-skill` | 启动/恢复引导式 Skill；支持 `--session-id`、`--resume` 和非交互运行。 | [Skill 指南](docs/cn/skills.md) |
| `list-skills` | 列出所有可发现 Skill 与其声明能力。 | [Skill 指南](docs/cn/skills.md) |
| `browse-skills` | 用终端卡片浏览并选择 Skill。 | [Skill 指南](docs/cn/skills.md) |
| `describe-skill` | 显示一个 Skill 的输入、输出、恢复和能力契约。 | [Skill 指南](docs/cn/skills.md) |
| `skill-status` | 查看可恢复 Skill 会话与集成 workflow phase。 | [Skill 指南](docs/cn/skills.md) |

<!-- CLI_COMMAND_REFERENCE_END -->

## 文件读取与上下文预算

`read_file` 不是固定只能读 200 或 3000 字符的工具。创建上下文敏感工具前，ResearchOS 会并发查询当前 provider 的直接模型记录和模型列表，并同时兼容带或不带 `/v1` 的 OpenAI 风格端点。只有返回记录与配置 model 匹配，且公开了 `context_length`、`context_window`、`max_context`、`max_input_tokens` 或等价嵌套容量字段时，才把该值作为有效容量。该结果在本次运行中缓存，并被文件读取、历史截断和摘要批处理共同使用。若服务端不公开可核验容量，系统默认回退到 `128k` token，不需要在命令中手工传入；它仍不是对 provider 公共 API 极限的声称。单模型配置下，task/state-machine 的 context override 不会改变用户看到的有效容量，系统以当前 provider 的实际能力为准。

有效容量会预留 system prompt、历史消息和后续 Tool 调用空间，并将余量的 70% 分给单次文件结果；文件只有在自动 整篇读取预算内才会一次返回，超过该比例即自动分页。公开的 `read_file` schema 只暴露 `path` 与 `offset`，刻意不接受人工或模型指定的 `max_chars`，因此不能再用 `max_chars=200` 把阅读拆成大量微小调用。需要看已知局部时，先用 `grep_search` 找到 offset，再以该 offset 调用 `read_file`；结果元数据会记录本次容量来源和分页预算。T2 读取大型 `literature/papers_raw.jsonl` 时保留完全读取能力，但会在超过 T2 专项页预算时使用检查点安全分页；页面在 JSONL 记录边界结束，并保留已完成 query、来源覆盖、raw 数量和唯一正确的下一页 `next_offset`，避免大页挤掉检索状态后重复扩 query 或重跑检索。

## 引导式 Skill

系统同时提供原子 Skill 与集成式、可恢复的研究工作流。原子 Skill 覆盖 PDF/DOI 导入、文献卡、 证据矩阵、Idea、论文撰写、审稿、润色、编译和投稿检查；集成 Skill 为多阶段任务增加阶段状态、 证据 Gate、产物清单和恢复点：

| 需求 | 集成 Skill | 工作内容 |
| --- | --- | --- |
| 一键领域综合 | `domain-synthesis-studio` | 范围 -> 可选补检 -> 方法/机制/张力综合 -> Survey 或 Idea 决策 |
| 一键文献比较 | `literature-comparison-studio` | DOI/PDF 解析 -> section 取证 -> 比较矩阵与可主张边界 |
| 一键文献综述工作台 | `literature-review-studio` | 综述范围 -> 检索 -> 阅读覆盖 -> taxonomy 就绪 -> Survey handoff |
| Survey 写作前准备 | `survey-evidence-package` | 语料充分性 -> taxonomy/故事线 -> 补检决策 -> T3.6 handoff |
| 一键跨域 Idea | `cross-domain-idea-studio` | Bridge 证据 -> 迁移风险审计 -> 候选治理 -> 人工选择 |
| 多篇论文阅读 | `paper-reading-workbench` | DOI/PDF 导入 -> 优先队列/文献卡 -> 定向问答 -> 跨论文学习 |
| 写作证据构建/修复 | `related-work-builder`、`draft-evidence-repair` | Related Work 或草稿 claim/citation 修复包 |

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a

python -m researchos.cli run-skill domain-synthesis-studio \
  "综合该领域并判断是否适合撰写 Survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli run-skill literature-review-studio \
  "准备关于可信 LLM agent memory 方法的英文综述" \
  --workspace ./workspace/project-a --session-id agent-memory-survey
```

TTY 终端中的 `run-skill` 会对缺失材料进行多轮收集，只把人工输入或上传内容暂存到 `user_inputs/<skill>/`；重新检查材料后，必须明确输入“执行”或“暂停”才开始 Skill。缺少输入时 不会写论文、实验或引用最终产物。受限 intake Agent 可以调用 provider 逐项提问，并只把人工提供的 内容整理到声明的输入文件。自动化/管道运行请显式加 `--non-interactive`，它会保留 `WAITING_INPUT` 可恢复会话，且不会创建 provider client。

集成 Skill 会在同一 session 文件中保存每个研究阶段的 `pending`、`running`、`completed`、 `waiting_input`、`waiting_evidence` 或 `skipped` 状态。研究者授权后，系统可以用带来源的 检索工具尝试补齐文献；但检索线索、metadata 和摘要仍会显式保留为弱证据，不能因为“找到了更多 记录”就升级为机制或强学术主张。

目录中展示的每个输入/输出路径都会在 Skill 发现阶段与该 Skill 的读写权限交叉校验，因此不会出现 “界面提示可以使用某文件、运行后却 `access_denied`”的契约漂移。

`pdf-note-card`、`paper-comparison` 与 `literature-comparison-studio` 的 guided intake 还支持用户明确给出的 DOI、arXiv/OpenAlex ID、直接 PDF URL、精确标题，或“主题 + 目标篇数”的有边界检索请求。系统只会把下载内容放到对应 Skill 声明的输入目录，并把标识/检索式、使用工具、落盘路径和访问结果写入 `user_inputs/<skill>/_source_resolution.md`；metadata 或搜索命中不会被冒充为已阅读全文。缺少材料后的控制项中，输入 `2` 或“暂停”会立即持久化为 `WAITING_INPUT` 并返回终端，不会自动开始下一轮 intake。

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 \
  --resume
```

完整能力与输入契约见 [Skill 指南](docs/cn/skills.md)。

## 实验协议边界

系统不是按名称禁用 AUUC、Qini、accuracy、F1、某个 benchmark 或 baseline。只要用户材料、 已审计 workspace Artifact 或已验证计划明确给出这些内容并可追溯来源，系统可以使用和审计它们。 禁止的是从研究主题、方法名、学科惯例或示例中把它们凭空固定为“当前项目协议”。缺失时必须标为 `unknown`、`proposed_not_verified` 或等待人工/证据补充。

## CLI 展示与调试

每个实际 CLI 命令默认都会显示一次 `DIG · BUAA` / ResearchOS 启动页。交互终端显示彩色的 `D -> DI -> DIG` 动画；非 TTY 会显示可复制的静态面板。

- `--no-banner`：脚本或 CI 中关闭启动页。
- `--no-color`：关闭 ANSI 颜色但保留同等信息。
- `--verbosity concise|normal|detailed`：控制科研过程展示密度。
- `--quiet`：仅保留关键状态、暂停、错误和最终结果。
- `--json-events`：额外向 stdout 镜像结构化事件；每次运行仍会写 `<workspace>/_runtime/events/<run-id>.jsonl`。

控制台展示阶段输入、计算、决策、风险和 Artifact Manifest，不会暴露模型私有思维或完整 prompt。日志和 trace 排障见 [日志与排障](docs/cn/logging.md)。

## 文档导航

| 需要了解 | 文档 |
| --- | --- |
| 首次运行与恢复 | [快速开始](docs/cn/QUICKSTART.md) |
| 阶段、分支与 Artifact | [流程总览](docs/cn/agent_pipeline.md) |
| 完整流程、命令与产物契约 | [完整流程说明](docs/cn/agent_pipeline_detail.md) |
| 配置 | [配置](docs/cn/config.md) |
| 本地、Docker 与 TeX | [Docker 与 TeX](docs/cn/docker.md) |
| T5 外部执行与 Codex/Claude 交接 | [T5 外部执行器使用指南](docs/cn/t5_external_executor.md) |
| Runtime、事件与扩展 | [运行时架构](docs/cn/runtime.md) |
| Skills | [Skill 指南](docs/cn/skills.md) |
| 日志、trace 与排障 | [日志与排障](docs/cn/logging.md) |
| 仓库与 workspace 目录 | [项目结构](docs/cn/project_structure.md) |
| 开发与测试 | [开发指南](docs/cn/dev.md) |
