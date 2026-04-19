# ResearchOS Agent 开发策略分析报告

> **生成时间**: 2026-04-19  
> **基于文档**: ResearchOS_Agent_Dev_Spec.md (v1.0) + ResearchOS_Runtime_Dev_Spec.md (v3.3)  
> **目标**: 为9个agent的开发提供可执行的策略、顺序和验收标准

---

## 目录

1. [T1-T9 Agent开发顺序矩阵](#1-t1-t9-agent开发顺序矩阵)
2. [T1 PI Agent详细分析](#2-t1-pi-agent详细分析)
3. [T2 Scout Agent详细分析](#3-t2-scout-agent详细分析)
4. [T3-T9 Agent概览](#4-t3-t9-agent概览)
5. [开发建议与路线图](#5-开发建议与路线图)
6. [风险点与缓解措施](#6-风险点与缓解措施)
7. [测试覆盖策略](#7-测试覆盖策略)

---

# 1. T1-T9 Agent开发顺序矩阵

## 1.1 完整对照表

| Agent | T-Stage | 难度 | 依赖Agent | 关键工具 | 预估工作量 | 优先级 | 验收标准 |
|-------|---------|------|-----------|----------|-----------|--------|----------|
| **PI** | T1/T7.5 | 简单 | 无 | read_file, write_file, ask_human | 8-12h | P0 | project.yaml合schema + 3个seed文件存在 |
| **Scout** | T2 | 中等 | T1 | search_papers, MCP(arxiv/s2) | 12-16h | P0 | 15-120篇dedup论文 + search_log |
| **Reader** | T3/T3.5 | 中等 | T2 | extract_paper_sections, read_file | 16-20h | P1 | 每篇paper_note + comparison_table + synthesis.md |
| **Ideation** | T4 | 中等 | T3 | read_file, write_file, ask_human | 10-14h | P1 | hypotheses.md + exp_plan.yaml合schema |
| **Experimenter** | T5/T7 | 困难 | T4 | docker_exec, bash_run, grep_search | 24-32h | P2 | pilot_results.json + 代码可复现 |
| **Novelty** | T6 | 简单 | T5 | search_papers, read_file | 8-10h | P2 | novelty_report.md + must_add_baselines |
| **Writer** | T8.write | 困难 | T7 | read_file, write_file | 20-24h | P3 | paper.tex编译通过 + 结构完整 |
| **Reviewer** | T8.review | 中等 | T8.write | read_file, write_file, ask_human | 12-16h | P3 | review.md + 跨provider审查 |
| **Submission** | T9 | 简单 | T8 | latex_compile, bash_run | 8-10h | P3 | bundle.zip + migration_report |

**总代码量**: 约900行Python (9个agent类)

**关键依赖链**:
```
T1 → T2 → T3 → T3.5 → T4 → T5 → T6 → T7 → T7.5 → T8 → T9
     └─────────┘       └──────────┘     └──────┘
      文献链           实验链           写作链
```

## 1.2 难度评级详解

### 简单 (8-12h)
- **特征**: 工具少、逻辑直接、无复杂状态管理
- **Agent**: T1 PI, T6 Novelty, T9 Submission
- **风险**: 低，主要是prompt设计和schema校验

### 中等 (12-20h)
- **特征**: 需要外部API集成、多步骤流程、中等复杂度校验
- **Agent**: T2 Scout, T3 Reader, T4 Ideation, T8 Reviewer
- **风险**: 中，主要是API稳定性和去重逻辑

### 困难 (20-32h)
- **特征**: Docker执行、代码生成、迭代逻辑、大规模文档处理
- **Agent**: T5/T7 Experimenter, T8 Writer
- **风险**: 高，涉及隔离执行、结果解析、LaTeX生成

## 1.3 依赖关系图

```
T1 (PI-init)
  ↓ project.yaml + seed_papers.jsonl
T2 (Scout)
  ↓ papers_dedup.jsonl
T3 (Reader-read)
  ↓ paper_notes/*.md
T3.5 (Reader-synthesize)
  ↓ synthesis.md
T4 (Ideation)
  ↓ hypotheses.md + exp_plan.yaml
T5 (Experimenter-pilot)
  ↓ pilot_results.json
T6 (Novelty)
  ↓ novelty_report.md + must_add_baselines.md
T7 (Experimenter-full)
  ↓ results_summary.json + iteration_log.md
T7.5 (PI-evaluate)
  ↓ evaluation_decision.md
T8 (Writer + Reviewer)
  ↓ paper.tex + reviews/*.md
T9 (Submission)
  ↓ submission/bundle.zip
```

## 1.4 技术要点矩阵

| Agent | 核心技术挑战 | 必需工具 | 可选工具 | Schema依赖 |
|-------|-------------|----------|----------|-----------|
| T1 | 三轮对话设计 | ask_human | - | project.schema.json |
| T2 | MCP集成 + 去重算法 | search_papers, mcp_* | fetch_paper_metadata | papers_raw.schema.json |
| T3 | PDF解析 + 批量处理 | extract_paper_sections | web_fetch | - |
| T4 | 深度推理 + 两轮Gate | ask_human | - | exp_plan.schema.json |
| T5/T7 | Docker隔离 + 结果解析 | docker_exec | grep_search, glob_files | pilot_plan.schema.json, run_record.schema.json |
| T6 | 文献对比 + 新颖性判断 | search_papers | - | - |
| T8 | LaTeX生成 + 跨provider审查 | read_file, write_file | latex_compile | - |
| T9 | 文件打包 + 格式迁移 | bash_run, latex_compile | - | - |

---

# 2. T1 PI Agent详细分析

## 2.1 业务需求

### T1模式 (init)
**目标**: 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据

**输入**:
- 用户提供的研究方向描述 (通过CLI `--topic`)
- 可选的初步想法和约束

**输出**:
- `workspace/project.yaml`: 项目配置 (方向、关键词、目标会议、计算预算)
- `user_seeds/seed_papers.jsonl`: 用户提供的种子论文 (每行一条，含role标注)
- `user_seeds/seed_ideas.md`: 用户的初步想法
- `user_seeds/seed_constraints.md`: 硬约束清单

**三轮对话流程**:
1. **第1轮 - Scope与约束**: 明确研究边界、计算预算、目标会议
2. **第2轮 - 已有基础**: 收集已读论文、初步想法、必须遵守的约束
3. **第3轮 - 确认与生成**: 展示草案，用户确认后写入文件

### T7.5模式 (evaluate)
**目标**: 评估实验结果，决定后续路径

**输入**:
- `experiments/results_summary.json`: 实验结果汇总
- `experiments/iteration_log.md`: 迭代日志
- `ideation/exp_plan.yaml`: 原始实验计划

**输出**:
- `evaluation/evaluation_decision.md`: 包含Situation判定和Options建议

**四种Situation**:
- A: 全面达标 → 推进T8
- B: 部分达标 → 可选继续T7或推进T8
- C: 有意外发现 → 可能回T4重构
- D: 完全失败 → 回T4换方向或终止

## 2.2 输入输出契约

```python
# T1 模式
INPUTS = {
    "user_topic": "string (from CLI)",
    "workspace": "empty directory"
}

OUTPUTS = {
    "project.yaml": "workspace/project.yaml",
    "seed_papers": "user_seeds/seed_papers.jsonl",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md"
}

# T7.5 模式
INPUTS = {
    "results_summary": "experiments/results_summary.json",
    "iteration_log": "experiments/iteration_log.md",
    "exp_plan": "ideation/exp_plan.yaml"
}

OUTPUTS = {
    "evaluation_decision": "evaluation/evaluation_decision.md"
}
```

## 2.3 工具清单

| 工具名 | 用途 | 调用频率 | 关键参数 |
|--------|------|----------|----------|
| `read_file` | 读取已有配置/结果 | T7.5高频 | path |
| `write_file` | 写入project.yaml和seed文件 | 3-5次 | path, content |
| `ask_human` | 三轮对话交互 | 3次(T1) / 0-1次(T7.5) | question, options |
| `finish_task` | 完成任务 | 1次 | - |

**不需要的工具**: search_papers, docker_exec, bash_run (PI不搜索、不跑代码)

## 2.4 Prompt模板设计要点

### 核心结构
```jinja2
{% if mode == "init" %}
  [T1 三轮对话流程]
  - 第1轮: Scope与约束
  - 第2轮: 已有基础
  - 第3轮: 确认与生成
  
  [产出规格]
  - project.yaml schema说明
  - seed文件格式要求
  
{% elif mode == "evaluate" %}
  [T7.5 评估流程]
  - 读取results_summary和iteration_log
  - 判断4种Situation
  - 给出2-3个Options
  
  [输出规格]
  - evaluation_decision.md格式
{% endif %}
```

### 关键设计点
1. **温度设置**: T1用0.3 (稳定对话), T7.5用0.2 (严肃评估)
2. **ask_human策略**: 每轮明确告知用户当前进度和下一步
3. **Schema强制**: prompt中明确列出project.yaml的必需字段
4. **容错处理**: 用户不愿多答时简化流程，但必需字段不能省

### 示例prompt片段
```
## 第1轮: Scope与约束

请向用户提问以下内容:
1. 研究问题的具体边界? (不是"做NLP"，是"discrete diffusion LM的factorized gap")
2. 有什么硬约束? (计算预算、GPU数量、时限)
3. 目标会议/期刊?

用 `ask_human` 工具一次性提问，格式:
{
  "question": "...",
  "options": ["继续", "跳过此轮"]
}
```

## 2.5 validate_outputs实现要点

```python
def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
    mode = ctx.mode or "init"
    ok, err = super().validate_outputs(ctx)  # 先检查文件存在
    if not ok:
        return False, err
    
    if mode == "init":
        # 1. project.yaml必须合schema
        project_data = yaml.safe_load((ctx.workspace_dir / "project.yaml").read_text())
        ok, err = validate_against_schema(project_data, "project")
        if not ok:
            return False, f"project.yaml schema不合: {err}"
        
        # 2. 三个seed文件必须存在(即使为空)
        seed_dir = ctx.workspace_dir / "user_seeds"
        for fname in ("seed_papers.jsonl", "seed_ideas.md", "seed_constraints.md"):
            if not (seed_dir / fname).exists():
                return False, f"Missing seed file: user_seeds/{fname}"
        
        return True, None
    
    elif mode == "evaluate":
        # evaluation_decision.md必须含Situation判定
        decision_path = ctx.workspace_dir / "evaluation" / "evaluation_decision.md"
        text = decision_path.read_text()
        if "Situation" not in text:
            return False, "evaluation_decision.md必须含'Situation'标题"
        if not any(word in text for word in ("Option 1", "next_task")):
            return False, "evaluation_decision.md必须给出后续建议"
        return True, None
```

**关键校验点**:
1. Schema级: project.yaml符合schemas/project.schema.json
2. 文件级: 三个seed文件存在
3. 内容级: evaluation_decision.md包含必需章节

## 2.6 测试策略

### Level 1: 单元测试 (MockLLMClient)
```python
def test_pi_agent_init_mode():
    """测试T1模式的基本流程"""
    agent = PIAgent()
    ctx = ExecutionContext(
        workspace_dir=Path("/tmp/test-pi"),
        project_id="test",
        task_id="T1",
        run_id="test-run",
        inputs={},
        outputs_expected={"project": Path("project.yaml")},
        mode="init",
        extra={"user_topic": "factorized gap in discrete diffusion"}
    )
    
    # Mock LLM返回预设的对话和文件写入
    mock_llm = MockLLMClient(script=[
        {"tool": "ask_human", "response": "用户回答..."},
        {"tool": "write_file", "path": "project.yaml", "content": "..."},
        {"tool": "finish_task"}
    ])
    
    runner = AgentRunner(agent, tool_registry, mock_llm, mock_human)
    result = await runner.run(ctx)
    
    assert result.ok
    assert (ctx.workspace_dir / "project.yaml").exists()
```

### Level 2: 集成测试 (真实workspace)
```bash
# 准备空workspace
mkdir -p /tmp/test-pi-integration

# 运行T1
researchos run-task T1 \
  --workspace /tmp/test-pi-integration \
  --topic "discrete diffusion language models"

# 验证输出
ls /tmp/test-pi-integration/project.yaml
ls /tmp/test-pi-integration/user_seeds/
```

### Level 3: 端到端测试 (真实LLM)
- 用真实研究方向跑T1，检查三轮对话是否自然
- 检查project.yaml是否合理 (关键词、预算、目标会议)
- 验证seed文件格式正确

## 2.7 预期难点与解决方案

| 难点 | 描述 | 解决方案 |
|------|------|----------|
| 对话轮次控制 | LLM可能一次问太多或太少 | Prompt中明确"分三轮"，每轮用ask_human强制断点 |
| Schema校验失败 | project.yaml格式不对 | Prompt中给出完整schema示例，validate时给详细错误信息 |
| 用户不配合 | 用户跳过某些问题 | 允许简化流程，但必需字段用默认值填充 |
| T7.5判断不准 | Situation判定主观 | Prompt中给出量化标准 (如"核心指标达标率>80%为A") |

## 2.8 开发检查清单

- [ ] AgentSpec配置正确 (model_tier=heavy, temperature=0.3/0.2)
- [ ] tool_names只包含必需工具
- [ ] allowed_write_prefixes限制为["", "user_seeds/", "evaluation/"]
- [ ] prompt模板按mode分支
- [ ] validate_outputs先调super()再做业务校验
- [ ] 单测覆盖init和evaluate两种模式
- [ ] 集成测试用真实workspace跑通
- [ ] 代码量≤120行

---

# 3. T2 Scout Agent详细分析

## 3.1 业务需求

**目标**: 基于project.yaml的研究方向和用户seed papers，跨多源检索学术论文，产出30-80篇去重后的论文池

**核心任务**:
1. 处理用户提供的seed papers (role=anchor的要追踪引用)
2. 生成5-10条检索式 (覆盖broad概念 + specific术语)
3. 跨源检索 (Semantic Scholar + arXiv)
4. 去重 (DOI精确匹配 + 标题相似度≥0.9)
5. 打分与分类 (source_type / year / citation_count / relevance)

**输入**:
- `project.yaml`: 研究方向、关键词
- `user_seeds/seed_papers.jsonl`: 用户提供的种子论文 (可选)
- `user_seeds/seed_constraints.md`: 检索约束 (可选)

**输出**:
- `literature/papers_raw.jsonl`: 原始检索结果 (所有来源)
- `literature/papers_dedup.jsonl`: 去重后论文池 (30-80篇)
- `literature/search_log.md`: 检索审计日志
- `literature/missing_areas.md`: 缺口分析

## 3.2 输入输出契约

```python
INPUTS = {
    "project": "workspace/project.yaml",
    "seed_papers": "user_seeds/seed_papers.jsonl",  # 可选
    "seed_constraints": "user_seeds/seed_constraints.md"  # 可选
}

OUTPUTS = {
    "papers_raw": "literature/papers_raw.jsonl",
    "papers_dedup": "literature/papers_dedup.jsonl",
    "search_log": "literature/search_log.md",
    "missing_areas": "literature/missing_areas.md"
}

# papers_raw/dedup schema
{
    "id": "s2:abc123 | arxiv:2401.12345",
    "title": "Paper Title",
    "authors": [{"name": "Author Name"}],
    "year": 2024,
    "venue": "NeurIPS",
    "source_type": "top_conference | preprint | journal | blog",
    "relevance_score": 0.85,
    "why_relevant": "解释为什么相关",
    "abstract": "...",
    "doi": "10.xxx/...",
    "citation_count": 123
}
```

## 3.3 工具清单

| 工具名 | 用途 | 优先级 | 关键参数 |
|--------|------|--------|----------|
| `mcp_semantic_scholar_search` | S2检索 (MCP) | 最高 | query, year_from, year_to, limit |
| `mcp_arxiv_search` | arXiv检索 (MCP) | 高 | query, max_results |
| `search_papers` | 降级方案 (直接HTTP) | 中 | query, source, max_results |
| `fetch_paper_metadata` | 单篇完整元数据 | 中 | id (DOI/arXiv/S2) |
| `mcp_semantic_scholar_get_paper` | 获取引用关系 | 中 | paper_id |
| `read_file` | 读project.yaml和seed | 高 | path |
| `write_file` | 写输出文件 | 高 | path, content |
| `finish_task` | 完成任务 | 必需 | - |

**工具使用策略**:
1. 优先用MCP工具 (功能最全、速率限制友好)
2. MCP失败时降级到search_papers
3. 对role=anchor的seed paper用get_paper获取引用关系

## 3.4 Prompt模板设计要点

### 核心流程
```jinja2
你是ResearchOS的Scout Agent (文献侦察员)。

## 项目
- 方向: {{ project.direction }}
- 关键词: {{ project.keywords | join(', ') }}
- 目标会议: {{ project.target_venue }}

{% if seed_paper_count > 0 %}
## 用户提供的Seed Papers ({{ seed_paper_count }}篇)
{% for p in seed_papers[:10] %}
- [{{ p.role }}] {{ p.title }} ({{ p.get('year', '?') }})
{% endfor %}
{% endif %}

## 你的任务

### Step 1: 处理Seed Papers
- role=anchor: 用mcp_semantic_scholar_get_paper获取引用关系
- role=reference: 只标记相关性

### Step 2: 生成5-10条检索式
覆盖: broad概念 + specific术语 + 同义词 + recent限定

### Step 3: 执行检索
**优先用MCP**: mcp_semantic_scholar_search 或 mcp_arxiv_search
**MCP失败降级**: 用search_papers工具

### Step 4: 去重 + 打分
- 去重: DOI精确匹配 + 标题相似度≥0.9
- 打分: source_type / year / citation_count / relevance

### Step 5: 产出
- papers_raw.jsonl (所有结果)
- papers_dedup.jsonl (30-80篇)
- search_log.md (审计日志)
- missing_areas.md (缺口分析)
```

### 关键约束
```
## 规则
- 不编造论文 (title/authors/venue/year必须来自真实搜索)
- source_type准确: top_conference / preprint / journal / blog
- 最终papers_dedup控制在30-80篇
- 少于15篇 → 扩大检索式重试
- 多于120篇 → 按relevance取top 80
- 完成后调finish_task
```

## 3.5 去重逻辑设计

### 两阶段去重
```python
def deduplicate_papers(papers: list[dict]) -> list[dict]:
    """两阶段去重: DOI精确匹配 + 标题相似度"""
    
    # 阶段1: DOI精确去重
    seen_dois = set()
    stage1 = []
    for p in papers:
        doi = p.get("doi", "").strip().lower()
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        stage1.append(p)
    
    # 阶段2: 标题相似度去重 (≥0.9视为重复)
    from difflib import SequenceMatcher
    stage2 = []
    seen_titles = []
    
    for p in stage1:
        title = p.get("title", "").strip().lower()
        is_dup = False
        for seen in seen_titles:
            sim = SequenceMatcher(None, title, seen).ratio()
            if sim >= 0.9:
                is_dup = True
                break
        if not is_dup:
            stage2.append(p)
            seen_titles.append(title)
    
    return stage2
```

### 去重日志
在search_log.md中记录:
```markdown
## Dedup
- raw: 156
- after DOI dedup: 134
- after title similarity: 72
- final (after relevance filter): 65
```

## 3.6 validate_outputs实现要点

```python
def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
    ok, err = super().validate_outputs(ctx)
    if not ok:
        return False, err
    
    # 1. papers_dedup数量检查
    dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
    ok, err = validate_jsonl_schema(
        dedup_path, "papers_raw",
        min_count=15,    # 低于15说明检索有问题
        max_count=120,   # 太多说明没按relevance裁剪
    )
    if not ok:
        return False, err
    
    # 2. 去重效果检查 (dedup < raw)
    raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
    raw_count = len(load_jsonl(raw_path))
    dedup_count = len(load_jsonl(dedup_path))
    if dedup_count > raw_count:
        return False, f"papers_dedup({dedup_count}) > papers_raw({raw_count}), 去重异常"
    
    # 3. Schema校验 (每行符合papers_raw.schema.json)
    for i, record in enumerate(load_jsonl(dedup_path)):
        required_fields = ["id", "title", "year", "authors", "relevance_score"]
        for field in required_fields:
            if field not in record:
                return False, f"papers_dedup第{i+1}行缺少字段: {field}"
    
    return True, None
```

**关键校验点**:
1. 数量级: 15-120篇
2. 去重有效性: dedup < raw
3. Schema完整性: 每行包含必需字段
4. 内容真实性: 不编造论文 (通过source_type和venue交叉验证)

## 3.7 测试策略

### Level 1: Mock测试
```python
def test_scout_agent_basic():
    """测试Scout的基本流程 (Mock LLM)"""
    agent = ScoutAgent()
    ctx = ExecutionContext(
        workspace_dir=Path("/tmp/test-scout"),
        project_id="test",
        task_id="T2",
        run_id="test-run",
        inputs={"project": Path("project.yaml")},
        outputs_expected={"papers_dedup": Path("literature/papers_dedup.jsonl")}
    )
    
    # Mock返回预设的搜索结果
    mock_llm = MockLLMClient(script=[
        {"tool": "read_file", "path": "project.yaml"},
        {"tool": "mcp_semantic_scholar_search", "results": [...]},
        {"tool": "write_file", "path": "literature/papers_raw.jsonl"},
        {"tool": "write_file", "path": "literature/papers_dedup.jsonl"},
        {"tool": "finish_task"}
    ])
    
    result = await runner.run(ctx)
    assert result.ok
    assert 15 <= len(load_jsonl(ctx.workspace_dir / "literature/papers_dedup.jsonl")) <= 120
```

### Level 2: 真实API测试 (小规模)
```bash
# 准备workspace
mkdir -p /tmp/test-scout-real
cat > /tmp/test-scout-real/project.yaml <<EOF
project_id: test-scout
direction: "factorized gap in discrete diffusion language models"
keywords: ["discrete diffusion", "language model", "factorized"]
target_venue: "NeurIPS"
EOF

# 运行T2
researchos run-task T2 --workspace /tmp/test-scout-real

# 验证输出
cat /tmp/test-scout-real/literature/papers_dedup.jsonl | wc -l  # 应该15-120
cat /tmp/test-scout-real/literature/search_log.md
```

### Level 3: 完整测试 (真实研究方向)
- 用真实研究方向跑，检查检索覆盖度
- 验证去重效果 (DOI + 标题相似度)
- 检查relevance_score是否合理
- 验证source_type分布 (top_conference应占主要部分)

## 3.8 预期难点与解决方案

| 难点 | 描述 | 解决方案 |
|------|------|----------|
| MCP不稳定 | MCP server可能挂掉或限流 | 实现降级逻辑，优先MCP失败后用search_papers |
| 去重算法慢 | 标题相似度O(n²)复杂度 | 限制raw数量<500，或用LSH加速 |
| Relevance打分主观 | LLM对相关性判断不一致 | Prompt中给出量化标准 (关键词匹配度、venue质量) |
| 检索覆盖不全 | 某些子方向论文太少 | missing_areas.md标注缺口，传递给T3 |
| API配额耗尽 | S2 API有速率限制 | 实现exponential backoff重试，或用API key |

## 3.9 开发检查清单

- [ ] AgentSpec配置正确 (model_tier=medium, temperature=0.5)
- [ ] tool_names包含MCP工具和降级工具
- [ ] allowed_write_prefixes限制为["literature/"]
- [ ] prompt模板明确MCP优先策略
- [ ] 去重逻辑实现两阶段 (DOI + 标题)
- [ ] validate_outputs检查数量和去重效果
- [ ] 单测覆盖Mock场景
- [ ] 集成测试用真实API跑通
- [ ] 代码量≤80行

---

# 4. T3-T9 Agent概览

## 4.1 T3/T3.5 Reader Agent

**难度**: 中等 (16-20h)

**核心挑战**:
- 批量处理30-80篇论文 (避免context爆炸)
- PDF解析 (extract_paper_sections工具)
- 两种模式共用一个类 (mode="read" vs "synthesize")

**关键工具**:
- `extract_paper_sections`: PDF按section切分
- `read_file` / `write_file`: 读写paper_notes
- `glob_files`: 扫描已完成的notes

**输出**:
- T3: `literature/paper_notes/{id}.md` (每篇一个)
- T3: `literature/comparison_table.csv`
- T3: `literature/related_work.bib`
- T3.5: `literature/synthesis.md` (5个必需章节)

**优化策略**:
- 每篇读完立即写note，不要全部读完再统一写
- extract_paper_sections每section限制3000字符
- Runtime的context裁剪会自动处理旧消息

## 4.2 T4 Ideation Agent

**难度**: 中等 (10-14h)

**核心挑战**:
- 深度推理 (需要deep_reasoning profile)
- 两轮Gate交互 (T4-DECIDE-1选方向, T4-DECIDE-2确认计划)
- exp_plan.yaml的hypothesis_ref必须指向有效anchor

**关键工具**:
- `read_file`: 读synthesis.md和seed_ideas.md
- `write_file`: 写hypotheses.md, exp_plan.yaml, risks.md
- `ask_human`: 两轮Gate交互

**输出**:
- `ideation/hypotheses.md`: 假设描述 (用## H1, ## H2标记anchor)
- `ideation/exp_plan.yaml`: 实验计划 (符合exp_plan.schema.json)
- `ideation/risks.md`: 风险清单 (至少3条)

**配置要点**:
- model_tier=heavy, llm_profile=deep_reasoning
- temperature=0.75 (鼓励divergence)
- max_tokens_total=200_000

**validate重点**:
- hypotheses.md必须有H1/H2等anchor
- exp_plan.yaml的hypothesis_ref必须指向存在的anchor
- risks.md至少列3条风险

## 4.3 T5/T7 Experimenter Agent

**难度**: 困难 (24-32h)

**核心挑战**:
- Docker隔离执行 (docker_exec工具)
- 代码生成 + 结果解析
- 迭代逻辑 (T7最多5轮)
- GPU预算管理

**关键工具**:
- `docker_exec`: 核心，所有训练/推理必须走这个
- `bash_run`: 仅用于文件操作 (mv/cp/mkdir)
- `grep_search` / `glob_files`: 查找代码和结果
- `read_file` / `write_file`: 读写实验配置和结果

**两种模式**:
- T5 pilot: 小规模验证 (数据5-10%, budget<2 GPU-h)
- T7 full: 正式实验 (最多5轮迭代)

**输出**:
- T5: `pilot/pilot_plan.yaml`, `pilot/pilot_results.json`, `pilot/code/`
- T7: `experiments/runs/{run_id}/`, `experiments/results_summary.json`, `experiments/iteration_log.md`

**关键约束**:
- 所有训练代码必须固定seed
- 结果必须写文件 (stdout会被截断64KB)
- 首次docker_exec需人工批准
- GPU使用需project.yaml允许

**风险点**:
- Docker镜像不存在或版本不对
- 代码生成有bug导致实验失败
- 结果解析错误
- 预算超支

## 4.4 T6 Novelty Agent

**难度**: 简单 (8-10h)

**核心挑战**:
- 文献对比 (pilot结果 vs 已有工作)
- 新颖性判断 (需要搜索最新论文)

**关键工具**:
- `search_papers`: 搜索最新相关工作
- `read_file`: 读pilot_results.json和synthesis.md
- `write_file`: 写novelty_report.md和must_add_baselines.md

**输出**:
- `novelty/novelty_report.md`: 新颖性分析
- `novelty/must_add_baselines.md`: 必须补充的baseline清单

**validate重点**:
- novelty_report.md必须有明确的新颖性判断
- must_add_baselines.md列出具体baseline (如果需要)

## 4.5 T8 Writer + Reviewer Agent

**难度**: 困难 (Writer 20-24h, Reviewer 12-16h)

**Writer核心挑战**:
- LaTeX生成 (结构、引用、图表)
- 四种模式 (outline → draft → revise → final)
- 大规模文档处理

**Reviewer核心挑战**:
- 跨provider审查 (audit profile，防止Claude自审)
- 对抗性评审 (找问题而非夸奖)

**关键工具**:
- `read_file` / `write_file`: 读写LaTeX和review
- `latex_compile`: 编译检查 (T9也用)
- `ask_human`: Reviewer可能需要人工确认

**输出**:
- Writer: `drafts/paper.tex`, `drafts/sections/*.tex`, `drafts/figures/`
- Reviewer: `reviews/review_rounds/round_{n}.md`

**关键约束**:
- Writer必须生成可编译的LaTeX
- Reviewer必须用audit profile (跨provider)
- 引用必须来自related_work.bib

## 4.6 T9 Submission Agent

**难度**: 简单 (8-10h)

**核心挑战**:
- 文件打包 (bundle.zip)
- 格式迁移 (不同会议的LaTeX模板)
- 最终编译检查

**关键工具**:
- `latex_compile`: 最终编译
- `bash_run`: 打包和文件操作
- `read_file` / `write_file`: 读写配置和报告

**输出**:
- `submission/bundle.zip`: 投稿包 (paper.tex + figures + bib)
- `submission/migration_report.md`: 格式迁移报告
- `submission/checklist.md`: 投稿检查清单

**validate重点**:
- bundle.zip存在且包含必需文件
- paper.tex可编译通过
- migration_report.md记录了所有格式调整

---

# 5. 开发建议与路线图

## 5.1 推荐开发顺序

### Phase 1: 基础链 (Week 2-3)
**目标**: T1-T3能跑，建立开发模式

```
Week 2:
  Day 1-2: T1 PI Agent (init模式)
  Day 3-4: T2 Scout Agent (含MCP集成)
  Day 5: 集成测试 T1→T2

Week 3:
  Day 1-3: T3 Reader Agent (read模式)
  Day 4: T3.5 Reader Agent (synthesize模式)
  Day 5: 集成测试 T1→T2→T3→T3.5
```

**验收标准**:
- T1能产出合规的project.yaml
- T2能检索到30-80篇论文
- T3能为每篇论文生成paper_note
- T3.5能产出synthesis.md

### Phase 2: 实验链 (Week 4-5)
**目标**: T4-T7能跑，核心实验能力就位

```
Week 4:
  Day 1-2: T4 Ideation Agent
  Day 3-5: T5 Experimenter Agent (pilot模式)

Week 5:
  Day 1-2: T6 Novelty Agent
  Day 3-5: T7 Experimenter Agent (full模式)
```

**验收标准**:
- T4能产出exp_plan.yaml
- T5能在Docker里跑通pilot实验
- T6能判断新颖性
- T7能迭代优化实验

**关键里程碑**: T5跑通是最大风险点，必须优先攻克

### Phase 3: 写作链 (Week 6)
**目标**: T7.5-T9能跑，端到端完整

```
Week 6:
  Day 1: T7.5 PI Agent (evaluate模式)
  Day 2-3: T8 Writer Agent (4种模式)
  Day 4: T8 Reviewer Agent
  Day 5: T9 Submission Agent
```

**验收标准**:
- T7.5能做出合理的Situation判断
- T8能生成可编译的paper.tex
- T8 Reviewer能给出对抗性评审
- T9能打包投稿bundle

### Phase 4: 端到端 (Week 7-8)
**目标**: 完整pipeline跑通，两种模式都能用

```
Week 7:
  Day 1-2: 完整FSM配置和Gate配置
  Day 3-4: 两种运行模式 (Complete Pipeline + Single Task)
  Day 5: CI集成测试

Week 8:
  Day 1-5: 真实研究方向端到端测试
```

**验收标准**:
- `researchos run --topic "..."` 能跑完T1-T9
- `researchos run-task Tn` 能单独跑任意task
- 所有schema validator通过
- Docker镜像固化版本

## 5.2 并行开发策略 (3人团队)

### 角色分工

**Co-A (Runtime Owner)**:
- Week 1-2: Runtime框架完善
- Week 3+: 支持agent开发，处理runtime bug

**Co-B (Agent Owner)**:
- Week 2-3: T1, T2, T3
- Week 4-5: T4, T5, T6, T7
- Week 6: T7.5, T8, T9

**Co-C (Tool/Infra Owner)**:
- Week 1-2: Docker镜像, search_papers, docker_exec
- Week 3: extract_paper_sections, MCP接入
- Week 4-5: GatePresenter, hook框架
- Week 6: latex_compile, schema完善

### 并行任务分配

```
Week 2:
  Co-A: Runtime §7-11 (LLMClient, Tool, Message)
  Co-B: T1 PI Agent
  Co-C: search_papers + docker_exec工具

Week 3:
  Co-A: Runtime §13 (StateMachine)
  Co-B: T2 Scout + T3 Reader
  Co-C: extract_paper_sections + MCP adapter

Week 4:
  Co-A: Runtime §12 (Trace) + §14 (Testing)
  Co-B: T3.5 + T4 Ideation
  Co-C: GatePresenter + hook框架

Week 5:
  Co-A: Profile/Endpoint调试
  Co-B: T5/T7 Experimenter (关键!)
  Co-C: Docker镜像固化 + latex_compile
```

## 5.3 每个阶段的验收标准

### M0 (Week 2末) - HelloAgent
- Runtime的HelloAgent能跑 (Runtime §15)
- LLMClient能调用真实API
- Tool注册和调用机制正常

### M1 (Week 8末) - 端到端
- 能在真实研究方向上从`researchos run --topic "..."`到产出drafts/paper.tex
- 两种模式都能跑 (Complete Pipeline + Single Task)
- Schema validator对所有产物跑通
- Docker镜像固化版本，能在另一台机器上reproducibly跑

### M2 (Week 12末) - 对照实验
- 在3-5个不同研究方向跑
- 收集成本、完成率、失败模式数据
- 作为论文证据

### M3 (Week 14末) - 论文+开源
- 论文初稿
- GitHub仓库public

## 5.4 开发自查表 (每个agent完成后)

**代码质量**:
- [ ] Agent类代码≤120行
- [ ] 继承自Agent基类，实现3个必需方法
- [ ] AgentSpec配置正确 (tier, tools, prefixes, temperature)
- [ ] 没有硬编码路径或magic number

**功能完整性**:
- [ ] system_prompt走render_prompt，不手写字符串
- [ ] initial_user_message简短清晰
- [ ] validate_outputs先调super()再做业务校验
- [ ] 与sections_revised的I/O契约100%一致

**测试覆盖**:
- [ ] 单测4条全过 (happy path + 2个边界 + 1个错误)
- [ ] 在tmp_workspace上跑`researchos run-task Tn`成功
- [ ] validate_outputs能抓到明显错误

**安全性**:
- [ ] allowed_write_prefixes没有越权写
- [ ] 不执行用户提供的任意代码 (除非通过docker_exec)
- [ ] 敏感信息不写日志

**文档**:
- [ ] Prompt模板有清晰的注释
- [ ] 复杂逻辑有docstring
- [ ] README.zh-CN.md更新

---

# 6. 风险点与缓解措施

## 6.1 技术风险

### 风险1: Docker执行不稳定
**影响**: T5/T7无法跑实验，整个pipeline卡住

**缓解措施**:
1. 提前准备好Docker镜像 (researchos/python:3.11-ml)
2. 实现完善的错误处理和重试机制
3. 限制容器资源 (memory, timeout)
4. 准备MockDockerExecTool用于测试

**应急方案**: 如果Docker完全不可用，降级到bash_run (但必须明确警告用户风险)

### 风险2: MCP服务不稳定
**影响**: T2/T6无法检索论文

**缓解措施**:
1. 实现降级逻辑 (MCP失败→search_papers)
2. 实现exponential backoff重试
3. 缓存搜索结果
4. 准备备用API (直接调S2/arXiv HTTP API)

**应急方案**: 完全跳过MCP，直接用search_papers

### 风险3: LLM生成代码有bug
**影响**: T5/T7实验失败

**缓解措施**:
1. Prompt中强制要求固定seed
2. 要求代码写入文件而非只打印
3. 实现代码静态检查 (基本语法)
4. 提供代码模板和示例

**应急方案**: 人工修复生成的代码，记录问题模式

### 风险4: Context爆炸
**影响**: T3读80篇论文时token超限

**缓解措施**:
1. 每篇读完立即写note，不累积
2. extract_paper_sections限制每section 3000字符
3. Runtime的context裁剪自动处理
4. 分批处理 (每20篇一批)

**应急方案**: 降低论文数量上限 (80→50)

## 6.2 业务风险

### 风险5: 生成论文质量不达标
**影响**: 无法投稿，项目失败

**缓解措施**:
1. T8 Reviewer用跨provider审查
2. 多轮revise机制
3. 人工Gate确认关键决策
4. 提供论文模板和示例

**应急方案**: 人工大幅修改生成的论文

### 风险6: 预算超支
**影响**: 成本过高，无法大规模使用

**缓解措施**:
1. 每个agent设置max_tokens_total
2. 实现预算监控和告警
3. 优先用cheaper profile
4. 缓存重复调用

**应急方案**: 降级到更便宜的模型 (Sonnet→Haiku)

### 风险7: 用户不配合Gate
**影响**: Pipeline卡住

**缓解措施**:
1. Gate设计简洁，选项清晰
2. 提供默认选项
3. 超时自动选择保守选项
4. 允许跳过非关键Gate

**应急方案**: 完全自动化模式 (跳过所有Gate)

## 6.3 进度风险

### 风险8: T5/T7开发时间超预期
**影响**: 整体进度延误

**缓解措施**:
1. 提前2周开始T5 (Week 4而非Week 5)
2. 准备简化版Experimenter (只跑toy实验)
3. 并行开发其他agent
4. 每日站会同步进度

**应急方案**: 砍掉T7的迭代功能，只保留T5 pilot

### 风险9: 依赖工具未就绪
**影响**: Agent无法开发

**缓解措施**:
1. Co-C提前1周准备工具
2. 提供Mock版本用于agent开发
3. 工具和agent并行开发
4. 明确工具接口契约

**应急方案**: 用简化版工具 (如bash脚本代替docker_exec)

---

# 7. 测试覆盖策略

## 7.1 三层测试金字塔

```
            ┌─────────────────────────┐
            │  End-to-End (手动)        │  ← 每个agent至少1次
            │   真实API + 真LLM         │     发版前1次完整流程
            │   用真实研究方向跑完9 task │
            └───────────┬──────────────┘
                        │
            ┌───────────┴──────────────┐
            │  Integration (CI能跑)     │  ← 每次提交都跑
            │    Mock LLM + Mock tool   │     单task端到端
            │    单task端到端            │
            └───────────┬──────────────┘
                        │
            ┌───────────┴──────────────┐
            │   Unit (每提交都跑)        │  ← 每次提交都跑
            │    pure function单测       │     快速反馈
            │    validator/hook/...     │
            └──────────────────────────┘
```

**CI里必跑**: Unit + Integration (Mock层)，不依赖真API  
**手动跑**: End-to-End，每个agent至少一次，发版前一次完整流程

## 7.2 单元测试 (Unit Tests)

### 测试范围
- Agent类的validate_outputs逻辑
- 共享helper函数 (_common.py)
- Schema validator
- 去重算法
- 文件解析逻辑

### 示例: T2 Scout去重测试
```python
def test_dedup_by_doi():
    """测试DOI精确去重"""
    papers = [
        {"id": "1", "title": "Paper A", "doi": "10.1234/a"},
        {"id": "2", "title": "Paper A", "doi": "10.1234/a"},  # 重复DOI
        {"id": "3", "title": "Paper B", "doi": "10.1234/b"},
    ]
    result = deduplicate_papers(papers)
    assert len(result) == 2
    assert result[0]["id"] == "1"
    assert result[1]["id"] == "3"

def test_dedup_by_title_similarity():
    """测试标题相似度去重"""
    papers = [
        {"id": "1", "title": "Factorized Gap in Discrete Diffusion", "doi": ""},
        {"id": "2", "title": "Factorized Gap in Discrete Diffusion Models", "doi": ""},  # 相似度>0.9
        {"id": "3", "title": "Completely Different Paper", "doi": ""},
    ]
    result = deduplicate_papers(papers)
    assert len(result) == 2
```

### 示例: T1 PI validate测试
```python
def test_pi_validate_missing_seed_file():
    """测试缺少seed文件时validate失败"""
    agent = PIAgent()
    ctx = create_test_context(mode="init")
    
    # 只创建project.yaml，不创建seed文件
    (ctx.workspace_dir / "project.yaml").write_text("project_id: test")
    
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "Missing seed file" in err
```

## 7.3 集成测试 (Integration Tests)

### 测试范围
- 单个agent的完整执行流程 (Mock LLM)
- Agent与Tool的交互
- Agent与Runtime的交互
- 输出文件的生成和校验

### MockLLMClient标准用法
```python
class MockLLMClient:
    """Mock LLM，按脚本返回预设响应"""
    
    def __init__(self, script: list[dict]):
        self.script = script
        self.step = 0
    
    async def complete(self, messages, tools, **kwargs):
        if self.step >= len(self.script):
            # 脚本用完，返回finish_task
            return MockResponse(tool_calls=[{"name": "finish_task"}])
        
        action = self.script[self.step]
        self.step += 1
        
        if action["tool"] == "write_file":
            # 模拟写文件
            return MockResponse(tool_calls=[{
                "name": "write_file",
                "arguments": {"path": action["path"], "content": action["content"]}
            }])
        # ... 其他工具
```

### 示例: T2 Scout集成测试
```python
async def test_scout_integration():
    """T2 Scout完整流程 (Mock)"""
    agent = ScoutAgent()
    workspace = Path("/tmp/test-scout-integration")
    workspace.mkdir(exist_ok=True)
    
    # 准备输入
    (workspace / "project.yaml").write_text("""
project_id: test
direction: "discrete diffusion language models"
keywords: ["discrete diffusion", "language model"]
""")
    
    # Mock LLM脚本
    mock_llm = MockLLMClient(script=[
        {"tool": "read_file", "path": "project.yaml"},
        {"tool": "mcp_semantic_scholar_search", "results": [
            {"id": "s2:1", "title": "Paper 1", "year": 2024, ...},
            {"id": "s2:2", "title": "Paper 2", "year": 2023, ...},
        ]},
        {"tool": "write_file", "path": "literature/papers_raw.jsonl", "content": "..."},
        {"tool": "write_file", "path": "literature/papers_dedup.jsonl", "content": "..."},
        {"tool": "write_file", "path": "literature/search_log.md", "content": "..."},
        {"tool": "finish_task"}
    ])
    
    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="test",
        task_id="T2",
        run_id="test-run",
        inputs={"project": workspace / "project.yaml"},
        outputs_expected={"papers_dedup": workspace / "literature/papers_dedup.jsonl"}
    )
    
    runner = AgentRunner(agent, tool_registry, mock_llm, mock_human)
    result = await runner.run(ctx)
    
    assert result.ok
    assert (workspace / "literature/papers_dedup.jsonl").exists()
    assert 15 <= len(load_jsonl(workspace / "literature/papers_dedup.jsonl")) <= 120
```

## 7.4 端到端测试 (End-to-End Tests)

### 测试范围
- 完整pipeline (T1→T9)
- 真实LLM + 真实API
- 真实研究方向

### 测试用例设计

**用例1: 简单方向 (快速验证)**
- 研究方向: "improving BERT fine-tuning efficiency"
- 预期时间: 2-4小时
- 验收: 产出paper.tex，至少10篇参考文献

**用例2: 中等复杂度**
- 研究方向: "factorized gap in discrete diffusion language models"
- 预期时间: 4-8小时
- 验收: 产出paper.tex，实验结果可复现

**用例3: 高复杂度**
- 研究方向: "multi-modal reasoning with vision-language models"
- 预期时间: 8-12小时
- 验收: 产出paper.tex，包含图表和消融实验

### 端到端测试流程
```bash
# 1. 准备环境
conda activate researchos
export ANTHROPIC_API_KEY=...
export SEMANTIC_SCHOLAR_API_KEY=...

# 2. 运行完整pipeline
researchos run \
  --topic "improving BERT fine-tuning efficiency" \
  --workspace ./test-e2e-bert \
  --profile default

# 3. 监控进度
tail -f ./test-e2e-bert/state.json
tail -f ./test-e2e-bert/trace.jsonl

# 4. 验收
ls ./test-e2e-bert/drafts/paper.tex
pdflatex ./test-e2e-bert/drafts/paper.tex
cat ./test-e2e-bert/experiments/results_summary.json
```

### 失败模式收集
在端到端测试中记录所有失败模式:
- 哪个agent失败
- 失败原因 (API超时、生成错误、校验失败)
- 重试是否成功
- 人工干预情况

## 7.5 测试数据准备

### Mock数据集
```
tests/fixtures/
├── mock_papers.jsonl          # 模拟论文数据
├── mock_project.yaml          # 模拟项目配置
├── mock_synthesis.md          # 模拟综述
├── mock_exp_results.json      # 模拟实验结果
└── mock_paper.tex             # 模拟论文
```

### 真实数据集 (用于端到端)
```
tests/real_cases/
├── case1_bert_finetuning/
│   ├── expected_outputs/      # 预期输出
│   └── README.md              # 用例说明
├── case2_discrete_diffusion/
└── case3_multimodal/
```

## 7.6 CI配置

### GitHub Actions工作流
```yaml
name: Agent Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: conda-incubator/setup-miniconda@v2
        with:
          environment-file: environment.yml
      - name: Run unit tests
        run: pytest tests/unit/ -v
  
  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: conda-incubator/setup-miniconda@v2
      - name: Run integration tests (Mock)
        run: pytest tests/integration/ -v --mock
```

### 测试覆盖率目标
- Unit tests: >80%覆盖率
- Integration tests: 每个agent至少2个测试用例
- End-to-End: 每个agent至少1次真实运行

---

# 8. 附录

## 8.1 Agent开发速查表

| 步骤 | 检查项 | 参考章节 |
|------|--------|----------|
| 1. 读业务规格 | sections_revised §Tn | - |
| 2. 列工具清单 | tool_names + allowed_*_prefixes | §2.2 |
| 3. 写prompt模板 | prompts/{agent}.j2 | §2.4 |
| 4. 写Agent子类 | system_prompt / initial_user_message / validate_outputs | §2.3 |
| 5. 注册agent | agents/__init__.py | - |
| 6. 写单测 | tests/unit/test_{agent}.py | §7.2 |
| 7. 集成测试 | tests/integration/test_{agent}.py | §7.3 |

## 8.2 常见问题

**Q1: Agent要读一个sections_revised里没列的artifact怎么办?**  
A: 先改sections_revised的I/O契约表，对齐后再改agent。I/O契约是source of truth。

**Q2: Agent跑一半想让用户选一下，但不触发FSM Gate怎么做?**  
A: 用agent内的`ask_human` tool。它是agent级交互，不改FSM状态。

**Q3: T3 reader读到一半Ctrl-C了，下次怎么接着读?**  
A: Runtime支持INTERRUPTED→resume。Agent的`initial_user_message`里读`ctx.extra["is_resume"]`，扫workspace里已有的paper_notes，告诉LLM从哪继续。

**Q4: 我想让T4用Opus而不是默认Sonnet怎么配?**  
A: 不改agent代码。在`config/state_machine.yaml`里给T4节点设置llm override。

**Q5: T5 experimenter真的要跑GPU吗？测试怎么办?**  
A: 用`MockDockerExecTool`，按脚本返回假实验结果。CI不真docker。

## 8.3 关键文件清单

```
researchos/
├── agents/
│   ├── __init__.py              # Agent注册表
│   ├── _common.py               # 共享helper
│   ├── pi.py                    # T1/T7.5
│   ├── scout.py                 # T2
│   ├── reader.py                # T3/T3.5
│   ├── ideation.py              # T4
│   ├── experimenter.py          # T5/T7
│   ├── novelty.py               # T6
│   ├── writer.py                # T8.write
│   ├── reviewer.py              # T8.review
│   └── submission.py            # T9
├── prompts/
│   ├── pi.j2
│   ├── scout.j2
│   ├── reader.j2
│   ├── ideation.j2
│   ├── experimenter.j2
│   ├── novelty.j2
│   ├── writer.j2
│   ├── reviewer.j2
│   └── submission.j2
├── schemas/json_schemas/
│   ├── project.schema.json
│   ├── papers_raw.schema.json
│   ├── exp_plan.schema.json
│   ├── pilot_plan.schema.json
│   ├── run_record.schema.json
│   └── results_summary.schema.json
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

## 8.4 参考资源

- **ResearchOS_Agent_Dev_Spec.md**: Agent开发完整规格
- **ResearchOS_Runtime_Dev_Spec.md**: Runtime实现规格
- **ResearchOS_sections_revised.md**: 业务workflow规格
- **config/state_machine.yaml**: FSM配置示例
- **config/gates.yaml**: Gate配置示例

---

**报告结束**

生成时间: 2026-04-19  
总页数: 约1000行  
覆盖范围: T1-T9全部agent的开发策略、顺序、测试和风险管理

