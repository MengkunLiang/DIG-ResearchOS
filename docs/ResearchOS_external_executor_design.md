# ResearchOS 外部执行器阶段设计文档

这一部分要注意前后衔接，前面Pre\-T5会传入一些文件信息，或者你可以让CC/Codex自己读某些文件夹，困难的任务都交给他，比如方法设计、抓资源、跑实验、改方法、重跑实验直至Sota，然后整理方法与绘图，以及实验结果与绘图。将对应的资料传给后续writer agent。

我感觉传入文件信息给cc/codex的时候，可以让cc/codex re\-boosting这个文件，要不然会有一定的misalignment，这也是一种增强。

https://github\.com/mattpocock/skills

上面是大佬的工程skill，写代码加进去

https://github\.com/mikubaka88/CCFA\-Skills

## 核心定位

外部执行器阶段不是简单“让 Codex / Claude Code 去跑实验”，而是把 Pre\-T5 已经形成的研究设想、文献证据、新颖性边界、方法意图和实验约束，重新编译成一套可执行的研究工程任务，让外部执行器在代码、实验和结果分析中继续帮助我们把 idea 做实、做强、做清楚。

它的核心不是泛泛的 multi\-agent 分工，而是一个受控的研究工程闭环：Builder 负责编码、配置、运行和整理结果；Reviewer 负责审查代码是否真正实现方法机制、实验协议是否公平、baseline 是否一致、指标是否正确、结果解释是否过度。更进一步，实验结果不能只是拿来画表，而要反向分析：baseline 的哪些模块强、我们的方法哪些模块有效、失败点在哪里、是否需要调整 method mechanism、ablation plan，甚至收窄或重构 idea。

因此，这一阶段的最终目标不是“得到一个实验结果表”，而是形成一组可被 T7 审计、可被 T8 写作的结构化材料：

```Plain Text
1. 可复现的实验结果
2. 可追溯的 baseline / config / raw log
3. 实验后沉淀出的 realized method package
4. 最终 framework figure 及其 caption draft
5. result diagnosis 与 module attribution
6. claim boundary 与 must-not-claim
7. Writer 可直接使用的 method / experiment / figure handoff
```

---

## 这部分到底做什么？

外部执行器阶段做五件事。

第一，它把 Pre\-T5 的研究内容重新变成“可执行任务”。T4/T4\.5 之前产出的 `hypotheses.md`、`exp_plan.yaml`、`idea_scorecard.yaml`、`novelty_audit.md`、`synthesis.md` 本质上还是研究设计文档。T5 要把这些内容 re\-boost 成外部执行器能直接执行的东西：研究目标是什么、方法机制不能偏离什么、哪些 baseline 必须跑、最小实验闭环是什么、哪些 claim 不能说、哪些结果必须回传给 Writer。

第二，它生成项目专属 skill suite。ResearchOS 可以保存一组通用 skill 模板，但不能把模板原样交给外部执行器。T5 应当读取当前项目的 Pre\-T5 文件，把模板改写成适合当前项目的 `external_executor/skills/`（**改写后的project\-specific skills要放在workspace中**）。外部执行器启动时只需一句话：读取 `external_executor/AGENTS.md`，并执行 `external_executor/skills/research_execution/SKILL.md`。

第三，它让 Codex / Claude Code 不只是跑一次实验，而是做一个受控的研究工程循环：找资源，复现 baseline，设计实验，细化方法，实现方法，review 代码和实验协议，运行实验，诊断结果，分析 baseline 和 ours 的模块作用，再决定是否修正方法、收窄 claim 或继续重跑。

第四，它让实验结果反向喂回研究设计。比如 baseline 表现很好，不只是说明“我们没超过 baseline”，还要分析 baseline 强在哪里；我们的模块 A 有提升但模块 B 没有，说明初始 method intent 中某个机制假设需要修正；某个数据集上有效、另一个数据集上无效，说明 contribution boundary 要收窄。这些都应该进入后续 T7/T8，而不是只留一个结果表。

第五，它在实验完成之后补全最终 Method 写作材料。T5 一开始只生成 `method_intent`，用于约束外部执行器不要跑偏；真正给 T8 Writer 使用的方法概要、模块结构、算法流程、framework 图和 caption，必须在实验完成、结果诊断和模块归因之后生成，也就是 `realized_method_package`。T7 再检查 realized method 是否和代码、消融、实验结果、新颖性边界一致。

---

## 总体流程图

```Plain Text
Pre-T5 Research Artifacts
(project / literature / idea / novelty / experiment plan)
        |
        v
T5-REBOOST-GATE
        |
        |-- Context Re-boosting
        |   把 Pre-T5 重新整理为外部执行器可执行语境
        v
T5-HANDOFF
        |
        |-- Method Intent Drafting
        |   生成“方法意图”，用于约束实现，不是最终 Method
        v
T5-SKILL-CUSTOMIZATION-GATE
        |
        |-- Project-Specific Skill Compilation
        |   直接调用 LLM API，根据当前项目改写 workspace 中的 skill 模板
        |
        v
T5-EXECUTOR-GATE
用户选择 Codex / Claude Code / manual / dry-run
        |
        v
External Execution Loop
        |
        |-- 1. Context alignment check
        |-- 2. Resource & baseline mining
        |-- 3. Baseline reproduction
        |-- 4. Claim-evidence experiment design
        |-- 5. Method refinement before coding
        |-- 6. Code implementation
        |-- 7. Code review + protocol review
        |-- 8. Smoke run / small-scale validation / formal run
        |-- 9. Result diagnosis
        |-- 10. Baseline/module attribution
        |-- 11. Idea/method refinement decision
        |-- 12. Re-run until budget / plateau / audited target / narrowing
        |-- 13. Realized method package generation
        |-- 14. Final framework figure generation
        |-- 15. Figure/table packaging
        |-- 16. Writer handoff
        |
        v
External Result Pack
raw_results / configs / logs / patches / figures / tables / method package
        |
        v
T7-INGEST
把外部结果转成 ResearchOS 内部实验证据
        |
        v
T7-AUDIT / T7-METHOD-AUDIT / T7-POST-NOVELTY / T7-CLAIMS
审计结果、审计方法一致性、检查 novelty 是否变化、生成可写 claim
        |
        v
T8 Writer
只消费审计后的 realized method / result_to_claim / evidence_pack / figures / limitations
```

---

## T5 的核心不是执行，而是“编译”

T5 应该定位为：

```Plain Text
Project-Specific External Execution Compiler
```

它不是亲自跑实验，而是把前面已经生成的研究信息编译成外部执行器能执行的 skill package。T5 的主要输出不是实验结果，而是：

