# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS 是以 workspace 产物为事实源的多智能体科研运行时，用于文献检索与阅读、证据约束的 Idea 生成、外部实验交接、论文写作与审阅、真实编译和投稿包生成。系统不依赖模型记忆上一轮对话； 每个阶段都有落盘文件、校验、日志与恢复语义。

```text
T1 研究范围 -> T2 文献 -> T3 阅读 -> T3.5 综合
  -> 可选 T3.6 综述 -> T4 Idea -> T4.5 新颖性
  -> T5 外部执行 -> T8 论文 -> T9 投稿包
```

## 从这里开始

先按你的目标选择一条路径。不要为了跳到某个阶段而手工编辑 `state.yaml`，也不要在已有 `state.yaml` 的目录中再次使用 `run`。

| 你的情况 | 首个命令 | 接下来会发生什么 | 不要做什么 |
| --- | --- | --- | --- |
| 从零开始一个研究项目 | `init-workspace`，然后 `run` | 建立独立 workspace，从 T1 开始；系统会在研究范围与文献参数等 Gate 停下。 | 不要先创建或修改 `state.yaml`。 |
| 同一项目因 Ctrl+C、Gate 或服务中断暂停 | `resume --workspace <目录>` | 从当前持久化状态继续；T2/T3 会给出“按当前范围继续或修改”的轻量确认。 | 不要再运行 `run`，也不要并发启动第二个终端。 |
| 需要有意回到 T2/T3/T4 | `resume --from-task T2` 等 | 重新进入指定研究决策面；T2 会完整重选参数，T3 会先复查检索覆盖。 | 不要把 `run-task` 当作这个用途的替代。 |
| 用另一个项目的材料建立新项目 | `run --from <来源> --start-task T2` 等 | 复制声明的上游材料到新 workspace；T2/T3 会先进行本次迁移自己的参数/覆盖选择。 | 不会合并来源项目的 `state.yaml`、历史或运行时日志。 |
| 只完成一个独立能力，例如论文卡或领域综合 | `browse-skills`，再 `run-skill` | 建立独立、可恢复的 Skill 会话；输入、输出和恢复路径由该 Skill 的契约决定。 | 不要直接运行仅由 pipeline/T5 拥有的内部 Skill。 |
| T5 已到外部执行器选择 | 在 Gate 选执行器，然后按 `external_executor/AGENTS.md` 操作 | 外部执行器在同一 workspace 中准备资源、实现、实验、诊断和回传证据。 | 执行器写入期间，不要对同一 workspace 运行 `resume`、第二个执行器或 T8。 |

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
| T4.5 新颖性审计 | 检查相似工作和机制差异，把选定方向变成正式研究包。 | 复核新颖性审计结论与必需 baseline。 | `ideation/proposal/research_proposal.md`、`ideation/hypotheses.md`、`ideation/exp_plan.yaml` |
| T5 外部执行准备 | 把 T4.5 的正式研究包变成外部执行器不能擅自改写的交接。 | 明确仍影响研究边界的设置；可放置已有资源，或让执行器自动准备公开资源。 | `external_executor/handoff_pack.json`、`resources/` |
| T8 写作 | 用已经核验的实验事实写作、审稿和修订。 | 选择写作风格或模板。 | `drafts/`、实验 claim/evidence 文件 |
| T9 投稿 | 审阅、真实编译并生成提交包。 | 只在环境或编译恢复时处理问题。 | `submission/`、最终 PDF 与编译报告 |

最重要的两个边界是：T4.5 把“有潜力的想法”变成“有假设、实验计划和风险边界的研究方案”；T5 不自行跑实验，它只准备和核验执行契约，随后由同一 workspace 中的 Codex、Claude 或人工执行器完成真实工作。

T4.5 成功时，终端会显示“重点研究文件”表，直接指出 proposal、假设、实验计划、贡献/验证映射、停止条件和新颖性审计的路径及其后续用途；这些文件会传递给 T5，不需要靠记忆寻找。

自己复核 T4.5 结果时，建议按下列顺序阅读。它们是研究计划和可反驳性契约，不是已经得到的实验结论。

