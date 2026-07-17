# 可观测性、日志与排障

> [中文](../cn/logging.md) | [English](../en/logging.md)

ResearchOS 将面向研究者的流程信息与机器级诊断分离开。终端旨在解释输入、计算、决策、输出产物和阻塞因素，而不暴露私有的模型推理或原始提示载荷。

## 控制台

除非提供了 `--no-banner` 或 `--quiet`，否则每个 CLI 命令都以 DIG · BUAA / ResearchOS 面板开始。交互式终端使用颜色和三帧标记 `D -> DI -> DIG`；非 TTY 输出则为单一便携面板。

正式阶段遵循相同的流程：

1. **阶段开始** — 目标、研究问题、输入产物表、验证、计划操作、预期输出、可能的分支/门控。
2. **阶段进行中** — 有界统计、Top-N、分布、决策、失败、积压工作和不支持的证据；从不包含完整的工具载荷。
3. **阶段总结** — 结论、风险、产物清单、下游用途以及所需的人工操作。

使用：

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity concise
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --no-color
```

`concise` 仍会报告输入、输出和人工操作。`normal` 为默认值。`detailed` 会增加有界的按查询、按论文、按桥接或按候选信息。`--json-events` 会将经过净化的 event JSON 镜像输出到 stdout，不应与人工门控会话混用。

## 每次运行写入的文件

```text
<workspace>/_runtime/
├── logs/researchos.log           Human-readable operational timeline
├── logs/researchos-debug.log     Lower-level debug logging
├── traces/<run-id>.jsonl         Machine trace with messages/tool payloads
├── events/<run-id>.jsonl         Sanitized researcher-facing event stream
└── skill_sessions/<id>.json      Guided Skill state and resume information
```

即使没有 `--json-events`，事件也会被写入。`trace` 是敏感的操作数据，未经审查不应公开发布。

集成 Skill 会话会在同一个会话文件中添加一个 `workflow` 对象。其中包含阶段标签、当前阶段、可见状态、摘要、产物路径、证据边界和下一步行动。它有意排除了提示、私有推理和完整的工具载荷。`skill-status` 会渲染这一阶段状态；仅在需要恢复诊断时，才检查会话 JSON 以获取持久化的细节。

## 初步调试命令

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli validate --task T3.6-SEC-INTRO --scope outputs --workspace ./workspace/project-a
python -m researchos.cli validate --task T3.6-VISUALS --scope inputs --workspace ./workspace/project-a
python -m researchos.cli trace <run-id> --workspace ./workspace/project-a
tail -n 120 ./workspace/project-a/_runtime/logs/researchos.log
```

## 常见状态解读

| 控制台状态 | 含义 | 正确操作 |
| --- | --- | --- |
| `WAITING_INPUT` | 某个 Skill 需要人工材料 | 阅读 `user_inputs/<skill>/_intake.md` 或 `_followup_request.md`；添加/回答它并恢复同一会话。 |
| `WAITING_CONFIRMATION` | 输入已就绪，但 Skill 执行尚未被授权 | 明确选择 `执行` 或 `暂停`。 |
| `WAITING_ENVIRONMENT` | 缺少 TeX、Python 包、Docker 或提供程序先决条件 | 运行 `doctor`/预检；修复命名的环境项；恢复会话。 |
| `DEGRADED` | 某个非阻塞源/工具失败，同时替代方案继续运行 | 阅读阶段总结和源健康状况表；不要假设覆盖率为零。 |
| `unsupported` | 证据无法支持所请求的结论 | 添加指定的证据，削弱声明，或选择其他方向。 |
| `waiting_evidence`（Skill 阶段） | 集成工作流在预检后存在已知的证据缺口 | 授权限定范围的检索，上传指定的源，或选择更窄/措辞更弱的输出。 |
| 验证失败 | 前置契约尚未就绪，或产物存在但违反了其声明的契约 | 用 `validate --task <task> --scope inputs` 检查前置材料，用 `--scope outputs` 检查已生成产物；修复指定的文件/状态后再恢复。 |

## T3.6 示例：章节验证

Survey 章节验证会检查写作契约、引用、语言、章节归属和持久化状态。这不是要求一个神奇的固定表述。如果引言被拒绝，请检查命名的产物和状态：

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

对于相同的 survey 计划，`build_survey_state` 是幂等的：它会保留那些文件和提纲指纹仍然有效的已完成章节。更改计划或写作契约会故意将受影响的章节恢复为待处理状态。

对于 `T3.6-SEC-*`，`resume` 首先验证所声明的章节及其匹配的 `survey_state` 条目。当两者均有效时，控制台会报告该章节正在推进，而无需再次通过提供程序重写。章节任务不能写入兄弟章节、提纲、survey 汇编文件、图表或编译输出；试图跨章节修改会是一个显式的访问错误，而非隐藏的状态更改。

## T3.6 汇编与 Survey 审计

`validate --task T3.6-ASSEMBLE` 检查当前存储的汇编清单和审计信息。在更正了引用的来源、BibTeX 条目、章节文件、计划或状态文件后，在通过提供程序恢复之前，使用确定性审计命令：

