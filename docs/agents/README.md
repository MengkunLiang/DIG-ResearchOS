# ResearchOS Agent 实现文档总览

本目录包含ResearchOS系统中所有Agent的详细实现文档。每个Agent负责研究流程中的特定阶段，通过文件通信协作完成从idea到论文投稿的完整流程。

## Agent列表

### T1: PI Agent（项目初始化与评估）
**文档**: [T1_PI_AGENT.md](./T1_PI_AGENT.md)

**职责**:
- **T1 (init模式)**: 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
- **T7.5 (evaluate模式)**: 评估实验结果，决定后续路径

**输入**: 用户研究方向（T1）或实验结果（T7.5）

**输出**: 
- T1: `project.yaml`, `user_seeds/seed_papers.jsonl`, `seed_ideas.md`, `seed_constraints.md`
- T7.5: `evaluation/evaluation_decision.md`

**模型层级**: heavy

---

### T2: Scout Agent（文献侦察员）
**文档**: [T2_SCOUT_AGENT.md](./T2_SCOUT_AGENT.md)

**职责**: 跨源检索学术论文，实现去重和相关性打分，产出高质量论文池

**输入**: `project.yaml`, `user_seeds/seed_papers.jsonl`（可选）

**输出**: 
- `literature/papers_raw.jsonl`（100-200篇原始结果）
- `literature/papers_dedup.jsonl`（15-120篇去重后论文池）
- `literature/search_log.md`
- `literature/missing_areas.md`

**模型层级**: medium

**关键特性**:
- 多源检索（Semantic Scholar, arXiv, CrossRef, EuropePMC）
- 两阶段去重（DOI精确匹配 + 标题相似度≥0.9）
- 相关性打分（0.0-1.0）

---

### T3: Reader Agent（深度阅读模式）
**文档**: [T3_READER_AGENT.md](./T3_READER_AGENT.md)

**职责**: 逐篇精读论文并产出结构化笔记

**输入**: `literature/papers_dedup.jsonl`, `project.yaml`

**输出**:
- `literature/paper_notes/{id}.md`（每篇论文的11项checklist笔记）
- `literature/comparison_table.csv`（论文对比表）
- `literature/related_work.bib`（BibTeX引用库）

**模型层级**: medium

**关键特性**:
- 11项checklist模板（Problem, Method, Results, Claims vs Evidence等）
- 支持PDF全文提取
- 批次处理（一次处理1篇）

---

### T3.5: Reader Agent（文献综合模式）
**文档**: [T3.5_SYNTHESIS_AGENT.md](./T3.5_SYNTHESIS_AGENT.md)

**职责**: 综合所有论文笔记产出系统性的文献综述

**输入**: `literature/paper_notes/`, `comparison_table.csv`, `project.yaml`

**输出**: `literature/synthesis.md`（包含5个必需章节）

**模型层级**: heavy

**5个必需章节**:
1. 方法家族分类（Method Families）
2. 共同假设（Shared Assumptions）
3. 性能-效率前沿（Performance-Efficiency Frontier）
4. 技术趋势（Trends）
5. 可操作研究问题（Actionable Research Questions）

---

### T4: Ideation Agent（假设生成）
**文档**: [T4_IDEATION_AGENT.md](./T4_IDEATION_AGENT.md)

**职责**: 基于文献综述生成研究假设和实验计划，通过两轮Gate确认

**输入**: `literature/synthesis.md`, `missing_areas.md`, `user_seeds/seed_ideas.md`（可选）

**输出**:
- `ideation/hypotheses.md`（3-6个研究假设）
- `ideation/exp_plan.yaml`（实验计划）
- `ideation/risks.md`（风险评估）

**模型层级**: heavy

**关键特性**:
- Temperature 0.75（鼓励创造性）
- 两轮Human Gate确认（假设草案 + 实验计划）
- 预算检查（单个实验不超过预算85%）

---

### T4.5: Novelty Auditor Agent（新颖性审计）
**文档**: [T4.5_NOVELTY_AUDITOR_AGENT.md](./T4.5_NOVELTY_AUDITOR_AGENT.md)

**职责**: 对研究假设进行新颖性审计，检查是否与已有工作重复

**输入**: `ideation/hypotheses.md`, `literature/synthesis.md`, `comparison_table.csv`

**输出**:
- `ideation/novelty_audit.md`（新颖性审计报告）
- `ideation/collision_cases.md`（潜在撞车案例，如果有）

**模型层级**: heavy

