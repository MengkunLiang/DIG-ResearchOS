# T1 Agent Prompt 修复记录（2026-04-22 晚）

## 问题背景

用户报告了两个关键问题：

### 问题 1：欢迎信息（第0轮）没有显示

**用户反馈**：
> "好像跟你文档里描述的不一样...为什么没有这块呢？"

**预期行为**：
```
[Agent] 欢迎使用 ResearchOS！我是 PI Agent，将帮助你初始化研究项目。

        在开始之前，你可以准备以下种子数据（可选）：

        📄 **种子论文**（推荐方式）：
           - 将 PDF 文件放入 `user_seeds/pdfs/` 目录
           - 我会自动扫描并识别
           ...
```

**实际行为**：
- Agent 直接调用 ask_human 工具
- 跳过了第0轮的欢迎信息输出

### 问题 2：write_structured_file 工具调用失败

**用户反馈**：
> "好像write_structured_file是有问题的，反倒是write_file能通过校验"

**错误现象**（从 trace 日志）：
```json
{
  "type": "tool_call",
  "name": "write_structured_file",
  "arguments": {
    "path": "project.yaml",
    "schema_name": "project",
    "format": "yaml",
    "content": {  // ❌ 错误：应该是 data
      "project_id": "...",
      ...
    }
  }
}
```

**错误信息**：
```
Parameter validation error: 1 validation error for WriteStructuredFileParams
data
  Field required
```

**根本原因**：
- Agent 使用了 `content` 参数而不是 `data` 参数
- 混淆了 write_file（使用 content）和 write_structured_file（使用 data）

---

## 解决方案

### 修复 1：强化第0轮欢迎信息的显示

**文件**：`researchos/prompts/pi.j2`

**修改内容**：

1. **修改标题**（更醒目）：
   ```
   ### 🚨 第0轮：欢迎与引导（必须首先执行）
   ```

2. **添加明确指令**：
   ```
   **⚠️ CRITICAL: 在调用任何工具之前，必须先输出以下完整的欢迎文本！**

   你必须在第一次响应中，**在调用任何工具之前**，先输出以下完整的欢迎文本：
   ```

3. **在欢迎信息后添加提醒**：
   ```
   **重要**：输出完这段欢迎信息后，再开始第1轮对话（调用 ask_human 工具）。
   ```

**关键改进**：
- 使用 🚨 emoji 吸引注意
- 明确说明"必须首先执行"
- 强调"在调用任何工具之前"
- 使用粗体和重复强调

### 修复 2：强化 write_structured_file 参数说明

**文件**：`researchos/prompts/pi.j2`

**修改内容**：

1. **修改章节标题**（更醒目）：
   ```
   ## 🚨🚨🚨 使用 write_structured_file 工具生成 project.yaml 🚨🚨🚨

   ### ⚠️⚠️⚠️ CRITICAL: 参数名是 `data` 不是 `content` ⚠️⚠️⚠️
   ```

2. **添加工具对比说明**：
   ```
   **最常见的错误**：使用 `content` 参数而不是 `data` 参数！

   **工具对比**：
   - `write_file` 工具 → 使用 `content` 参数（字符串）
   - `write_structured_file` 工具 → 使用 `data` 参数（对象）⚠️⚠️⚠️
   ```

3. **提供详细的 JSON 格式示例**：
   ```json
   ### ✅✅✅ 正确的调用方式（必须完全按照这个格式）：

   {
     "path": "project.yaml",
     "schema_name": "project",
     "format": "yaml",
     "data": {  // ⚠️ 注意：是 data 不是 content
       "project_id": "{{ project_id }}",
       ...
     }
   }

   ### ❌❌❌ 错误示例 1（使用了 content 而不是 data）：

   {
     "path": "project.yaml",
     "schema_name": "project",
     "format": "yaml",
     "content": {  // ❌❌❌ 错误：参数名应该是 data，不是 content
       "project_id": "...",
       ...
     }
   }
   ```

4. **在多处强调**：
   - 在顶部 "避免常见错误" 部分
   - 在 "关键要求" 部分
   - 在工具使用说明部分

