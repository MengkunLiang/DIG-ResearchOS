# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS 是以 workspace 产物为事实源的多智能体科研运行时，用于文献检索与阅读、证据约束的 Idea 生成、外部实验交接、论文写作与审阅、真实编译和投稿包生成。系统不依赖模型记忆上一轮对话； 每个阶段都有落盘文件、校验、日志与恢复语义。

```text
T1 研究范围 -> T2 文献 -> T3 阅读 -> T3.5 综合
  -> 可选 T3.6 综述 -> T4 Idea -> T4.5 新颖性
  -> T5 外部执行器交接 -> T7 证据与 Claim
  -> T8 论文 -> T9 投稿包
```

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

T3 的全文精读仍逐篇处理，因为页码覆盖、section 证据和阅读状态必须逐篇可审计。其后的 abstract sweep 则会在 provider 返回可用上下文窗口时，把多篇相互独立的摘要动态装入一次调用， 再分别写出一张 `ABSTRACT-ONLY` 笔记。系统不设置“每批固定几篇”的上限；分组由当前模型 binding 的 context window 与同一模型 tokenizer 决定。批次输出损坏时，只回退受影响的论文， 不会混淆为全文证据。

## 日常命令

| 目标 | 命令 |
| --- | --- |
| 查看当前阶段与暂停原因 | `python -m researchos.cli status --workspace ./workspace/project-a` |
| 总览全部本机 workspace、活跃进程、Gate 与疑似失联状态 | `python -m researchos.cli workspace-status --workspace-root ./workspace` |
| 继续暂停项目 | `python -m researchos.cli resume --workspace ./workspace/project-a` |
| 在当前 workspace 校验前置产物后从 T4 重入 | `python -m researchos.cli resume --workspace ./workspace/project-a --from-task T4` |
| 从另一项目的已验证上游产物新建完整 T4 流程 | `python -m researchos.cli run --workspace ./workspace/project-b --from ./workspace/project-a --start-task T4` |
| 从另一项目复制前置材料，只调试 T4 | `python -m researchos.cli run-task T4 --workspace ./workspace/t4-debug --from ./workspace/project-a` |
| 只跑一个 task | `python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| 校验一个 task 的产物 | `python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a` |
| 无模型重跑 Survey 覆盖审计 | `python -m researchos.cli audit-survey --workspace ./workspace/project-a` |
| 查看已记录 run | `python -m researchos.cli trace <run-id> --workspace ./workspace/project-a` |
| 检查环境和 TeX | `python -m researchos.cli doctor --workspace ./workspace/project-a` |
| 检查系统配置 | `python -m researchos.cli validate-config` |

跨项目复用已验证上游材料时，创建新的目标 workspace 后使用 `run --from <source-workspace> --start-task <task>`。它不是两个项目的合并操作。恢复细节见 [快速开始](docs/cn/QUICKSTART.md)。

同一 workspace 的明确重入使用 `resume --from-task <task>`。该命令会先校验目标 task 的前置 artifact，再清除旧 pending gate，并在 `state.yaml` 中记录重入原因；不需要、也不允许用户手工修改 状态文件。T3.6 已完成但需要直接进入 Idea 时使用 `--from-task T4`。若 T4 前置材料不完整，命令会在 启动模型前退出并说明缺项，不会用“跳过”掩盖缺失证据。

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
| Runtime、事件与扩展 | [运行时架构](docs/cn/runtime.md) |
| Skills | [Skill 指南](docs/cn/skills.md) |
| 日志、trace 与排障 | [日志与排障](docs/cn/logging.md) |
| 仓库与 workspace 目录 | [项目结构](docs/cn/project_structure.md) |
| 开发与测试 | [开发指南](docs/cn/dev.md) |
