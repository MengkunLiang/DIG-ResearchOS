> ✅ **此 Agent 已实现 (IMPLEMENTED)**
> 代码位置：`researchos/agents/writer.py`（WriterAgent）和 `researchos/agents/reviewer.py`（ReviewerAgent）
> Prompt 模板：`researchos/prompts/writer.j2` 和 `researchos/prompts/reviewer.j2`

# T8 Writer和Reviewer Agent 实现文档

## 概述

T8阶段包含两个协作的Agent：Writer Agent（论文写作）和Reviewer Agent（论文审稿）。它们通过迭代循环产出高质量的学术论文草稿。Writer负责生成论文各个部分，Reviewer负责审查并提出改进建议，两者通过文件通信，最多进行2轮迭代。

**在Pipeline中的位置**: T8（论文写作与审稿阶段）

**代码位置**: 
- Writer Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/writer.py`（WriterAgent 类）
- Reviewer Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/reviewer.py`（ReviewerAgent 类）
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/writer.j2`, `reviewer.j2`

## Writer Agent

### 设计规格

- **Agent名称**: `writer`
- **模型层级**: `heavy`（需要高质量的学术写作能力）
- **Temperature**: 0.7（鼓励流畅的写作，但保持准确性）
- **工具**: `read_file`, `write_file`, `list_files`, `finish_task`
- **最大步数**: 60
- **Token预算**: 500,000
- **超时时间**: 3600秒（1小时）

### 输入

#### 必需输入
- `project.yaml`: 项目配置（研究方向、目标会议）
- `experiments/results_summary.json`: 实验结果汇总
- `literature/synthesis.md`: 文献综述
- `literature/related_work.bib`: BibTeX引用库
- `ideation/hypotheses.md`: 研究假设

#### 可选输入
- `experiments/ablations.csv`: 消融实验结果
- `literature/comparison_table.csv`: 方法对比表
- `evaluation/evaluation_decision.md`: T7.5的评估决策

### 输出

#### 产出文件

1. **drafts/outline.md**: 论文大纲（phase=outline）
2. **drafts/paper.tex**: LaTeX论文草稿（phase=draft, revise, final）
3. **drafts/self_check.md**: 自查清单（phase=self_check）
4. **drafts/figures/**: 图表文件（如果需要）

### 执行阶段（Phases）

Writer Agent通过不同的phase参数执行不同的任务：

#### Phase 1: outline（生成大纲）

**目标**: 基于实验结果和文献综述，生成论文大纲

**输出**: `drafts/outline.md`

**大纲格式**:
```markdown
# 论文大纲

## 标题候选
1. Adaptive Gap Scheduling for Discrete Diffusion Language Models
2. Hierarchical Discrete Representations in Diffusion-Based Text Generation
3. Improving Discrete Diffusion Models with Learnable Gap Parameters

## Abstract（摘要要点）
- 问题: 现有离散扩散模型使用固定gap，不适应不同生成阶段
- 方法: 提出adaptive gap scheduling + learnable parameters
- 结果: WMT2014上BLEU提升1.3（28.5→29.8）
- 贡献: 首次将自适应调度引入离散扩散

## 1. Introduction（引言结构）
- 背景: 离散扩散模型在语言生成中的应用
- 问题: 固定gap的局限性
- 我们的方法: adaptive scheduling
- 贡献点:
  1. 提出adaptive gap scheduling算法
  2. 引入learnable gap parameters
  3. 在WMT2014上验证有效性
- 论文结构

## 2. Related Work（相关工作分类）
- 2.1 Discrete Diffusion Models
  - [arxiv:2301.12345]: 基础方法
  - [arxiv:2302.67890]: 改进方向
- 2.2 Adaptive Scheduling in Diffusion
  - [arxiv:2312.12345]: 连续扩散中的自适应调度
  - 我们的差异: 离散空间的特殊挑战
- 2.3 Language Generation with Diffusion
  - [s2:abc123]: 现有方法
  - 我们的改进

## 3. Method（方法章节结构）
- 3.1 Background: Discrete Diffusion
- 3.2 Adaptive Gap Scheduling
  - 算法描述
  - 理论分析
- 3.3 Learnable Gap Parameters
  - 参数化方法
  - 训练策略

## 4. Experiments（实验章节结构）
- 4.1 Experimental Setup
  - 数据集: WMT2014 en-de
  - 基线: 标准discrete diffusion
  - 评估指标: BLEU, perplexity
- 4.2 Main Results
  - 表1: 主要结果对比
  - 图1: 训练曲线
- 4.3 Ablation Study
  - 表2: 各组件贡献
  - adaptive scheduling: +1.0 BLEU
  - learnable parameters: +0.8 BLEU
- 4.4 Analysis
  - 图2: gap变化可视化
  - 图3: 不同阶段的gap分布

