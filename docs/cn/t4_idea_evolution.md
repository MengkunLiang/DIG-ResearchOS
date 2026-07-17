# T4 多智能体 Idea Evolution：原生架构、Prompt、校验与恢复

> [中文](../cn/t4_idea_evolution.md) | [English](../en/t4_idea_evolution.md)

> 本文描述当前仓库中原生 T4 的实际实现。它以 `researchos/ideation/`、`researchos/runtime/orchestrator.py`、`researchos/orchestration/state_machine.py`、`researchos/cli_runners/`、`researchos/ui/idea_evolution_renderer.py`、`researchos/prompts/idea_*.j2` 和 `config/system_config/t4_evolution.yaml` 为准。旧工作区中可能仍有 Legacy 投影文件；这些文件是兼容出口，不是原生 T4 的控制面。

T4 不是“让一个模型填完一张最终论文方案表”，也不是“任何字段少一点就终止”的表单流水线。它是一个可恢复的 Population-based Idea Evolution 系统：模型负责提出、解释、比较、反驳和重构研究想法；确定性代码负责身份、谱系、状态、artifact、来源权限和持久化；研究者负责方向选择、探索成本和最终权威。

```text
严格保护：事实边界、来源权限、谱系、状态与 artifact 一致性
宽容处理：格式差异、字段富化不足、Route underfill、局部模型失败、数量波动
优先顺序：无损规范化 → 有界修复 → 对象级降级 → 人工决策
```

本文特别强调一个容易误解的边界：**证据约束认证，不封闭想象。** LLM 可以基于项目上下文、通用学术知识、反事实推演和跨域结构类比提出大胆的 Idea；但是此类内容必须保留为猜想、待验证假设或待升级阅读线索，不能被写成已被现有文献证实的机制、实验结果、数据集可用性或外部新颖性结论。

---

## 1. 阅读地图与核心不变量

### 1.1 面向研究者的最短心智模型

```text
T3 / T3.5 / T3.6 的材料、用户种子和 Cross-domain catalog
                         │
                         ▼
                 T4 Pre-run Gate
                         │
                         ▼
 Evidence Index + Opportunity Map + 多 Route Idea Seed
                         │
                         ▼
   Candidate Enricher → 独立三维评分 → Family / Interaction Graph
                         │
                         ▼
 Mutation Plan + Crossover Compatibility Review + Child / Deferral
                         │
                         ▼
 Union 评分 + Contract / Delta / Complexity 诊断 + Family-aware Survival
                         │
                         ▼
 Portfolio + LLM Final Card Compiler + Gate1（D1、D2、D3…）
                         │
      ┌──────────────────┼───────────────────┐
      ▼                  ▼                   ▼
  选择进入 T4.5      再演化/聚焦         查看/比较/回滚/并行保留
```

P0、每一代 Population、Candidate 版本、评分、Plan、Child、Deferral、Interaction Graph、最终卡片和 Gate 指令都会落盘。`resume` 的目标是复用仍与输入和运行配置匹配的 checkpoint，而不是重复消耗模型调用或丢弃已有研究工作。

### 1.2 LLM、确定性层与人的职责

| 事情 | 权威主体 | 为什么 |
| --- | --- | --- |
| 问题重构、概念跳跃、机制链、竞争解释、反直觉预测、研究纲领 | LLM | 这是科研表达与创造力所在，不能被词频、固定标签或模板替代 |
| Opportunity、Idea Seed、Candidate Enrichment、评分理由、Interaction 解释、Crossover 评审、Child、最终卡片 | LLM | 这些都是项目语料特定的科学判断与解释 |
| Candidate ID、版本、Population ID、Parent ID、Plan ID、路径、fingerprint、恢复顺序 | Controller / State Machine | 它们是可复现运行状态，不是科研判断 |
| JSON/YAML/fence 解析、已知别名、枚举同义词、单字符串转单元素列表 | Recovery layer | 这些是表层表达问题，不应消耗研究者或扼杀有价值的模型输出 |
| Evidence Permission、阅读等级、SourceRef、artifact 写入边界 | 原始 artifact + 确定性层 | 模型不能自行把摘要线索升级为已证实机制 |
| Route 预算、并发、批大小、Population 目标、Family soft cap | 配置与 Controller | 这些约束探索成本和排程，不定义一个 Idea 是否“合法” |
| 投稿取向、继续几轮、是否扩大资源窗口、是否进入 T4.5 | 研究者 | 这是研究策略与成本取舍，不能被系统静默决定 |

### 1.3 不能放松的 Hard Invariants

以下项目违反后，继续运行会造成科研欺骗、状态损坏或不可恢复的错误绑定。它们可以阻止**受影响对象或操作**，并应留下明确诊断；不是所有异常都属于这一类。

1. 不得伪造论文、citation key、SourceRef、文件路径、数据集、指标、实验结果、成本或外部新颖性结论。
2. 不得将 `abstract_only`、`metadata_only`、`synthesis_inference` 或 LLM conjecture 升格为已支持的机制、强证据或最终 Claim。
3. Candidate、Genome、Lineage 的 ID 必须一致；Child 不得覆盖 Parent，不能伪造 Parent/Plan。
4. 已批准的 Crossover Child 必须遵从其 Parent 集与 Gene Donor Map；Mutation Child 必须遵从计划的 preserve/modify 约束。
5. Population 的 active 与 archived 集不能重叠；输入、运行配置和选择的 fingerprint 不能混用。
6. 写入必须位于当前 workspace；不能通过路径穿越、覆盖原生 artifact 或以 Legacy 文件替换新流程结果。
7. 真正进入 T4.5 的选择必须指向当前、可追溯且 selection-ready 的 Candidate。

### 1.4 不属于 Hard Invariant 的事情

以下情况默认是修复、告警或降级，而不是整轮失败：Markdown fence、YAML 替代 JSON、camelCase、数字字符串、单对象/单元素数组差异、`parallel` 这类可判定同义表达、可富化字段缺失、一个 Route 只产出一个 Candidate、一个 Score batch 失败、一个 Child 失败、Cross-domain review 缺失、Population 少于目标、Portfolio 少于三个方向、卡片富化暂未完成。

这里的“降级”首先保护的是 Population 连续性，而不是允许半成品的研究者决策面。若 Portfolio Final Card 缺少候选级 LLM 解释，Candidate、评分和谱系仍然保留，但 Gate1 不会以旧 `gate1_card`、分数 rationale、标题截断或固定套话替代它。运行时会进入定向 Final Card LLM 修复；修复预算耗尽后再打开 Human Recovery Gate。

这一区分是 T4 鲁棒性的关键：系统不会为了“看上去整齐”而制造 filler，也不会为了一个局部输出不完整而抹掉已有 Population。

---

## 2. 入口、模式、Pre-run Gate 与状态机

### 2.1 常用命令与写入规则

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `python -m researchos.cli run --workspace <ws> --from-task T4` | 从完整主管道进入 T4 | 使用完整 Pipeline Runner 与 State Machine |
| `python -m researchos.cli resume --workspace <ws>` | 恢复 `PAUSED` / `WAITING_HUMAN` 工作区 | 优先重新展示持久化 Gate 或从 checkpoint 继续 |
| `python -m researchos.cli run-task T4 --workspace <ws>` | 隔离调试 T4 | SingleTask Runner 也会处理 T4 Pre-run 与可恢复 Gate |

同一 workspace 同一时间只能有一个写入者。不要在同一目录并行运行 `run`、`resume`、`run-task`，也不要让外部 Skill 与 T4 同时改写 `ideation/` 或 `literature/` 下的同一 artifact。

### 2.2 T4 Pre-run Gate 做什么

首次运行或输入 fingerprint 变化后，T4 先执行只读的 `inspect_t4_inputs()`，然后由 `t4_prerun_gate` 让研究者确认模式、Crossover、Portfolio 上限与 Publication Orientation。它不会在确认前调用 Generator。

当前真正的阻塞输入只有：

- `project.yaml`；
- `literature/synthesis.md`；
- `literature/synthesis_workbench.json`；
- `literature/domain_map.json`。

`comparison_table.csv`、阅读笔记、T3.6 `survey_insights.json`、用户种子和 Cross-domain catalog 都是增强材料。它们缺失会导致 warning、较弱的定位或更多 conjectural Candidate，**不会**因为“证据包不完整”而关闭 T4 的创造空间。

### 2.3 模式的真实语义

| 模式 | 新建默认轮数 | 当前流程 | 适用场景 |
| --- | ---: | --- | --- |
| `quick` | 0 | P0 形成、独立评分、Family/Interaction Graph、Portfolio、Gate1 | 快速查看候选空间，不生成 Child |
| `standard` | 2 | P0 → P1 → P2 | 默认完整探索：先形成，再连续两轮机制、反事实和验证深化 |
| `deep` | 3 | P0 → P1 → P2 → P3 | 研究者明确愿意付出更大探索成本时使用 |
| `auto` | 默认 2，可显式设 0–3 | 使用已确认的轮数预算 | 当前是“可配置预算模式”，不是系统静默替人决定何时停止 |

旧 workspace 可能保留 `standard=1` 或 `deep=2` 的已确认配置；它们可以继续 resume。模式的轮数是探索成本上限，不是“必须生成这么多 Child”的配额。

### 2.4 Publication Orientation 的边界

目标取向可为 `utd_is`、`management_is`、`ccf_cs`、`technical_cs`、`hybrid` 或 `custom`。它影响：

- 三维 `overall_readiness` 的公开权重；
- Prompt 与最终卡片的叙事重点；
- 单独展示的 qualitative `Profile Fit`；
- 人类比较时的“更适合哪种论文叙事”解释。

它不改变 SourceRef、Evidence Permission、核心 Genome、外部事实，也不把 `Profile Fit` 偷偷并入生存选择的科学分数。改变取向可重新编译取向相关的解释或评分视图；它不是篡改已形成 Idea 的事实基础。

### 2.5 状态与权威 artifact

