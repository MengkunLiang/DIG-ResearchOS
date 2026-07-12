#!/usr/bin/env python3
"""Shared, stdlib-only helpers for the experiment-design skill."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def find_workspace(start: Path) -> Path:
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "project.yaml").exists() and (candidate / "external_executor").is_dir():
            return candidate
    raise FileNotFoundError("Could not locate workspace containing project.yaml and external_executor/")


def resolve_workspace(value: str | None) -> Path:
    if value:
        workspace = Path(value).expanduser().resolve()
        if not (workspace / "project.yaml").exists() or not (workspace / "external_executor").is_dir():
            raise FileNotFoundError(f"Invalid workspace: {workspace}")
        return workspace
    return find_workspace(Path.cwd())


def resolve_in_workspace(workspace: Path, value: str) -> Path:
    raw = value.replace("<workspace>", str(workspace)).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve(strict=False)


def relpath(workspace: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(workspace.resolve()).as_posix()


def parse_allowed_paths(workspace: Path) -> tuple[list[Path], list[Path]]:
    policy_path = workspace / "external_executor" / "allowed_paths.txt"
    if not policy_path.exists():
        raise FileNotFoundError(policy_path)
    allowed: list[Path] = []
    denied: list[Path] = []
    for raw_line in policy_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        target = allowed
        for prefix in ("deny:", "forbid:", "!", "-"):
            if line.lower().startswith(prefix):
                target = denied
                line = line[len(prefix):].strip()
                break
        else:
            for prefix in ("allow:", "write:", "+"):
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
        if not line:
            continue
        if any(ch in line for ch in "*?["):
            indices = [line.find(ch) for ch in "*?[" if line.find(ch) >= 0]
            line = line[: min(indices)].rstrip("/") or "."
        target.append(resolve_in_workspace(workspace, line))
    return allowed, denied


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def assert_write_allowed(workspace: Path, path: Path) -> None:
    allowed, denied = parse_allowed_paths(workspace)
    resolved = path.resolve(strict=False)
    if any(is_within(resolved, d) for d in denied):
        raise PermissionError(f"Path is explicitly denied: {resolved}")
    if not allowed or not any(is_within(resolved, a) for a in allowed):
        raise PermissionError(f"Path is outside allowed write roots: {resolved}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_hash(data: Any) -> str:
    return sha256_bytes(canonical_json(data).encode("utf-8"))


def schema_major(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:^|[._-])v?(\d+)(?:$|[._-])", value)
    if not match:
        match = re.search(r"v(\d+)$", value)
    return int(match.group(1)) if match else None


def get_nested(data: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok:
            return current
    return default


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def slugify(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:64] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    text = "|".join(str(p) for p in parts)
    first = str(parts[0]) if parts else prefix
    return f"{prefix}-{slugify(first)}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def deep_without(data: Any, keys: set[str]) -> Any:
    if isinstance(data, dict):
        return {k: deep_without(v, keys) for k, v in data.items() if k not in keys}
    if isinstance(data, list):
        return [deep_without(v, keys) for v in data]
    return data


def ensure_known_ids(values: Iterable[str], known: set[str], label: str) -> list[str]:
    return [f"Unknown {label}: {value}" for value in values if value not in known]


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
