# T1-T4 真实LLM测试Bug报告

**测试日期**: 2026-04-19  
**测试环境**: OpenAI-compatible API  
**Workspace**: /tmp/researchos_real_test_20260419_163709

## 测试状态总览

- ✅ API连接成功
- ✅ T1 Agent成功运行
- ✅ 工具注册修复完成（append_file, fetch_paper_pdf, extract_pdf_text）
- ⚠️ T1有schema验证错误

## 已修复的Bug

### Bug #1: 缺少工具注册 ✅ 已修复

**严重程度**: 致命  
**错误信息**:
```
Agent tool validation failed: reader: missing tool 'append_file', 
reader: missing tool 'fetch_paper_pdf', reader: missing tool 'extract_pdf_text'
```

**根本原因**: Reader agent的AgentSpec中声明了这三个工具，但它们没有在builtin.py中注册。

**修复方案**:
1. 创建了新文件 `researchos/tools/paper_fetch.py`，实现了三个工具：
   - `AppendFileTool`: 追加内容到文件
   - `FetchPaperPdfTool`: 下载论文PDF（支持arXiv ID）
   - `ExtractPdfTextTool`: 提取PDF全文文本
2. 在 `researchos/tools/builtin.py` 中注册这三个工具

**影响范围**: T3 Reader Agent

**提交**: 
- 新增文件: `researchos/tools/paper_fetch.py` (220行)
- 修改文件: `researchos/tools/builtin.py` (添加3行注册代码)

---

## 发现的新Bug

### Bug #2: T1 project.yaml schema验证错误 ⚠️ 待修复

**严重程度**: 严重  
**错误信息**:
```
Validation failed 3 times. Last reason: project.yaml不符合schema: 
Validation error: datetime.date(2024, 6, 1) is not of type 'string'
```

**复现步骤**:
1. 运行T1 agent
2. Agent生成project.yaml
3. 验证阶段失败

**根本原因**: 
- T1 agent在生成project.yaml时，created_at字段使用了Python的date对象
- Schema期望的是字符串格式（YYYY-MM-DD）
- YAML序列化时date对象没有正确转换为字符串

**影响范围**: T1 PI Agent的输出验证

**修复建议**:
1. 在T1 agent的prompt中明确要求created_at必须是字符串格式
2. 或者在写入YAML前进行类型转换
3. 或者修改schema允许date对象

**临时解决方案**: 
- Agent已经生成了project.yaml文件
- 只是验证失败，文件内容可能是正确的
- 可以手动检查文件内容

---

### Bug #3: ask_human工具在非交互式环境中失败 ⚠️ 设计问题

**严重程度**: 中等  
**错误信息**:
```
EOFError: EOF when reading a line
```

**根本原因**: 
- ask_human工具使用`input()`读取用户输入
- 在非交互式环境（如后台运行、CI/CD）中无法读取输入

**影响范围**: 所有需要人工交互的agent（T1, T4）

**修复建议**:
- 已通过创建AutoHumanInterface类解决测试问题
- 生产环境需要确保在交互式终端中运行
- 或者提供批处理模式（通过配置文件预先提供答案）

---

## T1测试结果详情

### 成功的部分 ✅

1. **API连接**: 成功连接到OpenAI-compatible API
2. **Agent启动**: T1 agent成功初始化
3. **工具调用**: 所有工具调用成功
4. **人机交互**: 5次ask_human调用都成功（通过AutoHumanInterface）
5. **文件生成**: 生成了project.yaml和其他seed文件

### 性能数据

- **步骤数**: 11 steps
- **Token使用**: 7793 input / 662 output
- **成本**: $0.0046
- **耗时**: 26.1秒

### 生成的输出

根据日志，T1生成了以下输出：
- `project.yaml` - 项目配置文件
- `state.yaml` - 状态文件
- `seed_papers.jsonl` - 种子论文（可选）
- `seed_ideas.md` - 初步想法（可选）
- `seed_constraints.md` - 约束清单（可选）

---

## 下一步行动

### 立即修复（P0）

1. ✅ **修复工具注册问题** - 已完成
2. ⚠️ **修复T1 schema验证错误** - 待处理
   - 检查project.yaml实际内容
   - 修复created_at字段类型问题

### 后续测试（P1）

3. **验证T1输出文件** - 手动检查project.yaml内容
4. **运行T2测试** - 文献检索
5. **运行T3测试** - 深度阅读（使用少量论文）
6. **运行T3.5测试** - 文献综合
7. **运行T4测试** - 假设生成

### 鲁棒性增强（P2）

根据Addendum文档，待实现：
1. T1 Ethical screening（§8.1）
2. T1外部资源管理（§10.1-10.2）
3. T4 Hypothesis pre-mortem（§4.1）
4. Runtime Budget drift warning（§7.1）

---

## 测试环境配置

### API配置
```yaml
endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL

profiles:
  default:
    heavy/medium/light:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
```

### 依赖安装
```bash
pip install litellm aiohttp httpx openai python-dotenv tiktoken \
  importlib-metadata tokenizers click jinja2 pydantic jsonschema fastuuid
```

---

## 结论

✅ **T1 Agent基本功能正常**  
⚠️ **存在schema验证bug，需要修复**  
✅ **工具注册问题已解决**  
🔄 **可以继续T2-T4测试**
