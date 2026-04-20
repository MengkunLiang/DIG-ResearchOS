"""Integration test for T5 Pilot Mode.

集成测试 T5 Pilot 模式的完整流程：
- 准备测试 workspace
- 创建 ExperimenterAgent（mode="pilot"）
- 生成 system_prompt 和 initial_user_message
- Mock 输出文件
- 验证输出校验逻辑
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext


def prepare_pilot_workspace(workspace_dir: Path) -> None:
    """准备 pilot 模式的测试 workspace。

    Args:
        workspace_dir: workspace 根目录
    """
    # 创建 project.yaml
    project_data = {
        "research_direction": "基于自适应扩散的文本生成优化",
        "domain": "NLP",
        "constraints": {
            "max_budget_usd": 500.0,
            "max_gpu_hours": 20.0,
        },
    }
    project_path = workspace_dir / "project.yaml"
    project_path.write_text(
        yaml.dump(project_data, allow_unicode=True),
        encoding="utf-8"
    )

    # 创建 ideation 目录
    ideation_dir = workspace_dir / "ideation"
    ideation_dir.mkdir(parents=True, exist_ok=True)

    # 创建 hypotheses.md
    hypotheses_content = """# 研究假设

## H1: 自适应调度策略

我们假设通过动态调整扩散步数，可以在保持生成质量的同时显著降低计算成本。

**理论依据**：
- 简单样本不需要完整的扩散步数
- 复杂样本需要更多的去噪步骤

**预期效果**：
- 平均推理速度提升 30-50%
- 生成质量下降 < 5%

## H2: 层次化表示学习

我们假设在扩散过程中引入层次化的潜在表示可以提升长文本生成的连贯性。

**理论依据**：
- 长文本需要全局和局部两个层次的规划
- 层次化表示可以更好地建模长程依赖

**预期效果**：
- 长文本连贯性指标提升 10-15%
- 生成速度略有下降（< 10%）
"""
    (ideation_dir / "hypotheses.md").write_text(
        hypotheses_content,
        encoding="utf-8"
    )

    # 创建 exp_plan.yaml
    exp_plan_data = {
        "experiments": [
            {
                "name": "h1_adaptive_scheduling",
                "hypothesis_ref": "H1",
                "tier": "headline",  # 需要 3 个 seed
                "description": "测试自适应调度策略的效果",
                "dataset": "wikitext-103",
                "data_fraction": 0.1,  # pilot 模式使用 10% 数据
                "baseline_methods": [
                    "fixed_50_steps",
                    "fixed_100_steps",
                ],
                "our_method": {
                    "name": "adaptive_scheduling",
                    "description": "根据样本复杂度动态调整步数（20-100 步）",
                    "hyperparameters": {
                        "min_steps": 20,
                        "max_steps": 100,
                        "complexity_threshold": 0.5,
                    },
                },
                "metrics": [
                    "perplexity",
                    "bleu",
                    "inference_time",
                    "avg_steps_used",
                ],
                "compute_estimate": {
                    "gpu_hours": 1.0,
                    "gpu_type": "V100",
                },
                "success_criteria": [
                    {
                        "metric": "inference_time",
                        "comparison": "speedup",
                        "threshold": 1.3,  # 至少 30% 加速
                    },
                    {
                        "metric": "perplexity",
                        "comparison": "degradation",
                        "threshold": 0.05,  # 最多 5% 下降
                    },
                ],
            },
            {
                "name": "h2_hierarchical_repr",
                "hypothesis_ref": "H2",
                "tier": "final_method",  # 需要 2 个 seed
                "description": "测试层次化表示学习的效果",
                "dataset": "longform-qa",
                "data_fraction": 0.1,
                "baseline_methods": [
                    "flat_diffusion",
                ],
                "our_method": {
                    "name": "hierarchical_diffusion",
                    "description": "两层潜在表示：全局规划 + 局部生成",
                    "hyperparameters": {
                        "global_dim": 512,
                        "local_dim": 256,
                        "hierarchy_levels": 2,
                    },
                },
                "metrics": [
                    "coherence_score",
                    "rouge_l",
                    "inference_time",
                ],
                "compute_estimate": {
                    "gpu_hours": 1.5,
                    "gpu_type": "V100",
                },
                "success_criteria": [
                    {
                        "metric": "coherence_score",
                        "comparison": "improvement",
                        "threshold": 0.1,  # 至少 10% 提升
                    },
                ],
            },
        ],
        "ablations": {
            "required": [
                "without_adaptive_threshold",
                "without_complexity_estimation",
                "without_hierarchical_global",
            ],
            "optional": [
                "different_step_ranges",
                "different_hierarchy_depths",
            ],
        },
    }
    (ideation_dir / "exp_plan.yaml").write_text(
        yaml.dump(exp_plan_data, allow_unicode=True),
        encoding="utf-8"
    )


def create_pilot_outputs(workspace_dir: Path) -> None:
    """创建 pilot 模式的输出文件（用于测试校验逻辑）。

    Args:
        workspace_dir: workspace 根目录
    """
    # 创建 pilot 输出目录
    pilot_dir = workspace_dir / "pilot"
    pilot_dir.mkdir(parents=True, exist_ok=True)

    pilot_code_dir = pilot_dir / "pilot_code"
    pilot_code_dir.mkdir(parents=True, exist_ok=True)

    # 1. 创建 pilot_results.json
    pilot_results = {
        "seed": 42,
        "total_experiments": 2,
        "completed": 2,
        "failed": 0,
        "experiments": [
            {
                "experiment_id": "pilot_h1_adaptive_20240420_001",
                "name": "h1_adaptive_scheduling",
                "hypothesis_ref": "H1",
                "status": "DONE",
                "metrics": {
                    "perplexity": 28.5,
                    "bleu": 0.32,
                    "inference_time": 12.3,
                    "avg_steps_used": 45.2,
                    "speedup": 1.42,  # 42% 加速
                },
                "duration_seconds": 1800,
                "run_dir": "pilot/runs/pilot_h1_adaptive_20240420_001",
            },
            {
                "experiment_id": "pilot_h2_hierarchical_20240420_002",
                "name": "h2_hierarchical_repr",
                "hypothesis_ref": "H2",
                "status": "DONE",
                "metrics": {
                    "coherence_score": 0.78,
                    "rouge_l": 0.45,
                    "inference_time": 18.5,
                    "improvement": 0.12,  # 12% 提升
                },
                "duration_seconds": 2400,
                "run_dir": "pilot/runs/pilot_h2_hierarchical_20240420_002",
            },
        ],
        "summary": {
            "h1_success": True,
            "h1_reason": "达到 30% 加速目标，质量下降可接受",
            "h2_success": True,
            "h2_reason": "连贯性提升 12%，超过 10% 目标",
        },
    }
    (pilot_dir / "pilot_results.json").write_text(
        json.dumps(pilot_results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 2. 创建 motivation_validation.md
    motivation_validation = """# Pilot 实验动机验证报告