| 先看什么 | 为什么重要 | T5 如何使用 |
| --- | --- | --- |
| `ideation/proposal/research_proposal.md` | 完整研究叙事：问题、机制、理论与现实含义、贡献、研究设计、风险和局限。 | 保留结构化控制文件背后的研究意图与边界。 |
| `ideation/hypotheses.md` | 可反驳的中心/支持假设、前提、预期观察、竞争解释。 | 防止执行器把假设改写成已经观察到的结果。 |
| `ideation/exp_plan.yaml` | 已规划任务、指标、必需 baseline、已知数据集/benchmark 与评价规则。 | 成为实验执行的核心约束。 |
| `ideation/contribution_hypothesis_map.yaml` 与 `ideation/validation_map.yaml` | 每个贡献依赖哪条假设，以及什么证据能验证或反驳。 | 成为实现和 T8 写作时的 claim/evidence 边界。 |
| `ideation/kill_criteria.yaml` | 收窄、停止或否决主张的条件。 | 保持负结果与失效路径可见。 |
| `ideation/novelty_audit.md` | 相似工作 collision、机制差异、必需 baseline 与未解决缺口。 | 定义比较与新颖性边界，未解决缺口不会被隐藏。 |

## Workspace 是项目事实源

每个项目都在一个独立 workspace 中运行。聊天记录、终端滚屏和模型记忆都不是项目事实；恢复、下游阶段和审计只读取 workspace 内的产物。下面列出用户最常需要查看或放置材料的位置。

| 位置 | 谁写入 / 谁读取 | 你应在什么时候打开或放入内容 |
| --- | --- | --- |
| `project.yaml` | T1 写入；所有阶段读取 | 查看研究问题、范围、目标 venue 和初始约束。不要手改它来跳过阶段。 |
| `user_seeds/` | 你提供；T1/T2/T3 读取 | 放种子 PDF、DOI、想法和约束。已有材料可在启动前放入。 |
| `literature/` | T2/T3 与文献 Skill 写入；T3.5-T5 读取 | 查看检索记录、阅读队列、深读/摘要笔记、综合、资源线索。 |
| `ideation/` | T4/T4.5 写入；T5 读取 | 查看 Candidate、proposal、正式假设、实验计划、新颖性审计和停止条件。 |
| `resources/` | 你或 T5 Phase B 放入；外部执行器读取 | 放你已有的数据集、代码、benchmark、baseline 或权重的原始材料。 |
| `external_executor/` | T5 与外部执行器写入；T8 读取 | 保存执行交接、项目专属 Skill、可运行代码、原始结果、图表、证据包与回传报告。 |
| `drafts/` | T3.6/T8 写入 | 查看综述、论文草稿、写作交接和结果到主张映射。 |
| `submission/` | T9 写入 | 查看最终编译、审阅与投稿包。 |
| `user_inputs/<skill>/` | Skill intake 暂存你提供的内容 | 仅在某个独立 Skill 要求补材料时使用；路径由 `describe-skill` 明确展示。 |
| `_runtime/` | 系统写入 | 保存 state、事件、trace、日志、Skill session 与恢复记录。排障时读取，正常研究时不要手工修改。 |

同一 workspace 同一时刻只能有一个写入者。可以在另一个终端运行只读的 `status`、`trace` 或查看文件；不要同时让两个 `run`/`resume`/`run-skill`/外部执行器写入同一目录。

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
| 继续被暂停的同一项目 | `resume --workspace <项目目录>` | 复用已确认选择和已合格文件；T2/T3 Gate 的“继续”不会重检索。 |
| 有意从 T2/T3/T4 等阶段重新开始 | `resume --from-task T4` | 先校验该阶段需要的前置材料；T2/T3 会先回到研究范围决策。 |
| 用另一个项目的材料开一个新项目 | `run --from <来源目录> --start-task T4` | 创建独立目标 workspace，只复制声明的上游材料，不合并历史。 |
| 只诊断一个阶段 | `run-task T4` | 只运行这个 task，不自动推进主流程；除 T8 接收外部 handoff 外，不替代完整 pipeline。 |
| 浏览或运行独立功能 | `browse-skills` / `run-skill` | 在独立、可恢复的 Skill 会话中工作；先读输入/输出契约。 |

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

# 显式重开 T2：完整选择本次覆盖参数，不沿用旧范围。
python -m researchos.cli resume --workspace ./workspace/project-a --from-task T2

# 用另一项目的已验证上游材料创建独立的新 workspace。
# 迁移到 T2 会完整选参；迁移到 T3 会先复查阅读队列与覆盖范围。
python -m researchos.cli run --workspace ./workspace/project-b \
  --from ./workspace/project-a --start-task T2

python -m researchos.cli run --workspace ./workspace/project-c \
  --from ./workspace/project-a --start-task T3

# 在当前 workspace 缺少声明输入时，从另一 workspace 补齐后再重入。
python -m researchos.cli resume --workspace ./workspace/t4-debug \
  --from ./workspace/project-a --from-task T4
