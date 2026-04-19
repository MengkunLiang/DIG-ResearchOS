from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable

import jsonschema


_SCHEMAS_DIR = Path(__file__).parent / "json_schemas"
_TASK_CHECKERS: dict[str, Callable[[Path], tuple[bool, list[str]]]] = {}


@lru_cache(maxsize=64)
def _load_schema(schema_name: str) -> dict:
    path = _SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_record(record: object, schema_name: str) -> tuple[bool, str | None]:
    schema = _load_schema(schema_name)
    try:
        jsonschema.validate(instance=record, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:
        location = " > ".join(str(part) for part in exc.absolute_path) or "<root>"
        return False, f"{location}: {exc.message}"


def validate_against_schema(data: object, schema_name: str) -> tuple[bool, str | None]:
    return validate_record(data, schema_name)


def register_task_checker(task_id: str, checker: Callable[[Path], tuple[bool, list[str]]]) -> None:
    _TASK_CHECKERS[task_id] = checker


def validate_task_artifacts(workspace: Path, task_id: str) -> tuple[bool, list[str]]:
    checker = _TASK_CHECKERS.get(task_id)
    if checker is None:
        return False, [f"No checker registered for task {task_id}"]
    return checker(workspace)