**新颖性等级**:
- Level 3: 高新颖性（几乎没有直接相关工作）
- Level 2: 中等新颖性（有相关工作但差异明显）
- Level 1: 低新颖性（有多篇相似工作）
- Level 0: 无新颖性（已有几乎相同的工作）

---

### T4.5: Novelty Auditor Agent（新颖性预审）
**文档**: [T4.5_NOVELTY_AUDITOR_AGENT.md](./T4.5_NOVELTY_AUDITOR_AGENT.md)

**职责**: 对研究假设进行新颖性预审，检查是否与已有工作重复，在 Pilot 实验前识别撞车风险

**输入**: `ideation/hypotheses.md`, `literature/synthesis.md`, `comparison_table.csv`

**输出**:
- `ideation/novelty_audit.md`（新颖性审计报告）
- `ideation/collision_cases.md`（潜在撞车案例，如果有）

**模型层级**: heavy

**关键区别 vs T6**:
| 方面 | T4.5 NoveltyAuditor | T6 Novelty |
|------|---------------------|------------|
| 时机 | T4 Ideation 后，Pilot 前 | T5 Pilot 后，Full 前 |
| 输入 | 纯假设，无实验结果 | 有 Pilot 实验证据 |
| 目的 | 预审假设新颖性，识别撞车风险 | 基于实验验证新颖性，补充基线 |
| 输出目录 | `ideation/` | `novelty/` |

**新颖性等级**:
- Level 3: 高新颖性（几乎没有直接相关工作）
- Level 2: 中等新颖性（有相关工作但差异明显）
- Level 1: 低新颖性（有多篇相似工作）
- Level 0: 无新颖性（已有几乎相同的工作）

---

### T6: Novelty Agent（新颖性最终验证）
**文档**: [T6_NOVELTY_AGENT.md](./T6_NOVELTY_AGENT.md)（与 T5 Experimenter 共享代码）

**职责**: 基于 Pilot 实验结果验证新颖性，搜索近期相关工作，补充必须的基线方法

**输入**: `ideation/hypotheses.md`, `pilot/pilot_results.json`, `pilot/motivation_validation.md`

**输出**:
- `novelty/novelty_report.md`（新颖性最终报告）
- `novelty/collision_cases.md`（潜在撞车案例，如果有）
- `novelty/must_add_baselines.md`（必须补充的基线方法）

**模型层级**: medium

**Gate T6-DECIDE**:
- PASS: 所有假设 Level 2+ 且 Pilot 充分验证 → 进入 T7 完整实验
- REVISE: 存在 Level 1 假设或 Pilot 部分验证 → 修改假设
- FAIL: 存在 Level 0 假设或 Pilot 未验证核心假设 → 重新构思

---

### T7: Experimenter Agent（完整实验）
**文档**: [T7_EXPERIMENTER_AGENT.md](./T7_EXPERIMENTER_AGENT_AGENT.md)（与 T5 共享代码）

**职责**: 执行完整的实验计划，收集全面结果，支持 Docker 隔离执行

**输入**: `ideation/hypotheses.md`, `ideation/exp_plan.yaml`, `pilot/pilot_results.json`, `novelty/novelty_report.md`

**输出**:
- `experiments/results_summary.json`（实验结果汇总）
- `experiments/iteration_log.md`（实验迭代日志）
- `experiments/ablations.csv`（消融实验结果）
- `experiments/docker_digests.txt`（Docker 镜像摘要）

**模型层级**: medium

**关键特性**:
- 支持 Docker 隔离执行
- 依赖关系管理（拓扑排序）
- 预算管理（GPU 时间和成本估算）
- 最多 5 轮迭代

---

### T5: Experimenter Agent（试点实验 Pilot）
**文档**: [T5_EXPERIMENTER_PILOT_AGENT.md](./T5_EXPERIMENTER_PILOT_AGENT.md)

**职责**: 执行小规模试点实验，验证假设可行性，收集动机验证证据

**输入**: `ideation/hypotheses.md`, `ideation/exp_plan.yaml`, `project.yaml`

**输出**:
- `pilot/pilot_results.json`（试点实验结果）
- `pilot/motivation_validation.md`（动机验证报告）
- `pilot/smoke_test_passed.marker`（冒烟测试通过标记）
- `pilot/docker_digests.txt`（Docker 镜像摘要）

**模型层级**: medium

**关键特性**:
- 试点模式（小规模验证）
- 动机验证（验证假设的动机是否成立）
- 冒烟测试（快速检查核心假设）

> ⚠️ **注意**: T8 和 T9 Agent 尚未实现（代码未实现），以下是规划中的设计文档

