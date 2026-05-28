# ResearchOS 设计优化建议 v2：CDR 统一框架

> **v2 修订说明**：v1 试图用"强制多来源 provenance（≥2 篇、必须 bridge、必须附 closest_baseline）"来治 marginal——但那本身就是 marginal 的病根（把 idea 拴回现有文献）。v2 推翻这一类设计，确立新原则并据此重写架构。

---

## 0. 核心原则（统领全文）

**Provenance ≠ Quality。文献是 grounding/check，不是 generative seed。**

1. 一个好 idea 可以来自：综述**整体格局**（synthesis gestalt）、**问题重构**、**第一性原理的设计论证推导**、**跨域类比**、甚至与所有现有工作**正交**（无 closest baseline）。
2. 判 idea 好坏的**唯一标准是 contribution character**——前瞻性问题："如果它成立/被做出来，领域会因此**怎样不同**？" 不是它引了几篇、离最近工作多近。
3. 因此：`supporting_papers` / `closest_baseline` / `from_synthesis_section` 等 **provenance 字段一律降级为自由文档，永不做 gate**。
4. **"没有相近工作" = 最高 novelty 信号**（伴随高风险/可行性 flag），绝不是要去填的缺口。
5. 单一范式：**默认就是 CDR，不再分 IS / DS 分支**。好论文的写作思路是统一的——任何强贡献都要从 design rationale（为什么这样设计）+ data view（凭什么数据/证据相信它）讲清楚。

---

## 1. 架构重构：两段式 ideation（这是"产不出好 idea"的根本答案）

**问题诊断（架构级）**：当前流水线 = `检索 → 精读 → 压缩成综述 → 从综述推 idea`，是一个 **literature-derivative** 模型，结构天花板就是"对现有论文的重组"。无论加多少 gate，产出都锁在增量区。光修 prompt 治不了，必须把**生成**和**验证**拆开。

### Pass 1 — 前向生成（文献只当背景，不当约束）**【硬 / 改 T4 流程】**

给定**问题结构** + 综述里浮现的设计论证、张力（tension）、未解释现象，**向前生成**候选 design rationale。明确允许并鼓励这些来源：

- `problem_reframing`：把问题本身换一种看法。
- `design_rationale_derivation`：从问题结构出发，第一性原理地推"设计**应该**是什么样"。
- `cross_domain_analogy`：迁移其他领域的设计论证（**用模型自身知识 + 已有语料，不触发再检索**）。
- `synthesis_gestalt`：从综述整体涌现，不指向任何单篇。
- `free_reasoning` / `seed_refinement` / `evidence_driven`：保留。

**这一步不要求每个 idea 指向具体论文，也不强制 closest_baseline。**

### Pass 2 — 文献接地 / 验证（文献当验证器）**【硬 / 改 T4.5 流程】**

对每个候选，回查综述 / comparison_table / paper_notes，完成三件事：

- **Novelty check**：确认未被做过；**只有当确实存在相近工作时**才记录 closest work 与差异。无相近工作 → 标 `novelty: high, prior_art: none`（高新颖 + 高风险）。
- **Feasibility check**：在预算/资源内能否验证。
- **诚实记录 provenance**（自由文档）：可以是"综述整体涌现""类比 X 领域""与现有工作正交"。

Pass 2 **能下调或砍掉** idea，但**不负责"长"出** idea。这样生成不被语料封顶，验证又保证不空中楼阁。

> 落点：`ideation.j2` 把现有 Step 2（发散）拆成 Pass 1 / Pass 2 两个明确阶段；`ideation.py` orchestration 据此分两步执行。**不增加任何回到 T2 的检索回路。**

---

## 2. CDR Design Tuple（统一受控词表，单一事实源，无分支）

新增 `config/cdr_schema.yaml`，定义贯穿 T2→T3→T3.5→T4→T4.5→T7.5→T8 的统一字段。所有 prompt 从它渲染，所有 validator 引用它。**【硬 / ALL】**

