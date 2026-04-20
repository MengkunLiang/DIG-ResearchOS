# T2 Scout Agent 实现文档

## 概述

Scout Agent（文献侦察员）是ResearchOS pipeline中的第一个Agent，负责跨源检索学术论文并产出去重后的论文池。它基于项目研究方向和用户提供的种子论文，从多个学术数据源检索相关文献，经过两阶段去重和相关性打分，最终产出30-80篇高质量论文供后续阶段使用。

**在Pipeline中的位置**: T2（文献普查阶段）

**代码位置**: 
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/scout.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/scout.j2`

## 输入

### 必需输入
- `project.yaml`: 项目配置文件，包含研究方向、关键词、目标会议等信息

### 可选输入
- `user_seeds/seed_papers.jsonl`: 用户提供的种子论文（JSONL格式，每行一个JSON对象）
- `user_seeds/seed_constraints.md`: 检索约束说明（如时间范围、特定会议等）

### 输入格式示例

**project.yaml**:
```yaml
research_direction: "离散扩散模型在语言生成中的应用"
keywords: ["discrete diffusion", "language models", "factorized gap"]
target_venue: "NeurIPS"
```

**seed_papers.jsonl**:
```json
{"id": "arxiv:2301.12345", "title": "Discrete Diffusion Models", "role": "anchor", "year": 2023}
{"id": "s2:abc123", "title": "Language Generation", "role": "reference", "year": 2024}
```

## 输出

### 产出文件

1. **literature/papers_raw.jsonl**: 原始检索结果（所有API返回的论文）
2. **literature/papers_dedup.jsonl**: 去重后的论文池（15-120篇）
3. **literature/search_log.md**: 检索审计日志
4. **literature/missing_areas.md**: 文献缺口分析

### 输出格式

**papers_raw.jsonl** (每行一个JSON对象):
```json
{"id": "s2:abc123", "source": "semantic_scholar", "title": "...", "authors": [{"name": "Ada"}], "year": 2024, "venue": "NeurIPS", "abstract": "...", "doi": "10.xxx/...", "citation_count": 123}
```

**papers_dedup.jsonl** (每行一个JSON对象):
```json
{"id": "s2:abc123", "source": "semantic_scholar", "title": "...", "authors": ["Ada", "Bob"], "year": 2024, "venue": "NeurIPS", "source_type": "top_conference", "relevance_score": 0.85, "why_relevant": "解决了离散扩散模型的factorized gap问题", "abstract": "...", "citation_count": 123, "url": "https://..."}
```

**必需字段说明**:
- `id`: 论文唯一标识（如"s2:abc123"或"arxiv:2401.00001"）
- `source`: 数据源（"semantic_scholar"、"arxiv"、"crossref"、"europepmc"等）
- `title`: 论文标题
- `authors`: 作者列表（raw中为对象数组，dedup中为字符串数组）
- `year`: 发表年份（整数）
- `venue`: 发表会议/期刊
- `abstract`: 摘要
- `citation_count`: 引用次数
- `relevance_score`: 相关性评分（0.0-1.0，仅dedup）
- `why_relevant`: 相关性说明（仅dedup）
- `source_type`: 来源类型（仅dedup）

## Workflow

### 5步执行流程

#### Step 1: 读取项目配置
- 使用 `read_file` 读取 `project.yaml` 确认研究方向
- 如果存在 `user_seeds/seed_papers.jsonl`，读取完整种子论文列表
- 对于 `role=anchor` 的论文，可使用 MCP 工具获取引用关系（如果MCP已配置）

#### Step 2: 构建检索式（5-10条）
基于研究方向和关键词，生成多样化的检索式：
- **Broad 概念**（如 "discrete diffusion models"）
- **Specific 术语**（如 "factorized gap in language models"）
- **同义词变体**（如 "discrete flow" vs "discrete diffusion"）
- **Recent 限定**（如 "discrete diffusion 2023-2024"）

#### Step 3: 跨源检索
**当前可用工具**:
- `multi_source_search`: 多源论文搜索工具（推荐）
  - 参数: `query`, `max_results`, `sources`（默认["crossref", "arxiv", "europepmc"]）
  - 自动处理速率限制和API失败
  - 返回真实可验证的论文数据
- `search_papers`: 单源检索接口（备用）
  - 参数: `query`, `source`（"semantic_scholar" 或 "arxiv"）, `max_results`

**检索策略**:
1. 优先使用 `multi_source_search`
2. 每条检索式获取20-30篇论文
3. 总原始结果控制在50-200篇
4. 如果某个检索式返回结果少，继续下一个检索式
5. 总结果至少要有20篇才能继续

#### Step 4: 去重 + 打分

**两阶段去重**:
1. **DOI 精确去重**: 相同 DOI 只保留一条
2. **标题相似度去重**: 标题相似度 ≥ 0.9 视为重复（使用 `difflib.SequenceMatcher`）

**打分维度**（relevance_score: 0.0-1.0）:
- `source_type` 权重: top_conference(1.0) > journal(0.8) > preprint(0.6) > blog(0.3)
- `year` 权重: 2024(1.0), 2023(0.9), 2022(0.8), 更早递减
- `citation_count` 权重: >100(1.0), 50-100(0.8), 10-50(0.6), <10(0.4)
- 关键词匹配度: title/abstract 中出现项目关键词的比例

**最终筛选**:
- 按 `relevance_score` 排序
- 取 top 30-80 篇（如果 >120 篇则裁剪到 80）
- 如果 <15 篇，扩大检索式重试

#### Step 5: 产出文件
使用 `write_file` 写入所有输出文件，并调用 `finish_task` 完成任务。

### 去重逻辑伪代码

```python
# 阶段1: DOI精确去重
seen_dois = set()
stage1 = []
for paper in raw_papers:
    doi = paper.get("doi", "").strip().lower()
    if doi and doi in seen_dois:
        continue
    if doi:
        seen_dois.add(doi)
    stage1.append(paper)