```Plain Text
external_executor/handoff_pack.json
external_executor/AGENTS.md
external_executor/CLAUDE.md
external_executor/allowed_paths.txt
external_executor/expected_outputs_schema.json
external_executor/skills/
```

其中最关键的是：

```Plain Text
handoff_pack.json
external_executor/skills/research_execution/SKILL.md
expected_outputs_schema.json
```

T5 的质量决定外部执行器是否会跑偏。如果 T5 只是把一堆 Pre\-T5 文件路径扔给 Codex / Claude Code，外部执行器很可能会误读研究目标、忽略 novelty audit、跳过 required baseline，或者把临时工程 trick 当成论文方法贡献。因此 T5 必须先进行 context re\-boosting，再编译项目专属 skill。

---

## T5 应读取哪些 Pre\-T5 文件？

T5 固定读取以下文件：

```Plain Text
project.yaml
literature/synthesis.md
literature/synthesis_workbench.json
literature/domain_map.json
literature/comparison_table.csv
ideation/hypotheses.md
ideation/exp_plan.yaml
ideation/idea_scorecard.yaml
ideation/risks.md
novelty/novelty_audit.md
```

如果这些文件不足以支撑执行判断，T5 可以进一步回查原材料，而不是盲目相信上层摘要。

例如：

```Plain Text
如果 synthesis.md 不足以解释某个方法家族或 bridge domain，
则读取 literature/paper_notes/ 和 literature/paper_notes_abstract/。

如果 domain_map.json 只给出结构而没有机制解释，
则读取 paper_notes 中对应论文的 A/B 桥接字段和 mechanism claim。

如果 novelty_audit.md 要求某些 baseline，
但 exp_plan.yaml 没有写清楚实验协议，
则以 novelty_audit.md 为 required baseline 来源，并在 handoff_pack 中标出 mismatch。

如果 user_seeds/bridge_domains.yaml 或 seed_external_resources.jsonl 存在，
则将其作为资源和 bridge-domain 检索提示，而不是最终语义判断。
```

可选读取：

```Plain Text
literature/paper_notes/
literature/paper_notes_abstract/
resources/
user_seeds/seed_external_resources.jsonl
user_seeds/bridge_domains.yaml
```

注意：T5 不应把所有文件内容直接塞给外部执行器。T5 要先做 re\-boosting，把它们重新组织成执行语境。

---

## Context Re\-boosting 怎么做？

Re\-boosting 的目的不是摘要，而是重新排布执行语境。它要把 Pre\-T5 的研究设计转成外部执行器真正需要的信息。

它应该回答：

```Plain Text
1. 当前研究目标是什么？
2. central hypothesis 是什么？
3. 方法机制不能偏离什么？
4. 哪些方法模块只是候选，哪些是核心贡献意图？
5. novelty_audit 里有哪些 required baselines？
6. 哪些 baseline 必须跑，哪些可以作为替代？
7. 哪些实验构成最低闭环？
8. 哪些结果可以支持强 claim？
9. 哪些结果只能支持弱 claim？
10. 哪些 claim 现在不能说？
11. 实验结果如何反向精炼 method / idea？
12. 外部执行器完成后必须给 Writer 什么？
```

建议直接写入现有：

```Plain Text
external_executor/handoff_pack.json
```

不要新增一堆文件。

建议结构：

```JSON
{
  "schema_version": "external_executor_handoff.v1",
  "context_reboost": {
    "project_goal": "...",
    "central_hypothesis": "...",
    "method_mechanism": {
      "core_mechanism": "...",
      "must_preserve_components": [],
      "candidate_components": [],
      "allowed_refinements": [],
      "forbidden_scope_changes": []
    },
    "required_baselines": [],
    "baseline_matrix": [],
    "claim_evidence_matrix": [],
    "minimum_experiment_loop": [],
    "iteration_budget": {
      "max_rounds": 3,
      "stop_conditions": [
        "budget_exhausted",
        "improvement_plateau",
        "required_baseline_unavailable",
        "audited_target_reached",
        "implementation_blocked",
        "claim_must_be_narrowed"
      ]
    },
    "claim_boundaries": [],
    "writer_handoff_contract": [],
    "source_files_used": [],
    "known_context_mismatches": []
  }
}
```

这里最重要的是：

```Plain Text
method_mechanism
baseline_matrix
claim_evidence_matrix
minimum_experiment_loop
claim_boundaries
writer_handoff_contract
```

---

## Method Intent：T5 只生成“方法意图”，不是最终 Method

T5 阶段需要写方法信息，但不能把它当成最终 Method。因为外部执行器在实现、实验、诊断、迭代后，方法很可能发生细化、删减、重组或收窄。

因此，T5 生成的是：

```Plain Text
method_intent
```

而不是：

```Plain Text
final_method_package
```

`method_intent` 的作用是：

```Plain Text
防止外部执行器偏离 idea；
帮助 Codex / Claude Code 把抽象研究设想转成可实现模块；
帮助 Reviewer 检查代码是否对齐初始方法机制；
帮助实验后判断哪些机制被支持、哪些机制被推翻；
帮助 T7 检查 contribution drift。
```

它不是 T8 最终 Method 的事实源。

建议写入 `handoff_pack.json`：

```JSON
{
  "method_intent": {
    "status": "draft_intent_only",
    "not_final_method_source": true,
    "central_mechanism_hypothesis": "...",
    "candidate_modules": [
      {
        "module_id": "M1",
        "name": "...",
        "intended_role": "...",
        "expected_input": "...",
        "expected_output": "...",
        "why_it_may_help": "...",
        "related_claim": "...",
        "planned_ablation": "..."
      }
    ],
    "expected_algorithm_flow": [
      {
        "step": 1,
        "description": "...",
        "related_module": "M1"
      }
    ],
    "allowed_refinements": [],
    "forbidden_silent_changes": [
      "replace_core_mechanism",
      "drop_required_baseline",
      "change_task_or_benchmark",
      "change_contribution_type_without_review"
    ],
    "mechanism_to_ablation_plan": [
      {
        "mechanism": "...",
        "planned_test": "...",
        "expected_observation_if_supported": "...",
        "expected_observation_if_not_supported": "..."
      }
    ],
    "initial_framework_figure_sketch": {
      "status": "draft_intent_only",
      "purpose": "guide implementation, not final paper figure",
      "main_message": "...",
      "candidate_panels": [],
      "candidate_nodes": [],
      "candidate_edges": [],
      "must_not_be_used_directly_by_T8": true
    }
  }
}
```

