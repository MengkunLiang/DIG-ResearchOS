# ResearchOS 外部 Skills 调研报告

本文档总结了对外部成熟 research-writing/research-skill 系统的调研结果，分析其设计思想的可迁移性。

---

## 1. 调研概述

### 1.1 调研来源

| 来源 | 排名 | Stars | Agents/Skills | 适用性 |
|------|------|-------|---------------|--------|
| [academic-research-skills](https://github.com/Imbad0202/academic-research-skills) | Tier 1 | 3.2k | 42 agents, 10 stages | 最高 |
| [AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs) | Tier 2 | - | 87 skills, npm package | 高 |
| [claude-scientific-writer](https://github.com/K-Dense-AI/claude-scientific-writer) | Tier 3 | - | 19+ skills | 中 |
| [awesome-ai-research-writing](https://github.com/Leey21/awesome-ai-research-writing) | Tier 4 | - | prompt library | 低 |

### 1.2 评估标准

- **可直接适配**: 设计思想与 ResearchOS 兼容，可直接迁移
- **借鉴思想**: 设计思想有价值，但需要调整后适配
- **不适用**: 与 ResearchOS 目标不符或过于复杂

---

## 2. Tier 1: academic-research-skills

### 2.1 项目概述

| 指标 | 值 |
|------|-----|
| Stars | 3.2k |
| Agents | 42+ |
| Stages | 10 |
| 特点 | Material Passport, Integrity Gate, 7 AI Research Failure Modes |

### 2.2 核心设计思想

#### Integrity Gate

**描述**: Stage 2.5 预审阶段，引用/引用验证必须在同行评审前完成。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 新增 T4.5→T5 Integrity Gate
- 在 Pilot 实验前验证假设完整性
- 检查 hypotheses.md 和 novelty_audit.md

**参考实现**:
```python
def pre_pilot_integrity_check(workspace_dir):
    """验证 Pilot 实验前的假设完整性"""
    issues = []

    # 1. 检查假设文件
    hypotheses_path = workspace_dir / "ideation" / "hypotheses.md"
    if not hypotheses_path.exists():
        issues.append("缺少 hypotheses.md")

    # 2. 检查新颖性审计
    audit_path = workspace_dir / "ideation" / "novelty_audit.md"
    if not audit_path.exists():
        issues.append("缺少新颖性审计")

    return len(issues) == 0, issues
```

#### Material Passport

**描述**: 结构化跨会话状态管理，记录制品来源和元数据。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 每个 agent 输出增加 manifest.yaml
- 记录制品版本、依赖输入、时间戳、校验和

**Schema**:
```yaml
manifest_version: "1.0"
created_at: "2024-04-20T10:30:00Z"
agent: "novelty"
task_id: "T6"

artifacts:
  - path: "novelty/novelty_report.md"
    type: "markdown"
    checksum: "sha256:abc123..."

inputs:
  - path: "ideation/hypotheses.md"
    required: true
    checksum: "sha256:xyz789..."
```

#### 7 AI Research Failure Modes

**描述**: 实现bug、幻觉结果、捷径依赖、bug转insight重构、方法论伪造、框架锁定、引用幻觉。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 集成到 T5/T7 validate_outputs
- 检测常见 AI 错误模式

**Checklist**:
| ID | 模式 | 检测方法 |
|----|------|----------|
| FM1 | Implementation Bugs | 检查 loss 是否发散 |
| FM2 | Hallucinated Results | 交叉验证关键数字 |
| FM3 | Shortcut Reliance | 消融实验是否分离组件 |
| FM4 | Bug-as-Insight Reframing | 检查结果是否符合预期 |
| FM5 | Methodology Fabrication | 验证方法描述与实现一致 |
| FM6 | Frame-Lock | 检查是否有多视角分析 |
| FM7 | Citation Hallucinations | 验证引用存在 |

#### SCR Loop (State-Challenge-Reflect)

**描述**: 承诺门收集用户预测，不确定性触发矛盾检测。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 增强 T4.5 NoveltyAuditor 的质疑机制
- 在假设审计中增加自我挑战

#### Devil's Advocate Protocol

**描述**: 形式化让步阈值评分，只在 score>=4 时让步。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 增强 T4.5 的撞车风险评估
- 量化相似度评分决策

#### Score Trajectory Tracking

**描述**: 修订轮次中逐维度评分增量追踪，delta<-3 触发强制检查点。

**可迁移性**: ⭐⭐ 直接适配

**ResearchOS 应用**:
- 增强 T6 Novelty 的修订历史
- 追踪新颖性等级变化

#### Writing Quality Check

**描述**: 25 个 AI 高频词警告 + burstiness + em dash 限制。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T8 Writer prompt 中增加去AI化检查
- 检测 AI 生成文本的机器特征

#### Knowledge Isolation Directive

**描述**: 标记 `[MATERIAL GAP]` 而非从记忆中填充。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- 在 T6 prompt 中标记未知领域
- 避免 AI 幻觉式填补知识空白

#### Reference Verification

**描述**: Semantic Scholar API + Levenshtein >=0.70 匹配。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T4.5/T6 的引用验证增强
- 使用 search_papers 验证引用存在

---

## 3. Tier 2: AI-Research-SKILLs

### 3.1 项目概述

| 指标 | 值 |
|------|-----|
| 形态 | npm package |
| Skills | 87 |
| 特点 | Two-Loop Architecture, Persistent Workspace, Skill Routing |

### 3.2 核心设计思想

#### Two-Loop Architecture

**描述**: 内环优化实验，外环综合知识。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- T5 Pilot: 内环（快速验证）
- T7 Full: 外环（全面实验）

#### Persistent Workspace

**描述**: research-state.yaml + findings.md + research-log.md。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 增强工作区持久化
- 每个阶段增加 state.yaml

**文件结构**:
```
workspace/
├── project.yaml
├── state.yaml           # 项目状态
├── findings.md          # 关键发现
└── research-log.md      # 活动日志
```

#### Skill Routing Layer

**描述**: 自动路由到 86 个 skill，agent 无需知道具体 skill。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- Agent 内部的能力路由
- Tool 选择优化

#### Human-in-the-Loop via to_human/

**描述**: 结构化 agent-to-human 通信目录。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 增强 ask_human 工具
- 结构化 Gate 输出

**示例**:
```
to_human/
├── T4-DRAFT-GATE/
│   ├── hypotheses_draft.md
│   ├── user_response.md
│   └── approved: true/false
└── T6-DECIDE/
    ├── decision: PASS/REVISE/FAIL
    └── rationale.md
```

#### Ideation Lenses

**描述**: 10 个互补视角用于结构化头脑风暴。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- 直接增强 T4 IdeationAgent
- 要求从 10 个视角审视每个假设

**10 个视角**:
1. Contrastive（对比视角）
2. First-principles（第一性原理）
3. Analogical（类比视角）
4. Constraint-based（约束视角）
5. Multi-scale（多尺度视角）
6. Temporal（时间视角）
7. Causal（因果视角）
8. Uncertainty（不确定性视角）
9. Resource（资源视角）
10. Stakeholder（利益相关者视角）

#### Lessons & Constraints

**描述**: 跨会话累积制度知识。

**可迁移性**: ⭐⭐⭐ 直接适配

**ResearchOS 应用**:
- lessons.md: 从实验中学到的教训
- constraints.md: 项目约束和限制

---

## 4. Tier 3: claude-scientific-writer

### 4.1 项目概述

| 指标 | 值 |
|------|-----|
| Agents | 19+ |
| Formats | NeurIPS, ICML, ACL 等 |
| 特点 | Citation Verification, ScholarEval, Skill Chaining |

### 4.2 核心设计思想

#### File Auto-Routing

**描述**: images → figures/, data → data/, docs → markdown。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T8 Writer 的文件组织
- 图表和数据的自动路由

#### Citation Verification

**描述**: 实时文献搜索确保声明有可验证来源。

**可迁移性**: ⭐⭐⭐ 可用于 T4.5/T6

**ResearchOS 应用**:
- T4.5/T6 的引用完整性检查
- 使用 search_papers 验证声明

#### ScholarEval 8-Dimension

**描述**: 8 维度定量同行评审评分。

**可迁移性**: ⭐ 需适配

**ResearchOS 应用**:
- T8 Reviewer 的评分标准
- 长期目标

**8 维度**:
1. Originality
2. Technical Quality
3. Clarity
4. Reproducibility
5. Significance
6. Depth
7. Presentation
8. Overall

#### Skill Chaining

**描述**: research-lookup → peer-review → paper generation。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T4.5→T5→T6 的技能链
- Agent 间的数据流优化

---

## 5. Tier 4: awesome-ai-research-writing

### 5.1 项目概述

| 指标 | 值 |
|------|-----|
| 类型 | prompt library |
| 特点 | 去AI味, Execution Protocol, Logic Validation |

### 5.2 核心设计思想

#### AI-Detection Removal ("去AI味")

**描述**: 去除 AI 生成文本的机器检测特征。

**可迁移性**: ⭐ 优先级低

**ResearchOS 应用**:
- T8 Writer 的后期处理
- 当前优先级低

#### Execution Protocol Self-Checks

**描述**: 每个 prompt 的强制自检。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- Agent 的 post-hook 校验
- 数字准确性检查

#### Logic Validation with High Tolerance

**描述**: 只标记致命矛盾。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T6 Novelty 的矛盾检测
- 避免过度挑剔

#### Three-Stage Doc-Coauthoring

**描述**: 明确 reader testing 阶段。

**可迁移性**: ⭐⭐ 借鉴思想

**ResearchOS 应用**:
- T8 Writer 的写作阶段
- Reader testing 作为第三阶段

---

## 6. 可迁移性总结

### 6.1 高优先级（可直接适配）

| 设计思想 | 来源 | 目标 Agent/阶段 |
|---------|------|----------------|
| Ideation Lenses | AI-Research-SKILLs | T4 Ideation |
| Integrity Gate | academic-research-skills | T4.5→T5 |
| Material Passport | academic-research-skills | 全局 |
| 7 AI Research Failure Modes | academic-research-skills | T5 validate_outputs |
| SCR Loop | academic-research-skills | T4.5 |
| Devil's Advocate Protocol | academic-research-skills | T4.5 |
| Score Trajectory Tracking | academic-research-skills | T6 |
| to_human/ Checkpoints | AI-Research-SKILLs | 全局 Gate |
| findings.md / research-log.md | AI-Research-SKILLs | 全局 |
| Citation Verification | claude-scientific-writer | T4.5/T6 |
| Two-Loop Architecture | AI-Research-SKILLs | T5/T7 |

### 6.2 中优先级（借鉴思想）

| 设计思想 | 来源 | 建议应用 |
|---------|------|---------|
| Writing Quality Check | academic-research-skills | T8 Writer |
| Knowledge Isolation | academic-research-skills | T6 prompt |
| Skill Routing Layer | AI-Research-SKILLs | Agent 内部 |
| ScholarEval 8-Dimension | claude-scientific-writer | T8 Reviewer |
| Execution Protocol Self-Checks | awesome-ai-research-writing | Agent hooks |

### 6.3 低优先级（暂不适用）

| 设计思想 | 来源 | 原因 |
|---------|------|------|
| AI-Detection Removal | awesome-ai-research-writing | 优先级低 |
| PRISMA/RAISE Compliance | academic-research-skills | 过于复杂 |
| Cross-Model Verification | academic-research-skills | 需要额外 API |
| Continuous Autonomous Operation | AI-Research-SKILLs | 需要 CLI 支持 |
| npm skill routing | AI-Research-SKILLs | 与架构不兼容 |

---

## 7. 实施建议

### 7.1 近期（Phase 3）

1. **Ideation Lenses → T4**
   - 修改 `researchos/prompts/ideation.j2`
   - 添加 10 个视角审视要求

2. **Integrity Gate → T5**
   - 修改 `researchos/agents/experimenter.py`
   - pilot 模式增加 pre-execution 验证

3. **Failure Mode Checklist → T5**
   - 修改 `researchos/agents/experimenter.py`
   - validate_outputs 增加 failure mode 检查

### 7.2 中期

4. **Material Passport → Handoffs**
   - 修改 `_common.py`
   - 添加 manifest 生成函数

5. **Citation Verification → T4.5/T6**
   - 修改 prompt 模板
   - 增加引用验证步骤

### 7.3 长期

6. **T8 Writer/Reviewer 实现**
7. **T9 Submission 实现**
8. **ScholarEval 集成**

---

## 8. 参考链接

- **academic-research-skills**: https://github.com/Imbad0202/academic-research-skills
- **AI-Research-SKILLs**: https://github.com/Orchestra-Research/AI-Research-SKILLs
- **claude-scientific-writer**: https://github.com/K-Dense-AI/claude-scientific-writer
- **awesome-ai-research-writing**: https://github.com/Leey21/awesome-ai-research-writing
