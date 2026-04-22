# T2 Scout Agent 问题修复总结（2026-04-22）

## 发现的问题

### 问题 1：abstract 和 doi 字段为空

**现象**：
```json
{"id": "1", "source": "crossref", "title": "...", "abstract": "", "doi": "", ...}
```

**原因**：
- **Crossref API 的限制**：Crossref 主要提供元数据（标题、作者、年份），但不总是提供摘要
- Crossref 的数据质量参差不齐，某些论文记录不完整

**影响**：
- 缺少摘要会影响相关性评分的准确性
- 缺少 DOI 会影响论文的唯一标识和引用

**解决方案**：
1. ✅ 启用 MCP Semantic Scholar 工具（提供更完整的数据）
2. ✅ 优先使用 arXiv 和 Europe PMC（这些源通常提供摘要）
3. 对于缺少摘要的论文，可以使用 `fetch_paper_metadata` 工具补充

---

### 问题 2：检索策略导致高重复率

**现象**：
```
原始结果: 57篇
去重后: 5篇
去重率: 91.23%  ← 严重问题！
```

**原因**：
检索式重复度太高，导致检索到大量相同的论文：
```
1. "AI Agent MemRL memory reinforcement learning long-term"
2. "episodic memory for AI agents"
3. "reinforcement learning with long-term memory"
4. "agent memory retrieval in AI"
```

这些检索式都包含相似的关键词（"memory", "agent", "reinforcement learning"）。

**正常的去重率应该是 50-70%**

**解决方案**：
✅ 修改 prompt，添加检索式设计指南：

**❌ 错误示例（重复度高）**：
```
1. "AI agent memory reinforcement learning"
2. "reinforcement learning with memory"
3. "agent memory retrieval"
4. "memory system for AI agents"
```

**✅ 正确示例（多样化）**：
```
1. "LLM agent memory system"           # 核心概念
2. "episodic memory for AI agents"     # 特定类型
3. "retrieval augmented generation"    # 相关技术
4. "vector database for agents"        # 底层技术
5. "long-term context in LLM"          # 相关问题
6. "agent state persistence"           # 工程实现
7. "memory-augmented neural networks"  # 理论基础
8. "knowledge base for chatbots"       # 应用场景
```

**检索式设计原则**：
- 从不同角度覆盖研究主题（理论、技术、应用）
- 使用同义词和相关概念（不要重复相同关键词）
- 包含上下游技术
- 包含相关领域

---

### 问题 3：MCP 工具未启用

**现象**：
```python
# MCP工具暂时移除，等MCP配置完成后再启用
# "mcp_semantic_scholar_search",
# "mcp_semantic_scholar_get_paper",
```

**影响**：
- 无法使用 Semantic Scholar API（提供更完整的论文数据）
- 数据质量受限于免费 API（Crossref、arXiv、Europe PMC）

**解决方案**：
✅ 启用 MCP 工具：
```python
tool_names=[
    # ...
    "mcp_semantic_scholar_search",
    "mcp_semantic_scholar_get_paper",
]
```

**MCP 工具的优势**：
- 提供完整的摘要
- 提供引用关系
- 提供更准确的元数据
- 更好的搜索相关性

---

### 问题 4：papers_dedup.jsonl 无法生成

**现象**：
```
[Agent] 调用工具: write_structured_file
[Agent 输出] 我发现数据格式仍然不符合要求...
[Agent] 调用工具: write_structured_file
[Agent 输出] 我需要彻底检查数据的格式和字段...
```

Agent 多次尝试写入但都失败。

**原因**：
`papers_dedup.schema.json` 缺少 prompt 中要求的必需字段：
- ❌ 缺少 `source` 字段
- ❌ 缺少 `source_type` 字段
- ❌ 缺少 `why_relevant` 字段
- ❌ `required` 字段列表不完整

**解决方案**：
✅ 修复 `papers_dedup.schema.json`：
```json
{
  "required": [
    "id", "source", "title", "authors", "year", "venue",
    "source_type", "relevance_score", "why_relevant",
    "abstract", "citation_count", "url"
  ],
  "properties": {
    "source": {"type": "string"},
    "source_type": {"type": "string"},
    "why_relevant": {"type": "string"},
    // ...
  }
}
```

---

### 问题 5：papers_raw.schema.json 字段命名不一致

**现象**：
Schema 验证失败，Agent 无法写入 papers_raw.jsonl