| 字段                  | 含义                                                         |
| --------------------- | ------------------------------------------------------------ |
| `problem_frame`       | 问题/现象 + 场景/利益相关者                                  |
| `design_rationale`    | **为什么是这个设计**（justificatory knowledge）——CDR 的心脏  |
| `artifact`            | 被设计的东西：construct / model / formulation / method / system |
| `design_principles`   | 抽象、可迁移的设计知识（适用时）                             |
| `data_view`           | 数据范式 + 识别/效度策略 + 评估设置                          |
| `evaluation_mode`     | 怎么知道它有效（formative/summative；naturalistic/artificial 或 benchmark/统计/部署） |
| `contribution_type`   | 受控枚举：`invention` / `improvement` / `exaptation` / `routine`（routine 默认被 gate 拦） |
| `boundary_conditions` | 在哪成立 / 在哪失效                                          |
| `cross_paper_tension` | 与哪几篇在哪个点上冲突/不可通约/设计论证竞争                 |

> **关键澄清**：`cross_paper_tension` 是**生成素材（启发 idea）**，**不是 provenance 约束**。它出现在 T3 笔记和 T3.5 综述里，给 Pass 1 当燃料；T4 不强制"每个 idea 必须 bridge 一个 tension"。
>
> `contribution_type` 是**质量信号**（驱动 anti-incrementalism gate）；`design_rationale` 是**论证骨架**（贯穿到写作）。这两者——而非 provenance——才是判 marginal 的标准。

---

## 3. 端到端一致性：CDR 必须到达 T7.5 与 T8 **【硬 / ALL】**

最容易白干的一点：若 PI 评估（T7.5）和写作（T8）仍按"指标是否超 baseline"判断，CDR 会在评估关**退回 CS**，T4 记的 `design_rationale` / `contribution_type` 没人消费，idea 又被拉回增量。

- T7.5 评估标准 = "`design_rationale` 是否被实验证据支持、`contribution_type` 是否兑现"，而非只看主指标。
- T8 写作（Intro/Related/Discussion）以 `design_rationale` + `contribution_type` 为叙事主轴，而非 results 表。
- 实现：T4 的 Design Tuple 进 workspace，作为 T7.5 与 T8 section_plan 的强制输入。

---

## 4. T2 Scout — 检索广度（`scout.j2` + runtime 裁剪）

目标：给 Pass 1 一个**足够宽**的背景语料，但**广度只服务于"接地与类比的素材"，不用于"约束 idea 必须来自语料"**。

- **4.1 跨域论文保护配额** **【硬】**：runtime 裁剪（行 451–453）和 deep_read_queue 排序优先砍低 relevance 的跨域论文——而这正是类比/重构的素材。给 `adjacent_field=true` 论文留固定配额（如 ≥15%），并保证它们能拿到 full-text 笔记（不沦为 abstract-only）。
- **4.2 venue/source mix 倾斜** **【软+硬】**：补 INFORMS 之外的相关 venue 与数据挖掘系；弱化纯 arXiv/S2 的 CS 默认偏向（它从源头注入 CS 化语料）。query 模板里"相邻领域""理论桥接"槽位从建议升级为**必填 bucket**。
- **4.3 不做任何"覆盖不足→自动再检索"的触发**：覆盖统计只作诊断展示，**绝不**触发自动补检循环（避免 T2 自己也进死循环）。

---

## 5. T3 Reader 笔记 schema（`reader.j2` read 模式）— **最高杠杆**

笔记里没有的东西，T3.5 综合不出来，Pass 1 也没素材。

- **5.1 用 CDR Design Tuple 扩 13 节模板** **【硬】**：在 §1–§13 之上新增 `§14 Design Rationale`（最重要）、`§15 Artifact & Design Principles`、`§16 Data View & Evaluation Mode`、`§17 Contribution Type`、`§18 Boundary Conditions`。
- **5.2 新增 `§19 Cross-Paper Tension`** **【硬】**：当前所有字段都是单篇局部。记录本篇与已读笔记的具体冲突点。≥3 篇后每篇至少 1 条 tension（或显式声明无张力+理由）。**这是 idea 燃料，不是约束。**
- **5.3 改 §6 增量措辞** **【软】**：行 192 "哪些部分**可以改进**" → "这篇的 design rationale 在哪些边界/假设上**最脆弱、最可被重新设计**"。从"改进点"转向"设计论证的可挑战点"。
- **5.4 validator 配套** **【硬】**：强制 §14（非空）、§17（合法枚举）、§19（覆盖）。full-text 笔记要求完整 tuple；abstract-only 只填子集。

---

## 6. T3.5 Synthesis（`reader.j2` synthesize 模式）

