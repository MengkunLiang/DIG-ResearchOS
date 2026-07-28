from __future__ import annotations

"""Deterministic repository audit for public and executor Skill contracts.

The public Skill loader validates frontmatter only for the top-level ``skills/``
catalog.  External-executor templates intentionally live in a nested protected
directory and contain their own scripts and references.  This module gives
release checks one read-only entry point for both collections without trying to
run an LLM, acquire a resource, or mutate a workspace.
"""

from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .loader import load_skill


PUBLIC_SKILLS_RELATIVE = Path("skills")
EXECUTOR_SKILLS_RELATIVE = PUBLIC_SKILLS_RELATIVE / "external_executor_skills"
_LOCAL_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:references|scripts|agents)/[A-Za-z0-9_./-]+"
)
_MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _record_issue(record: dict[str, Any], code: str, message: str) -> None:
    record.setdefault("errors", []).append({"code": code, "message": message})


def _local_resource_references(text: str) -> list[str]:
    return sorted(
        {
            value.rstrip(".,:;)`]}>")
            for value in _LOCAL_RESOURCE_RE.findall(text)
        }
    )


def _markdown_relative_links(text: str) -> list[str]:
    links: set[str] = set()
    for raw_target in _MARKDOWN_LINK_RE.findall(text):
        target = raw_target.strip().strip("<>").split("#", 1)[0].strip()
        if not target or target.startswith(("#", "/", "mailto:")) or "://" in target:
            continue
        links.add(target)
    return sorted(links)


def _script_paths(skill_dir: Path) -> list[Path]:
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "_common.py")


def _audit_skill(
    *,
    skill_dir: Path,
    kind: str,
    check_script_help: bool,
) -> dict[str, Any]:
    """Audit one package without running its research workflow."""

    skill_md = skill_dir / "SKILL.md"
    record: dict[str, Any] = {
        "name": skill_dir.name,
        "kind": kind,
        "path": skill_dir.as_posix(),
        "errors": [],
        "script_count": 0,
        "script_help_checked": 0,
    }
    if not skill_md.is_file():
        _record_issue(record, "missing_skill_md", "SKILL.md is missing")
        return record

    try:
        skill = load_skill(skill_dir)
        record.update(
            {
                "name": skill.name,
                "execution_scope": skill.execution_scope,
                "execution_owner": skill.execution_owner or None,
                "capability_profiles": list(skill.capability_profiles),
            }
        )
    except Exception as exc:  # noqa: BLE001 - an audit must collect every bad package
        _record_issue(record, "invalid_skill_contract", str(exc))
        return record

    text = skill_md.read_text(encoding="utf-8", errors="replace")
    for relative in _local_resource_references(text):
        if not (skill_dir / relative).is_file():
            _record_issue(record, "missing_local_resource", relative)
    for relative in _markdown_relative_links(text):
        if not (skill_dir / relative).exists():
            _record_issue(record, "broken_markdown_link", relative)

    scripts = _script_paths(skill_dir)
    record["script_count"] = len(scripts)
    for script in scripts:
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except (OSError, SyntaxError, UnicodeError) as exc:
            _record_issue(record, "invalid_python_script", f"{script.name}: {exc}")
            continue
        if not check_script_help:
            continue
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        record["script_help_checked"] += 1
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")[:500]
            _record_issue(record, "script_help_failed", f"{script.name}: exit {completed.returncode}; {detail}")

    record["status"] = "pass" if not record["errors"] else "fail"
    return record


def audit_skill_suite(repo_root: Path, *, check_script_help: bool = False) -> dict[str, Any]:
    """Audit all repository-owned public and external-executor Skill packages.

    ``repo_root`` must be the directory containing ``skills/``.  The return
    value is JSON serializable so callers can print it in CI or add richer UI
    around the same deterministic facts.
    """

    root = Path(repo_root).resolve()
    public_root = root / PUBLIC_SKILLS_RELATIVE
    executor_root = root / EXECUTOR_SKILLS_RELATIVE
    records: list[dict[str, Any]] = []
    suite_errors: list[dict[str, str]] = []
    if not public_root.is_dir():
        suite_errors.append({"code": "missing_public_skills_root", "message": str(public_root)})
    else:
        for skill_md in sorted(public_root.glob("*/SKILL.md")):
            records.append(
                _audit_skill(
                    skill_dir=skill_md.parent,
                    kind="public",
                    check_script_help=check_script_help,
                )
            )
    if not executor_root.is_dir():
        suite_errors.append({"code": "missing_executor_skills_root", "message": str(executor_root)})
    else:
        for skill_md in sorted(executor_root.glob("*/SKILL.md")):
            records.append(
                _audit_skill(
                    skill_dir=skill_md.parent,
                    kind="external_executor",
                    check_script_help=check_script_help,
                )
            )

    seen: dict[str, str] = {}
    for record in records:
        name = str(record["name"])
        previous = seen.get(name)
        if previous is not None:
            _record_issue(record, "duplicate_skill_name", f"also declared by {previous}")
        else:
            seen[name] = str(record["path"])
        record["status"] = "pass" if not record["errors"] else "fail"

    by_kind = Counter(str(record["kind"]) for record in records)
    failed = [record for record in records if record["status"] != "pass"]
    return {
        "schema_version": "researchos_skill_suite_audit.v1",
        "repository_root": str(root),
        "status": "pass" if not suite_errors and not failed else "fail",
        "summary": {
            "total_skills": len(records),
            "public_skills": by_kind["public"],
            "external_executor_skills": by_kind["external_executor"],
            "failed_skills": len(failed),
            "script_help_checked": sum(int(record["script_help_checked"]) for record in records),
        },
        "errors": suite_errors,
        "skills": records,
    }


def render_skill_suite_audit(report: dict[str, Any]) -> str:
    """Render the compact human-readable form used by the CLI."""

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "Skill Suite Audit",
        f"Status: {report.get('status')}",
        (
            "Skills: "
            f"{summary.get('total_skills', 0)} total "
            f"({summary.get('public_skills', 0)} public, "
            f"{summary.get('external_executor_skills', 0)} external executor)"
        ),
        f"External script help checks: {summary.get('script_help_checked', 0)}",
    ]
    for issue in report.get("errors", []):
        lines.append(f"- {issue.get('code')}: {issue.get('message')}")
    for record in report.get("skills", []):
        if record.get("status") == "pass":
            continue
        for issue in record.get("errors", []):
            lines.append(f"- {record.get('name')}: {issue.get('code')}: {issue.get('message')}")
    return "\n".join(lines)
