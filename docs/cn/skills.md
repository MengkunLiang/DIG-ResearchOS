# Skill：发现、输入、网络检索、执行与恢复

> [中文](../cn/skills.md) | [English](../en/skills.md)

技能是存储在 `skills/<name>/SKILL.md` 中的可发现工作流。它们可以是原子性的，也可以是集成式的：集成式技能声明了持久的研究阶段、证据边界和人类决策点，同时复用与流水线智能体相同的工作区策略、ToolRegistry、追踪、事件、输出验证和恢复模型。受保护的 `skills/external_executor_skills/` 目录具有独立的所有权，不属于公共技能重写路径。

仓库中的每个 Skill 都有执行范围约定。已有独立 Skill 保留兼容的 `standalone` 默认值，所有非独立的仓库 Skill 都会显式声明范围和归属。`list-skills`、`browse-skills` 和 `run-skill` 只展示并启动具有独立会话约定的 Skill。由流水线负责的 Skill 仍可由其所属阶段加载，但直接调用会在创建工作区和连接模型之前停止，并说明实际归属。具体而言，`research-reboost` 属于 `T5-REBOOST-GATE`，`project-skill-specialization` 属于 `T5-SPECIALIZE-EXECUTOR-SKILLS`，历史 `method-builder` 仅供项目专属 external-executor Skill Suite 内部使用。这样不会把依赖流水线 Artifact 的契约错误地当成空工作区中的自由提示词。

## 先发现再运行

```bash
python -m researchos.cli list-skills --workspace ./workspace/project-a
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli describe-skill pdf-note-card --workspace ./workspace/project-a
```

`browse-skills` 支持数字、完整名称或双语模糊关键词，例如 `文献`、`literature`、`Idea` 或 `创新点`。在 `run <id>` 之前先查看卡片：它会说明目的、输入、输出产物、限制和恢复命令。

## 引导式会话约定

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01
```

在 TTY 中，默认流程是：

1. 读取声明的输入约定并检查本地文件。
2. 通过显式的 `ask_human` 通道一次询问一个缺失的材料或事实。
3. 仅将人类提供的材料或明确授权的远程来源暂存在 `user_inputs/<skill>/` 中。
4. 重新检查确定性就绪状态。
5. 持久化 `WAITING_CONFIRMATION` 并请求显式的 `执行` / `暂停`。
6. 仅在获得明确授权后运行技能。
7. 将可观察的阶段、当前工具、输出、摘要和恢复命令持久化到 `_runtime/skill_sessions/<session-id>.json`。

在引导式技能被列出或运行之前，ResearchOS 会验证其约定中的每个输入路径是否可读，以及每个声明的输出在该技能的工作区权限下是否可写。运行时向技能显示的能力边界与之完全相同。这是有意为之的严格限制：公共技能不得声明一个随后会变成 `access_denied` 的文件位置。

当正在运行的技能识别到语义上的证据缺口时，它会在向人类提问前写入 `user_inputs/<skill>/_followup_request.md`。它不得猜测缺失的来源、出处、引用、实验或结果信息。

### 远程论文来源和暂停语义

当研究人员明确提供 DOI、arXiv/OpenAlex 标识符、直接 PDF URL、精确标题或主题加请求数量时，`pdf-note-card`、`paper-comparison` 和 `literature-comparison-studio` 可以在引导式摄取过程中解析来源。受限的摄取智能体只会接收到已声明的来源解析工具和文件暂存工具。它无法运行 Shell、更改研究输出或浏览无关的工作区路径。

| 输入形式 | 摄取执行的操作 | 摄取后的证据状态 |
| --- | --- | --- |
| 上传的 PDF | 检查声明的输入路径并将其传递给技能。 | 在进行章节提取之前，该 PDF 视为未读来源。 |
| DOI/arXiv/OpenAlex ID 或直接 URL | 尝试元数据解析并将 PDF 下载至声明的 `user_inputs/<skill>/` 路径。 | 下载结果和标识符写入 `_source_resolution.md`；仅有元数据不算章节证据。 |
| 精确标题 | 搜索声明的学术来源，当有多个匹配结果且影响重大时，聚焦式地请求澄清。 | 搜索结果仅为线索，而非经过验证的论文证据。 |
| 主题加数量 | 在阅读/比较之前记录查询、请求数量、候选条目、选择规则和访问结果。 | 未读或不可访问的候选条目仍明确标记为 weak/unknown。 |

对于 PDF 笔记卡片，可以直接将来源请求作为技能请求提供：

```bash
python -m researchos.cli run-skill pdf-note-card \
  "Read DOI 10.1145/nnnnnnn.nnnnnnn and build a method/limitation note card" \
  --workspace ./workspace/project-a --session-id reading-doi-01