# 阶段2: 标题相似度去重
from difflib import SequenceMatcher
stage2 = []
seen_titles = []
for paper in stage1:
    title = paper.get("title", "").strip().lower()
    is_dup = False
    for seen in seen_titles:
        sim = SequenceMatcher(None, title, seen).ratio()
        if sim >= 0.9:
            is_dup = True
            break
    if not is_dup:
        stage2.append(paper)
        seen_titles.append(title)
```

## 工具使用

### 可用工具列表
- `read_file`: 读取项目配置和种子论文
- `write_file`: 写入输出文件
- `multi_source_search`: 多源论文搜索（推荐）
- `search_papers`: 单源论文搜索（备用）
- `fetch_paper_metadata`: 获取论文元数据
- `finish_task`: 完成任务

### 工具使用示例

**multi_source_search**:
```python
multi_source_search(
    query="discrete diffusion language models",
    max_results=30,
    sources=["crossref", "arxiv", "europepmc"]
)
```

**search_papers**:
```python
search_papers(
    query="discrete diffusion",
    source="semantic_scholar",
    max_results=20
)
```

## 关键实现细节

### 1. 数据真实性保证

**🚨 绝对禁止编造论文数据 🚨**

这是最重要的规则，违反将导致严重的研究错误：

1. **所有论文数据必须来自真实API返回**
   - title、authors、venue、year、abstract 等所有字段必须来自 API 返回结果
   - 绝对不允许根据研究主题"推测"或"编造"论文信息
   - 绝对不允许使用看起来合理的arXiv ID但内容是编造的

2. **如果API全部失败，必须让任务失败**
   - 如果所有检索式都失败（429错误、网络错误等），调用 `finish_task` 并说明原因
   - 不要创建 papers_raw.jsonl 和 papers_dedup.jsonl
   - 不要为了满足输出要求而编造数据

3. **数据质量检查**
   - 每篇论文必须有真实的 title（不能是"Paper 1"这种模式）
   - 每篇论文必须有真实的 authors（不能是["Author A"]这种占位符）
   - 如果发现数据看起来是编造的，停止任务并报告问题

### 2. source_type 分类规则

- `top_conference`: NeurIPS, ICML, ICLR, ACL, EMNLP, CVPR, ICCV 等
- `journal`: JMLR, TACL, Nature, Science 等
- `preprint`: arXiv, bioRxiv 等
- `blog`: 技术博客、公司博客

### 3. JSONL格式要求

**重要**: 必须使用JSONL格式（每行一个JSON对象），不要使用JSON数组格式。

**正确示例**:
```
{"id": "paper1", "title": "..."}
{"id": "paper2", "title": "..."}
```

**错误示例**:
```
[
  {"id": "paper1", "title": "..."},
  {"id": "paper2", "title": "..."}
]
```

## 配置参数

### AgentSpec 配置

```python
AgentSpec(
    name="scout",
    model_tier="medium",  # 使用中等规模模型
    max_steps=50,  # 最多50步
    max_tokens_total=200_000,  # 总token预算200K
    max_wall_seconds=1800,  # 最长运行30分钟
    temperature=0.5,  # 温度0.5（平衡创造性和准确性）
    allowed_read_prefixes=["", "user_seeds/"],  # 允许读取的目录
    allowed_write_prefixes=["literature/"],  # 允许写入的目录
)
```

### 可调整参数

- **检索式数量**: 5-10条（在prompt中调整）
- **每条检索式结果数**: 20-30篇（`max_results`参数）
- **最终论文数量**: 30-80篇（在去重后裁剪）
- **标题相似度阈值**: 0.9（在去重逻辑中调整）
- **最小论文数**: 15篇（在validate_outputs中检查）
- **最大论文数**: 120篇（在validate_outputs中检查）

## 常见问题

### Q1: API调用失败怎么办？
**A**: `multi_source_search` 会自动处理API失败和速率限制。如果所有API都失败，任务会失败并报告原因，不会编造数据。

### Q2: 检索结果太少（<15篇）怎么办？
**A**: 
1. 扩大检索式，使用更宽泛的关键词
2. 增加检索式数量
3. 如果仍然不够，让任务失败而不是编造数据

### Q3: 检索结果太多（>120篇）怎么办？
**A**: 按 `relevance_score` 排序后取 top 80 篇。

### Q4: 如何处理MCP工具速率限制？
**A**: 
1. 优先使用 `multi_source_search`（自动处理速率限制）
2. 如遇429错误，等待后重试
3. 记录所有API调用到 `search_log.md` 以便审计

### Q5: 如何验证去重效果？
**A**: 
1. 检查 `papers_dedup.jsonl` 数量 ≤ `papers_raw.jsonl` 数量
2. 检查 `search_log.md` 中的去重统计
3. validate_outputs 会自动检查去重异常

## 测试方法

### 单元测试
```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
pytest tests/test_agents.py::test_scout_agent -v
```

### 集成测试
```bash
# 使用真实LLM测试
pytest tests/test_t2_scout_real.py -v -s