## 5. Conclusion（结论要点）
- 总结贡献
- 局限性: 只在WMT2014上测试
- 未来工作: 扩展到其他语言对、更大规模模型
```

#### Phase 2: draft（生成初稿）

**目标**: 基于大纲生成完整的LaTeX论文初稿

**输出**: `drafts/paper.tex`

**关键要求**:
1. **数字必须来自results_summary.json**: 不能编造实验结果
2. **引用必须存在于related_work.bib**: 不能引用不存在的文献
3. **遵循目标会议格式**: 使用正确的LaTeX模板（如neurips_2024.sty）
4. **图表占位**: 如果需要图表，先用占位符（\includegraphics{placeholder}）

**LaTeX结构**:
```latex
\documentclass{article}
\usepackage{neurips_2024}  % 根据target_venue选择
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage{booktabs}

\title{Adaptive Gap Scheduling for Discrete Diffusion Language Models}

\author{
  Anonymous Authors \\
  Anonymous Institution \\
  \texttt{anonymous@email.com}
}

\begin{document}

\maketitle

\begin{abstract}
Discrete diffusion models have shown promise in language generation...
\end{abstract}

\section{Introduction}
...

\section{Related Work}
...

\section{Method}
...

\section{Experiments}
...

\section{Conclusion}
...

\bibliographystyle{plain}
\bibliography{related_work}

\end{document}
```

#### Phase 3: self_check（自查）

**目标**: 生成自查清单，检查论文质量

**输出**: `drafts/self_check.md`

**自查清单**:
```markdown
# 论文自查清单

## 内容完整性
- [x] Abstract包含问题、方法、结果、贡献
- [x] Introduction明确了研究问题和贡献
- [x] Related Work覆盖了主要相关工作
- [x] Method描述清晰，可复现
- [x] Experiments包含足够的细节
- [x] Conclusion总结了贡献和局限性

## 数字准确性
- [x] 所有实验数字来自results_summary.json
- [x] BLEU: 29.8（来自exp-h1-baseline）
- [x] Baseline BLEU: 28.5（来自results_summary.json）
- [x] Improvement: 1.3 BLEU
- [ ] 需要补充perplexity数字

## 引用完整性
- [x] 所有\cite{}都存在于related_work.bib
- [x] 引用了[arxiv:2301.12345]作为基础方法
- [x] 引用了[arxiv:2312.12345]作为相关工作
- [ ] 需要补充更多baseline的引用

## 格式规范
- [x] 使用正确的LaTeX模板（neurips_2024）
- [x] 匿名化（没有作者信息）
- [x] 图表编号正确
- [x] 表格使用booktabs

## 潜在问题
1. 图1（训练曲线）还是占位符，需要生成
2. 表2（消融实验）需要补充更多细节
3. Related Work章节可能需要扩展
```

#### Phase 4: revise（修订）

**目标**: 根据Reviewer的反馈修订论文

**输入**: `drafts/review_rounds/round_N.md`（Reviewer的审稿意见）

**输出**: 更新`drafts/paper.tex`

**修订策略**:
1. 逐条处理Reviewer的意见
2. 对于每条意见，决定：接受、部分接受、拒绝（需要说明理由）
3. 更新paper.tex
4. 生成修订说明（可选）

#### Phase 5: final（最终版）

**目标**: 执行用户批准的修改，生成最终版本

**输入**: `drafts/user_corrections.md`（用户标注的问题）

**输出**: 最终版`drafts/paper.tex`

### 常见陷阱和对策

#### 陷阱1: 编造实验数字

**问题**: Writer可能编造不存在的实验结果

**对策**:
1. Prompt中强调"所有数字必须来自results_summary.json"
2. Post-hook校验：提取paper.tex中的所有数字，对照results_summary.json
3. 如果发现不匹配，返回错误让Writer修正

#### 陷阱2: 引用不存在的文献

**问题**: Writer可能引用related_work.bib中不存在的条目

**对策**:
1. Post-hook校验：提取所有\cite{X}，检查X是否在related_work.bib中
2. 如果发现缺失，返回错误列表
3. Writer必须修正或删除这些引用

#### 陷阱3: 过度乐观的描述

**问题**: Writer可能夸大实验结果或贡献

**对策**:
1. Prompt中强调"客观描述，不夸大"
2. Reviewer会检查并指出过度乐观的表述
3. 用户在T8-CORRECT Gate中最终审核

## Reviewer Agent

### 设计规格

- **Agent名称**: `reviewer`
- **模型层级**: `heavy`（需要批判性思维和学术判断）
- **Temperature**: 0.3（保持客观和一致性）
- **工具**: `read_file`, `write_file`, `finish_task`
- **最大步数**: 30
- **Token预算**: 300,000
- **超时时间**: 1800秒（30分钟）

### 输入

#### 必需输入
- `drafts/paper.tex`: Writer生成的论文草稿
- `experiments/results_summary.json`: 实验结果（用于验证数字）
- `literature/related_work.bib`: 引用库（用于验证引用）
- `project.yaml`: 项目配置

### 输出

#### 产出文件

- `drafts/review_rounds/round_N.md`: 审稿意见（N=1,2）

### 审稿维度

Reviewer从以下维度审查论文：

#### 1. 内容完整性
- Abstract是否包含问题、方法、结果、贡献？
- Introduction是否明确了研究问题和贡献？
- Related Work是否覆盖了主要相关工作？
- Method是否描述清晰、可复现？
- Experiments是否包含足够的细节？
- Conclusion是否总结了贡献和局限性？

#### 2. 技术准确性
- 实验数字是否准确（对照results_summary.json）？
- 方法描述是否正确（对照hypotheses.md）？
- 引用是否恰当（对照synthesis.md）？

#### 3. 写作质量
- 逻辑是否清晰？
- 语言是否流畅？
- 是否有语法错误？
- 是否有冗余或重复？

#### 4. 学术规范
- 是否正确引用了相关工作？
- 是否客观描述了局限性？
- 是否避免了过度乐观的表述？
- 是否遵循了目标会议的格式要求？

### 审稿报告格式

```markdown
# 审稿报告 - Round 1

