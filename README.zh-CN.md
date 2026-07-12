# ResearchOS

[English](README.md) | [中文](README.zh-CN.md)

ResearchOS 是一个以落盘产物为中心的多智能体研究运行时。它把一个 workspace 组织成可审计、可恢复的研究项目：文献检索与阅读、证据约束的 idea 生成、外部实验交接、论文写作与审阅，以及投稿包生成。

系统不依赖模型记住上一次对话。workspace 中的文件才是事实源：每个阶段都有确定的输入/输出、校验、日志、trace 和恢复语义。

```text
T1 选题 -> T2 文献 -> T3 阅读 -> T3.5 综合
  -> 可选 T3.6 综述 -> T4 idea -> T4.5 新颖性
  -> T5 外部执行器交接 -> T7 证据与 claim
  -> T8 论文写作/审阅 -> T9 投稿包
```

本文件是主项目 README 的中文版本；英文说明见 [README.md](README.md)。

## 选择运行方式

同一个 workspace 同一时刻只能有一个写入者。

| 模式 | 适用场景 | LaTeX 行为 |
| --- | --- | --- |
| 本地模式 | 开发、调试、直接在宿主机使用 | `auto` 依次选择本机 `latexmk`、本机 `tectonic`、已配置的 Docker TeX 镜像。 |
| Docker Compose | 固定 CLI 环境、部署 | 镜像内置 `latexmk`、pdfLaTeX、XeLaTeX、BibTeX 和中文 TeX 支持，在容器内直接编译。 |

`pyproject.toml` 是 Python 包元数据来源，`requirements.txt` 是与其对应、可供 Docker 缓存安装的 runtime/dev 依赖清单（包括综述数据图所需的 matplotlib）。TeX Live、`latexmk` 与字体是操作系统级依赖，必须由系统包管理器安装，或写入 Docker 镜像；不能也不应放进 `requirements.txt`。

## 本地安装

```bash
git clone <repository-url> DIG-ResearchOS
cd DIG-ResearchOS

conda env create -f environment.yml
conda activate researchos

cp .env.example .env
```

若不使用 Conda，可运行 `pip install -r requirements.txt && pip install -e .`。

T3.6 综述和 T9 投稿需要真实 PDF 时，建议在宿主机安装完整 TeX 工具链。Ubuntu/Debian：

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk \
  texlive-latex-base \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-xetex \
  texlive-lang-chinese
```

macOS 使用 MacTeX，或安装 BasicTeX 后补 `latexmk`。Windows 使用 MiKTeX 或 TeX Live，并确认 `latexmk`、`pdflatex`、`xelatex`、`bibtex` 在 `PATH` 中。

安装后运行以下检查。`doctor` 会探测真正可用的编译后端，而不是只检查命令是否存在：

```bash
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/agentic
python -m researchos.cli selftest
```

如果 editable 安装尚未完成，明确使用当前 checkout：

```bash
PYTHONPATH="$PWD" python -m researchos.cli doctor --workspace ./workspace/agentic
```

## Docker Compose 安装

Docker 模式使用与本地模式完全相同的 CLI、状态机、validator 和 workspace 格式。宿主机的 `workspace/` 会挂载为容器中的 `/app/workspace`，容器退出不会删除项目产物。

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Linux 上若 Docker 默认桥接网络下载镜像依赖很慢，可在构建镜像时使用宿主网络：

```bash
docker build --network=host -t researchos/system:latest -f infra/docker/Dockerfile .
```

这只影响镜像构建速度；实际 LaTeX 编译仍在受限环境中执行且不开放网络。

## 配置并启动项目

API key 等机密只放到 `.env`；非机密运行参数放在 `config/user_settings.yaml` 和 `config/runtime.yaml`。不要将密钥、workspace、PDF 或运行日志提交到 Git。

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "面向 LLM Agent 的记忆系统"

python -m researchos.cli run --workspace ./workspace/project-a
```

重要选择由 human gate 管理。T2 参数可直接输入自然语言，包括检索/阅读数量和语言策略，例如：

```text
候选30篇，精读15篇，粗读15篇，英文稿，不搜索中文文献
```

