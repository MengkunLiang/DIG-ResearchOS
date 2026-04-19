# ResearchOS T1-T4 真实LLM测试计划

## 前置条件

### 1. 配置API Key

```bash
# 方法1：Anthropic API（推荐）
export ANTHROPIC_API_KEY="your-api-key-here"

# 方法2：OpenAI API
export OPENAI_API_KEY="your-api-key-here"
# 并修改 config/model_routing.yaml 使用OpenAI endpoint
```

### 2. 验证环境

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos

# 检查API配置
python -c "import os; print('ANTHROPIC_API_KEY:', 'configured' if os.getenv('ANTHROPIC_API_KEY') else 'missing')"

# 运行selftest
python -m researchos.cli selftest
```

## 测试流程

### Phase 1: T1 项目初始化测试

```bash
# 创建测试workspace
TEST_WS=/tmp/researchos_real_test_$(date +%Y%m%d_%H%M%S)
python -m researchos.cli init-workspace --workspace $TEST_WS

# 运行T1
python -m researchos.cli run-task T1 \
  --workspace $TEST_WS \
  --topic "efficient attention mechanisms for transformers" \
  --no-banner

# 验证输出
ls -lh $TEST_WS/project.yaml
ls -lh $TEST_WS/user_seeds/
cat $TEST_WS/project.yaml
```

**预期输出**：
- `project.yaml`：包含research_direction、keywords、constraints
- `user_seeds/seed_papers.jsonl`：种子论文（可选）
- `user_seeds/seed_ideas.md`：初步想法（可选）
- `user_seeds/seed_constraints.md`：约束清单（可选）

**可能的bug**：
- [ ] Prompt渲染错误
- [ ] 三轮对话流程中断
- [ ] ask_human工具调用失败
- [ ] 输出文件格式不符合schema
- [ ] project.yaml缺少必需字段

### Phase 2: T2 文献检索测试

```bash
# 运行T2（依赖T1的输出）
python -m researchos.cli run-task T2 \
  --workspace $TEST_WS \
  --no-banner

# 验证输出
ls -lh $TEST_WS/literature/
wc -l $TEST_WS/literature/papers_raw.jsonl
wc -l $TEST_WS/literature/papers_dedup.jsonl
head -5 $TEST_WS/literature/papers_dedup.jsonl
```

**预期输出**：
- `literature/papers_raw.jsonl`：30-80篇论文
- `literature/papers_dedup.jsonl`：去重后论文
- `literature/search_log.md`：搜索日志
- `literature/missing_areas.md`：缺口分析

**可能的bug**：
- [ ] multi_source_search工具调用失败
- [ ] API速率限制导致失败
- [ ] 去重逻辑错误（papers_dedup数量异常）
- [ ] 论文数量不足（<30篇）
- [ ] JSONL格式错误
- [ ] 缺少必需字段（title、authors、year等）

### Phase 3: T3 深度阅读测试（简化版）

由于T3需要处理30-80篇论文，完整测试时间较长（30-60分钟）。建议先用少量论文测试：

```bash
# 手动创建一个只有3篇论文的测试文件
cat > $TEST_WS/literature/papers_dedup_mini.jsonl << 'EOF'
{"id":"arxiv:2301.12345","title":"Test Paper 1","authors":["Author A"],"year":2023,"abstract":"This is a test abstract.","url":"https://arxiv.org/abs/2301.12345"}
{"id":"arxiv:2302.23456","title":"Test Paper 2","authors":["Author B"],"year":2023,"abstract":"Another test abstract.","url":"https://arxiv.org/abs/2302.23456"}
{"id":"arxiv:2303.34567","title":"Test Paper 3","authors":["Author C"],"year":2023,"abstract":"Third test abstract.","url":"https://arxiv.org/abs/2303.34567"}
EOF