这里的 `initial_framework_figure_sketch` 只是帮助外部执行器理解预期方法结构，不能直接作为 T8 的最终 framework 图。

---

## Skill 编译：大 skill 套小 skill

T5 可以保存一组通用 skill 模板，但不要直接给外部执行器用。每次 T5 要根据当前项目改写这些模板，生成项目专属 skill。

建议目录：

```Plain Text
external_executor/skills/
  research_execution/SKILL.md
  context_alignment/SKILL.md
  resource_and_baseline_mining/SKILL.md
  baseline_reproduction/SKILL.md
  experiment_design/SKILL.md
  method_refinement/SKILL.md
  implementation/SKILL.md
  code_and_protocol_review/SKILL.md
  experiment_iteration/SKILL.md
  result_diagnosis/SKILL.md
  module_attribution/SKILL.md
  figure_table_packaging/SKILL.md
  writer_handoff/SKILL.md
```

外部执行器启动时不需要用户逐个调用。用户只需要说：

```Plain Text
请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research_execution/SKILL.md。
```

`research_execution/SKILL.md` 是总控 skill，它会调度小 skill。

每个 skill 都应该有清楚边界：

```Plain Text
Use for:
Do not use for:
Reads:
Writes:
Workflow:
Output contract:
Evidence rules:
Stop conditions:
```

可以参考工程 skill 的组织方式：`SKILL.md` 保持短，复杂 schema / checklist / policy 放到 `references/`，可复用脚本放到 `scripts/`，模板放到 `assets/`。这样外部执行器不会因为一个巨长 prompt 而迷失。

---

## multi\-agent 应该怎么理解？

这里不要把 multi\-agent 理解成“很多角色并行写不同部分”。更合理的是：

```Plain Text
Builder-Reviewer Loop
```

也就是：

```Plain Text
Builder:
  负责写代码、改 config、接入 baseline、跑实验、整理结果。

Reviewer:
  负责审查代码是否真的实现了 method intent，
  实验是否公平，
  baseline 是否一致，
  指标是否正确，
  数据 split 是否一致，
  结果解释是否过度，
  是否需要回滚或重跑。
```

如果外部环境支持 subagent，可以让 Reviewer 单独作为 review agent；如果不支持，就在 root skill 中强制执行 self\-review phase。

推荐外部执行循环：

```Plain Text
Build
  写代码 / 改配置 / 接入 baseline
        |
        v
Review
  检查代码、实验协议、配置、指标、数据 split
        |
        v
Run
  执行 baseline / ours / ablation
        |
        v
Diagnose
  分析结果、失败原因、baseline 强项、模块贡献
        |
        v
Refine
  修改方法或精进 claim
        |
        v
Review again
```

实验阶段最容易出错的不是没人分工，而是：

```Plain Text
代码实现偏离方法
baseline 不公平
指标方向弄反
结果解释过度
失败后乱改方法
```

所以 Reviewer 的职责非常关键。

---

## 外部执行器内部完整流程

### Step 1：Context Alignment Check

外部执行器先读：

```Plain Text
external_executor/AGENTS.md
external_executor/handoff_pack.json
external_executor/skills/research_execution/SKILL.md
external_executor/expected_outputs_schema.json
```

然后做对齐检查：

```Plain Text
当前任务目标是否清楚？
central hypothesis 是否清楚？
method intent 是否清楚？
required baselines 是否清楚？
experiment minimum loop 是否清楚？
allowed_paths 是否清楚？
result_pack schema 是否清楚？
```

如果发现 `context_reboost` 和源文件冲突，例如 `novelty_audit.md` 要求 baseline A，但 `handoff_pack.json` 漏掉了，要记录：

```Plain Text
context_mismatch
```

并以源文件和 `novelty_audit.md` 为准。

输出进入：

```JSON
{
  "context_alignment": {
    "status": "pass | mismatch | blocked",
    "source_files_checked": [],
    "mismatches": [],
    "resolution": []
  }
}
```

---

### Step 2：Resource \& Baseline Mining

这一步不是随便搜 GitHub，而是按 `baseline_matrix` 找资源。

每个 baseline 要记录：

```Plain Text
baseline name
why included
official repo
unofficial repo
paper/source
dataset compatibility
metric compatibility
runnability
license
dependency risk
compute cost
status
```

如果找不到 baseline，不能偷偷换一个容易跑的。要写：

```Plain Text
baseline_unavailable_reason
replacement_candidate
claim_risk
```

这一步的输出进入：

```Plain Text
result_pack.resources
result_pack.baseline_candidates
```

---

### Step 3：Baseline Reproduction

先跑 baseline，再写新方法。

这是硬规则。否则很容易出现：

```Plain Text
新方法跑起来了，但 baseline 没复现
无法比较
结果没有说服力
```

baseline reproduction 要记录：

```Plain Text
command
config
dataset split
seed
metric
raw log path
result
failure reason
```

如果 baseline 跑不通，Reviewer 要判断：

```Plain Text
是环境问题？
代码过旧？
数据不可用？
配置不清？
还是 baseline 本身不适合？
```

然后给出：

```Plain Text
reproduce / repair / replace / mark_unavailable
```

如果 baseline 无法复现，必须记录 claim risk。不能因为某个 baseline 难跑，就悄悄换成弱 baseline。

---

### Step 4：Experiment Design From Claims

实验设计要从 claim 出发，而不是从表格出发。

外部执行器不应该只说“跑主实验、消融、鲁棒性”。它应该建立：

```Plain Text
claim -> reviewer question -> evidence -> experiment
```

例如：

```Plain Text
Claim:
  Semantic codebook improves transfer under heterogeneous feature spaces.

Reviewer question:
  Is the improvement from semantic alignment, or just from larger representation capacity?

Evidence needed:
  Compare ours vs no-codebook, random-codebook, shared encoder, HTCE baseline.

Experiment:
  main comparison + mechanism ablation + transfer gap analysis.
```

输出进入：

```Plain Text
result_pack.claim_evidence_matrix
```

每个实验都必须回答一个 reviewer question。消融实验必须测试 method mechanism，而不是随便删模块。

---

### Step 5：Method Refinement Before Coding

外部执行器不要直接开写代码。它先把 `method_intent` 从研究设想改成可实现规格：

```Plain Text
input
output
modules
losses
training loop
inference procedure
config
ablation switch
expected failure mode
```

关键是：方法可以 refine，但不能 silent drift。

允许：

```Plain Text
把 abstract idea 转成具体 module
调整实现细节
增加必要的 training trick
补充 ablation switch
修正不影响核心贡献的工程细节
```

