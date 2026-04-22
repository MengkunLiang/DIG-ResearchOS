# ResearchOS Agent 配置指南

## Agent 配置位置

每个 Agent 的配置在其对应的 Python 文件中，通过 `AgentSpec` 定义。

### 配置文件路径

```
researchos/agents/
├── pi.py          # T1 PI Agent
├── scout.py       # T2 Scout Agent
├── reader.py      # T3 Reader Agent
├── ideation.py    # T4 Ideation Agent
├── experimenter.py # T5 Experimenter Agent
└── writer.py      # T6 Writer Agent
```

---

## AgentSpec 参数说明

### 示例（T2 Scout Agent）

```python
class ScoutAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="scout",                    # Agent 名称
                model_tier="medium",             # 模型层级
                tool_names=[...],                # 可用工具列表
                max_steps=50,                    # 最大步数
                max_tokens_total=200_000,        # Token 预算
                max_wall_seconds=1800,           # 最大执行时间（秒）
                temperature=0.5,                 # 温度参数
                allowed_read_prefixes=[...],     # 允许读取的目录
                allowed_write_prefixes=[...],    # 允许写入的目录
                prompt_template="scout.j2",      # Prompt 模板
                structured_outputs={...},        # 结构化输出映射
            )
        )
```

---

## 关键参数详解

### 1. model_tier（模型层级）

**可选值**：
- `"light"` - 轻量级模型（快速、便宜，适合简单任务）
- `"medium"` - 中等模型（平衡性能和成本）
- `"heavy"` - 重量级模型（最强性能，适合复杂任务）

**实际模型映射**（在 `config/model_routing.yaml` 中配置）：
```yaml
light: gpt-4o-mini
medium: gpt-4o
heavy: gpt-4o
```

**使用建议**：
- T1 (PI): `medium` - 需要理解用户意图
- T2 (Scout): `medium` - 需要处理大量论文数据
- T3 (Reader): `heavy` - 需要深度理解论文内容
- T4 (Ideation): `heavy` - 需要创造性思维
- T5 (Experimenter): `heavy` - 需要编写和调试代码
- T6 (Writer): `heavy` - 需要高质量写作

### 2. max_tokens_total（Token 预算）

**含义**：Agent 在整个执行过程中可以使用的最大 token 数（输入 + 输出）

**常见值**：
- 简单任务：50,000 - 100,000
- 中等任务：100,000 - 200,000
- 复杂任务：200,000 - 500,000

**当前配置**：
- T1 (PI): 100,000
- T2 (Scout): 200,000
- T3 (Reader): 300,000
- T4 (Ideation): 200,000
- T5 (Experimenter): 500,000
- T6 (Writer): 300,000

**如何调整**：
```python
# 如果 T2 经常超限，可以增加到 250K
max_tokens_total=250_000,
```

**注意**：
- Token 预算超限会导致 Agent 被强制停止
- 从日志中可以看到实际使用量：`error: Budget exceeded on tokens: 213723/200000`

### 3. max_steps（最大步数）

**含义**：Agent 可以执行的最大工具调用次数

**常见值**：
- 简单任务：10 - 20 步
- 中等任务：30 - 50 步
- 复杂任务：50 - 100 步

**当前配置**：
- T1 (PI): 30 步
- T2 (Scout): 50 步
- T3 (Reader): 100 步
- T4 (Ideation): 50 步
- T5 (Experimenter): 100 步
- T6 (Writer): 80 步

**如何调整**：
```python
# 如果 T2 需要更多步数
max_steps=80,
```

### 4. max_wall_seconds（最大执行时间）

**含义**：Agent 的最大执行时间（秒）

**常见值**：
- 快速任务：300 秒（5 分钟）
- 中等任务：1800 秒（30 分钟）
- 长时间任务：3600 秒（1 小时）

**当前配置**：
- 大部分 Agent：1800 秒（30 分钟）

### 5. temperature（温度参数）

**含义**：控制模型输出的随机性

**范围**：0.0 - 1.0
- 0.0：完全确定性（适合需要精确输出的任务）
- 0.5：平衡（推荐）
- 1.0：最大随机性（适合创造性任务）

**当前配置**：
- T1 (PI): 0.3（需要准确理解用户意图）
- T2 (Scout): 0.5（平衡）
- T3 (Reader): 0.3（需要准确理解论文）
- T4 (Ideation): 0.7（需要创造性）
- T5 (Experimenter): 0.5（平衡）
- T6 (Writer): 0.7（需要创造性写作）

### 6. tool_names（可用工具列表）

**含义**：Agent 可以调用的工具列表

**示例**：
```python
tool_names=[
    "read_file",              # 读取文件
    "write_file",             # 写入文件
    "write_structured_file",  # 写入结构化文件
    "multi_source_search",    # 多源搜索
    "deduplicate_papers",     # 去重论文
    "finish_task",            # 完成任务
]
```