```bash
python -m researchos.cli audit-survey \
  --workspace ./workspace/project-a

python -m researchos.cli validate \
  --task T3.6-ASSEMBLE \
  --workspace ./workspace/project-a
```

`audit-survey` 会写入 `drafts/survey/survey_audit.json` 和 `.md`，仅报告阻塞性的失败检查，且不调用 LLM。它区分了三种常见情况：

| 失败类型 | 含义 | 修复范围 |
| --- | --- | --- |
| `citation_diversity` | 引用使用确实过于集中，或引用的不同键过少。重复上限会随着总引用次数而变化，因此，一份长篇 survey 不会仅因某篇基础论文超过固定次数就被拒绝。 | 在声明支持允许的情况下，添加相关的现有引用或移除冗余引用。 |
| `bibliography_quality` | 某个被引用的 BibTeX 记录格式错误、包含占位符，或期刊/会议类型不正确。 | 修复 `literature/related_work.bib`，然后重新汇编一次，以便指纹更新。 |
| `citation_claim_alignment` 或章节/深度检查 | 被引用的段落或指定章节不满足其实际契约。 | 仅修改受牵连的章节及其证据锚点；不要重写不相关的 Survey 章节。 |
| `survey_graphics_layout` | 双栏 CCF 模板中包含一个普通的 `figure`，其图片使用了不安全的 `\\textwidth` 宽度。它可能编译成功，但会与第二栏文本重叠。 | 使用 `figure*` 来放置全宽的类图，或将普通图表改为 `\\columnwidth`/`\\linewidth`；重新汇编、审计并编译。 |

汇编 Agent 会直接收到失败的检查详情。它不能在未更改相关输入的情况下重复调用 `assemble_survey`。如果所需证据不可用，它会写入一个修复计划并暂停，而不是将阻塞因素隐藏在重试之后。

普通的 `latex_compile` 调用不会重写已审计的 TeX 源代码。可选的宽表格 `resizebox` 转换是显式启用的，因为它属于源代码编辑：仅在刻意的写作修复步骤中使用它，然后重新汇编并重新审计，之后才将结果视为当前版本。

## T3.6 审阅与编译恢复

`T3.6-REVIEW` 在汇编后检查展示效果和学术结构。它可以修补指定的章节、重新汇编、重新审计、写入审阅/行动文件，并绑定当前的输入指纹。它不得直接覆盖 `survey.tex`：文档是由章节文件、模板、状态和参考文献派生而来的。如果审阅发现标题或模板缺陷，使用 `assemble_survey(title=...)` 重新构建，记录该操作，并在后续汇编之前确保标题来源持久化。

在任何审阅驱动的汇编之后，旧的 PDF/报告有意变为过时。确定性编译验证器会直接报告这一点，例如 `survey_compile_report.pdf_mtime 早于当前 survey.tex`。这不是审计循环：只需运行一次真正的 `latex_compile` 阶段，然后再次验证 `T3.6-COMPILE`。切勿手动编辑 `survey_compile_report.json` 或重用之前的 PDF 哈希。

## T4 结构化写入失败

T4 通过 `write_structured_file` 写入 `ideation/idea_rationales.json` 和 `ideation/idea_scorecard.yaml`。失败意味着对象在写入磁盘前被拒绝，而不是运行时丢失了文件。现在，进度结果会包含模式、目标产物和精确路径。

| 诊断模式 | 含义 | 正确修复 |
| --- | --- | --- |
| `$.ideas[n].counterfactual_check [required]` | 每个评分卡候选项都需要一个反事实结果类别。 | 添加 `collapses`、`survives_weakened`、`independent` 或 `insufficient_evidence` 之一，并附上解释说明。 |
| `decision.rejection_reason [type]` | 模式需要列表格式，即使只有一个解释。 | 使用 `rejection_reason: ["原因"]`；为被拒绝/延迟/合并的候选项保留 `can_revisit_if`。 |
| `literature_observations[n].strength [enum]` | 散文标签不是有效的证据级别。 | 使用 `direct`、`indirect` 或 `weak`；不要用 `supporting` 代替。 |
| `minimum_experiment.source_refs [required]` | 声称支持/用户提供的协议需要一个来源。 | 添加工作区/路径锚点，或诚实降级为 `proposed_not_verified`/`unknown`。 |

T4 提示和 Gate1 渲染器会保留所有候选项。在不同字段上重复出现错误，并不是盲目调用相同写入操作的理由：应修复报告的对象并重试。当外部包装器仅输出错误代码时，`trace <run-id>` 包含完整的有界诊断载荷。

## 不应从日志中推断的内容

- 检索覆盖率的缺口不是研究缺口。
- 引用图谱或排名信号不是最终的科学判断。
- 工具成功只意味着工具完成了；阶段验证决定其产物是否可用。
- 模型生成的自然语言计划，在没有可追溯的当前项目输入的情况下，不是一个实验方案。