| 作用 | 路径 | 权威性 |
| --- | --- | --- |
| 主管道状态、pending Gate、错误、历史 | `state.yaml` | Pipeline / State Machine 权威 |
| T4 内部状态 | `ideation/evolution/state.json` | 当前 Population、阶段、fingerprint、显示 Candidate |
| 已确认运行配置 | `ideation/evolution/t4_run_config.json` | 本次模式、轮数、预算、取向、Route quota |
| P0/P1/... 快照 | `ideation/populations/P<n>.json` | active / archived 指针与代际版本 |
| Candidate dossier | `ideation/candidates/<id>.v<version>.json` | 研究实体、Genome、Lineage、Hypotheses、Creative Context |
| 评分 checkpoint | `ideation/evolution/scoring/` 与 `ideation/evolution/scores/` | 三维评分、repair / isolation / unscored receipt |
| Route checkpoint | `ideation/evolution/routes/round_0/<route>.json` | 每条 Route 的 supported / partial / unsupported 状态 |
| Interaction Graph | `ideation/evolution/interactions/P<n>.json` | 可解释的候选关系短名单和可选 LLM review |
| 演化计划 | `ideation/evolution/plans/round_<n>.json` | Parent、Mutation / Crossover Plan、Compatibility 决定 |
| Child / deferral | `ideation/evolution/offspring/` | 每项 Plan 的 Child 或“不强造 Child”的明确理由 |
| 最终卡片 | `ideation/final_cards/portfolio_cards.json` | Portfolio 的 LLM 卡片翻译；失败可单独降级 |
| Gate1 兼容投影 | `ideation/_candidate_directions.json` 等 | 人机交互读模型；不是原生 Candidate 的唯一来源 |

所有可复用 checkpoint 都受 `input_fingerprint` 和 `run_config_fingerprint` 约束。输入或已确认配置改变时，系统不应把旧 Population 伪装成本轮结果；已完成且仍有效的局部 artifact 也不应被删除。

---

## 3. 输入证据、Cross-domain 与创造性边界

### 3.1 Evidence Index 是校准底座，不是 Idea 许可名单

`researchos/ideation/evidence.py` 从可读取材料构建 `EvidenceAtom`。每个 atom 包含稳定 ID、来源路径、section locator、阅读等级、domain role、bridge 归属、允许用途和禁止用途。Controller 的工作是保留这些边界、去重、压缩 prompt 上下文和写索引；它不通过固定规则判断哪一个科学机会“最有价值”。

典型阅读等级与作用如下：

| 阅读等级 | 可作为 | 不能作为 |
| --- | --- | --- |
| `full_text` | 已读 section 范围内的 recall、问题锚点、机制支持、条件性/最终 Claim | 仍不可超出实际 section 与 SourceRef 范围 |
| `partial_text` | 有边界的机制支持、条件性 Claim、灵感 | 无条件最终 Claim |
| `abstract_only` | 主题、趋势、候选发现、类比灵感、阅读升级线索 | 已被证实的机制、强支持、最终 Claim |
| `metadata_only` | 资源线索、检索/阅读优先级 | 问题锚点、机制或结果支持 |
| `synthesis_inference` | 综合推断、问题重构灵感 | 独立机制证据或强 Claim |
| `brainstorm` | LLM/用户的创意燃料 | 已有事实、论文、数据、实验结果 |

因此，“当前材料没有精读某一篇”不等于“模型不能提出该方向”；它只意味着方向应保留为 `conjectural` / `verification_required`，并附带可被拒绝的测试或阅读升级要求。

### 3.2 Cross-domain 不是 `bridge_notes` 的同义词

当前目录结构明确区分了**检索/类比目录**与**真实阅读笔记**：

```text
literature/
  bridge_domain_plan.json                   # B1/B2/... 的用户确认意图、理由、查询
  cross_domain_catalogs/                    # Cross-domain catalog 的 canonical 根
    index.json
    B1/
      bridge_context.json
      _bridge_context.md
      paper_catalog.json
  bridge_notes/                              # 实际读过的 Bridge 论文 Markdown note
    B1/<paper-id>.md
  deep_read_notes/                           # 主线深读 note
  shallow_read_notes/                        # abstract / shallow note
```

`cross_domain_catalogs/<bridge>/` 中的 `bridge_context.json` 保存领域名称、研究者为何把它作为桥、优先级、查询和 usage boundary；`paper_catalog.json` 保存检索 metadata、abstract、读取状态、`canonical_note_path` 与 bridge 关联。它们是 B1/B2/B3… 的独立信息层，不应该因为 `bridge_notes/` 暂时为空而被视为不存在或失效。

`bridge_notes/` 只容纳真实论文阅读 note，并按其真实阅读等级提供 permission。一个 catalog record 没有 canonical note 时，仍可成为 `ABSTRACT_ONLY` 或 `METADATA_ONLY` Cross-domain atom，用于类比、问题重构、历史定位、baseline/dataset lead、验证问题和下一篇优先阅读；它不能单独证明“机制已经成立”。

### 3.3 旧工作区迁移与不重复注入

历史工作区曾把 catalog JSON 和 bridge note 放在同一 `bridge_notes/` 目录。workspace 初始化以及 T2/T3 刷新会调用 `researchos/runtime/bridge_catalog.py` 的非破坏性迁移：

1. 将旧的 `bridge_context.json`、`paper_catalog.json`、`_bridge_context.md` 复制到 canonical `cross_domain_catalogs/`；
2. 不移动、不删除真实 Markdown note；任何 linked canonical note 才决定该论文可以支撑何种 Claim；
3. canonical 文件已存在且不同内容时记录冲突，不静默覆盖；
4. 旧路径只作为 canonical 路径缺失时的读取回退；
5. 每个 bridge ID 只选一份 catalog，避免同一 abstract 被重复注入 T4 prompt。

因此，遇到“`bridge_notes` 是空的”时，应首先检查：

```text
literature/bridge_domain_plan.json
literature/cross_domain_catalogs/index.json
literature/cross_domain_catalogs/<B#>/bridge_context.json
literature/cross_domain_catalogs/<B#>/paper_catalog.json
```

只有在需要确认某篇论文已被深读时，才检查 `literature/bridge_notes/<B#>/`。空的深读目录不等于 B1/B2 的 Cross-domain 信息丢失。

### 3.4 T4 如何使用 Cross-domain 信息

T4 向 Opportunity Planner 与 Generator 提供两类材料：

1. Evidence Index 中来自真实 note 的 bridge atom；
2. `load_bridge_catalog_summaries()` 生成的压缩 catalog track，包括名称、rationale、查询、少量 abstract/metadata 摘要、阅读状态和明确 usage boundary。

为避免大量主线 note 挤掉跨域信息，`workspace_research_context` 会为不同 bridge 保留有界的代表条目。`cross_domain_bridge` Route 还会收到研究者确认的 bridge 名称、动机与查询，即使还没有专门 deep-read note，也可将它们作为**猜想性结构迁移脚手架**。

一个合格的 Cross-domain Candidate 需要说明：

- 源领域与目标问题之间的结构映射；
- 拟迁移的是机制、方法、评价视角、baseline/dataset lead 还是相邻应用；
- 迁移为什么可能失败；
- 哪些是 conjectural；
- 下一步应读什么或做什么区分性验证。

它不能仅因共享关键词而宣称机制等价，也不能将 catalog 标题当作已验证证据。若本轮无法形成可辩护的结构映射，Route 输出 `unsupported` 或 `deferred` 是正常、可见的结果，不影响其它 Route 或整个 Gate1。

### 3.5 Cross-domain 在后续工作流中的持续价值

T4 不是 catalog 的唯一消费者。catalog/context 与真实 bridge note 可继续服务于 T3/T3.5/T3.6 的 taxonomy、历史/前沿补充检索、T4.5 的差异化检索、T8 Related Work 的相邻机制定位，以及相关 Skill 的工作上下文。跨模块共享的原则一致：**catalog 是补充性跨域语境，canonical note 才能提供相应阅读等级的证据权限。**

---

## 4. 原生 T4 的逐阶段执行过程

### 4.1 总览

```text
Pre-run inspection / 研究者确认
  → Evidence Index + workspace research context
  → Opportunity Map
  → P0：独立多 Route Seed formation
  → Candidate Enrichment（逐 Candidate，可降级）
  → Independent Scoring（逐 batch，必要时逐 Candidate 隔离）
  → Idea Family + Population Interaction Graph
  → Parent selection + Mutation plan
  → Crossover compatibility review（仅在允许时）
  → Child / explicit deferral
  → Union rescoring 或 Parent-score reuse
  → Contract / Gene Delta / Complexity diagnosis
  → Pareto + diversity + soft Family cap survival
  → Portfolio + LLM Final Card compilation + Gate1 projection
```

### 4.2 Evidence Routing 与研究上下文压缩

Controller 写入：

```text
ideation/evidence/evidence_index.jsonl
ideation/evidence/evidence_index_summary.json
```

随后构建 `workspace_research_context`。它是一个**截断与检索**过程，而非确定性“研究缺口发现器”：包括 `project.yaml`、`synthesis.md`、用户种子、若干 evidence atom 与有界 Cross-domain track。prompt 体积被控制，但完整索引仍可用于定向回查；被压缩掉的 note 不会因此失去可访问性。

### 4.3 Opportunity Map

`idea_opportunity_planner.j2` 生成的是不同科研意图的 `OpportunityQuery`，不是 Candidate，不评分也不做 portfolio 选择。它可从机制张力、隐含假设、失效边界、评价盲区、用户挑战或结构性跨域映射提出问题。

如果 Planner 超时、provider 不可用或返回不可恢复结构，Controller 会写：

```text
ideation/evolution/diagnostics/opportunity_planner_recovery.json
```

并以明确标注的 provisional fallback opportunity 继续 Route Formation。该 fallback 不是对研究领域的断言，只是“在 Planner 暂不可用时，所有后续问题必须保持可证伪和待验证”的运行收据。Planner 的局部失败不应暂停整个 T4。

### 4.4 P0 多 Route 形成

默认 Route 与当前最大探索预算如下。`minimum` / `maximum` 是探索成本和视角覆盖建议，不是“必须凑满”的合格条件。

| Route | 默认范围 | 科学作用 |
| --- | ---: | --- |
| `evidence_routed_literature` | 3 | 从主线材料的机制、张力、反例和空白出发 |
| `informed_brainstorm` | 2–3 | 使用项目上下文和 LLM 学术知识提出显式 conjectural 的跳跃性方向 |
| `mechanism_challenge` | 0–1 | 挑战默认机制或寻找更能解释现象的替代链 |
| `reverse_operation` | 0–1 | 反转目标、因果方向或操作逻辑 |
| `subgroup_failure` | 0–1 | 从异质性、失败群体或边界条件重构问题 |
| `gap_exploration` | 0–1 | 探索尚未被充分解释、测量或比较的结构缺口 |
| `cross_domain_bridge` | 0–2 | 用 Cross-domain 结构映射提出待验证迁移问题 |

每条 Route 有独立 checkpoint：

```text
ideation/evolution/routes/round_0/<route>.json
```

Route 的合法状态是：

