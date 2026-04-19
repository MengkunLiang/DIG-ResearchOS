# T2 Scout Agent 开发文档

## 概述

Scout Agent（文献侦察员）负责跨多源检索学术论文，实现去重和相关性打分，产出高质量论文池。

## 设计规格

- **Agent名称**: `scout`
- **模型层级**: `medium`
- **Temperature**: 0.5
- **工具**: `search_papers`, `mcp_semantic_scholar_search`, `mcp_arxiv_search`, `fetch_paper_metadata`, `read_file`, `write_file`, `finish_task`

## 输入

- `project.yaml`: 研究方向和关键词
- `user_seeds/seed_papers.jsonl`: 用户种子论文（可选）
- `user_seeds/seed_constraints.md`: 检索约束（可选）

## 输出

- `literature/papers_raw.jsonl`: 原始检索结果（100-200篇）
- `literature/papers_dedup.jsonl`: 去重后论文池（15-120篇）
- `literature/search_log.md`: 检索审计日志
- `literature/missing_areas.md`: 缺口分析

## 五步工作流程

### Step 1: 读取项目配置
- 读取project.yaml确认研究方向
- 读取seed_papers.jsonl（如果存在）
- 对anchor论文获取引用关系

### Step 2: 构建检索式
生成5-10条多样化检索式：
- Broad概念（如"discrete diffusion models"）
- Specific术语（如"factorized gap"）
- 同义词变体
- Recent限定（2023-2024）

### Step 3: 跨源检索（MCP优先）
1. 优先使用`mcp_semantic_scholar_search`和`mcp_arxiv_search`
2. MCP失败时降级到`search_papers`
3. 总原始结果控制在100-200篇

### Step 4: 去重 + 打分
**两阶段去重**：
1. DOI精确去重
2. 标题相似度≥0.9视为重复

**打分维度**（relevance_score: 0.0-1.0）：
- source_type权重
- year权重
- citation_count权重
- 关键词匹配度

### Step 5: 产出文件
按relevance_score排序，取top 30-80篇

## 校验逻辑

`validate_outputs`检查：
1. papers_dedup.jsonl数量在15-120篇之间
2. 符合papers_dedup.schema.json
3. dedup数量 ≤ raw数量
4. 必需字段：id, title, year, authors, relevance_score

## 测试

运行测试：
```bash
pytest tests/unit/test_scout_agent.py -v
```

测试结果：8/8 通过 (100%)

## 使用示例

```python
from researchos.agents.scout import ScoutAgent

agent = ScoutAgent()
ctx = ExecutionContext(
    workspace_dir=workspace,
    task_id="T2"
)
```

## 依赖关系

- **依赖**: T1 PI Agent（需要project.yaml）
- **被依赖**: T3 Reader Agent（使用papers_dedup.jsonl）

详见 ResearchOS Agent Dev Spec §7