```

`resume --from-task` 不会合并历史记录，也不要求你修改 `state.yaml`。`T3.6` 是可选综述决策，`T8` 是写作入口。普通 `resume` 回到 T2/T3 时会先展示“继续当前范围或修改”的轻量 Gate；确认继续不发起新检索。显式重开或 `--from` 迁移 T2 会打开完整参数选择，避免新的检索边界静默沿用旧范围。显式重开或迁移 T3 会先展示检索覆盖决策；只要阅读队列还在，缺少旧 `search_log.md` 等摘要不会跳过该选择。

| 入口 | 目标是 T2 时的首个 Gate | 目标是 T3 时的首个 Gate | 是否保留现有论文/笔记 |
| --- | --- | --- | --- |
| `resume --workspace <同一项目>` | 参数确认：继续或修改 | 覆盖确认：继续、补检或调整 | 是 |
| `resume --from-task T2/T3` | 完整参数选择 | 有队列则覆盖确认；无队列则参数选择 | 是 |
| `run --from <来源> --start-task T2/T3` | 完整参数选择 | 有队列则覆盖确认；无队列则参数选择 | 来源材料复制到新项目；来源不变 |
| `resume --from <来源> --from-task T2/T3` | 完整参数选择 | 有队列则覆盖确认；无队列则参数选择 | 目标保留；只补齐缺失声明输入 |

### 4. Skill：独立、可恢复的小型工作流

Skill 不是 T1-T9 的别名。它是一个可单独发起、带明确读写权限和 session 的能力，用于完成一个聚焦任务，例如生成论文卡、比较多篇论文、构建领域综合或修复 Related Work。完整 pipeline 仍由 `run`/`resume` 控制；T5 内部的 project-specific executor Skill 由状态机和外部执行器控制，不能用 `run-skill` 绕过。

| 命令 | 先做什么 | 结果在哪里 | 什么时候使用 |
| --- | --- | --- | --- |
| `list-skills` | 列出可由用户直接启动的 standalone Skill。 | 只读终端目录。 | 不知道名称或能力时。 |
| `browse-skills` | 按类别/关键词浏览，并可从终端选择。 | 只读终端目录。 | 想按目标找工具时。 |
| `describe-skill <名称>` | 显示任务说明、必需/可选输入、允许路径、输出、示例和恢复方式。 | 只读终端契约。 | **每次首次使用某 Skill 前。** |
| `run-skill <名称> "任务"` | 创建或继续 session，检查输入；交互终端会先收集缺失材料。 | `user_inputs/<skill>/`、该 Skill 声明的输出、`_runtime/skill_sessions/<id>.json`。 | 已确认 Skill 适合任务时。 |
| `skill-status` | 查看 session、阶段状态、文件检查和恢复命令。 | 只读 session 状态。 | 中断后、等待材料时或想确认结果时。 |

```bash
# 先发现与阅读契约。list 只列用户可直接运行的 standalone Skill。
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a

# 运行一个原子 Skill；session-id 用于恢复。同名并行任务必须用不同 ID。
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01 "为这篇 PDF 建立可追溯阅读卡"

# 中断、等待材料或等待确认后，用同一 ID 恢复。
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a \
  --session-id reading-01 --resume

