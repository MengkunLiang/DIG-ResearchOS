"""测试迭代死锁检测功能（Phase 2.3）。

验证 StateMachine 能够检测并阻止相同参数的无限迭代。
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from researchos.orchestration.state_machine import StateMachine
from researchos.schemas.state import StateYaml


@pytest.fixture
def temp_config_dir():
    """创建临时配置目录。"""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def simple_fsm_config(temp_config_dir: Path):
    """创建简单的状态机配置。"""
    config_path = temp_config_dir / "state_machine.yaml"
    config = {
        "initial_state": "task_a",
        "states": {
            "task_a": {
                "agent": "test_agent",
                "inputs": {"input_file": "input.txt"},
                "outputs": {"output_file": "output.txt"},
                "llm": {"profile": "default", "tier": "fast"},
                "next_on_success": "task_b",
                "next_on_failure": "failed",
            },
            "task_b": {
                "agent": "test_agent",
                "next_on_success": "__terminal__",
                "next_on_failure": "failed",
            },
            "failed": {"terminal": True},
        },
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_normal_iteration_different_params(simple_fsm_config: Path, temp_config_dir: Path):
    """测试正常迭代：不同参数不应触发死锁检测。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")

    # 第一次执行
    workspace = temp_config_dir / "workspace"
    workspace.mkdir()
    ctx1 = sm.build_execution_context(workspace, state)
    assert ctx1.task_id == "task_a"

    # 修改节点参数（模拟不同配置）
    sm.nodes["task_a"].llm = {"profile": "default", "tier": "slow"}

    # 第二次执行（参数不同）
    ctx2 = sm.build_execution_context(workspace, state)
    assert ctx2.task_id == "task_a"

    # 应该成功，不抛出异常
    assert len(state.iteration_history.get("task_a", [])) == 0  # build_execution_context 不记录


def test_deadlock_detection_same_params_three_times(simple_fsm_config: Path, temp_config_dir: Path):
    """测试死锁检测：相同参数尝试3次应触发错误。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")
    workspace = temp_config_dir / "workspace"
    workspace.mkdir()

    # 手动记录3次相同参数的迭代历史
    node = sm.nodes["task_a"]
    for _ in range(3):
        sm._record_iteration_attempt(state, node)

    # 第4次尝试应该触发死锁检测
    with pytest.raises(RuntimeError, match="检测到迭代死锁"):
        sm.build_execution_context(workspace, state)


def test_production_t4_failure_does_not_self_loop_to_deadlock():
    """T4 内部已有 retry；状态机失败应保留真实错误，不做同参数自循环。"""
    sm = StateMachine(Path("config/system_config/state_machine.yaml"))

    assert sm.nodes["T4"].next_on_failure == "failed"


def test_boundary_case_two_same_params(simple_fsm_config: Path, temp_config_dir: Path):
    """测试边界情况：相同参数2次应发出警告但不阻止。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")
    workspace = temp_config_dir / "workspace"
    workspace.mkdir()

    # 手动记录2次相同参数的迭代历史
    node = sm.nodes["task_a"]
    for _ in range(2):
        sm._record_iteration_attempt(state, node)

    # 第3次尝试应该成功（只是警告）
    ctx = sm.build_execution_context(workspace, state)
    assert ctx.task_id == "task_a"