```

对于比较，提供两个标识符或授权窄范围的主题检索：

```bash
python -m researchos.cli run-skill paper-comparison \
  "Compare DOI 10.xxxx/a and arXiv:2501.01234 on treatment heterogeneity" \
  --workspace ./workspace/project-a --session-id compare-two

python -m researchos.cli run-skill literature-comparison-studio \
  "Find and compare 4 recent papers on the declared research topic; prefer readable full text" \
  --workspace ./workspace/project-a --session-id compare-topic
```

当摄取后的控制界面显示 `[1] 继续收集缺失材料` 和 `[2] 暂停并保留会话` 时，`1`/`继续` 会启动下一轮聚焦摄取，`2`/`暂停` 会立即持久化 `WAITING_INPUT` 并返回 Shell。无法识别的响应会重新询问；绝不会静默地开始另一轮摄取。在添加材料或更改请求后，使用相同的会话 ID 恢复。

对于自动化或管道场景：

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

缺失输入时会生成可恢复的 `WAITING_INPUT`，且不会构建提供程序客户端。在添加材料后继续：

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --session-id reading-01 --resume
```

## Skill 页面与材料准备

先运行 `browse-skills` 或 `describe-skill <名称>`。目录用于快速选择：每项只显示用途、需要的材料数量和会生成的文件数量。详情页再用 Rich 表格说明材料放置位置、材料用途、可用能力、完成后的输出和恢复命令。默认不显示完整 Tool 名称或实现细节；添加 `--verbose` 才会展开它们。

第一次启动引导式 Skill 时，系统先检查已经存在的项目文件和该 Skill 的材料目录。若材料已经齐全，会说明可以开始并请求执行确认；若缺少材料，只会询问下一项缺失内容，并提供上传、粘贴 DOI/arXiv/OpenAlex ID、URL、精确标题，或在支持的 Skill 中提供“主题 + 篇数”的选择。输入“暂停”“退出”或“稍后”会保存当前会话并返回终端，不会继续追问。

界面中的“论文阅读笔记”指保存了来源、阅读范围和论文位置的笔记；“论文中的相关内容或位置”是指可回查的段落、标题或页码。它们不是要求研究者理解内部 `section anchor`、`artifact` 或 `schema`。需要排障时，再使用 `--verbose`、`trace` 或运行日志查看技术信息。

## 能力分组

| 分组 | 典型技能 | 成果 |
| --- | --- | --- |
| 研究摄取 | `research-material-ingest`、标识符/PDF 解析 | 用户材料的清单及其来源 |
| 论文证据 | `pdf-note-card`、章节证据、笔记审查 | 带有证据边界的可引用论文卡片 |
| 文献分析 | 查询规划、引文图谱、比较、证据矩阵、空白地图 | 范围受限的检索和综合产物 |
| 想法与设计 | idea fanout、假设编译、实验设计审查 | 候选方案/治理产物，而非凭空捏造的协议事实 |
| 写作 | 论文大纲、论文撰写、声明-证据映射 | 草稿结构和与证据对齐的文本 |
| 审查与修订 | 会议匹配度、同行评审、润色、修订 | 可审计的审查发现和补丁 |
| 定稿 | 论文编译、提交就绪检查 | 实际的编译/状态检查和提交产物 |

### 能力配置文件和工具边界

每个公共技能现在都会获得 `workspace_navigation` 配置文件：`list_files`、`glob_files` 和 `grep_search`。这些工具遵循技能自身的 `allowed_read_prefixes`；它们不提供检查其他工作区或任意主机路径的途径。目录还会为每个技能解析一组显式的配置文件，并通过 `list-skills` 和 `describe-skill` 展示。

