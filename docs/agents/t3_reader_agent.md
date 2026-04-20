# T3 Reader Agent 实现文档

## 概述

Reader Agent（深度阅读模式）是ResearchOS pipeline中的第二个Agent，负责逐篇精读论文并产出结构化笔记。它从T2 Scout产出的论文池中读取论文，尝试获取全文PDF，按照11项checklist模板为每篇论文生成详细笔记，同时累积对比表和BibTeX引用库。

**在Pipeline中的位置**: T3（深度阅读阶段）

**代码位置**: 
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/reader.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/reader.j2`（read模式部分）

## 输入

### 必需输入
- `project.yaml`: 项目配置文件（包含研究方向）
- `literature/papers_dedup.jsonl`: T2产出的去重论文池（15-120篇）

### 可选输入
- `literature/pdfs/`: 可能包含部分论文的PDF文件（由用户预先下载或T2阶段下载）

### 输入格式示例

**papers_dedup.jsonl** (每行一个JSON对象):
```json
{"id": "arxiv:2301.12345", "title": "Discrete Diffusion Models", "authors": ["Ada", "Bob"], "year": 2023, "venue": "NeurIPS", "abstract": "...", "relevance_score": 0.85}
```

## 输出

### 产出文件

1. **literature/paper_notes/{id}.md**: 每篇论文的结构化笔记（11项checklist）
2. **literature/comparison_table.csv**: 论文对比表（累积写入）
3. **literature/related_work.bib**: BibTeX引用库（累积写入）

### 输出格式

**paper_notes/{id}.md** (11项checklist):
```markdown
# {title}

- **ID**: {id}
- **Authors**: {authors}
- **Venue**: {venue} ({year})
- **DOI/arXiv**: {doi_or_arxiv}
- **Citations**: {citation_count}
- **Status**: [FULL-TEXT] 或 [ABSTRACT-ONLY]

## 1. Problem & Motivation
{这篇论文要解决什么问题？为什么重要？1-2段}

## 2. Method Overview
{他们的方法是什么？核心技术思路？3-5句话概括}

## 3. Key Results
{主要实验结果，必须包含具体数字}
- {指标1}: {具体数值} (baseline: {数值})
- {指标2}: {具体数值}

## 4. Claims vs Evidence
{论文的主要声明是否有充分证据支持？}
| Claim | Evidence | Strength |
|-------|----------|----------|
| {声明1} | {证据} | Strong/Moderate/Weak |

## 5. Limitations
{论文自己承认的或你发现的局限性}

## 6. Relevance to Our Research
{这篇论文与我们的研究方向的关系}

## 7. Technical Details Worth Noting
{值得记录的技术细节：超参数、数据集、实现技巧等}

## 8. Strengths
{这篇论文做得好的地方}

## 9. Weaknesses / Gaps
{这篇论文的不足之处，可能是我们idea的切入点}

## 10. Key Quotes
{重要的原文引用}
> "..." (页码或章节)