不允许不记录就做：

```Plain Text
换掉核心机制
换任务
换 benchmark
丢 required baseline
改变 contribution type
把失败方法改成另一个 paper 的方法
```

如果需要大改，写：

```Plain Text
scope_change_request
```

由 ResearchOS 或用户决定是否接受。

---

### Step 6：Implementation with Review

这里的 multi\-agent 最应该体现为：

```Plain Text
Implementer + Reviewer
```

Implementer 做：

```Plain Text
写代码
接入 config
写训练脚本
写 evaluation
保存 raw logs
保存 patch summary
```

Reviewer 查：

```Plain Text
代码是否真的实现 method intent
是否破坏 baseline
是否 data leakage
metric 是否方向正确
seed/split 是否一致
ablation switch 是否可用
日志是否足够复现
```

每轮 implementation 后都要有 review 结果：

```Plain Text
review_status = pass | needs_fix | blocked
```

只有 `pass` 才进入正式实验。

---

### Step 7：Experiment Run

实验运行分三层：

```Plain Text
smoke run
small-scale validation
formal run
```

不要一上来 full run。

Smoke run 的目的：

```Plain Text
代码能跑
数据能加载
loss 正常
metric 正常
日志能保存
```

Small\-scale validation 的目的：

```Plain Text
方法方向是否有信号
baseline 是否能对齐
主要模块是否明显崩掉
```

Formal run 的目的：

```Plain Text
正式比较
正式消融
正式鲁棒性/泛化
正式图表
```

所有实验必须保存：

```Plain Text
command
config
seed
dataset split
raw log
metric output
code commit or patch id
```

---

### Step 8：Result Diagnosis

实验结果不是只用来判断赢没赢，而是用来理解 idea。

诊断要回答：

```Plain Text
1. 哪个 baseline 最强？
2. baseline 强在哪里？
3. 我们的方法在哪些场景有效？
4. 哪些场景失败？
5. 是哪个模块带来提升？
6. 是哪个模块没有作用？
7. 提升来自核心机制，还是训练技巧 / 容量 / 数据处理？
8. 是否需要调整 method mechanism？
9. 是否需要缩小 claim？
10. 是否有新的 bridge / theory insight？
```

这一步建议成为独立 skill：

```Plain Text
result_diagnosis/SKILL.md
```

输出进入：

```JSON
{
  "result_diagnosis": {
    "strongest_baseline": "...",
    "baseline_strength_analysis": [],
    "where_ours_wins": [],
    "where_ours_fails": [],
    "likely_active_mechanisms": [],
    "inactive_or_harmful_modules": [],
    "metric_anomalies": [],
    "claim_implications": [],
    "next_iteration_recommendations": []
  }
}
```

---

### Step 9：Module Attribution

这一步比普通 ablation 更进一步。普通 ablation 是：

```Plain Text
w/o module A
w/o module B
```

但 module attribution 要回答：

```Plain Text
模块 A 为什么有效？
模块 A 在哪个数据分布下有效？
它是否只是增加容量？
它是否只改善某个 subset？
它和 baseline 的差异在哪里？
```

建议把 baseline 也拆成模块看：

```Plain Text
baseline encoder
baseline adaptation loss
baseline regularizer
baseline treatment representation
baseline evaluation protocol
```

然后比较：

```Plain Text
ours module vs baseline module
```

这能帮助“不断精进 idea”。

例如：

```Plain Text
如果 baseline 的 domain adversarial loss 很强，而我们的 semantic codebook 只在 sparse feature overlap 下有效，
那 idea 应该从“通用 transfer uplift”收窄成
“semantic-discrete alignment for sparse heterogeneous feature spaces”。
```

输出进入：

```JSON
{
  "module_attribution": {
    "baseline_effective_modules": [],
    "ours_effective_modules": [],
    "ours_weak_modules": [],
    "mechanism_supported": [],
    "mechanism_not_supported": [],
    "idea_refinement": {
      "keep": [],
      "modify": [],
      "drop": [],
      "new_boundary": []
    }
  }
}
```

这就是外部实验反向强化 Pre\-T5 idea 的关键。

---

### Step 10：Idea / Method Refinement Loop

实验结果应该允许反向更新 idea，但不能乱改。

每轮迭代都要产生一个 decision：

```Plain Text
continue_same_idea
minor_method_fix
module_reweight
claim_narrowing
baseline_repair
benchmark_shift_request
scope_change_request
stop_and_report
```

建议循环逻辑：

```Plain Text
if baseline not reproduced:
    repair baseline first
elif ours fails smoke:
    fix implementation
elif ours underperforms all baselines:
    diagnose mechanism; try at most N method refinements
elif one module works:
    refine idea around active mechanism
elif only subset works:
    narrow claim boundary
elif results strong:
    run ablation + robustness + figure packaging
else:
    stop with honest limitation
```

注意：不要写“重跑直至 SOTA”作为硬承诺。应该写：

```Plain Text
在预算内追求更强结果；
如果达到 SOTA，记录；
如果未达到，分析差距并调整 claim。
```

停止条件包括：

```Plain Text
budget_exhausted
improvement_plateau
required_baseline_unavailable
audited_target_reached
implementation_blocked
claim_must_be_narrowed
scope_change_requires_human_review
```

---

### Step 11：Realized Method Package

这是 method 相关内容的关键补充。

T5 只生成 `method_intent`。外部执行器完成实验、诊断和迭代后，必须补上：

```Plain Text
realized_method_package
```

这才是 T8 Writer 的主要 Method 写作素材。

它应该写入 `external_executor/result_pack.json`：

```JSON
{
  "realized_method_package": {
    "final_method_name": "",
    "one_sentence_method": "",
    "actual_core_mechanism": "",
    "implemented_modules": [
      {
        "module_id": "M1",
        "name": "",
        "purpose": "",
        "input": "",
        "output": "",
        "code_paths": [],
        "config_keys": [],
        "supported_by_ablation": true,
        "evidence_refs": []
      }
    ],
    "dropped_modules": [
      {
        "module_id": "",
        "reason": "",
        "effect_on_contribution": ""
      }
    ],
    "added_modules": [
      {
        "module_id": "",
        "reason": "",
        "effect_on_contribution": ""
      }
    ],
    "actual_algorithm_flow": [
      {
        "step": 1,
        "description": "",
        "related_module": "",
        "code_path": ""
      }
    ],
    "actual_losses": [
      {
        "name": "",
        "role": "",
        "implemented": true,
        "code_path": "",
        "ablation_or_diagnostic_ref": ""
      }
    ],
    "module_attribution_summary": [],
    "supported_mechanisms": [],
    "unsupported_mechanisms": [],
    "claim_boundary": [],
    "delta_from_method_intent": [
      {
        "change": "",
        "reason": "",
        "affects_contribution": true,
        "requires_post_novelty_check": true
      }
    ]
  }
}
```