| 配置文件 | 增加内容 | 用途 |
| --- | --- | --- |
| `literature_discovery` | 多源、Semantic Scholar、arXiv、OpenAlex、Crossref、Scopus、INFORMS 搜索和元数据查找 | DOI/标题/主题发现、来源三角验证、会议感知搜索 |
| `paper_acquisition` | PDF 获取、PDF 文本/章节提取、本地记录查找 | 阅读指定论文或比较检索到的候选论文 |
| `paper_curation` | 种子论文处理和笔记卡片保存 | 将已解析的材料转化为持久的证据卡片 |
| `literature_processing` | 查询扩展、去重、筛选、可访问性审计、深度阅读队列、引文图谱和综合工作台 | 综述规模语料库管理和证据覆盖 |
| `structured_artifacts` | 经过模式校验的 YAML/JSON 写入 | 机读计划、评分卡、清单和审计记录 |
| `idea_analysis` | 集中度、新颖性信号、机制/设计理由元组工具 | 证据范围受限的候选比较和创新审计 |
| `claim_review` | 声明、证据和写作技巧审计 | 草稿修复、同行评审、润色和提交检查 |
| `manuscript_planning` / `survey_workflow` / `tex_delivery` | 稿件/摘要组装、综述审计/图表、实际 TeX 编译 | 具有声明输出的写作和交付工作流 |

配置文件是加性且可见的，但它们并非环境权限。它们不授予 `bash_run` 或 `docker_exec`；文件访问仍受单个技能约定的约束；来源获取需要明确的 DOI/arXiv/OpenAlex ID、URL、精确标题或主题加数量请求，以及一个可写的声明目标位置。这为阅读或审阅技能提供了足够解析和检查证据的工具，而不允许无关的工作区更改或任意的主机执行。

## T4 与下游 Skill

T4 使用职责分离的 Generator、Scorer 和 Evolver，Gate1 后的状态路径取决于研究者确认的操作。选择已经准备好的 Candidate 走 `T4 -> T4-GATE1 -> T4.5`；演化、定向优化、重跑 Route 或确认后的组合走 `T4 -> T4-GATE1 -> T4`，完成新版本后再次回到 Gate1；查看和比较则是留在 Gate1 的只读操作。Generator 形成证据校准但可创造性发散的 Candidate；Scorer 独立评估已脱敏的 Candidate，绝不生成 Idea；Evolver 只能创建受 plan 约束的 Mutation Child 或通过 Compatibility Check 的 Crossover Child。证据不是封闭的 Idea 空间：正常 Generator Route 可以使用通用学术知识、反事实推演和结构性跨域类比，只要相关内容始终明确为 conjectural、需要验证。若 Workspace 中没有可辩护的结构性迁移关系，Bridge Route 可以返回带 escape-hatch record 的 `unsupported`。

当研究者需要安全地进入原生 T4 时，使用 `t4-evolution`。它会检查当前的 Evidence Index、pre-run confirmation、Population、Portfolio 和 resume 状态，再用研究者能理解的语言说明下一步是新建 P0、恢复未完成的 Route 或评分批次、等待 Gate1，还是在确认选择后进入 T4.5。该 Skill 只写可读的启动说明，绝不编辑原生 T4 产物。新进入 T4 使用 `python -m researchos.cli run --workspace <workspace> --from-task T4`；中断或等待中的运行使用 `python -m researchos.cli resume --workspace <workspace>`。同一 workspace 不能并发运行多个命令。

T4 把语义格式恢复与科研安全分开处理，并统一记录 `valid`、`repairable`、`degraded`、`blocked` 四种结果。`blocked` 仅保护 Hard Invariant：来源/证据权限越界、虚假或不可追溯引用、Candidate/Parent/Plan 谱系冲突、ID 覆盖、fingerprint 或工作区状态损坏，以及 Legacy 覆盖风险。Markdown fence、YAML、字段别名、对象/列表外层差异、非核心字段缺失、一个 Route 或评分调用失败、数量不足和 Crossover 不兼容不会直接终止整轮；它们依次经过 tolerant extraction、确定性归一、schema-only repair、定向语义 repair 和重新验证，仍不完整时以 `degraded` 连续运行并留下诊断。

