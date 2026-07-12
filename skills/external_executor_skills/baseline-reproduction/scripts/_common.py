#!/usr/bin/env python3
"""Shared stdlib helpers for baseline-reproduction."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SECRET_NAME_RE = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH|PRIVATE", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def find_workspace(start: Path) -> Path:
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "project.yaml").exists() and (candidate / "external_executor").is_dir():
            return candidate
    raise FileNotFoundError("No workspace containing project.yaml and external_executor/")


def resolve_workspace(value: str | None) -> Path:
    if value:
        workspace = Path(value).expanduser().resolve()
        if not (workspace / "project.yaml").exists() or not (workspace / "external_executor").is_dir():
            raise FileNotFoundError(f"Invalid workspace: {workspace}")
        return workspace
    return find_workspace(Path.cwd())


def resolve_in_workspace(workspace: Path, value: str) -> Path:
    value = value.replace("<workspace>", str(workspace)).strip()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve(strict=False)


def relpath(workspace: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(workspace.resolve()).as_posix()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def parse_allowed_paths(workspace: Path) -> tuple[list[Path], list[Path]]:
    policy = workspace / "external_executor" / "allowed_paths.txt"
    if not policy.exists():
        raise FileNotFoundError(policy)
    allowed: list[Path] = []
    denied: list[Path] = []
    for raw in policy.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
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
            line = re.split(r"[\*\?\[]", line, maxsplit=1)[0].rstrip("/") or "."
        target.append(resolve_in_workspace(workspace, line))
    return allowed, denied


def assert_write_allowed(workspace: Path, path: Path) -> None:
    allowed, denied = parse_allowed_paths(workspace)
    path = path.resolve(strict=False)
    if any(is_within(path, d) for d in denied):
        raise PermissionError(f"Explicitly denied path: {path}")
    if not allowed or not any(is_within(path, a) for a in allowed):
        raise PermissionError(f"Path outside allowed write roots: {path}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def slugify(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:72] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    text = "|".join(str(p) for p in parts)
    name = slugify(str(parts[0]) if parts else prefix)
    return f"{prefix}-{name}-{hashlib.sha256(text.encode()).hexdigest()[:10]}"


def get_nested(data: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        cur = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def tree_manifest(root: Path, max_files: int = 30000, max_hash_bytes: int = 256 * 1024 * 1024) -> dict[str, Any]:
    root = root.resolve(strict=False)
    entries = []
    total = 0
    truncated = False
    if root.is_file():
        st = root.stat()
        entries.append({"path": root.name, "type": "file", "size_bytes": st.st_size, "sha256": sha256_file(root) if st.st_size <= max_hash_bytes else None})
        total = st.st_size
    elif root.is_dir():
        for idx, path in enumerate(sorted(root.rglob("*"), key=lambda p: p.as_posix())):
            if idx >= max_files:
                truncated = True
                break
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "type": "symlink", "target": os.readlink(path)})
                continue
            if not path.is_file():
                continue
            try:
                st = path.stat()
                total += st.st_size
                entries.append({"path": rel, "type": "file", "size_bytes": st.st_size, "sha256": sha256_file(path) if st.st_size <= max_hash_bytes else None})
            except OSError as exc:
                entries.append({"path": rel, "type": "unreadable", "error": str(exc)})
    return {"root": str(root), "entry_count": len(entries), "total_bytes": total, "truncated": truncated, "manifest_sha256": canonical_hash(entries), "entries": entries}


def safe_env(base: dict[str, str], allowed_names: Iterable[str], overrides: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    safe_base_names = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS"}
    requested = set(allowed_names) | safe_base_names
    out: dict[str, str] = {}
    redacted = []
    for name in requested:
        if SECRET_NAME_RE.search(name):
            redacted.append(name)
            continue
        if name in base:
            out[name] = str(base[name])
    for name, value in (overrides or {}).items():
        if SECRET_NAME_RE.search(str(name)):
            redacted.append(str(name))
            continue
        out[str(name)] = str(value)
    out.setdefault("PYTHONUNBUFFERED", "1")
    return out, sorted(set(redacted))


def read_simple_selector(data: Any, selector: str | None) -> Any:
    if not selector:
        return data
    cur = data
    for token in selector.split("."):
        if isinstance(cur, dict):
            cur = cur[token]
        elif isinstance(cur, list) and token.isdigit():
            cur = cur[int(token)]
        else:
            raise KeyError(selector)
    return cur


def finite_number(value: Any) -> bool:
    try:
        number = float(value)
        return number == number and number not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False