## 11. My Questions
{阅读时产生的疑问，传递给T3.5综合阶段}
```

**comparison_table.csv**:
```csv
id,title,year,venue,method_family,dataset,key_metric,metric_value,baseline_of_ours,relevance_score
arxiv:2301.12345,Efficient Transformers,2023,ICML,Attention Optimization,ImageNet,Top-1 Acc,85.3,Yes,9
```

**related_work.bib**:
```bibtex
@inproceedings{smith2023efficient,
  title={Efficient Transformers for Large-Scale Vision},
  author={Smith, John and Doe, Jane},
  booktitle={International Conference on Machine Learning},
  year={2023},
  pages={1234--1245}
}
```

## Workflow

### 批次处理原则
- **一次处理1篇**，完成一篇再处理下一篇
- 不要并行处理多篇论文，避免context混淆
- 每篇读完立即写note，不要累积在context中

### 单篇论文处理流程

#### Step 1: 读取论文metadata
从 `papers_dedup.jsonl` 读取下一篇论文的metadata（id, title, authors, year, venue, abstract等）

#### Step 2: 尝试获取全文

**ID规范化规则**:
- 论文ID中的特殊字符（如 `:` `/`）在文件名中替换为 `_`
- 例如：`arxiv:2301.12345` → 文件名使用 `arxiv_2301.12345.pdf`
- 例如：`doi:10.1234/5678` → 文件名使用 `doi_10.1234_5678.pdf`

**获取全文步骤**:
1. 优先检查 `literature/pdfs/{normalized_id}.pdf` 是否存在
2. 如果存在，使用 `extract_pdf_text` 提取全文
3. 如果不存在，尝试使用 `fetch_paper_pdf` 下载（传入原始ID，如 `arxiv:2301.12345`）
   - `fetch_paper_pdf` 支持 arXiv ID（如 `arxiv:2301.12345`）
   - 对于其他来源（crossref、europepmc等），如果有URL可以尝试下载，但可能失败
4. 如果都失败，基于abstract和metadata生成笔记，并在笔记中标注 **[ABSTRACT-ONLY]**

#### Step 3: 按模板写笔记
按照11项checklist模板写 `literature/paper_notes/{normalized_id}.md`（注意文件名也要规范化）

#### Step 4: 追加对比表
追加一行到 `literature/comparison_table.csv`（如果文件不存在则先创建表头）

#### Step 5: 追加BibTeX
追加一条到 `literature/related_work.bib`

#### Step 6: 继续下一篇
重复Step 1-5，直到处理完所有论文或达到最小笔记数要求

## 工具使用

### 可用工具列表
- `read_file`: 读取论文池和项目配置
- `write_file`: 写入笔记、对比表、BibTeX
- `append_file`: 追加内容到对比表和BibTeX
- `list_files`: 列出已有笔记和PDF文件
- `fetch_paper_pdf`: 下载论文PDF
- `extract_pdf_text`: 提取PDF文本
- `finish_task`: 完成任务

### 工具使用示例

**fetch_paper_pdf**:
```python
# 下载arXiv论文
fetch_paper_pdf(paper_id="arxiv:2301.12345", output_path="literature/pdfs/arxiv_2301.12345.pdf")
```

**extract_pdf_text**:
```python
# 提取PDF文本
extract_pdf_text(pdf_path="literature/pdfs/arxiv_2301.12345.pdf")
```

**append_file**:
```python
# 追加到对比表
append_file(
    file_path="literature/comparison_table.csv",
    content="arxiv:2301.12345,Efficient Transformers,2023,ICML,Attention Optimization,ImageNet,Top-1 Acc,85.3,Yes,9\n"
)
```

## 关键实现细节

### 1. 11项Checklist详解

每篇笔记必须包含以下11个部分，缺一不可：

1. **Problem & Motivation**: 论文要解决什么问题？为什么重要？（1-2段）
2. **Method Overview**: 核心技术思路（3-5句话概括）
3. **Key Results**: 主要实验结果，**必须包含具体数字**（不能写"显著提升"）
4. **Claims vs Evidence**: 论文的主要声明是否有充分证据支持？（表格形式）
5. **Limitations**: 论文自己承认的或你发现的局限性
6. **Relevance to Our Research**: 与我们研究方向的关系（是baseline？是related work？）
7. **Technical Details Worth Noting**: 值得记录的技术细节（超参数、数据集、实现技巧等）
8. **Strengths**: 论文做得好的地方
9. **Weaknesses / Gaps**: 论文的不足之处，可能是我们idea的切入点
10. **Key Quotes**: 重要的原文引用（带页码或章节）
11. **My Questions**: 阅读时产生的疑问，传递给T3.5综合阶段

### 2. 数字必须具体

**禁止模糊表述**:
- ❌ "显著提升"
- ❌ "大幅改进"
- ❌ "性能更好"

**必须具体数值**:
- ✅ "Top-1 Acc: 85.3% (baseline: 82.1%)"
- ✅ "推理时间: 45ms (baseline: 120ms)"
- ✅ "参数量: 25M (baseline: 88M)"

### 3. ID规范化

论文ID中的特殊字符在文件名中必须替换：
- `:` → `_`
- `/` → `_`
- `.` → `.`（保留）

**示例**:
- `arxiv:2301.12345` → `arxiv_2301.12345.md`
- `doi:10.1234/5678` → `doi_10.1234_5678.md`
- `s2:abc123` → `s2_abc123.md`

### 4. 容错处理

如果个别论文处理失败（PDF损坏、格式异常等）：
1. 记录错误信息
2. 基于abstract生成 [ABSTRACT-ONLY] 笔记
3. 继续处理下一篇
4. 不要因为个别失败而终止整个任务

### 5. 禁止编造数据

如果PDF读不到：
- ✅ 基于abstract写笔记，标注 **[ABSTRACT-ONLY]**
- ❌ 编造实验结果或技术细节

## 配置参数

### AgentSpec 配置

```python
AgentSpec(
    name="reader",
    model_tier="medium",  # 使用中等规模模型
    max_steps=80,  # 最多80步（处理多篇论文）
    max_tokens_total=400_000,  # 总token预算400K
    max_wall_seconds=7200,  # 最长运行2小时
    temperature=0.5,  # 温度0.5（平衡创造性和准确性）
    allowed_read_prefixes=["", "literature/"],  # 允许读取的目录
    allowed_write_prefixes=["literature/"],  # 允许写入的目录
)
```

### 可调整参数

- **最小笔记数**: 动态确定，基于papers_dedup.jsonl的实际数量
  - 至少80%的论文应该有笔记
  - 最少3篇
  - 如果papers_dedup.jsonl有50篇，则至少需要40篇笔记
- **批次大小**: 一次处理1篇（不可调整，避免context混淆）
- **PDF下载超时**: 由 `fetch_paper_pdf` 工具控制
- **PDF提取超时**: 由 `extract_pdf_text` 工具控制

## 常见问题

### Q1: PDF下载失败怎么办？
**A**: 基于abstract和metadata生成笔记，并在Status字段标注 **[ABSTRACT-ONLY]**。不要编造数据。

### Q2: PDF提取的文本格式混乱怎么办？
**A**: 
1. 尽量从混乱的文本中提取关键信息
2. 如果实在无法解析，降级为 [ABSTRACT-ONLY] 模式
3. 在笔记中标注 "PDF格式异常，部分信息可能不准确"

### Q3: 如何判断论文是否与研究方向相关？
**A**: 
1. 参考papers_dedup.jsonl中的 `relevance_score` 和 `why_relevant`
2. 在笔记的 "Relevance to Our Research" 部分详细说明
3. 如果相关性很低，可以写简短笔记但仍需包含11项checklist

### Q4: 如何处理非英文论文？
**A**: 
1. 如果有英文abstract，基于abstract生成笔记
2. 如果完全没有英文信息，标注 "Non-English paper, skipped" 并继续下一篇
3. 不要尝试翻译或猜测内容

### Q5: comparison_table.csv的字段如何填写？
**A**: 
- `id`: 论文ID（规范化后的）
- `title`: 论文标题
- `year`: 发表年份
- `venue`: 发表会议/期刊
- `method_family`: 方法家族（如"Attention Optimization"、"Knowledge Distillation"）
- `dataset`: 主要数据集
- `key_metric`: 关键指标（如"Top-1 Acc"、"BLEU"）
- `metric_value`: 指标数值
- `baseline_of_ours`: 是否可作为我们的baseline（Yes/No）
- `relevance_score`: 相关性评分（1-10）

## 测试方法

### 单元测试
```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
pytest tests/test_agents.py::test_reader_agent -v
```

### 集成测试
```bash
# 使用真实LLM测试
pytest tests/test_t3_reader_real.py -v -s

