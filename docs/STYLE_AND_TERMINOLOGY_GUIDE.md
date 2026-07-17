# ResearchOS 文档写作与术语指南

状态：current
适用范围：当前 ResearchOS 代码、用户界面、CLI 帮助、Skill 说明和维护文档
最后核验：以 `researchos` CLI、状态机、Artifact Contract 与各 Workspace 的已保存产物为准

本文规定面向用户的文档如何说明 ResearchOS。目标不是把内部字段逐词翻译成中文，而是让研究者先理解系统要解决什么问题、当前处于什么阶段、下一步可以做什么，以及失败后如何恢复。开发者仍可在 Trace、Schema、调试文档和原始 Artifact 中使用精确的内部名称。

## 写作原则

先说明目的和读者收益，再说明实现边界。段落应有明确主语，并把原因、动作和结果连成自然顺序。不要用大量短句、标签式句子或堆叠的名词代替解释。

文档必须区分以下四类陈述。

| 类型 | 写法 | 示例 |
| --- | --- | --- |
| 已实现事实 | 说明代码和持久化产物当前能做什么 | `T3.6-VISUALS` 在模型调用前检查 Literature Manifest；没有可读论文卡时会阻塞。 |
| 证据边界 | 说明当前材料只能支持什么 | 摘要级笔记可以说明覆盖范围或研究趋势，不能单独支撑机制或因果结论。 |
| 用户决策 | 说明系统为何请求确认及其后果 | 推进 Candidate 会创建可回滚的新研究选择；查看 Candidate 只读取已保存信息。 |
| 计划或实验性能力 | 明确尚未保证的行为 | 未经真实端到端验证的 Docker 路径应标为待验证，不得写成已支持。 |

不要使用“赋能”“打通”“全链路”“一站式”“多维度协同”等无法核验的宣传语。不要把设计目标、模型建议或候选假设写成既成研究结论。

## 面向用户的表达

Normal UI 和用户指南应优先使用研究者可直接行动的说法。

| 不宜直接暴露的内部说法 | 面向用户的推荐说法 |
| --- | --- |
| `active pointer`、`pending_gate` | 已保存的当前步骤；等待你确认的决策 |
| `Population snapshot` | 当前候选池的已保存版本 |
| `Gene Donor Map` | 组合时保留或借用的机制部分，仅在详情中解释 |
| `Pre-Novelty brief` | 新颖性审查前的研究方案摘要 |
| `Artifact` | 首次出现时写作“Artifact，即可恢复的持久化研究产物”；后文可保留 Artifact |
| `Validator` | Validator，或“产物验证器” |
| `Runtime` | Runtime，首次说明为“负责推进任务、保存状态和恢复的运行时” |
| `Workspace` | Workspace，首次说明为“一个研究项目的独立工作目录” |

用户页应解释“推进”“优化”“再探索”“查看”和“暂停”的区别，说明哪些操作只读，哪些操作会调用模型或创建新版本。不要要求用户先理解 state machine、lineage 或 fingerprint 才能继续研究。

## 术语表