英文稿并明确排除中文文献时，除用户 seed 外的中文论文不会进入 active pool。中文/双语稿，或明确允许中文文献时，中文候选会保留并接受常规证据和引用质量审查。

只验证 runtime、工具和写文件闭环，而不启动研究流程：

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/project-a
```

## 面向研究者的 CLI 过程可观测性

`run`、`resume` 和 `run-task` 使用同一套 **阶段开始 -> 阶段过程 -> 阶段总结** 协议。
它展示的是可审计的科研活动和阶段判断，不会输出模型私有思维或完整 Tool payload：

- **阶段开始**：显示阶段目标、要回答的研究问题、计划操作，以及输入/预计输出 Artifact 表。每个文件都会注明含义、状态、规模和下游用途。
- **阶段过程**：只展示有研究价值的受限结果。例如 T2 的 query/source 覆盖、候选排序和阅读优先级；T3 的证据覆盖；T3.5 的机制/张力；T4 的来源、补充通道、接地复核和候选治理；T7 的运行/claim 审计；T8/T9 的证据对齐和真实编译状态。
- **阶段总结**：显示主要结论、风险与 unsupported、实际读取的 workspace 文件，以及 Artifact Manifest。产物状态会区分 `created`、`updated`、`reused`、`missing`、`invalid`。

可用展示控制如下。即使是 `concise`，也不会省略阶段输入、输出和需要人工操作的内容。
`--json-events` 只是在 stdout 额外镜像受限 JSON 事件，适合集成工具；**每一次运行**都会把事件持久化到
`<workspace>/_runtime/events/<run_id>.jsonl`，无需开启该参数。

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity concise --no-color
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --json-events
```

终端不是 chain-of-thought 输出。检索覆盖缺口、引用图信号、工具聚类和排序都会明确标为提示或证据边界，只有源 Artifact 支撑时才会升级为更强结论。控制台、日志、trace 和事件 JSONL 的区别见 [docs/logging.md](docs/logging.md)。

交互终端会使用带颜色的 Rich 面板展示阶段标题、Agent Markdown、工具起止/结果、警告和 Artifact Manifest。Agent Markdown 在渲染前会归一化，因此 `1️⃣` 这类 keycap 编号会显示为普通有序列表，不会出现终端中的拆散字符。`--no-color` 保留完全相同的信息，但输出无 ANSI、可直接复制。独立 Skill 也使用同一协议：`list-skills` 按科研流程分组原子能力；`describe-skill`、`run-skill` 和 `skill-status` 显示明确的上传路径、产物含义、会话状态和恢复命令。

## 引导式独立 Skill

Skill 不是 LangChain chain，也不是无法检查的聊天提示词。每个可发现的
`skills/<名称>/SKILL.md` 都会被同一套 runtime 包装为 `SkillAgent`。面向用户的
学术 Skill 会先校验声明的输入契约、写入可恢复会话，再初始化 LLM。

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill paper-outline --workspace ./workspace/project-a
```

`describe-skill` 会完整展示应上传的 workspace 相对路径、可接受格式、输出文件
含义以及恢复命令。例如，将研究简介放在
`user_inputs/paper-outline/brief.md`，然后启动一个独立的大纲会话：

```bash
python -m researchos.cli run-skill paper-outline \
  "为 NeurIPS 风格英文实证论文建立大纲" \
  --workspace ./workspace/project-a \
  --session-id neuri-2026-outline
```

非交互命令缺少必需文件时，会写入
`_runtime/skill_sessions/neuri-2026-outline.json`，明确指出需要上传什么、放到哪里，
并停在可恢复状态。真实终端可加 `--interactive`：受限 intake Agent 会逐项询问上传或粘贴，
仅将人工提供的材料整理到该 Skill 的 `user_inputs/<skill>/`；如果材料仍缺少必要事实，
它会继续提出聚焦问题并重检，通过后才开始真正 Skill。它不会写论文、实验结果或引用。补齐后使用同一会话继续：

```bash
python -m researchos.cli run-skill paper-outline \
  --workspace ./workspace/project-a \
  --session-id neuri-2026-outline \
  --resume