`realized_method_package` 必须基于真实实现和实验后诊断生成，而不是 T5 初始设想。它回答：

```Plain Text
最终方法到底是什么？
哪些模块真的实现了？
哪些模块被删掉了？
哪些模块是后来加的？
哪些机制被实验支持？
哪些机制没有被支持？
最终 claim 应该如何收窄？
```

---

### Step 12：Final Framework Figure Generation

framework 图也不能在 T5 一开始定死。T5 可以有 `initial_framework_figure_sketch`，但最终给 T8 的图应该在实验完成后生成。

Final framework figure 应该来自：

```Plain Text
realized_method_package
actual code structure
module attribution
supported mechanisms
claim boundary
```

而不是来自最初的 method intent。

它必须满足：

```Plain Text
只展示实际实现模块；
图中模块要能对应代码路径；
图中核心模块要能对应消融或诊断证据；
图注要和最终 claim boundary 一致；
不能展示被删掉、未实现或未被支持的模块为核心贡献。
```

建议写入 `result_pack.json`：

```JSON
{
  "final_framework_figure": {
    "figure_type": "method_framework",
    "status": "ready_for_T7_audit",
    "main_message": "",
    "panels": [
      {
        "panel_id": "A",
        "title": "",
        "purpose": "",
        "related_modules": []
      }
    ],
    "nodes": [
      {
        "id": "",
        "label": "",
        "module_id": "",
        "implemented": true,
        "code_refs": []
      }
    ],
    "edges": [
      {
        "from": "",
        "to": "",
        "label": "",
        "meaning": ""
      }
    ],
    "visual_emphasis": [],
    "caption_draft": "",
    "editable_source": "external_executor/figures/framework.drawio",
    "rendered_files": [
      "external_executor/figures/framework.svg",
      "external_executor/figures/framework.pdf"
    ],
    "must_not_show": [],
    "evidence_mapping": [
      {
        "figure_element": "",
        "supported_by": "code | ablation | diagnostic | method_definition",
        "source_ref": ""
      }
    ]
  }
}
```

注意区分两类图：

```Plain Text
1. Method framework figure
   来源：realized method package / code structure / module attribution
   用途：Method section
   不需要实验数字

2. Result figure / table
   来源：raw_results / audited metrics
   用途：Experiments section
   必须绑定 source log / config / metric output
```

---

### Step 13：Figure / Table Packaging

图表不是最后随便画，而应该从 claim\-evidence matrix 来。

每张图/表都要有：

```Plain Text
figure_id
claim_supported
source_result
source_config
source_log
plot_script
caption_draft
evidence_level
```

分类：

```Plain Text
main comparison table
ablation table
robustness table
efficiency table
failure case figure
method framework figure
diagnostic figure
```

对于 result figure，必须追溯到 raw results。对于 framework figure，必须追溯到 realized method package 和 code/module mapping。

---

### Step 14：Writer Handoff

外部执行器最后不是写论文，而是给 Writer 交接审计前材料。

写入 `result_pack.writer_handoff`：

```JSON
{
  "writer_handoff": {
    "method_summary": "...",
    "implementation_summary": "...",
    "realized_method_package_ref": "...",
    "framework_figure_ref": "...",
    "main_results": [],
    "ablation_results": [],
    "diagnostic_findings": [],
    "figures": [],
    "tables": [],
    "claim_candidates": [],
    "must_not_claim": [],
    "limitations": [],
    "open_risks": [],
    "recommended_storyline_update": []
  }
}
```

然后 ResearchOS 的 T7 继续：

```Plain Text
T7-INGEST
T7-AUDIT
T7-METHOD-AUDIT
T7-POST-NOVELTY
T7-CLAIMS
```

T8 只能用 T7 审计后的东西写。

---

## Artifact Schema 设计

外部执行器阶段必须有统一 artifact schema，否则后续 T7/T8 很难稳定消费。

### 10\.1 schema version

所有核心 JSON 都必须有版本号：

```JSON
{
  "schema_version": "external_executor_result.v1"
}
```

建议核心文件：

```Plain Text
external_executor/handoff_pack.json
external_executor/expected_outputs_schema.json
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/run_manifest.json
```

### 10\.2 required / optional 规则

`result_pack.json` 最低 required 字段：

```Plain Text
schema_version
executor_status
context_alignment
resources
baseline_reproduction
experiment_runs
result_diagnosis
module_attribution
realized_method_package
final_framework_figure
figure_table_inventory
writer_handoff
```

optional 字段：

```Plain Text
scope_change_requests
failed_trials
replacement_baselines
additional_resources
manual_notes
```

### 10\.3 枚举字段

常用枚举：

```Plain Text
executor_status:
  completed | partial | blocked | failed

review_status:
  pass | needs_fix | blocked

run_type:
  smoke | small_scale | formal | ablation | robustness | diagnostic

evidence_level:
  raw_result | audited_result | diagnostic_hint | method_definition | abstract_only | unsupported

claim_strength:
  strong | moderate | weak | unsupported

contribution_drift:
  none | minor | major

required_action:
  none | update_method | rerun_experiment | rerun_novelty | human_review | narrow_claim
```

### 10\.4 兼容策略

schema 兼容规则：

```Plain Text
新增字段默认 optional；
删除字段必须升级 major version；
字段语义变化必须升级 version；
T7 读取未知字段时忽略，但保留到 evidence index；
required 字段缺失时 T7 不得继续进入 T8。
```

### 10\.5 错误处理规则

如果外部执行器输出不完整：

```Plain Text
result_pack 缺 required 字段 -> T7-INGEST failed
baseline 未复现 -> T7-AUDIT 标记 claim risk
method package 缺失 -> T8 不得写 Method
framework 图缺失 -> T8 可写 Method 但不得引用 framework figure
raw log 缺失 -> 对应 result 不得进入 result_to_claim
scope change 未审计 -> T7-POST-NOVELTY 必须 human_review
```

---

## T7 如何接外部执行器结果？

之前的 T7 仍然需要，但它不能只是“摄取外部结果”。它应该升级为外部执行结果的审计和证据闭合层。

建议将 T7 拆成四个内部子阶段：

```Plain Text
T7-INGEST
T7-AUDIT
T7-METHOD-AUDIT / T7-POST-NOVELTY
T7-CLAIMS
```