## 判定：PASS

## 各假设评估

### H1: 自适应调度策略

**实验结果**：
- 推理速度提升：42%（目标 30%）✓
- 质量下降：perplexity 从 27.8 → 28.5（1.7% 下降，目标 < 5%）✓
- 平均步数：45.2 步（范围 20-100）

**结论**：
- ✅ **PASS** - 初步结果验证了假设的可行性
- 自适应策略确实可以在保持质量的同时显著加速
- 建议在 full 实验中进一步优化复杂度估计算法

### H2: 层次化表示学习

**实验结果**：
- 连贯性提升：12%（目标 10%）✓
- 速度下降：8%（目标 < 10%）✓
- 长文本生成质量明显改善

**结论**：
- ✅ **PASS** - 层次化表示确实提升了长文本连贯性
- 建议在 full 实验中测试更多层次深度（2-4 层）

## 总体建议

### 继续 Full 实验的理由
1. 两个假设都通过了 pilot 验证
2. 初步结果超过预期目标
3. 没有发现重大技术障碍

### Full 实验重点
1. **H1**：优化复杂度估计算法，测试不同步数范围
2. **H2**：测试不同层次深度，分析计算成本权衡
3. **Ablation**：重点测试各组件的贡献度

### 风险提示
1. 小规模数据可能不能完全反映大规模数据的行为
2. 需要在 full 实验中验证泛化性能
3. 计算成本可能随数据规模非线性增长

## 预算估计

- Pilot 实际消耗：2.5 GPU-h
- Full 实验预估：15-18 GPU-h（包含 ablation 和 seed ensemble）
- 剩余预算：17.5 GPU-h（充足）
"""
    (pilot_dir / "motivation_validation.md").write_text(
        motivation_validation,
        encoding="utf-8"
    )

    # 3. 创建 pilot_code/run_pilot.py
    pilot_code = """#!/usr/bin/env python3
\"\"\"Pilot 实验执行脚本

支持参数：
- --smoke_test: 运行烟测（batch_size=2, max_steps=3）
- --seed: 随机种子（默认 42）
- --data_fraction: 数据比例（默认 0.1）
\"\"\"

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch


