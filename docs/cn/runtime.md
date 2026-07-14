# 运行时架构、能力边界与恢复契约

> [中文](../cn/runtime.md) | [English](../en/runtime.md)

本文档面向维护者和高级用户。有关命令，请从根 README 开始；有关阶段语义，请阅读 [agent_pipeline.md](agent_pipeline.md)。

## 执行模型

```text
CLI
  -> RuntimeSettings + Workspace initialization
  -> ToolRegistry + Skills + optional MCP adapters
  -> StateMachine / runner
  -> ExecutionContext
  -> AgentRunner -> Agent, SkillAgent, or integrated SkillAgent -> policy-bounded tools
  -> workspace artifacts + validators + events/logs/traces
```

关键实现路径：

| 职责 | 路径 |
| --- | --- |
| CLI 和命令调度 | `researchos/cli.py` |
| 完整/单任务运行器 | `researchos/cli_runners/` |
| 状态机和门控 | `researchos/orchestration/state_machine.py` |
| 任务 I/O 契约 | `researchos/orchestration/task_io_contract.py` |
| 代理执行和验证重试 | `researchos/runtime/orchestrator.py` |
| 控制台/事件报告器 | `researchos/runtime/observability/` |
| 内置工具注册 | `researchos/tools/builtin.py` |
| 工作区策略 | `researchos/tools/workspace_policy.py` |
| 技能 | `researchos/skills/` |

## 工作区与验证

`ExecutionContext` 携带工作区、项目、任务、运行、策略和运行时元数据。工具只能读取/写入允许的工作区相对路径。代理的 `finish_task` 是验证请求，而非成功声明。验证器在状态推进前检查声明的制品、模式、状态、指纹、引用、编译结果和任务特定条件。

状态机是拓扑权威：

```text
config/system_config/state_machine.yaml
```

其输入/输出定义阶段契约；Python 验证器定义制品是否可用。两者只能作为协调的兼容性更改一起修改。

当启动前发现 YAML 节点与 Python I/O contract 不一致时，CLI 会显示 Rich 错误面板，并列出实际加载的 `state_machine.yaml`、`task_io_contract.py`、缺少/额外/路径变化的字段，以及 `validate-config` 命令。该错误通常意味着运行了旧 checkout、`RESEARCHOS_SYSTEM_CONFIG_DIR` 指向旧配置，或只同步了 Python 与 YAML 中的一部分；系统会在启动任何 Agent 前停止，不会写入研究产物。`validate-config` 的输出也包含两个实际来源路径，便于比较部署环境。

## 可观察性协议

`runtime/observability/` 接收结构化的阶段/工具事件，并将相同信息渲染为彩色的 Rich 面板、可移植的无颜色文本和 JSONL。它应传达与研究者相关的事实，而非思维链：

- 阶段开始：输入制品的含义/状态、计划的计算和分支。
- 进度：有界计数、排名、分布、决策、失败、不支持的证据和输出写入。
- 总结：结论、风险、制品清单和下游消费者。

原始工具负载和提供方响应属于跟踪记录，而非普通控制台输出。CLI 启动面板集中在 `runtime/cli_ui.py` 中，并在每个实际命令的 `main` 处发出一次。运行时命令稍后可能添加工作区发现摘要，而不会重放标语。

正常终端渲染面向研究人员的摘要，而不是重复已传递给代理的结果。PDF 提取报告页面覆盖和延续状态；部分提取报告识别的部分；web、命令、Docker、LaTeX 和结构化写入工具仅报告状态、计数、制品路径和必要的下一步操作。完整的 PDF 文本、HTML、stdout、JSON 负载、提供方诊断和堆栈跟踪保留在代理上下文、跟踪和日志中，用于审计，而不会使终端充斥信息。

### 终端展示与信息层级

