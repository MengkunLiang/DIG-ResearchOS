# ResearchOS Manuscript Writing Design

本文档说明 ResearchOS 的 T8 论文写作系统。核心原则是：不能让 LLM 一次端到端写完整篇 paper；必须先锁定资源、证据和章节作业，再按 section 独立调用 Writer，最后拼装、审稿和修订。

## 1. 设计目标

T8 要把前面阶段的 `synthesis.md`、paper notes、`comparison_table.csv`、`hypotheses.md`、`idea_scorecard.yaml`、`novelty_audit.md`、`results_summary.json`、`integrity_audit.json`、`drafts/result_to_claim.json`、`drafts/experiment_evidence_pack.json`、run logs、代码/配置/图表 artifact 转化为可审计 manuscript。单次 prompt 写作会浪费这些材料，也会造成上下文爆炸、数字漂移、术语不一致、claim 过强和后半段质量衰减。

当前主链是：

```text
T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-METHOD
 -> T8-SEC-EXPERIMENTS
 -> T8-SEC-RELATED
 -> T8-SEC-ANALYSIS
 -> T8-SEC-INTRO
 -> T8-SEC-CONCLUSION
 -> T8-SEC-ABSTRACT
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
 -> T9
```

`T8-PAPER-CLAIM-AUDIT` 是 T9 前的最终 evidence gate：它读取 `paper.tex`、`experiment_evidence_pack.json` 和 `result_to_claim.json`，检查正文数字和强 claim 是否能追溯到实验审计。未在 evidence pack 中出现的实验数字是 FAIL，不是软提示；mock-only evidence、forbidden wording、unsupported strong claim 也会阻断进入 T9。`paper_claim_audit.json` 会写 `input_fingerprints`，绑定当前 paper/evidence/result-to-claim 的 hash；resume 或 deterministic manuscript refresh 时会同步刷新该 audit，避免旧审计放行新正文。旧入口 `T8` 和旧报告中的 `next_task: T8-WRITE` 会优先映射到 `T8-STYLE-GATE`；只有 `drafts/writing_style.json` 已存在且合法时，才直接进入 `T8-RESOURCE`。旧 `T8-SECTIONS` 只作为兼容入口，不再是主链正文写作节点。旧 `T8-SEC-LIMITATIONS` 已移除，limitations 必须写进 Conclusion 的 `\subsection{Limitations}`。

当前 Reviewer 链路是“单节点 section-aware”：`T8-REVIEW-N` 在一次 ReviewerAgent 调用里要求先写所有 `round_N_sections/*.md`，再写 `round_N.md`。这已经比整篇凭印象审稿强，但还不是“每个 section 一次独立 LLM 调用”。如果要把审稿做到和 Writer 一样严格，应采用本文 13.1.1 的 per-section Reviewer 状态机设计。

## 2. 参考资料

本设计参考 `/mnt/data/reference/opendraft-master` 的 section compose/QA/export、`/mnt/data/reference/academic-research-skills-main/academic-paper/SKILL.md` 的 structure/argument blueprint/section-by-section drafting/citation compliance，以及 `/mnt/data/reference/claude-scientific-writer-main` 的真实 citation 和 source-first 写作规范。官方规范参考 NeurIPS checklist、ICML author instructions、ICLR author guide、ACM artifact badging 和 Nature reporting/code sharing guidance。

这些资料只用于设计流程和质量门，不把领域知识结论硬编码进 tool。

## 3. Agent 和 Tool 边界

Writer LLM 负责：论证结构、contribution wording、method explanation、related work positioning、claim strength、limitations、章节 prose 和修订取舍。

Tool/validator 负责：资源索引、BibTeX key 抽取、result metric 抽取、section/evidence/figure slot、`paper_state.json` 状态、section outline 文件、LaTeX 拼装、citation/number/section/figure 机械审计。

新增工具：

- `initialize_manuscript_state`：读取全局大纲和计划，生成 `drafts/paper_state.json` 与 `drafts/section_outlines/*.md`。
- `update_manuscript_section_state`：某个 section 写完后更新 `paper_state.json` 中该 section 的状态、路径、长度和摘录。
- `build_manuscript_registries`：根据 resource/evidence/figure plan 生成 `cdr_claim_ledger.json`、`claim_ledger.json` 和 `figure_registry.json` 三个机械 registry seed。
- `build_manuscript_revision_patches`：读取综合 review 和逐章节 review，把 issue 机械定位到 section/file/severity，生成 `drafts/patches/round_N_patches.json`。它不决定修订文本，只防止 Writer 在 revise 阶段整体重写。
- `audit_paper_claims`：读取 `drafts/paper.tex`、`drafts/experiment_evidence_pack.json` 和 `drafts/result_to_claim.json`，生成 `drafts/paper_claim_audit.md/json`。它不替代 Reviewer 的语义判断，但会稳定拦截 mock-only evidence、未在 evidence pack 中出现的实验数字和过强 claim 风险。

## 4. 分阶段执行

### T8-RESOURCE

### T8-STYLE-GATE

`WriterAgent(style_gate)` 根据 `project.yaml.target_venue` 推断建议写作风格，然后调用 `ask_human` 让用户选择 `is`、`ccf_a` 或 `both`，并写 `drafts/writing_style.json`。`writing_style.json` 必须包含 `human_interaction_id`，且该 id 必须出现在 `_runtime/human_interactions.jsonl` 中；这让风格选择成为可追踪的人类决策，而不是 Writer 自己补一个默认值。如果运行环境不能输入或回答为空，runtime 会暂停等待 resume，不允许模型伪造选择。

### T8-RESOURCE

`WriterAgent(resource_index)` 调用 `build_manuscript_resource_index` 扫描项目、文献、假设、新颖性审计、实验、图表、表格和代码资源，写 `drafts/manuscript_resource_index.json`。随后调用 `plan_manuscript_sections` 写 `drafts/section_plan.json`，再调用 `plan_manuscript_evidence` 写 `drafts/evidence_plan.json` 和 `drafts/figure_table_plan.json`。最后调用 `build_manuscript_registries` 生成 `drafts/cdr_claim_ledger.json`、`drafts/claim_ledger.json` 和 `drafts/figure_registry.json`，再调用 `build_alignment_matrix` 写 `drafts/alignment_matrix.json`。

Validator 会解析 JSON，而不是只看字符串长度；必须有 `artifacts`、核心 sections、`claim_slots`、`experiments_main_result`、`fig:main_results`、`tab:main_results`，并要求三个 registry seed 的 `semantics`、核心列表和 CDR claim 字段结构正确。

### T8-WRITE

`WriterAgent(outline)` 写 `drafts/outline.md`。它必须包含 title candidates、paper thesis、contribution map、section-by-section argument、figure/table plan 和 claim ledger。这里是 LLM 知识判断阶段：tool 只给资源和 slots，LLM 决定论文如何讲。

### T8-SECTION-PLAN

`WriterAgent(section_plan)` 不写正文。它调用：