# 使用mock LLM测试
pytest tests/test_t2_scout_mock.py -v
```

### 手动测试
```bash
# 准备测试数据
mkdir -p /tmp/test_scout
echo "research_direction: 'test'" > /tmp/test_scout/project.yaml

# 运行Scout Agent
python -m researchos.cli run scout --workspace /tmp/test_scout

# 检查输出
ls /tmp/test_scout/literature/
cat /tmp/test_scout/literature/search_log.md
```

### 验证清单
- [ ] papers_raw.jsonl 存在且格式正确（JSONL格式）
- [ ] papers_dedup.jsonl 存在且数量在15-120之间
- [ ] papers_dedup.jsonl 包含所有必需字段
- [ ] papers_dedup 数量 ≤ papers_raw 数量
- [ ] search_log.md 包含检索统计和去重统计
- [ ] missing_areas.md 包含缺口分析
- [ ] 所有论文数据来自真实API（无编造数据）
- [ ] relevance_score 在0.0-1.0之间
- [ ] source_type 分类正确

## 性能指标

- **平均运行时间**: 5-15分钟（取决于检索式数量和API响应速度）
- **Token消耗**: 50K-150K tokens
- **成功率**: >95%（在API正常情况下）
- **输出质量**: 15-120篇高相关性论文

## 已知限制

1. **API依赖**: 依赖外部学术API（Crossref、arXiv、Europe PMC等），API故障会导致任务失败
2. **速率限制**: 免费API有速率限制，大量检索可能触发429错误
3. **覆盖范围**: 不同数据源覆盖的领域不同，某些小众领域可能检索结果较少
4. **时效性**: 最新论文（发表<1周）可能尚未被索引
5. **去重精度**: 标题相似度去重可能误判（如同一作者的系列论文）

## 下一步改进方向

1. 支持更多学术数据源（如Google Scholar、DBLP）
2. 改进相关性打分算法（引入语义相似度）
3. 支持引用网络分析（基于seed papers的引用追踪）
4. 优化去重算法（引入作者信息、摘要相似度）
5. 支持增量检索（避免重复检索已有论文）
