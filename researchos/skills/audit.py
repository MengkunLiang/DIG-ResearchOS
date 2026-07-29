"""Deterministic repository audit for public and executor Skill contracts.

The public Skill loader validates frontmatter only for the top-level ``skills/``
catalog.  External-executor templates intentionally live in a nested protected
directory and contain their own scripts and references.  This module gives
release checks one read-only entry point for both collections without trying to
run an LLM, acquire a resource, or mutate a workspace.
"""

from __future__ import annotations


from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from .contracts import check_skill_readiness, parse_skill_interaction
from .loader import discover_skills, load_skill, register_skill_tools


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
    """Return public command entrypoints, excluding private support modules.

    Executor Skills keep shared parsers and lineage helpers in ``_*.py``.
    Those modules are imported by real CLI entrypoints and deliberately do not
    implement a ``--help`` contract. Treating them as commands produced a
    misleading smoke-test success even though no user could invoke them.
    """

    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if not path.name.startswith("_"))


def _runtime_tool_names(public_root: Path) -> set[str]:
    """Build the normal public-Skill registry and return its registered names.

    A parsed ``tools`` list is not enough: the agent only fails after an LLM
    session starts if a capability profile or explicit tool name lacks a
    factory. Reconstructing the ordinary registry here keeps this audit
    read-only while checking the same binding path used by the CLI.
    """

    from ..runtime.config import RuntimeSettings
    from ..tools.builtin import register_builtin_tools
    from ..tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    skills = discover_skills(public_root)
    register_skill_tools(registry, [public_root], discovered_skills=skills)
    return set(registry.available_names())


