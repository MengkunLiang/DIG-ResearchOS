# ResearchOS Agent 开发指南

> 本文档面向开发ResearchOS T1-T9 agent的开发者

## 快速开始

### 开发一个新Agent的步骤

1. **阅读设计文档**
   - Agent Dev Spec对应章节
   - AGENT_DEVELOPMENT_STRATEGY.md中的分析

2. **创建Agent类** (`researchos/agents/<name>.py`)
   ```python
   from ..runtime.agent import Agent, AgentSpec, ExecutionContext
   from ._common import load_project, validate_files_exist
   
   class MyAgent(Agent):
       def __init__(self):
           super().__init__(AgentSpec(
               name="my_agent",
               model_tier="medium",
               tool_names=["read_file", "write_file", "finish_task"],
               # ...
           ))
       
       def system_prompt(self, ctx: ExecutionContext) -> str:
           # 使用render_prompt渲染模板
           pass
       
       def initial_user_message(self, ctx: ExecutionContext) -> str:
           # 简短的启动消息
           pass
       
       def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
           # 先调用super()，再做业务校验
           pass
   ```

3. **创建Prompt模板** (`researchos/prompts/<name>.j2`)
   - 使用Jinja2语法
   - 清晰的指令和示例
   - 中文prompt

4. **注册Agent** (`researchos/agents/registry.py`)
   ```python
   from .my_agent import MyAgent
   
   AGENT_REGISTRY["my_agent"] = MyAgent()
   TASK_TO_AGENT_MAP["MY_TASK"] = "my_agent"
   ```

5. **编写测试**
   - 单元测试：`tests/unit/test_my_agent.py`
   - 集成测试：`tests/integration/test_my_agent_e2e.py`

6. **编写文档** (`docs/agents/MY_AGENT.md`)

7. **运行验证**
   ```bash
   pytest tests/unit/test_my_agent.py -v
   pytest tests/integration/test_my_agent_e2e.py -v
   ```

## 代码规范

### Agent类规范

- 代码行数无所谓（不含注释和docstring）
- 每个方法都有详细注释
- 使用`agents/_common.py`的helper函数
- 遵循Agent Dev Spec §2的模式

### Prompt模板规范

- 使用中文
- 清晰的步骤说明
- 包含输入输出示例
- 使用Jinja2变量注入上下文

### 测试规范

- 使用MockLLMClient进行单元测试
- 至少覆盖：happy path、边界情况、错误处理
- 集成测试使用完整的workspace fixture
- 所有测试必须通过

## 常用Helper函数

```python
from ._common import (
    load_project,           # 读取project.yaml
    load_jsonl,            # 读取JSONL文件
    write_jsonl,           # 写入JSONL文件
    validate_files_exist,  # 校验文件存在
    validate_jsonl_schema, # 校验JSONL schema
)
```

## 调试技巧

### 使用debug脚本

```bash
python scripts/debug_agent.py --agent my_agent --workspace ./workspace/test --mock
```

### 查看trace

```bash
researchos trace <run_id> --workspace ./workspace/test
```

### 查看日志

```bash
tail -f ./workspace/test/_runtime/logs/researchos.log
```

## 常见问题

### Q: Agent代码太长怎么办？
A: 检查是否有逻辑应该放在tool或helper函数中。Agent类应该只做协调。

### Q: 如何处理多模态（init/evaluate）？
A: 在AgentSpec中声明，在system_prompt中根据ctx.extra判断模式。

### Q: 如何测试需要真实LLM的场景？
A: 先用MockLLMClient测试逻辑，再用真实LLM做冒烟测试。

### Q: validate_outputs应该检查什么？
A: 三层：文件存在 → Schema合规 → 内容合理（数量、格式等）

## 参考资源

- [Agent Dev Spec](../reference_materials/ResearchOS_Agent_Dev_Spec.md)
- [Runtime Dev Spec](../reference_materials/ResearchOS_Runtime_Dev_Spec.md)
- [开发策略分析](../AGENT_DEVELOPMENT_STRATEGY.md)
- [HelloAgent示例](../researchos/agents/hello.py)