# 备份原文件
mv $TEST_WS/literature/papers_dedup.jsonl $TEST_WS/literature/papers_dedup_full.jsonl
cp $TEST_WS/literature/papers_dedup_mini.jsonl $TEST_WS/literature/papers_dedup.jsonl

# 运行T3（只处理3篇论文）
python -m researchos.cli run-task T3 \
  --workspace $TEST_WS \
  --no-banner

# 验证输出
ls -lh $TEST_WS/literature/paper_notes/
wc -l $TEST_WS/literature/comparison_table.csv
head -20 $TEST_WS/literature/paper_notes/*.md | head -50
```

**预期输出**：
- `literature/paper_notes/*.md`：3篇笔记（每篇包含11项checklist）
- `literature/comparison_table.csv`：对比表（至少4行：表头+3篇论文）
- `literature/related_work.bib`：BibTeX库

**可能的bug**：
- [ ] PDF下载失败处理不当
- [ ] extract_pdf_text工具调用失败
- [ ] paper_note格式不完整（缺少11项中的某些项）
- [ ] comparison_table.csv格式错误
- [ ] related_work.bib格式错误
- [ ] 每篇读完未立即写note，导致context爆炸
- [ ] ID中的特殊字符（:、/）未正确处理

### Phase 4: T3.5 文献综合测试

```bash
# 运行T3.5
python -m researchos.cli run-task T3.5 \
  --workspace $TEST_WS \
  --no-banner

# 验证输出
ls -lh $TEST_WS/literature/synthesis.md
wc -l $TEST_WS/literature/synthesis.md
head -100 $TEST_WS/literature/synthesis.md
```

**预期输出**：
- `literature/synthesis.md`：包含5个必需章节
  1. 方法家族分类
  2. 共同假设
  3. 性能-效率前沿
  4. 技术趋势
  5. 可操作研究问题

**可能的bug**：
- [ ] 章节缺失
- [ ] 内容过短（<2000字符）
- [ ] 未引用具体论文ID
- [ ] 方法家族分类不合理
- [ ] 研究问题不够具体

### Phase 5: T4 假设生成测试

```bash
# 运行T4（需要人工交互）
python -m researchos.cli run-task T4 \
  --workspace $TEST_WS \
  --no-banner

# 在Gate1时，选择一个方向（例如输入"1"）
# 在Gate2时，确认计划（输入"确认"）

# 验证输出
ls -lh $TEST_WS/ideation/
cat $TEST_WS/ideation/hypotheses.md
cat $TEST_WS/ideation/exp_plan.yaml
cat $TEST_WS/ideation/risks.md
```

**预期输出**：
- `ideation/hypotheses.md`：研究假设（带H1/H2等anchor）
- `ideation/exp_plan.yaml`：实验计划（符合schema）
- `ideation/risks.md`：Top 3风险分析

**可能的bug**：
- [ ] Gate1交互失败（ask_human工具问题）
- [ ] Gate2交互失败
- [ ] hypotheses.md缺少anchor
- [ ] exp_plan.yaml不符合schema
- [ ] hypothesis_ref引用不存在的anchor
- [ ] compute_estimate超出预算
- [ ] risks.md少于3条风险

## 常见问题排查

### 问题1：API调用失败

```bash
# 检查API key
echo $ANTHROPIC_API_KEY | head -c 20

# 检查网络连接
curl -I https://api.anthropic.com

# 查看详细错误日志
tail -100 $TEST_WS/_runtime/logs/researchos.log
```

### 问题2：工具调用失败

```bash
# 检查工具注册
python -c "from researchos.tools.registry import BUILTIN_TOOLS; print(list(BUILTIN_TOOLS.keys()))"

# 检查multi_source_search
python -c "from researchos.tools.multi_source_search import MultiSourceSearchTool; print('OK')"
```

### 问题3：输出校验失败

```bash
# 手动运行validate
python -m researchos.cli validate --workspace $TEST_WS

# 检查schema
python -c "from researchos.schemas.validator import validate_record; import yaml; data = yaml.safe_load(open('$TEST_WS/project.yaml')); print(validate_record(data, 'project'))"
```

### 问题4：Context超限

```bash
# 检查token使用
grep -i "token" $TEST_WS/_runtime/logs/researchos.log | tail -20

# 检查truncation配置
cat config/model_routing.yaml | grep -A 5 "truncation"
```

## 测试检查清单

### T1测试
- [ ] 三轮对话正常完成
- [ ] project.yaml生成且格式正确
- [ ] research_direction字段存在且合理
- [ ] keywords字段存在
- [ ] constraints字段存在
- [ ] 可选的seed文件生成（如果用户提供）

### T2测试
- [ ] 搜索到30-80篇论文
- [ ] papers_raw.jsonl格式正确
- [ ] papers_dedup.jsonl去重正确
- [ ] 每篇论文包含必需字段
- [ ] search_log.md记录搜索过程
- [ ] missing_areas.md标注缺口

### T3测试
- [ ] 至少15篇paper_notes生成
- [ ] 每篇note包含11项checklist
- [ ] comparison_table.csv格式正确
- [ ] related_work.bib格式正确
- [ ] PDF失败时降级到abstract-only

### T3.5测试
- [ ] synthesis.md生成
- [ ] 包含5个必需章节
- [ ] 长度≥2000字符
- [ ] 引用具体论文ID
- [ ] 研究问题具体可操作

### T4测试
- [ ] Gate1交互成功
- [ ] Gate2交互成功
- [ ] hypotheses.md有H1/H2 anchor
- [ ] exp_plan.yaml符合schema
- [ ] hypothesis_ref引用正确
- [ ] compute_estimate在预算内
- [ ] risks.md有3条风险

## 性能基准

### 预期时间
- T1: 5-10分钟（含人工交互）
- T2: 5-15分钟（取决于API速率）
- T3: 30-60分钟（30-80篇论文）
- T3.5: 10-20分钟
- T4: 15-30分钟（含人工交互）

### 预期成本（Anthropic API）
- T1: $0.5-1（heavy tier）
- T2: $1-2（medium tier）
- T3: $5-15（medium tier，30-80篇）
- T3.5: $2-5（heavy tier）
- T4: $3-8（heavy tier + deep_reasoning）
- **总计**: $11.5-31

## Bug修复流程

1. **记录bug**：在测试过程中记录所有错误和异常
2. **分类bug**：
   - 致命bug：导致流程中断
   - 严重bug：输出不符合预期
   - 轻微bug：格式或提示问题
3. **修复bug**：按优先级修复
4. **回归测试**：修复后重新运行相关测试
5. **提交代码**：所有bug修复后提交

## 下一步：鲁棒性增强

测试完成并修复所有bug后，按照Addendum文档补充：

### 必做项（T1-T4相关）
1. **T1**: Ethical screening（§8.1）
2. **T1**: 外部资源管理（§10.1-10.2）
3. **T4**: Hypothesis pre-mortem（§4.1）
4. **Runtime**: Budget drift warning（§7.1）

### 推荐项
- 根据实际测试结果评估是否需要

## 测试报告模板

```markdown
# T1-T4 真实LLM测试报告

**测试日期**: YYYY-MM-DD
**测试环境**: Anthropic API / OpenAI API
**Workspace**: /tmp/researchos_real_test_YYYYMMDD_HHMMSS

## 测试结果总览
- T1: ✅/❌
- T2: ✅/❌
- T3: ✅/❌
- T3.5: ✅/❌
- T4: ✅/❌

## 发现的Bug

### Bug #1: [标题]
- **严重程度**: 致命/严重/轻微
- **复现步骤**: ...
- **错误信息**: ...
- **影响范围**: T1/T2/T3/T4
- **修复方案**: ...

## 性能数据
- 总耗时: XX分钟
- 总成本: $XX
- Token使用: XXX,XXX

## 改进建议
1. ...
2. ...
```