- **6.1 换前沿框架** **【硬】**：`§3 Performance-Efficiency Frontier + 空白区域`（行 420–427）是 CS 框架，定义机会为"指标差一点"。换成 **Contribution-Space Map**：按 `design_rationale` 谱系聚类，标出 design-rationale 缺口 / 未解释现象 / 未被利用的问题表述。"空白" = 设计论证空白，不是指标空白。
- **6.2 Shared Assumptions → + Cross-Paper Contradictions** **【硬】**：消费 §5.2 的 tension。矛盾比共识更能催生非增量 idea。校验须列出 ≥N 个 tension。
- **6.3 输出对齐 Pass 1** **【硬】**：synthesis_workbench.json 增 `contribution_space` 与 `cross_paper_tensions` 块，作为 Pass 1 的背景素材（**素材，非约束**）。

---

## 7. T4 Ideation（`ideation.j2`）— 落地两段式 + 质量门

- **7.1 两段式落地**：Step 2 拆成 Pass 1（前向生成，§1）/ Pass 2（文献接地，§1）。
- **7.2 合法 origins 扩展并平权** **【硬】**：`idea_origin` 枚举加入 `synthesis_gestalt` / `problem_reframing` / `design_rationale_derivation` / `cross_domain_analogy`，与 `evidence_driven` 等**完全平权**。删除任何"主线必须来自 X 篇/必须对应某 Q"的硬性要求。
- **7.3 provenance 全部 optional** **【硬】**：`supporting_papers` / `closest_baselines` / `from_synthesis_section` 改为**可空文档字段**；validator **不**校验其数量或存在。`prior_art: none` 是合法且高新颖的值。
- **7.4 anti-incrementalism gate（前瞻式）** **【硬】**：pre-mortem（行 314–358）加第 4 维——强制回答 contribution character 问题："若成立/做出来，领域会**怎样不同**？这是 invention/improvement/exaptation 还是 routine？凭什么不是 routine？" 判为 routine 或论证不足 → gate 拦下。Gate1 把每个 idea 的 `contribution_type` 直接 paste 给用户。**这是唯一的 marginal 守门，取代所有 provenance gate。**
- **7.5 novelty 评分锚 contribution character，不锚 baseline 距离** **【软+硬】**：`novelty` 高分的理由是"它推进/重构了哪个 design rationale、领域会怎样不同"，**不是**"离 closest_baseline 多远"。无相近工作 → novelty 取高分 + 标注高风险。
- **7.6 删 `paper_shapability`** **【硬】**（行 75/158/590/624）：它系统性奖励"安全好打包"= marginal。换成 `contribution_strength`（由 `contribution_type` 驱动）。
- **7.7 类比通道为主线，不再是受限补充** **【硬】**：`cross_domain_analogy` 进 Pass 1 主线，用模型自身知识 + 已有语料，**不触发再检索**。这是 `exaptation` 贡献的主要来源。
- **7.8 mechanism 框架 CDR 化** **【软】**：mechanism/prediction/counterfactual 保留为"可检验命题"层；在 CDR 下补 `design_rationale` + `proposition` + `evaluation_episode`（formative/summative）。无 IS/DS 分支，统一一套。

---

## 8. T4.5 → Novelty + Ambition Audit

- **8.1 加正交的 ambition 轴** **【硬】**：现有 Level 0-3 + mechanism tuple 只查 collision（撞车），抓不到增量。新增独立轴 `ambition / contribution_distance`（消费 `contribution_type`）：范式推进 / 中度 / 增量。最终 gate = collision × ambition，两轴都过才放行。
- **8.2 ambition 轴严禁惩罚"无 baseline"** **【硬】**：与所有现有工作正交 = 高 ambition + 高风险，应放行（标风险），不得因"找不到相近工作"降级。collision 轴查重复，ambition 轴查增量，**两者都不查 provenance 数量**。
- **8.3 mechanism tuple → design-rationale tuple** **【硬】**：`extract_mechanism_tuple` 扩展为抽 `design_rationale + contribution_type`，碰撞检测比的是"设计论证/贡献定位像不像"，而非只比机制词。

---

## 9. 多样性：抗 mode collapse（不靠 provenance gate）