---

### T8: Writer和Reviewer Agent（论文写作与审稿）⚠️规划中
**文档**: [T8_WRITER_REVIEWER_AGENT.md](./T8_WRITER_REVIEWER_AGENT.md)

**状态**: 规划中（NOT IMPLEMENTED）

**职责**: 
- **Writer**: 生成论文各个部分（大纲、初稿、修订、最终版）
- **Reviewer**: 审查论文并提出改进建议

**输入**: `experiments/results_summary.json`, `literature/synthesis.md`, `related_work.bib`

**输出**:
- `drafts/outline.md`（论文大纲）
- `drafts/paper.tex`（LaTeX论文草稿）
- `drafts/self_check.md`（自查清单）
- `drafts/review_rounds/round_N.md`（审稿意见）

**模型层级**: heavy（两个Agent都是）

**关键特性**:
- Writer-Reviewer迭代循环（最多2轮）
- Post-hooks校验（引用完整性、数字准确性）
- 5个执行阶段（outline, draft, self_check, revise, final）

---

### T9: Submission Agent（投稿准备）⚠️规划中
**文档**: [T9_SUBMISSION_AGENT.md](./T9_SUBMISSION_AGENT.md)

**状态**: 规划中（NOT IMPLEMENTED）

**职责**: 将论文草稿转换为符合目标会议格式的投稿包

**输入**: `drafts/paper.tex`, `figures/`, `related_work.bib`, `project.yaml`

**输出**:
- `submission/bundle/`（投稿包目录）
- `submission/migration_report.md`（迁移报告）
- `submission/bundle.zip`（打包文件）

**模型层级**: medium

**关键特性**:
- 模板迁移（支持NeurIPS, ICML, ACL等主要会议）
- 匿名化检查（Pre-hook）
- LaTeX编译验证（Docker隔离）
- 格式检查（页数、字体、页边距等）

---

## Agent协作流程

```
T1 (PI Agent - init)
  ↓ project.yaml, seed_papers.jsonl
T2 (Scout Agent)
  ↓ papers_dedup.jsonl
T3 (Reader Agent - read)
  ↓ paper_notes/, comparison_table.csv
T3.5 (Reader Agent - synthesize)
  ↓ synthesis.md
T4 (Ideation Agent)
  ↓ hypotheses.md, exp_plan.yaml
T4.5 (NoveltyAuditor Agent) ← 新颖性预审（无实验证据）
  ↓ novelty_audit.md
T5 (Experimenter Agent - pilot) ← 试点实验
  ↓ pilot_results.json
T6 (Novelty Agent) ← 新颖性最终验证（基于实验证据）
  ↓ novelty_report.md
T7 (Experimenter Agent - full) ← 完整实验
  ↓ results_summary.json
T7.5 (PI Agent - evaluate)
  ↓ evaluation_decision.md
  ├─→ 继续迭代（回到T4或T6）
  └─→ 准备写作
T8 (Writer + Reviewer Agents) ⚠️规划中
  ↓ paper.tex
T9 (Submission Agent) ⚠️规划中
  ↓ submission/bundle/
```

## 通用设计原则

### 1. Agent间通信 = 文件
- Agent之间不共享内存对象，不互相调用API
- 上游Agent的输出写到文件，下游Agent从文件读
- 好处：断点恢复、用户可介入、调试简单

### 2. Runtime做重，Agent做轻
- Agent只定义：system prompt、tool列表、输入输出schema
- Runtime负责：消息协议、主循环、工具调用、重试、超时、token控制、日志

### 3. 一个Agent一个模型
- 每个Agent在AgentSpec中声明`model_tier: heavy/medium/light`
- Runtime根据`model_routing.yaml`映射到具体模型
- 支持每个T-stage独立配置模型

### 4. 严格校验
- 每个Agent的`validate_outputs`检查输出文件存在性和格式
- 使用Pydantic schema校验结构化数据
- 校验不通过 → Runtime把错误喂给Agent → Agent继续修复

### 5. Human Gate
- 关键决策点设置Human Gate（如T4的两轮确认、T8的用户审核）
- 用户可以介入修改、批准或拒绝
- Gate通过`ask_human`工具实现

## 模型层级说明

### Heavy（重型模型）
- **用途**: 需要深度推理、创造性思维、高质量写作
- **Agent**: T1 PI, T3.5 Synthesis, T4 Ideation, T4.5 Novelty Auditor, T8 Writer/Reviewer
- **推荐模型**: Claude Opus 4, GPT-4
- **特点**: 高成本、高质量、支持extended thinking