python -m researchos.cli skill-status --workspace ./workspace/project-a
```

每个 guided 会话还会写入 `user_inputs/<skill>/_intake.md`。独立 workspace 中它是可编辑的上传清单；项目 workspace 中它会记录系统发现的候选文件，但“文件存在”不等于材料足以支撑要写的结论。运行中的 Skill 必须做语义核验；若发现缺少目标 venue、证据、引用、结果或约束，会写
`user_inputs/<skill>/_followup_request.md`，提出精确问题和建议补充路径，再等待真实人工回答并用同一 `--session-id ... --resume` 继续。这样多轮交互可追溯，而不会把聊天记忆或文件名当作证据。

独立运行与项目内运行的 Skill 均**不设 token 上限，也不设 step 上限**。它们只会因明确完成、等待人工输入、人工取消、provider/runtime 故障或产物校验结果而结束/暂停；这不意味着可以突破 provider 的上下文窗口、限流、可用性或账户限制。

`browse-skills` 是可在 SSH/普通终端使用的卡片浏览器：输入序号或名称可先看完整契约，直接输入
`文献`、`Idea`、`创新点` 等关键词即可做本地中英文/别名/有限模糊搜索，也可使用 `search <关键词>`；
输入 `run <序号>` 才会进入该 Skill 的引导式会话。带色目录会把每个研究分类框起来，并逐项说明
流程位置、用途、输入、产物和启动命令。Skill 运行时，`skill-status` 会展示已经持久化的
步骤、可观察阶段、当前工具、产物和恢复命令；它不会输出或伪装模型内部思维。

T2 中单个文献来源的限流、连接失败或可选 seed 文件缺失也不会再被一律显示为“阶段失败”：终端会区分
`SKIPPED`（声明为可选的输入未提供）、`DEGRADED`（来源可重试且其它来源继续）和真正 `FAILED`。
阶段总结中的来源健康表会说明哪些来源实际返回了候选、哪些在冷却期。论文卡可被 T4.5、T5、外部执行器、
T7 和 T8 按需使用，但只能用于机制/基线/边界/引用出处，不能当作新方法的实验性能证据。

推荐的原子工作流是：`research-material-ingest`（导入研究者自己的 PDF、数据和代码）→
`research-scope`（范围）→ `paper-identifier-resolver`（DOI/arXiv/标题解析）或
`pdf-note-card`（单篇 PDF 笔记）/`paper-section-evidence`（针对一个问题的 PDF section 取证）→
`citation-graph-explorer`（有边界的一跳引文扩展）→ `paper-comparison` /
`literature-evidence-matrix` / `literature-gap-map`（比较、证据矩阵与缺口治理）→
`literature-query-plan` / `literature-evidence-scout`（检索）→ `paper-note-review`（笔记 section 复核）→
`idea-fanout-jury` / `hypothesis-compiler`（候选与假设）→
`experiment-design-review`（实验设计审查）→ `paper-outline` / `paper-write`（写作）→
`claim-evidence-map` / `citation-library-curator` / `citation-provenance-audit` /
`venue-fit-review` / `paper-peer-review` / `paper-polish` / `paper-revision`（审阅修订）→
`paper-compile` / `submission-readiness`（真实编译与投稿检查）。`survey-visuals` 最多只从
`survey_plan.json` 的显式 taxonomy 和直接关联 paper ID 生成一张可复现 taxonomy PDF；严禁跨论文性能、
相对提升、T2 筛选分数和推断性热图。taxonomy 不足时会写明 `skipped`，绝不编造装饰图。
计划中每一个直接 paper ID 还必须解析到本地结构化 note card；任一 ID 无法解析时会移除旧的 canonical PDF 并写 `skipped` manifest。该检查只核验来源链接可追溯，不等价于论文的实证证据更强。
所有公共 Skill 都保留源文件，只使用 workspace 中可追溯的证据和 citation key，并在输出旁写入
审计报告。完整的原子能力、上传路径和边界见 [docs/skills.md](docs/skills.md)，契约详情见
[docs/runtime.md](docs/runtime.md)，更多示例见 [docs/QUICKSTART.md](docs/QUICKSTART.md)。

## Venue-Aware 论文写作

T8 会将用户确认的模板/风格写到 `drafts/writing_style.json`，并在分章节写作前生成
`drafts/writing_storyline.md`。该文件把问题、rationale 或技术根因、核心洞见、设计选择、claim、证据、替代解释和限制连接为可审计的研究故事。

- UTD/IS/INFORMS 档案强调完整的现象/理论/理由 -> 机制 -> 设计原则 -> 证据 -> 有边界的理论与实践含义。
- NeurIPS、ICML、ICLR、KDD 档案强调简洁的技术瓶颈优先写法：每条贡献都要映射到方法模块和主结果、消融、分析或 failure evidence。
- `config/system_config/venue_writing_profiles.yaml` 只保存内部写作密度目标和审稿问题，**不是**官方页数、匿名规则或当年投稿政策；投稿前必须核对当前官方 CFP/template。

`audit_writing_craft` 会报告已解析档案、章节词数与 storyline 覆盖情况，它们是诊断提示。章节短缺不能用泛泛背景填充，Writer 必须补足有证据的 rationale，或诚实保留 limitation。

## 恢复与重跑

先查看当前阶段和最后的运行原因：

```bash
python -m researchos.cli status --workspace ./workspace/project-a
tail -n 100 ./workspace/project-a/_runtime/logs/researchos.log
```

### 在同一个项目继续

人工 gate、provider 超时、工具/环境修复或进程中断后，使用 `resume`。它保留已通过校验的产物，并从 workspace 重建上下文：

```bash
python -m researchos.cli resume --workspace ./workspace/project-a
```

Docker Compose 内使用容器路径：

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

### 只重跑一个阶段

`run-task` 适合定向调试，不会自动推进完整状态机：

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/project-a
python -m researchos.cli run-task T3.6-COMPILE --workspace ./workspace/project-a
python -m researchos.cli run-task T9 --workspace ./workspace/project-a
```