| 英文 | 推荐中文或用法 | 使用说明 |
| --- | --- | --- |
| Survey | 文献综述、领域综述、技术综述 | 按研究问题和读者选择，不写“基于分类法的调查”。 |
| Taxonomy | 分类框架、分类体系 | 首次出现时说明按什么维度区分研究。 |
| Taxonomy-based Survey | 基于分类框架组织的领域综述 | 强调它不是逐篇论文摘要。 |
| Literature Survey | 文献综述 | 与领域综述的区别由范围而非名称决定。 |
| Systematic Review | 系统性综述 | 仅在检索、纳入和排除过程满足相应要求时使用。 |
| Research Landscape | 研究版图、领域全景 | 用于概括研究流派、关系和空白。 |
| Contribution Space | 贡献空间 | 说明已有工作覆盖与尚未覆盖的贡献类型。 |
| Artifact | Artifact | 首次解释为可恢复的持久化研究产物。 |
| Validator | Validator、产物验证器 | 指检查结构、来源、状态或证据边界的确定性组件。 |
| Runtime | Runtime、运行时 | 指推进任务、保存状态、恢复与记录事件的组件。 |
| Workspace | Workspace、研究工作区 | 每个项目的隔离目录；同一时间只允许一个写入者。 |
| Resume | Resume | 首次说明为从已保存状态和 Artifact 继续，而非重跑已完成步骤。 |
| Rollback | 回滚 | 回到已保存版本，不删除后续 Artifact。 |
| Gate | 人工决策 Gate | 系统需要研究者确认方向、范围或执行后果的位置。 |
| Candidate | Candidate、候选方案 | T4 中可查看、比较、推进或优化的研究方向。 |
| Population | Candidate Population、候选池 | 候选方案的当前集合，不写“候选人池”。 |
| Route | Idea 生成 Route、生成路径 | T4 用于说明候选如何形成，不替代研究解释。 |
| Skill | Skill | 使用仓库中注册的 Skill 名称。 |
| Cross-domain | Cross-domain、跨领域 | 说明相邻领域材料与主线问题之间的可迁移关系。 |
| Bridge Note | Cross-domain Bridge Note、跨域桥接笔记 | 论文级阅读笔记，位于 `literature/bridge_notes/`。 |
| Cross-domain Catalog | Cross-domain 目录、跨领域检索与上下文目录 | 位于 `literature/cross_domain_catalogs/`；它不是论文级阅读笔记。 |
| Evidence Bundle | Evidence Bundle、证据包 | 说明一组可追溯输入及其证据权限。 |
| Claim | 研究主张 | 区分背景、机制、结果、边界和未来议程。 |
| Novelty Review | 新颖性审查 | T4.5 对选中方案进行的文献碰撞和重复性审查。 |
| Collision Review | 文献碰撞检查、重复性审查 | 检查主张与近期工作是否过近。 |
| Lineage | 演化谱系 | 只在详情或开发语境中解释版本来源。 |
| Boundary Condition | 边界条件 | 说明结论在什么条件下可能不成立。 |
| Mechanism Claim | 机制主张 | 说明变量或设计如何产生预期变化，不能由摘要级材料单独支撑。 |
| Design Rationale | 设计依据 | 解释为什么采用该设计，而不只是描述如何实现。 |
| Evaluation Protocol | 评测协议 | 说明数据、比较对象、指标和验证边界。 |
| Research Gap | 研究缺口 | 必须基于已核验的文献比较，不能只由主题直觉推出。 |

## Literature Artifact 命名

以下名称是当前 Workspace 的 canonical roots。

| 用途 | 相对路径 | 可以支持的内容 |
| --- | --- | --- |
| 精读或可定位的部分全文笔记 | `literature/deep_read_notes/` | 在笔记记录的范围内支持方法、机制、实现和结果讨论。 |
| 摘要级或轻读笔记 | `literature/shallow_read_notes/` | 支持范围、趋势、候选发现和阅读升级建议。 |
| 跨域桥接论文笔记 | `literature/bridge_notes/` | 与精读笔记相同，且记录跨领域来源；它是论文级材料。 |
| 跨领域检索和上下文目录 | `literature/cross_domain_catalogs/` | 支持检索上下文、结构类比和阅读线索；不能代替论文笔记支撑机制或结果。 |
| 跨任务文献清单 | `literature/literature_manifest.json` | 记录当前可发现、可读和可追溯的论文卡，是 T3.5、T3.6、T4、T4.5、T5 和 T8 的共同事实入口。 |

`bridge_notes` 与 `cross_domain_catalogs` 语义不同，不得全局替换。`paper_notes`、`paper_notes_abstract`、`abstract_notes` 和 `paper_notes_bridge` 只在旧 Workspace 的兼容迁移说明中出现，不能作为新项目的写入路径。

## 文档检查与维护

每次修改用户文档、CLI 示例、任务名称或 Artifact Contract 后，从仓库根目录运行：

```bash
python scripts/check_docs.py
python scripts/check_docs.py --strict \
  --report tmp/debug/08_documentation_audit/docs_quality.json
```

默认模式因断链、缺失锚点、无效 CLI 子命令、无效选项、无效 task ID 和缺失术语指南失败。术语、旧路径上下文和无法解析的 shell 示例是 warning；`--strict` 会将 warning 也视为失败。该工具只读扫描文档，除非显式提供 `--report`，否则不会写入文件。

更多入口见 [英文文档导航](en/README.md)、[中文文档导航](cn/README.md)、[运行时与恢复说明](cn/runtime.md) 和 [开发说明](cn/dev.md)。