```text
initialize_manuscript_state(...)
```

输出：

- `drafts/paper_state.json`
- `drafts/section_outlines/abstract.md`
- `drafts/section_outlines/introduction.md`
- `drafts/section_outlines/related_work.md`
- `drafts/section_outlines/methodology.md`
- `drafts/section_outlines/experiments.md`
- `drafts/section_outlines/analysis.md`
- `drafts/section_outlines/conclusion.md`

`paper_state.json` 包含 `semantics=shared_state_for_section_by_section_writing_not_final_claims`、`section_order`、每章状态、每章目标文件、`shared_facts.bib_keys`、`shared_facts.result_metrics`、claim slots 和 planned visuals。它是防止数字漂移和术语漂移的共享状态。若旧 workspace 中已有错误语义的 `paper_state.json`，`T8-SECTION-PLAN` 会在进入 LLM 前用 `initialize_manuscript_state` 确定性修复并跳过重复 LLM 写作。续跑前也可以运行 `validate --task T8-SECTION-PLAN`，validator 会复用同一安全修复逻辑后再返回结果。

当前实测行为：`local-test5` 曾停在 `PAUSED / T8-SECTION-PLAN`，修复后的 `paper_state.json` 通过 `validate --task T8-SECTION-PLAN` 后，再执行 `resume` 会先跳过 T8-SECTION-PLAN 的 LLM 调用，然后直接进入 `T8-SEC-METHOD`。如果用户在 `T8-SEC-METHOD` 中途中断，下一次 resume 会从 Method 单章继续，不会回头重建 section plan。

### T8-SEC-METHOD

`WriterAgent(section_draft, section_id=methodology)` 只写 `drafts/sections/methodology.tex`。它读取 `paper_state.json`、`section_outlines/methodology.md`、`hypotheses.md`、`exp_plan.yaml` 和可用 config/code artifacts，讲清方法名、机制、输入输出、算法/流程和实现细节。它不能用实验结果证明方法有效，不能写其它章节。写完调用 `update_manuscript_section_state(section_id="methodology")`。

### T8-SEC-EXPERIMENTS

只写 `drafts/sections/experiments.tex`。它读取 `results_summary.json`、`ablations.csv`、run artifacts、`exp_plan.yaml` 和 figure/table plan，写 setup、datasets、baselines、metrics、seed、compute、main results、ablations。所有数字必须来自实验 artifact；缺失统计写 TODO 或 limitation。

### T8-SEC-RELATED

只写 `drafts/sections/related_work.tex`。它读取 `synthesis.md`、`comparison_table.csv`、paper notes 和 `related_work.bib`，按 taxonomy/问题维度组织，不逐篇流水账。每个 citation key 必须来自 `.bib`。

### T8-SEC-ANALYSIS

只写 `drafts/sections/analysis.tex`。它读取 ablations、iteration log、novelty audit 和前面 method/experiments 的状态，解释机制证据、替代解释、failure cases、sensitivity。这里允许 LLM 做解释，但必须把依据回指到 artifact。

### T8-SEC-INTRO

只写 `drafts/sections/introduction.tex`。它在 method 和 experiments 已经成稿后运行，读取前两章以及 global outline，写 motivation funnel、gap、insight、contribution bullets 和 result headline。Intro 不能承诺 method/experiments 没有支持的 claim。

### T8-SEC-CONCLUSION

只写 `drafts/sections/conclusion.tex`。它读取 introduction、experiments、risks、novelty audit、results、`result_to_claim.json`、`experiment_evidence_pack.json` 和 `paper_state.json`，收束贡献与未来工作，不引入新 claim。Limitations 在这一章中用 `\subsection{Limitations}` 单独写清，包括外部执行器证据边界、mock/dry-run 是否只用于协议验证、baseline 覆盖、数据规模、外部有效性和复现性风险。

### T8-SEC-ABSTRACT

只写 `drafts/sections/abstract.tex`。它最后运行，读取主要章节，压缩 problem、method、evidence、result、contribution。Abstract 不放正式引用：不使用 LaTeX citation command，不写作者-年份括号引用，也不写数字引用；具体 prior work citation 放到 Introduction 或 Related Work。Abstract 也不能引入正文没有的数字、claim、术语或未讨论文献。

### T8-DRAFT

`WriterAgent(draft)` 调用 `assemble_manuscript` 从 `drafts/sections/*.tex` 拼装 `drafts/paper.tex`，加入 LaTeX wrapper、title、abstract、正文和 `\bibliography{related_work}`。随后 Writer 做全局 spot-check：术语、变量名、baseline 名称、章节过渡、intro/conclusion 呼应、method/experiment setup 一致性。若需要改正文，先改对应 section 文件，再重新拼装；不要直接一次性重写整篇 `paper.tex`。最后调用 `audit_manuscript_claims` 写 `drafts/manuscript_audit.md`，调用 `audit_writing_craft` 写 `drafts/craft_audit.md`；如果存在 `drafts/experiment_evidence_pack.json`，必须调用 `audit_paper_claims` 写 `drafts/paper_claim_audit.md/json`。

### 章节深度协议

T8 的 section-by-section 不是把一次整篇写作拆成几个短文件。除 Abstract 外，每个 section 都应围绕本章责任充分展开，但不设置僵硬字数下限：

- Methodology 应包含 artifact 总览、组件职责、输入输出、algorithm/notation、关键设计选择，以及被放弃的替代设计与理由。
- Experiments 应包含 RQ、数据/切分、baseline、metrics、seed/compute、main result、ablation/error analysis，并让主要数字回到结果 artifact。
- Related Work 应按 2-4 个 competing rationale 或 taxonomy subsection 组织，每个 subsection 说明代表工作、共同 tension 和本文定位。
- Analysis 应解释 design rationale 是否被支持，覆盖 alternative explanation、failure case、sensitivity 或 boundary，而不是重复实验数字。
- Introduction 和 Conclusion 应形成完整 problem -> method -> evidence -> contribution 论证链，避免 placeholder intro 或复制 abstract 式 conclusion。

如果证据不足，Writer 应写 TODO/limitation，而不是为了凑篇幅编造事实；如果证据充足，Writer 应展开到目标 venue 需要的写作密度。

### T8-SELF-CHECK

写 `drafts/self_check.md`，必须覆盖 argument chain、number audit、citation audit、figure/table audit、reproducibility audit、外部执行器证据边界、paper claim audit 和 High/Medium/Low TODO。

### T8-REVIEW-1 / T8-REVIEW-2

当前实现中，Reviewer 是 section-aware 但仍是单节点执行。每轮 `T8-REVIEW-N` 先写：

- `drafts/review_rounds/round_N_sections/abstract.md`
- `drafts/review_rounds/round_N_sections/introduction.md`
- `drafts/review_rounds/round_N_sections/related_work.md`
- `drafts/review_rounds/round_N_sections/methodology.md`
- `drafts/review_rounds/round_N_sections/experiments.md`
- `drafts/review_rounds/round_N_sections/analysis.md`
- `drafts/review_rounds/round_N_sections/limitations.md`
- `drafts/review_rounds/round_N_sections/conclusion.md`