- `supported`：保留了本轮形成的非重复 Candidate；
- `partial`：有可用方向，但少于目标；
- `unsupported`：无法负责任地形成不同且可表达的方向，并留下具体理由。

系统会为一个薄弱 Route 执行一次有界 semantic repair / creative re-divergence。仍不足时写 receipt，而不是循环重试到产生语义重复的 filler。其它 Route 并行继续，Gate1 可以让研究者明确重跑某一条 Route。

### 4.5 IdeaSeed：初始契约故意很小

初始 Generator 不应被要求一次输出完整论文。最小 `IdeaSeed` 只需要：

- 项目内的问题；
- 一句话 thesis；
- 候选机制；
- 一个 contribution sketch；
- 一条可证伪预测；
- 一个主要不确定性；
- Route origin 与已有证据引用（若有）。

若模型已输出完整 `CandidateDossier`，系统会接收；若只输出最小 Seed，则 Controller 把它投影为可追溯、显式 `seed` 成熟度的 dossier。投影不发明引用、实验、评分或科研解释。一个结构可用但展示字段未成熟的初始 Candidate 会被标为 Seed，而不是让整个 Route 失败。

### 4.6 Candidate Enricher：将表达深度从 Generator 契约中拆开

每个 Seed 可单独调用 `LLMCandidateEnricher`。它的任务是把一个已经被保留的概念扩展为更完整、可比较的研究方案，包括：

- 更清楚的机制链和竞争解释；
- 2–4 条非重复的 provisional hypotheses；
- 2–4 项 contribution；
- 验证逻辑、边界、风险与 kill criterion；
- 研究者可读的 CandidatePresentation。

它**不得**改变 Candidate ID、Route、Parent lineage、问题重构、Core Thesis、已有 conceptual leap、SourceRef 或 Evidence Permission；也不评分、选择、淘汰或合并 Candidate。

落盘位置为：

```text
ideation/evolution/enrichment/<candidate-id>.json
ideation/evolution/diagnostics/enrichment_<candidate-id>_attempt_<n>.json
```

每个 Candidate 有一次普通尝试和一次结构修复尝试。两次仍不可用时，原始 Seed 留在 Population，追加 `enrichment_degraded` 警告；之后可通过 Focused Evolution、阅读升级或研究者选择继续深化。Enricher 失败绝不能删除 Seed 或停止 P0。

### 4.7 Candidate Genome、Creative Context 与 Family

完整 `CandidateDossier` 的科学核心在 `IdeaGenome` 中：

```text
problem, opportunity, challenged_assumption, core_thesis, mechanism,
design_or_artifact, contribution_package, hypothesis_bundle,
validation_logic, boundary_conditions, risks
```

`CreativeContext` 与 Genome 并存，保存不会因为表单压缩而丢掉的 LLM 科研表达：`conceptual_leap`、`competing_explanations`、`surprising_prediction`、`research_program_potential`、知识来源、证据状态与 reading/validation upgrade。它们描述的是 proposal，不是认证结论。

Family 是候选组织和比较工具。代码可从 Genome 的透明相似度召回相近候选，便于 diversity 与交互分析；但词面相似度不是“这两个 Idea 等价”、更不是删除其中一个的科学结论。机制 DNA、依赖关系、是否应并行保留及是否应组合，必须由 LLM reviewer 和研究者解释。

### 4.8 Independent Scoring：三个正式数值维度

新的正式数值 `ScoreDimensions` **只有三项**：

| 维度 | 要回答的问题 | 不应被误用为 |
| --- | --- | --- |
| `research_value` | 若猜想经检验成立，这个问题会带来多大的科学或实际认识增量？ | 现有阅读量、数据是否已齐、投稿成熟度 |
| `mechanism_integrity` | 机制、假设、竞争解释和因果逻辑是否连贯且可反驳？ | 因尚未做完文献阅读而自动扣分 |
| `contribution_distinctiveness` | 它相对当前 T4 Population 在问题、机制、贡献或联合 thesis 上是否实质不同？ | 文献世界范围的新颖性证明 |

`overall_readiness` 是运行时从这三项导出的摘要，不是第四个模型评分，也不是硬 Gate。它可根据已确认 publication orientation 使用不同的**三维**权重；不包含证据、验证、风险、不确定性或 Profile Fit。

以下全是非阻断 diagnostics，不是额外的数值门槛：

- `diagnostics.evidence_calibration`；
- `diagnostics.validation_feasibility`；
- `scientific_upside`、`evolution_potential`、`score_uncertainty`；
- `wildcard_recommended` 及其理由；
- `dominant_strength`、`dominant_bottleneck`；
- qualitative `profile_fit`；
- 旧五维 / compatibility grid 的历史字段。

Scorer 匿名接收 Candidate，不能生成、改写、合并、选择或删除 Candidate。批量评分失败后 Controller 会：先做一次有界 repair；若失败则拆到更小的 batch / 单 Candidate；仍失败时写 unscored receipt。一个未评分 Candidate 保持可见、标为 unranked，不会因为虚构 fallback score 获得排名，也不会让已评分 Candidate 被清空。

### 4.9 Interaction Reviewer 与 Population Interaction Graph

每个 P0 / P<n> 都可写：

```text
ideation/evolution/interactions/P<n>.json
```

Interaction Graph 分两层：

1. **确定性 shortlist**：从已写入的 Genome 提取 problem、mechanism、contribution、hypothesis、validation 的透明 token overlap / distance，召回可能的 competitor、complement 和 distant-transfer pair；
2. **LLM Interaction Reviewer**：只解释 shortlist 中的共同核心、关键差异、互相挑战、可转移元素、需要区分之处、Crossover 潜力与风险，并给出 `competitor`、`complement`、`distant_transfer` 或 `parallel` 关系。

图不是第二个 Scorer，也不是隐藏的 Survival 机制。确定性相似度只决定“值得让模型看哪一对”，不认证科学关系；LLM review 也不打分、选优、删除、合并或改写 Candidate。reviewer 不可用或输出异常时，图写为 `deterministic_degraded` 并记录诊断，后续 Mutation、Crossover、Population 和 Gate1 仍继续。

已解释的 peer context 会作为有界、明确标注的 advisory 信息附到 Mutation Plan。Evolver 可以忽略不合适的 transfer，并返回 deferral；它不应被图强迫合并或采用某个机制。

### 4.10 Parent、Mutation Plan 与 Crossover Compatibility

Parent 选择和 Mutation Plan 的确定性部分只把评分、Family 和 lineage 编译为可审计操作边界：保留哪些 Gene、修改哪些 Gene、预期改善和失败条件。真正的科学改写由 Evolver 完成。好的 Mutation 不只是换术语，而应至少完成以下之一：澄清可证伪的机制、区分竞争解释、形成有信息量的反事实、将高风险 thesis 收窄为可检验命题，或把 Cross-domain analogy 转成可反驳机制。

Crossover 仅在允许时进行，且顺序是：

```text
Interaction Graph 的建议 pair（或有界 fallback pair）
  → LLM Crossover Compatibility Reviewer
  → 仅 approved pair 编译 Gene Donor Map / Crossover Plan
  → Evolver 生成 Child
```

Compatibility Reviewer 检查的是 problem compatibility、bottleneck complementarity、单一机制链、一致的假设、证据安全、assumption conflict、complexity risk 和完整 donor map，不是关键词是否相似。

持久化 enum 包括 `approved`、`rejected`、`uncertain` 和 `parallel`。其中只有 `approved` 能够授权编译 Gene Donor Map / Crossover Plan；`parallel` 是一等的、语义明确的“并行保留而不生成 Child”结论，不是错误或被降格的 rejection。模型常见但语义正确的“不要合并”输出会被无损处理：

| 模型表达 | 规范化结果 | 科研含义 |
| --- | --- | --- |
| `parallel`、`keep parallel`、`并行`、`并行保留`、`保持并行` | `parallel` | 不生成 Crossover Child；两个 Parent 均保留为可比较的独立方向 |
| `incompatible` | `rejected` | 当前不能自动合并；不是 Candidate 被删除 |
| `needs clarification`、`defer`、`待澄清` | `uncertain` | 需要更多解释或证据；不强迫 Child |

`conflicts` 既接受列表，也接受一个单独字符串并规范化为单元素列表。此前 `decision="parallel"` 触发 Pydantic 错误属于 Prompt/Schema 失配；当前 `parallel` 会被持久化为正常 no-child verdict。恢复时，已保存的 `parallel_crossover` / `no_approved_crossover` plan batch 会被重用，不会因为其中没有 Child 而再次调用 Compatibility Reviewer 或 Evolver。

### 4.11 Offspring、显式 Deferral 与 Parent Preservation

Evolver 对每个 Plan 生成一个具有新 ID、正确 lineage、Gene Delta、复杂度变化和完整成熟 Candidate 的 Child；它不能自己换 Parent、换 Plan、打分或做 Survival。对于单 Plan，以下是正常的科研结果：

- `no_improvement`：无法在不制造 cosmetic 改写的情况下得到实质改善；
- `incompatible`：Crossover 无法形成单一 coherent thesis；
- `deferred`：需要阅读升级、外部条件或人的选择。

这些会形成 `EvolutionPlanDeferral`，写出具体理由与 revisit condition。它们不是 provider error，且 Parent 继续存在。一个 Child 的模型/解析/契约失败最多消耗其自身的修复窗口，记录 Plan-local diagnostic，不影响其它 Child 或完整 Parent Population。

### 4.12 Union 评分、Contract、Gene Delta、Complexity 与 Survival

有 admitted Child 时，系统对 Parent + Child union 重新独立评分；没有 admitted Child 时，系统复用已完成的 Parent score 并写明 reuse receipt。它不会为了没有 Child 再调用一次模型制造没有信息增量的评分。

随后执行：

- `validate_idea_contract`：检查可追溯的结构完整性，而不是用“科研质量模板”判死刑；
- `compute_gene_delta`：识别 substantive、clarification-only、cosmetic 或 regressive 改变；
- `detect_complexity_inflation`：把复杂度增长作为诊断和人工审阅提示，而不是把所有大胆 Idea 压回保守方案；
- `select_survivors`：三维 Pareto layer 优先，随后使用 Parent–Child 改善、结构多样性、Family soft cap 和明确 wildcard preservation；
- `select_portfolio`：优先展示不同 Family 的 lead / alternative / high-upside，候选不足时可以只展示 1–2 个真实方向。

Evidence calibration、validation feasibility、Profile Fit、不确定性和 upside 不会伪装为第四/第五个数值 survival 维度。未评分但结构可用的 Candidate 可被保留为 `unscored_retained_for_review`；它不会超过已有独立评分的 Candidate。

