#!/usr/bin/env python3
"""
断点恢复机制验证测试脚本。

测试内容：
1. PAUSED 状态检测
2. WAITING_HUMAN 状态检测
3. resume 场景检测（interrupted/retry_after_failure/iteration）
4. 状态持久化验证

运行方式：
    python scripts/test_resume_mechanism.py [--verbose]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.orchestration.state_machine import StateMachine
from researchos.schemas.state import StateYaml, TaskHistoryEntry
from researchos.runtime.agent import AgentResult


def log(msg: str, verbose: bool = True) -> None:
    """打印日志。"""
    if verbose:
        print(f"[Resume Test] {msg}", flush=True)


def log_result(name: str, passed: bool, details: str = "") -> None:
    """打印测试结果。"""
    status = "✅" if passed else "❌"
    print(f"  {status} {name}", flush=True)
    if details:
        print(f"     {details}", flush=True)


def test_paused_state_detection() -> dict[str, Any]:
    """测试 PAUSED 状态检测。"""
    print("\n" + "=" * 60)
    print("测试 1: PAUSED 状态检测")
    print("=" * 60)

    results = {}

    # 创建 PAUSED 状态的 state（无需 StateMachine 实例）

    # 创建 PAUSED 状态的 state
    state = StateYaml(
        project_id="test-project",
        status="PAUSED",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-001",
                status="INTERRUPTED",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                stop_reason=AgentResult.STOP_INTERRUPTED,
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    # 验证 PAUSED 状态
    is_paused = state.status == "PAUSED"
    log_result("state.status == 'PAUSED'", is_paused)
    results["paused_status"] = is_paused

    # 验证最近一次 history 是 INTERRUPTED
    last_history = state.history[-1] if state.history else None
    is_interrupted = last_history and last_history.status == "INTERRUPTED"
    log_result("最近一次 history.status == 'INTERRUPTED'", is_interrupted)
    results["last_history_interrupted"] = is_interrupted

    # 验证 stop_reason 是 STOP_INTERRUPTED
    is_stopped_by_interrupt = (
        last_history and last_history.stop_reason == AgentResult.STOP_INTERRUPTED
    )
    log_result("stop_reason == AgentResult.STOP_INTERRUPTED", is_stopped_by_interrupt)
    results["stopped_by_interrupt"] = is_stopped_by_interrupt

    results["passed"] = is_paused and is_interrupted and is_stopped_by_interrupt
    return results


def test_waiting_human_state() -> dict[str, Any]:
    """测试 WAITING_HUMAN 状态检测。"""
    print("\n" + "=" * 60)
    print("测试 2: WAITING_HUMAN 状态检测")
    print("=" * 60)

    results = {}

    # 创建 WAITING_HUMAN 状态的 state
    state = StateYaml(
        project_id="test-project",
        status="WAITING_HUMAN",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        pending_gate={
            "gate_id": "T4-Gate2",
            "task_id": "T4-Ideation",
            "presented_at": datetime.now().isoformat(),
            "presentation": {
                "title": "请选择研究方向",
                "content": "以下是可选的研究方向：",
                "items": [
                    {"value": "1", "label": "方案A"},
                    {"value": "2", "label": "方案B"},
                ],
            },
            "options": [
                {"value": "1", "label": "方案A"},
                {"value": "2", "label": "方案B"},
            ],
        },
        history=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    # 验证 WAITING_HUMAN 状态
    is_waiting_human = state.status == "WAITING_HUMAN"
    log_result("state.status == 'WAITING_HUMAN'", is_waiting_human)
    results["waiting_human_status"] = is_waiting_human

    # 验证 pending_gate 存在
    has_pending_gate = state.pending_gate is not None
    log_result("state.pending_gate is not None", has_pending_gate)
    results["has_pending_gate"] = has_pending_gate

    # 验证 pending_gate 内容
    gate_valid = (
        has_pending_gate
        and state.pending_gate.gate_id == "T4-Gate2"
        and len(state.pending_gate.options) == 2
    )
    log_result("pending_gate 内容有效", gate_valid)
    results["gate_valid"] = gate_valid

    results["passed"] = is_waiting_human and has_pending_gate and gate_valid
    return results


def test_resume_scenarios() -> dict[str, Any]:
    """测试各种 resume 场景检测。"""
    print("\n" + "=" * 60)
    print("测试 3: Resume 场景检测")
    print("=" * 60)

    results = {}

    # 使用项目中的 state_machine.yaml 配置
    config_path = Path(__file__).parent.parent / "config" / "state_machine.yaml"
    sm = StateMachine(config_path)

    # 场景 1: INTERRUPTED → resume
    log("场景 1: INTERRUPTED → resume", True)
    state1 = StateYaml(
        project_id="test-project",
        status="PAUSED",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-001",
                status="INTERRUPTED",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                stop_reason=AgentResult.STOP_INTERRUPTED,
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    ctx1 = sm.build_execution_context(
        workspace_dir=Path("/tmp/test_workspace"),
        state=state1,
    )
    scenario1_passed = (
        ctx1.extra.get("is_resume") == True
        and ctx1.extra.get("resumed_from_run_id") == "run-001"
        and ctx1.extra.get("resume_reason") == "interrupted"
    )
    log_result("场景 1: is_resume + resumed_from_run_id + resume_reason=interrupted", scenario1_passed)
    results["scenario1"] = scenario1_passed

    # 场景 2: FAILED → retry_after_failure
    log("场景 2: FAILED → retry_after_failure", True)
    state2 = StateYaml(
        project_id="test-project",
        status="PAUSED",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-002",
                status="FAILED",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                stop_reason=AgentResult.STOP_MAX_STEPS,
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    ctx2 = sm.build_execution_context(
        workspace_dir=Path("/tmp/test_workspace"),
        state=state2,
    )
    scenario2_passed = (
        ctx2.extra.get("is_resume") == True
        and ctx2.extra.get("resumed_from_run_id") == "run-002"
        and ctx2.extra.get("resume_reason") == "retry_after_failure"
    )
    log_result("场景 2: resume_reason=retry_after_failure", scenario2_passed)
    results["scenario2"] = scenario2_passed

    # 场景 3: iteration
    log("场景 3: iteration", True)
    state3 = StateYaml(
        project_id="test-project",
        status="PAUSED",
        current_task="T4",
        iteration_count={"T4": 1},
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-003",
                status="DONE",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                stop_reason=AgentResult.STOP_MAX_STEPS,
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    ctx3 = sm.build_execution_context(
        workspace_dir=Path("/tmp/test_workspace"),
        state=state3,
    )
    scenario3_passed = (
        ctx3.extra.get("is_resume") == True
        and ctx3.extra.get("resumed_from_run_id") == "run-003"
        and ctx3.extra.get("resume_reason") == "iteration"
    )
    log_result("场景 3: resume_reason=iteration", scenario3_passed)
    results["scenario3"] = scenario3_passed

    # 场景 4: 非 resume 场景（正常启动）
    log("场景 4: 非 resume 场景（正常启动）", True)
    state4 = StateYaml(
        project_id="test-project",
        status="RUNNING",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    ctx4 = sm.build_execution_context(
        workspace_dir=Path("/tmp/test_workspace"),
        state=state4,
    )
    scenario4_passed = (
        ctx4.extra.get("is_resume") is None
        or ctx4.extra.get("is_resume") == False
    )
    log_result("场景 4: 正常启动不设置 is_resume", scenario4_passed)
    results["scenario4"] = scenario4_passed

    results["passed"] = all([results.get(f"scenario{i}", False) for i in range(1, 5)])
    return results


def test_mark_interrupted() -> dict[str, Any]:
    """测试 mark_interrupted 方法。"""
    print("\n" + "=" * 60)
    print("测试 4: mark_interrupted 方法")
    print("=" * 60)

    results = {}

    # 使用项目中的 state_machine.yaml 配置
    config_path = Path(__file__).parent.parent / "config" / "state_machine.yaml"
    sm = StateMachine(config_path)

    # 创建 RUNNING 状态的 state
    state = StateYaml(
        project_id="test-project",
        status="RUNNING",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-001",
                status="RUNNING",
                started_at=datetime.now().isoformat(),
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    # 调用 mark_interrupted
    paused_state = sm.mark_interrupted(state)

    # 验证状态转换
    is_paused = paused_state.status == "PAUSED"
    log_result("状态转换为 PAUSED", is_paused)
    results["status_paused"] = is_paused

    # 验证 history 状态更新
    last_history = paused_state.history[-1]
    is_interrupted = last_history.status == "INTERRUPTED"
    log_result("history.status 转换为 INTERRUPTED", is_interrupted)
    results["history_interrupted"] = is_interrupted

    # 验证 stop_reason 设置
    is_stopped = last_history.stop_reason == AgentResult.STOP_INTERRUPTED
    log_result("stop_reason 设置为 STOP_INTERRUPTED", is_stopped)
    results["stop_reason"] = is_stopped

    # 验证 paused_at 设置
    has_paused_at = paused_state.paused_at is not None
    log_result("paused_at 已设置", has_paused_at)
    results["has_paused_at"] = has_paused_at

    results["passed"] = is_paused and is_interrupted and is_stopped and has_paused_at
    return results


def test_state_persistence() -> dict[str, Any]:
    """测试状态持久化。"""
    print("\n" + "=" * 60)
    print("测试 5: 状态持久化验证")
    print("=" * 60)

    results = {}

    import yaml

    # 创建测试 state
    state = StateYaml(
        project_id="test-project",
        status="PAUSED",
        current_task="T4",
        workspace_dir=Path("/tmp/test_workspace"),
        history=[
            TaskHistoryEntry(
                task="T4",
                run_id="run-001",
                status="INTERRUPTED",
                started_at=datetime.now().isoformat(),
                finished_at=datetime.now().isoformat(),
                stop_reason=AgentResult.STOP_INTERRUPTED,
            )
        ],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        paused_at=datetime.now().isoformat(),
    )

    # 序列化为 YAML
    state_dict = state.model_dump(mode="json")
    yaml_str = yaml.dump(state_dict, allow_unicode=True, default_flow_style=False)

    log(f"序列化后的 YAML:\n{yaml_str[:500]}...", True)

    # 反序列化
    loaded_dict = yaml.safe_load(yaml_str)
    loaded_state = StateYaml(**loaded_dict)

    # 验证关键字段
    field_checks = [
        ("project_id", state.project_id, loaded_state.project_id),
        ("status", state.status, loaded_state.status),
        ("current_task", state.current_task, loaded_state.current_task),
        ("history[0].status", state.history[0].status, loaded_state.history[0].status),
        ("paused_at", state.paused_at, loaded_state.paused_at),
    ]

    all_match = True
    for field_name, original, loaded in field_checks:
        matches = original == loaded
        if not matches:
            all_match = False
            log_result(f"字段 {field_name}", matches, f"原值={original}, 加载值={loaded}")
        else:
            log_result(f"字段 {field_name}", matches)

    results["persistence"] = all_match
    results["passed"] = all_match
    return results


def main() -> int:
    """主函数。"""
    print("=" * 60)
    print("断点恢复机制验证测试套件")
    print("=" * 60)

    all_results = {}

    # 运行所有测试
    all_results["paused_state_detection"] = test_paused_state_detection()
    all_results["waiting_human_state"] = test_waiting_human_state()
    all_results["resume_scenarios"] = test_resume_scenarios()
    all_results["mark_interrupted"] = test_mark_interrupted()
    all_results["state_persistence"] = test_state_persistence()

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    total_tests = 0
    passed_tests = 0

    for test_name, result in all_results.items():
        passed = result.get("passed", False)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} - {test_name}")
        total_tests += 1
        if passed:
            passed_tests += 1

    print(f"\n总计: {passed_tests}/{total_tests} 测试通过")

    # 输出详细结果 JSON
    output_file = Path("/tmp/resume_test_results.json")
    output_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n详细结果已保存到: {output_file}")

    return 0 if passed_tests == total_tests else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="断点恢复机制验证测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    sys.exit(main())
