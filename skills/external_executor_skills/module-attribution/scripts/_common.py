#!/usr/bin/env python3
"""Shared stdlib-only helpers for module-attribution."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            json.dump(data, fh, indent=2, ensure_ascii=False, allow_nan=False)
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
        for key in ("items", "runs", "records", "experiments", "modules"):
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
    diagnoses = section_items(result.get("result_diagnoses"))
    if diagnoses:
        return str(diagnoses[-1].get("iteration_id"))
    runs = section_items(result.get("experiment_runs"))
    ids = [str(r.get("iteration_id")) for r in runs if r.get("iteration_id")]
    return ids[-1] if ids else None


def current_diagnosis(result: dict[str, Any], iteration_id: str) -> dict[str, Any] | None:
    section = result.get("result_diagnoses")
    items = section_items(section)
    if isinstance(section, dict):
        current_id = get_nested(section, f"current_by_iteration.{iteration_id}", default=None)
        if current_id:
            for item in items:
                if str(item.get("diagnosis_id")) == str(current_id):
                    return item
    matches = [x for x in items if str(x.get("iteration_id")) == str(iteration_id)]
    return matches[-1] if matches else None


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


def normalize_state_map(value: Any) -> dict[str, bool]:
    out: dict[str, bool] = {}
    if isinstance(value, dict):
        for key, state in value.items():
            if isinstance(state, bool):
                out[str(key)] = state
            elif str(state).lower() in {"on", "enabled", "present", "true", "1"}:
                out[str(key)] = True
            elif str(state).lower() in {"off", "disabled", "removed", "false", "0"}:
                out[str(key)] = False
    return out


def artifact_ref(workspace: Path, path: Path, evidence_level: str = "diagnostic_hint") -> dict[str, Any]:
    stat = path.stat()
    digest = sha256_file(path)
    return {
        "artifact_id": stable_id("ART", relpath(workspace, path), digest),
        "path": relpath(workspace, path),
        "sha256": digest,
        "size_bytes": stat.st_size,
        "producer": "module-attribution",
        "created_at": utc_now(),
        "evidence_level": evidence_level,
    }


def collect_known_ids(snapshot: dict[str, Any], facts: dict[str, Any], result_pack: dict[str, Any] | None = None) -> set[str]:
    known: set[str] = set()
    for section in ("modules", "mechanisms", "runs", "intervention_observations"):
        for item in snapshot.get(section, []):
            for key in ("module_id", "mechanism_id", "run_id", "evidence_id", "intervention_id", "experiment_id", "diagnosis_id"):
                if item.get(key):
                    known.add(str(item[key]))
    for section in ("module_registry", "mechanism_registry", "intervention_effects", "interaction_effects", "confounds", "module_facts", "mechanism_facts"):
        sec = facts.get(section, {})
        for item in sec.get("items", []) if isinstance(sec, dict) else []:
            for key in ("module_id", "mechanism_id", "effect_id", "interaction_id", "confound_id", "module_fact_id", "mechanism_fact_id"):
                if item.get(key):
                    known.add(str(item[key]))
            known.update(str(x) for x in item.get("evidence_refs", []))
    if result_pack:
        diag = current_diagnosis(result_pack, active_iteration_id(result_pack) or "")
        if diag and diag.get("diagnosis_id"):
            known.add(str(diag["diagnosis_id"]))
        for item in section_items(result_pack.get("claim_evidence_matrix")):
            for key in ("claim_id", "experiment_id"):
                if item.get(key):
                    known.add(str(item[key]))
    return known
