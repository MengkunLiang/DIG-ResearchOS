# ResearchOS 研究流水线

> [中文](../cn/agent_pipeline.md) | [English](../en/agent_pipeline.md)

规范拓扑结构见 [config/system_config/state_machine.yaml](../../config/system_config/state_machine.yaml)。本文档解释面向研究者的契约，而非私有的模型推理。所有阶段均采用 Stage Start -> Progress -> Summary，并产出一份制品清单。

## 主流程

```text
T1 -> T2 -> T3 -> T3.5
  -> T3.6 survey gate (optional) -> T4 -> T4.5
  -> T5 reboost -> T5 specialize executor Skills -> T5 executor gate
  -> T5 external execution/wait
  -> T8 manuscript/review -> T9 submission bundle
```

HELLO 是一个独立的冒烟任务，并非主链起源。遗留的内部实验节点仅为兼容性保留；默认研究路径编译外部执行器移交包，然后把外部执行器的研究报告直接交给 T8。

## 阶段

| 阶段 | 研究问题 | 关键输出 | 人类控制 |
| --- | --- | --- | --- |
| T1 | 研究范围和制约因素/种子材料是什么？ | `project.yaml`、范围/桥接制品 | 范围和桥接关卡 |
| T2 | 哪些来源支持的论文构成可信的候选池？ | 经验证的论文、领域图谱、队列、待办列表、搜索日志 | 覆盖率/语言参数关卡 |
| T3 | 每篇保留的论文实际支持什么？ | 论文笔记/卡片、对比表格、阅读审计 | 访问/证据暂停 |
| T3.5 | 涌现出哪些机制、张力、贡献空间与迁移？ | 综合/工作台、缺失领域审计 | 可选的 Survey 决策以及当前语料库 vs 定向检索偏好 |
| T3.6 | 基于分类框架组织的领域综述是否必要且证据充分？ | 综述计划/状态/章节/审计/实际 PDF | 综述、大纲、语料库、编译恢复关卡 |
| T4 | 哪个基于证据的 Research Idea 值得继续演化或选择？ | P0/P1/P2 Population、Evidence Index、评分、谱系、Portfolio、Pre-Novelty brief | 运行前确认；Gate1 directive、composition、rollback |
| T4.5 | 选中的 Pre-Novelty idea 经定向 novelty/collision audit 后是否仍然成立？ | novelty/collision audit；仅在通过时生成正式 hypotheses、maps、kill criteria 和 experiment plan | Novelty human review |
| T5 | 外部执行器在不发明协议的情况下能实现什么，哪些研究报告已可交给写作？ | 移交包、项目专属 Skill suite、specialization execution 记录、执行器选择、`external_executor/executor_research_report.md` | 执行器关卡 |
| T8 | 如何将来源/结果转化为与证据一致的论文？ | 风格、叙事线、章节、评审、修订、声明审计 | 风格/模板关卡 |
| T9 | 提交包是否内部一致并真实编译？ | 打包、编译报告、PDF/源文件指纹 | 环境/恢复暂停 |

## T2 与 T3 的可见性

T2 展示查询组合去重、来源贡献、元数据验证、分数分布、引用图谱提示、阅读队列和待办列表原因。分数和图谱结构是优先级提示，而非最终的学术判断。

T3 报告每篇论文的访问/证据级别、页面覆盖范围、提取/截断状态、机制证据、设计原理、边界、张力、桥接点以及不支持字段。全文、部分文本、仅摘要和仅元数据证据保持明显区分。

深度阅读后，符合条件的浅层记录可在提供程序上下文自适应的批次中进行摘要阅读。批次规划使用当前活动的模型绑定和分词器，而非固定的每次调用论文数。每篇论文仍会获得单独的笔记，且绝不会仅仅因为与其他摘要共享一次 LLM 调用就成为全文证据。

## T3.6 Survey 分支

T3.6 是一个调查论文分支，而非综合到 TeX 的转换。其紧凑的默认结构为引言、背景/范围、分类法、比较分析、挑战、未来方向、结论和摘要。

T3.5 Survey 关卡首先询问是跳过 Survey、使用当前语料库撰写，还是在撰写前请求一次定向补充。该偏好持久化保存在 `drafts/survey/decision.json` 中，在分类法/语料库规划期间可见，并且不会将搜索线索转变为 Survey 证据。当研究者需要独立的、引导式的撰写前充分性/分类法工作流时，请使用 `survey-evidence-package`。

