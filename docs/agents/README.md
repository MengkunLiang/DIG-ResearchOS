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

### T6: Experimenter Agent（实验执行）
**文档**: [T6_EXPERIMENTER_AGENT.md](./T6_EXPERIMENTER_AGENT.md)

**职责**: 执行实验计划，收集结果

**输入**: `ideation/exp_plan.yaml`, `hypotheses.md`, `project.yaml`

**输出**:
- `experiments/results_summary.json`（实验结果汇总）
- `experiments/iteration_log.md`（实验迭代日志）
- `experiments/runs/{run_id}/`（每个实验的详细结果）

**模型层级**: medium

**关键特性**:
- 支持Docker隔离执行
- 依赖关系管理（拓扑排序）
- 预算管理（GPU时间和成本估算）
- 最多5轮迭代

---

### T8: Writer和Reviewer Agent（论文写作与审稿）
**文档**: [T8_WRITER_REVIEWER_AGENT.md](./T8_WRITER_REVIEWER_AGENT.md)

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

### T9: Submission Agent（投稿准备）
**文档**: [T9_SUBMISSION_AGENT.md](./T9_SUBMISSION_AGENT.md)

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
T4.5 (Novelty Auditor Agent)
  ↓ novelty_audit.md
T6 (Experimenter Agent)
  ↓ results_summary.json
T7.5 (PI Agent - evaluate)
  ↓ evaluation_decision.md
  ├─→ 继续迭代（回到T4或T6）
  └─→ 准备写作
T8 (Writer + Reviewer Agents)
  ↓ paper.tex
T9 (Submission Agent)
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

## 已知限制

1. **T5 Pilot模式**: 当前Experimenter Agent主要实现了full模式，pilot模式待完善
2. **T7 正式实验**: 与T6功能重叠，可能需要合并或重新设计
3. **Writer/Reviewer Agent**: 当前为设计文档，代码实现待完成
4. **Submission Agent**: 当前为设计文档，代码实现待完成
5. **MCP集成**: 当前T2 Scout Agent的MCP工具已注释，等MCP配置完成后再启用

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
