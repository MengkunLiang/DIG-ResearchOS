from __future__ import annotations

"""Small helpers for binding generated artifacts to their input files."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


T45_INPUT_FINGERPRINT_PATHS = {
    "project": "project.yaml",
    "hypotheses": "ideation/hypotheses.md",
    "idea_scorecard": "ideation/idea_scorecard.yaml",
    "idea_rationales": "ideation/idea_rationales.json",
    "gate_decisions": "ideation/gate_decisions.json",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "comparison_table": "literature/comparison_table.csv",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "agent_params_config": "config/system_config/agent_params.yaml",
    "model_settings_config": "config/model_settings.yaml",
}

T45_FINGERPRINT_REPORT_REL_PATH = "ideation/novelty_audit_fingerprints.json"
T45_FINGERPRINT_SEMANTICS = "t45_novelty_audit_input_fingerprints"


def file_fingerprint(workspace_dir: Path, rel_path: str) -> dict[str, Any]:
    path = _resolve_fingerprint_path(workspace_dir, rel_path)
    item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
    if path.exists() and path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        item["sha256"] = digest.hexdigest()
        item["size"] = path.stat().st_size
    elif path.exists() and path.is_dir():
        children = [child for child in path.rglob("*") if child.is_file()]
        item["kind"] = "dir"
        item["file_count"] = len(children)
        digest = hashlib.sha256()
        for child in sorted(children, key=lambda p: p.relative_to(path).as_posix()):
            rel = child.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(str(child.stat().st_size).encode("ascii"))
                digest.update(b"\0")
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        item["sha256"] = digest.hexdigest()
    return item


def _resolve_fingerprint_path(workspace_dir: Path, rel_path: str) -> Path:
    workspace_path = workspace_dir / rel_path
    if workspace_path.exists() or not rel_path.startswith("config/"):
        return workspace_path
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / rel_path


def build_input_fingerprints(workspace_dir: Path, paths: dict[str, str]) -> dict[str, dict[str, Any]]:
    workspace_dir = workspace_dir.resolve()
    return {label: file_fingerprint(workspace_dir, rel_path) for label, rel_path in paths.items()}


def validate_input_fingerprints(
    workspace_dir: Path,
    fingerprints: object,
    paths: dict[str, str],
    *,
    label_for_error: str,
) -> tuple[bool, str | None]:
    if not isinstance(fingerprints, dict):
        return False, f"{label_for_error} 缺少 input_fingerprints，必须刷新"
    current = build_input_fingerprints(workspace_dir, paths)
    stale: list[str] = []
    for label, item in current.items():
        previous = fingerprints.get(label)
        if not isinstance(previous, dict):
            stale.append(label)
            continue
        if bool(previous.get("exists")) != bool(item.get("exists")):
            stale.append(label)
            continue
        if item.get("exists") and str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            stale.append(label)
    if stale:
        return False, f"{label_for_error} 对应输入已变化，必须刷新: " + ", ".join(stale)
    return True, None


def write_fingerprint_report(
    workspace_dir: Path,
    *,
    output_rel_path: str,
    semantics: str,
    input_paths: dict[str, str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": semantics,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprints": build_input_fingerprints(workspace_dir, input_paths),
    }
    if extra:
        payload.update(extra)
    output_path = workspace_dir / output_rel_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def validate_fingerprint_report(
    workspace_dir: Path,
    *,
    report_rel_path: str,
    expected_semantics: str,
    input_paths: dict[str, str],
    label_for_error: str,
) -> tuple[bool, str | None]:
    report_path = workspace_dir / report_rel_path
    if not report_path.exists() or report_path.stat().st_size <= 0:
        return False, f"缺少 {report_rel_path}，必须刷新 {label_for_error}"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"{report_rel_path} 解析失败: {exc}"
    if not isinstance(report, dict):
        return False, f"{report_rel_path} 顶层必须是对象"
    if report.get("semantics") != expected_semantics:
        return False, f"{report_rel_path} semantics 不正确"
    return validate_input_fingerprints(
        workspace_dir,
        report.get("input_fingerprints"),
        input_paths,
        label_for_error=label_for_error,
    )


def write_t45_fingerprint_report(workspace_dir: Path) -> dict[str, Any]:
    return write_fingerprint_report(
        workspace_dir,
        output_rel_path=T45_FINGERPRINT_REPORT_REL_PATH,
        semantics=T45_FINGERPRINT_SEMANTICS,
        input_paths=T45_INPUT_FINGERPRINT_PATHS,
    )


def validate_t45_fingerprint_report(workspace_dir: Path) -> tuple[bool, str | None]:
    return validate_fingerprint_report(
        workspace_dir,
        report_rel_path=T45_FINGERPRINT_REPORT_REL_PATH,
        expected_semantics=T45_FINGERPRINT_SEMANTICS,
        input_paths=T45_INPUT_FINGERPRINT_PATHS,
        label_for_error="T4.5 novelty audit",
    )