然后综合生成 `drafts/review_rounds/round_N.md`。第一轮重点事实、逻辑、证据和结构，第二轮额外检查上一轮 High/Medium issue 是否闭环，并关注表达、清晰度和一致性。

目标实现应进一步拆成 per-section Reviewer：每个 section review 是一个独立 Reviewer 调用，最后再由 synthesis 节点只汇总已有逐章审稿，不再重新生成逐章审稿。这样可以避免 Reviewer 一次读完整篇后遗漏后半段，也能让失败 resume 精确回到某个 section review。

### T8-REVISE-1 / T8-REVISE-2

Writer 先调用 `build_manuscript_revision_patches(round_num=N)`，把本轮综合 review 和 `round_N_sections/*.md` 转成 `drafts/patches/round_N_patches.json`。Patch list 只定位 issue，不替代 LLM 判断。Writer 按 High -> Medium -> Low 顺序处理 patch：能定位到章节的问题先改 `drafts/sections/<section>.tex`，每改完一章调用 `update_manuscript_section_state(status="revised")`；global issue 优先拆到多个章节。全部修订后重新调用 `assemble_manuscript` 拼装 `paper.tex`，刷新 `manuscript_audit.md`，并写 `drafts/revision_response_round_N.md` 记录 resolved/unresolved/deferred。无法解决的问题必须说明原因和风险，不允许只写“已修订”。

## 5. 事实一致性与数字漂移协议

T8 的事实控制不是让 tool 代替 LLM 写论文，而是让 LLM 的判断有稳定事实源。`paper_state.json`、`results_summary.json`、`ablations.csv`、`related_work.bib`、`evidence_plan.json` 和 `figure_table_plan.json` 共同构成事实边界。

需要抽取和核对的 claim 类型：

- Numeric claim：指标值、提升百分比、运行时间、seed 数、样本量、页数、baseline 数量。
- Comparative claim：best / stronger / improves / lower cost / more robust。
- Causal or mechanism claim：某组件导致某效果、某机制解释某现象。
- Coverage claim：覆盖哪些 dataset、baseline、领域、论文家族。
- Categorical claim：first / novel / SOTA / never / always / all。

当前机械门：

- `audit_manuscript_claims` 抽取 LaTeX citation key、数字、figure/table ref 和核心章节。
- citation key 不在 `related_work.bib` 中就是 fail。
- section 文件包含整篇 wrapper 就是 fail。
- revise 阶段缺 patch list、revision response 或 audit 就是 fail。

人工/LLM 判断门：

- 数字虽然出现在 artifact 中，但是否被正确解释，需要 Writer/Reviewer 判断。
- Claim 是否过强、gap 是否真实、机制解释是否有因果证据，需要 Reviewer 判断。
- 如果 audit 报出无法自动确认的数字，Writer 应回读 artifact；能确认则保留并说明来源，不能确认则降级为 TODO/limitation。

当前已经加入结构化 registry seed：

```json
{
  "claim_id": "C001",
  "section": "experiments",
  "claim_type": "numeric|comparative|mechanism|coverage|categorical",
  "claim_text": "...",
  "source_artifacts": ["experiments/results_summary.json"],
  "verdict": "pass|warn|fail",
  "notes": "..."
}
```

`drafts/claim_ledger.json` 由 `build_manuscript_registries` 生成，只保存 claim slot、候选证据、citation pool、metric refs 和 support status 空位。它不是最终科学判断；Writer/Reviewer 必须读取原始 artifact 后填写或质疑 claim。另一个 `drafts/cdr_claim_ledger.json` 专门记录 CDR tuple hint 和 contribution claims：`problem_frame`、`design_rationale`、`artifact`、`data_view`、`evaluation_mode`、`contribution_type`、`boundary_conditions` 等字段是写作和审稿时的责任分配，不允许用 provenance 数量替代贡献判断。

## 6. 图表和实验结果进入写作的协议

图表不是装饰，也不是每篇论文强制大量生成。参考资料中关于 visualization 的规范用于质量门，但 ResearchOS 采用 artifact-driven visuals：只有当已有结果、ablation、机制流程或对比表确实需要可视化时才生成或引用图表。

选择规则：

- 精确数字、多 dataset 多 baseline 对比：优先 table。
- 趋势、分布、sensitivity、ablation curve：优先 figure。
- 多阶段方法流程：可用 schematic，但必须来自 hypotheses/exp_plan/code 结构，不伪装成实验结果。
- 1-2 句话就能说清的结果：不强制画图。

每个 planned visual 必须先出现在 `drafts/figure_table_plan.json` 中，例如 `fig:main_results`、`fig:ablation`、`tab:main_results`、`tab:related_work`。Experiments/Analysis 只能引用已有 artifact 或可由 artifact 生成的图表；缺数据时写 TODO 或 limitations。

图表质量门：

- 数据来源明确：`results_summary.json`、`ablations.csv`、run artifact、comparison table 或 hypotheses/exp_plan。
- caption 说明 metric、dataset、seed/CI/误差线是否存在。
- LaTeX label 唯一，正文首次引用存在。
- 表格数字和正文数字一致。
- figure/table 不引入正文没有讨论的 claim。
- 缺少图文件或表格数据时，audit 应保留 open issue。

当前已经加入 `drafts/figure_registry.json` registry seed：

```json
{
  "visual_id": "fig:main_results",
  "type": "figure|table|schematic",
  "intended_section": "experiments",
  "source_artifacts": ["experiments/results_summary.json"],
  "generated_files": ["drafts/figures/main_results.pdf"],
  "latex_snippet": "\\\\begin{figure}...",
  "status": "available|planned|missing_data|deferred"
}
```

该 registry 只登记 planned visuals、source artifacts、label、file_path/caption 空位和状态。它不自动生成图，也不替 LLM 决定图表想传达的学术信息；后续 plot/table 工具应在此基础上填充 `file_path` 和可复核的 LaTeX snippet。

## 7. 核心 Artifacts

- `manuscript_resource_index.json`：资源地图，不生成学术结论。
- `section_plan.json`：每章 required/available/missing inputs、expected outputs、LLM tasks。
- `evidence_plan.json`：claim slots，例如 `intro_problem_gap`、`method_mechanism`、`experiments_main_result`。
- `figure_table_plan.json`：figure/table slots，例如 `fig:main_results`、`tab:main_results`。
- `paper_state.json`：逐章节共享状态，包含 citation keys、result metrics、section statuses。
- `result_to_claim.json`：审计后 result-to-claim map，限制 claim 的 support status、allowed wording 和 forbidden wording。
- `experiment_evidence_pack.json`：规范化实验写作证据包，记录 metrics、artifacts、integrity status 和 limitations。
- `section_outlines/*.md`：每章局部大纲和证据作业单。
- `sections/*.tex`：单章正文源文件。
- `manuscript_audit.md`：机械审计 hint，不是语义事实证明。
- `paper_claim_audit.md/json`：论文 claim 与实验 evidence pack 的机械追踪审计。
- `patches/round_N_patches.json`：review issue 的机械定位，不是最终修订判断。
- `revision_response_round_N.md`：本轮修订响应，记录 resolved/unresolved/deferred。