Generator 可以先提交最小 `IdeaSeed`，而非一次生成最终论文方案。Seed 只需问题、核心命题、候选机制、贡献草图、一条可证伪预测、主要不确定性和 Route 来源；详细展示、多条假设、完整 Evidence Map、实验与影响说明会在 Scoring、Mutation、Reading Upgrade 或 Final Card 阶段补全。`CreativeContext` 会保留概念跃迁、竞争解释、反直觉预测和研究纲领潜力，避免初始结构把非增量 Idea 压扁。LLM 参数知识只能以 `conjectural` / `verification_required=true` 提出待验证 Idea，不能充当 Citation、已证实机制、可用数据集、指标或实验结果。Scoring 将当前 `overall_readiness` 与 `scientific_upside` 分开；LLM 推荐的 Wildcard 只是保留给人类比较的选项，不是选择或证据认证的捷径。Scorer 在有限重试后仍失败时将 Candidate 标为 `unscored` 并保留，Mutation 失败保留 Parent，带清晰理由的 `no_improvement` defer 会保留 Parent 而不制造措辞型 Child，Crossover `incompatible` 是正常审查结果，Portfolio 可显示不足 3 个方向。验证器保护完整性；修复循环保护连续性；Evolution 处理不完整 Idea；Human Gate 保留最终决定权。

Publication Orientation 现在明确区分内部 `utd_is` 与 `ccf_cs` 两种 lens。`utd_is` 强调 phenomenon、theoretical tension、explanatory mechanism、identification、boundary conditions 与 organizational implications；`ccf_cs` 强调精确的 computational problem、technical mechanism、evaluation discipline、robustness、efficiency 与 reproducibility。它们是可配置的研究评价 lens，并非对任一 venue 当前官方审稿规则的声明。为兼容已有 workspace，旧的 `management_is` 和 `technical_cs` 仍然可以读取。

在 Gate1 选择完整 Candidate 后，系统会生成 Pre-Novelty brief 与 T4.5 search scope。`hypothesis-compiler`、`paper-outline` 及其它非执行型 Skill 可以用它们追溯已选方向或准备明确标为 provisional 的材料，但不能将其视为已验证的新颖性或可执行 protocol。组件级请求会先通过 Compatibility Check、Gene Donor Map、Independent Scoring 与第二次确认，形成 Human-composed Candidate；来源 Candidate 会完整保留。T5 与所有 executor Skill 在计划或运行实验之前，必须读取 T4.5 后的正式 hypotheses、experiment plan 和已接受的 novelty audit。

## 集成式研究工作流

以下公共技能是组合式工作流，而非单个 LLM 提示的别名。它们都始于引导式约定，写入产物清单，将阶段状态持久化到 `_runtime/skill_sessions/<id>.json`，并在范围扩展、开销高昂的阅读、候选选择或综述交接前使用显式的人类门控。

