from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

from ...runtime.agent import AgentResult, ExecutionContext
from ..agent import SkillAgent
from ..loader import Skill, load_skill
from .compiler import _atomic_write_json, specialize_project_skills
from .paths import build_specialization_paths
from .validation import (
    make_error,
    validate_context_schema,
    validate_json_schema,
    validate_mapping,
    validate_template_integrity,
    validate_template_markers,
)


TASK_ID = "T5-SPECIALIZE-EXECUTOR-SKILLS"
SKILL_NAME = "project-skill-specialization"
SKILL_REL_PATH = "skills/project-skill-specialization/SKILL.md"
EXECUTION_REL_PATH = "external_executor/report/skill_specialization_execution.json"
EXPECTED_SKILL_COUNT = 13
ACTIVE_EXECUTOR_STATUSES = {"running", "experiment_running", "claimed_by_executor"}

REQUIRED_WORKSPACE_INPUTS = (
    "project.yaml",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/AGENTS.md",
    "external_executor/allowed_paths.txt",
)
OPTIONAL_SOURCE_INPUTS = (
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/risks.md",
    "ideation/novelty_audit.md",
    "novelty/novelty_audit.md",
    "ideation/idea_scorecard.yaml",
    "literature/synthesis.md",
    "literature/synthesis_workbench.json",
    "literature/domain_map.json",
    "literature/comparison_table.csv",
    "literature/bridge_domain_plan.json",
    "literature/cross_domain_catalogs/index.json",
    "literature/deep_read_notes",
    "literature/bridge_notes",
    "literature/cross_domain_catalogs",
    "literature/shallow_read_notes",
    "literature/notes_manifest.json",
)
REQUIRED_SKILL_SCRIPTS = (
    "_common.py",
    "preflight_specialization.py",
    "run_specialization.py",
    "summarize_specialization_report.py",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _rel_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass
class TaskValidation:
    status: str
    report_status: str = "failed"
    checked_at: str = field(default_factory=_now_iso)
    skills_specialized: int = 0
    skills_total: int = EXPECTED_SKILL_COUNT
    required_uncertain_fields: list[Any] = field(default_factory=list)
    optional_uncertain_fields: list[Any] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "pass" and self.report_status in {"ready", "incomplete"}

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_at": self.checked_at,
            "report_status": self.report_status,
            "skills_specialized": self.skills_specialized,
            "skills_total": self.skills_total,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class ProjectSkillSpecializationAgent(SkillAgent):
    """Task adapter for running the repository project-specialization Skill."""

    def __init__(
        self,
        *,
        skill: Skill,
        available_tools: set[str],
        llm_profile: str | None = None,
    ) -> None:
        super().__init__(skill=skill, available_tools=available_tools, llm_profile=llm_profile)
        self.spec.pre_hooks.append(project_skill_specialization_preflight_hook)
        self.spec.post_hooks.append(project_skill_specialization_post_hook)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        request = str(ctx.extra.get("user_request") or "").strip()
        if request:
            return (
                request.replace("Workspace: <resolved by runtime>", f"Workspace: {ctx.workspace_dir.resolve()}")
                .replace("Repository root: <resolved by runtime>", f"Repository root: {_repo_root_from_ctx(ctx).resolve()}")
            )
        return default_task_request(ctx.workspace_dir, _repo_root_from_ctx(ctx))

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        if ctx.task_id != TASK_ID:
            return super().validate_outputs(ctx)
        if not _current_run_executed_wrapper(ctx) and not ctx.extra.get(
            "project_skill_specialization_reused"
        ):
            return False, "project-skill-specialization wrapper was not executed successfully in this task run"
        validation = validate_project_skill_specialization_outputs(
            workspace=ctx.workspace_dir,
            repo_root=_repo_root_from_ctx(ctx),
        )
        ctx.extra["project_skill_specialization_validation"] = validation.to_record()
        ctx.extra["project_skill_specialization_report_status"] = validation.report_status
        if not validation.ok:
            return False, _format_error_summary(validation.errors)
        return True, None


def build_project_skill_specialization_agent(
    *,
    skill: Skill,
    available_tools: set[str],
    llm_profile: str | None = None,
) -> ProjectSkillSpecializationAgent:
    return ProjectSkillSpecializationAgent(
        skill=skill,
        available_tools=available_tools,
        llm_profile=llm_profile,
    )


def default_task_request(workspace: Path, repo_root: Path) -> str:
    return (
        "Execute the repository skill `project-skill-specialization` for the current "
        "ResearchOS workspace in build mode.\n\n"
        f"Workspace: {workspace.resolve()}\n"
        f"Repository root: {repo_root.resolve()}\n\n"
        "Use the Skill's bundled preflight, specialization, and report-summary scripts. "
        "Do not manually create or edit project_skill_context.yaml. "
        "Do not directly rewrite Project-Specific Guidance. "
        "Do not modify content outside reserved markers. "
        "Finish only after reading the durable specialization report."
    )


def _repo_root_from_ctx(ctx: ExecutionContext) -> Path:
    raw = ctx.extra.get("project_skill_specialization_repo_root") or ctx.extra.get("repo_root")
    if raw:
        return Path(str(raw)).resolve()
    return repository_root()


def _skill_dir(repo_root: Path) -> Path:
    return repo_root / "skills" / SKILL_NAME


def _current_run_executed_wrapper(ctx: ExecutionContext) -> bool:
    return int(ctx.extra.get("project_skill_specialization_wrapper_success_count", 0) or 0) > 0


def project_skill_specialization_preflight_hook(ctx: ExecutionContext) -> tuple[bool, str | None]:
    if ctx.task_id != TASK_ID:
        return True, None
    repo_root = _repo_root_from_ctx(ctx)
    ctx.extra["project_skill_specialization_started_at"] = ctx.extra.get(
        "project_skill_specialization_started_at",
        _now_iso(),
    )
    ctx.extra["project_skill_specialization_repo_root"] = str(repo_root)
    ctx.extra["skill_dir"] = str(_skill_dir(repo_root))
    preflight = run_project_skill_specialization_preflight(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
        mode="build",
    )
    ctx.extra["project_skill_specialization_preflight"] = preflight
    fingerprint = build_project_skill_specialization_fingerprint(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    ctx.extra["project_skill_specialization_input_fingerprint"] = fingerprint
    if preflight["status"] == "fail":
        return False, _format_error_summary(preflight["errors"])
    return True, None


def run_project_skill_specialization_preflight(
    *,
    workspace: Path,
    repo_root: Path,
    mode: str = "build",
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    workspace = workspace.resolve()
    repo_root = repo_root.resolve()
    paths = build_specialization_paths(workspace=workspace, repo_root=repo_root)
    skill_dir = _skill_dir(repo_root)

    if not workspace.is_dir():
        errors.append(make_error("workspace_not_found", "workspace directory does not exist", path=str(workspace)))
    for rel in REQUIRED_WORKSPACE_INPUTS:
        if not (workspace / rel).is_file():
            errors.append(make_error("required_input_missing", "required artifact is missing", path=rel))
    for rel in OPTIONAL_SOURCE_INPUTS:
        if not (workspace / rel).exists():
            warnings.append(make_error("optional_source_missing", "optional source is missing; compiler may mark fields uncertain", path=rel))

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append(make_error("repository_skill_missing", "project-skill-specialization SKILL.md missing", path=str(skill_md)))
    else:
        try:
            skill = load_skill(skill_dir)
            if skill.name != SKILL_NAME:
                errors.append(make_error("repository_skill_invalid", f"Skill name must be {SKILL_NAME}", path=str(skill_md)))
        except Exception as exc:  # noqa: BLE001 - frontmatter diagnostics
            errors.append(make_error("repository_skill_frontmatter_invalid", str(exc), path=str(skill_md)))
    references_dir = skill_dir / "references"
    scripts_dir = skill_dir / "scripts"
    if not references_dir.is_dir():
        errors.append(make_error("repository_skill_references_missing", "references/ is missing", path=str(references_dir)))
    if not scripts_dir.is_dir():
        errors.append(make_error("repository_skill_scripts_missing", "scripts/ is missing", path=str(scripts_dir)))
    for script in REQUIRED_SKILL_SCRIPTS:
        if not (scripts_dir / script).is_file():
            errors.append(make_error("repository_skill_script_missing", "required Skill script is missing", path=str(scripts_dir / script)))

    schema: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    if not paths.mapping_path.is_file():
        errors.append(make_error("mapping_missing", "skill_specialization.yaml is missing", path=str(paths.mapping_path)))
    else:
        try:
            mapping = yaml.safe_load(paths.mapping_path.read_text(encoding="utf-8")) or {}
            if not isinstance(mapping, dict):
                raise TypeError("mapping root must be an object")
        except Exception as exc:  # noqa: BLE001
            errors.append(make_error("mapping_parse_error", str(exc), path=str(paths.mapping_path)))
    if not paths.schema_path.is_file():
        errors.append(make_error("schema_missing", "project_skill_context schema is missing", path=str(paths.schema_path)))
    else:
        try:
            schema = json.loads(paths.schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise TypeError("schema root must be an object")
        except Exception as exc:  # noqa: BLE001
            errors.append(make_error("schema_parse_error", str(exc), path=str(paths.schema_path)))
    if schema is not None:
        errors.extend(validate_json_schema(schema))
    if schema is not None and mapping is not None:
        errors.extend(validate_mapping(schema=schema, mapping=mapping, template_root=paths.template_root))
        errors.extend(_template_marker_preflight(paths.template_root, mapping))

    executor_status = _read_executor_status(workspace)
    if executor_status == "unreadable":
        errors.append(make_error("executor_status_unreadable", "executor_status.json cannot be parsed safely", path="external_executor/executor_status.json"))
    elif mode == "build" and executor_status in ACTIVE_EXECUTOR_STATUSES:
        errors.append(
            make_error(
                "active_executor_suite_cannot_be_replaced",
                "external executor suite cannot be replaced while executor is active",
                path="external_executor/executor_status.json",
            )
        )

    return {
        "schema_version": "project_skill_specialization_task_preflight.v1",
        "status": "fail" if errors else ("warning" if warnings else "pass"),
        "mode": mode,
        "workspace": str(workspace),
        "repo_root": str(repo_root),
        "skill_name": SKILL_NAME,
        "skill_path": SKILL_REL_PATH,
        "errors": errors,
        "warnings": warnings,
    }


def _template_marker_preflight(template_root: Path, mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    guidance = mapping.get("guidance") if isinstance(mapping.get("guidance"), Mapping) else {}
    begin = str(guidance.get("begin_marker") or "")
    end = str(guidance.get("end_marker") or "")
    skills = mapping.get("skills") if isinstance(mapping.get("skills"), Mapping) else {}
    for skill_name in skills:
        skill_path = template_root / str(skill_name) / "SKILL.md"
        if not skill_path.is_file():
            continue
        try:
            text = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(make_error("template_skill_unreadable", str(exc), path=str(skill_path), skill_name=str(skill_name)))
            continue
        errors.extend(validate_template_markers(text, begin, end, skill_name=str(skill_name)))
    return errors


def _read_executor_status(workspace: Path) -> str | None:
    path = workspace / "external_executor" / "executor_status.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    if not isinstance(payload, Mapping):
        return None
    for key in ("executor_status", "status", "current_state"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip().lower()
    return None


def validate_project_skill_specialization_outputs(
    *,
    workspace: Path,
    repo_root: Path | None = None,
) -> TaskValidation:
    repo_root = (repo_root or repository_root()).resolve()
    workspace = workspace.resolve()
    paths = build_specialization_paths(workspace=workspace, repo_root=repo_root)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {}

    result = specialize_project_skills(workspace=workspace, repo_root=repo_root, validate_only=True)
    if result.errors:
        errors.extend(result.errors)
    if result.status == "failed":
        errors.append(make_error("validate_only_failed", "Project Skill Specializer validate-only failed", path="external_executor/report/skill_specialization_report.json"))

    if not paths.output_schema_path.is_file():
        errors.append(make_error("context_schema_missing", "workspace project_skill_context.schema.json missing", path="external_executor/schemas/project_skill_context.schema.json"))
        workspace_schema: dict[str, Any] | None = None
    else:
        try:
            workspace_schema = json.loads(paths.output_schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(workspace_schema)
        except Exception as exc:  # noqa: BLE001
            workspace_schema = None
            errors.append(make_error("context_schema_invalid", str(exc), path="external_executor/schemas/project_skill_context.schema.json"))

    if paths.output_report_path.is_file():
        try:
            report = json.loads(paths.output_report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise TypeError("report root must be an object")
        except Exception as exc:  # noqa: BLE001
            errors.append(make_error("specialization_report_unreadable", str(exc), path="external_executor/report/skill_specialization_report.json"))
            report = {}
    else:
        errors.append(make_error("specialization_report_missing", "skill_specialization_report.json missing", path="external_executor/report/skill_specialization_report.json"))

    context: dict[str, Any] = {}
    if paths.output_context_path.is_file():
        try:
            loaded = yaml.safe_load(paths.output_context_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise TypeError("context root must be an object")
            context = loaded
        except Exception as exc:  # noqa: BLE001
            errors.append(make_error("context_unreadable", str(exc), path="external_executor/project_skill_context.yaml"))
    else:
        errors.append(make_error("context_missing", "project_skill_context.yaml missing", path="external_executor/project_skill_context.yaml"))
    if workspace_schema is not None and context:
        errors.extend(validate_context_schema(workspace_schema, context))

    try:
        root_schema = json.loads(paths.schema_path.read_text(encoding="utf-8"))
        mapping = yaml.safe_load(paths.mapping_path.read_text(encoding="utf-8")) or {}
        if not isinstance(mapping, dict):
            raise TypeError("mapping root must be an object")
        errors.extend(validate_mapping(schema=root_schema, mapping=mapping, template_root=paths.template_root))
    except Exception as exc:  # noqa: BLE001
        mapping = {}
        errors.append(make_error("mapping_or_schema_load_failed", str(exc), path=str(paths.mapping_path)))

    errors.extend(_validate_report_contract(report, mapping))
    errors.extend(_validate_specialized_skills(paths, mapping, report))

    report_status = str(report.get("status") or result.status or "failed")
    required_uncertain = list(report.get("required_uncertain_fields") or [])
    if report_status == "ready" and required_uncertain:
        errors.append(make_error("ready_with_required_uncertain_fields", "ready report must not contain required uncertain fields", path="external_executor/report/skill_specialization_report.json"))
    if report_status == "incomplete" and not required_uncertain:
        errors.append(make_error("incomplete_without_required_uncertain_fields", "incomplete report must list required uncertain fields", path="external_executor/report/skill_specialization_report.json"))

    return TaskValidation(
        status="fail" if errors else "pass",
        report_status=report_status if report_status in {"ready", "incomplete", "failed"} else "failed",
        skills_specialized=int(report.get("skills_specialized") or 0),
        skills_total=int(report.get("skills_total") or EXPECTED_SKILL_COUNT),
        required_uncertain_fields=required_uncertain,
        optional_uncertain_fields=list(report.get("optional_uncertain_fields") or []),
        warnings=warnings,
        errors=errors,
        report=report,
    )


def _validate_report_contract(report: Mapping[str, Any], mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    skills = mapping.get("skills") if isinstance(mapping.get("skills"), Mapping) else {}
    expected_names = {str(name) for name in skills}
    if report.get("schema_version") != "skill_specialization_report.v1":
        errors.append(make_error("report_schema_version_invalid", "report schema_version must be skill_specialization_report.v1", path="external_executor/report/skill_specialization_report.json"))
    if report.get("status") not in {"ready", "incomplete"}:
        errors.append(make_error("report_status_invalid", "report status must be ready or incomplete", path="external_executor/report/skill_specialization_report.json"))
    if report.get("context_file") != "external_executor/project_skill_context.yaml":
        errors.append(make_error("report_context_file_invalid", "report context_file path is incorrect", path="external_executor/report/skill_specialization_report.json"))
    if report.get("context_schema") != "external_executor/schemas/project_skill_context.schema.json":
        errors.append(make_error("report_context_schema_invalid", "report context_schema path is incorrect", path="external_executor/report/skill_specialization_report.json"))
    if int(report.get("skills_total") or 0) != EXPECTED_SKILL_COUNT:
        errors.append(make_error("report_skill_total_invalid", "report skills_total must be 13", path="external_executor/report/skill_specialization_report.json"))
    if int(report.get("skills_specialized") or 0) != EXPECTED_SKILL_COUNT:
        errors.append(make_error("report_skill_count_invalid", "report skills_specialized must be 13", path="external_executor/report/skill_specialization_report.json"))
    report_skills = report.get("skills")
    if not isinstance(report_skills, list):
        errors.append(make_error("report_skills_invalid", "report skills must be a list", path="external_executor/report/skill_specialization_report.json"))
        return errors
    reported_names = {str(item.get("skill_name")) for item in report_skills if isinstance(item, Mapping)}
    missing = sorted(expected_names - reported_names)
    extra = sorted(reported_names - expected_names)
    if missing:
        errors.append(make_error("report_skills_missing", "report missing skills: " + ", ".join(missing), path="external_executor/report/skill_specialization_report.json"))
    if extra:
        errors.append(make_error("report_skills_extra", "report contains unexpected skills: " + ", ".join(extra), path="external_executor/report/skill_specialization_report.json"))
    return errors


def _validate_specialized_skills(paths, mapping: Mapping[str, Any], report: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    guidance = mapping.get("guidance") if isinstance(mapping.get("guidance"), Mapping) else {}
    begin = str(guidance.get("begin_marker") or "")
    end = str(guidance.get("end_marker") or "")
    skills = mapping.get("skills") if isinstance(mapping.get("skills"), Mapping) else {}
    report_items = {
        str(item.get("skill_name")): item
        for item in report.get("skills", []) or []
        if isinstance(item, Mapping) and item.get("skill_name")
    }
    for skill_name in skills:
        name = str(skill_name)
        template_path = paths.template_root / name / "SKILL.md"
        output_path = paths.output_skills_path / name / "SKILL.md"
        if not output_path.is_file():
            errors.append(make_error("specialized_skill_missing", "specialized Skill missing", path=_rel_to_workspace(output_path, paths.workspace), skill_name=name))
            continue
        text = output_path.read_text(encoding="utf-8", errors="replace")
        marker_errors = validate_template_markers(text, begin, end, skill_name=name)
        errors.extend(marker_errors)
        if not marker_errors:
            start = text.index(begin) + len(begin)
            stop = text.index(end)
            guidance_text = text[start:stop]
            if "## Project-Specific Guidance" not in guidance_text or len(guidance_text.strip()) < 80:
                errors.append(make_error("project_guidance_empty", "Project-Specific Guidance is missing or empty", path=_rel_to_workspace(output_path, paths.workspace), skill_name=name))
        if template_path.is_file():
            template_text = template_path.read_text(encoding="utf-8", errors="replace")
            errors.extend(
                validate_template_integrity(
                    template_text=template_text,
                    rendered_text=text,
                    begin_marker=begin,
                    end_marker=end,
                    skill_name=name,
                )
            )
        report_item = report_items.get(name)
        if isinstance(report_item, Mapping) and report_item.get("template_integrity") == "fail":
            errors.append(make_error("report_template_integrity_failed", "report records template integrity failure", skill_name=name))
    return errors


def build_project_skill_specialization_fingerprint(
    *,
    workspace: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = (repo_root or repository_root()).resolve()
    workspace = workspace.resolve()
    paths = build_specialization_paths(workspace=workspace, repo_root=repo_root)
    items: dict[str, dict[str, Any]] = {}
    for rel in REQUIRED_WORKSPACE_INPUTS:
        items[f"workspace:{rel}"] = _fingerprint_path(workspace / rel, display_path=rel)
    for rel in OPTIONAL_SOURCE_INPUTS:
        path = workspace / rel
        if path.exists():
            items[f"workspace:{rel}"] = _fingerprint_path(path, display_path=rel)
    repo_paths = {
        "repo:project_skill": _skill_dir(repo_root),
        "repo:project_specialization_compiler": repo_root / "researchos" / "skills" / "project_specialization",
        "repo:skill_specialization_mapping": paths.mapping_path,
        "repo:project_skill_context_schema": paths.schema_path,
        "repo:external_executor_templates": paths.template_root,
    }
    for label, path in repo_paths.items():
        items[label] = _fingerprint_path(path, display_path=path.relative_to(repo_root).as_posix())
    digest = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "project_skill_specialization_input_fingerprint.v1",
        "hash": digest,
        "generated_at": _now_iso(),
        "items": items,
    }


def _fingerprint_path(path: Path, *, display_path: str) -> dict[str, Any]:
    item: dict[str, Any] = {"path": display_path, "exists": path.exists()}
    if not path.exists():
        return item
    if path.is_file():
        item["kind"] = "file"
        item["size"] = path.stat().st_size
        item["sha256"] = _sha256_file(path)
        return item
    if path.is_dir():
        digest = hashlib.sha256()
        count = 0
        for child in sorted((entry for entry in path.rglob("*") if entry.is_file()), key=lambda entry: entry.relative_to(path).as_posix()):
            rel = child.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_sha256_file(child).encode("ascii"))
            digest.update(b"\0")
            count += 1
        item["kind"] = "dir"
        item["file_count"] = count
        item["sha256"] = digest.hexdigest()
    return item


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def can_reuse_existing_project_skill_specialization(ctx: ExecutionContext) -> bool:
    if ctx.task_id != TASK_ID:
        return False
    repo_root = _repo_root_from_ctx(ctx)
    ctx.extra["project_skill_specialization_started_at"] = ctx.extra.get(
        "project_skill_specialization_started_at",
        _now_iso(),
    )
    ctx.extra["project_skill_specialization_repo_root"] = str(repo_root)
    ctx.extra["skill_dir"] = str(_skill_dir(repo_root))
    fingerprint = build_project_skill_specialization_fingerprint(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    ctx.extra["project_skill_specialization_input_fingerprint"] = fingerprint
    execution = _read_execution_record(ctx.workspace_dir)
    if not execution:
        return False
    if execution.get("status") not in {"ready", "incomplete"}:
        return False
    if execution.get("input_fingerprint") != fingerprint["hash"]:
        return False
    preflight = run_project_skill_specialization_preflight(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
        mode="validate-only",
    )
    ctx.extra["project_skill_specialization_preflight"] = preflight
    if preflight["status"] == "fail":
        return False
    validation = validate_project_skill_specialization_outputs(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    if not validation.ok:
        return False
    ctx.extra["project_skill_specialization_reused"] = True
    ctx.extra["project_skill_specialization_validation"] = validation.to_record()
    ctx.extra["project_skill_specialization_report_status"] = validation.report_status
    ctx.extra["completion_mode"] = "project_skill_specialization_reused"
    return True


def _read_execution_record(workspace: Path) -> dict[str, Any] | None:
    path = workspace / EXECUTION_REL_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def project_skill_specialization_post_hook(ctx: ExecutionContext, result: AgentResult) -> None:
    if ctx.task_id != TASK_ID:
        return
    repo_root = _repo_root_from_ctx(ctx)
    validation = validate_project_skill_specialization_outputs(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    preflight = ctx.extra.get("project_skill_specialization_preflight")
    preflight_warnings = preflight.get("warnings", []) if isinstance(preflight, Mapping) else []
    warnings = list(preflight_warnings) + list(validation.warnings)
    errors = list(validation.errors)
    wrapper_called = _current_run_executed_wrapper(ctx)
    reused = bool(ctx.extra.get("project_skill_specialization_reused"))

    if result.ok and not validation.ok:
        result.ok = False
        result.stop_reason = AgentResult.STOP_ERROR
        result.error = "ResearchOS deterministic post-validation failed: " + _format_error_summary(errors)
        result.message = result.error
    elif not result.ok and validation.ok and (wrapper_called or reused):
        warnings.append(
            make_error(
                "llm_response_artifact_mismatch",
                "LLM did not report a successful finish, but durable artifacts validate; using artifact status",
            )
        )
        result.ok = True
        result.stop_reason = AgentResult.STOP_FINISHED
        result.error = None
        result.message = "Agent completed; durable specialization artifacts validated"

    final_status = validation.report_status if result.ok and validation.ok else "failed"
    if final_status == "failed" and result.error:
        errors.append(make_error("task_execution_failed", result.error, path=EXECUTION_REL_PATH))
    record = _execution_record(
        ctx=ctx,
        result=result,
        final_status=final_status,
        validation=validation,
        warnings=warnings,
        errors=errors,
    )
    execution_path = ctx.workspace_dir / EXECUTION_REL_PATH
    try:
        _atomic_write_json(execution_path, record)
    except Exception as exc:  # noqa: BLE001 - make write failure final
        result.ok = False
        result.stop_reason = AgentResult.STOP_ERROR
        result.error = f"failed to write {EXECUTION_REL_PATH}: {exc}"
        result.message = result.error
        return
    result.outputs_produced["skill_specialization_execution"] = execution_path
    result.outputs_produced.setdefault("skill_specialization_report", ctx.workspace_dir / "external_executor/report/skill_specialization_report.json")
    result.metadata.setdefault("project_skill_specialization", {})
    result.metadata["project_skill_specialization"] = _summary_for_metadata(record)


def write_deterministic_project_skill_specialization_execution(
    *,
    workspace: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write the task execution record for deterministic/offline repair runs."""

    repo_root = (repo_root or repository_root()).resolve()
    workspace = workspace.resolve()
    validation = validate_project_skill_specialization_outputs(
        workspace=workspace,
        repo_root=repo_root,
    )
    fingerprint = build_project_skill_specialization_fingerprint(
        workspace=workspace,
        repo_root=repo_root,
    )
    final_status = validation.report_status if validation.ok else "failed"
    record = {
        "schema_version": "project_skill_specialization_execution.v1",
        "task_id": TASK_ID,
        "skill_name": SKILL_NAME,
        "skill_path": SKILL_REL_PATH,
        "mode": "build",
        "status": final_status,
        "input_fingerprint": str(fingerprint.get("hash") or ""),
        "input_fingerprints": fingerprint.get("items") if isinstance(fingerprint.get("items"), Mapping) else {},
        "llm_run": {
            "trace_id": "deterministic-cli",
            "trace_file": "",
            "model_profile": "deterministic",
            "model_tier": "deterministic",
            "model": "deterministic_project_specialization",
            "endpoint": "local",
            "started_at": "",
            "finished_at": _now_iso(),
            "finish_reason": "finished" if validation.ok else "error",
            "reused_existing_artifacts": False,
        },
        "outputs": {
            "context": "external_executor/project_skill_context.yaml",
            "schema": "external_executor/schemas/project_skill_context.schema.json",
            "skills": "external_executor/skills",
            "report": "external_executor/report/skill_specialization_report.json",
            "execution": EXECUTION_REL_PATH,
        },
        "skills_specialized": int(validation.report.get("skills_specialized") or validation.skills_specialized),
        "required_uncertain_fields": list(validation.report.get("required_uncertain_fields") or validation.required_uncertain_fields),
        "warnings": _dedupe_messages(list(validation.warnings)),
        "errors": _dedupe_messages(list(validation.errors)),
        "validation": validation.to_record(),
    }
    _atomic_write_json(workspace / EXECUTION_REL_PATH, record)
    return record


def _execution_record(
    *,
    ctx: ExecutionContext,
    result: AgentResult,
    final_status: str,
    validation: TaskValidation,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    fingerprint = ctx.extra.get("project_skill_specialization_input_fingerprint")
    if not isinstance(fingerprint, Mapping):
        fingerprint = build_project_skill_specialization_fingerprint(
            workspace=ctx.workspace_dir,
            repo_root=_repo_root_from_ctx(ctx),
        )
    trace_file = result.trace_file
    trace_rel = _rel_to_workspace(trace_file, ctx.workspace_dir) if trace_file else ""
    report = validation.report
    return {
        "schema_version": "project_skill_specialization_execution.v1",
        "task_id": TASK_ID,
        "skill_name": SKILL_NAME,
        "skill_path": SKILL_REL_PATH,
        "mode": "build",
        "status": final_status,
        "input_fingerprint": str(fingerprint.get("hash") or ""),
        "input_fingerprints": fingerprint.get("items") if isinstance(fingerprint.get("items"), Mapping) else {},
        "llm_run": {
            "trace_id": ctx.run_id,
            "trace_file": trace_rel,
            "model_profile": result.llm_profile or "",
            "model_tier": result.llm_tier or "",
            "model": result.llm_model_used or "",
            "endpoint": result.llm_endpoint_used or "",
            "started_at": str(ctx.extra.get("project_skill_specialization_started_at") or ""),
            "finished_at": _now_iso(),
            "finish_reason": result.stop_reason,
            "reused_existing_artifacts": bool(ctx.extra.get("project_skill_specialization_reused")),
        },
        "outputs": {
            "context": "external_executor/project_skill_context.yaml",
            "schema": "external_executor/schemas/project_skill_context.schema.json",
            "skills": "external_executor/skills",
            "report": "external_executor/report/skill_specialization_report.json",
            "execution": EXECUTION_REL_PATH,
        },
        "skills_specialized": int(report.get("skills_specialized") or validation.skills_specialized),
        "required_uncertain_fields": list(report.get("required_uncertain_fields") or validation.required_uncertain_fields),
        "warnings": _dedupe_messages(warnings),
        "errors": _dedupe_messages(errors),
        "validation": validation.to_record(),
    }


def _summary_for_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    required = record.get("required_uncertain_fields")
    return {
        "task": TASK_ID,
        "skill": SKILL_NAME,
        "status": record.get("status"),
        "skills": int(record.get("skills_specialized") or 0),
        "context": "external_executor/project_skill_context.yaml",
        "report": "external_executor/report/skill_specialization_report.json",
        "execution": EXECUTION_REL_PATH,
        "required_uncertain_count": len(required) if isinstance(required, list) else 0,
        "trace": (record.get("llm_run") or {}).get("trace_id") if isinstance(record.get("llm_run"), Mapping) else "",
    }


def _dedupe_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _format_error_summary(errors: list[dict[str, Any]] | object) -> str:
    if not isinstance(errors, list) or not errors:
        return "unknown project skill specialization error"
    parts: list[str] = []
    for item in errors[:5]:
        if isinstance(item, Mapping):
            code = str(item.get("code") or "error")
            path = str(item.get("path") or item.get("field_path") or "")
            message = str(item.get("message") or "")
            parts.append(": ".join(part for part in (code, path, message) if part))
        else:
            parts.append(str(item))
    if len(errors) > 5:
        parts.append(f"... {len(errors) - 5} more")
    return "; ".join(parts)


def mark_project_skill_specialization_bash_call(
    ctx: ExecutionContext,
    *,
    command: str,
    cwd: str | None,
    ok: bool,
) -> None:
    if ctx.task_id != TASK_ID:
        return
    skill_dir = str(ctx.extra.get("skill_dir") or "")
    normalized_cwd = str(Path(cwd).resolve()) if cwd else ""
    command_text = " ".join(str(command or "").split())
    in_skill_dir = bool(skill_dir and normalized_cwd == str(Path(skill_dir).resolve()))
    if "run_specialization.py" in command_text and (in_skill_dir or SKILL_NAME in command_text):
        ctx.extra["project_skill_specialization_wrapper_call_count"] = int(
            ctx.extra.get("project_skill_specialization_wrapper_call_count", 0) or 0
        ) + 1
        if ok:
            ctx.extra["project_skill_specialization_wrapper_success_count"] = int(
                ctx.extra.get("project_skill_specialization_wrapper_success_count", 0) or 0
            ) + 1
    if "preflight_specialization.py" in command_text and (in_skill_dir or SKILL_NAME in command_text):
        ctx.extra["project_skill_specialization_skill_preflight_call_count"] = int(
            ctx.extra.get("project_skill_specialization_skill_preflight_call_count", 0) or 0
        ) + 1
