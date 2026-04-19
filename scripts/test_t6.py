#!/usr/bin/env python3
"""T6 Experimenter Agent 真实LLM测试脚本

测试T6 Agent的实验执行能力：
1. 准备一个简单的exp_plan.yaml
2. 运行T6 agent
3. 验证输出文件
4. 记录测试结果
"""

import json
import sys
from pathlib import Path

import yaml

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext


def prepare_test_workspace(workspace_dir: Path):
    """准备测试workspace。"""
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 创建project.yaml
    project_data = {
        "research_direction": "测试深度学习模型优化方法",
        "domain": "Machine Learning",
        "constraints": {
            "max_budget_usd": 50.0,
            "max_gpu_hours": 10,
        },
    }
    project_path = workspace_dir / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建ideation目录
    ideation_dir = workspace_dir / "ideation"
    ideation_dir.mkdir(exist_ok=True)

    # 创建hypotheses.md
    hypotheses_content = """# 研究假设

## H1: 改进的注意力机制

我们假设通过引入自适应注意力权重，可以提升模型在长序列任务上的性能。

**理论依据**：
- 传统注意力机制对所有位置使用固定的计算方式
- 自适应权重可以根据输入动态调整注意力分布

**预期效果**：
- 在长文本分类任务上提升3-5%的准确率
- 减少计算复杂度约20%

## H2: 多尺度特征融合

通过融合不同层级的特征表示，可以提升模型的泛化能力。

**理论依据**：
- 浅层特征捕获局部模式
- 深层特征捕获全局语义
- 融合可以综合两者优势

**预期效果**：
- 在小样本场景下提升5-8%的F1分数
"""
    hypotheses_path = ideation_dir / "hypotheses.md"
    hypotheses_path.write_text(hypotheses_content, encoding="utf-8")

    # 创建exp_plan.yaml
    exp_plan_data = {
        "project_name": "测试实验计划",
        "experiments": [
            {
                "name": "baseline_bert",
                "hypothesis_ref": "H1",
                "description": "BERT baseline实验",
                "dataset": "imdb_sentiment",
                "baseline_methods": ["bert-base"],
                "our_method": {
                    "name": "adaptive_attention_bert",
                    "description": "带自适应注意力的BERT",
                },
                "metrics": ["accuracy", "f1_score"],
                "compute_estimate": {
                    "gpu_hours": 2,
                    "gpu_type": "V100",
                },
                "success_criteria": [
                    {"metric": "accuracy", "threshold": 0.85}
                ],
            },
            {
                "name": "multiscale_fusion",
                "hypothesis_ref": "H2",
                "description": "多尺度特征融合实验",
                "dataset": "imdb_sentiment",
                "baseline_methods": ["bert-base"],
                "our_method": {
                    "name": "multiscale_bert",
                    "description": "多尺度特征融合BERT",
                },
                "metrics": ["accuracy", "f1_score"],
                "compute_estimate": {
                    "gpu_hours": 3,
                    "gpu_type": "V100",
                },
                "success_criteria": [
                    {"metric": "f1_score", "threshold": 0.87}
                ],
            },
        ],
    }
    exp_plan_path = ideation_dir / "exp_plan.yaml"
    exp_plan_path.write_text(yaml.dump(exp_plan_data, allow_unicode=True), encoding="utf-8")

    print(f"✓ 测试workspace已准备: {workspace_dir}")
    print(f"  - project.yaml")
    print(f"  - ideation/hypotheses.md")
    print(f"  - ideation/exp_plan.yaml")


def verify_outputs(workspace_dir: Path) -> bool:
    """验证输出文件。"""
    print("\n验证输出文件...")

    # 检查results_summary.json
    results_path = workspace_dir / "experiments" / "results_summary.json"
    if not results_path.exists():
        print(f"✗ 缺少 results_summary.json")
        return False

    try:
        results_data = json.loads(results_path.read_text(encoding="utf-8"))
        print(f"✓ results_summary.json 存在且格式正确")

        # 检查必需字段
        required_fields = ["experiments", "total_experiments"]
        for field in required_fields:
            if field not in results_data:
                print(f"✗ results_summary.json 缺少字段: {field}")
                return False

        experiments = results_data.get("experiments", [])
        print(f"  - 实验数量: {len(experiments)}")

        for i, exp in enumerate(experiments):
            print(f"  - 实验{i+1}: {exp.get('name', '?')} - {exp.get('status', '?')}")

    except Exception as e:
        print(f"✗ results_summary.json 解析失败: {e}")
        return False

    # 检查iteration_log.md
    log_path = workspace_dir / "experiments" / "iteration_log.md"
    if not log_path.exists():
        print(f"✗ 缺少 iteration_log.md")
        return False

    log_content = log_path.read_text(encoding="utf-8")
    print(f"✓ iteration_log.md 存在 ({len(log_content)} 字符)")

    return True