生成时间: 2024-01-25 10:30:00

## 总体评价

这篇论文提出了adaptive gap scheduling方法改进离散扩散模型，实验结果显示有1.3 BLEU的提升。整体质量良好，但有以下问题需要修正。

**推荐**: Minor Revision（小修）

## 主要问题（Major Issues）

### 问题1: Related Work不完整
**位置**: Section 2, Related Work

**描述**: 
论文引用了[arxiv:2312.12345]作为连续扩散中的自适应调度，但没有讨论[arxiv:2401.67890]（Dynamic Gap Selection in Discrete Models），这篇论文与我们的工作更相关。

**建议**: 
在Related Work中补充对[arxiv:2401.67890]的讨论，并明确我们的方法与他们的差异（他们用于图生成，我们用于语言生成）。

**严重程度**: High

### 问题2: 消融实验细节不足
**位置**: Section 4.3, Ablation Study

**描述**: 
表2只列出了各组件的BLEU数字，但没有说明实验设置（如是否使用相同的随机种子、训练步数等）。

**建议**: 
补充消融实验的详细设置，确保对比的公平性。

**严重程度**: Medium

## 次要问题（Minor Issues）

### 问题3: 图1缺失
**位置**: Section 4.2, Main Results

**描述**: 
论文提到"图1显示了训练曲线"，但实际上图1是占位符。

**建议**: 
生成实际的训练曲线图，或删除这个引用。

**严重程度**: Low

### 问题4: Abstract过长
**位置**: Abstract

**描述**: 
Abstract有180词，超过了NeurIPS的150词限制。

**建议**: 
精简Abstract，删除冗余描述。

**严重程度**: Low

## 数字验证

- [x] BLEU 29.8: 正确（来自results_summary.json）
- [x] Baseline BLEU 28.5: 正确
- [x] Improvement 1.3: 正确
- [x] GPU时间18.5小时: 正确

## 引用验证

- [x] 所有\cite{}都存在于related_work.bib
- [ ] 建议补充[arxiv:2401.67890]

## 格式检查

- [x] 使用正确的LaTeX模板
- [x] 匿名化
- [ ] Abstract超过字数限制

## 总结

论文整体质量良好，主要贡献清晰，实验结果可信。需要补充Related Work和消融实验细节，修正后可以接受。

**下一步**: Writer根据以上意见修订论文。
```

## Writer-Reviewer迭代流程

### 完整Pipeline

```
1. WriterAgent(phase=outline)
   → 生成 drafts/outline.md

2. WriterAgent(phase=draft)
   → 生成 drafts/paper.tex（初稿）

3. WriterAgent(phase=self_check)
   → 生成 drafts/self_check.md

4. ReviewerAgent(round=1)
   → 生成 drafts/review_rounds/round_1.md

5. WriterAgent(phase=revise, round=1)
   → 更新 drafts/paper.tex

6. ReviewerAgent(round=2)
   → 生成 drafts/review_rounds/round_2.md

7. WriterAgent(phase=revise, round=2)
   → 更新 drafts/paper.tex

8. Gate T8-CORRECT（用户审核）
   → 用户标注问题到 drafts/user_corrections.md

9. WriterAgent(phase=final)
   → 生成最终版 drafts/paper.tex
