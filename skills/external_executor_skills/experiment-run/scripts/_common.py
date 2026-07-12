#!/usr/bin/env python3
"""Shared deterministic helpers for experiment-run scripts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def workspace_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    return root


def resolve_in_workspace(root: Path, value: str, *, must_exist: bool = False) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {value}") from exc
    if must_exist and not candidate.exists():
        raise ValueError(f"path does not exist: {value}")
    return candidate


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def canonical_sha256(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_ref(root: Path, path: Path, *, evidence_level: str = "raw_result") -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {relative_path(root, path)}")
    stat = path.stat()
    return {
        "artifact_id": f"sha256:{sha256_file(path)}",
        "path": relative_path(root, path),
        "sha256": sha256_file(path),
        "size_bytes": stat.st_size,
        "producer": "experiment-run",
        "created_at": now_utc(),
        "evidence_level": evidence_level,
    }


def allowed_patterns(root: Path) -> list[str]:
    path = root / "external_executor" / "allowed_paths.txt"
    if not path.is_file():
        raise ValueError("missing external_executor/allowed_paths.txt")
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        mode = "rw"
        if len(parts) == 2 and parts[0].lower() in {"rw", "write", "allow", "ro", "read", "no", "deny", "forbid"}:
            mode, line = parts[0].lower(), parts[1].strip()
        elif line.lower().startswith(("deny:", "forbid:", "!", "-")):
            mode = "no"
        elif line.lower().startswith(("allow:", "write:", "+")):
            for prefix in ("allow:", "write:", "+"):
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
        if mode == "rw" and line:
            patterns.append(line.lstrip("./"))
    if not patterns:
        raise ValueError("allowed_paths.txt has no usable rules")
    return patterns


def _matches_allowed(relative: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return relative == prefix or relative.startswith(prefix + "/")
    if any(char in pattern for char in "*?["):
        return Path(relative).match(pattern)
    return relative == pattern or relative.startswith(pattern + "/")


def require_allowed(root: Path, path: Path) -> None:
    relative = relative_path(root, path)
    if not any(_matches_allowed(relative, pattern) for pattern in allowed_patterns(root)):
        raise ValueError(f"path is not authorized by allowed_paths.txt: {relative}")


def expand_regular_files(root: Path, values: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for value in values:
        path = resolve_in_workspace(root, value, must_exist=True)
        require_allowed(root, path)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in sorted(path.rglob("*")) if item.is_file())
        else:
            raise ValueError(f"unsupported output type: {value}")
    return files


def error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