### Medium（中型模型）
- **用途**: 代码生成、数据处理、格式转换
- **Agent**: T2 Scout, T3 Reader, T6 Experimenter, T9 Submission
- **推荐模型**: Claude Sonnet 4, GPT-4o
- **特点**: 平衡成本和质量

### Light（轻型模型）
- **用途**: 简单任务、快速响应
- **Agent**: 当前没有使用
- **推荐模型**: Claude Haiku, GPT-4o-mini
- **特点**: 低成本、快速

## 配置文件

### model_routing.yaml
```yaml
heavy:
  provider: "anthropic"
  model: "claude-opus-4"
  max_tokens: 4096
  supports_thinking: true

medium:
  provider: "anthropic"
  model: "claude-sonnet-4"
  max_tokens: 4096

light:
  provider: "anthropic"
  model: "claude-haiku-4"
  max_tokens: 4096
```

### runtime.yaml
```yaml
agents:
  pi:
    max_retries: 3
    timeout_seconds: 1800
    enable_thinking: true
  
  scout:
    max_retries: 3
    timeout_seconds: 1800
  
  # ... 其他Agent配置
```

## 测试

每个Agent都有对应的单元测试和集成测试：

```bash
# 运行所有Agent测试
pytest tests/unit/test_*_agent.py -v

# 运行特定Agent测试
pytest tests/unit/test_pi_agent.py -v
pytest tests/integration/test_scout_agent_e2e.py -v
```

## 开发指南

### 添加新Agent

1. 在`researchos/agents/`创建新的Agent类
2. 继承`Agent`基类，定义`AgentSpec`
3. 实现`system_prompt()`, `initial_user_message()`, `validate_outputs()`
4. 在`researchos/prompts/`创建对应的Jinja2模板
5. 在`researchos/agents/registry.py`注册Agent
6. 编写单元测试和集成测试
7. 编写Agent文档（参考本目录的文档格式）

### 修改现有Agent

1. 修改Agent类或prompt模板
2. 更新对应的测试
3. 更新Agent文档
4. 运行测试确保没有破坏现有功能

## 参考资料

- **Runtime规范**: `/home/liangmengkun/reference_materials/ResearchOS_Runtime_Dev_Spec.md`
- **系统设计**: `/home/liangmengkun/reference_materials/ResearchOS_v3_complete.md`
- **代码实现**: `/home/liangmengkun/ResearchOS/researchos/agents/`
- **测试**: `/home/liangmengkun/ResearchOS/tests/`

## Agent 状态矩阵

| Agent | Task | 代码状态 | 文档状态 | 模型层级 | 备注 |
|-------|------|---------|---------|---------|------|
| Hello | HELLO | ✅ 已实现 | ✅ 完整 | medium | 调试用 |
| PI | T1, T7.5 | ✅ 已实现 | ✅ 完整 | heavy | init/evaluate 模式 |
| Scout | T2 | ✅ 已实现 | ✅ 完整 | medium | |
| Reader | T3, T3.5 | ✅ 已实现 | ✅ 完整 | medium | read/synthesize 模式 |
| Ideation | T4 | ✅ 已实现 | ✅ 完整 | heavy | |
| NoveltyAuditor | T4.5 | ✅ 已实现 | ✅ 完整 | heavy | 新颖性预审 |
| Experimenter | T5, T7 | ✅ 已实现 | ✅ 完整 | medium | pilot/full 模式 |
| Novelty | T6 | ✅ 已实现 | ✅ 完整 | medium | 新颖性最终验证 |
| Writer | T8 | ❌ 未实现 | ⚠️ 规划中 | heavy | |
| Reviewer | T8 | ❌ 未实现 | ⚠️ 规划中 | heavy | |
| Submission | T9 | ❌ 未实现 | ⚠️ 规划中 | medium | |

## 鲁棒性增强功能

ResearchOS 实现了多项鲁棒性增强功能，提高系统的可靠性和研究质量：

### 1. T4 Hypothesis Pre-mortem（假设预演）

**位置**: T4 Ideation Agent，Gate1 和 Gate2 之间

**功能**: 对选定的研究方向执行三维检查
- 物理/数学约束检查
- 已知反例识别
- 资源可行性评估

**触发条件**: 发现 High 风险且无缓解方案时，提示用户重新选择方向

**实现**: `researchos/prompts/ideation.j2`（阶段 A.5）

### 2. Runtime Budget Drift Warning（预算漂移预警）

