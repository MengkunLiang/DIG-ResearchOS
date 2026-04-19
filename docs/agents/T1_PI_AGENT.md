# T1 PI Agent 开发文档

## 概述

PI Agent（项目初始化与评估Agent）负责两个关键任务：
- **T1 (init模式)**: 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
- **T7.5 (evaluate模式)**: 评估实验结果，决定后续路径

## 设计规格

- **Agent名称**: `pi`
- **模型层级**: `heavy`
- **Temperature**: 0.3
- **工具**: `read_file`, `write_file`, `ask_human`, `finish_task`

## T1 Init模式

### 三轮对话流程
1. **第1轮**: 明确研究边界、硬约束、目标会议
2. **第2轮**: 收集已读论文、初步想法、技术约束
3. **第3轮**: 生成project.yaml草案，确认后写入文件

### 输出文件
- `project.yaml`: 项目配置（必须符合project.schema.json）
- `user_seeds/seed_papers.jsonl`: 种子论文
- `user_seeds/seed_ideas.md`: 初步想法
- `user_seeds/seed_constraints.md`: 硬约束清单

## T7.5 Evaluate模式

### 输入
- `experiments/results_summary.json`
- `experiments/iteration_log.md`
- `ideation/exp_plan.yaml`

### 输出
- `evaluation/evaluation_decision.md`: 必须包含Situation判定和Options建议

## 测试

运行测试：
```bash
pytest tests/unit/test_pi_agent.py -v
```

测试结果：12/12 通过 (100%)

## 使用示例

```python
from researchos.agents.pi import PIAgent

agent = PIAgent()
ctx = ExecutionContext(
    mode="init",
    extra={"user_topic": "discrete diffusion language models"}
)
```

详见 ResearchOS Agent Dev Spec §6
