#!/usr/bin/env python3
"""Shared stdlib-only helpers for the evidence-packaging skill."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


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
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def dump_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if text and not text.endswith("\n"):
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
    raise FileNotFoundError("Could not locate workspace containing project.yaml and external_executor/")


def resolve_workspace(value: str | None) -> Path:
    if value:
        path = Path(value).expanduser().resolve()
        if not (path / "project.yaml").exists() or not (path / "external_executor").is_dir():
            raise FileNotFoundError(f"Invalid workspace: {path}")
        return path
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
        glob_positions = [line.find(ch) for ch in "*?[" if line.find(ch) >= 0]
        if glob_positions:
            line = line[: min(glob_positions)].rstrip("/") or "."
        target.append(resolve_in_workspace(workspace, line))
    return allowed, denied


def assert_write_allowed(workspace: Path, path: Path) -> None:
    allowed, denied = parse_allowed_paths(workspace)
    resolved = path.resolve(strict=False)
    if any(is_within(resolved, root) for root in denied):
        raise PermissionError(f"Path is denied: {resolved}")
    if not allowed or not any(is_within(resolved, root) for root in allowed):
        raise PermissionError(f"Path is outside allowed write roots: {resolved}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_hash(data: Any) -> str:
    return sha256_bytes(canonical_json(data).encode("utf-8"))


def file_ref(workspace: Path, path: Path, *, producer: str = "evidence-packaging", evidence_level: str = "provenance") -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    ref: dict[str, Any] = {
        "artifact_id": stable_id("ART", relpath(workspace, resolved)),
        "path": relpath(workspace, resolved),
        "producer": producer,
        "created_at": utc_now(),
        "evidence_level": evidence_level,
    }
    if resolved.exists() and resolved.is_file():
        ref["sha256"] = sha256_file(resolved)
        ref["size_bytes"] = resolved.stat().st_size
    else:
        ref["sha256"] = None
        ref["size_bytes"] = None
    return ref


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
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def slugify(value: str, fallback: str = "item") -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return out[:72] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    first = str(parts[0]) if parts else prefix
    return f"{prefix}-{slugify(first)}-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


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


def walk_dicts(value: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from walk_dicts(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from walk_dicts(child, child_path)


def extract_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, str):
        if "/" in value or value.endswith((".json", ".yaml", ".yml", ".csv", ".tsv", ".log", ".txt", ".py", ".svg", ".pdf", ".png")):
            refs.append(value)
    elif isinstance(value, dict):
        for key in ("path", "artifact_ref", "artifact_path", "code_ref", "config_ref", "log_ref", "metric_output_ref", "source_ref"):
            if key in value:
                refs.extend(extract_refs(value[key]))
        for key in ("evidence_refs", "artifact_refs", "code_refs", "config_refs", "log_refs", "source_refs", "rendered_files", "plot_script_refs", "source_result_refs", "metric_output_refs"):
            if key in value:
                refs.extend(extract_refs(value[key]))
    elif isinstance(value, list):
        for item in value:
            refs.extend(extract_refs(item))
    return unique_strings(refs)


def record_status(record: dict[str, Any]) -> str:
    return str(record.get("status") or record.get("run_status") or record.get("verdict") or "unknown").lower()


def record_id(record: dict[str, Any], fallback_path: str = "record") -> str:
    for key in ("run_id", "record_id", "experiment_id", "candidate_id", "review_id", "diagnosis_id", "attribution_id", "decision_id", "module_id", "artifact_id", "id"):
        if nonempty(record.get(key)):
            return str(record[key])
    return stable_id("REC", fallback_path, canonical_json_hash(record))


def path_exists(workspace: Path, value: str) -> bool:
    try:
        path = resolve_in_workspace(workspace, value)
    except Exception:
        return False
    return path.exists()


def xml_escape(text: Any) -> str:
    value = str(text)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