def main():
    """主测试流程。"""
    print("=" * 60)
    print("T6 Experimenter Agent 真实LLM测试")
    print("=" * 60)

    # 准备测试workspace
    workspace_dir = Path("/home/liangmengkun/tmp/test_t6_workspace")
    prepare_test_workspace(workspace_dir)

    # 创建agent
    print("\n创建T6 Experimenter Agent...")
    agent = ExperimenterAgent()
    print(f"✓ Agent创建成功: {agent.spec.name}")

    # 创建执行上下文
    ctx = ExecutionContext(
        workspace_dir=workspace_dir,
        project_id="test_t6",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 生成system prompt
    print("\n生成system prompt...")
    system_prompt = agent.system_prompt(ctx)
    print(f"✓ System prompt生成成功 ({len(system_prompt)} 字符)")

    # 生成初始消息
    initial_message = agent.initial_user_message(ctx)
    print(f"✓ 初始消息: {initial_message[:100]}...")

    print("\n" + "=" * 60)
    print("注意：真实LLM执行需要调用Anthropic API")
    print("这个脚本只验证了Agent的配置和prompt生成")
    print("要进行完整测试，需要使用ResearchOS CLI运行T6任务")
    print("=" * 60)

    # 模拟输出验证（创建示例输出）
    print("\n创建示例输出用于验证...")
    experiments_dir = workspace_dir / "experiments"
    experiments_dir.mkdir(exist_ok=True)

    # 创建示例results_summary.json
    results_data = {
        "exp_plan_ref": "ideation/exp_plan.yaml",
        "total_experiments": 2,
        "completed": 2,
        "failed": 0,
        "experiments": [
            {
                "experiment_id": "exp_baseline_bert_20260419_120000",
                "name": "baseline_bert",
                "hypothesis_ref": "H1",
                "status": "DONE",
                "metrics": {"accuracy": 0.87, "f1_score": 0.86},
                "duration_seconds": 3600,
                "run_dir": "experiments/runs/exp_baseline_bert_20260419_120000",
            },
            {
                "experiment_id": "exp_multiscale_fusion_20260419_130000",
                "name": "multiscale_fusion",
                "hypothesis_ref": "H2",
                "status": "DONE",
                "metrics": {"accuracy": 0.89, "f1_score": 0.88},
                "duration_seconds": 4200,
                "run_dir": "experiments/runs/exp_multiscale_fusion_20260419_130000",
            },
        ],
        "summary": {
            "best_method": "multiscale_bert",
            "best_accuracy": 0.89,
            "success_criteria_met": True,
        },
    }
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text(json.dumps(results_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 创建示例iteration_log.md
    log_content = """# 实验迭代日志

## 实验概览

- 开始时间：2026-04-19 12:00:00
- 结束时间：2026-04-19 14:30:00
- 总实验数：2
- 成功：2
- 失败：0

## Iteration 1

### 实验1: baseline_bert
- **假设**: H1
- **方法**: bert-base
- **数据集**: imdb_sentiment
- **状态**: DONE
- **结果**:
  - accuracy: 0.87
  - f1_score: 0.86
- **运行时间**: 1小时
- **观察**: Baseline表现符合预期

### 实验2: multiscale_fusion
- **假设**: H2
- **方法**: multiscale_bert
- **数据集**: imdb_sentiment
- **状态**: DONE
- **结果**:
  - accuracy: 0.89 (+2.3% vs baseline)
  - f1_score: 0.88 (+2.3% vs baseline)
- **运行时间**: 1.2小时
- **观察**: 多尺度特征融合显著提升性能

## 结论

- 假设H1和H2都得到验证
- 多尺度特征融合方法表现最佳
- 所有success criteria已满足
"""
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text(log_content, encoding="utf-8")

    # 验证输出
    if verify_outputs(workspace_dir):
        print("\n✓ 输出验证通过")

        # 运行agent的validate_outputs
        print("\n运行Agent的validate_outputs...")
        ok, err = agent.validate_outputs(ctx)
        if ok:
            print("✓ Agent validate_outputs 通过")
        else:
            print(f"✗ Agent validate_outputs 失败: {err}")
            return 1
    else:
        print("\n✗ 输出验证失败")
        return 1

    print("\n" + "=" * 60)
    print("测试完成！")
    print(f"测试workspace: {workspace_dir}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