### 4.13 Final Card 编译与 Gate1 投影

原生 Candidate 与 Score 是权威记录。`LLMFinalIdeaCardCompiler` 针对 Portfolio Candidate 生成面向研究者的中文 `FinalIdeaCardTranslation`，然后兼容投影把 Population 写到：

```text
ideation/_candidate_directions.json
ideation/_pass1_forward_candidates.json
ideation/_pass2_grounding_review.json
ideation/_family_distribution.md
ideation/_gate1_candidate_cards.md
ideation/_gate1_selection_brief.md
ideation/bridge_coverage_review.json
```

Card compiler 或 renderer 的局部失败不应把完整 Population 改写为失败。运行时保存 Candidate、评分、谱系、Portfolio、原始响应摘要和诊断，并先执行一次语义修复、再执行一次定向的新鲜编译。若仍未取得每张完整的 LLM Card，Gate1 转入 `t4_recovery_gate`，由研究者选择继续定向修复、查看保存的对象或暂停；Renderer 不能编造机制、证据解释、推荐语或假设来填空。

---

## 5. LLM Prompt 系统：每个模板的职责、输入和边界

所有 `idea_*.j2` 是结构化角色 prompt。Role caller 负责带上 `payload_json`，使用 tolerant parser，再通过 Pydantic schema、角色级语义检查和 Controller contract 落盘。prompt 不应承担状态迁移、路径管理或最终人工决策。

### 5.1 Prompt 总表

| 模板 | 角色与主要输出 | 可以做什么 | 不能做什么 | 失败后的默认处置 |
| --- | --- | --- | --- | --- |
| `idea_opportunity_planner.j2` | Opportunity Planner，`opportunities` | 从张力、假设、反例、用户种子、Cross-domain 映射提出不同研究问题 | 评分、选择 Candidate、把缺检索区写成事实 gap | 写 planner diagnostic，使用 provisional fallback，Route 继续 |
| `idea_opportunity_semantic_repair.j2` | Planner 修复 | 规范化已有机会的字段与证据边界 | 产生新 score / source / novelty claim | 有界修复；失败仍可 fallback |
| `idea_generator.j2` | Route Generator，`seeds` / candidate dossier / `unsupported` | 形成最小、跳跃但可证伪的 Idea Seed | 要求一开始就是终稿、伪造来源或把类比当事实 | Route-local repair / partial / unsupported receipt |
| `idea_route_semantic_repair.j2` | Generator 输出修复 | 重新组织已有内容、降级不安全 provenance、补充受上下文约束的短展示字段 | 评分、选优、删除 Candidate、虚构引用 | 失败只影响该 Route |
| `idea_candidate_enricher.j2` | Candidate Enricher，`candidate` | 扩展一个已接纳 Seed 的机制、假设、贡献、验证、风险和中文解释 | 改 ID、Route、Parents、问题、thesis、来源权限，或做选择 | Candidate-local degraded Seed |
| `idea_scorer.j2` | Independent Scorer，`scores` | 匿名给出三维科学评分和可选诊断 | 改写、排序、合并、淘汰 Candidate；输出旧 compatibility grid | batch repair → isolate → unscored receipt |
| `idea_score_semantic_repair.j2` | Score schema 修复 | 正规化可解析 score 的字段、别名和三维结构 | 伪造评分理由、改变 Candidate | 有界修复后拆 batch |
| `idea_score_rationale_repair.j2` | Score rationale 修复 | 让已有诊断更清楚、与 Candidate 对齐 | 把诊断变为硬 Gate 或产生新事实 | 诊断缺失保持可见，不阻断分数 |
| `idea_interaction_reviewer.j2` | Interaction Reviewer，`reviews` | 解释 shortlisted candidate pair 的关系 | 打分、选优、删改、合并或把词面相似当证据 | graph 降级为 deterministic shortlist |
| `idea_crossover_reviewer.j2` | Compatibility Check，`decisions` | 评估是否可形成单一 thesis 与 donor map | 生成 Child、强制合并、排名 | rejected/uncertain/parallel 保留 Parent |
| `idea_evolver.j2` | Idea Evolver，`children` / `deferred_plans` | 在明确 Plan 内做 substantive Mutation/Crossover | 改 Plan、选 Parent、评分、做 Survival | Plan-local repair；最终 deferral / Parent preservation |
| `idea_offspring_semantic_repair.j2` | Child 修复 | 修复 Child 的结构、lineage 与计划一致性 | 制造新 Parent、突破 donor map、伪造 evidence | 只影响这一 Plan |
| `idea_final_card_compiler.j2` | Final Card Compiler，`cards` | 生成 Portfolio 的中文、取向敏感、证据校准卡片 | 改 Genome、scores、sources、lineage；补不存在的机制或商业结果 | 语义修复 → 有界新鲜编译 → Human Recovery Gate；Population 保留 |
| `idea_final_card_semantic_repair.j2` | Card 修复 | 从既有 Candidate、Score、风险、来源权限和 Portfolio 关系写出候选级解释并映射到 card schema | 新增科研 Claim、假设、引用或推荐模板 | 修复失败留下结构化诊断；不由 Renderer 降级补写 |
| `idea_human_composer.j2` | Human-composed Candidate | 只在确认的 compatibility / donor map 下构造一个新 Candidate | 直接拼接文本、绕过二次确认、自己选胜者 | 失败保留 source Candidates |
| `idea_composition_reviewer.j2` | Human composition Compatibility | 评估研究者指定组件是否可组成一个 coherent Idea | 直接生成 Candidate、强制合并、宣称外部新颖性 | `keep_parallel` / `request_user_choice` / `reject_auto_merge` 是正常结果 |

### 5.2 Opportunity Planner 的创造性契约

Planner 明确允许 LLM 使用通用学术知识、反事实重构和结构性 Cross-domain analogy。对不是逐字来自工作区的机会，应标记 `knowledge_origin` 为 `llm_parametric_knowledge`、`cross_domain_analogy` 或 `mixed`，设置 `verification_required=true`，并保留 conceptual leap 与 competing explanations。它不把“缺少某类文献”描述为确定事实，也不把 Cross-domain catalog 变成文献证明。

### 5.3 Generator 的最小 Seed 契约

Generator 的 prompt 明确要求：优先提出问题、thesis、机制、一个贡献、一个可证伪预测、一个风险与 Route origin；完整 presentation、多个 hypothesis、完整 evidence map、implication 和最终 experiment plan 可以留给 Enricher、Mutation 或 Card Compiler。Route 的 quota 是成本预算；若只会重复已存在的因果逻辑，少生成或 `unsupported` 比机械凑数更正确。

对 `cross_domain_bridge`，桥域名称、理由和查询本身是合法的创意语境，不需要先假装存在深读论文。模型必须把结构迁移写成 conjectural，说明风险和阅读/验证升级，不得只返回 bridge review 而不返回 Idea Seed。

### 5.4 Candidate Enricher 的保护性约束

Enricher 允许使用 LLM 学术知识和跨域推理提高表达的深度，但不能把它们改写为已证实事实。它只能在原 Seed 的科学核心上展开；输入不支持的部分应保留 uncertainty，而不是用编造的数据集、baseline、指标、成本或结果填表。这正是“初始跳跃性”与“后续可读性”分离的原因。

### 5.5 Scorer 的三维合同

Scorer 的 JSON `scores` 对象必须恰好包含三个正式维度。每项应有针对 Candidate 的 rationale；不能以统一表扬语、文献数量、Route、Parent、年龄或 Generator 偏好替代科学判断。evidence/validation/upside/profile 是可选 qualitative observation，不能成为“证据包不够所以 Idea 不合法”的隐形 veto。

### 5.6 Interaction 与 Crossover 的分工

Interaction Reviewer 解释“两个 Candidate 的关系”；Crossover Reviewer 回答“是否可以安全、连贯地形成一个 Child”。两者都不能直接合并。`parallel` 在 Interaction 中是合法关系，在 Crossover 中被规范化为不批准合并；它是保留多样性的正当结果，不是失败。

### 5.7 Evolver 的科学而非文字编辑契约

Evolver 被鼓励做真正的概念改善，而不是模块堆叠或近义改写。它需要保留 Plan 要求的 Gene、说明复杂度和未解决风险，并提供可拒绝的验证路径。若没有实质、计划一致的 Child，返回明确 deferral 比制造一个空洞 Child 更好。

### 5.8 Final Card 与 Human Composition

Final Card Compiler 只把现有 Candidate / Score 编译为研究者能够比较的中文说明。它必须候选级、取向敏感、说明关系和依赖，不能用统一英文 fallback。Human Composition 则需要研究者明确指定组件，先由 reviewer 评估 coherence，再在确认后新建 Candidate、独立评分；系统绝不把 D1 和 D2 的文本直接拼在一起。

---

## 6. Schema、结构化契约与 Legacy 隔离

### 6.1 Candidate 相关模型

| 模型 | 作用 | 关键约束 |
| --- | --- | --- |
| `IdeaSeed` | 最小、可探索的初始想法 | 可不具备最终卡片；必须有问题、thesis、机制、预测与风险 |
| `IdeaGenome` | 稳定的科学基因 | Candidate ID、Route、Parents、11 个 Gene 的结构载体 |
| `CandidateDossier` | 原生候选实体 | dossier/genome/lineage ID 一致；evolved candidate 需 2–4 contributions 与 2–4 hypotheses |
| `CreativeContext` | 保护概念跳跃 | 记录 leap、竞争解释、惊异预测、研究纲领、知识来源与升级需求 |
| `CandidatePresentation` | LLM 作者的展示层 | title、innovation、basis、card、minimum validation 等；成熟 Candidate 可要求，Seed 可先缺失 |
| `ProvisionalHypothesis` | 可证伪假设 | statement、mechanism、observable prediction、discriminating test 都是可读文本 |
| `Contribution` | 贡献包 | statement、类型和“若为真将改变什么” |
| `IdeaFamily` | 相近候选的比较/组织 | 不是外部新颖性裁决，也不是删除机制 |

### 6.2 Score 相关模型

`ScoreReport` 的核心是 `scores: ScoreDimensions`。历史五维 payload 仍可读取：被退役的数值迁入 `diagnostics.legacy_numeric_values`，旧 `compatibility_scores` / `compatibility_rationales` 仅作为兼容查看字段。新 Scorer 不要求也不应生成它们。

`ProfileFitAssessment` 单独保存选定论文取向的 qualitative `overall_fit`、维度解释、caution 和旧数值迁移信息。它可在研究者更换取向后改变，而核心科学分数与证据权限不应因此被改写。

