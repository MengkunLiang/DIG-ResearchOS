#!/usr/bin/env python3
"""Shared stdlib-only helpers for the ResearchOS implementation skill."""
from __future__ import annotations

import fnmatch
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
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def find_workspace(start: Path) -> Path:
    start = start.expanduser().resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "project.yaml").exists() and (candidate / "external_executor").is_dir():
            return candidate
    raise FileNotFoundError("Could not locate workspace containing project.yaml and external_executor/")


def resolve_workspace(value: str | None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
        if not (root / "project.yaml").exists() or not (root / "external_executor").is_dir():
            raise FileNotFoundError(f"Invalid workspace: {root}")
        return root
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
        target: list[Path] | None = allowed
        lower = line.lower()
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in {"rw", "write", "allow", "ro", "read", "no", "deny", "forbid"}:
            mode, line = parts[0].lower(), parts[1].strip()
            target = allowed if mode in {"rw", "write", "allow"} else denied if mode in {"no", "deny", "forbid"} else None
        else:
            for prefix in ("deny:", "forbid:", "!", "-"):
                if lower.startswith(prefix):
                    target = denied
                    line = line[len(prefix):].strip()
                    break
            else:
                for prefix in ("allow:", "write:", "+"):
                    if lower.startswith(prefix):
                        line = line[len(prefix):].strip()
                        break
        if target is None:
            continue
        if not line:
            continue
        if any(ch in line for ch in "*?["):
            line = re.split(r"[*?[]", line, maxsplit=1)[0].rstrip("/") or "."
        target.append(resolve_in_workspace(workspace, line))
    return allowed, denied


def assert_write_allowed(workspace: Path, path: Path) -> None:
    allowed, denied = parse_allowed_paths(workspace)
    resolved = path.resolve(strict=False)
    if any(is_within(resolved, root) for root in denied):
        raise PermissionError(f"Path explicitly denied: {resolved}")
    if not allowed or not any(is_within(resolved, root) for root in allowed):
        raise PermissionError(f"Path outside allowed write roots: {resolved}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    material = "|".join(str(part) for part in parts)
    slug_source = str(parts[0]) if parts else prefix
    slug = re.sub(r"[^A-Za-z0-9]+", "-", slug_source).strip("-").lower()[:48] or prefix.lower()
    return f"{prefix}-{slug}-{hashlib.sha256(material.encode()).hexdigest()[:10]}"


def get_nested(data: Any, *paths: str, default: Any = None) -> Any:
    for dotted in paths:
        current = data
        valid = True
        for part in dotted.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                valid = False
                break
        if valid:
            return current
    return default


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def schema_major(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:^|[._-])v?(\d+)(?:$|[._-])", value) or re.search(r"v(\d+)$", value)
    return int(match.group(1)) if match else None


def active_iteration(result_pack: dict[str, Any]) -> dict[str, Any] | None:
    direct = result_pack.get("active_iteration") or result_pack.get("iteration_plan")
    if isinstance(direct, dict) and direct.get("status") not in {"complete", "stale", "cancelled"}:
        return direct
    plans = result_pack.get("iteration_plans")
    items = plans.get("items", []) if isinstance(plans, dict) else plans if isinstance(plans, list) else []
    candidates = [item for item in items if isinstance(item, dict) and item.get("status") in {"active", "planned", "running", "approved"}]
    return candidates[-1] if candidates else None


def implementation_spec(result_pack: dict[str, Any], iteration: dict[str, Any] | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for key in ("implementation_spec", "method_implementation_spec", "implementation_specs"):
        value = result_pack.get(key)
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            candidates.extend(item for item in value["items"] if isinstance(item, dict))
        elif isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            candidates.append(value)
    if iteration:
        embedded = iteration.get("implementation_spec") or iteration.get("approved_implementation_delta")
        if isinstance(embedded, dict):
            candidates.append(embedded)
    if not candidates:
        return None
    iteration_id = (iteration or {}).get("iteration_id") or (iteration or {}).get("id")
    matching = [item for item in candidates if not iteration_id or item.get("iteration_id") in {None, iteration_id}]
    return (matching or candidates)[-1]


def tree_manifest(root: Path, *, max_files: int = 30000, max_hash_bytes: int = 128 * 1024 * 1024) -> dict[str, Any]:
    root = root.resolve(strict=False)
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False
    if root.is_file():
        paths = [root]
    elif root.is_dir():
        paths = sorted((path for path in root.rglob("*") if path.is_file() or path.is_symlink()), key=lambda p: p.as_posix())
    else:
        paths = []
    for index, path in enumerate(paths):
        if index >= max_files:
            truncated = True
            break
        name = path.name if root.is_file() else path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append({"path": name, "type": "symlink", "target": os.readlink(path)})
            continue
        stat = path.stat()
        total_bytes += stat.st_size
        item = {"path": name, "type": "file", "size_bytes": stat.st_size, "executable": bool(stat.st_mode & 0o111)}
        if stat.st_size <= max_hash_bytes:
            item["sha256"] = sha256_file(path)
        else:
            item["sha256"] = None
            item["hash_skipped_reason"] = "size_limit"
        entries.append(item)
    return {
        "root": str(root),
        "entry_count": len(entries),
        "total_bytes": total_bytes,
        "truncated": truncated,
        "manifest_sha256": canonical_json_hash(entries),
        "entries": entries,
    }


def match_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = path.replace(os.sep, "/").lstrip("./")
    return any(fnmatch.fnmatch(normalized, pattern.lstrip("./")) for pattern in patterns)


def is_text_file(path: Path, limit: int = 2 * 1024 * 1024) -> bool:
    if not path.is_file() or path.stat().st_size > limit:
        return False
    raw = path.read_bytes()[:4096]
    return b"\x00" not in raw


def safe_environment(allowed_keys: Iterable[str] = ()) -> dict[str, str]:
    base_allowed = {"PATH", "LANG", "LC_ALL", "TZ", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "CUDA_VISIBLE_DEVICES"}
    allowed = base_allowed | {str(key) for key in allowed_keys}
    return {key: value for key, value in os.environ.items() if key in allowed}


def reject_symlinks(root: Path) -> list[str]:
    findings: list[str] = []
    if root.is_symlink():
        findings.append(str(root))
    elif root.is_dir():
        for path in root.rglob("*"):
            if path.is_symlink():
                findings.append(str(path))
    return findings


def artifact_ref(workspace: Path, path: Path, *, level: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "artifact_id": stable_id("ART", relpath(workspace, path), sha256_file(path)),
        "path": relpath(workspace, path),
        "sha256": sha256_file(path),
        "size_bytes": stat.st_size,
        "producer": "implementation",
        "created_at": utc_now(),
        "evidence_level": level,
    }
