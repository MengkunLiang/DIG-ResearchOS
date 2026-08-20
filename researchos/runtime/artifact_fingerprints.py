"""Small helpers for binding generated artifacts to their input files."""

from __future__ import annotations


import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


T45_INPUT_FINGERPRINT_PATHS = {
    "project": "project.yaml",
    "hypothesis_brief": "ideation/hypothesis_brief.yaml",
    "selected_candidate": "ideation/selected/selected_candidate.json",
    "t45_search_targets": "ideation/selected/t45_search_targets.json",
    "idea_scorecard": "ideation/idea_scorecard.yaml",
    "idea_rationales": "ideation/idea_rationales.json",
    "gate_decisions": "ideation/gate_decisions.json",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "comparison_table": "literature/comparison_table.csv",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "agent_params_config": "config/system_config/agent_params.yaml",
}

# Before v2 the novelty audit fingerprinted the entire mutable literature
# library.  A later, unrelated supplement therefore made a completed audit
# stale even when the audit had never retrieved or used that paper.  Keep the
# old map only to validate existing receipts; new receipts bind the stable T4
# decision context plus the exact evidence files named by the audit.
T45_LEGACY_INPUT_FINGERPRINT_PATHS = {
    **T45_INPUT_FINGERPRINT_PATHS,
    "literature_manifest": "literature/literature_manifest.json",
    "cross_domain_catalogs": "literature/cross_domain_catalogs",
}

T45_FINGERPRINT_REPORT_REL_PATH = "ideation/novelty_audit_fingerprints.json"
T45_FINGERPRINT_SEMANTICS = "t45_novelty_audit_input_fingerprints"
T45_FINGERPRINT_VERSION = "2.0"
_LITERATURE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(literature/(?:deep_read_notes|bridge_notes|shallow_read_notes|"
    r"cross_domain_catalogs|evidence_queries|targeted_supplements)/[^\s`)'\"<>]+)"
)


def _is_operational_directory_guide(path: Path) -> bool:
    """Return whether a file is runtime scaffolding rather than research input.

    Workspaces are initialized lazily by several public entry points.  That
    initialization writes ``_DIR_GUIDE.md`` into standard artifact directories.
    Treating those generated guides as contents of a scientific-input directory
    made an unchanged T4 confirmation stale merely because a user invoked
    ``run-task`` after creating a workspace by hand.  The guide is explicitly
    runtime-owned documentation, so exclude only this exact filename; all
    actual note/resource files remain fingerprinted byte-for-byte.
    """

    return path.name == "_DIR_GUIDE.md"


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
        children = [
            child
            for child in path.rglob("*")
            if child.is_file() and not _is_operational_directory_guide(child)
        ]
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
    if "literature/literature_manifest.json" in paths.values():
        # Consumers that declare the shared Literature Artifact Contract should
        # fingerprint the actual manifest, not a stale or missing copy.  The
        # manifest writer preserves bytes when note/catalog inputs are
        # semantically unchanged, so this refresh is safe for resume checks.
        from .literature_contract import build_literature_manifest

        build_literature_manifest(workspace_dir, write=True)
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
    evidence_paths = _t45_explicit_evidence_dependencies(workspace_dir)
    return write_fingerprint_report(
        workspace_dir,
        output_rel_path=T45_FINGERPRINT_REPORT_REL_PATH,
        semantics=T45_FINGERPRINT_SEMANTICS,
        input_paths=T45_INPUT_FINGERPRINT_PATHS,
        extra={
            "version": T45_FINGERPRINT_VERSION,
            "dependency_policy": "stable_inputs_plus_explicitly_used_evidence",
            "evidence_dependencies": {
                path: file_fingerprint(workspace_dir, path) for path in evidence_paths
            },
        },
    )


def validate_t45_fingerprint_report(workspace_dir: Path) -> tuple[bool, str | None]:
    report_path = workspace_dir / T45_FINGERPRINT_REPORT_REL_PATH
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"{T45_FINGERPRINT_REPORT_REL_PATH} 解析失败: {exc}"
    if not isinstance(report, dict) or report.get("semantics") != T45_FINGERPRINT_SEMANTICS:
        return False, f"{T45_FINGERPRINT_REPORT_REL_PATH} semantics 不正确"

    input_paths = (
        T45_INPUT_FINGERPRINT_PATHS
        if str(report.get("version") or "").startswith("2")
        else T45_LEGACY_INPUT_FINGERPRINT_PATHS
    )
    ok, error = validate_input_fingerprints(
        workspace_dir,
        report.get("input_fingerprints"),
        input_paths,
        label_for_error="T4.5 novelty audit",
    )
    if not ok:
        return ok, error
    if not str(report.get("version") or "").startswith("2"):
        return True, None

    dependencies = report.get("evidence_dependencies")
    if not isinstance(dependencies, dict):
        return False, "T4.5 novelty audit 缺少 evidence_dependencies"
    for rel_path, previous in dependencies.items():
        if not isinstance(rel_path, str) or not isinstance(previous, dict):
            return False, "T4.5 novelty audit evidence_dependencies 格式不正确"
        current = file_fingerprint(workspace_dir, rel_path)
        if bool(previous.get("exists")) != bool(current.get("exists")):
            return False, f"T4.5 novelty audit 使用的证据已变化: {rel_path}"
        if current.get("exists") and str(previous.get("sha256") or "") != str(current.get("sha256") or ""):
            return False, f"T4.5 novelty audit 使用的证据已变化: {rel_path}"
    return True, None


def _t45_explicit_evidence_dependencies(workspace_dir: Path) -> list[str]:
    """Return evidence the audit explicitly names, plus its own tool receipts.

    The report itself is the authority for actual use.  T4.5 query/supplement
    receipts remain dependencies because they define the search scope and
    stopping boundary, but receipts produced later by Formalizer or Writer do
    not retroactively invalidate the novelty decision.
    """

    workspace = Path(workspace_dir)
    audit_path = workspace / "ideation" / "novelty_audit.md"
    try:
        audit_text = audit_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        audit_text = ""
    paths = {
        match.group(1).rstrip(".,;:，。；：")
        for match in _LITERATURE_PATH_RE.finditer(audit_text)
    }

    for root_rel in ("literature/evidence_queries", "literature/targeted_supplements"):
        root = workspace / root_rel
        if not root.is_dir():
            continue
        pattern = "*.json" if root_rel.endswith("evidence_queries") else "supplement.json"
        for path in root.rglob(pattern):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or str(payload.get("caller_task_id") or "") != "T4.5":
                continue
            paths.add(path.relative_to(workspace).as_posix())

    # Never allow prose to smuggle an absolute or escaping dependency into a
    # recovery receipt.  The regex already restricts the roots; resolve once
    # more so validation remains safe if the pattern is changed later.
    safe: list[str] = []
    workspace_resolved = workspace.resolve()
    for rel_path in sorted(paths):
        candidate = (workspace / rel_path).resolve()
        if candidate == workspace_resolved or workspace_resolved not in candidate.parents:
            continue
        safe.append(rel_path)
    return safe