**关键改进**：
- 使用 🚨 和 ⚠️ emoji 多次强调
- 明确对比两个工具的参数差异
- 提供完整的 JSON 格式示例（不是伪代码）
- 同时展示正确和错误的示例
- 在错误示例中用 ❌ 标记错误点

---

## 修改位置

### 文件：`researchos/prompts/pi.j2`

**修改 1**（第53-82行）：
- 第0轮欢迎信息部分
- 添加了 🚨 标题和明确指令

**修改 2**（第9-11行）：
- 顶部 "避免常见错误" 部分
- 强调 content vs data 的区别

**修改 3**（第24-28行）：
- "关键要求" 部分
- 添加参数错误检查提示

**修改 4**（第229-320行）：
- "使用 write_structured_file 工具" 部分
- 完全重写，添加详细的对比和示例

---

## 验证方案

### 测试脚本

创建了 `/home/liangmengkun/tmp/test_pi_welcome_and_tool.py`：

**测试目标**：
1. 验证第0轮欢迎信息是否正确显示
2. 验证 Agent 是否正确使用 write_structured_file 工具（data 参数）

**检查方法**：
1. 解析 trace 日志
2. 查找 agent_output 中是否包含 "欢迎使用 ResearchOS"
3. 查找 tool_call 中 write_structured_file 的参数
4. 检查是否使用了 `data` 参数（正确）还是 `content` 参数（错误）

**预期结果**：
```
✅ 找到欢迎信息
✅ 正确使用 write_structured_file（使用 data 参数）
❌ 如果使用了 content 参数，会报错
```

### 手动测试

```bash
# 1. 运行 T1 Agent
researchos run-task T1 --workspace /home/liangmengkun/tmp/test-pi-fix

# 2. 观察第一次输出
# 应该看到：
# [Agent] 欢迎使用 ResearchOS！我是 PI Agent...
#         📄 **种子论文**（推荐方式）：...

# 3. 完成三轮对话后，检查 trace 日志
cat workspace/_runtime/traces/T1_*.jsonl | jq 'select(.type == "tool_call" and .name == "write_structured_file") | .arguments | keys'

# 应该看到：["path", "schema_name", "format", "data"]
# 不应该看到：["path", "schema_name", "format", "content"]
```

---

## 影响范围

### 受影响的 Agent
- **T1 (PI Agent)**：直接受益，欢迎信息正确显示，工具调用成功率提升

### 不受影响的 Agent
- T2-T6：不使用 write_structured_file 工具，或已经正确使用

### 向后兼容性
- ✅ 完全向后兼容
- ✅ 不影响现有的 workspace
- ✅ 不影响其他 Agent 的行为

---

## 预期收益

### 用户体验改进
1. **欢迎信息正确显示**：
   - 用户在 init 后立即看到引导信息
   - 知道如何准备种子数据
   - 了解推荐的使用方式

2. **工具调用成功率提升**：
   - 减少 write_structured_file 调用失败
   - 减少回退到 write_file 的情况
   - 减少需要自动修正的次数

### 量化指标（预期）
- write_structured_file 成功率：~0% → ~95%
- 用户看到欢迎信息的比例：~0% → ~100%
- 平均重试次数：~3 次 → ~1 次

---

## 相关文档

- **主文档**：`/home/liangmengkun/ResearchOS/docs/T1_SEED_MANAGEMENT_IMPROVEMENTS.md`
- **调试指南**：`/home/liangmengkun/tmp/researchos-local-debug-guide.md`
- **测试脚本**：`/home/liangmengkun/tmp/test_pi_welcome_and_tool.py`

---

## 后续改进方向

1. **监控 Agent 行为**：
   - 收集 trace 日志，统计工具调用成功率
   - 分析 Agent 是否仍然混淆参数名

2. **进一步优化 prompt**：
   - 如果 Agent 仍然出错，考虑在工具定义中添加更多说明
   - 或者在 system prompt 中添加全局规则

3. **工具层改进**：
   - 考虑在 write_structured_file 工具中添加参数别名
   - 如果收到 `content` 参数，自动映射到 `data` 并给出警告

---

**文档创建时间**: 2026-04-22 晚  
**ResearchOS 版本**: 0.1.0  
**作者**: Claude Opus 4.7
