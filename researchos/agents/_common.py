"""9个agent共享的helper函数，避免重复实现。

参考：Agent Dev Spec §1.2
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from ..runtime.agent import ExecutionContext


# ══════════════════════════════════════════════════════
# 1. Artifact 读取 helper
# ══════════════════════════════════════════════════════

def load_project(ctx: "ExecutionContext") -> dict:
    """读 workspace/project.yaml，所有agent都用。"""
    project_path = ctx.workspace_dir / "project.yaml"
    if not project_path.exists():
        return {}
    return yaml.safe_load(project_path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    """读JSONL格式artifact（papers_raw, papers_dedup等）。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    results = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            # 清理无效控制字符（如\x00）
            cleaned_line = line.replace('\x00', '')
            results.append(json.loads(cleaned_line))
        except json.JSONDecodeError as e:
            # 记录错误但继续处理其他行
            print(f"Warning: Failed to parse line {i}: {e}")
            continue
    return results


def append_jsonl(path: Path, records: list[dict]) -> None:
    """追加到JSONL（agent产出期间用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, records: list[dict]) -> None:
    """覆盖写入JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════
# 2. 标准 validate_outputs helper
# ══════════════════════════════════════════════════════

def validate_files_exist(
    ctx: "ExecutionContext", required: list[str]
) -> tuple[bool, str | None]:
    """检查workspace中必需文件存在。返回(ok, err_msg)。"""
    missing = []
    for rel in required:
        p = ctx.workspace_dir / rel
        if not p.exists():
            missing.append(rel)
    if missing:
        return False, f"缺少必需产出: {missing}"
    return True, None


def validate_jsonl_schema(
    path: Path,
    schema_name: str,
    min_count: int = 0,
    max_count: int | None = None,
) -> tuple[bool, str | None]:
    """校验JSONL的每行符合schema + 数量约束。

    Args:
        path: JSONL文件路径
        schema_name: 对应schemas/{schema_name}.schema.json
        min_count: 最少记录数
        max_count: 最多记录数（None表示不限）

    Returns:
        (ok, err_msg)
    """
    from ..schemas.validator import validate_record

    records = load_jsonl(path)
    if len(records) < min_count:
        return False, f"{path.name} 只有 {len(records)} 条，至少需要 {min_count} 条"
    if max_count and len(records) > max_count:
        return False, f"{path.name} 有 {len(records)} 条，超过上限 {max_count}"

    for i, rec in enumerate(records):
        ok, err = validate_record(rec, schema_name)
        if not ok:
            return False, f"{path.name}:第 {i+1} 条不合schema: {err}"

    return True, None


# ══════════════════════════════════════════════════════
# 3. State.yaml 轻量读写（agent只读用）
# ══════════════════════════════════════════════════════

def read_state(ctx: "ExecutionContext") -> dict:
    """Agent读state.yaml。注意agent不写state，由StateMachine统一管。"""
    state_path = ctx.workspace_dir / "state.yaml"
    if not state_path.exists():
        return {}
    return yaml.safe_load(state_path.read_text(encoding="utf-8"))


def read_iteration_count(ctx: "ExecutionContext", key: str) -> int:
    """读iteration_count[key]，用于T5重做、T7多轮实验。"""
    state = read_state(ctx)
    return state.get("iteration_count", {}).get(key, 0)


# ══════════════════════════════════════════════════════
# 4. 其他常用helper
# ══════════════════════════════════════════════════════

def ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def read_text_file(path: Path, default: str = "") -> str:
    """安全读取文本文件，不存在返回默认值。"""
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    """写入文本文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