- **9.1 多次独立发散 + 聚类** **【硬】**：单次高温发散会收敛到少数主题（已知 LLM idea diversity collapse）。Pass 1 做 K 次独立发散（不同种子/persona/切入 lens），聚类去重，每簇取代表。多样性来自**结构**，不是高 temperature，也**不是强制多来源**。
- **9.2 来源集中度仅作弱诊断** **【软】**：可以把"多数 idea 依赖少数论文"作为**展示给用户的提示**，但**绝不做 gate**——因为 synthesis_gestalt / 正交 idea 天然 provenance 稀疏。质量永远由 §7.4 的 contribution gate 判，不由集中度判。

---

## 10. 文件清单（落地视图）

| 文件                                        | 动作                                                         |
| ------------------------------------------- | ------------------------------------------------------------ |
| `config/cdr_schema.yaml`                    | 新增：Design Tuple + contribution_type 枚举（单一事实源，无分支） |
| `reader.j2` (read)                          | 加 §14–§19；改 §6 措辞                                       |
| `reader.j2` (synthesize)                    | 换贡献空间地图；加 contradictions；workbench 增 contribution_space/tensions |
| `scout.j2` + runtime 裁剪                   | 跨域 reserved quota；venue mix；必填跨域/理论 query bucket；**无再检索触发** |
| `ideation.j2`                               | 两段式 Pass 1/2；origins 平权；provenance 全 optional；前瞻式 anti-incrementalism gate；删 paper_shapability；类比主线 |
| `ideation.py` validator                     | 撤销所有 provenance 硬约束；改为 contribution_type gate；多次发散+聚类 orchestration |
| `novelty_auditor.j2` + `mechanism_tools.py` | 加 ambition 轴（不罚 no-baseline）；tuple 扩为 design-rationale tuple |
| `experimenter`/`pi`(T7.5)/`writer`(T8)      | 消费 Design Tuple，按 contribution 评估与叙事                |
| `config/state_machine.yaml`                 | 加 ambition gate（不过则回 T4 重发散）；**不加任何 T4→T2 回路** |

---

## 11. 落地顺序

1. `config/cdr_schema.yaml`（公共词表，防漂移）。
2. **T3 笔记 schema（§5）**——抬天花板，最高杠杆。
3. T3.5 综合框架（§6）——顺势升级为贡献空间 + tensions 素材。
4. T2 检索广度（§4）——给 Pass 1 喂宽语料（仅作素材）。
5. **T4 两段式 + 质量门（§7）+ 多样性（§9）**——核心修复。
6. T4.5 ambition 轴（§8）+ 状态机 ambition gate（§10）——守门。
7. T7.5 / T8 一致性（§3）——闭合，防评估关退回 CS。

> 一句话：**先定词表 → 抬 T3 天花板 → 顺势改 T3.5/T2 → 把 T4 拆成"生成/验证"两段并以 contribution 守门 → 最后闭合 T7.5/T8。全程不靠 provenance 约束，不加再检索回路。**

---

## 12. 风险与权衡

- **schema 膨胀**：§14–§19 让 T3 更慢更贵。缓解：字段一句话级、多用枚举；abstract-only 只填子集。
- **模型糊弄新字段**：所有"硬"项必须 validator 落地，且查占位（如 contribution_type=improvement 但 design_rationale 空 → 拒）。
- **前向生成放飞 / 不可行**：靠 Pass 2 接地兜底——生成可天马行空，验证必须砍掉不可行/已存在的。
- **anti-incrementalism gate 误杀**：contribution gate 判的是"论证是否站得住"，不是"是否激进"；exaptation/正交都应放行（带风险标注），只拦 routine 与论证空洞。
- **去 CS 化过度**：CDR 本就是统一框架，不再分支；若确为纯 CS/ML venue，则 mechanism 层即可独立承担，design_rationale 退化为方法动机即可，不冲突。

2. 当前 T8 最大缺口
缺口 1：T8-RESOURCE 没有显式生成 CDR ledger

现在 resource_index 阶段会生成：

drafts/manuscript_resource_index.json
drafts/section_plan.json
drafts/evidence_plan.json
drafts/figure_table_plan.json

这是好设计。

但按 CDR 设计，T8 还应该额外生成一个强制文件：

drafts/cdr_claim_ledger.json

它应该来自：

ideation/idea_scorecard.yaml
ideation/hypotheses.md
ideation/novelty_audit.md
experiments/results_summary.json
literature/synthesis.md
literature/synthesis_workbench.json

建议结构：