### 从另一个项目的中间阶段开始

`resume` 不会合并两个项目。要复用另一个 workspace 的上游输入，请创建新目标目录，使用 `--from` 和 `--start-task`：

```bash
python -m researchos.cli run \
  --workspace ./workspace/project-a-t3-redo \
  --from ./workspace/project-a \
  --start-task T3
```

未写 `--start-task` 时，`run --from` 默认从 T2 开始。新目标 workspace 不能带冲突的 `state.yaml`；系统只复制该起点声明的上游输入，不把旧阶段输出当作新证据直接复用。

### LaTeX 环境恢复

在重试 T3.6 或 T9 前先运行 `doctor`。现在环境 preflight 在 LLM 开始前执行，缺 TeX 会先暂停，不会在写作完成后才报错：

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
```

不要手工伪造 PDF 或 compile report。ResearchOS 会核对 TeX、PDF、log、参考文献和依赖 fingerprint，只有真实编译结果才能通过。

## 文档入口

| 需求 | 文档 |
| --- | --- |
| 最快上手 | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| 本地/Docker、TeX 与镜像排障 | [docs/docker.md](docs/docker.md) |
| 配置、语言与引用策略 | [docs/config.md](docs/config.md) |
| 日志、trace、status 与恢复排障 | [docs/logging.md](docs/logging.md) |
| 仓库和 workspace 目录 | [docs/project_structure.md](docs/project_structure.md) |
| 完整阶段与产物契约 | [docs/agent_pipeline.md](docs/agent_pipeline.md) |
| runtime 内部机制 | [docs/runtime.md](docs/runtime.md) |
| 开发、测试与贡献 | [docs/dev.md](docs/dev.md) |
| 文档索引 | [docs/README.md](docs/README.md) |

## 关键保证

- 引用与论断要回到 workspace provenance 校验，不把模型生成的文献当作已验证来源。
- T3.6 和 T9 必须产生真实 PDF、log 和 compile report，不能只写“编译成功”。
- 人工选择会落盘，能够在中断后继续。
- 实验走显式 handoff/result-pack 契约，模型文字不能替代实验结果。
- 日志和逐次 trace 位于 `<workspace>/_runtime/`。

仓库贡献约束见 [AGENTS.md](AGENTS.md)。