每次启动先显示 Rich 启动卡：项目目录、已加载的研究流程、模型设置、可用 Skill 和 MCP 状态。随后“系统检查”卡只报告模型连接与本地依赖是否可用，不再输出 YAML、`startup_selftest`、配置路径列表或原始 provider trace。`--no-color` 只关闭颜色，仍保留卡片和表格；`--verbose` 才显示配置路径、完整 Tool 名称、详细错误和过程文件。

普通运行只展示研究者需要采取行动的信息：当前正在做什么、已经完成什么、还需要什么以及下一步。高容量 Tool（PDF 文本、网页、命令输出和结构化记录）仍把完整结果保存在运行记录中，终端只给出页码、范围、文件或状态摘要。长文本在 Rich 单元格中自然换行；不会使用字符级省略号截断半句话。模型等待会显示一条原位刷新的 heartbeat，优先保留当前活动和已完成的公开里程碑，不重复刷出同一行。

用户界面称“材料准备”“论文阅读笔记”“论文中的相关内容或位置”“输出文件”。`taxonomy`、`baseline`、`ablation`、`claim` 和 `Related Work` 等学术术语保持原样。内部的 `schema`、`artifact`、`intake`、Agent 名称、Tool 名称和原始 provider 错误仍可在 `--verbose`、trace 和日志中查看，但不会作为普通用户界面的主要说明。

### 临时提供方故障

一个请求只使用 `config/model_settings.yaml` 中配置的同一个 provider/model。若因 timeout、连接中断、502/503/504 或临时过载失败，runtime 会先按该文件中的同模型 retry 策略等待并重试；连续恢复仍失败时，终端提供“立即重试”“等待后重试”“暂停项目”三个明确选项。等待不计入 Agent 的有效工作时间，也不会消耗研究步骤。

暂停会保留当前任务和所有 artifact；服务恢复后使用原来的 `resume` 命令继续。普通 CLI 不显示 API key、完整 SDK stack trace 或内部 retry 细节。认证、URL 与 model 配置问题会直接提示运行 `configure-llm`，而不是进入无意义的网络重试。

## 引导式技能会话

公开技能具有已解析的 `SKILL.md` 契约和持久化会话：

```text
_runtime/skill_sessions/<session-id>.json
user_inputs/<skill>/_intake.md
user_inputs/<skill>/_followup_request.md   # only when semantic input is missing
```

TTY 会话在创建提供方之前确定性地检查就绪状态，在 `user_inputs/<skill>/` 下收集人工材料，重新检查，然后要求显式的执行确认。非交互式缺少输入路径在 `WAITING_INPUT` 处停止，不创建 LLM 客户端。接收阶段不得写入最终的研究制品。

技能不施加人为的内部令牌/步骤限制。提供方约束和实际运行时条件仍然适用。

### 集成技能工作流

集成公开技能向 `SKILL.md` 添加声明性 `workflow` 部分。加载器在发现时验证阶段 ID、标签、目标、操作和门控标志。`record_readiness` 将该契约复制到正常会话文件中，而不会在恢复时重置已完成的阶段记录。有界的 `update_skill_workflow` 工具只能更新活动独立技能会话和记录：

```text
phase id / visible status / summary / artifact paths / evidence boundary / next action
```

这不是嵌套的 `run-skill` 执行。当前运行时一次执行一个 SkillAgent 和一个受策略约束的 ToolRegistry；组合技能在命名阶段序列内重用真实工具和制品契约。这避免了隐藏的子会话、路径策略漂移和不明确的恢复所有权。

### 提供方上下文摘要批处理

T3 的全文阅读仍然逐篇进行。LLM client 会从 OpenAI-compatible `/models` metadata 尝试识别当前 model 的 `context window`，同时兼容 `/v1` 与非 `/v1` URL；可接受 `context_length`、`context_window`、`max_context`、`max_input_tokens` 等常见字段。该值在当前 client 内缓存，用于 file reading、history trimming 和 abstract batching。provider 没有可验证 metadata 时，runtime 使用 128k token fallback；研究者不需要配置 context 或 batch size。

