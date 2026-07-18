#!/usr/bin/env python3
"""Shared stdlib-only helpers for the method-refinement skill."""
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


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
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
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(workspace.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {value}") from exc
    return resolved


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
        target: list[Path] | None = allowed
        lowered = line.lower()
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in {"rw", "write", "allow", "ro", "read", "no", "deny", "forbid"}:
            mode, line = parts[0].lower(), parts[1].strip()
            target = allowed if mode in {"rw", "write", "allow"} else denied if mode in {"no", "deny", "forbid"} else None
        else:
            matched = False
            for prefix in ("deny:", "forbid:", "!", "-"):
                if lowered.startswith(prefix):
                    target = denied
                    line = line[len(prefix):].strip()
                    matched = True
                    break
            if not matched:
                for prefix in ("allow:", "write:", "+"):
                    if lowered.startswith(prefix):
                        line = line[len(prefix):].strip()
                        break
        if target is None:
            continue
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


def first_nonempty(*values: Any, default: Any = None) -> Any:
    for value in values:
        if nonempty(value):
            return value
    return default


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def dictify(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def normalized_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def semantically_equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return normalized_text(a) == normalized_text(b)
    return canonical_json(a) == canonical_json(b)


def active_iteration_plan(result: dict[str, Any]) -> dict[str, Any]:
    direct = result.get("current_iteration_plan")
    if isinstance(direct, dict) and direct:
        return direct
    plans = result.get("iteration_plans")
    plan_items = plans.get("items", []) if isinstance(plans, dict) else plans if isinstance(plans, list) else []
    if plan_items:
        active = [p for p in plan_items if isinstance(p, dict) and p.get("status") in {"active", "planned", "running"}]
        return (active or [p for p in plan_items if isinstance(p, dict)])[-1]
    return {}


def latest_record(result: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    for key in keys:
        value = result.get(key)
        if isinstance(value, list) and value:
            dicts = [item for item in value if isinstance(item, dict)]
            if dicts:
                return dicts[-1]
        if isinstance(value, dict) and value:
            records = value.get("items")
            if isinstance(records, list):
                dicts = [item for item in records if isinstance(item, dict)]
                if dicts:
                    return dicts[-1]
            return value
    return {}


def extract_protocol_fingerprint(result: dict[str, Any]) -> str:
    plan = dictify(result.get("experiment_plan"))
    fp = plan.get("protocol_fingerprint")
    if isinstance(fp, str):
        return fp
    if isinstance(fp, dict):
        return str(fp.get("fingerprint") or fp.get("sha256") or "")
    return str(get_nested(plan, "protocol_snapshot.protocol_fingerprint", default="") or "")


def extract_plan_version(result: dict[str, Any]) -> int:
    value = get_nested(result, "experiment_plan.plan_version", default=0)
    return int(value) if isinstance(value, int) else 0


def component_id(item: dict[str, Any], prefix: str = "M") -> str:
    return str(item.get("module_id") or item.get("component_id") or stable_id(prefix, item.get("name") or item.get("id") or "component"))


def flatten_source_refs(*values: Any) -> list[str]:
    refs: list[Any] = []
    for value in values:
        if isinstance(value, list):
            refs.extend(value)
        elif value:
            refs.append(value)
    return unique_strings(refs)