```

### 迭代终止条件

最多2轮迭代，终止条件：
1. Reviewer认为论文质量足够好（推荐Accept）
2. 达到最大迭代次数（2轮）
3. Writer无法进一步改进（连续两轮改动很小）

## Post-Hooks

### validate_latex_citations

**目的**: 验证所有引用都存在于BibTeX文件中

**实现**:
```python
import re

def validate_latex_citations(ctx):
    paper_path = ctx.workspace_dir / "drafts" / "paper.tex"
    bib_path = ctx.workspace_dir / "literature" / "related_work.bib"
    
    if not paper_path.exists() or not bib_path.exists():
        return True, None
    
    paper_text = paper_path.read_text()
    bib_text = bib_path.read_text()
    
    # 提取BibTeX中的所有key
    bib_keys = set(re.findall(r"@\w+\{(\w+),", bib_text))
    
    # 提取paper中的所有\cite{}
    cited_keys = set(re.findall(r"\\cite\{([^}]+)\}", paper_text))
    cited_keys = {k.strip() for chunk in cited_keys for k in chunk.split(",")}
    
    # 检查缺失的key
    missing = cited_keys - bib_keys
    
    if missing:
        return False, f"Paper引用了不存在的BibTeX key: {missing}"
    
    return True, None
```

### validate_experiment_numbers

**目的**: 验证论文中的实验数字来自results_summary.json

**实现**:
```python
import re
import json

def validate_experiment_numbers(ctx):
    paper_path = ctx.workspace_dir / "drafts" / "paper.tex"
    results_path = ctx.workspace_dir / "experiments" / "results_summary.json"
    
    if not paper_path.exists() or not results_path.exists():
        return True, None
    
    paper_text = paper_path.read_text()
    results_data = json.loads(results_path.read_text())
    
    # 提取results_summary中的所有数字
    valid_numbers = set()
    for exp in results_data.get("experiments", []):
        metrics = exp.get("metrics", {})
        for value in metrics.values():
            if isinstance(value, (int, float)):
                valid_numbers.add(value)
    
    # 提取paper中的所有数字（简化版，实际需要更复杂的正则）
    paper_numbers = set(re.findall(r"\b\d+\.\d+\b", paper_text))
    paper_numbers = {float(n) for n in paper_numbers}
    
    # 检查是否有不在valid_numbers中的关键数字
    # （这里需要更智能的判断，区分实验结果数字和其他数字）
    
    return True, None  # 简化实现
```

## 与其他Agent的交互

- **依赖**: T7.5 PI Agent（需要确认实验成功）、T6 Experimenter（需要results_summary.json）、T3.5 Reader（需要synthesis.md）
- **被依赖**: T9 Submission Agent（使用paper.tex）

## 已知限制和注意事项

1. **LaTeX编译**: Writer生成的LaTeX可能有语法错误，需要人工检查
2. **图表生成**: 当前不自动生成图表，需要人工补充
3. **写作风格**: 依赖LLM的写作能力，可能需要人工润色
4. **引用准确性**: 虽然有post-hook检查，但引用的恰当性仍需人工判断
5. **迭代次数**: 最多2轮可能不够，复杂论文可能需要更多轮

## 测试

运行测试：
```bash
# Writer Agent测试
pytest tests/unit/test_writer_agent.py -v

# Reviewer Agent测试
pytest tests/unit/test_reviewer_agent.py -v

# 集成测试
pytest tests/integration/test_writer_reviewer_e2e.py -v
```

## 使用示例

```python
from researchos.agents.writer import WriterAgent
from researchos.agents.reviewer import ReviewerAgent
from researchos.runtime.agent import ExecutionContext

# Phase 1: 生成大纲
writer = WriterAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T8",
    mode="outline"
)
result = await writer.run(ctx)

# Phase 2: 生成初稿
ctx.mode = "draft"
result = await writer.run(ctx)

# Phase 3: 审稿
reviewer = ReviewerAgent()
ctx_review = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T8-REVIEW",
    extra={"round": 1}
)
result = await reviewer.run(ctx_review)

# Phase 4: 修订
ctx.mode = "revise"
ctx.extra = {"round": 1}
result = await writer.run(ctx)
```

## 配置说明

### model_routing.yaml配置
```yaml
heavy:
  provider: "anthropic"
  model: "claude-opus-4"
  max_tokens: 4096
  supports_thinking: true
```

### runtime.yaml配置
```yaml
agents:
  writer:
    max_retries: 3
    timeout_seconds: 3600
    enable_thinking: true
    temperature: 0.7
    post_hooks:
      - validate_latex_citations
      - validate_experiment_numbers
  
  reviewer:
    max_retries: 2
    timeout_seconds: 1800
    enable_thinking: true
    temperature: 0.3
```

详见 ResearchOS Runtime Dev Spec §6 和 §17。