- `build_survey_state` 生成章节撰写契约，并在相同计划下重新发布时保留有效的已完成章节。
- `build_survey_figures` 可能仅创建 `drafts/survey/figures/fig_taxonomy_overview.pdf`。
- 该图编码显式的分类法标签和直接解析的笔记卡片链接。它从不编码性能、相对增益、基线、来源分数或推断的证据强度。
- `latex_compile` 需要真正的后端。在恢复之前修复 TeX/Docker 环境错误，而不是花费更多撰写重试。
- 每个 `T3.6-SEC-*` 工作器被沙箱化到其声明的一个章节加上匹配的共享状态条目。有效的被中断章节在恢复时会经过验证器检查并推进，而不是被静默重写。
- `T3.6-ASSEMBLE` 首先创建 `survey.tex`，然后运行确定性审计。在进行具体修复后使用 `audit-survey --workspace <workspace>` 重新生成 `survey_audit.md/json`，无需联系提供程序。引用多样性同时使用小语料库下限和总用量缩放重复限制；它不会仅仅因为一个合法引用被使用了 13 次而拒绝一个 104 次引用的 Survey。
- `T3.6-REVIEW` 将 `survey.tex` 视为派生文件。它仅审查和修补源章节，然后使用 `assemble_survey` 重新生成包装器，并使用 `audit_survey_coverage` 刷新证据检查。在此阶段，对 `survey.tex` 的普通全文件写入会被拒绝，因为部分上下文读取可能会破坏组装好的文档。审查驱动的组装刻意使先前的 PDF/报告过时；然后 T3.6-COMPILE 执行一次真正的编译。

## T4 候选治理

T4 是一个 artifact-first 的 Research Idea Formation & Evolution 工作流。Gate1 后的状态由确认操作决定，而不是固定回到 T4。选择已准备好的 Candidate 走 `T4 -> T4-GATE1 -> T4.5`；演化或优化走 `T4 -> T4-GATE1 -> T4`，并创建可保留的新 Candidate 版本；只读查看留在 Gate1。内部不是让模型一次性写出最终假设，而是先形成、比较并演化一组可追溯的 Candidate Population。

运行前，Rich 确认面板会说明已有的论文阅读笔记、风险提示、运行模式、这一轮会做什么、预计耗时类型以及是否可以 rollback。默认 Standard mode 会完成一次完整的 `P0 -> P1`：Evidence Routing、Opportunity Map、非对称 Multi-route Generation、Idea Genome 与 Idea Family、Independent Scoring、Parent Selection、Mutation/Crossover 计划、Child 生成、union rescoring、Idea Contract、Family-level Survival Selection、Population Update 和 Portfolio Selection。P0 默认包含 Literature 3 个、Informed Brainstorm 2–3 个、四类补充 Route 各 1 个，以及在证据允许时的 1–2 个 Cross-domain/Bridge Candidate。这些是探索预算，不是凑数式产出的命令。某条 Route 没有足够依据时可以明确标为 `unsupported`；系统会记录原因，不会为了凑数虚构方向。实时面板会分开显示当前活动、当前产物和后续阶段：`研究机会探索（Opportunity Map）` 的产物是研究机会清单，而不是最终 Candidate，随后才进入多视角 Idea 发散。正常展示只用 Rich 呈现 Evidence Routing、初始 Population、独立评分和 Portfolio 等关键结果；准备和内部过渡显示为单行状态，避免挤满终端。

Evidence Index 会召回主线和 Bridge 的全文/部分全文笔记，也会召回摘要层笔记。Evidence Permission 决定一条材料能支持到什么程度：abstract-only 可以帮助发现线索、补充 taxonomy 或提示需要升级阅读，但不能用来确认机制、设计理由或强 Claim。证据限制的是认证，不是想象：正常 Generator Route 可以使用学术知识、反事实推演和结构性跨域类比，以 `conjectural`、需要验证的 `CreativeContext` 形式提出超过现有文本的概念跃迁。每个 Candidate 都保留来源路径、阅读层级、不确定性和升级阅读要求。若某个 Route 的结构化回复缺少 Bridge 解释等必需字段，系统只会对该 Route 进行一次定向修复；网络、鉴权或 provider 问题不会被当成内容错误反复调用。中断发生在 Route 之后时，`resume` 会复用输入指纹一致的 Evidence Index 与 Opportunity Map，只重试未完成的 Route。

