from __future__ import annotations

"""ResearchOS artifact 校验器。"""

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping

import jsonschema
import yaml


_SCHEMAS_DIR = Path(__file__).parent / "json_schemas"
_TASK_CHECKERS: dict[str, Callable[[Path], tuple[bool, list[str]]]] = {}


@lru_cache(maxsize=64)
def _load_schema(schema_name: str) -> dict:
    """按名称加载 schema 文件，并做进程级缓存。"""
    path = _SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_record(record: object, schema_name: str) -> tuple[bool, str | None]:
    """校验单条对象是否符合指定 schema。"""
    schema = _load_schema(schema_name)
    try:
        jsonschema.validate(instance=record, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:
        location = " > ".join(str(part) for part in exc.absolute_path) or "<root>"
        return False, f"{location}: {exc.message}"


def validate_against_schema(data: object, schema_name: str) -> tuple[bool, str | None]:
    return validate_record(data, schema_name)


def validate_jsonl_file(
    path: Path,
    schema_name: str,
    *,
    min_count: int = 0,
    max_count: int | None = None,
) -> tuple[bool, list[str]]:
    """校验一个 JSONL 文件里的每一行。"""
    if not path.exists():
        return False, [f"File not found: {path}"]
    errors: list[str] = []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < min_count:
        errors.append(f"{path.name} has {len(lines)} records, expected at least {min_count}")
    if max_count is not None and len(lines) > max_count:
        errors.append(f"{path.name} has {len(lines)} records, expected at most {max_count}")
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index} invalid JSON: {exc}")
            continue
        ok, err = validate_record(record, schema_name)
        if not ok and err:
            errors.append(f"{path.name}:{index} {err}")
    return not errors, errors


def register_task_checker(task_id: str, checker: Callable[[Path], tuple[bool, list[str]]]) -> None:
    """注册某个 task 的专用 checker。"""
    _TASK_CHECKERS[task_id] = checker


def validate_declared_outputs(
    workspace: Path,
    declared_outputs: Mapping[str, str] | Mapping[str, Path],
) -> tuple[bool, list[str]]:
    """按状态机声明的 outputs 做最基础的“文件是否存在”校验。"""
    errors: list[str] = []
    for name, rel_path in declared_outputs.items():
        candidate = rel_path if isinstance(rel_path, Path) else workspace / str(rel_path)
        if not candidate.exists():
            errors.append(f"Missing output '{name}': {candidate.relative_to(workspace)}")
    return not errors, errors


def validate_task_artifacts(
    workspace: Path,
    task_id: str,
    *,
    declared_outputs: Mapping[str, str] | Mapping[str, Path] | None = None,
) -> tuple[bool, list[str]]:
    """统一 task 校验入口。

    优先级：
    1. 若 task_id 已注册专用 checker，用专用 checker；
    2. 否则若提供了 declared_outputs，则退回到“检查声明输出存在”；
    3. 两者都没有时返回失败，让上层知道当前 task 还没有校验器。
    """
    checker = _TASK_CHECKERS.get(task_id)
    if checker is not None:
        return checker(workspace)
    if declared_outputs is not None:
        return validate_declared_outputs(workspace, declared_outputs)
    return False, [f"No checker registered for task {task_id}"]


def register_builtin_task_checkers() -> None:
    """注册当前仓库里能实际工作的最小 checker。"""
    if "HELLO" not in _TASK_CHECKERS:
        register_task_checker("HELLO", _check_hello)
    if "T1" not in _TASK_CHECKERS:
        register_task_checker("T1", _check_t1)


def _check_hello(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    hello = workspace / "hello.txt"
    if not hello.exists():
        errors.append("hello.txt missing")
    elif hello.read_text(encoding="utf-8").strip() != "Hello, Runtime!":
        errors.append("hello.txt content must be 'Hello, Runtime!'")
    return not errors, errors


def _check_t1(workspace: Path) -> tuple[bool, list[str]]:
    """当前仓库还没有真正的 T1 agent，这里只实现保守的基础检查。"""
    errors: list[str] = []
    project_yaml = workspace / "project.yaml"
    state_yaml = workspace / "state.yaml"
    if not project_yaml.exists():
        errors.append("project.yaml missing")
    else:
        try:
            yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"project.yaml load failed: {exc}")
    if not state_yaml.exists():
        errors.append("state.yaml missing")
    return not errors, errors


def build_declared_outputs_from_state_machine(
    state_machine_path: Path,
    task_id: str,
) -> dict[str, str] | None:
    """从 state_machine.yaml 中提取某个 task 声明的 outputs。"""
    raw = yaml.safe_load(state_machine_path.read_text(encoding="utf-8")) or {}
    source = raw.get("states") or raw.get("nodes") or {}
    if isinstance(source, list):
        for node in source:
            if node.get("id") == task_id:
                return dict(node.get("outputs") or {})
        return None
    node = source.get(task_id)
    if not node:
        return None
    return dict(node.get("outputs") or {})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m researchos.schemas.validator")
    parser.add_argument("workspace")
    parser.add_argument("--task", required=True)
    parser.add_argument("--state-machine", default="config/state_machine.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    register_builtin_task_checkers()
    args = build_arg_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve()
    declared_outputs = build_declared_outputs_from_state_machine(
        Path(args.state_machine).resolve(),
        args.task,
    )
    ok, errors = validate_task_artifacts(
        workspace,
        args.task,
        declared_outputs=declared_outputs,
    )
    payload = {"ok": ok, "task": args.task, "errors": errors}
    print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