### 6.3 Evolution、Crossover 与 Population 模型

| 模型 | 用途 | 不变量 |
| --- | --- | --- |
| `EvolutionPlan` | 一个 Mutation 或 Crossover 的执行边界 | mutation 恰好一个 Parent；crossover 恰好两个 Parent 且有 donor map；preserve / modify 不重叠 |
| `CrossoverCompatibilityDecision` | Pair 的合并可行性 | 允许 approved/rejected/uncertain/parallel；只有 approved 可授权 Child，parallel 明确保留两个独立方向 |
| `EvolutionPlanDeferral` | 不强造 Child 的可审计决定 | 必须有具体 rationale 与 revisit condition |
| `GeneDelta` | Parent–Child 的实质变化 | 分辨 substantive、clarification-only、cosmetic、regressive |
| `ComplexityReport` | 复杂度增长诊断 | 说明新增组件/数据/阶段，不将大胆方案自动判死刑 |
| `PopulationSnapshot` | 某代 active/archived 指针 | active 与 archived 不重叠；elite 必须 active |
| `PortfolioSelection` | Gate1 的 lead/alternative/high-upside | 最多 3，不强制凑满 |

### 6.4 Final Card 模型

`FinalIdeaCardTranslation` 是一个**非变异**的展示翻译。它绑定 Candidate ID、profile type、core thesis、contribution / hypothesis ID，此外由 LLM 给出：

- 短标题、plain-language summary、why it matters、代表性情境；
- scientific/technical core、当前失败点、风险与边界；
- contribution type、innovation type、innovation delta、非例行性说明；
- 与 Portfolio 的关系、明确依赖、composition guidance；
- candidate-specific recommendation 与 bottleneck explanation；
- 适用时的 scientific / engineering / managerial / business / deployment implications，并标明 evidence status 和条件；
- 不应提出的 Claim。

Card 不得改写 Genome、Score、SourceRef 或 lineage。若 card 缺失，Candidate Population 标记为“Card repair required”，但 Gate1 不会展开半成品卡片，也不会让用户依据旧 Candidate 字段作选择。系统先以完整的 Candidate package 进行定向 LLM Card 修复，再进行一次有界新鲜编译；若仍失败，Human Recovery Gate 明确说明失败类别、已保留 artifact、可选恢复动作和是否会调用模型。Renderer 只显示操作性恢复信息，绝不填一段貌似聪明的固定科研文本。

#### 为什么完整 Card 仍然可能失败，以及为什么不能直接忽略它

“理论上模型已经有 Candidate，为什么还会没有某个 Card 字段”是一个运行时问题，不是对 Idea 的科学否定。常见原因包括：

1. provider 超时、限流或空响应，导致 Card Compiler 根本未得到回答；
2. 模型给了可读 prose，却使用 Markdown、YAML、别名、嵌套对象或遗漏 JSON envelope；
3. 模型只为 3 个 Portfolio 中的 2 个返回 Card，或重复返回一个 Candidate；
4. 输出被截断，常见结果是前半部分 Card 合法而 `recommendation`、`relationship_to_portfolio` 或风险字段缺失；
5. 模型复述 core thesis 时进行了有意义但不精确的改写，或 Portfolio / target profile 在生成期间已切换；
6. 老 workspace 有 Candidate / Score / projection，但没有当前 Population 对应的 Card；
7. 上游 Candidate 仍有可修复的 presentation 兼容字段，不能让旧字段自动假扮新的 Portfolio explanation。

因此，`FinalIdeaCardTranslation` 的完整性校验不是“要求初始 Idea 一次写成最终论文”，也不是让 Validator 对想法质量打分。它只验证：用户即将看到的每个 Portfolio Candidate，确实有一份**候选级、LLM 生成、和当前 Candidate identity 一致**的解释；系统没有把 D1 的建议混给 D2，也没有把旧版本的卡片用于新的 Population。

完整恢复链如下：

```text
已保存 Candidate / Score / Population
        │
        ├─ 初次 Card Compiler
        │      ├─ 可解析且完整：写 portfolio_cards.json → 投影 → Gate1
        │      └─ 解析、schema、coverage 或 immutable echo 问题
        │                 │
        │                 ▼
        ├─ LLM Semantic Repair
        │      只使用 canonical Candidate、Score、风险、来源权限和 Portfolio 上下文
        │      不可补造机制、引用、数据集、指标、实验结果或新颖性结论
        │                 │
        │                 ▼
        ├─ 有界 Fresh Compile Retry
        │      不重跑 Generator、Scorer、Evolver 或 Population Survival
        │                 │
        │                 ▼
        └─ Human Recovery Gate
               继续 LLM 修复 / 扩展一次资源窗口 / 查看保存对象 / 暂停或退出
```

诊断会区分 `llm_timeout`、`provider_failure`、`empty_response`、`response_parse_failure`、`schema_mismatch`、`coverage_mismatch`、`immutable_field_mismatch`、`stale_population_or_card`、`source_data_missing` 和 `unexpected_final_card_failure`。这使修复针对真实原因，而不是把所有问题粗暴归为“Validator 太严”。

### 6.5 Legacy 投影的正确位置

`researchos/ideation/legacy_projection.py` 是从原生 Population 导出 Gate1 兼容文件的 best-effort 投影层。它不是默认 Idea Generator，也不应静默调用旧 `ideation.j2`。原生 `CandidateDossier`、`ScoreReport`、`PopulationSnapshot` 与 `EvolutionPlan` 始终是事实和谱系的权威。

兼容投影允许缺少旧 score grid 或完整卡片时保留 traceable Candidate，并以 `unscored`、`seed`、`projection_status=degraded` 或 card diagnostic 诚实显示限制。它不能因为某个旧展示字段缺失而覆盖/删除原生 Population。

### 6.6 Gate1 Validator 的严格边界与可见降级

`validate_t4_gate1_ready()` 不是“最终论文方案验收器”。它对 Gate1 的硬边界是：至少一个完整、可追溯的 Candidate Pool；Candidate/Score/Lineage/artifact 的身份一致性；真实的研究命题锚点；已声明 Source Claim 的来源与 Cross-domain provenance；已提供的三维数值必须可解析且在 1–5 范围；Pass1、Pass2 和 Candidate Directions 对同一 Population 的覆盖与可见性一致。若这些条件不成立，Gate1 可能把错误对象、损坏分数或错误来源呈现给人，必须阻止并进入有诊断的恢复路径。

下列项目则是**可见的 enrichment/degraded 状态，而不是 Population pause 条件**：

- 非 Portfolio Candidate 的长解释、legacy `CandidatePresentation`、完整 Final Card translation 缺失；
- innovation explanation、basis-source interpretation、候选级 recommendation 不完整；
- 历史七维 compatibility score grid 与 rationale 缺失；
- 只有一条 hypothesis，或 hypothesis 尚待富化；
- `Profile Fit`、scientific upside、uncertainty、evidence/validation qualitative diagnostics 缺失；
- 单个 Candidate 无独立 score，但仍具有完整且可追溯的 Candidate / lineage / source 边界。

这些缺口必须显示为“待富化”“未评分”或相应 diagnostic，并可通过 Enricher、Focused Evolution、阅读升级或人类指令处理；Renderer 不能用固定学术套话掩盖它们，也不能让它们导致整个 Population 暂停。

唯一需要额外说明的是**当前 Portfolio 的完整 Final Card**。它不是 Candidate 真实性或 Population 存续的硬条件，却是打开可选择 Gate1 的人机解释条件。缺失时只阻止“展示和选择该 Portfolio 卡片”，不删除 Candidate、不重跑已成功的 Route/Scorer/Evolver；运行时改为只执行 Final Card 修复链，之后进入带明确选项的恢复 Gate。

---

## 7. 四态校验、Repair、Degradation 与阻塞边界

### 7.1 `T4ValidationResult` 四态

`researchos/ideation/response_recovery.py` 定义统一结果：

| 状态 | 意思 | 默认动作 |
| --- | --- | --- |
| `valid` | 结构与角色契约满足 | 继续 |
| `repairable` | 科研内容仍可用，只是表层结构不匹配 | 无损规范化 / schema repair / 再验 |
| `degraded` | 局部内容或角色不可用，但可诚实保留已有工作 | 写 diagnostic、标明限制、继续其它对象 |
| `blocked` | 继续会违反事实/状态/谱系/路径不变量 | 阻止受影响操作，并给出恢复选择 |

推荐的处理顺序是：

```text
deterministic normalization
  → tolerant JSON/YAML/fence parsing
  → role-specific schema repair
  → bounded LLM semantic repair
  → revalidation
  → route / candidate / plan / card 级 degradation
  → 只有安全继续不可能或需要研究者成本/方向选择时才进入 Human Gate
```

### 7.2 无损规范化能修什么

下列只改变表达形式，不产生科研内容：

- 从 Markdown fence、前后解释文字中提取唯一 JSON mapping；
- 识别结构化 YAML；
- 去除可安全处理的 trailing comma；
- 把顶层 list 包进角色已知 envelope；
- 将一个可识别的单对象包成一个元素的 `seeds` / `scores` / `children` 等数组；
- `candidateId`、`scoringBatchId`、`sourceRefs`、`parentIds`、`planId`、`schemaVersion` 等已知 alias；
- JSON 字符串 `"null"` 到真正 `null`；
- 常见 enum alias；
- `conflicts` 的单字符串到单元素列表；
- `parallel` / `并行保留` 到 Crossover 持久化 enum 的 `parallel`，不改变其 no-child 语义。

这些恢复不会凭空补 hypothesis、citation、evidence、score、provenance 或 lineage。任何未知、歧义或会改变科学语义的内容仍交给 schema/semantic repair 或明确降级。

### 7.3 Repairable Constraints 的例子

| 情况 | 正确处理 |
| --- | --- |
| JSON 外有 Markdown / 解释文本 | tolerant extraction |
| YAML 替代 JSON | 安全 YAML parse |
| 字段为常见 alias / camelCase | deterministic normalization |
| 候选输出只有最小 Seed | 投影为 Seed，交给 Enricher，而不是拒绝 Route |
| 候选有稳定 Genome 但 legacy presentation 不完整 | 保留原 Candidate / Seed 与 Genome，移除不完整兼容 payload，记录 enrichment requirement |
| Score 缺少 rationale / Profile Fit / legacy grid | 评分仍可用；把缺失显示为 diagnostic |
| 当前 Portfolio card 字段局部不完整 | LLM semantic repair → 有界 fresh compile；Population 保留，Gate1 卡片选择等待修复或 Human Recovery |
| Crossover `parallel` | no-merge，不生成 Child，Parent 保留 |
| Cross-domain review 未输出 | `unreviewed` 诊断；Route/Candidate 不受影响 |