def test_iteration_history_recording(simple_fsm_config: Path, temp_config_dir: Path):
    """测试迭代历史记录功能。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")

    node = sm.nodes["task_a"]

    # 记录第一次迭代
    sm._record_iteration_attempt(state, node)
    assert "task_a" in state.iteration_history
    assert len(state.iteration_history["task_a"]) == 1

    entry = state.iteration_history["task_a"][0]
    assert "param_hash" in entry
    assert "timestamp" in entry
    assert "params" in entry
    assert entry["params"]["inputs"] == {"input_file": "input.txt"}
    assert entry["params"]["outputs"] == {"output_file": "output.txt"}

    # 记录第二次迭代
    sm._record_iteration_attempt(state, node)
    assert len(state.iteration_history["task_a"]) == 2

    # 两次参数相同，哈希应该相同
    assert state.iteration_history["task_a"][0]["param_hash"] == state.iteration_history["task_a"][1]["param_hash"]


def test_param_hash_consistency(simple_fsm_config: Path):
    """测试参数哈希的一致性。"""
    sm = StateMachine(simple_fsm_config)

    params1 = {"inputs": {"a": "1"}, "outputs": {"b": "2"}}
    params2 = {"outputs": {"b": "2"}, "inputs": {"a": "1"}}  # 顺序不同

    hash1 = sm._compute_param_hash(params1)
    hash2 = sm._compute_param_hash(params2)

    # 参数内容相同，哈希应该相同（不受顺序影响）
    assert hash1 == hash2


def test_param_hash_difference(simple_fsm_config: Path):
    """测试不同参数产生不同哈希。"""
    sm = StateMachine(simple_fsm_config)

    params1 = {"inputs": {"a": "1"}}
    params2 = {"inputs": {"a": "2"}}

    hash1 = sm._compute_param_hash(params1)
    hash2 = sm._compute_param_hash(params2)

    # 参数不同，哈希应该不同
    assert hash1 != hash2


def test_start_task_records_iteration(simple_fsm_config: Path, temp_config_dir: Path):
    """测试 start_task 自动记录迭代历史。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")
    workspace = temp_config_dir / "workspace"
    workspace.mkdir()

    # 构建 execution context 获取 run_id
    ctx = sm.build_execution_context(workspace, state)

    # start_task 应该记录迭代历史
    state = sm.start_task(state, ctx.run_id)

    assert "task_a" in state.iteration_history
    assert len(state.iteration_history["task_a"]) == 1


def test_backward_compatibility_no_iteration_history(simple_fsm_config: Path, temp_config_dir: Path):
    """测试向后兼容：旧 state.yaml 没有 iteration_history 字段。"""
    sm = StateMachine(simple_fsm_config)

    # 创建没有 iteration_history 的旧状态
    state = StateYaml(
        project_id="test_project",
        current_task="task_a",
        status="RUNNING",
    )

    # 应该自动初始化为空字典
    assert state.iteration_history == {}

    workspace = temp_config_dir / "workspace"
    workspace.mkdir()

    # 应该能正常工作
    ctx = sm.build_execution_context(workspace, state)
    assert ctx.task_id == "task_a"


def test_different_tasks_separate_history(simple_fsm_config: Path):
    """测试不同任务的迭代历史是独立的。"""
    sm = StateMachine(simple_fsm_config)
    state = sm.create_initial_state("test_project")

    node_a = sm.nodes["task_a"]
    node_b = sm.nodes["task_b"]

    # 记录 task_a 的迭代
    state.current_task = "task_a"
    sm._record_iteration_attempt(state, node_a)
    sm._record_iteration_attempt(state, node_a)

    # 记录 task_b 的迭代
    state.current_task = "task_b"
    sm._record_iteration_attempt(state, node_b)

    # 验证历史是独立的
    assert len(state.iteration_history["task_a"]) == 2
    assert len(state.iteration_history["task_b"]) == 1


def test_extract_task_params_comprehensive(simple_fsm_config: Path):
    """测试参数提取包含所有关键字段。"""
    sm = StateMachine(simple_fsm_config)
    node = sm.nodes["task_a"]

    params = sm._extract_task_params(node)

    # 验证所有关键参数都被提取
    assert "inputs" in params
    assert "outputs" in params
    assert "llm" in params
    assert params["inputs"] == {"input_file": "input.txt"}
    assert params["outputs"] == {"output_file": "output.txt"}
    assert params["llm"] == {"profile": "default", "tier": "fast"}
