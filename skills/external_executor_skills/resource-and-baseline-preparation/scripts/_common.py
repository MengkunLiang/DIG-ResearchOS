#!/usr/bin/env python3
"""Shared, stdlib-only helpers for the resource preparation skill."""
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
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in {"rw", "write", "allow", "ro", "read", "no", "deny", "forbid"}:
            mode, line = parts[0].lower(), parts[1].strip()
            target = allowed if mode in {"rw", "write", "allow"} else denied if mode in {"no", "deny", "forbid"} else None
        else:
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
        if target is None:
            continue
        if not line:
            continue
        if any(ch in line for ch in "*?["):
            # Prefix before the first glob token is the conservative writable root.
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


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def tree_manifest(root: Path, max_files: int = 20000, max_hash_bytes: int = 128 * 1024 * 1024) -> dict[str, Any]:
    root = root.resolve(strict=False)
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False
    if root.is_file():
        stat = root.stat()
        entry = {"path": root.name, "type": "file", "size_bytes": stat.st_size}
        if stat.st_size <= max_hash_bytes:
            entry["sha256"] = sha256_file(root)
        else:
            entry["sha256"] = None
            entry["hash_skipped_reason"] = "size_limit"
        entries.append(entry)
        total_bytes = stat.st_size
    elif root.is_dir():
        count = 0
        for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
            if count >= max_files:
                truncated = True
                break
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "type": "symlink", "target": os.readlink(path)})
                count += 1
                continue
            if path.is_dir():
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                entries.append({"path": rel, "type": "unreadable", "error": str(exc)})
                count += 1
                continue
            total_bytes += stat.st_size
            entry = {"path": rel, "type": "file", "size_bytes": stat.st_size, "executable": bool(stat.st_mode & 0o111)}
            if stat.st_size <= max_hash_bytes:
                try:
                    entry["sha256"] = sha256_file(path)
                except OSError as exc:
                    entry["sha256"] = None
                    entry["hash_error"] = str(exc)
            else:
                entry["sha256"] = None
                entry["hash_skipped_reason"] = "size_limit"
            entries.append(entry)
            count += 1
    manifest_hash = canonical_json_hash(entries)
    return {
        "root": str(root),
        "entry_count": len(entries),
        "total_bytes": total_bytes,
        "truncated": truncated,
        "manifest_sha256": manifest_hash,
        "entries": entries,
    }


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


def slugify(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:64] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    text = "|".join(str(p) for p in parts)
    return f"{prefix}-{slugify(str(parts[0]) if parts else prefix)}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def redact_url(url: str) -> str:
    # Remove embedded credentials while preserving the public repository identity.
    return re.sub(r"(https?://)[^/@]+@", r"\1", url)


def ensure_known_ids(values: Iterable[str], known: set[str], label: str) -> list[str]:
    return [f"Unknown {label}: {value}" for value in values if value not in known]