| 技能 | 主要阶段 | 关键输出 | 门控行为 |
| --- | --- | --- | --- |
| `domain-synthesis-studio` | 清单 -> 检索决策 -> 来源补充 -> 综合 -> 后续路径决策 | 领域报告、方法家族图谱、矛盾图谱、证据登记册 | 询问是综合当前材料、授权限定范围的检索，还是上传来源；然后提供综述/想法/阅读路径。 |
| `literature-comparison-studio` | 比较约定 -> DOI/标题/PDF/主题来源就绪 -> 章节证据 -> 比较审计 | 比较报告/CSV/JSON、声明边界 | 支持两个标识符、上传的 PDF、来源列表或显式的主题加数量请求；未知单元格保持未知。 |
| `literature-review-studio` | 综述范围 -> 查询/检索 -> 阅读覆盖率 -> 综合/分类 -> 综述交接 | 语料库清单、查询组合、矩阵、综合、就绪报告 | 需要检索授权，之后询问是准备综述、补充阅读，还是停在领域综合阶段。 |
| `survey-evidence-package` | 意图 -> 充分性 -> 补充决策 -> 交接 | 语料库充分性、分类候选、故事情节、证据包 | 不撰写综述稿件。它首先让综述证据决策变得可见。 |
| `cross-domain-idea-studio` | 目标约定 -> 桥接检索 -> 迁移审计 -> 候选评审 | 桥接计划、迁移卡片、风险登记册、候选池 | 桥接类比并非证据。候选方案在假设编译前需要人类选择。 |
| `t4-evolution` | 状态检查 -> 研究者意图确认 -> 原生 T4 交接 | 启动/恢复说明 | 解释唯一安全的原生 pipeline 操作并保留所有 Population 版本；绝不编辑原生 T4 产物。 |
| `paper-reading-workbench` | 来源约定 -> 访问 -> 证据阅读 -> 跨论文学习 | 阅读索引、卡片、答案、跨论文摘要 | 按问题阅读 PDF/章节，并保留全文/部分/摘要/元数据状态。 |
| `research-landscape-report` | 范围 -> 映射/覆盖 -> 机会决策 | 景观报告/数据、覆盖率、机会登记册 | 检索空白和图谱信号与研究方向分开报告。 |
| `related-work-builder` | 定位 -> 证据绑定 -> 章节草稿 | TeX 章节、证据映射、引用/声明审计 | 无来源时不创建引用或直接基线声明。 |
| `draft-evidence-repair` | 稿件约定 -> 证据清单 -> 修复决策 -> 包 | 修复报告/JSON、补丁计划、声明边界 | 缺失证据会触发人类选择：补充、弱化、删除或暂停。 |

通过普通的 CLI 使用这些新工作流；无需特殊的运行器：

```bash
python -m researchos.cli run-skill domain-synthesis-studio \
  "综合该领域，先判断是否需要定向检索，再决定是否准备 Survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli run-skill cross-domain-idea-studio \
  "用已审计桥接证据生成跨域候选，不要未验证实验配置" \
  --workspace ./workspace/project-a --session-id bridge-ideas

python -m researchos.cli run-skill t4-evolution \
  "检查当前 T4 状态，并告诉我唯一安全的恢复命令" \
  --workspace ./workspace/project-a --session-id native-t4
```

集成式会话会在就绪、完成和 `skill-status` 视图中显示阶段表。有效状态为 `pending`、`running`、`completed`、`waiting_input`、`waiting_evidence` 和 `skipped`。技能会在阶段边界调用受限的 `update_skill_workflow` 工具；这仅记录面向用户的研究进展，而非模型推理或原始提示。

### 自动补充

当集成式技能拥有返回结果的搜索工具且研究人员授权检索时，它可以尝试自行补充缺失的文献。结果是一条线索/出处记录，而非自动生效的有力证据。来源必须按所需粒度阅读之后，才能支持某个机制、因果声明、分类核心、基线比较或论文定位。当自动搜索无法弥合该证据缺口时，工作流会要求上传、缩小范围或使用单独的阅读技能。

请使用实时目录而非此表获取准确的名称：目录是已安装能力的真实来源。

## 证据边界

技能仅在其当前项目允许的输入或已审计的产物中明确标识了 AUUC、Qini、准确率、F1、命名数据集、基线、种子或资源数量时，才能使用这些数值。这不是禁止这些名称，而是一项溯源要求：缺失的细节保持 `unknown` 或 `proposed_not_verified`，并触发聚焦的后续询问。

`idea-fanout-jury` 说明了这一边界。如果有证据支持的综合分析或论文卡片，它可以产生经过评分、来源锚定的方向。如果没有，它只能产生一个带有缺失证据账本的标签化初步概念集。它不得凭空捏造当前项目的数据集、基线、指标、AUUC/Qini 值、预算、种子、命令或数值期望。

## 状态

```bash
python -m researchos.cli skill-status --workspace ./workspace/project-a
python -m researchos.cli skill-status pdf-note-card --workspace ./workspace/project-a
```

状态面板报告会话模式、就绪状态、当前可观察阶段、工具活动、输出、阻塞项以及精确的恢复命令。它不显示私有的模型推理。