# 使用mock LLM测试
pytest tests/test_t3_reader_mock.py -v
```

### 手动测试
```bash
# 准备测试数据（需要先运行T2 Scout）
mkdir -p /tmp/test_reader/literature
# 复制papers_dedup.jsonl到/tmp/test_reader/literature/

# 运行Reader Agent
python -m researchos.cli run reader --workspace /tmp/test_reader --mode read

# 检查输出
ls /tmp/test_reader/literature/paper_notes/
cat /tmp/test_reader/literature/comparison_table.csv
cat /tmp/test_reader/literature/related_work.bib
```

### 验证清单
- [ ] paper_notes/ 目录存在且包含至少80%的论文笔记
- [ ] 每篇笔记包含11项checklist
- [ ] 每篇笔记的 Key Results 包含具体数字
- [ ] comparison_table.csv 存在且至少有2行（表头+数据）
- [ ] related_work.bib 存在且包含有效的BibTeX条目
- [ ] 所有笔记的Status字段正确标注 [FULL-TEXT] 或 [ABSTRACT-ONLY]
- [ ] 文件名使用规范化的ID（特殊字符替换为下划线）
- [ ] 无编造数据（所有数字来自论文或标注为ABSTRACT-ONLY）

## 性能指标

- **平均运行时间**: 30-90分钟（取决于论文数量和PDF可用性）
- **Token消耗**: 150K-350K tokens
- **成功率**: >90%（在PDF部分可用的情况下）
- **输出质量**: 每篇笔记包含11项checklist，具体数字，无编造数据

## 已知限制

1. **PDF依赖**: 依赖PDF可用性，部分论文可能只能基于abstract生成笔记
2. **PDF格式**: 某些PDF格式（扫描版、双栏、复杂公式）提取效果差
3. **语言限制**: 仅支持英文论文，非英文论文会跳过
4. **时间消耗**: 处理大量论文（>50篇）可能需要1-2小时
5. **Token消耗**: 处理全文PDF会消耗大量tokens，可能触发预算限制

## 下一步改进方向

1. 支持并行处理（多篇论文同时处理，但需要careful context管理）
2. 改进PDF提取（使用更好的PDF解析库，如PyMuPDF）
3. 支持增量更新（只处理新增论文，不重复处理已有笔记）
4. 支持笔记模板自定义（允许用户自定义checklist）
5. 支持多语言论文（自动翻译abstract）
6. 添加笔记质量评分（自动评估笔记完整性和准确性）