不一定要在状态机上拆成四个大节点，也可以作为 T7 内部步骤。但概念上必须区分。

---

### 11\.1 T7\-INGEST

T7\-INGEST 负责把外部执行器输出转成 ResearchOS 内部可用证据。

读取：

```Plain Text
external_executor/result_pack.json
external_executor/run_manifest.json
external_executor/raw_results/
external_executor/configs/
external_executor/logs/
external_executor/patches/
external_executor/figures/
external_executor/tables/
```

输出：

```Plain Text
experiments/results_summary.json
experiments/run_records.jsonl
experiments/evidence_index.json
```

它不判断 claim 是否成立，只做标准化和索引。

---

### 11\.2 T7\-AUDIT

T7\-AUDIT 负责实验结果审计。

检查：

```Plain Text
baseline 是否完整
metric 是否方向正确
raw log 是否存在
config 是否可复现
seed / split 是否一致
是否存在 mock / dry-run 冒充 formal result
是否 cherry-pick
result figure 是否有 source table / raw result
```

输出：

```Plain Text
experiments/result_audit.json
```

没有通过审计的结果不能进入 T8 的 claim。

---

### 11\.3 T7\-METHOD\-AUDIT / T7\-POST\-NOVELTY

这是新增重点。

T7 不仅要审计实验结果，还要审计方法是否发生了变化。

它检查：

```Plain Text
method_intent vs realized_method_package
realized_method_package vs code paths
final_framework_figure vs implemented modules
ablation_mapping vs actual modules
module_attribution vs claim boundary
realized method 是否变成已有 baseline 的变体
是否发生 contribution drift
```

输出：

```JSON
{
  "method_consistency_audit": {
    "method_intent_matches_realized_method": true,
    "realized_method_matches_code": true,
    "framework_figure_matches_code": true,
    "ablation_matches_modules": true,
    "contribution_drift": "none | minor | major",
    "requires_post_novelty_check": false,
    "required_action": "none | update_method | rerun_novelty | human_review | narrow_claim"
  }
}
```

如果 realized method 和原本 method intent 发生重大变化，则必须触发：

```Plain Text
rerun_novelty
human_review
claim_narrowing
```

不能直接让 T8 写。

---

### 11\.4 T7\-CLAIMS

T7\-CLAIMS 负责把审计后的实验和方法材料转成 Writer 可用的 claim 证据。

输出：

```Plain Text
drafts/result_to_claim.json
drafts/experiment_evidence_pack.json
drafts/method_writing_resources.json
drafts/must_not_claim.md
drafts/claim_support_matrix.csv
```

其中 `method_writing_resources.json` 应包含：

```JSON
{
  "method_writing_resources": {
    "method_overview": "",
    "realized_method_package": {},
    "module_graph": [],
    "algorithm_flow": [],
    "final_framework_figure": {},
    "caption_draft": "",
    "symbol_table": [],
    "ablation_mapping": [],
    "implementation_notes": [],
    "method_consistency_audit": {},
    "do_not_claim": []
  }
}
```

T8 Writer 只能消费这些审计后的材料，而不能直接相信外部执行器自然语言总结。

---

## T8 如何消费外部执行器结果？

T8 的输入优先级应该是：

```Plain Text
1. drafts/method_writing_resources.json
2. drafts/result_to_claim.json
3. drafts/experiment_evidence_pack.json
4. drafts/claim_support_matrix.csv
5. drafts/must_not_claim.md
6. external_executor/figures/ 中通过 T7 审计的图
7. T5 method_intent 仅作为历史背景，不作为最终 Method 事实源
```

如果 T5 method intent 和 realized method 冲突：

```Plain Text
以 realized_method_package + T7 method_consistency_audit 为准。
```

如果 framework 图展示了未实现模块：

```Plain Text
T8 不得使用该图。
```

如果某个 result 没有 raw log / config / audit 通过：

```Plain Text
T8 不得写成实验结论。
```

如果某个机制只在 method intent 中出现，但 realized method 没实现或实验不支持：

```Plain Text
T8 不得把它写成方法贡献。
```

---

## 接入 ResearchOS 全系统的流程

### 13\.1 Pre\-T5 负责什么？

Pre\-T5 包括 T1\-T4\.5，主要形成研究设计。

```Plain Text
T1: 项目初始化、用户种子、任务边界
T2: 文献检索、bridge domain、domain map、deep_read_queue
T3: 精读论文、abstract-level 补读、comparison table、bib
T3.5: synthesis workbench、理论/相邻领域桥接
T4: idea optimizer，形成 hypotheses / exp_plan / idea_scorecard
T4.5: novelty audit，生成 required baselines / risks / claim boundary
```

Pre\-T5 的产物是研究设计，不是可执行工程任务。

---

### 13\.2 T5 现在是什么？

T5 应该被定义为：

```Plain Text
External Execution Compiler
```

它包含三个内部动作：

```Plain Text
T5.1 Context Re-boosting
T5.2 Method Intent Drafting
T5.3 Project-Specific Skill Compilation
```

当前实现中，这三个动作分别落在 `T5-REBOOST-GATE`、`T5-HANDOFF` 和 `T5-SKILL-CUSTOMIZATION-GATE`：re-boost 与 skill compilation 都由 ResearchOS 直接调用当前配置的 LLM API 完成，不需要用户手动拉起 Codex。

T5 的输出：

```Plain Text
external_executor/handoff_pack.json
external_executor/AGENTS.md
external_executor/CLAUDE.md
external_executor/allowed_paths.txt
external_executor/expected_outputs_schema.json
external_executor/skills/
```

T5 不跑实验，不写最终 Method，不生成最终 framework 图。它只负责编译外部执行任务和方法意图。

---

### 13\.3 是否需要 T6？

可以不单独设置 T6。外部执行器执行阶段可以作为 T5 的外部等待状态：

```Plain Text
T5-REBOOST-GATE
T5-HANDOFF
T5-SKILL-CUSTOMIZATION-GATE
T5-EXECUTOR-GATE
T5-EXTERNAL-WAIT
T5-EXTERNAL-RESULT-RECEIVED
```

如果系统想保持状态机更清楚，也可以把外部执行阶段叫：

```Plain Text
T6-EXTERNAL-EXECUTION
```

但从 ResearchOS 内部看，T6 不一定是内部 agent。它更像一个外部执行窗口：

```Plain Text
external executor runs outside ResearchOS;
ResearchOS waits for result_pack;
then T7 ingests and audits.
```

为了减少状态复杂度，建议先不新增 T6，把它作为 T5 的 external substate。

