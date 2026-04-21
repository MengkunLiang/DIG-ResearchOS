#!/usr/bin/env python
"""迭代死锁检测功能演示（Phase 2.3）

演示如何使用迭代死锁检测来防止 Agent 在相同参数上无限循环。
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from researchos.orchestration.state_machine import StateMachine
from researchos.schemas.state import StateYaml


def demo_normal_iteration():
    """演示正常迭代：不同参数不会触发死锁检测。"""
    print("=" * 60)
    print("演示 1: 正常迭代（不同参数）")
    print("=" * 60)

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # 创建简单的状态机配置
        config_path = tmpdir / "state_machine.yaml"
        config = {
            "initial_state": "task_a",
            "states": {
                "task_a": {
                    "agent": "test_agent",
                    "inputs": {"input_file": "input.txt"},
                    "outputs": {"output_file": "output.txt"},
                    "llm": {"profile": "default", "tier": "fast"},
                    "next_on_success": "__terminal__",
                },
            },
        }
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

        sm = StateMachine(config_path)
        state = sm.create_initial_state("demo_project")
        workspace = tmpdir / "workspace"
        workspace.mkdir()

        # 第一次执行
        print("\n第1次执行（参数：tier=fast）")
        ctx1 = sm.build_execution_context(workspace, state)
        sm.start_task(state, ctx1.run_id)
        print(f"  ✓ 成功，run_id={ctx1.run_id}")

        # 修改参数后第二次执行
        sm.nodes["task_a"].llm = {"profile": "default", "tier": "slow"}
        print("\n第2次执行（参数：tier=slow）")
        ctx2 = sm.build_execution_context(workspace, state)
        sm.start_task(state, ctx2.run_id)
        print(f"  ✓ 成功，run_id={ctx2.run_id}")

        print(f"\n迭代历史记录数: {len(state.iteration_history.get('task_a', []))}")
        print("✓ 不同参数的迭代正常执行")


def demo_deadlock_detection():
    """演示死锁检测：相同参数3次后触发错误。"""
    print("\n" + "=" * 60)
    print("演示 2: 死锁检测（相同参数3次）")
    print("=" * 60)

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        config_path = tmpdir / "state_machine.yaml"
        config = {
            "initial_state": "task_a",
            "states": {
                "task_a": {
                    "agent": "test_agent",
                    "inputs": {"input_file": "input.txt"},
                    "outputs": {"output_file": "output.txt"},
                    "llm": {"profile": "default", "tier": "fast"},
                    "next_on_success": "__terminal__",
                },
            },
        }
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

        sm = StateMachine(config_path)
        state = sm.create_initial_state("demo_project")
        workspace = tmpdir / "workspace"
        workspace.mkdir()

        node = sm.nodes["task_a"]

        # 手动记录3次相同参数的迭代
        for i in range(3):
            sm._record_iteration_attempt(state, node)
            print(f"\n第{i+1}次尝试已记录")
            print(f"  参数哈希: {state.iteration_history['task_a'][i]['param_hash']}")

        # 第4次尝试应该触发死锁检测
        print("\n第4次尝试（相同参数）...")
        try:
            sm.build_execution_context(workspace, state)
            print("  ✗ 错误：应该触发死锁检测")
        except RuntimeError as e:
            print(f"  ✓ 成功触发死锁检测")
            print(f"  错误信息: {str(e)[:100]}...")


def demo_boundary_case():
    """演示边界情况：相同参数2次只警告不阻止。"""
    print("\n" + "=" * 60)
    print("演示 3: 边界情况（相同参数2次）")
    print("=" * 60)

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        config_path = tmpdir / "state_machine.yaml"
        config = {
            "initial_state": "task_a",
            "states": {
                "task_a": {
                    "agent": "test_agent",
                    "inputs": {"input_file": "input.txt"},
                    "outputs": {"output_file": "output.txt"},
                    "llm": {"profile": "default", "tier": "fast"},
                    "next_on_success": "__terminal__",
                },
            },
        }
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

        sm = StateMachine(config_path)
        state = sm.create_initial_state("demo_project")
        workspace = tmpdir / "workspace"
        workspace.mkdir()

        node = sm.nodes["task_a"]

        # 记录2次相同参数的迭代
        for i in range(2):
            sm._record_iteration_attempt(state, node)
            print(f"\n第{i+1}次尝试已记录")

        # 第3次尝试应该成功（只是警告）
        print("\n第3次尝试（相同参数）...")
        try:
            ctx = sm.build_execution_context(workspace, state)
            print(f"  ✓ 成功执行（会有警告日志）")
            print(f"  run_id={ctx.run_id}")
        except RuntimeError:
            print("  ✗ 错误：不应该在第3次尝试时阻止")


def demo_state_persistence():
    """演示状态持久化：iteration_history 可以序列化到 YAML。"""
    print("\n" + "=" * 60)
    print("演示 4: 状态持久化")
    print("=" * 60)

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # 创建包含 iteration_history 的状态
        state = StateYaml(
            project_id="demo_project",
            current_task="task_a",
            iteration_history={
                "task_a": [
                    {
                        "param_hash": "abc123",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "params": {"inputs": {"input_file": "input.txt"}},
                    },
                    {
                        "param_hash": "abc123",
                        "timestamp": "2024-01-01T01:00:00Z",
                        "params": {"inputs": {"input_file": "input.txt"}},
                    },
                ]
            },
        )

        # 保存到文件
        state_file = tmpdir / "state.yaml"
        state.dump_yaml(state_file)
        print(f"\n✓ 状态已保存到: {state_file}")

        # 读取并验证
        loaded_state = StateYaml.load_yaml(state_file)
        print(f"✓ 状态已加载")
        print(f"  迭代历史记录数: {len(loaded_state.iteration_history['task_a'])}")
        print(f"  第1次参数哈希: {loaded_state.iteration_history['task_a'][0]['param_hash']}")
        print(f"  第2次参数哈希: {loaded_state.iteration_history['task_a'][1]['param_hash']}")

        # 显示 YAML 内容
        print("\nstate.yaml 内容片段:")
        yaml_content = state_file.read_text(encoding="utf-8")
        for line in yaml_content.split("\n"):
            if "iteration_history" in line or "param_hash" in line or "timestamp" in line:
                print(f"  {line}")


if __name__ == "__main__":
    print("\n迭代死锁检测功能演示")
    print("Phase 2.3: 防止 Agent 在相同参数上无限迭代\n")

    demo_normal_iteration()
    demo_deadlock_detection()
    demo_boundary_case()
    demo_state_persistence()

    print("\n" + "=" * 60)
    print("演示完成！")
    print("=" * 60)
    print("\n关键特性:")
    print("  1. 自动检测相同参数的重复执行")
    print("  2. 3次以上触发错误，快速失败")
    print("  3. 2次时发出警告，不阻止执行")
    print("  4. 完整的状态持久化支持")
    print("  5. 向后兼容旧的 state.yaml 文件")
    print()