def _audit_skill(
    *,
    skill_dir: Path,
    kind: str,
    check_script_help: bool,
    runtime_tool_names: set[str] | None = None,
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

    if runtime_tool_names is not None:
        unknown_tools = sorted({name for name in skill.allowed_tools if name not in runtime_tool_names})
        for name in unknown_tools:
            _record_issue(record, "unregistered_declared_tool", name)

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


def _audit_execution_boundary(*, record: dict[str, Any]) -> None:
    """Check that a package's declared scope matches its repository layer."""

    kind = str(record.get("kind") or "")
    scope = str(record.get("execution_scope") or "")
    owner = str(record.get("execution_owner") or "")
    if kind == "public" and scope == "internal_only":
        _record_issue(
            record,
            "internal_skill_in_public_catalog",
            "internal_only packages must not live directly under skills/",
        )
    if kind == "external_executor" and scope != "executor_template":
        _record_issue(
            record,
            "executor_template_scope_required",
            "external executor templates must declare execution_scope: executor_template",
        )
    if scope != "standalone" and not owner:
        _record_issue(record, "managed_skill_owner_missing", "non-standalone Skill has no execution_owner")


def _audit_standalone_interaction(
    *,
    skill_dir: Path,
    record: dict[str, Any],
    repo_root: Path,
    workspace: Path,
) -> None:
    """Run the no-model interaction surfaces of one public standalone Skill.

    This checks the interface a researcher actually receives: an empty
    workspace must yield a precise readiness state, and ``describe-skill``
    must render successfully without preparing an LLM runtime.  It does not
    claim that arbitrary source material would make the subsequent research
    task scientifically complete.
    """

    skill = load_skill(skill_dir)
    interaction = parse_skill_interaction(skill.metadata)
    if interaction is None or interaction.mode != "guided":
        _record_issue(
            record,
            "standalone_guided_interaction_required",
            "repository standalone Skills must declare interaction.mode: guided",
        )
        return
    if not interaction.request_prompt.strip():
        _record_issue(record, "interaction_request_prompt_missing", "guided interaction has no request_prompt")
    if not interaction.outputs:
        _record_issue(record, "interaction_outputs_missing", "guided interaction has no declared outputs")
    readiness = check_skill_readiness(
        skill_name=skill.name,
        metadata=skill.metadata,
        workspace=workspace,
        request="audit request",
    )
    record["interaction_readiness"] = "ready" if readiness.ready else "waiting_inputs"
    record["interaction_inputs_checked"] = len(readiness.input_statuses)
    if not readiness.request_ready:
        _record_issue(record, "interaction_request_not_accepted", "non-empty audit request was not accepted")
    if len(readiness.input_statuses) != len(interaction.required_inputs) + len(interaction.optional_inputs):
        _record_issue(record, "interaction_readiness_incomplete", "readiness did not report every declared input")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "researchos.cli",
            "describe-skill",
            skill.name,
            "--workspace",
            str(workspace),
            "--no-banner",
            "--no-color",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    record["description_checked"] = True
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")[:500]
        _record_issue(record, "describe_skill_failed", f"exit {completed.returncode}; {detail}")
        return
    output = completed.stdout
    if skill.name not in output or "开始前需要提供" not in output or "完成后会得到" not in output:
        _record_issue(record, "describe_skill_incomplete", "description omitted the guided input/output contract")


def _audit_managed_route(
    *,
    skill_dir: Path,
    record: dict[str, Any],
    repo_root: Path,
    workspace: Path,
    skills_root: Path | None = None,
) -> None:
    """Verify managed modules reject direct execution with an actionable route."""

    skill = load_skill(skill_dir)
    command = [
        sys.executable,
        "-m",
        "researchos.cli",
        "run-skill",
        skill.name,
        "audit",
        "--workspace",
        str(workspace),
        "--non-interactive",
        "--no-banner",
        "--no-color",
    ]
    if skills_root is not None:
        command.extend(["--skills-root", str(skills_root)])
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    record["managed_route_checked"] = True
    output = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode != 2:
        _record_issue(record, "managed_run_not_rejected", f"expected exit 2, received {completed.returncode}")
    if "不支持独立运行" not in output or "建议命令：" not in output:
        _record_issue(record, "managed_route_not_actionable", "direct-run refusal did not include a safe next command")


def audit_skill_suite(
    repo_root: Path,
    *,
    check_script_help: bool = False,
    check_interactions: bool = False,
) -> dict[str, Any]:
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
    runtime_tools: set[str] | None = None
    if not public_root.is_dir():
        suite_errors.append({"code": "missing_public_skills_root", "message": str(public_root)})
    else:
        try:
            runtime_tools = _runtime_tool_names(public_root)
        except Exception as exc:  # noqa: BLE001 - keep auditing individual packages after registry failure
            suite_errors.append({"code": "runtime_tool_registry_unavailable", "message": str(exc)})
        for skill_md in sorted(public_root.glob("*/SKILL.md")):
            records.append(
                _audit_skill(
                    skill_dir=skill_md.parent,
                    kind="public",
                    check_script_help=check_script_help,
                    runtime_tool_names=runtime_tools,
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

    for record in records:
        if not record.get("errors"):
            _audit_execution_boundary(record=record)

    if check_interactions:
        # The audit creates one isolated empty workspace.  Readiness checks are
        # non-mutating; subprocesses only render descriptions or reject managed
        # routes before workspace initialization and model setup.
        with tempfile.TemporaryDirectory(prefix="researchos_skill_interaction_audit_") as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            jobs: list[tuple[dict[str, Any], str, Path, Path | None]] = []
            for record in records:
                if record.get("errors"):
                    continue
                skill_dir = root / str(record["path"])
                kind = str(record.get("kind"))
                scope = str(record.get("execution_scope"))
                if kind == "public" and scope == "standalone":
                    jobs.append((record, "standalone", skill_dir, None))
                elif kind == "public":
                    jobs.append((record, "managed", skill_dir, None))
                elif kind == "external_executor":
                    jobs.append((record, "managed", skill_dir, executor_root))

            # Each job reads only its own immutable contract and starts an
            # isolated subprocess.  A bounded pool keeps the audit responsive
            # (55 Python imports would otherwise take minutes) without an
            # unbounded process burst on shared research machines.
            with ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as executor:
                futures = {
                    executor.submit(
                        _run_interaction_audit_job,
                        record=record,
                        kind=job_kind,
                        skill_dir=skill_dir,
                        repo_root=root,
                        workspace=workspace,
                        skills_root=skills_root,
                    ): record
                    for record, job_kind, skill_dir, skills_root in jobs
                }
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - retain the remaining audit findings
                        _record_issue(record, "interaction_audit_runtime_error", str(exc))

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
    by_scope = Counter(str(record.get("execution_scope") or "unknown") for record in records)
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
            "runtime_tool_bindings_checked": sum(
                1 for record in records if record["kind"] == "public" and runtime_tools is not None
            ),
            "standalone_skills": by_scope["standalone"],
            "pipeline_owned_skills": by_scope["state_machine"],
            "executor_templates": by_scope["executor_template"],
            "interaction_contracts_checked": sum(
                1 for record in records if record.get("interaction_readiness") is not None
            ),
            "description_commands_checked": sum(
                1 for record in records if record.get("description_checked") is True
            ),
            "managed_routes_checked": sum(
                1 for record in records if record.get("managed_route_checked") is True
            ),
        },
        "errors": suite_errors,
        "skills": records,
    }


def _run_interaction_audit_job(
    *,
    record: dict[str, Any],
    kind: str,
    skill_dir: Path,
    repo_root: Path,
    workspace: Path,
    skills_root: Path | None,
) -> None:
    """Dispatch one independent no-model interaction audit job."""

    if kind == "standalone":
        _audit_standalone_interaction(
            skill_dir=skill_dir,
            record=record,
            repo_root=repo_root,
            workspace=workspace,
        )
        return
    _audit_managed_route(
        skill_dir=skill_dir,
        record=record,
        repo_root=repo_root,
        workspace=workspace,
        skills_root=skills_root,
    )


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
        (
            "Execution scopes: "
            f"{summary.get('standalone_skills', 0)} standalone, "
            f"{summary.get('pipeline_owned_skills', 0)} pipeline-owned, "
            f"{summary.get('executor_templates', 0)} executor templates"
        ),
    ]
    if summary.get("interaction_contracts_checked", 0) or summary.get("managed_routes_checked", 0):
        lines.append(
            "Interaction checks: "
            f"{summary.get('interaction_contracts_checked', 0)} readiness, "
            f"{summary.get('description_commands_checked', 0)} descriptions, "
            f"{summary.get('managed_routes_checked', 0)} managed routes"
        )
    for issue in report.get("errors", []):
        lines.append(f"- {issue.get('code')}: {issue.get('message')}")
    for record in report.get("skills", []):
        if record.get("status") == "pass":
            continue
        for issue in record.get("errors", []):
            lines.append(f"- {record.get('name')}: {issue.get('code')}: {issue.get('message')}")
    return "\n".join(lines)