### 7.4 Soft Quality Rules 与 Heuristics

以下是观察、排序或研究者提示，不应该让整轮停止：

- 路线数、初始 Population、Family、Portfolio 是否达到目标；
- Family 相似度、MMR / diversity、Interaction Graph 的 lexical shortlist；
- evidence calibration、validation feasibility、uncertainty、scientific upside；
- Profile Fit；
- complexity growth；
- 当前卡片是否已富化；
- bridge coverage 是否已有 LLM review；
- citation / note coverage 的“还需升级”提示。

它们可改变下一轮要探索什么、提醒研究者风险或保留 wildcard，但不能成为隐藏的 Candidate 删除器。

### 7.5 Hard Invariant 失败时的正确行为

硬失败不应被静默掩盖为成功，也不应通过“放松证据真实性”修复。系统应保存：失败对象、错误类型、保留的 artifact、可复用 checkpoint、是否可以局部重建、是否需要人确认。通常只停止受影响的选择/写入/Child；只有没有任何可追溯 active Candidate，或 workspace/状态安全已无法保证时，才阻止抵达 Gate1。

---

## 8. 局部失败隔离与动态 Human Recovery Gate

### 8.1 不应暂停全局 T4 的结果

| 情况 | 正确结果 |
| --- | --- |
| Opportunity Planner 失败 | fallback opportunity + diagnostic，Route 继续 |
| 一个 Route provider / schema 失败 | 一次 repair 后 `unsupported` / `partial` receipt，其它 Route 继续 |
| Route underfill | 保留非重复 Candidate；不制造 filler |
| 一个 Seed Enricher 失败 | 原 Seed 保留为 `enrichment_degraded` |
| 一个评分 batch 失败 | repair → isolation；最终 Candidate `unscored` 但可见 |
| 一个 Mutation Child 失败 | archive 该 Plan；Parent 保留 |
| 无实质 Mutation | `no_improvement` deferral；Parent 保留 |
| Crossover rejected / uncertain / parallel | 不生成 Child；两个 Parent 保留 |
| 所有 Crossover 未获批 | 仍完成 no-op / parent survival snapshot |
| Interaction Reviewer 失败 | `deterministic_degraded` graph；演化继续 |
| Final Card Compiler 失败 | Population 保留；只重试 Card compiler，不重跑科学演化；若耗尽预算，Gate1 转 Human Recovery 而不展示半成品 Card |
| Population / Portfolio 小于目标 | 显示 underfill / diversity warning；可展示 1–2 个方向 |

### 8.2 何时出现 T4 专用恢复 Gate

当 T4 因非完整性错误而未能安全到达 Gate1，State Machine 可进入 `t4_recovery_gate`。它会保留 Candidate、评分、谱系、Plan、诊断和 checkpoint，并提供：

1. **继续修复并重试**：从保存的 checkpoint 恢复，不重跑已成功 Route、评分或 Population；
2. **查看已保存 Candidate**：仅当 Gate1 artifact 已完整时，打开只读决策面板；
3. **暂停**：保存诊断，稍后再次 `resume`；
4. **结束本次运行**：本次命令结束，不删除任何 artifact。

T4 的 recoverable runtime failure 包括 provider、局部 validator/projection、card/renderer、Route、score、mutation、crossover 等中断。以下显式完整性模式不应被盲目 retry：路径越界、不可安全写入、Legacy 覆盖、状态损坏、fingerprint/selection 不匹配、ID collision、伪造 lineage 等。

### 8.3 通用 Runtime Recovery Gate

T4 之外，State Machine 还提供动态持久化 `runtime_recovery_gate`。它覆盖可恢复的 validation / artifact validation、provider、runtime、environment 与人机输入不可用问题。旧 workspace 的持久化记录可能仍提到 budget 或 max steps，但当前 ResearchOS 默认不施加普通内部 step/token 上限，除非开发者显式启用 bounded override。Gate 保存：

- target task；
- 失败类型、错误摘要、已有输出；
- `_runtime/recovery/<task>_runtime_recovery.json` 中的恢复 directive；
- 当前诊断和 resume state path。

研究者可选择：

| 操作 | 语义 |
| --- | --- |
| `retry_targeted_repair` | 只读取并修复实际失败点，保留有效内容 |
| `extend_recovery_window` | 旧 bounded-budget 恢复记录的兼容选项；当前默认运行已经不受 ResearchOS 内部 step/token 上限限制 |
| `inspect_then_pause` | 不重跑，保留 pending Gate；下次 resume 再展示 |
| `exit` | 本次命令结束，不删除 artifact；之后 resume 仍先显示恢复决策 |

任何恢复选项都不放松 Evidence Permission、schema、工具权限、引用真实性或科研表达边界。provider context window、timeout、环境就绪性和输出校验仍可能暂停运行。显式由用户选择的暂停不会再立即被另一个通用 Gate 覆盖。

### 8.4 动态 Gate 与旧 workspace 的兼容

pending Gate 的 presentation/options 被持久化在 `state.yaml`。`refresh_pending_gate_presentation()` 对运行时动态 Gate 采用“持久化内容优先、注册表装饰可选”的策略：即使旧 workspace 保存的 gate ID 在新 `gates.yaml` 不存在，`resume` 也不能因 `_find_gate()` 抛 `KeyError`。这修复了历史 `t36_assemble_recovery_gate` 一类状态直接崩溃的问题，并同样保护 T4 的动态恢复流程。

### 8.5 什么情况下仍需真正阻塞或人工判断

| 情形 | 原因 | 应有的选择 |
| --- | --- | --- |
| 缺少 T4 核心上游 artifact | 不能知道项目研究对象与基本综合上下文 | 查看缺失、回上游、导入材料、暂停、退出 |
| 所有 Route 都没有任何 traceable Candidate | 没有可诚实展示的 Population | 扩展/替换 Route、补材料、调整探索预算、暂停、退出 |
| workspace / fingerprint / lineage / selection 冲突 | 继续可能绑定错误对象 | 检查、局部重建、重新进入 Gate1、暂停、退出 |
| 用户希望增加成本、改变方向、合并组件或进入 T4.5 | 这是研究策略而非格式修复 | 清楚说明是否调用 LLM、是否新建 Candidate、是否保留历史、是否可回滚 |

---

## 9. Gate1：短 ID、完整 Idea Card 与交互演化

### 9.1 研究者 ID 与内部 lineage ID

内部 ID 例如 `S-informed-brainstorm-1` 用于 artifact、fingerprint 和谱系，不是面向用户的决策句柄。Gate1 为当前 Portfolio 和剩余 active Population 分配稳定短 ID：`D1`、`D2`、`D3`……

```text
Gate1 默认显示：D1 可控 Agentic 决策基准
谱系详情显示：Internal ID: S-informed-brainstorm-1
               Origin Route: informed_brainstorm
               Generation: P0
               Family / Parents / artifact paths: ...
```

State Machine 只解析完整 `D<number>` token，再映射回当前 active Population 的内部 ID。用户可写：

```text
选择 D1
重新演化
只优化 D2 的机制
查看 D1 的评分 / 证据 / 谱系 / 全部假设 / 文件
比较 D1 和 D3
合并 D1 + D2
回到上一代
```

`重新演化` 被解析为 `continue_evolution`，不是一个无法识别的自由文本。只读命令不调用模型，也不修改版本。

### 9.2 Gate1 Summary Table 的职责

宽表只用于快速比较，不能塞完整 pitch。它应优先显示：

- D#；
- 短标题；
- Portfolio Role：Lead / Alternative / High-upside / Parallel；
- Contribution Type；
- Core Difference；
- Scientific Readiness（三维导出摘要）；
- Profile Fit（单独的 qualitative 说明）；
- Evidence Status；
- candidate-specific Recommended Action。

内部 ID、长 pitch、完整风险、完整 evidence 文本、无解释的 `seed`、统一英文 fallback 都不应占据 summary table。终端变窄时 Renderer 应减少列或转卡片，而不是把多行长句塞进每个单元格。

### 9.3 每张完整 Idea Card 必须回答什么

Final Card 的科学解释来自 Candidate、ScoreReport 和 LLM Final Card Compiler；Renderer 只负责布局和中文本地化。卡片至少应能回答：

1. **Header**：D#、短标题、Portfolio Role、来源 Route、Candidate Stage、Contribution Type、Idea Family；
2. **One-line Thesis**：一句可比较的核心命题；
3. **Why It Matters**：真实问题、受影响过程、现有做法为何不够、若成立将改变什么；
4. **Representative Scenario**：条件性、无虚构数字的具体场景；
5. **现实意义**：把当前提案翻译为可能影响的现实决策或流程，明确潜在影响以验证成立为前提，而不是把它写成已证实结果；
6. **Scientific / Technical Core**：problem、thesis、mechanism、competing explanation、boundary；
7. **Innovation**：类型、增量、为何非例行模块堆叠；
8. **Contribution Package**：主贡献与辅助贡献；
9. **Draft Hypotheses**：直接显示 1–3 条命题、observable prediction 与 discriminating test，而非只显示数量；
10. **Validation**：minimum test、支持/否定结果、未确认 dataset/baseline/metric 的明确标记；
11. **Evidence**：当前等级、anchor/线索、能支持什么、不能支持什么、阅读升级需求；
12. **Relationship**：同 Family 的角色、差异、依赖、可组合部分与不建议合并部分；
13. **Implications**：仅展示真正适用的 scientific / engineering / managerial / business / deployment 含义，并标注 supported / inferred / speculative 与条件；
14. **Scores**：三项正式评分、导出摘要、Profile Fit、strength、bottleneck、upside/uncertainty 诊断，以及成熟度低究竟源自 Idea、证据还是验证；
15. **Risks / Boundaries**：风险、早期信号、mitigation、kill criterion；
16. **Recommendation**：该 Candidate 专属的中文下一步建议。

若字段没有上游 LLM 内容，正确显示是“展示富化待完成”或对应 diagnostic，而不是 Renderer 自行生成看似完整的学术文字。

### 9.4 Family、依赖与 Portfolio 的正确呈现

若 D1 是 benchmark/system，D2 是以 D1 为环境的 algorithm，D3 是以 D1 为基础的 evaluation study，它们不是三个无关方向。Gate1 应显示共同 Family、每个候选的研究角色、依赖箭头和可组合方式，例如：