{
  "paper_thesis": "...",
  "cdr_tuple": {
    "problem_frame": "...",
    "design_rationale": "...",
    "artifact": "...",
    "design_principles": ["..."],
    "data_view": "...",
    "evaluation_mode": "...",
    "contribution_type": "invention | improvement | exaptation | routine",
    "boundary_conditions": ["..."],
    "cross_paper_tension": ["..."]
  },
  "contribution_claims": [
    {
      "claim_id": "C1",
      "claim": "...",
      "cdr_field": "design_rationale",
      "required_section": ["introduction", "methodology", "analysis"],
      "evidence_artifacts": ["experiments/results_summary.json", "experiments/ablations.csv"],
      "citation_plan": ["bibkey1", "bibkey2"],
      "risk_if_unsupported": "high"
    }
  ]
}

如果这个文件不存在，后面的 T8 就会自然退回普通写作：

problem -> method -> experiment -> result -> contribution

而不是 CDR 写作：

problem frame -> design rationale -> artifact -> design principle -> data view -> evaluation -> contribution character

缺口 2：各章节写作规则还没有 CDR 分工

现在单章节写作规则是合理的，比如 Method 不用实验结果证明方法有效，Experiments 写 setup/main results/ablations，Related Work 按 taxonomy 组织，Analysis 解释机制、替代解释和 failure cases。

但 CDR 下，每章应该有更明确的职责。

我建议这样改：

章节	现在职责	需要补的 CDR 职责
Abstract	problem/method/evidence/result/contribution	必须压缩 problem_frame + design_rationale + contribution_type
Introduction	背景、gap、insight、贡献	必须回答“领域会怎样不同”
Related Work	taxonomy 和差异点	应改为 design rationale competition，不只是方法分类
Method	方法流程	必须说明 artifact 为什么这样设计
Experiments	setup/results/ablation	必须对应 data_view 和 evaluation_mode
Analysis	机制证据、failure cases	必须验证或削弱 design_rationale
Limitations	direct-full、外部有效性	必须写 boundary_conditions
Conclusion	收束贡献	必须回到 contribution_type 和 transferable design knowledge

尤其是 Related Work。当前提示是“按 taxonomy 组织，不按论文流水账”。这还不够。CDR 下应进一步要求：

Related Work 不是只说已有方法做了什么，
而是比较不同 design rationale 的竞争关系。

例如：

Family A assumes the bottleneck is retrieval accuracy.
Family B assumes the bottleneck is planning decomposition.
Our work instead treats the bottleneck as design-rationale misalignment under ...

这会比普通 related work 强很多。

缺口 3：Reviewer 现在审“论文质量”，不审“贡献性质”

reviewer.j2 当前审稿维度是内容完整性、技术准确性、写作质量、学术规范，并要求数字验证、引用验证、匿名化、逐章审稿。

这对普通论文足够，但对 CDR 不够。因为它不会主动问：

这个 design rationale 是否清楚？
contribution_type 是否兑现？
是否其实只是 routine improvement？
experiments 是否真的支撑 design rationale，而不只是支撑 metric gain？
boundary conditions 是否和 data view 一致？

因此 Reviewer 应该增加一个 CDR Contribution Review 维度。

建议在 reviewer prompt 里加：

### 5. CDR Contribution Audit

请独立判断：

1. Problem frame 是否清楚，还是只是普通 task description？
2. Design rationale 是否解释了“为什么这样设计”，而不只是“我们怎么做”？
3. Artifact 是否明确，是 construct / model / formulation / method / system 中哪一种？
4. Contribution type 是否兑现：
   - invention: 是否真的提出新 artifact/design principle？
   - improvement: 是否超越指标提升，解释了为什么改进成立？
   - exaptation: 是否明确迁移了外域设计论证？
   - routine: 如果只是 routine，必须标为 Major Issue。
5. Experiments 是否验证 design rationale，而不只是验证主指标？
6. Boundary conditions 是否诚实？
7. Related Work 是否呈现 design-rationale tension，而不是只列 taxonomy？

综合审稿报告里也应该加一节：

## CDR Contribution Verdict

- Problem frame clarity: Pass / Weak / Fail
- Design rationale support: Pass / Weak / Fail
- Contribution type credibility: Pass / Weak / Fail
- Evidence alignment: Pass / Weak / Fail
- Boundary condition honesty: Pass / Weak / Fail
- Verdict: CDR-ready / Needs reframing / Routine contribution risk

这样 Reviewer 才能真正防止 T8 把 CDR idea 写回普通 incremental CS paper。