---

### 13\.4 之前的 T7 是否需要？

需要，而且更重要。

但 T7 不应该只是“读取外部结果并整理”。它应该升级为：

```Plain Text
External Evidence Closure
```

也就是外部结果进入论文写作前的证据闭合层。

T7 应包含：

```Plain Text
T7-INGEST:
  标准化 result_pack、raw_results、configs、logs、figures。

T7-AUDIT:
  审计实验公平性、metric provenance、baseline coverage、mock/dry-run。

T7-METHOD-AUDIT:
  审计 realized method 是否和 method intent、代码、图、消融一致。

T7-POST-NOVELTY:
  如果实际方法发生变化，检查 novelty 是否变化，必要时回到 T4.5 或 human review。

T7-CLAIMS:
  生成 result_to_claim、method_writing_resources、must_not_claim、claim_support_matrix。
```

因此，T7 不删，反而要强化。

---

### 13\.5 T8 改成什么？

T8 仍然是 Writer，但要改成 evidence\-bound writer。

T8 不直接读外部执行器的自然语言总结，不直接读 T5 method intent 写最终 Method。T8 只读：

```Plain Text
T7 审计后的 method_writing_resources
T7 审计后的 result_to_claim
T7 审计后的 experiment_evidence_pack
T7 审计后的 figure/table inventory
T7 生成的 must_not_claim
```

T8 写作时必须遵守：

```Plain Text
没有审计结果，不写强 claim；
没有 raw log，不写实验数字；
没有 realized method，不写最终 Method；
framework 图未通过 method audit，不使用；
abstract-only / diagnostic-only 内容不能写成强证据。
```

---

## 外部执行器阶段最终状态图

```Plain Text
T5-REBOOST-GATE
  |
  |-- read Pre-T5
  |-- context reboost
  v
T5-HANDOFF
  |
  |-- method intent drafting
  v
T5-SKILL-CUSTOMIZATION-GATE
  |
  |-- compile project-specific skills with LLM API
  v
T5-EXECUTOR-GATE
  |
  v
T5-EXTERNAL-WAIT / External Executor
  |
  |-- Context Alignment
  |-- Resource/Baseline Mining
  |-- Baseline Reproduction
  |-- Claim-Evidence Experiment Design
  |-- Method Refinement
  |-- Implementation
  |-- Code + Protocol Review
  |-- Smoke Run
  |-- Formal Run
  |-- Result Diagnosis
  |-- Module Attribution
  |-- Idea/Method Refinement Decision
  |-- Iterate if needed
  |-- Realized Method Package
  |-- Final Framework Figure
  |-- Figure/Table Packaging
  |-- Writer Handoff
  v
T7-INGEST
  |
  |-- normalize result_pack
  |-- index raw logs/configs/figures
  v
T7-AUDIT
  |
  |-- check fairness
  |-- check metric provenance
  |-- check baseline coverage
  |-- check mock/dry-run
  v
T7-METHOD-AUDIT / T7-POST-NOVELTY
  |
  |-- check method_intent vs realized_method
  |-- check realized_method vs code
  |-- check final framework figure vs implemented modules
  |-- check whether implementation changed contribution
  |-- check whether baseline result changes novelty
  v
T7-CLAIMS
  |
  |-- result_to_claim
  |-- experiment_evidence_pack
  |-- method_writing_resources
  |-- must_not_claim
  |-- claim_support_matrix
  v
T8 Writer
```

---

## 相对旧设计的关键变化

旧设计是：

```Plain Text
T5 生成 handoff prompt
外部执行器跑实验
T7 摄取结果
T8 写作
```

新设计是：

```Plain Text
T5 编译项目专属 skill
T5 生成 method intent，而不是最终 Method
外部执行器执行受控 Builder-Reviewer 研发闭环
实验结果反向诊断 idea 和 method
外部执行器在实验后生成 realized method package 和 final framework figure
T7 审计实验、方法一致性和 novelty drift
T7 生成 writer 可用的 evidence-bound resources
T8 基于审计证据写作
```

区别在于：外部执行器不再只是“实验工人”，而是一个受控的研究工程迭代器。

但它也不是自由研究员。它不能随便改变 idea，不能跳过 baseline，不能编造结果，不能直接写论文 claim，不能直接替 T8 写最终 Method。

---

## 给 Codex 的核心开发指令

请重新设计 ResearchOS 外部执行器阶段。目标不是简单生成一个 Codex / Claude prompt，而是让 T5 把 Pre\-T5 文件编译成项目专属 external execution skill suite，并让外部执行器在受控的 build\-review\-run\-diagnose\-refine 循环中完成资源抓取、baseline 复现、方法实现、实验迭代、结果诊断、模块归因、最终方法沉淀、framework 图生成、图表整理和 writer handoff。

核心设计如下。

### 16\.1 T5\-REBOOST\-GATE 做 context reboost

读取：

```Plain Text
project.yaml
literature/synthesis.md
literature/synthesis_workbench.json
literature/domain_map.json
literature/comparison_table.csv
ideation/hypotheses.md
ideation/exp_plan.yaml
ideation/idea_scorecard.yaml
ideation/risks.md
novelty/novelty_audit.md
```

必要时回查：

```Plain Text
literature/paper_notes/
literature/paper_notes_abstract/
resources/
user_seeds/seed_external_resources.jsonl
user_seeds/bridge_domains.yaml
```

在 `handoff_pack.json` 中生成 `context_reboost`，并写入 `reboost_report.json`。随后 `T5-HANDOFF` 保留该 reboost 输出并继续补全：

```Plain Text
context_reboost
method_intent
baseline_matrix
claim_evidence_matrix
minimum_experiment_loop
writer_handoff_contract
```

### 16\.2 T5 生成 method\_intent，但不生成最终 Method

`method_intent` 必须标记：

```Plain Text
status = draft_intent_only
not_final_method_source = true
```

T8 不得直接使用 `method_intent` 写最终 Method。

### 16\.3 T5-SKILL-CUSTOMIZATION-GATE 编译 project-specific skills

`T5-HANDOFF` 先把仓库中的 13 个通用模板复制到 workspace 的 `external_executor/skills/`，然后 `T5-SKILL-CUSTOMIZATION-GATE` 直接调用 ResearchOS 当前配置的 LLM API，读取 `external_executor/skills/skills_customization/SKILL.md`、`template_manifest.json` 和 handoff pack，把这些副本原地改写成项目专属 skills，并写出 `external_executor/skills/customization_report.json`。这一步不再要求用户手动启动 Codex。

输出到：