## 8. 质量门

当前硬门：

- resource/plan JSON 必须可解析且结构正确。
- `T8-SECTION-PLAN` 必须生成 `paper_state.json` 和 7 个 section outline。
- 每个 `T8-SEC-*` 只能验证对应一个 section 文件，且必须更新 `paper_state.json` 状态为 `written` 或 `revised`。
- section 文件禁止 `\documentclass`、`\begin{document}`、`\end{document}`。
- section 文件也不能夹带其它核心章节标题；例如 `experiments.tex` 里出现
  `\section{Conclusion}` 会被 validator 拒绝。这保证 section-by-section 不是“一个
  agent 一口气写完再拆文件”。
- draft/revise 必须存在 `manuscript_audit.md`。
- revise 必须存在 `drafts/patches/round_N_patches.json` 和 `drafts/revision_response_round_N.md`。
- citation key 必须存在于 `related_work.bib`。
- 当前 reviewer 必须生成逐章节 review 和综合 review。
- 目标 per-section reviewer 中，`T8-REVIEW-N-SEC-*` 只校验单个 section review；`T8-REVIEW-N-SYNTH` 才校验所有 section review 和综合 review。

不应硬编码为 tool 的内容：

- gap 是否真实、claim 是否有足够学术贡献、method mechanism 是否合理、related work taxonomy 是否最佳、语义级 claim-to-source support。它们需要 LLM/Skill/Reviewer 判断，但判断结果必须落到可审计 artifact。

## 9. 外部实验执行器与 Claim 证据边界

当前主链走 `T4.5 -> T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-DRY-RUN/T5-EXTERNAL-WAIT -> T7-INGEST -> T7-AUDIT -> T7-POST-NOVELTY -> T7-CLAIMS -> T7.5 -> T8-RESOURCE`。写作时必须说明和遵守：

- ResearchOS 不声称自己在 T5-T7 内部实现并运行了真实实验；它负责协议、handoff、摄取、审计、result-to-claim 和写作闭环。
- 如果当前 workspace 只有 `mock_only=true` 的 dry-run artifact，Experiments 只能写“协议 dry-run 已跑通”，不能写成论文实证结果。
- 任何数值 claim 必须能追溯到 `drafts/experiment_evidence_pack.json` 中的 metric 和 source artifact。
- 任何比较性或机制性 claim 必须受 `drafts/result_to_claim.json` 的 `support_status`、`allowed_wording`、`forbidden_wording` 和 `limitations` 约束。
- Introduction 可以概括审计后证据支持的主发现，但不能把 weak/mock/unsupported mapping 写成 confirmed result。
- Experiments 要明确 executor、result pack、run manifest、raw results、configs、logs、seeds/baselines 的可追溯性。
- Limitations 必须说明外部执行器、artifact 完整性、baseline 覆盖、数据规模、seed/compute 和复现边界。

## 10. 测试策略

已覆盖：

- manuscript resource/evidence/figure plan tool。
- `build_manuscript_registries` 生成 `cdr_claim_ledger.json`、`claim_ledger.json` 和 `figure_registry.json`，Writer resource validator 已强制检查。
- `initialize_manuscript_state` 和 `update_manuscript_section_state`。
- Writer resource JSON 真校验。
- Writer section_plan validator。
- Writer single-section validator：缺 state update 会失败。
- section wrapper 禁止规则。
- draft/revise audit 要求。
- Reviewer section-aware prompt 和逐章节 review validator。
- T7.5 gate 进入 `T8-RESOURCE`。
- T4 resume prefinalize 对新版 ideation artifacts 的校验。
- T9 `latex_compile` 会写 `submission/compile_report.json`，Submission validator 会用 hash/mtime/path/attempts 校验 PDF 确实来自当前 `main.tex`。

后续建议：

- 无真实 LLM 的完整 T8 runner E2E：覆盖所有 `T8-SEC-*` 节点。
- 真实 LLM 小 workspace smoke：只跑 `T8-RESOURCE`、`T8-SECTION-PLAN`、一个 `T8-SEC-*`、`T8-DRAFT`。
- 结构化 audit JSONL。
- 图表生成 tool：从 results/ablations 生成 plot script、image 和 LaTeX snippet，并回写 `figure_registry.json`。

## 11. 每个阶段的执行契约

本节把 T8/T9 写作链路写成 runtime 可执行契约，避免“看起来分阶段，实际上仍是一口气写完”的问题。每个阶段都必须遵守三个原则：

- 单阶段只做当前阶段声明的工作，不顺手完成后续阶段。
- LLM 负责学术判断和语言组织；tool 负责索引、拼装、校验、定位和状态记录。
- 每次 LLM 调用的上下文只放本阶段必需资料，避免把全项目一次塞进 prompt。

### 11.1 T8-RESOURCE

输入：

