#!/usr/bin/env python3
"""Shared stdlib-only helpers for result-diagnosis."""
from __future__ import annotations

import hashlib
import json
import math
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
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resolve_workspace(value: str | None) -> Path:
    if value:
        ws = Path(value).expanduser().resolve()
        if not (ws / "project.yaml").exists() or not (ws / "external_executor").is_dir():
            raise FileNotFoundError(f"Invalid workspace: {ws}")
        return ws
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "project.yaml").exists() and (candidate / "external_executor").is_dir():
            return candidate
    raise FileNotFoundError("Could not find workspace containing project.yaml and external_executor/")


def resolve_in_workspace(workspace: Path, value: str) -> Path:
    text = value.replace("<workspace>", str(workspace)).strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = workspace / path
    resolved = path.resolve(strict=False)
    resolved.relative_to(workspace.resolve())
    return resolved


def relpath(workspace: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(workspace.resolve()).as_posix()


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
        if any(ch in line for ch in "*?["):
            line = re.split(r"[*?[ ]", line, maxsplit=1)[0].rstrip("/") or "."
        if line:
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
        raise PermissionError(f"Denied path: {resolved}")
    if not allowed or not any(is_within(resolved, a) for a in allowed):
        raise PermissionError(f"Path outside allowed roots: {resolved}")


def canonical_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def slugify(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").lower()
    return text[:64] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    head = slugify(parts[0] if parts else prefix)
    return f"{prefix}-{head}-{hashlib.sha256(raw.encode()).hexdigest()[:10]}"


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def section_items(section: Any) -> list[dict[str, Any]]:
    if isinstance(section, list):
        return [x for x in section if isinstance(x, dict)]
    if isinstance(section, dict):
        for key in ("items", "runs", "records", "experiments"):
            if isinstance(section.get(key), list):
                return [x for x in section[key] if isinstance(x, dict)]
    return []


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


def schema_major(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:^|[._-])v?(\d+)(?:$|[._-])", value) or re.search(r"v(\d+)$", value)
    return int(match.group(1)) if match else None


def active_iteration_id(result: dict[str, Any]) -> str | None:
    explicit = get_nested(result, "active_iteration.iteration_id", "iteration_plan.iteration_id", default=None)
    if explicit:
        return str(explicit)
    plans = section_items(result.get("iteration_plans"))
    active = [p for p in plans if p.get("status") in {"active", "running", "planned"}]
    if active:
        return str(active[-1].get("iteration_id") or active[-1].get("id"))
    runs = section_items(result.get("experiment_runs"))
    ids = [str(r.get("iteration_id")) for r in runs if r.get("iteration_id")]
    return ids[-1] if ids else None


def metric_direction(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    aliases = {
        "maximize": "higher_is_better", "max": "higher_is_better", "higher": "higher_is_better",
        "higher_is_better": "higher_is_better", "greater_is_better": "higher_is_better",
        "minimize": "lower_is_better", "min": "lower_is_better", "lower": "lower_is_better",
        "lower_is_better": "lower_is_better", "smaller_is_better": "lower_is_better",
    }
    return aliases.get(text)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def evidence_ref(path: str, evidence_id: str, kind: str) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "path": path, "kind": kind}


def collect_known_ids(snapshot: dict[str, Any], statistics: dict[str, Any], result_pack: dict[str, Any] | None = None) -> set[str]:
    known: set[str] = set()
    for item in snapshot.get("runs", []):
        for key in ("evidence_id", "run_id", "experiment_id"):
            if item.get(key): known.add(str(item[key]))
        known.update(str(x) for x in item.get("claim_ids", []))
    for section in ("metric_observations", "metric_summaries", "method_comparisons", "strongest_baselines", "anomalies"):
        for item in statistics.get(section, {}).get("items", []):
            for key in ("observation_id", "aggregate_id", "comparison_id", "strongest_baseline_id", "anomaly_id"):
                if item.get(key): known.add(str(item[key]))
    if result_pack:
        for item in section_items(result_pack.get("claim_evidence_matrix")):
            if item.get("claim_id"): known.add(str(item["claim_id"]))
            if item.get("experiment_id"): known.add(str(item["experiment_id"]))
    return known


def artifact_ref(workspace: Path, path: Path, producer: str = "result-diagnosis") -> dict[str, Any]:
    stat = path.stat()
    return {
        "artifact_id": stable_id("ART", relpath(workspace, path), sha256_file(path)),
        "path": relpath(workspace, path),
        "sha256": sha256_file(path),
        "size_bytes": stat.st_size,
        "producer": producer,
        "created_at": utc_now(),
        "evidence_level": "diagnostic_hint",
    }