def set_seed(seed: int):
    \"\"\"设置随机种子以确保可复现性。\"\"\"
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def smoke_test():
    \"\"\"运行烟测：快速验证代码可以执行。\"\"\"
    print("Running smoke test...")

    # 模拟 forward + backward + optimizer.step()
    model = torch.nn.Linear(10, 10)
    optimizer = torch.optim.Adam(model.parameters())

    for step in range(3):
        x = torch.randn(2, 10)
        y = model(x)
        loss = y.sum()
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
        print(f"  Step {step+1}/3: loss={loss.item():.4f}")

    print("smoke_test: PASS")

    # 创建 marker 文件
    marker_path = Path("pilot/smoke_test_passed.marker")
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("smoke_test: PASS\\n")

    return 0


def run_pilot_experiment(seed: int, data_fraction: float):
    \"\"\"运行 pilot 实验。\"\"\"
    set_seed(seed)

    print(f"Running pilot experiment with seed={seed}, data_fraction={data_fraction}")

    # 模拟实验执行
    time.sleep(2)

    # 生成模拟结果
    results = {
        "seed": seed,
        "data_fraction": data_fraction,
        "experiments": [
            {
                "experiment_id": "pilot_h1",
                "status": "DONE",
                "metrics": {"accuracy": 0.75 + random.random() * 0.05},
            }
        ],
    }

    # 保存结果
    output_path = Path("pilot/pilot_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2))

    print("Pilot experiment completed successfully")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Pilot 实验执行脚本")
    parser.add_argument("--smoke_test", action="store_true", help="运行烟测")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--data_fraction", type=float, default=0.1, help="数据比例")

    args = parser.parse_args()

    if args.smoke_test:
        return smoke_test()
    else:
        return run_pilot_experiment(args.seed, args.data_fraction)


if __name__ == "__main__":
    exit(main())
"""
    (pilot_code_dir / "run_pilot.py").write_text(pilot_code, encoding="utf-8")

    # 4. 创建 smoke_test_passed.marker
    (pilot_dir / "smoke_test_passed.marker").write_text(
        "smoke_test: PASS\n",
        encoding="utf-8"
    )

    # 5. 创建 docker_digests.txt
    docker_digests = """# Docker 镜像 Digest 记录
# 用于确保实验可复现

researchos/system@sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
"""
    (pilot_dir / "docker_digests.txt").write_text(docker_digests, encoding="utf-8")


def main():
    """主测试函数。"""
    import tempfile

    print("=" * 80)
    print("T5 Pilot Mode Integration Test")
    print("=" * 80)

    # 创建临时 workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir)
        print(f"\n1. 准备测试 workspace: {workspace_dir}")
        prepare_pilot_workspace(workspace_dir)

        # 创建 ExperimenterAgent
        print("\n2. 创建 ExperimenterAgent")
        agent = ExperimenterAgent()
        print(f"   - Agent: {agent.spec.name}")
        print(f"   - Model tier: {agent.spec.model_tier}")
        print(f"   - Max steps: {agent.spec.max_steps}")

        # 创建 ExecutionContext（pilot 模式）
        print("\n3. 创建 ExecutionContext (mode=pilot)")
        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="test_pilot_project",
            task_id="T5",
            run_id="test_run_001",
            mode="pilot",
        )
        print(f"   - Mode: {ctx.mode}")
        print(f"   - Task: {ctx.task_id}")

        # 生成 system_prompt
        print("\n4. 生成 system_prompt")
        system_prompt = agent.system_prompt(ctx)
        print(f"   - Prompt length: {len(system_prompt)} 字符")
        print(f"   - Contains 'pilot': {'pilot' in system_prompt.lower()}")
        print(f"   - Contains 'smoke': {'smoke' in system_prompt.lower()}")

        # 生成 initial_user_message
        print("\n5. 生成 initial_user_message")
        initial_message = agent.initial_user_message(ctx)
        print(f"   - Message: {initial_message[:100]}...")

        # 创建输出文件
        print("\n6. 创建 pilot 输出文件")
        create_pilot_outputs(workspace_dir)
        print("   - pilot_results.json ✓")
        print("   - motivation_validation.md ✓")
        print("   - pilot_code/run_pilot.py ✓")
        print("   - smoke_test_passed.marker ✓")
        print("   - docker_digests.txt ✓")

        # 验证输出
        print("\n7. 验证输出")
        ok, err = agent.validate_outputs(ctx)
        if ok:
            print("   ✅ 输出校验通过")
        else:
            print(f"   ❌ 输出校验失败: {err}")
            return 1

        print("\n" + "=" * 80)
        print("✅ T5 Pilot Mode Integration Test PASSED")
        print("=" * 80)

        return 0


if __name__ == "__main__":
    exit(main())