**位置**: StateMachine，每个 task 完成后

**功能**: 监控累计花费，防止预算超支
- 超过预算 70%：记录警告日志
- 超过预算 90%：记录严重警告并写入警告文件

**实现**: `researchos/orchestration/state_machine.py`（`_check_budget_drift` 方法）

### 3. T1 Ethical Screening（敏感方向拦截）

**位置**: T1 PI Agent，`validate_outputs` 阶段

**功能**: 检测敏感研究方向
- 武器、监控、操纵、隐私侵犯、歧视等敏感领域
- 检测到敏感词时返回警告并要求用户确认

**实现**: `researchos/agents/pi.py`（`_check_ethical_concerns` 方法）

### 4. T1 External Resources Management（外部资源管理）

**位置**: T1 PI Agent，三轮对话中的第 2.5 轮

**功能**: 询问并记录用户已有的外部资源
- 支持 7 种资源类型：dataset、baseline_repo、pretrained_model、docker_image、tool、script、other
- 生成 `user_seeds/seed_external_resources.jsonl` 文件
- 验证资源格式和 source 前缀

**实现**: `researchos/prompts/pi.j2`（第 2.5 轮对话）和 `researchos/agents/pi.py`（`_validate_external_resources` 方法）

### 5. T8 声明追溯与数值一致性检查

**位置**: T8 Writer Agent，post-hooks

**功能**: 确保论文中的声明有实验支撑，数值准确
- 声明追溯：检查每个声明是否有对应的实验结果
- 数值一致性：验证论文中的数值与实验结果一致

**状态**: ⚠️ 规划中（T8 未实现）

### 6. T6 机制相似度搜索

**位置**: T6 Novelty Agent

**功能**: 基于 Pilot 实验结果，搜索近期相关工作
- 使用实验证据验证新颖性
- 补充必须的基线方法

**实现**: `researchos/agents/novelty.py`

### 7. T5/T7 种子集成与外部资源

**位置**: T5 Pilot 和 T7 Full Experimenter

**功能**: 集成用户提供的外部资源
- 读取 `user_seeds/seed_external_resources.jsonl`
- 在实验中使用用户提供的数据集、模型、代码等

**实现**: `researchos/agents/experimenter.py`

### 8. 迭代死锁检测（所有 Agent）

**位置**: AgentRunner 主循环

**功能**: 检测 Agent 是否陷入无限循环
- 监控连续空回复次数
- 监控验证失败重试次数
- 超过阈值时终止执行

**实现**: `researchos/runtime/agent_runner.py`

**测试**: 所有功能均有对应的单元测试，见 `tests/unit/test_robustness_enhancements.py`

---

## 已知限制

1. **MCP集成**: 当前 T2 Scout Agent 的 MCP 工具已注释，等 MCP 配置完成后再启用
2. **T8/T9**: Writer/Reviewer Agent 和 Submission Agent 代码尚未实现，只有设计文档
3. **容器环境**: 所有 Agent 在统一 Docker 环境中运行，自动检测容器环境并适配执行模式

## T4.5 vs T6 新颖性验证的区别

| 方面 | T4.5 NoveltyAuditor | T6 Novelty |
|------|---------------------|------------|
| **时机** | T4 Ideation 后，Pilot 前 | T5 Pilot 后，Full 前 |
| **输入** | 纯假设，无实验结果 | 有 Pilot 实验证据 |
| **目的** | 预审假设新颖性，识别撞车风险 | 基于实验验证新颖性，补充基线 |
| **模型** | heavy (deep_reasoning) | medium |
| **输出目录** | `ideation/` | `novelty/` |
| **Gate** | 无 | T6-DECIDE (PASS/REVISE/FAIL) |
| **关键区别** | 无实验证据，纯假设审计 | 有 Pilot 证据支撑判断 |

## 更新日志

- **2024-04-20**: 创建完整的Agent文档集
  - 补充T1 PI Agent详细文档
  - 创建T4 Ideation Agent文档
  - 创建T4.5 Novelty Auditor Agent文档
  - 创建T6 Experimenter Agent文档
  - 创建T8 Writer/Reviewer Agent文档
  - 创建T9 Submission Agent文档
  - 整合重复的T2 Scout Agent文档
  - 统一文档命名格式

## 贡献

如果发现文档错误或需要补充，请：
1. 直接修改对应的Agent文档
2. 更新本README.md（如果涉及Agent列表或流程变更）
3. 提交commit并push到仓库

---

**维护者**: ResearchOS开发团队  
**最后更新**: 2024-04-20