**原因**：
- `multi_source_search` 工具返回 `citation_count`（下划线）
- 但 schema 定义的是 `citationCount`（驼峰）

**解决方案**：
✅ 修复 `papers_raw.schema.json`：
```json
{
  "properties": {
    "citation_count": {"type": "integer"},  // 改为下划线
    "doi": {"type": "string"}  // 添加缺失的字段
  }
}
```

---

## 修改的文件

### 1. researchos/schemas/json_schemas/papers_raw.schema.json
**修改内容**：
- `citationCount` → `citation_count`（字段命名统一）
- 添加 `doi` 字段

### 2. researchos/schemas/json_schemas/papers_dedup.schema.json
**修改内容**：
- 添加 `source` 字段（必需）
- 添加 `source_type` 字段（必需）
- 添加 `why_relevant` 字段（必需）
- 更新 `required` 字段列表
- 添加 `additionalProperties: true`（允许额外字段）

### 3. researchos/prompts/scout.j2
**修改内容**：
- 添加检索式设计指南
- 强调避免高重复率（目标 50-70%）
- 提供正确和错误的示例对比
- 说明检索式设计原则

### 4. researchos/agents/scout.py
**修改内容**：
- 启用 MCP 工具：
  - `mcp_semantic_scholar_search`
  - `mcp_semantic_scholar_get_paper`

---

## 验证方法

### 1. 检查 schema 修复

```bash
# 测试 papers_raw schema
python -c "
from researchos.schemas.validator import validate_record
data = {
    'id': 'test', 'source': 'arxiv', 'title': 'Test',
    'authors': [{'name': 'Author'}], 'year': 2024,
    'abstract': 'Test', 'venue': 'arXiv',
    'citation_count': 0, 'doi': '', 'url': ''
}
ok, err = validate_record(data, 'papers_raw')
print('✅ papers_raw schema OK' if ok else f'❌ {err}')
"

# 测试 papers_dedup schema
python -c "
from researchos.schemas.validator import validate_record
data = {
    'id': 'test', 'source': 'arxiv', 'title': 'Test',
    'authors': ['Author'], 'year': 2024, 'venue': 'arXiv',
    'source_type': 'preprint', 'relevance_score': 0.8,
    'why_relevant': 'Test', 'abstract': 'Test',
    'citation_count': 0, 'url': 'https://test.com'
}
ok, err = validate_record(data, 'papers_dedup')
print('✅ papers_dedup schema OK' if ok else f'❌ {err}')
"
```

### 2. 重新运行 T2 Agent

```bash
# 清理旧数据
rm -rf workspace/local-test/literature/*

# 运行 T2
researchos run-task T2 --workspace workspace/local-test

# 检查结果
ls -lh workspace/local-test/literature/
wc -l workspace/local-test/literature/papers_raw.jsonl
wc -l workspace/local-test/literature/papers_dedup.jsonl
cat workspace/local-test/literature/search_log.md
```

### 3. 检查去重率

```bash
# 从 search_log.md 中提取去重率
grep "去重率" workspace/local-test/literature/search_log.md

# 预期：50-70%（不应该 >80%）
```

---

## 预期改进

### 数据质量
- ✅ 更多论文包含摘要（启用 MCP Semantic Scholar）
- ✅ 更准确的元数据（DOI、引用数等）

### 检索效果
- ✅ 去重率降低到 50-70%（从 91% 降低）
- ✅ 更多样化的论文（覆盖不同子领域）
- ✅ 更高的相关性（通过多样化检索式）

### 文件生成
- ✅ papers_raw.jsonl 正确生成
- ✅ papers_dedup.jsonl 正确生成
- ✅ 所有必需字段都存在

---

## 后续优化建议

1. **自动补充缺失的摘要**：
   - 对于 abstract 为空的论文，自动调用 `fetch_paper_metadata` 补充
   - 或者使用 MCP Semantic Scholar 工具获取

2. **检索式质量评估**：
   - 在生成检索式后，自动评估重复度
   - 如果预测去重率 >80%，自动调整检索式

3. **数据源优先级**：
   - 优先使用提供摘要的数据源（Semantic Scholar > arXiv > Europe PMC > Crossref）
   - 对于同一篇论文，合并多个数据源的信息

4. **Token 预算优化**：
   - 当前已增加到 500K，但仍可能不够
   - 考虑在 prompt 中要求 Agent 控制检索数量

---

**文档创建时间**: 2026-04-22  
**ResearchOS 版本**: 0.1.0  
**作者**: Claude Opus 4.7
