#!/usr/bin/env python3
"""Shared standard-library helpers for research-execution scripts."""

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


def workspace_root(raw: str | Path) -> Path:
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    return root


def resolve_in_workspace(root: Path, raw: str | Path, *, must_exist: bool = False) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {raw}") from exc
    return resolved


def relative_to_workspace(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            continue
        if candidate.is_file():
            yield candidate


def parse_allowed_roots(root: Path, path: Path) -> list[Path]:
    """Parse simple path entries; wildcard entries use their static prefix."""
    allowed: list[Path] = []
    if not path.exists():
        return allowed
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        mode = "rw"
        if len(parts) == 2 and parts[0].lower() in {"rw", "write", "allow", "ro", "read", "no", "deny", "forbid"}:
            mode, line = parts[0].lower(), parts[1].strip()
        elif line.lower().startswith(("deny:", "forbid:", "!", "-")):
            mode = "no"
            for prefix in ("deny:", "forbid:", "!", "-"):
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
        elif line.lower().startswith(("allow:", "write:", "+")):
            mode = "rw"
            for prefix in ("allow:", "write:", "+"):
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
        if mode != "rw":
            continue
        for marker in ("*", "?", "["):
            if marker in line:
                line = line.split(marker, 1)[0]
        line = line.rstrip("/\\") or "."
        try:
            allowed.append(resolve_in_workspace(root, line, must_exist=False))
        except ValueError:
            continue
    return sorted(set(allowed))


def is_under_any(path: Path, roots: Iterable[Path]) -> bool:
    resolved = path.resolve(strict=False)
    for allowed in roots:
        try:
            resolved.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def emit_report(report: dict[str, Any], output: str | None = None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def section_status(value: Any) -> str | None:
    if isinstance(value, dict):
        status = value.get("status")
        return status if isinstance(status, str) else None
    return None
