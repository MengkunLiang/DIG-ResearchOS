# module-attribution 文件职责清单

## 根文件

| 文件 | 作用 |
| --- | --- |
| `SKILL.md` | Phase E2 的主执行说明，定义路径、前置条件、workflow、Gate、写入边界和返回契约。 |
| `MANIFEST.md` | 本文件，说明目录结构、各文件职责和推荐运行顺序。 |

## references/

| 文件 | 作用 |
| --- | --- |
| `attribution-policy.md` | 定义归因 Skill 的所有权、非目标、恢复和 staleness 规则。 |
| `evidence-hierarchy.md` | 定义 `direct_ablation`、`controlled_diagnostic`、`correlational_hint`、`implementation_fact`、`unsupported` 五级证据。 |
| `module-and-mechanism-contract.md` | 定义 ours/baseline 模块注册表、机制映射、复合模块与多功能开关规则。 |
| `ablation-and-intervention-analysis.md` | 定义可比性键、方向校正效应、paired ablation 汇总和 broken-ablation 判断。 |
| `interaction-and-confounding.md` | 定义完整 factorial interaction、difference-in-differences 和混杂因素清单。 |
| `confidence-and-causality.md` | 定义 confidence、局部 intervention effect、机制一致性和禁止的因果升级。 |
| `recommendation-and-boundary.md` | 定义 `keep/modify/drop/narrow/collect_evidence` 的本地建议语义及 root review 条件。 |
| `output-contract.md` | 定义 report envelope、module/mechanism attribution 项、Gate 和 narrow apply 映射。 |

## scripts/

| 文件 | 作用 |
| --- | --- |
| `_common.py` | 标准库公共函数：workspace/path、安全写入、fingerprint、稳定 ID、当前 iteration/diagnosis 解析、evidence ID 收集。 |
| `preflight_attribution.py` | 校验当前 diagnosis、iteration、模块身份、运行记录、schema 和写入权限。 |
| `build_attribution_snapshot.py` | 固定本轮 diagnosis、模块、机制、intervention run 和 metric 的完整证据表面。 |
| `inventory_modules.py` | 合并 method intent、implementation mapping 和 baseline module mapping，形成稳定模块注册表。 |
| `normalize_attribution_evidence.py` | 将运行记录转成规范化 intervention observations，并标注证据类型。 |
| `compute_ablation_effects.py` | 在严格 comparability key 和其他模块状态条件下，计算 paired module effects。 |
| `analyze_interactions.py` | 只在完整四格 factorial 对照存在时计算 pairwise interaction，并发现重复数、稳定性、容量/计算和复合开关混杂。 |
| `build_attribution_facts.py` | 将模块 effect、interaction 和 registry 编译为确定性 module/mechanism facts。 |
| `initialize_attribution_report.py` | 从 facts 初始化可由分析 Agent 补充的 attribution report。 |
| `compute_attribution_gate.py` | 根据证据、confidence 和 confound 计算 `ready_for_iteration_decision/partial/blocked`。 |
| `validate_attribution_report.py` | 校验 schema、evidence refs、因果边界、建议枚举和禁止的 root authority 字段。 |
| `apply_attribution_report.py` | 原子更新且只更新 `result_pack.module_attributions`。 |

## tests/

| 文件 | 覆盖内容 |
| --- | --- |
| `test_module_attribution_scripts.py` | preflight、snapshot、模块 inventory、paired effects、factorial interaction、facts、Gate、evidence validation、因果升级拒绝和 narrow apply。 |

## 推荐运行顺序

```text
preflight_attribution.py
→ build_attribution_snapshot.py
→ inventory_modules.py
→ normalize_attribution_evidence.py
→ compute_ablation_effects.py
→ analyze_interactions.py
→ build_attribution_facts.py
→ initialize_attribution_report.py
→ 完成人工/LLM evidence-bound interpretation
→ compute_attribution_gate.py
→ validate_attribution_report.py
→ apply_attribution_report.py
→ 返回 research-execution
```

## 项目特化入口

T5 在编译项目专属 Skill 时，应优先注入：

- ours 和 baseline 的稳定 module IDs；
- `mechanism_to_ablation_plan`；
- 每个 ablation/diagnostic switch 的精确语义；
- 参数量、计算、数据、预训练和 tuning-budget 控制；
- setting/subset 维度；
- practical threshold 或 neutral band；
- central mechanism 的 falsifying observation；
- 哪些建议会构成 major scope change。