- `project.yaml`
- `literature/synthesis.md`
- `literature/comparison_table.csv`
- `literature/related_work.bib`
- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/idea_scorecard.yaml`
- `ideation/novelty_audit.md`
- `experiments/results_summary.json`
- `experiments/ablations.csv`
- `experiments/runs/`
- `pilot/` 和 `novelty/`，如果存在

执行：

1. `WriterAgent(resource_index)` 先调用 `build_manuscript_resource_index`。
2. 工具扫描上述 artifact，抽取资源类型、文件路径、短 preview、bib keys、result-like JSON/CSV 和可用 figure/table 文件。
3. Writer 读取 resource index，判断 paper type、目标 venue、主要证据缺口。
4. 调用 `plan_manuscript_sections` 生成 `section_plan.json`。
5. 调用 `plan_manuscript_evidence` 生成 `evidence_plan.json` 和 `figure_table_plan.json`。
6. 调用 `build_manuscript_registries` 生成 CDR claim ledger、generic claim ledger 和 figure registry seed。

LLM 职责：

- 判断哪些资料对 manuscript 真正重要。
- 判断 paper 应更像 empirical paper、systems paper、method paper 还是 case-study paper。
- 为每章列出需要解释的问题和缺失资料。

Tool 职责：

- 不写学术结论。
- 只做资源索引、文件存在性、BibTeX key、数字候选、图表候选、claim/visual slot 和结构化计划。

硬性输出：

- `drafts/manuscript_resource_index.json`
- `drafts/section_plan.json`
- `drafts/evidence_plan.json`
- `drafts/figure_table_plan.json`
- `drafts/cdr_claim_ledger.json`
- `drafts/claim_ledger.json`
- `drafts/figure_registry.json`

失败处理：

- JSON 不可解析：Writer 必须重写对应 JSON。
- 缺 `experiments_main_result` 或 `fig:main_results` / `tab:main_results` 这类核心 slot：Writer 不能进入正文写作，应先补计划或标记 missing evidence。
- registry `semantics` 不正确、CDR tuple 缺核心字段、claim/visual 列表为空时，validator 会失败；应重新调用 `build_manuscript_registries`，不要让 LLM 手写 JSON 凑格式。

### 11.2 T8-WRITE

输入：

- `drafts/manuscript_resource_index.json`
- `drafts/section_plan.json`
- `drafts/evidence_plan.json`
- `drafts/figure_table_plan.json`
- `drafts/cdr_claim_ledger.json`
- `drafts/claim_ledger.json`
- `drafts/figure_registry.json`
- 文献、假设、实验和项目文件的短 preview

执行：

1. Writer 读取上述计划。
2. 必要时用 `read_file` 查看关键 artifact 的完整内容。
3. 写 `drafts/outline.md`。

`outline.md` 必须包含：

- title candidates
- paper thesis
- contribution map
- section-by-section argument
- evidence map
- figure/table plan
- claim ledger draft
- CDR narrative plan：每条贡献如何连接 `problem_frame`、`design_rationale`、`contribution_type`、证据和边界条件
- 外部执行器证据边界说明

LLM 职责：

- 设计论文叙事。
- 判断 related work taxonomy。
- 判断 related work 中哪些是 competing design rationales，哪些只是方法类别。
- 判断 contribution 的强弱措辞。
- 判断 introduction 的 problem funnel 和 method 的 exposition order。

Tool 职责：

- 只提供计划和资料索引，不替 LLM 决定论文观点。

硬性输出：

- `drafts/outline.md`

失败处理：

- 没有 `##` 章节结构、缺 Introduction/Related Work/Method/Experiments，validator 会失败。
- outline 太像目录而不是论证蓝图，Reviewer 后续应作为 major issue 提出。

### 11.3 T8-SECTION-PLAN

输入：

- `drafts/outline.md`
- `drafts/section_plan.json`
- `drafts/evidence_plan.json`
- `drafts/figure_table_plan.json`
- `literature/related_work.bib`
- `experiments/results_summary.json`
- `experiments/ablations.csv`

执行：

1. Writer 调用 `initialize_manuscript_state`。
2. 工具生成 `drafts/paper_state.json`。
3. 工具生成 8 个 `drafts/section_outlines/*.md`。
4. Writer 检查每章 outline 是否与 global outline 一致。

`paper_state.json` 必须记录：

- `semantics`
- `section_order`
- 每章 `file`、`outline`、`status`
- `shared_facts.bib_keys`
- `shared_facts.result_metrics`
- evidence slots
- planned visuals
- revision log

硬性输出：

- `drafts/paper_state.json`
- `drafts/section_outlines/abstract.md`
- `drafts/section_outlines/introduction.md`
- `drafts/section_outlines/related_work.md`
- `drafts/section_outlines/methodology.md`
- `drafts/section_outlines/experiments.md`
- `drafts/section_outlines/analysis.md`
- `drafts/section_outlines/conclusion.md`

失败处理：

- 任一 section outline 过短或缺失，不能进入 `T8-SEC-*`。
- `paper_state.json` 中 section 文件路径不等于 `drafts/sections/{section_id}.tex`，不能通过。

## 12. Section-by-Section 写作规范

每个 `T8-SEC-*` 是一次独立 Writer 调用。它不是一个 Writer 调用生成多章再拆文件。当前 validator 会拒绝 section 文件中的整篇 LaTeX wrapper，也会拒绝一个 section 文件夹带其它核心 `\section{...}` 标题。

每个 section 调用的标准输入：

- `drafts/paper_state.json`
- 本章 `drafts/section_outlines/{section_id}.md`
- 本章证据所需的最小 artifact
- 已完成的相邻章节 tail preview

每个 section 调用的标准输出：

- `drafts/sections/{section_id}.tex`
- `paper_state.json` 中该 section 的 `status=written`
- 必要时追加 `revision_log` 或 notes

### 12.1 Method / Methodology

目标：

- 让读者能复现方法逻辑，而不是只读到营销式描述。
- 解释 artifact 为什么应该这样设计，把 design rationale 和可操作机制落到算法/流程上。

读取：

- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/idea_scorecard.yaml`
- `drafts/evidence_plan.json`
- `external_executor/handoff_pack.json`
- `external_executor/run_manifest.json`
- `experiments/evidence_index.json`
- `experiments/configs/` 或 run configs
- legacy `pilot/pilot_code/`（仅旧内部实验显式产物存在时参考）

应写：

- problem formulation
- method name and notation
- mechanism intuition
- design rationale and design principles
- algorithmic steps
- implementation details relevant to experiments
- complexity or resource discussion when relevant

不应写：

- 未由 experiments 支持的性能结论。
- “first/SOTA/always”等强新颖性结论。
- 其它章节，如 Experiments 或 Conclusion。

### 12.2 Experiments

目标：

- 把实验设计、运行条件、主结果、消融和统计可靠性说清楚。
- 让 `data_view` 和 `evaluation_mode` 变成可验证证据，而不是只展示指标表。

读取：

- `experiments/results_summary.json`
- `experiments/ablations.csv`
- `experiments/seed_ensemble_summary.json`
- `experiments/iteration_log.md`
- `experiments/runs/*`
- `ideation/exp_plan.yaml`
- `drafts/figure_table_plan.json`

应写：

- datasets and splits
- baselines
- metrics
- evaluation mode and validity assumptions
- seed protocol
- compute/resource environment
- main result table/figure references
- ablation setup
- failure or negative result disclosure

数字规则：

- 任何指标值必须来自 experiment artifact。
- improvement 百分比必须能从 artifact 重新计算。
- 缺 confidence interval 时不要声称 statistical significance。

### 12.3 Related Work

目标：

- 解释本文和已有研究的关系，而不是罗列论文。
- 比较 competing design rationales 和 cross-paper tensions，而不只是按方法名分类。

读取：

- `literature/synthesis.md`
- `literature/comparison_table.csv`
- `literature/paper_notes/*.md`
- `literature/related_work.bib`

应写：

- taxonomy by problem/mechanism/evidence
- design-rationale competition and boundary differences
- key representative works
- how the proposed work differs
- what evidence gap remains

引用规则：

- 只能使用 `.bib` 中存在的 key。
- 不允许凭记忆发明 citation。
- 不确定某篇论文贡献时回读 note 或 synthesis。

### 12.4 Analysis / Discussion

目标：

- 解释结果为什么出现、什么时候有效、什么时候可能失败。
- 判断实验是否支持、削弱或只部分支持 `design_rationale`。

读取：

- `experiments/ablations.csv`
- `experiments/iteration_log.md`
- `ideation/novelty_audit.md`
- method and experiments section drafts

应写：

- mechanism interpretation
- design-rationale support or weakening evidence
- alternative explanations
- sensitivity or robustness observations
- failure cases
- implications for future work

边界：

- Analysis 可以做解释，但不能把相关性写成因果性。
- 如果没有专门 ablation，必须降级为 hypothesis or interpretation。

### 12.5 Introduction

目标：

- 用最少上下文让读者理解问题、缺口、核心 insight、方法和证据。
- 明确回答“如果本文成立，领域会怎样不同”，并把 contribution type 写成可被后文兑现的承诺。

读取：

- `drafts/sections/methodology.tex`
- `drafts/sections/experiments.tex`
- `drafts/outline.md`
- `drafts/paper_state.json`
- `literature/synthesis.md`

应写：

- broad problem
- specific gap
- problem frame and field-change argument
- why existing methods/evidence are insufficient
- core idea
- contribution bullets
- headline result only if experiments support it

边界：

- Introduction 在 Method 和 Experiments 后写，是为了避免承诺不存在的 claim。
- 不应引入正文没有展开的 dataset、baseline 或 mechanism。

### 12.6 Limitations

目标：

- 明确威胁、边界和可复现性限制。
- 把 `boundary_conditions` 写清楚，包括 CDR claim 没有被当前证据支持的部分。

读取：

- `ideation/risks.md`
- `ideation/novelty_audit.md`
- `experiments/iteration_log.md`
- `experiments/integrity_audit.json`
- `drafts/result_to_claim.json`
- `drafts/experiment_evidence_pack.json`

应写：

- dataset/domain limits
- design-rationale boundary conditions
- baseline coverage limits
- compute/seed limits
- external-executor provenance and handoff boundaries
- mock/dry-run outputs cannot support real empirical claims
- deployment or ethical limits when relevant

### 12.7 Conclusion

目标：

- 收束贡献，不引入新事实。
- 回到 contribution type 和可迁移 design knowledge。

读取：

- Introduction
- Experiments
- Limitations

应写：

- concise recap
- strongest supported finding
- transferable design knowledge
- restrained future work

### 12.8 Abstract

目标：

- 最后压缩全文。
- 压缩 `problem_frame`、`design_rationale`、artifact/evidence、result 和 `contribution_type`，不新增 claim。

读取：

- 所有 section drafts
- `paper_state.json`

应写：

- problem
- method
- key evidence
- contribution

边界：

- Abstract 不放正式引用；具体文献 attribution 放到 Introduction 或 Related Work。
- Abstract 不放正文没有的数字。
- Abstract 不夸大 novelty。

## 13. Review 和 Revision 规范

### 13.1 Review

Reviewer 必须写逐章节 review，再写综合 review。当前实现是在一个 `T8-REVIEW-N` 节点里完成这两步；目标实现应改成每章一个 Reviewer 节点，再由综合节点汇总。逐章节 review 至少覆盖：

- Section purpose check
- Evidence and number check
- CDR Alignment Check：本章是否兑现 `cdr_claim_ledger.json` 中对应的 CDR 职责
- Logic and writing issues
- Actionable fixes

Round 1 重点：

- 事实正确性
- claim 是否被实验支持
- design rationale 是否被方法和实验共同支撑
- contribution type 是否可信，是否存在 routine contribution risk
- related work 覆盖
- method 是否可复现
- section 是否缺关键内容

Round 2 重点：

- Round 1 issue 是否闭环
- 表达清晰度
- 术语一致性
- CDR Contribution Verdict 是否从 `Needs reframing` 收敛到 `CDR-ready` 或明确保留 major risk
- 过渡和整体读感
- 投稿前残留风险

### 13.1.1 Per-section Reviewer 状态机设计

目标不是让 tool 代替审稿判断，而是把 Reviewer 的阅读范围缩小到单章，使 LLM 能真正读细。推荐状态机结构如下：

```text
T8-SELF-CHECK
 -> T8-REVIEW-1-SEC-METHOD
 -> T8-REVIEW-1-SEC-EXPERIMENTS
 -> T8-REVIEW-1-SEC-RELATED
 -> T8-REVIEW-1-SEC-ANALYSIS
 -> T8-REVIEW-1-SEC-INTRO
 -> T8-REVIEW-1-SEC-LIMITATIONS
 -> T8-REVIEW-1-SEC-CONCLUSION
 -> T8-REVIEW-1-SEC-ABSTRACT
 -> T8-REVIEW-1-SYNTH
 -> T8-REVISE-1
 -> T8-REVIEW-2-SEC-METHOD
 -> T8-REVIEW-2-SEC-EXPERIMENTS
 -> T8-REVIEW-2-SEC-RELATED
 -> T8-REVIEW-2-SEC-ANALYSIS
 -> T8-REVIEW-2-SEC-INTRO
 -> T8-REVIEW-2-SEC-LIMITATIONS
 -> T8-REVIEW-2-SEC-CONCLUSION
 -> T8-REVIEW-2-SEC-ABSTRACT
 -> T8-REVIEW-2-SYNTH
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
```

这个顺序复用 Writer 的事实依赖顺序：先审 method/experiments/related/analysis 这些证据密集章节，再审 intro/abstract 这类压缩和承诺型章节。Intro 和 abstract 最后审，能检查它们是否过度承诺了正文没有支撑的 claim。

每个 `T8-REVIEW-N-SEC-*` 节点应配置：

```yaml
T8-REVIEW-1-SEC-EXPERIMENTS:
  agent: reviewer
  mode: section_review
  round: 1
  extra:
    section_id: experiments
  inputs:
    project: project.yaml
    paper_state: drafts/paper_state.json
    section: drafts/sections/experiments.tex
    section_outline: drafts/section_outlines/experiments.md
    manuscript_audit: drafts/manuscript_audit.md
    self_check: drafts/self_check.md
    results_summary: experiments/results_summary.json
    ablations: experiments/ablations.csv
    related_work_bib: literature/related_work.bib
  outputs:
    section_review: drafts/review_rounds/round_1_sections/experiments.md
  next_on_success: T8-REVIEW-1-SEC-RELATED
  next_on_failure: failed
```

不同 section 的最小输入应不同：

- `methodology`：`hypotheses.md`、`exp_plan.yaml`、`paper_state.json`、method section。
- `experiments`：`results_summary.json`、`ablations.csv`、run logs、experiment section。
- `related_work`：`synthesis.md`、`comparison_table.csv`、`related_work.bib`、related section。
- `analysis`：`ablations.csv`、`novelty_audit.md`、method/experiments section 的短 preview。
- `introduction`：method/experiments/contribution state、intro section、self-check。
- `limitations`：risks、novelty audit、external-executor evidence boundary、paper claim audit 和 limitations section。
- `conclusion`：intro/experiments/limitations 的短 preview、conclusion section。
- `abstract`：paper_state、intro/method/experiments/analysis/limitations/conclusion 的摘要或 tail preview。

`T8-REVIEW-N-SYNTH` 应只做综合，不再写任何 `round_N_sections/*.md`：

```yaml
T8-REVIEW-1-SYNTH:
  agent: reviewer
  mode: review_synthesis
  round: 1
  inputs:
    project: project.yaml
    paper_state: drafts/paper_state.json
    paper: drafts/paper.tex
    manuscript_audit: drafts/manuscript_audit.md
    self_check: drafts/self_check.md
    section_review_dir: drafts/review_rounds/round_1_sections
  outputs:
    review_report: drafts/review_rounds/round_1.md
  next_on_success: T8-REVISE-1
  next_on_failure: failed
```

Round 2 section review 节点额外读取：

- `drafts/review_rounds/round_1.md`
- `drafts/review_rounds/round_1_sections/{section_id}.md`
- `drafts/revision_response_round_1.md`
- 对应修订后的 `drafts/sections/{section_id}.tex`

这样 Round 2 可以逐章判断上一轮问题是否闭环，而不是只在综合报告里笼统说“已改进”。

ReviewerAgent 需要新增两个执行模式：

- `section_review`：只读一个 `section_id` 对应的 `.tex`、outline、paper_state 和必要证据，只写一个 `round_N_sections/{section_id}.md`。
- `review_synthesis`：读取所有逐章节 review、audit、self-check 和必要的全文结构，只写 `round_N.md`。

不建议让 `section_review` 直接读取完整 `drafts/paper.tex`。如果需要跨章一致性，只给相邻章节 200-400 字 preview 或 `paper_state.json` 中的摘要，避免它又退化成整篇审稿。

### 13.1.2 Validator 影响

当前 `ReviewerAgent.validate_outputs()` 只接受综合任务语义：它检查 `round_N.md`、`round_N_sections/` 和所有核心章节 review。per-section 拆分后应拆成三个校验分支：

1. `mode=section_review`
   - 只检查 `drafts/review_rounds/round_N_sections/{section_id}.md`。
   - 文件长度不少于 80-120 字符。
   - 必须包含 `# Section Review: <section_id>`。
   - 必须包含 `## Section Purpose Check`、`## Evidence And Number Check`、`## Logic And Writing Issues`、`## Actionable Fixes`。
   - `Actionable Fixes` 至少有一条 `High`、`Medium`、`Low` 或明确写 `No blocking issue`。
   - 不要求 `round_N.md` 存在，避免单章审稿节点被综合报告卡住。

2. `mode=review_synthesis`
   - 检查所有 `CORE_SECTIONS` 的 section review 已存在且通过结构校验。
   - 检查 `drafts/review_rounds/round_N.md` 存在，包含 `## 总体评价`、`## 主要问题`、`## 次要问题`、`## 数字验证`、`## 引用验证`、`## 总结`。
   - Round 2 必须出现上一轮闭环字段，例如 `Round 1 Closure` 或 `上一轮问题闭环`。
   - 不允许 synthesis 节点把 section review 文件整体重写为空或占位；可用 mtime 或内容 hash 记录在 `_runtime/task_recovery` 中做弱检测。

3. 兼容模式
   - 保留旧 `T8-REVIEW-1` / `T8-REVIEW-2` 的 aggregate validator，允许旧 workspace 和单任务调试继续运行。
   - 当 `ctx.extra.mode` 为空时走当前 aggregate 逻辑；当 `mode` 明确为 `section_review` 或 `review_synthesis` 时走新逻辑。

`researchos/schemas/validator.py` 的 task checker 也要跟着注册新节点。可以通过 task id 解析 round/section，构造：

```python
extra = {
    "mode": "section_review",
    "round": 1,
    "section_id": "experiments",
}
```

再调用 `ReviewerAgent().validate_outputs(ctx)`。综合节点则传：

```python
extra = {
    "mode": "review_synthesis",
    "round": 1,
}
```

`task_io_contract.py` 应为每个 per-section 节点声明单一 `section_review` output，综合节点声明 `review_report` output。不要让每个 section 节点都声明整个 `section_review_dir`，否则恢复和 prefinalize 会误判为整轮审稿已完成。

### 13.1.3 迁移风险

- 节点数会增加约 18 个，完整 T8 时间变长，但每次 LLM 调用更小，失败后 resume 更精确。
- 如果保持旧 `T8-REVIEW-N` 作为 aggregate，同时新增 `T8-REVIEW-N-SYNTH`，文档和 CLI 容易出现两个入口。推荐最终把旧名保留为兼容入口，主链使用 `*-SYNTH`。
- Reviewer 的 prompt 必须按 mode 分叉。`section_review` prompt 不能继续要求“一次写所有 section review”，否则拆状态机没有意义。
- Round 2 要避免只读上一轮综合报告。必须读取上一轮同 section review 和 revision response，否则闭环检查会偏泛。
- 如果 synthesis 节点仍读取完整 `paper.tex`，它可能重新审稿并覆盖 section findings。prompt 应明确 synthesis 只聚合和排序，不替代逐章审稿。

### 13.2 Revision

Revision 不允许直接整篇重写。标准流程：

1. 调用 `build_manuscript_revision_patches(round_num=N)`。
2. 读取 `drafts/patches/round_N_patches.json`。
3. 按 severity 排序处理 patch。
4. 对每个 patch 只读取目标 section、paper_state 和必要证据。
5. 修改对应 `drafts/sections/*.tex`。
6. 更新 paper_state section 状态为 `revised`。
7. 重新 assemble。
8. 刷新 manuscript audit。
9. 写 revision response。

如果 patch 是 global issue，应拆成多个 section-level patch。只有在跨章节术语体系整体错误时，才允许多 section 串行修改；仍然不能直接重写 `drafts/paper.tex` 作为唯一产物。

## 14. 图表生成模块设计

当前 T8 已有 `figure_table_plan.json` 和 `drafts/figure_registry.json` registry seed。后续应把实际图表生成进一步 tool 化。建议新增/完善工具：

- `generate_results_table`
- `generate_ablation_plot`
- `generate_method_schematic`
- `validate_visual_references`

建议产物：

- `drafts/figures/*.pdf`
- `drafts/tables/*.tex`
- 更新后的 `drafts/figure_registry.json`
- `drafts/visual_generation_log.md`

图表生成边界：

- 从 results/ablations 生成表格和 plot 是机械工作，适合 tool。
- 图表想说明什么、caption 如何解释、哪些 result 值得突出，是 LLM 工作。
- schematic 可以由 LLM 设计，但必须由 hypotheses/exp_plan/code 支撑。

最低质量门：

- 每个 figure/table 必须有 source artifact。
- 每个 label 唯一。
- 正文引用的 label 必须存在。
- caption 不能包含 artifact 中没有的数字。
- T9 编译前必须确认图文件路径存在。

## 15. T9 投稿编译衔接

T8 输出 `drafts/paper.tex` 后，T9 负责投稿模板迁移和编译。T9 不再只检查文件存在，而是：

- 进入 LLM 前检查本机 `latexmk` 或 Docker 统一镜像。
- 首先调用 `prepare_submission_bundle`，把 `drafts/paper.tex` 复制为 `submission/bundle/main.tex`，把 `literature/related_work.bib` 复制为 `submission/bundle/references.bib`，并把主稿中的 `\bibliography{...}` 统一改写为 `\bibliography{references}`。这一步是机械路径修复，不应靠 LLM 临场判断 bib 文件名。
- `prepare_submission_bundle` 还会把 `drafts/figures/` 中被主稿引用的图表复制到 `submission/bundle/figures/`，避免 bundle 内编译找不到图片。
- 优先调用 `latex_compile` 编译 `submission/bundle/main.tex`。
- 编译失败时读 log，修复再重试。
- 每次 `latex_compile` 都会在投稿主文件场景写入 `submission/compile_report.json`。
- `prepare_submission_bundle` 会写 `submission/bundle/bundle_manifest.json`，记录当前
  `drafts/paper.tex`、`literature/related_work.bib`、bundle 内 `main.tex/references.bib`
  和 copied figures 的 hash。T9 validator 和 prefinalize 必须用它确认 bundle 没有相对源文件过期。
- `main.pdf` 必须是真 PDF 文件，不能是占位文本。
- `main.pdf` 不能早于 `main.tex`。
- `main.log` 不能残留 fatal error、undefined references 或 unresolved citations。
- validator 会校验 `compile_report.json` 的 `semantics=latex_compile_attempt_report`、最后一次 attempt 成功、`tex_path/pdf_path/log_path` 指向当前 bundle，并用 `main_tex_sha256`、依赖 fingerprint、`pdf_sha256`、`log_sha256`、mtime 和 size 证明 PDF/log 来自当前 `main.tex` 和当前 bundle 依赖。
- resume 到 T9 时，如果 bundle manifest、compile report、PDF/log 和 migration report 已全部通过 validator，runtime 会在编译环境检查和 LLM 前直接 `t9_submission_prefinalize` 完成。
- `latex_compile` 会复用同一 TeX + dependency fingerprint 的成功 PDF；如果同一 fingerprint 的源级失败已经达到上限，会要求先改 TeX 或依赖。Docker/latexmk 不可用这类环境失败不计入 source-level attempt 上限。

当前机器的 Docker Root Dir 已配置为 `/mnt/data/Docker`。宿主机没有 `latexmk` 时，T9 会走 Docker 镜像 `researchos/system:latest`；Docker 命令、daemon 或镜像不可用时，T9 以 `WAITING_ENVIRONMENT` 暂停，修好环境后可直接 `resume`。

这意味着 T8 写作阶段应尽量保证：

- citation key 都来自 `related_work.bib`
- figure/table 路径真实存在
- LaTeX macro 不依赖缺失 style
- section 文件不含多余 wrapper

## 16. 运行与验证命令

常用单阶段调试：

```bash
python -m researchos.cli run-task T8-RESOURCE --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-WRITE --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-SECTION-PLAN --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-SEC-METHOD --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-DRAFT --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-REVIEW-1 --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-REVISE-1 --workspace ./workspace/local-test2
python -m researchos.cli run-task T9 --workspace ./workspace/local-test2
```

per-section Reviewer 落地后，建议增加这些调试入口：

```bash
python -m researchos.cli run-task T8-REVIEW-1-SEC-EXPERIMENTS --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-REVIEW-1-SYNTH --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-REVIEW-2-SEC-EXPERIMENTS --workspace ./workspace/local-test2
python -m researchos.cli run-task T8-REVIEW-2-SYNTH --workspace ./workspace/local-test2
```

校验：

```bash
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T8-SECTION-PLAN
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T8-SEC-METHOD
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T8-DRAFT
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T8-REVIEW-1
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T8-REVISE-1
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T9
```

`T8-SECTION-PLAN` 的 validate 有一个特殊安全行为：当 outline/resource/section/evidence/figure plan 已存在但 `paper_state.json` 是旧语义或 outline 缺失时，validate 会先调用确定性 recovery helper 重建 `paper_state.json` 和 `section_outlines/*.md`，再返回校验结果。这个命令适合在长时间 resume 前先校准写作状态。

测试：

```bash
pytest tests/unit/test_writer_reviewer_submission.py -q
pytest tests/unit/test_search_and_docker_tools.py -q
pytest tests/unit/test_runtime_config_and_validator_extensions.py -q
pytest tests/unit/test_runner_basic.py tests/unit/test_runtime_extended_tools.py tests/unit/test_writer_reviewer_submission.py tests/unit/test_search_and_docker_tools.py tests/unit/test_ideation_agent.py tests/unit/test_reader_agent.py tests/unit/test_mechanism_tools.py tests/unit/test_paper_enrichment.py tests/unit/test_paper_save_tools.py -q
```

per-section Reviewer 需要补充的测试：

```bash
pytest tests/unit/test_writer_reviewer_submission.py -q
pytest tests/unit/test_state_machine_runtime_features.py -q
pytest tests/unit/test_runtime_config_and_validator_extensions.py -q
```

重点覆盖：

- `ReviewerAgent(section_review)` 初始消息只包含一个 section 目标。
- 单章 review 缺结构标题会失败，且不会要求 `round_N.md` 存在。
- `ReviewerAgent(review_synthesis)` 在缺任一 section review 时失败。
- Round 2 section review prompt 包含上一轮同 section review 和 `revision_response_round_1.md`。
- 状态机从 `T8-SELF-CHECK` 串到 8 个 section review，再到 synth，再进 revise。
- `validate --task T8-REVIEW-1-SEC-EXPERIMENTS` 只校验单章产物。

## 17. 已知限制与下一步

已知限制：

- Reviewer 当前是单节点 section-aware，还不是每个 section 一个独立 Reviewer 节点。13.1.1 已给出可执行状态机设计；落地时需要同步改 `config/state_machine.yaml`、`ReviewerAgent` mode 分支、`task_io_contract.py` 和 task checker。
- Revision patch list 已结构化，但 patch 应用过程仍主要靠 prompt 和 validator 间接约束。
- `audit_manuscript_claims` 目前偏机械 hint；`claim_ledger.json` 已生成 seed，但 claim support 的最终语义判断仍靠 Writer/Reviewer。
- 图表 registry 已生成 seed，但自动 plot/table tool 尚未完全接入。
- 顶会/顶刊风格判断仍依赖 LLM 和 Skill，不应硬编码到工具。

下一步优先级：

1. 增加 results/ablation plot tools，并回写 `figure_registry.json` 的 file path/caption/status。
2. 按 13.1.1 把 Reviewer 拆成可选的 per-section review execution mode。
3. 增加 T8 小 workspace E2E，覆盖 RESOURCE -> one SEC -> DRAFT -> REVIEW -> REVISE。
4. 增加 T9 TeX fixture，测试真实 `latex_compile` 成功和失败修复路径。
5. 将 `claim_ledger.json` 的 supported/unsupported 状态与 review/revise patch 进一步联动。