**如何添加工具**：
1. 在 `researchos/tools/` 中实现工具
2. 在 `researchos/tools/builtin.py` 中注册工具
3. 在 Agent 的 `tool_names` 中添加工具名称

### 7. allowed_read_prefixes / allowed_write_prefixes

**含义**：Agent 可以读取/写入的目录前缀

**示例**：
```python
allowed_read_prefixes=["", "user_seeds/", "seeds/"],  # 可以读取根目录、user_seeds、seeds
allowed_write_prefixes=["literature/"],               # 只能写入 literature 目录
```

**安全性**：
- 限制 Agent 的文件访问权限
- 防止 Agent 误删除或覆盖重要文件

### 8. structured_outputs（结构化输出映射）

**含义**：声明哪些输出文件需要 schema 验证

**示例**：
```python
structured_outputs={
    "literature/papers_dedup.jsonl": "papers_dedup",  # 文件路径 -> schema 名称
    "literature/papers_raw.jsonl": "papers_raw",
}
```

**用途**：
- 在 `validate_outputs()` 中自动验证文件格式
- 确保输出文件符合 schema 定义

---

## 如何修改 Agent 配置

### 步骤 1：找到对应的 Agent 文件

```bash
# 例如修改 T2 Scout Agent
vim /home/liangmengkun/ResearchOS/researchos/agents/scout.py
```

### 步骤 2：修改 AgentSpec 参数

```python
class ScoutAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="scout",
                model_tier="medium",
                max_steps=80,              # 从 50 增加到 80
                max_tokens_total=250_000,  # 从 200K 增加到 250K
                temperature=0.5,
                # ... 其他参数
            )
        )
```

### 步骤 3：重新运行 Agent

```bash
researchos run-task T2 --workspace ./workspace/local-test
```

---

## 常见调整场景

### 场景 1：Token 预算超限

**症状**：
```
error: Budget exceeded on tokens: 213723/200000
```

**解决方法**：
```python
# 增加 max_tokens_total
max_tokens_total=250_000,  # 或 300_000
```

### 场景 2：步数不够

**症状**：
```
[Agent] 步骤 50/50 | Token: 150000 | 成本: $0.45
[Agent] 达到最大步数限制
```

**解决方法**：
```python
# 增加 max_steps
max_steps=80,  # 或 100
```

### 场景 3：执行时间超限

**症状**：
```
error: Execution timeout after 1800 seconds
```

**解决方法**：
```python
# 增加 max_wall_seconds
max_wall_seconds=3600,  # 1 小时
```

### 场景 4：需要更强的模型

**症状**：
- Agent 理解能力不足
- 输出质量不高

**解决方法**：
```python
# 升级模型层级
model_tier="heavy",  # 从 medium 升级到 heavy
```

### 场景 5：需要更多创造性

**症状**：
- 输出过于保守
- 缺乏创新性

**解决方法**：
```python
# 增加 temperature
temperature=0.8,  # 从 0.5 增加到 0.8
```

---

## 全局配置

### 模型路由配置

**文件**：`config/model_routing.yaml`

```yaml
# 模型层级映射
light: gpt-4o-mini
medium: gpt-4o
heavy: gpt-4o

# 或者使用不同的模型
light: gpt-4o-mini
medium: gpt-4o
heavy: claude-opus-4
```

### 状态机配置

**文件**：`config/state_machine.yaml`

定义 Agent 之间的依赖关系和执行顺序。

---

## 监控和调试

### 查看 Agent 配置

```python
from researchos.agents.scout import ScoutAgent

agent = ScoutAgent()
print(f"模型层级: {agent.spec.model_tier}")
print(f"最大步数: {agent.spec.max_steps}")
print(f"Token 预算: {agent.spec.max_tokens_total}")
print(f"温度: {agent.spec.temperature}")
```

### 查看执行日志

```bash
# 查看最新的 trace 日志
ls -lt workspace/local-test/_runtime/traces/

# 查看 token 使用情况
grep "Token:" workspace/local-test/_runtime/traces/T2_*.jsonl | tail -5
```

### 查看成本统计

```bash
# 从日志中提取成本信息
grep "成本:" workspace/local-test/_runtime/traces/T2_*.jsonl | tail -1
```

---

## 最佳实践

1. **从保守配置开始**：
   - 先使用较小的 token 预算和步数
   - 根据实际需求逐步增加

2. **监控实际使用情况**：
   - 查看日志中的 token 使用量
   - 查看实际执行的步数
   - 根据实际情况调整配置

3. **平衡成本和性能**：
   - 不是所有任务都需要 heavy 模型
   - 合理设置 temperature（不要过高或过低）

4. **测试配置变更**：
   - 修改配置后先在测试 workspace 中验证
   - 确认无误后再应用到生产环境

---

**文档创建时间**: 2026-04-22  
**ResearchOS 版本**: 0.1.0  
**作者**: Claude Opus 4.7