```Plain Text
external_executor/skills/
  research_execution
  context_alignment
  resource_and_baseline_mining
  baseline_reproduction
  experiment_design
  method_refinement
  implementation
  code_and_protocol_review
  experiment_iteration
  result_diagnosis
  module_attribution
  figure_table_packaging
  writer_handoff
```

### 16\.4 multi\-agent 采用 Builder\-Reviewer 质量闭环

Builder 负责编码、配置、运行。

Reviewer 负责检查：

```Plain Text
代码是否对齐 method_intent
baseline 是否公平
metric 是否正确
数据 split 是否一致
结果解释是否过度
是否需要回滚或重跑
```

如果外部环境支持 subagent，可分离执行；否则在 root skill 中顺序执行 review phase。

### 16\.5 外部执行器必须先复现 baseline

不能未复现 baseline 就宣称方法有效。

baseline 失败必须记录：

```Plain Text
failure reason
replacement candidate
claim risk
```

### 16\.6 实验设计从 claim 出发

建立：

```Plain Text
claim_evidence_matrix
```

每个实验必须回答一个 reviewer question。ablation 必须测试 method mechanism，而不是随便删模块。

### 16\.7 实验迭代必须有诊断

每轮实验后输出：

```Plain Text
strongest_baseline
baseline_strength_analysis
where_ours_wins
where_ours_fails
active_mechanisms
inactive_or_harmful_modules
next_iteration_recommendations
claim_implications
```

### 16\.8 增加 module\_attribution

分析：

```Plain Text
baseline 哪些模块有效
ours 哪些模块有效
原 method intent 哪些机制被支持
哪些机制不被支持
idea_refinement: keep / modify / drop / narrow_boundary
```

### 16\.9 实验后生成 realized\_method\_package

外部执行器完成实验、诊断、迭代后，必须在 `result_pack.json` 中生成：

```Plain Text
realized_method_package
```

它包含：

```Plain Text
final_method_name
one_sentence_method
actual_core_mechanism
implemented_modules
dropped_modules
added_modules
actual_algorithm_flow
actual_losses
module_attribution_summary
supported_mechanisms
unsupported_mechanisms
claim_boundary
delta_from_method_intent
```

### 16\.10 实验后生成 final\_framework\_figure

final framework figure 必须来自：

```Plain Text
realized_method_package
actual code structure
module attribution
supported mechanisms
claim boundary
```

它必须：

```Plain Text
只展示实际实现模块
对应代码路径
对应消融/诊断证据
不展示被删掉、未实现或未被支持的模块为核心贡献
```

### 16\.11 外部执行器可以优化方法，但不能 silent drift

小修允许。以下变化必须写 `scope_change_request`：

```Plain Text
改核心 hypothesis
丢 required baseline
换 benchmark
改变 contribution type
把方法改成另一个已有 baseline 的变体
```

### 16\.12 停止条件

停止条件包括：

```Plain Text
budget_exhausted
improvement_plateau
required_baseline_unavailable
audited_target_reached
implementation_blocked
claim_must_be_narrowed
scope_change_requires_human_review
```

### 16\.13 T7 强化为 evidence closure

T7 不删除。T7 升级为：

```Plain Text
T7-INGEST
T7-AUDIT
T7-METHOD-AUDIT
T7-POST-NOVELTY
T7-CLAIMS
```

### 16\.14 T8 只能消费 T7 审计后的资源

T8 不得直接使用外部执行器自然语言总结，也不得直接使用 T5 method\_intent 写最终 Method。

T8 只能消费：

```Plain Text
drafts/method_writing_resources.json
drafts/result_to_claim.json
drafts/experiment_evidence_pack.json
drafts/claim_support_matrix.csv
drafts/must_not_claim.md
```

---

## 验收标准

### T5 验收

```Plain Text
T5 后 external_executor/skills/ 有项目特化 skill suite。
handoff_pack.json 包含 context_reboost。
handoff_pack.json 包含 method_intent，且明确标记 draft_intent_only。
handoff_pack.json 包含 baseline_matrix 和 claim_evidence_matrix。
external_executor/AGENTS.md 可以用一句话启动 root skill。
expected_outputs_schema.json 定义 result_pack required 字段。
```

### 外部执行器验收

```Plain Text
result_pack.json 包含 context_alignment。
result_pack.json 包含 resources 和 baseline_reproduction。
result_pack.json 包含 experiment_runs 和 raw result references。
result_pack.json 包含 result_diagnosis。
result_pack.json 包含 module_attribution。
result_pack.json 包含 realized_method_package。
result_pack.json 包含 final_framework_figure。
result_pack.json 包含 figure_table_inventory。
result_pack.json 包含 writer_handoff。
```

### T7 验收

```Plain Text
T7 能基于 result_pack 生成 results_summary。
T7 能基于 raw logs/configs 生成 evidence_index。
T7 能审计 baseline fairness、metric provenance、mock/dry-run。
T7 能审计 method_intent vs realized_method_package。
T7 能审计 final framework figure 是否和实现一致。
T7 能判断 contribution_drift。
T7 能生成 result_to_claim、method_writing_resources、must_not_claim、claim_support_matrix。
```

### T8 验收

```Plain Text
T8 不直接使用 method_intent 写最终 Method。
T8 不直接相信外部执行器自然语言总结。
T8 使用 realized_method_package 写 Method。
T8 使用通过审计的 final_framework_figure 写图注。
T8 使用 result_to_claim 写实验结论。
T8 遵守 must_not_claim。
```

---

## 最终结论

外部执行器阶段不是“跑实验”，而是：

```Plain Text
受控地把 idea 通过 baseline、代码、实验、诊断和迭代做实，
并把实验反馈反向精炼 idea 和 method。
```

最关键的设计不是泛泛的 multi\-agent 分工，而是：

```Plain Text
Builder-Reviewer 质量闭环
+
Result Diagnosis
+
Module Attribution
+
Idea / Method Refinement Loop
+
Realized Method Package
+
Final Framework Figure
+
T7 Evidence Closure
```

这样外部执行器才不只是得到一个结果表，而是真的帮助 ResearchOS 判断：

```Plain Text
这个 idea 哪部分成立？
哪部分只是工程噪声？
baseline 为什么强？
我们的方法机制是否被支持？
最终方法应该怎么写？
framework 图应该展示什么？
claim 应该增强、收窄，还是转向？
```

最终，T5 负责“编译执行任务和方法意图”，外部执行器负责“实验和工程闭环”，T7 负责“结果与方法审计”，T8 负责“基于审计证据写作”。这才是 ResearchOS 外部执行器阶段应承担的研究价值。