```text
Idea Family: Agentic Decision Transfer

D1  基础设施 / Benchmark    → 提供可控环境
D2  Algorithm                → 部分依赖 D1，研究可迁移机制
D3  Evaluation / Empirical    → 强依赖 D1，研究何时迁移失败
```

Portfolio selection 首先尝试跨 Family 的 quality-diversity；同 Family 强依赖候选仍可保留和展示，但不应被伪装成三个独立、可任选其一的论文。候选不足时展示一两个真实方向，比凑满三个同质 seed 更诚实。

### 9.5 Gate1 可用操作及版本语义

| 操作 | 是否调用模型 | 是否新建 Candidate | 是否保留历史 |
| --- | ---: | ---: | ---: |
| 查看评分、证据、谱系、假设、contribution、genome、文件、其余 Population、比较 | 否 | 否 | 是 |
| 选择完整 Candidate | Gate1 不调用；随后 T4.5 可能调用 | 否 | 是 |
| 再演化一轮 | 是 | 可能 | 是 |
| 聚焦演化 D# | 是 | 可能 | 是；其它 active Candidate 保留 |
| 重跑指定 Route | 是 | 可能 | 是；旧 Route 输出仍留存 |
| 创建 Crossover | 是，先 compatibility review | 仅 approved 后 | 是 |
| 组合已选组件 | 是，先 compatibility，再二次确认 | 确认后 | 是 |
| 并行保留 | 否 | 否 | 是 |
| 调整 Publication Orientation | 可重评/重新编译取向视图 | 否 | 是 |
| 回滚到上一代 | 否 | 否 | 是；只切换 active Population pointer |
| 暂停 | 否 | 否 | 是 |

### 9.6 进入 T4.5 前的选择边界

IdeaSeed 可以留在 Population、用于下一轮 Evolution 或作为人类讨论对象；但它不应仅因为“看起来新”而直接进入 T4.5。选择 readiness 需要独立评分、完整 LLM Final Card、可追溯 Core Thesis、至少一条由 LLM 写出的可证伪草案假设，以及不会将旧 Population/选择错误绑定的当前 fingerprint。满足这些输入的 Seed 会以 provisional 方向进入 T4.5，其成熟度、证据缺口和单条假设限制全部写入 Pre-Novelty warning，由 T4.5 审计；它们不能在二次确认后变成返回 T4 的隐藏失败。

Pass2 只补充接地、风险与选择建议，不能隐藏 Candidate。尤其当 `constraint_status=not_supported_by_current_evidence` 时，Candidate 可以保持 Gate1 可见，供研究者要求补证据、补机制或继续演化；但其 `screening_recommendation` 必须是 `revise_before_selection` 或其他非直接选择状态，绝不能与 `proceed` 同时出现。该规则防止“证据待补”被 UI 误写成可直接进入最终选择，同时不会抹掉有创造价值的探索性方向。

选择后产生的是 pre-novelty material，如 `ideation/selected/selected_candidate.json`、`hypothesis_brief.yaml`、`hypothesis_lineage.json`、`t45_search_targets.json` 和 `pre_novelty_brief.md`。它们是 T4.5 检索与 collision audit 的输入，绝不是“已通过外部 novelty 审计”的宣告。

Gate1 是持续的研究对话，不是一行命令后就结束的菜单。研究者可以先输入“查看 D1”，继续追问评分原因或比较 D1 与 D3，再回到尚未确认的研究操作，而不会重新生成 T4。Enter 只是在当前轮加入新行。Ctrl+D 会提交已经输入的文本；若终端或 IDE 截获 EOF，则可单独输入 `END` 完成同样的提交。只输入 `D1` 被视为有歧义，系统必须追问是查看、推进还是优化。查看和比较是本地只读操作。推进、优化、组合或再探索会先被复述为操作计划，随后进入二次确认。

确认后的状态路径取决于操作本身。选择一个已准备好的 Candidate 走 `T4 -> T4-GATE1 -> T4.5`，并写入 pre-novelty 选择回执，不会重新运行 T4。演化、聚焦优化、重跑 Route 或已批准的组合走 `T4 -> T4-GATE1 -> T4`，先创建独立的新版本，再返回 Gate1。只读操作仍停留在 Gate1。没有提交任何文本就按 Ctrl+D 时，系统会保存 Gate 并暂停。已有待确认操作计划时按 Ctrl+D，只会保留草稿，绝不会执行。`resume` 会重新打开这个持久 Gate，不会重复已完成的 T4 模型调用。

---

## 10. Prompt、Schema、Validator、UI 的一致性检查清单

任何改动 T4 时，都必须从 prompt 一直查到恢复路径：

```text
Prompt instruction
  → payload builder
  → tolerant parser / normalization
  → Pydantic schema
  → role-specific semantic validation
  → controller contract
  → persisted artifact
  → resume loader
  → native / legacy projection
  → Gate1 ViewModel / Rich renderer
  → State Machine failure classification
  → regression fixture
```

特别容易再次出现的失配包括：

- Prompt 接受 `parallel`，但 schema、持久化 plan 或 resume loader 没有把它作为显式 no-child verdict；
- Generator 允许最小 Seed，Projection 却把缺 card 当成全局失败；
- 新 Scorer 只给三项，Legacy Grid 被错误当硬前置；
- `overall_readiness` 被模型输出或 UI 当成独立第四分；
- Profile Fit 被错误地作为 survival 数值乘子；
- Final Card 想显示 Family/依赖，数据层却没有让 LLM 编译关系；
- Renderer 缺字段时用英文通用 recommendation 填充；
- parser 已经 normalize，新 resume loader 却直接 strict-validate 旧 checkpoint；
- catalog 与 `bridge_notes` 被重复扫描，或空 `bridge_notes` 被误判为没有 Cross-domain 信息；
- Gate 保存了运行时动态 ID，但 resume 又要求它必须存在于当前 YAML registry。

每一种失配都应有一个最小 regression fixture。不要只依赖“真实 API 有时跑通”的 happy-path 测试。

---

## 11. 配置、预算与成本边界

主要配置在：

```text
config/system_config/t4_evolution.yaml
config/system_config/idea_scoring_rubric.yaml
config/system_config/idea_evidence_permissions.yaml
config/system_config/idea_evolution_operators.yaml
config/system_config/t4_target_profiles.yaml
config/system_config/gates.yaml
```

当前默认值的含义应理解为：

- 初始 Population 最大 14，active target 7，允许范围 6–8，Portfolio 1–3；这些是目标，不是数量 hard gate；
- Mutation 默认 2–4，Crossover 0–2，总 offspring 最多 6；没有适合的 Child 时可为 0；
- Opportunity 默认 3–6；
- 最大自动轮数 3；
- Route 并发默认 2，独立评分 batch 默认 3；
- `family_similarity_threshold=0.45` 仅用于召回可比较 Family，不证明学术等价；
- `complexity_growth_ratio_limit=1.8` 用于复杂度诊断；
- bridge policy 默认 `allow_abstract_with_upgrade`，即 catalog / abstract 可作为跨域创意和升级线索，不能变成机制认证。

预算只约束探索成本、并发与恢复窗口。它不应迫使模型收缩可表达的科学假设，更不能通过把模糊内容写成“已经验证”来提高通过率。

---

## 12. 调试、artifact 检查与回归验证

### 12.1 先看状态，再看具体对象

```bash
python -m researchos.cli workspace-status --workspace ./workspace/<name>
python -m researchos.cli validate --task T4 --workspace ./workspace/<name>
python -m researchos.cli trace <run-id> --workspace ./workspace/<name>
python -m researchos.cli validate-config --no-banner
```

| 现象 | 首先检查的 artifact | 预期诊断方向 |
| --- | --- | --- |
| Cross-domain 看起来为空 | `bridge_domain_plan.json`、`cross_domain_catalogs/index.json`、每个 `paper_catalog.json` | 区分 catalog 缺失、读取回退、无真实 deep note 与真正检索失败 |
| 一个 Route 没 Candidate | `ideation/evolution/routes/round_0/<route>.json`、`diagnostics/` | supported/partial/unsupported 与 repair attempt |
| P0 Candidate 看起来太薄 | `enrichment/<id>.json`、enrichment diagnostics、Candidate dossier | Seed 是否已保留、Enricher 是否局部降级 |
| 模型格式错误 | `diagnostics/*structured_output*` 与 role response repair artifact | fence/YAML/alias/semantic repair 是否已发生 |
| Scorer 缺分或超时 | `evolution/scoring/`、unscored receipt、score isolation plan | 不应再把候选删除或全局暂停 |
| Crossover 没有 Child | `plans/round_<n>.json`、Compatibility decision、offspring deferral | `parallel/rejected/uncertain` 是否被正常保留 |
| Child 没进入 Population | `round_<n>_diagnostics.json`、GeneDelta、Complexity、survival | cosmetic/regressive、Plan-local failure 或 Pareto/diversity 结果 |
| Gate1 内容不完整 | `final_cards/portfolio_cards.json`、`_candidate_directions.json`、`_gate1_candidate_cards.md` | final card / projection degraded，而非 Candidate 已丢失 |
| resume 直接报 gate KeyError | `state.yaml` 的 pending gate、State Machine refresh 路径 | 动态 Gate 应使用持久化 presentation，不应依赖 registry 存在 |
| 用户指令未识别 | `ideation/_gate1_user_selection.json`、directive artifact、D# map | 确认短 ID 映射和 typed directive parse |

### 12.2 必要的长期回归类别

#### 格式与兼容

- fenced JSON、YAML、trailing comma、prose-wrapped object；
- 顶层 list、单 Candidate object、字段 alias、字符串数值；
- `parallel`、`并行保留`、单字符串 `conflicts`；
- 历史五维 ScoreReport 的可读迁移，新的三维 Scorer 不要求 legacy grid；
- 旧 dynamic recovery gate 在 registry 缺失时仍可 resume。

#### 局部失败与连续性

- 单 Route timeout / unsupported / underfill；
- Candidate Enricher timeout / malformed output；
- Score batch 失败后 isolate，最终 unscored candidate 保留；
- Interaction Reviewer 失败后 graph `deterministic_degraded`；
- Mutation Child 无效 / no improvement；
- 所有 Crossover incompatible / parallel；
- Final Card compiler 失败后 Population 仍可保留；Gate1 必须进入 LLM Card 修复 / Human Recovery，不能显示半成品 Candidate Card；
- Population 小于目标、Portfolio 只有 1–2 个。

#### 科学与状态完整性