# 查看 session 实际读取/写入了什么，以及当前下一步。
python -m researchos.cli skill-status --workspace ./workspace/project-a
```

交互式 `run-skill` 的生命周期是：**任务说明 -> 输入检查 -> 缺失材料 intake -> 显式“执行”确认 -> 写入声明输出 -> `skill-status` 可恢复检查**。缺少必需输入时，系统只会写入 `user_inputs/<skill>/` 和 session 记录，不会伪造论文、实验、引用或完成状态。`--non-interactive` 适用于脚本：缺输入时保存 `WAITING_INPUT`，不创建 provider client；随后补材料后以相同 `--session-id ... --resume` 恢复。

常见 Skill 的准确定位如下。真实的输入路径、输出路径和是否允许自动补检始终以 `describe-skill` 的当前输出为准，因为每个 Skill 的契约不同。

| 你要完成的事 | 优先 Skill | 典型输入 | 典型输出 / 不能替代什么 |
| --- | --- | --- | --- |
| 对一篇 PDF 做可追溯结构化阅读 | `pdf-note-card` | PDF、DOI、标题或明确来源。 | `literature/skill_pdf_note_cards/`；不替代 T3 的 canonical deep-read queue。 |
| 比较 DOI/PDF 的方法、结果与可主张边界 | `literature-comparison-studio` | 一组 DOI/PDF/明确题目。 | 比较矩阵和证据边界；不自动把摘要升级为全文证据。 |
| 从问题出发综合一个领域 | `domain-synthesis-studio` | 领域问题、范围和后续使用目的。 | 领域报告、机制/张力图和 handoff；可在授权下补检。 |
| 准备系统性综述的证据包 | `literature-review-studio` 或 `survey-evidence-package` | 综述范围、语言、时间边界、已有语料。 | taxonomy/覆盖/Survey handoff；不直接替代 T3.6 的正式写作与 TeX 编译。 |
| 比较多篇论文并持续问答 | `paper-reading-workbench` | DOI/PDF、阅读问题或主题范围。 | 优先级卡和跨论文学习记录；不改变完整 pipeline 状态。 |
| 生成或审计写作证据 | `related-work-builder`、`draft-evidence-repair` | 草稿、主张、引用或已有笔记。 | 可追溯写作包；不能编造 citation 或实验结果。 |

更多 integrated workflow、输入示例、状态含义和权限边界见 [Skill 指南](docs/cn/skills.md)。

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

`proposed_not_verified` 是**主张验证状态**，不是资源问题或 T5 报错。它表示中心假设或贡献有意保持为待外部实验验证的研究命题。文献背景可以是 `source_supported`，已发现资源可以只是线索；这些状态必须分开，避免把预期结果写成已观察到的结果。

| 你在 T5 看到的情况 | 你应选择或执行什么 | 系统会做什么 | 不应做什么 |
| --- | --- | --- | --- |
| `ready`，且你已有数据/代码/权重 | “先盘点已有实验材料” | 只盘点 `resources/` 与已部署的 `external_executor/expr/`，随后继续执行器选择。 | 不要把原始下载包直接当作可运行资产放进 `expr/`。 |
| `protocol_decision_required`，或手头没有资源 | “让外部执行器自动准备资源” | 受限 Phase A/B 检索公开资源、固定版本、做许可证/安全/协议审查并记录来源；不跑正式实验。 | 不需要先人工寻找公开数据、repo 或 benchmark。 |
| 需要换研究问题、机制、必需 baseline、benchmark 范围或 claim 边界 | 选择协议重编译或返回 T4 重构 | 保留当前材料，并把真正的研究边界变更显式纳入上游决策。 | 不要让执行器或手工编辑 handoff 静默改变这些内容。 |
| 已到执行器选择 | 选择 Codex CLI、Claude Code 或人工执行器 | 系统保留 handoff、allowed paths、输出 schema 和项目专属 Skill。 | 不要把 mock/dry-run 当作真实实验事实。 |
| 显示“等待外部执行器回传” | 在同一 workspace 完成外部执行，写回要求的结果包。 | T8 会检查真实文件、manifest 和报告引用。 | 外部执行器写入时不要运行 `resume`、第二个执行器或 `run-task T8`。 |

```bash
# 仅做 T5 定点诊断，不会启动真实实验。
python -m researchos.cli run-task T5-REBOOST --workspace ./workspace/project-a
# 规范状态机名称；短别名 T5-SPECIALIZE 也可接受。
python -m researchos.cli run-task T5-SPECIALIZE-EXECUTOR-SKILLS --workspace ./workspace/project-a
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

### 迁移与恢复的进阶保证

前面的恢复矩阵用于日常决策；下面说明少见但重要的规则，不改变该矩阵的用法：

- `run --from <source-workspace> --start-task <task>` 从另一项目声明且已验证的上游产物初始化**新的**目标 workspace，绝不合并 state 或历史。`run-task <task> --from <source-workspace>` 复制相同的声明输入，但只运行一个用于诊断的 task。
- 对依赖文献的下游阶段（`T3.5`、`T3.6`、`T4`、`T5`、`T8`），导入闭包包含完整 `literature/` 树。导入的空目录不会遮蔽真实笔记卡；已声明时会同时保留 `bridge_notes/` 与 `cross_domain_catalogs/`。
- 导入在模型连接检查之前完成，因此 provider 故障不会丢弃已准备好的调试 workspace。`resume --from <source>` 只向现有目标补齐缺失的声明输入，也不会合并来源 state/history。
- 有 `state.yaml` 的 workspace 必须用 `resume`；没有它的目录必须用 `run`；`COMPLETED` workspace 会拒绝 `resume`，因为已完成产物不能被静默重写。
- 公共别名可用：`T3.6` 打开 Survey 决策，`T5-SPECIALIZE` 等价于 `T5-SPECIALIZE-EXECUTOR-SKILLS`，`T8` 打开写作风格 Gate。可选 Survey 已完成后，只有 T4 前置仍通过时，`resume --from-task T4` 才会进入 Idea 工作。
- 综述单节中断时，先校验该章节再恢复。有效的 `T3.6-SEC-*` 输出会直接推进，不会改写已完成的 `.tex` 文件及其对应的 `survey_state` 条目。