Generator、Scorer 和 Evolver 是彼此分离的角色。Generator 只负责按 Route 形成 Candidate，不给自己打分或做选择；Scorer 在看不到 Route 和亲子关系的条件下独立评分，不能生成或改写 Idea，并会区分当前成熟度与科学上行空间，必要时保留高上行 Wildcard 供人类比较；Evolver 会生成已经批准的 Mutation Child 或通过 Compatibility Check 的 Crossover Child，若强行生成只能得到措辞型改写，则记录明确的 no-improvement/incompatibility deferral。一张成熟的 Idea Card 会展示一句话核心、Overall Readiness、五个独立评分及解释、研究问题、贡献包、Draft Hypotheses、机制链、风险、Evidence Composition、系统建议以及对应的论文阅读笔记路径。provisional Seed 在具备独立评分、完整 LLM Final Card、可追溯 Core Thesis 和至少一条由 LLM 写出的可证伪草案假设时，也可以作为待审计方向进入 T4.5。其 Seed 成熟度、证据缺口和单条假设限制会随 Pre-Novelty brief 进入审计，而不是在确认后悄然退回 T4。缺少 Final Card、评分、核心命题或草案假设时仍会在确认前明确阻塞。

Gate1 首先展示 1–3 个 Portfolio Candidates，但会保留 6–8 个 Active Candidates 和完整 Archive。用户可以选择完整 Candidate、继续一轮 Evolution、聚焦某个 Candidate 或 Idea Family、创建 Crossover、组合 Hypothesis/Contribution/Gene、并行保留多个完整方向、查看 Score/Evidence/Lineage、重跑一条 Route、rollback 或 pause。只读操作不会调用模型；每个会改变状态的操作都会说明是否会调用模型、预计做什么、当前版本是否保留、能否 rollback，以及会不会进入 T4.5。

自然语言会先由可选的 LLM parser 解析为 `IdeaDirective`，再由本地规则核对 Candidate ID、组件引用、fingerprint 和确认要求。用户同时提到多个完整 Candidate 时，默认理解为 parallel，不会擅自合并。跨 Candidate 的 Hypothesis、Contribution 或 Gene 选择会先进行 Compatibility Check，给出 Gene Donor Map，等待第二次确认，再生成 Human-composed Candidate、执行 Independent Scoring，并写入新的 Population snapshot。系统不会把两段文字直接拼接成假设文件，也不会覆盖来源 Candidate。

选择一个完整 Candidate 后，T4 只会生成 `ideation/hypothesis_brief.yaml`、lineage、T4.5 search targets 和 Pre-Novelty brief；这些文件用于查新，不是实验执行授权。随后直接进入 T4.5 的 novelty/collision audit。只有 T4.5 明确通过后，系统才允许生成正式的 `hypotheses.md`、Contribution–Hypothesis Mapping、Validation Map、Kill Criteria、`exp_plan.yaml` 和供 T5 使用的 post-novelty formalization manifest。

## T5 到 T8 外部证据路径

ResearchOS 先准备执行器 handoff，然后运行 `T5-SPECIALIZE-EXECUTOR-SKILLS`：LLM 消费仓库级 `project-skill-specialization` Skill，调用确定性 wrapper，ResearchOS 再独立校验已发布的 context/report/13-Skill suite，之后才进入 executor 选择。外部执行必须把 T8 核心交接文件留在 `external_executor/executor_research_report.md`；`external_executor/` 下的其他文件继续保留，供 T8 按需读取作为追溯材料。外部执行器总控 Skill 在完成前会做最终 handoff 输入检查。模拟空运行仅验证协议链，不生成经验性 claim。

## T8-T9 撰写与提交

T8 消费 `external_executor/executor_research_report.md`，在起草章节前构建资源索引和对齐矩阵。会议/期刊配置文件塑造叙事密度，但不是官方的页数限制或政策来源。T9 组装包，调用真正的编译，记录警告和错误，对源文件/PDF 进行指纹识别，并检查声明审计是否匹配当前版本。

## 恢复规则

- `resume` 在指定阻塞问题修复后继续当前工作区。
- `run-task <task>` 用于独立诊断，不会推进主管道；`run-task <task> --from <source>` 会先复制该 task 声明的前置材料，来源 workspace 不会被修改。
- `validate --task <task>` 在代价高昂的恢复之前检查修复后的制品。
- `run --from <workspace> --start-task <task>` 从另一个工作区初始化新项目；这不是状态合并。
- `resume --from-task T3.6` 可用作 Survey 决策入口的公共别名，等价于 `T3.6-GATE-SURVEY`。

有关可观察状态，请参阅 [logging.md](logging.md)；有关实现契约，请参阅 [runtime.md](runtime.md)。
