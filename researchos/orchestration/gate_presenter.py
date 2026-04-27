from __future__ import annotations

"""Gate 展示内容构造器。

这里把“gate 配置如何转换成给用户展示的内容”从状态机主体中拆出来，
避免 StateMachine 同时承担状态推进和展示拼装两类职责。
"""

from pathlib import Path
from typing import Any


def build_presentation(
    gate_spec: dict[str, Any],
    state: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    """根据 gates.yaml 的声明式规则生成展示内容。

    支持的规则：
    - `literal` / `value`：直接写死文本或对象
    - `from_file`：读取 workspace 内单个文件
    - `from_dir`：列出目录文件
    - `from_state`：从 state.yaml 中按点路径取值
    可选字段：
    - `max_chars`：对大文本做截断，避免 CLI 一次刷太多内容
    - `glob` / `max_items`：目录 listing 的过滤与截断
    """

    out: dict[str, Any] = {
        "_title": gate_spec.get("title", ""),
        "_description": gate_spec.get("description", ""),
    }
    for key, rule in (gate_spec.get("presentation") or {}).items():
        out[key] = _resolve_rule(rule, state=state, workspace=workspace)
    return out


def _resolve_rule(
    rule: Any,
    *,
    state: dict[str, Any],
    workspace: Path,
) -> Any:
    if isinstance(rule, str):
        return rule
    if not isinstance(rule, dict):
        return rule

    if "literal" in rule:
        return rule["literal"]
    if "value" in rule:
        return rule["value"]
    if "from_state" in rule:
        return _lookup_dotted(state, str(rule["from_state"]))
    if "from_file" in rule:
        path = workspace / str(rule["from_file"])
        if not path.exists():
            return f"[file not found: {rule['from_file']}]"
        text = path.read_text(encoding="utf-8", errors="replace")
        limit = rule.get("max_chars")
        if limit is not None and len(text) > int(limit):
            return text[: int(limit)] + f"\n\n[... truncated from {len(text)} chars]"
        return text
    if "from_file_regex" in rule:
        spec = rule["from_file_regex"] or {}
        path = workspace / str(spec.get("path", ""))
        if not path.exists():
            return spec.get("default", f"[file not found: {spec.get('path', '')}]")
        import re

        text = path.read_text(encoding="utf-8", errors="replace")
        pattern = spec.get("pattern")
        if not pattern:
            return spec.get("default")
        match = re.search(str(pattern), text, re.DOTALL | re.IGNORECASE)
        if match is None:
            return spec.get("default")
        group = int(spec.get("group", 1))
        try:
            return match.group(group).strip()
        except IndexError:
            return spec.get("default")
    if "from_dir" in rule:
        dir_path = workspace / str(rule["from_dir"])
        if not dir_path.is_dir():
            return []
        pattern = str(rule.get("glob", "*"))
        items = sorted(
            path.relative_to(workspace).as_posix()
            for path in dir_path.glob(pattern)
            if path.is_file()
        )
        max_items = rule.get("max_items")
        if max_items is not None:
            items = items[: int(max_items)]
        return items
    return rule


def _lookup_dotted(data: dict[str, Any], dotted_path: str) -> Any:
    """读取 `a.b.c` 形式的嵌套字段。"""
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