- abstract-only / catalog 不得被升级为 mechanism support；
- SourceRef、Parent、Plan、Gene Donor Map、Candidate ID 不得伪造；
- Child 不能覆盖 Parent；
- 输入/运行配置/selection fingerprint 不得混用；
- Legacy 不得静默接管原生流程；
- Renderer 不得生成新的科研 Claim。

#### 人机交互与恢复

- Pre-run 确认后 `run-task T4` 必须实际进入 Controller；
- 可恢复 T4 故障进入 `t4_recovery_gate`，通用校验、provider、环境或人机输入中断进入 `runtime_recovery_gate`；
- `retry` 复用成功 artifact；旧 `extend_recovery_window` 仅作为 bounded-recovery 兼容记录保留，不代表当前默认运行存在 step/token 上限；
- D# 默认可读，内部 ID 仅在谱系详情；
- `重新演化`、`只优化 D2`、`查看 D1 的证据`、`比较 D1 与 D2` 能解析；
- 窄/宽终端均不将长 pitch 塞入 summary table。

#### 真实 API 与容器 smoke

真实 API 测试不能用 mock 成功替代，应限制 Route 数、轮数和预算，不打印 key、不使用敏感用户数据，并记录 provider、错误类型、repair/degradation 路径、Candidate 数、Gate1 状态。Docker smoke 应在最小 workspace 中验证配置、artifact 写入、resume、动态 Gate 和可读 Gate1。

### 12.3 不要用这些方式“修复”T4

- 不要删除 Candidate、评分或整个 Population 来掩盖一个 schema error；
- 不要因一个 Route 或 Child 失败重跑整轮；
- 不要因为没有 deep note 删除 Cross-domain catalog；
- 不要让旧 `ideation.j2` 静默取代 native controller；
- 不要用虚构 citation / dataset / metric / result 来填满 card 或 validator；
- 不要为了凑三个 Card 将同 Family 的基础设施、算法、评测模块假装成三个独立论文；
- 不要把 Evidence Permission 放松成“任何模型说法都可当已证实事实”。

---

## 13. 当前实现边界与下一步审计重点

本节不是降低标准，而是明确系统当前已经做什么、还应持续测试什么。

1. `auto` 当前是可配置的 0–3 轮预算模式，不是会自主判定“再跑一轮还是停止”的 LLM 研究策略器；这种自动决策若加入，应有独立 artifact、可解释理由和人类 override。
2. Interaction Graph 的确定性层只做 shortlist；它必须始终被当作可解释的召回启发式，而非学术关系或分数。
3. Candidate Enricher 和 Interaction Reviewer 是可选的高价值 LLM 扩展；它们局部不可用时系统应透明降级，不能编造内容补洞。Final Card Compiler 对 Population 存续仍是可恢复的展示扩展，但对当前 Portfolio 的可选择 Gate1 是必经的 LLM 解释层：失败必须走定向修复与 Human Recovery，不能用旧字段替代。
4. Legacy Gate1 文件仍被保留以兼容旧 workflow，但它们不拥有 Candidate 真相。新增 UI 字段应优先从 typed native dossier / score / card 读取。
5. Cross-domain catalog 的迁移是 non-destructive；对旧目录的读取回退必须一直避免重复注入同一 bridge record。
6. 每次改 Prompt、Schema、Validator、State Machine 或 Renderer 时都应运行本章测试矩阵；T4 的历史问题多来自层间语义不同步，而不是单个模型“能力不足”。

### 13.1 已发生故障的契约复盘：为什么旧流程曾能运行，新路径却暴露错误

“以前系统提示词渲染、T4 Context Pack、LLM 路由和工具构建都没有错”并不说明这些环节是这次 T4 故障的直接根因。需要按实际失败点区分，不能把所有异常都归因于模型或 Context Pack。

| 现象 | 实际失败边界 | 为什么旧路径没有暴露 | 正确修复 | 不应采取的修复 |
| --- | --- | --- | --- | --- |
| `decision='parallel'` 被 Pydantic 拒绝 | Crossover Reviewer 的新 Prompt 把“并行保留”作为合理结论，但旧 `CrossoverCompatibilityDecision` 只允许 `approved/rejected/uncertain` | 旧 Prompt 没有明确鼓励该结论，或先前模型刚好使用旧枚举 | 将 `parallel` 作为持久化、不可生成 Child 的显式 verdict；只让 `approved` 进入 Child compiler | 把 `parallel` 偷偷改写为 `rejected`，或因一对 Parent 不适合合并而丢弃整轮 |
| `conflicts` 是一个字符串而不是列表 | 模型提供了一条完整的冲突解释；Schema 把容器形状误当成科研正确性 | 先前输出碰巧使用数组 | 在解析边界将非空单字符串规范化为单元素列表，并保留原解释 | 删除冲突解释、将其当作无效科研判断，或放松 Parent/Gene 身份校验 |
| 非批准 verdict 返回空 donor map | `parallel/rejected/uncertain` 的 `{donors:{}}` 语义是“没有发生基因转移”，旧 `GeneDonorMap` 的非空约束却在此前先触发 | 先前输出没有该占位对象 | 对非批准 verdict 丢弃无语义 donor placeholder；批准的 Child 仍严格要求非空 donor map | 允许 `approved` Child 使用空 donor map，或用代码编造 donor |
| Scorer 返回 `7/8`、顶层维度或三元素分数序列 | 三维 rubric 实际要求 `1.0–5.0` 的命名对象，Prompt 对尺度不够显式，parser 又没有覆盖几个无损外壳 | 旧模型输出偶然沿用 `1–5` 的对象；只有某些 Candidate / repair call 使用了另一种表达 | Prompt 与 runtime contract 均显式声明 `1.0–5.0`；顶层三维字段、已声明顺序的三元素序列和嵌套 qualitative diagnostic 无损恢复；超范围值交给独立 Scorer 重新评估 | 自动 clamp、把 10 分制随意除以二、用 readiness 或旧分数补新分，或把未评分 Candidate 删除 |
| T3.6 报 `bridge_catalog_count is undefined` | `survey_writer.j2` 新增 Cross-domain catalog 条件块，`survey_compile` 调用仍缺少同名模板变量；Jinja 的 `StrictUndefined` 在模型调用前抛错 | 模板新增前没有该变量；其它 phase 可能恰好传了该变量 | 由同一个 Survey Writer context builder 为所有 phase 提供完整默认上下文，并以 prompt-render regression 覆盖每个 phase | 在 Jinja 中用空白套话掩盖变量，或把 catalog 删除来避开分支 |
| Context Pack / LLM routing / tool construction 抛出 CLI traceback | 这些启动动作曾位于 `AgentRunner.run()` 的常规异常边界之前，因此任何代码、路径或配置缺陷都能逃逸到 CLI | 没有异常时自然看起来正常；这不是一次已证明的正常 T4 LLM 调用失败 | 将它们纳入启动期恢复边界，持久化 `runtime_recovery` 诊断并进入 Human Recovery Gate | 静默退回到未校验的原始上下文、吞掉错误继续生成，或把 trace 当成用户操作错误 |
| resume 时找不到动态 recovery gate | 工作区保存的是一次性恢复 Gate；旧 refresh 逻辑却假定所有 Gate 都必须存在于静态 YAML registry | 只有命中恢复分支和 resume 才会出现 | 用持久化 presentation 重建动态 Gate；静态 Gate 缺失在 `validate-config` 中明确报配置错误 | 捕获 `KeyError` 后静默跳过 Gate，或让用户失去 repair / pause / retry 选择 |

因此，四个“启动环节”已经被加上统一恢复边界，是为了避免今后任何启动期代码错误再次变成裸 traceback；它们不是本次三次 T4 实例中 `parallel`、`conflicts` 和空 donor map 的替代解释。真实 T4 调用在这些启动步骤之后已经成功进入 Evolution Planning、Child 生成和独立重评分，才会触及 Crossover 结构化输出的旧契约。

每次新增 Prompt 字段、枚举值或 artifact 后，必须同时检查下面五个消费者，而不是只测试首个模型请求：

```text
Prompt / runtime contract
  -> tolerant parser + typed schema
  -> checkpoint persistence + resume loader
  -> controller decision consumer
  -> Gate ViewModel / renderer + regression fixture
```

这是“代码 bug 不可静默”的精确定义：遇到真正的代码、路径或配置错误，运行必须留下分类诊断并进入可恢复 Gate；遇到表层模型表达差异，则应先无损规范化或要求模型修复；两者都不能伪装成一次普通的科研质量否决。

---

## 14. 代码地图

| 领域 | 主要实现 |
| --- | --- |
| 原生 Controller、P0/P1/P2、checkpoint、局部修复 | `researchos/ideation/evolution_controller.py` |
| Candidate / Score / Plan / Card schema | `researchos/ideation/models.py` |
| LLM roles、payload、parser、semantic repair | `researchos/ideation/llm_roles.py` |
| tolerant response recovery 四态 | `researchos/ideation/response_recovery.py` |
| Evidence Index 与 permission | `researchos/ideation/evidence.py` |
| Cross-domain catalog 迁移与加载 | `researchos/runtime/bridge_catalog.py` |
| Pre-run inspection、directive、run config | `researchos/ideation/prerun.py` |
| 三维分数权重与 Publication Orientation | `researchos/ideation/target_profile.py` |
| Family、Pareto survival、Portfolio | `researchos/ideation/population.py` |
| Interaction Graph | `researchos/ideation/interaction.py` |
| 原生到兼容 Gate1 projection | `researchos/ideation/legacy_projection.py` |
| selection → T4.5 编译 | `researchos/ideation/selected_compilation.py` |
| Gate、directive、resume、runtime recovery | `researchos/orchestration/state_machine.py` |
| T4 roles 装配、card 编译、运行时恢复信号 | `researchos/runtime/orchestrator.py` |
| 完整 Pipeline Runner | `researchos/cli_runners/complete_pipeline.py` |
| SingleTask Runner | `researchos/cli_runners/single_task.py` |
| Rich Idea Evolution UI | `researchos/ui/idea_evolution_renderer.py` |
| Prompt 模板 | `researchos/prompts/idea_*.j2` |
| 默认探索参数 | `config/system_config/t4_evolution.yaml` |

---

## 15. 最终原则

```text
Validators protect integrity.
Repair loops protect continuity.
LLM agents preserve and deepen scientific imagination.
Evolution handles incomplete ideas without pretending they are complete.
Cross-domain context broadens search without becoming false evidence.
Human Gates retain authority over research direction and exploration cost.
```

只要这六条同时成立，T4 才既不会把有跳跃性的 Idea 扼杀在表单中，也不会为了“跑通”而牺牲科研真实性与可恢复状态。