编排器用当前 model 的 `count_tokens()` 与发现到的 context window 自动打包 abstract records，不设置固定论文数量。每篇返回的 JSON 笔记都会规范化后写入 `shallow_read_notes/<paper>.md`；它属于粗读线索，不等同于全文证据。

### 上下文感知文件读取

`read_file` 仅公开 `path` 和 `offset`；页面大小根据有效上下文窗口计算。运行时为提示/历史/未来工具预留 `max(8,000, 上下文 15%)` 令牌，上限为 64,000，并将剩余令牌的 70% 分配给文件结果。仅当文件适合自动完整读取份额时，才返回完整文件；否则返回自动上下文大小的页面，并报告权威的下一个偏移量。T2 `papers_raw.jsonl` 在 JSONL 记录边界处分页。结果元数据包括应用的预算、有效上下文窗口及其来源：`provider_metadata`、`configured_fallback` 或 `explicit_override`。

批输出保持为 `ABSTRACT_ONLY` / `abstract_claim_hint`。格式错误或不完整的批次仅针对缺失论文进行回退，而仅元数据记录保留在其现有批次分类路径中。批次计数、每论文回退以及提供方上下文作为有界进度和访问审计事实发出。

## T3.6 调查运行时

`BuildSurveyStateTool` 创建部分契约和大纲文件。对于不变的调查计划，它是幂等的：已完成 `written`/`revised` 的部分，只要存在现有的部分文件且大纲指纹匹配，就会在重建中保留。计划或契约的更改有意使受影响的部分状态无效。

每个 `T3.6-SEC-*` 任务也是一个任务作用域的写入沙箱。它只能写入自己的 `drafts/survey/sections/<section>.tex` 文件，并更新共享的 `drafts/survey/survey_state.json` 条目中同一部分的内容。它不能重建部分大纲、写入其他部分、组装调查、生成图表或编译 PDF。在 `resume` 时，文件和状态通过其验证器的部分将直接推进，而不会进行第二次 LLM 重写。

调查可视化工具最多生成一个矢量 PDF：

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

它仅读取显式分类结构和已解析的本地笔记卡片链接。渲染器偏好 Times New Roman，并将其安装的衬线字体备用记录在 `survey_visual_manifest.json` 中。性能、基线、跨研究增益、排名或推断风险图会被策略和组装验证拒绝。

调查审计还检查物理 LaTeX 布局。对于内置双栏 CCF 模板（ICML、NeurIPS、ICLR 和 KDD），全页宽的分类图像必须包含在 `figure*` 中；普通的 `figure` 必须使用 `\\columnwidth`、`\\linewidth` 或严格更小的宽度。带有 `width=\\textwidth` 的普通 `figure` 可以编译，但会穿过相邻列绘制，因此 `survey_graphics_layout` 在审查或最终编译前阻止它。这是一条布局规则，而非内容规则：它不授权额外的图表或放宽仅分类的可视化清单。

`T3.6-REVIEW` 具有更严格的衍生制品边界。它可以修改 `drafts/survey/sections/<section>.tex`，然后调用 `assemble_survey` 和 `audit_survey_coverage`；它不能使用普通的 `write_file` 覆盖 `drafts/survey/survey.tex`。这防止了上下文受限的修复用模型恰好读取的文本替换完整的调查。标题/模板更正提供给 `assemble_survey(title=...)`，并且必须记录在 `survey_review_actions.json` 中；稍后的正常组装应使用修复后的标题源，而不是隐藏的手动 TeX 编辑。

## 实验细节完整性

运行时与来源绑定，而非与度量名称绑定。具体的数据集、度量、基线、种子、资源值或阈值仅在当前项目通过允许的输入或审计的制品显式提供时才能使用。其使用必须附有相关的源路径和部分/字段。否则该值为 `unknown`、`proposed_not_verified` 或阻塞器。这适用于 AUUC/Qini，如同适用于 accuracy/F1。

## 技能能力契约