恢复示例见 [中文快速开始](docs/cn/QUICKSTART.md)，trace 排障见 [中文日志与排障](docs/cn/logging.md)。

## 完整 CLI 命令参考

<!-- CLI_COMMAND_REFERENCE_START -->

下表是完整命令表，并由 unit test 与真实 `build_parser()` 子命令集合对照检查。任何新命令若未同时 写入中英文 README，测试会失败。除 help 明确例外外，运行类命令都支持 `--workspace`、`--no-color`、 `--verbose`、`--verbosity`、`--quiet`、`--no-banner` 等共享参数。

| 命令 | 用途 | 首选文档 |
| --- | --- | --- |
| `init-workspace` | 初始化标准 workspace，并可创建 `project.yaml`。 | 本 README、[中文快速开始](docs/cn/QUICKSTART.md) |
| `run` | 运行完整状态机；新 workspace 可使用 `--from`、`--start-task`。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `run_smoke` | 运行缩小规模但真实的 pipeline 联调配置。 | `researchos run_smoke --help` |
| `resume` | 继续暂停 workspace，或以 `--from-task` 经校验安全重入。 | [中文快速开始](docs/cn/QUICKSTART.md) |
| `run-t8` | `run-task T8` 的兼容别名；接收现代 T5 Writer Handoff 并运行完整 T8 链。 | [中文快速开始](docs/cn/QUICKSTART.md) |
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

## Skill 的进阶边界与集成工作流

[Skill 指南](docs/cn/skills.md) 是完整契约参考。本节保留在选择集成工作流或理解证据状态时有用的进阶行为。系统同时提供原子 Skill 与集成式、可恢复的研究工作流。原子 Skill 覆盖 PDF/DOI 导入、文献卡、证据矩阵、Idea、论文撰写、审稿、润色、编译和投稿检查；集成 Skill 为多阶段任务增加阶段状态、证据 Gate、产物清单和恢复点：

| 需求 | 集成 Skill | 工作内容 |
| --- | --- | --- |
| 一键领域综合 | `domain-synthesis-studio` | 范围 -> 可选补检 -> 方法/机制/张力综合 -> Survey 或 Idea 决策 |
| 一键文献比较 | `literature-comparison-studio` | DOI/PDF 解析 -> section 取证 -> 比较矩阵与可主张边界 |
| 一键文献综述工作台 | `literature-review-studio` | 综述范围 -> 检索 -> 阅读覆盖 -> taxonomy 就绪 -> Survey handoff |
| Survey 写作前准备 | `survey-evidence-package` | 语料充分性 -> taxonomy/故事线 -> 补检决策 -> T3.6 handoff |
| 一键跨域 Idea | `cross-domain-idea-studio` | Bridge 证据 -> 迁移风险审计 -> 候选治理 -> 人工选择 |
| 多篇论文阅读 | `paper-reading-workbench` | DOI/PDF 导入 -> 优先队列/文献卡 -> 定向问答 -> 跨论文学习 |
| 写作证据构建/修复 | `related-work-builder`、`draft-evidence-repair` | Related Work 或草稿 claim/citation 修复包 |

集成 Skill 会在同一 session 文件中保存每个研究阶段的 `pending`、`running`、`completed`、`waiting_input`、`waiting_evidence` 或 `skipped` 状态。研究者授权后，系统可以用带来源的检索工具尝试补齐文献；但检索线索、metadata 和摘要仍会显式保留为弱证据，不能因为“找到了更多记录”就升级为机制或强学术主张。

`pdf-note-card`、`paper-comparison` 与 `literature-comparison-studio` 的 guided intake 支持用户明确给出的 PDF、DOI、arXiv/OpenAlex ID、直接 PDF URL、精确标题，或“主题 + 目标篇数”的有边界检索请求。系统只会把下载内容放到对应 Skill 声明的输入目录，并把标识/检索式、使用工具、落盘路径和访问结果写入 `user_inputs/<skill>/_source_resolution.md`；metadata 或搜索命中不会被冒充为已阅读全文。目录中展示的每个输入/输出路径都会在 Skill 发现阶段与该 Skill 的读写权限交叉校验，因此不会出现“界面提示可以使用某文件、运行后却 `access_denied`”的契约漂移。

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
