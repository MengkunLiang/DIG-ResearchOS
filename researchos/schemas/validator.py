"""Artifact schema validation.

Provides JSON Schema validation for all ResearchOS artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft7Validator, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


# Schema目录
SCHEMA_DIR = Path(__file__).parent / "json_schemas"
_SCHEMAS_DIR = SCHEMA_DIR  # 向后兼容别名


def load_schema(schema_name: str) -> dict:
    """加载JSON Schema定义。

    Args:
        schema_name: schema名称，如"papers_dedup"

    Returns:
        Schema字典

    Raises:
        FileNotFoundError: schema文件不存在
    """
    schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    return json.loads(schema_path.read_text(encoding="utf-8"))


# 向后兼容别名
_load_schema = load_schema


def validate_record(record: dict, schema_name: str) -> tuple[bool, str | None]:
    """校验单条记录是否符合schema。

    Args:
        record: 待校验的记录
        schema_name: schema名称

    Returns:
        (ok, error_message)
    """
    if not HAS_JSONSCHEMA:
        # 如果没有安装jsonschema，只做基本检查
        return True, None

    try:
        schema = load_schema(schema_name)
    except FileNotFoundError as e:
        return False, f"Schema not found: {e}"

    try:
        Draft7Validator(schema).validate(record)
        return True, None
    except ValidationError as e:
        return False, f"Validation error: {e.message}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def validate_task_artifacts(
    task_id_or_workspace: str | Path,
    workspace_dir_or_task_id: Path | str | None = None,
    declared_outputs: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """校验task的所有输出artifacts。

    根据task_io_contract定义的outputs，检查每个artifact是否符合schema。

    支持两种调用方式：
    1. validate_task_artifacts(task_id, workspace_dir) - 旧版本，用于agent.py
    2. validate_task_artifacts(workspace, task_id, declared_outputs=...) - 新版本，用于CLI

    Args:
        task_id_or_workspace: Task ID 或 Workspace路径
        workspace_dir_or_task_id: Workspace路径 或 Task ID
        declared_outputs: 可选的声明输出映射

    Returns:
        (ok, error_message)
    """
    # 判断调用方式
    if isinstance(task_id_or_workspace, Path):
        # 新版本调用: validate_task_artifacts(workspace, task_id, declared_outputs=...)
        workspace_dir = task_id_or_workspace
        task_id = str(workspace_dir_or_task_id)
    else:
        # 旧版本调用: validate_task_artifacts(task_id, workspace_dir)
        task_id = task_id_or_workspace
        workspace_dir = workspace_dir_or_task_id
        if workspace_dir is None:
            return False, "workspace_dir is required"

    from ..orchestration.task_io_contract import get_task_io

    try:
        task_io = get_task_io(task_id)
    except KeyError:
        return False, f"Unknown task: {task_id}"

    # task_io是dict，不是对象
    outputs = task_io.get("outputs", {})
    if not outputs:
        # 没有定义outputs，跳过校验
        return True, None

    errors = []

    for output_name, output_path in outputs.items():
        # output_path是字符串，如"hello.txt"或"literature/papers_dedup.jsonl"
        file_path = workspace_dir / output_path

        # 检查文件存在
        if not file_path.exists():
            errors.append(f"Missing output: {output_name} ({output_path})")
            continue

        # 检查schema（如果定义）
        schemas = task_io.get("schemas", {})
        schema_name = schemas.get(output_name)

        if schema_name and HAS_JSONSCHEMA:
            # 根据文件类型验证
            if file_path.suffix == ".jsonl":
                ok, err = _validate_jsonl_file(file_path, schema_name)
            elif file_path.suffix == ".json":
                ok, err = _validate_json_file(file_path, schema_name)
            elif file_path.suffix in [".yaml", ".yml"]:
                ok, err = _validate_yaml_file(file_path, schema_name)
            else:
                # 未知格式，跳过schema验证
                continue

            if not ok:
                errors.append(f"Schema验证失败 {output_name}: {err}")

    if errors:
        return False, "; ".join(errors)

    return True, None


def validate_prerequisites(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
    """校验task的前置条件（输入artifacts）是否满足。

    根据task_io_contract定义的inputs，检查每个输入artifact是否存在且有效。

    Args:
        workspace_dir: Workspace根目录
        task_id: Task ID（如"T1", "T2"）

    Returns:
        (ok, error_message)
    """
    from ..orchestration.task_io_contract import get_task_io

    try:
        task_io = get_task_io(task_id)
    except KeyError:
        return False, f"Unknown task: {task_id}"

    # task_io是dict，不是对象
    inputs = task_io.get("inputs", {})
    required_inputs = task_io.get("required_inputs", [])

    if not inputs:
        # 没有定义inputs，跳过校验
        return True, None

    errors = []

    for input_name, input_path in inputs.items():
        file_path = workspace_dir / input_path

        # 检查文件存在
        if not file_path.exists():
            if input_name in required_inputs:
                errors.append(f"Missing required input: {input_name} ({input_path})")
            continue

    if errors:
        return False, "; ".join(errors)

    return True, None


def build_declared_outputs_from_state_machine(state_machine_config: Path, task_id: str) -> dict[str, str]:
    """从状态机配置中提取task的声明输出。

    Args:
        state_machine_config: 状态机配置文件路径
        task_id: Task ID

    Returns:
        输出名称到相对路径的映射
    """
    from ..orchestration.state_machine import StateMachine

    # 加载状态机配置
    state_machine = StateMachine(state_machine_config)

    if task_id not in state_machine.nodes:
        return {}

    node = state_machine.nodes[task_id]
    return dict(node.outputs or {})


def validate_declared_outputs(workspace_dir: Path, declared_outputs: dict[str, str]) -> tuple[bool, str | None]:
    """校验声明的输出文件是否都存在。

    Args:
        workspace_dir: Workspace根目录
        declared_outputs: 输出名称到相对路径的映射

    Returns:
        (ok, error_message)
    """
    missing = []
    for name, rel_path in declared_outputs.items():
        file_path = workspace_dir / rel_path
        if not file_path.exists():
            missing.append(f"{name} ({rel_path})")

    if missing:
        return False, f"Missing declared outputs: {', '.join(missing)}"

    return True, None


def _validate_jsonl_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验JSONL文件的每一行。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                return False, f"Line {i}: Invalid JSON: {e}"

            ok, err = validate_record(record, schema_name)
            if not ok:
                return False, f"Line {i}: {err}"

        return True, None
    except Exception as e:
        return False, f"Failed to read file: {e}"


def _validate_json_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验JSON文件。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_record(data, schema_name)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Failed to read file: {e}"


def _validate_yaml_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验YAML文件。"""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return validate_record(data, schema_name)
    except yaml.YAMLError as e:
        return False, f"Invalid YAML: {e}"
    except Exception as e:
        return False, f"Failed to read file: {e}"


# ══════════════════════════════════════════════════════
# 内置checker函数（用于特定task的复杂校验）
# ══════════════════════════════════════════════════════

_TASK_CHECKERS: dict[str, Any] = {}


def register_task_checker(task_id: str, checker_fn):
    """注册task专属的checker函数。

    Checker函数签名: (workspace_dir: Path) -> tuple[bool, str | None]
    """
    _TASK_CHECKERS[task_id] = checker_fn


def get_task_checker(task_id: str):
    """获取task的checker函数。"""
    return _TASK_CHECKERS.get(task_id)


def register_builtin_task_checkers():
    """注册内置的task checker。"""

    def check_hello(workspace_dir: Path) -> tuple[bool, str | None]:
        """HELLO task checker。"""
        hello_file = workspace_dir / "hello.txt"
        if not hello_file.exists():
            return False, "hello.txt not found"
        content = hello_file.read_text(encoding="utf-8")
        if "Hello" not in content:
            return False, "hello.txt does not contain 'Hello'"
        return True, None

    register_task_checker("HELLO", check_hello)

    # TODO: 为T1-T9添加更多checker


# 启动时自动注册
register_builtin_task_checkers()

