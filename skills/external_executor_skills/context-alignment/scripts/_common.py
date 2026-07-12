#!/usr/bin/env python3
"""Shared helpers for context-alignment scripts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def workspace_root(raw: str | Path) -> Path:
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    return root


def resolve_in_workspace(root: Path, raw: str | Path, *, must_exist: bool = False) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {raw}") from exc
    return resolved


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and not candidate.is_symlink():
                yield candidate


def major_version(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    tail = value.rsplit(".", 1)[-1]
    if tail.startswith("v") and tail[1:].isdigit():
        return int(tail[1:])
    if value.startswith("v") and value[1:].isdigit():
        return int(value[1:])
    return None


def parse_allowed_entries(root: Path, path: Path) -> tuple[list[str], list[str]]:
    entries: list[str] = []
    errors: list[str] = []
    if not path.is_file():
        return entries, ["allowed paths file missing"]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        static = line
        for marker in ("*", "?", "["):
            static = static.split(marker, 1)[0]
        static = static.rstrip("/\\") or "."
        try:
            resolved = resolve_in_workspace(root, static)
            entries.append(relative_path(root, resolved))
        except ValueError as exc:
            errors.append(str(exc))
    return sorted(set(entries)), errors


def is_allowed_relative(relative: str, allowed_entries: Iterable[str]) -> bool:
    target = Path(relative).as_posix().rstrip("/") or "."
    for raw in allowed_entries:
        allowed = Path(raw).as_posix().rstrip("/") or "."
        if allowed == "." or target == allowed or target.startswith(allowed + "/"):
            return True
    return False


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
