from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from .context_builder import build_project_skill_context, ensure_metadata_for_injections
from .paths import build_specialization_paths, new_staging_dir, repo_relative, workspace_relative
from .renderer import render_skill_guidance
from .source_readers import read_json, read_yaml
from .types import RenderedSkill, SpecializationResult
from .validation import (
    make_error,
    validate_context_metadata,
    validate_context_schema,
    validate_json_schema,
    validate_mapping,
    validate_template_markers,
)


def specialize_project_skills(
    *,
    workspace: Path,
    repo_root: Path | None = None,
    dry_run: bool = False,
    validate_only: bool = False,
) -> SpecializationResult:
    paths = build_specialization_paths(workspace=workspace, repo_root=repo_root)
    paths.external_executor_root.mkdir(parents=True, exist_ok=True)
    try:
        schema = read_json(paths.schema_path)
        mapping = read_yaml(paths.mapping_path)
    except Exception as exc:
        return _failed(paths.output_report_path, [make_error("mapping_or_schema_load_failed", str(exc))])

    if validate_only:
        return _validate_existing(paths, schema, mapping)

    # A dry run only builds and validates an isolated staging suite.  It does
    # not publish or replace the executor's live Skills, so it remains safe
    # while an external executor is active.  A real build must still refuse
    # to replace that live suite.
    preflight_errors = _preflight(
        paths,
        schema,
        mapping,
        prohibit_active_executor=not dry_run,
    )
    if preflight_errors:
        report = _base_report(paths, status="failed")
        _extend_errors(report, preflight_errors)
        failure_report_path = _record_failure(paths, report, dry_run=dry_run)
        return SpecializationResult(
            status="failed",
            report_path=failure_report_path,
            context_path=paths.output_context_path,
            skills_path=paths.output_skills_path,
            errors=preflight_errors,
            report=report,
        )

    staging = new_staging_dir(paths.external_executor_root)
    skills_staging = staging / "skills"
    try:
        staging.mkdir(parents=True)
        context = build_project_skill_context(workspace=paths.workspace, schema=schema)
        ensure_metadata_for_injections(context, mapping)
        context_errors = validate_context_schema(schema, context) + validate_context_metadata(context, mapping)
        if context_errors:
            report = _base_report(paths, status="failed")
            _extend_errors(report, context_errors)
            failure_report_path = _record_failure(paths, report, dry_run=dry_run)
            return SpecializationResult(
                status="failed",
                report_path=failure_report_path,
                context_path=paths.output_context_path,
                skills_path=paths.output_skills_path,
                errors=context_errors,
                report=report,
            )

        context_staging = staging / "project_skill_context.yaml"
        schema_staging = staging / "schemas" / "project_skill_context.schema.json"
        _atomic_write_yaml(context_staging, context)
        _atomic_write_json(schema_staging, dict(schema))
        round_trip = yaml.safe_load(context_staging.read_text(encoding="utf-8"))
        context_errors = validate_context_schema(schema, round_trip) + validate_context_metadata(round_trip, mapping)
        if context_errors:
            report = _base_report(paths, status="failed")
            _extend_errors(report, context_errors)
            failure_report_path = _record_failure(paths, report, dry_run=dry_run)
            return SpecializationResult(
                status="failed",
                report_path=failure_report_path,
                context_path=paths.output_context_path,
                skills_path=paths.output_skills_path,
                errors=context_errors,
                report=report,
            )

        copy_errors = _copy_template_skills(paths.template_root, mapping, skills_staging)
        if copy_errors:
            report = _base_report(paths, status="failed")
            _extend_errors(report, copy_errors)
            failure_report_path = _record_failure(paths, report, dry_run=dry_run)
            return SpecializationResult(
                status="failed",
                report_path=failure_report_path,
                context_path=paths.output_context_path,
                skills_path=paths.output_skills_path,
                errors=copy_errors,
                report=report,
            )

        rendered = _render_all_skills(paths, skills_staging, context, mapping)
        render_errors = [
            error
            for skill in rendered
            for error in [*skill.render_errors, *skill.template_integrity_errors]
        ]
        report = _report_from_rendered(paths, rendered)
        report["source_overrides"] = _source_overrides(context)
        if render_errors:
            report["status"] = "failed"
            _extend_errors(report, render_errors)
            failure_report_path = _record_failure(paths, report, dry_run=dry_run)
            return SpecializationResult(
                status="failed",
                report_path=failure_report_path,
                context_path=paths.output_context_path,
                skills_path=paths.output_skills_path,
                errors=render_errors,
                report=report,
            )

        report_staging = staging / "skill_specialization_report.json"
        _atomic_write_json(report_staging, report)
        if not dry_run:
            _publish(paths, staging)
        return SpecializationResult(
            status=report["status"],
            report_path=paths.output_report_path,
            context_path=paths.output_context_path,
            skills_path=paths.output_skills_path,
            required_uncertain_fields=list(report.get("required_uncertain_fields") or []),
            optional_uncertain_fields=list(report.get("optional_uncertain_fields") or []),
            errors=[],
            report=report,
        )
    except Exception as exc:
        errors = [make_error("specialization_failed", str(exc))]
        report = _base_report(paths, status="failed")
        _extend_errors(report, errors)
        try:
            failure_report_path = _record_failure(paths, report, dry_run=dry_run)
        except Exception:
            failure_report_path = _failure_report_path(paths)
        return SpecializationResult(
            status="failed",
            report_path=failure_report_path,
            context_path=paths.output_context_path,
            skills_path=paths.output_skills_path,
            errors=errors,
            report=report,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _preflight(
    paths,
    schema: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    prohibit_active_executor: bool,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not paths.workspace.is_dir():
        errors.append(make_error("workspace_not_found", "workspace directory does not exist", path=str(paths.workspace)))
    if not (paths.workspace / "project.yaml").is_file():
        errors.append(make_error("workspace_not_found", "project.yaml is required", path="project.yaml"))
    if not paths.template_root.is_dir():
        errors.append(make_error("template_root_not_found", "external executor template root missing", path=str(paths.template_root)))
    if not paths.mapping_path.is_file():
        errors.append(make_error("mapping_missing", "skill_specialization.yaml missing", path=str(paths.mapping_path)))
    if not paths.schema_path.is_file():
        errors.append(make_error("schema_missing", "project_skill_context schema missing", path=str(paths.schema_path)))
    if not (paths.external_executor_root / "handoff_pack.json").is_file():
        errors.append(make_error("handoff_missing", "external_executor/handoff_pack.json is required", path="external_executor/handoff_pack.json"))
    errors.extend(validate_json_schema(schema))
    errors.extend(validate_mapping(schema=schema, mapping=mapping, template_root=paths.template_root))
    if prohibit_active_executor:
        errors.extend(_active_executor_errors(paths))
    return errors


def _active_executor_errors(paths) -> list[dict[str, Any]]:
    status_path = paths.external_executor_root / "executor_status.json"
    if not status_path.exists():
        return []
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, Mapping):
        return []
    status = str(data.get("status") or data.get("current_state") or "").strip().lower()
    if status in {"running", "experiment_running", "claimed_by_executor"}:
        return [
            make_error(
                "active_executor_suite_cannot_be_replaced",
                "external executor suite cannot be replaced while executor_status is active",
                path="external_executor/executor_status.json",
            )
        ]
    return []


def _failure_report_path(paths) -> Path:
    return paths.output_report_path.with_name("skill_specialization_failure_report.json")


def _published_report_is_usable(paths) -> bool:
    """A failed rebuild must not invalidate an already published Suite."""

    if not paths.output_report_path.is_file():
        return False
    try:
        report = json.loads(paths.output_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(report, Mapping) and report.get("status") in {"ready", "incomplete"}


def _record_failure(paths, report: dict[str, Any], *, dry_run: bool) -> Path:
    """Persist diagnostics without replacing a usable published report."""

    target = paths.output_report_path
    if _published_report_is_usable(paths):
        target = _failure_report_path(paths)
        report["previous_suite_preserved"] = True
        report["published_suite_report"] = "external_executor/skill_specialization_report.json"
    if not dry_run:
        _atomic_write_json(target, report)
    return target


def _copy_template_skills(template_root: Path, mapping: Mapping[str, Any], destination: Path) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    begin = str((mapping.get("guidance") or {}).get("begin_marker") or "")
    end = str((mapping.get("guidance") or {}).get("end_marker") or "")
    destination.mkdir(parents=True, exist_ok=True)
    for skill_name in (mapping.get("skills") or {}).keys():
        source = template_root / str(skill_name)
        target = destination / str(skill_name)
        if not source.is_dir():
            errors.append(make_error("template_skill_missing", "template skill directory missing", path=str(source), skill_name=str(skill_name)))
            continue
        symlink_error = _detect_escaping_symlink(source, template_root)
        if symlink_error:
            errors.append(make_error("staging_copy_failed", symlink_error, path=str(source), skill_name=str(skill_name)))
            continue
        skill_text = source.joinpath("SKILL.md").read_text(encoding="utf-8", errors="replace")
        marker_errors = validate_template_markers(skill_text, begin, end, skill_name=str(skill_name))
        errors.extend(marker_errors)
        if marker_errors:
            continue
        shutil.copytree(source, target)
    return errors


def _detect_escaping_symlink(source: Path, template_root: Path) -> str | None:
    for path in source.rglob("*"):
        if not path.is_symlink():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(template_root.resolve())
        except ValueError:
            return f"symlink escapes template root: {path}"
    return None


def _render_all_skills(paths, skills_staging: Path, context: Mapping[str, Any], mapping: Mapping[str, Any]) -> list[RenderedSkill]:
    rendered: list[RenderedSkill] = []
    for skill_name in (mapping.get("skills") or {}).keys():
        skill_name = str(skill_name)
        skill_path = skills_staging / skill_name / "SKILL.md"
        template_text = (paths.template_root / skill_name / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        rendered_skill = render_skill_guidance(
            skill_name=skill_name,
            template_text=template_text,
            context=context,
            specialization=mapping,
        )
        skill_path.write_text(rendered_skill.text, encoding="utf-8")
        rendered.append(rendered_skill)
    return rendered


def _report_from_rendered(paths, rendered: list[RenderedSkill]) -> dict[str, Any]:
    required_uncertain = _uncertain_records(rendered, required=True)
    optional_uncertain = _uncertain_records(rendered, required=False)
    status = "incomplete" if required_uncertain else "ready"
    report = _base_report(paths, status=status)
    report["skills_specialized"] = len(rendered)
    report["skills"] = [
        {
            "skill_name": skill.skill_name,
            "template": repo_relative(paths.template_root / skill.skill_name / "SKILL.md", paths.repo_root),
            "output": f"external_executor/skills/{skill.skill_name}/SKILL.md",
            "status": "ready" if not skill.required_uncertain_paths else "incomplete",
            "confirmed_injections": skill.confirmed_injections,
            "uncertain_injections": skill.uncertain_injections,
            "required_uncertain_paths": skill.required_uncertain_paths,
            "optional_uncertain_paths": skill.optional_uncertain_paths,
            "detail_refs": list(((read_yaml(paths.mapping_path).get("skills") or {}).get(skill.skill_name) or {}).get("detail_refs") or []),
            "template_integrity": "pass" if not skill.template_integrity_errors else "fail",
        }
        for skill in rendered
    ]
    report["required_uncertain_fields"] = required_uncertain
    report["optional_uncertain_fields"] = optional_uncertain
    return report


def _uncertain_records(rendered: list[RenderedSkill], *, required: bool) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for skill in rendered:
        paths = skill.required_uncertain_paths if required else skill.optional_uncertain_paths
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            result.append({"path": path, "skills": [item.skill_name for item in rendered if path in (item.required_uncertain_paths if required else item.optional_uncertain_paths)]})
    return result


def _source_overrides(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = context.get("field_metadata")
    if not isinstance(metadata, Mapping):
        return []
    overrides: list[dict[str, Any]] = []
    for path, item in metadata.items():
        if not isinstance(item, Mapping):
            continue
        if item.get("status") != "confirmed_from_source" or "handoff_value_ignored" not in item:
            continue
        overrides.append(
            {
                "path": str(path),
                "sources": list(item.get("sources") or []),
                "note": item.get("note"),
                "handoff_value_ignored": item.get("handoff_value_ignored"),
            }
        )
    return overrides


def _base_report(paths, *, status: str) -> dict[str, Any]:
    return {
        "schema_version": "skill_specialization_report.v1",
        "status": status,
        "specialization_method": "deterministic_project_specialization",
        "llm_specialization": {
            "enabled": False,
            "reason": "T5-SPECIALIZE-EXECUTOR-SKILLS uses an LLM to execute the repository Skill; the published Suite itself is rendered by the deterministic compiler.",
        },
        "context_file": "external_executor/project_skill_context.yaml",
        "context_schema": "external_executor/schemas/project_skill_context.schema.json",
        "template_root": "skills/external_executor_skills",
        "mapping_file": "skills/external_executor_skills/skill_specialization.yaml",
        "skills_output": "external_executor/skills",
        "skills_total": 13,
        "skills_specialized": 0,
        "skills": [],
        "required_uncertain_fields": [],
        "optional_uncertain_fields": [],
        "source_overrides": [],
        "missing_paths": [],
        "schema_errors": [],
        "mapping_errors": [],
        "render_errors": [],
        "template_integrity_errors": [],
        "generated_at": _now_iso(),
    }


def _extend_errors(report: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    for error in errors:
        code = str(error.get("code") or "")
        if code.startswith("schema"):
            report.setdefault("schema_errors", []).append(error)
        elif code.startswith("mapping") or code in {"display_field_invalid"}:
            report.setdefault("mapping_errors", []).append(error)
        elif code in {"context_path_missing", "schema_path_missing", "metadata_missing"}:
            report.setdefault("missing_paths", []).append(error)
        elif code.startswith("template"):
            report.setdefault("template_integrity_errors", []).append(error)
        else:
            report.setdefault("render_errors", []).append(error)


def _publish(paths, staging: Path) -> None:
    _atomic_write_yaml(paths.output_context_path, yaml.safe_load((staging / "project_skill_context.yaml").read_text(encoding="utf-8")))
    _atomic_write_json(paths.output_schema_path, json.loads((staging / "schemas" / "project_skill_context.schema.json").read_text(encoding="utf-8")))
    skills_backup: Path | None = None
    if paths.output_skills_path.exists():
        skills_backup = paths.output_skills_path.with_name(
            f"skills.backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        counter = 0
        while skills_backup.exists():
            counter += 1
            skills_backup = paths.output_skills_path.with_name(
                f"skills.backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{counter}"
            )
        paths.output_skills_path.rename(skills_backup)
    try:
        (staging / "skills").rename(paths.output_skills_path)
        _atomic_write_json(paths.output_report_path, json.loads((staging / "skill_specialization_report.json").read_text(encoding="utf-8")))
        if skills_backup is not None and skills_backup.exists():
            shutil.rmtree(skills_backup)
    except Exception:
        if paths.output_skills_path.exists():
            shutil.rmtree(paths.output_skills_path, ignore_errors=True)
        if skills_backup is not None and skills_backup.exists():
            skills_backup.rename(paths.output_skills_path)
        raise


def _validate_existing(paths, schema: Mapping[str, Any], mapping: Mapping[str, Any]) -> SpecializationResult:
    errors: list[dict[str, Any]] = []
    existing_report: dict[str, Any] = {}
    if not paths.output_context_path.is_file():
        errors.append(make_error("context_path_missing", "project_skill_context.yaml missing", path="external_executor/project_skill_context.yaml"))
    if not paths.output_report_path.is_file():
        errors.append(make_error("context_path_missing", "skill_specialization_report.json missing", path="external_executor/skill_specialization_report.json"))
    else:
        try:
            existing_report = json.loads(paths.output_report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(make_error("schema_invalid", f"skill_specialization_report.json is invalid JSON: {exc}", path="external_executor/skill_specialization_report.json"))
    if not paths.output_skills_path.is_dir():
        errors.append(make_error("context_path_missing", "external_executor/skills missing", path="external_executor/skills"))
    if not errors:
        context = yaml.safe_load(paths.output_context_path.read_text(encoding="utf-8")) or {}
        errors.extend(validate_context_schema(schema, context))
        errors.extend(validate_context_metadata(context, mapping))
        begin = str((mapping.get("guidance") or {}).get("begin_marker") or "")
        end = str((mapping.get("guidance") or {}).get("end_marker") or "")
        for skill_name in (mapping.get("skills") or {}).keys():
            skill_path = paths.output_skills_path / str(skill_name) / "SKILL.md"
            if not skill_path.is_file():
                errors.append(make_error("template_skill_missing", "specialized skill missing", path=workspace_relative(skill_path, paths.workspace), skill_name=str(skill_name)))
                continue
            text = skill_path.read_text(encoding="utf-8", errors="replace")
            errors.extend(validate_template_markers(text, begin, end, skill_name=str(skill_name)))
            if "## Project-Specific Guidance" not in text:
                errors.append(make_error("template_integrity_error", "specialized guidance heading missing", skill_name=str(skill_name)))
    existing_status = existing_report.get("status")
    if errors:
        status = "failed"
        report = _base_report(paths, status=status)
        report["skills_specialized"] = 0
        _extend_errors(report, errors)
    elif existing_status in {"ready", "incomplete"}:
        status = str(existing_status)
        report = existing_report
    else:
        status = "failed"
        report = _base_report(paths, status=status)
        errors.append(
            make_error(
                "schema_invalid",
                "skill_specialization_report.status must be ready or incomplete",
                path="external_executor/skill_specialization_report.json",
            )
        )
        _extend_errors(report, errors)
    return SpecializationResult(
        status=status,
        report_path=paths.output_report_path,
        context_path=paths.output_context_path,
        skills_path=paths.output_skills_path,
        required_uncertain_fields=list(report.get("required_uncertain_fields") or []),
        optional_uncertain_fields=list(report.get("optional_uncertain_fields") or []),
        errors=errors,
        report=report,
    )


def _failed(report_path: Path, errors: list[dict[str, Any]]) -> SpecializationResult:
    return SpecializationResult(status="failed", report_path=report_path, errors=errors)


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