每个引导式公开技能仅在对其 `SKILL.md` 契约进行确定性验证后才加载。每个声明的输入位置必须在其 `allowed_read_prefixes` 下；每个声明的输出必须在其 `allowed_write_prefixes` 下。运行时会将这些边界重复在技能系统上下文中。这防止了就绪面板宣传稍后会因 `access_denied` 而失败的路径。

此检查涵盖工作区相对路径。与显式批准的外部本地源一起使用的特殊用途工具将其外部路径验证保留在该工具内部；技能不得使用 `read_file` 探测工作区外部的绝对路径。

### 基于配置文件的工具表面

公开目录使用声明的能力配置文件，而不是为每个技能提供最小、发散的工具列表。所有公开技能在其自身的读取策略下接收 `workspace_navigation`（`list_files`、`glob_files`、`grep_search`）。适当的工作流还会额外接收按组分的文献发现、论文获取/策展、语料处理、结构化制品、构思、审查、手稿、调查、TeX 或执行器切换工具。使用目录在演示或运行前检查解析的工具表面：

```bash
python -m researchos.cli describe-skill paper-comparison \
  --workspace ./workspace/project-a
python -m researchos.cli list-skills --workspace ./workspace/project-a --verbose
```

配置文件是附加的便利，而非不受限制的权限。它们不添加 `bash_run` 或 `docker_exec`；`WorkspaceAccessPolicy` 仍然控制每个工作区路径；获取工具需要显式的源请求和可写入的声明目的地。有关配置文件映射，请参阅 [skills.md](skills.md)。

## 结构化制品诊断

`write_structured_file` 在创建或更改目标文件之前验证 JSON/YAML 对象。在模式失败时，该工具现在在代理上下文和终端事件中返回有界修复列表。每个条目包含实例路径、规则和消息，例如：

```text
$.ideas[2].decision.rejection_reason [type]: requires ['array'], current is str
$.ideas[0].counterfactual_check [required]: missing required field
$.ideas[0].basis.literature_observations[0].strength [enum]: supporting is not allowed
```

文件不会被部分写入。更正列出的字段，然后重试相同的 `write_structured_file` 调用。不要仅仅为了减少模式错误而删除候选记录：T4 需要完整的 Gate1 池，包括延迟、合并和拒绝的候选记录。终端错误代码保持为 `schema_validation_failed` 以供自动化使用；字段级诊断是可操作的原因。

T4 不会机械地产生固定数量的想法卡片，而是先构建 Evidence Index 和非对称 P0。Standard mode 完成 `P0 -> P1`：以不同的 Evidence Permission 召回全文/部分全文与摘要层论文阅读笔记，形成 Opportunity Map，按 Route 生成 Candidate，执行 Independent Scoring，生成受计划约束的 Mutation Child 和满足 Compatibility Check 的 Crossover Child，随后进行 union rescoring 与 Survival Selection。Gate1 通常先展示 1–3 个成熟 Candidate 的 Portfolio，同时保留 6–8 个 Active Candidates 和完整 Archive。每张成熟卡由 LLM 基于 Workspace 证据撰写，包含 2–4 项 Contribution、2–4 条 Draft Hypotheses、机制、验证路径、风险、Evidence Composition、评分解释、谱系和论文阅读笔记路径。运行时负责验证结构、来源、权限和生命周期，不会补写科研性文字。`resume` 会从最后一个有效 Phase 修复不完整的 native artifact；legacy artifact 只会迁移，不会被静默覆盖。

## 扩展点

1. 添加一个有界工具并在 `tools/builtin.py` 中注册。
2. 定义访问路径和结构化参数。
3. 在代理依赖制品模式/验证器之前添加或扩展它。
4. 如果阶段拓扑发生变化，更新状态机契约。
5. 通过现有的报告器发出结构化的可观察性事实。
6. 根据需要添加重点测试、CLI/运行时集成测试，并更新文档。

避免侧通道文件系统写入、为研究制品执行原始 shell 命令以及仅提示的状态转换。它们会绕过来源、恢复和审计。
