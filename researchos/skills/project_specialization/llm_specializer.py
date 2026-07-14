from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import yaml

from .compiler import _atomic_write_json, _record_failure, specialize_project_skills
from .paths import build_specialization_paths
from .types import SpecializationResult
from .validation import make_error


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def specialize_project_skills_with_llm(
    *,
    workspace: Path,
    llm_client: Any,
    repo_root: Path | None = None,
    dry_run: bool = False,
    validate_only: bool = False,
    profile: str | None = None,
    tier: str = "medium",
) -> SpecializationResult:
    """Generate project-specific executor skills with an LLM-assisted guidance pass.

    The deterministic compiler still owns schema validation, context extraction,
    template copying, marker integrity, and atomic publication.  This wrapper
    then calls the configured LLM once to specialize the published skill suite
    from the root templates plus the project context.
    """

    paths = build_specialization_paths(workspace=workspace, repo_root=repo_root)
    base = specialize_project_skills(
        workspace=workspace,
        repo_root=repo_root,
        dry_run=dry_run,
        validate_only=validate_only,
    )
    if base.status == "failed" or dry_run:
        return base
    if validate_only:
        return _validate_llm_specialization(base)

    skill_names = _skill_names(base.report, paths.output_skills_path)
    try:
        response = await llm_client.chat(
            messages=_build_llm_messages(paths, skill_names),
            tools=None,
            temperature=0.2,
            tier=tier,
            profile=profile,
            timeout=180,
            max_retries_per_model=2,
            retry_base_delay=2.0,
        )
        payload = _parse_json_object(_message_content(response))
        addenda = _normalize_skill_addenda(payload, skill_names)
        if len(addenda) != len(skill_names):
            missing = sorted(set(skill_names) - set(addenda))
            raise ValueError("LLM response missing skill specializations: " + ", ".join(missing))
        report = dict(base.report or {})
        report["specialization_method"] = "llm_assisted_project_specialization"
        report["llm_specialization"] = {
            "enabled": True,
            "source": "researchos_llm",
            "profile": profile,
            "tier": tier,
            "model": getattr(response, "model_used", ""),
            "endpoint": getattr(response, "endpoint_used", ""),
            "tokens_in": int(getattr(response, "tokens_in", 0) or 0),
            "tokens_out": int(getattr(response, "tokens_out", 0) or 0),
            "cost_usd": float(getattr(response, "cost_usd", 0.0) or 0.0),
            "skills_specialized": len(addenda),
            "generated_at": _now_iso(),
        }
        for item in report.get("skills", []) or []:
            if isinstance(item, dict) and str(item.get("skill_name")) in addenda:
                item["llm_specialization_status"] = "ready"
        staging_root = paths.external_executor_root / f".llm_skill_specialization_staging-{uuid4().hex}"
        staged_skills = staging_root / "skills"
        try:
            shutil.copytree(paths.output_skills_path, staged_skills)
            _apply_llm_addenda(staged_skills, addenda)
            _publish_llm_specialization(paths, staged_skills, report)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
        return SpecializationResult(
            status=report.get("status", base.status),
            report_path=paths.output_report_path,
            context_path=paths.output_context_path,
            skills_path=paths.output_skills_path,
            required_uncertain_fields=list(report.get("required_uncertain_fields") or []),
            optional_uncertain_fields=list(report.get("optional_uncertain_fields") or []),
            errors=[],
            report=report,
        )
    except Exception as exc:
        errors = [make_error("llm_skill_specialization_failed", str(exc))]
        report = dict(base.report or {})
        report["status"] = "failed"
        report["llm_specialization"] = {
            "enabled": True,
            "source": "researchos_llm",
            "profile": profile,
            "tier": tier,
            "status": "failed",
            "error": str(exc),
            "generated_at": _now_iso(),
        }
        report.setdefault("render_errors", []).extend(errors)
        try:
            failure_report_path = _record_failure(paths, report, dry_run=False)
        except Exception:
            failure_report_path = paths.output_report_path.with_name(
                "skill_specialization_failure_report.json"
            )
        return SpecializationResult(
            status="failed",
            report_path=failure_report_path,
            context_path=paths.output_context_path,
            skills_path=paths.output_skills_path,
            errors=errors,
            report=report,
        )


def _validate_llm_specialization(base: SpecializationResult) -> SpecializationResult:
    report = dict(base.report or {})
    llm = report.get("llm_specialization")
    if isinstance(llm, Mapping) and llm.get("enabled") is True and llm.get("skills_specialized"):
        return base
    errors = [
        make_error(
            "llm_specialization_missing",
            "skill_specialization_report.json does not record an LLM-assisted specialization pass",
            path="external_executor/skill_specialization_report.json",
        )
    ]
    report["status"] = "failed"
    report.setdefault("render_errors", []).extend(errors)
    return SpecializationResult(
        status="failed",
        report_path=base.report_path,
        context_path=base.context_path,
        skills_path=base.skills_path,
        errors=errors,
        report=report,
    )


def _build_llm_messages(paths, skill_names: list[str]) -> list[dict[str, str]]:
    context_text = _read_limited(paths.output_context_path, 24000)
    handoff_text = _read_limited(paths.external_executor_root / "handoff_pack.json", 14000)
    schema_text = _read_limited(paths.external_executor_root / "expected_outputs_schema.json", 10000)
    templates = "\n\n".join(_skill_template_excerpt(paths.template_root, name) for name in skill_names)
    user = (
        "Specialize the root external executor skill suite for this ResearchOS project.\n"
        "Use only the supplied project context, handoff pack, expected output schema, and skill template excerpts. "
        "Do not invent datasets, baselines, metrics, results, or claims.\n\n"
        "Return exactly one JSON object with this shape:\n"
        '{"skills":{"<skill-name>":{"focus":"...","priorities":["..."],'
        '"constraints":["..."],"completion_criteria":["..."],"uncertainty_handling":["..."]}}}\n'
        "Every listed skill must appear exactly once.\n\n"
        f"Skill names: {', '.join(skill_names)}\n\n"
        "PROJECT_CONTEXT_YAML:\n"
        f"{context_text}\n\n"
        "HANDOFF_PACK_JSON:\n"
        f"{handoff_text}\n\n"
        "EXPECTED_OUTPUTS_SCHEMA_JSON:\n"
        f"{schema_text}\n\n"
        "ROOT_SKILL_TEMPLATE_EXCERPTS:\n"
        f"{templates}\n"
    )
    return [
        {
            "role": "system",
            "content": (
                "You specialize ResearchOS external executor skills. "
                "You write concise, project-specific operational guidance as strict JSON only."
            ),
        },
        {"role": "user", "content": user},
    ]


def _skill_names(report: Mapping[str, Any], skills_path: Path) -> list[str]:
    names = [
        str(item.get("skill_name"))
        for item in report.get("skills", []) or []
        if isinstance(item, Mapping) and item.get("skill_name")
    ]
    if names:
        return names
    return sorted(path.parent.name for path in skills_path.glob("*/SKILL.md"))


def _read_limited(path: Path, limit: int) -> str:
    if not path.is_file():
        return f"[missing: {path.name}]"
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] + (f"\n[truncated from {len(text)} chars]" if len(text) > limit else "")


def _skill_template_excerpt(template_root: Path, skill_name: str) -> str:
    path = template_root / skill_name / "SKILL.md"
    text = _read_limited(path, 2400)
    return f"--- skill: {skill_name} root_template: {path.as_posix()} ---\n{text}"


def _message_content(response: Any) -> str:
    raw = getattr(response, "raw", None)
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return str(getattr(message, "content", "") or "")


def _parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE).strip()
    start = value.find("{")
    if start < 0:
        raise ValueError("LLM response did not contain a JSON object")
    decoded, _ = json.JSONDecoder().raw_decode(value[start:])
    if not isinstance(decoded, dict):
        raise ValueError("LLM response JSON root must be an object")
    return decoded


def _normalize_skill_addenda(payload: Mapping[str, Any], skill_names: list[str]) -> dict[str, dict[str, Any]]:
    raw_skills = payload.get("skills")
    if not isinstance(raw_skills, Mapping):
        raise ValueError("LLM response missing skills object")
    normalized: dict[str, dict[str, Any]] = {}
    for skill_name in skill_names:
        raw = raw_skills.get(skill_name)
        if not isinstance(raw, Mapping):
            continue
        normalized[skill_name] = {
            "focus": _string(raw.get("focus")),
            "priorities": _string_list(raw.get("priorities")),
            "constraints": _string_list(raw.get("constraints")),
            "completion_criteria": _string_list(raw.get("completion_criteria")),
            "uncertainty_handling": _string_list(raw.get("uncertainty_handling")),
        }
    return normalized


def _apply_llm_addenda(skills_path: Path, addenda: Mapping[str, Mapping[str, Any]]) -> None:
    for skill_name, data in addenda.items():
        skill_path = skills_path / skill_name / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8", errors="replace")
        marker = "<!-- PROJECT-SPECIFIC-GUIDANCE:END -->"
        if marker not in text:
            raise ValueError(f"{skill_name} missing project-specific guidance end marker")
        addendum = _render_addendum(skill_name, data)
        updated = text.replace(marker, addendum + "\n" + marker, 1)
        skill_path.write_text(updated, encoding="utf-8")


def _publish_llm_specialization(paths, staged_skills: Path, report: Mapping[str, Any]) -> None:
    """Publish complete LLM guidance or keep the deterministic Suite intact."""

    live_skills = paths.output_skills_path
    backup = live_skills.with_name(
        f"skills.llm-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    live_skills.rename(backup)
    try:
        staged_skills.rename(live_skills)
        _atomic_write_json(paths.output_report_path, dict(report))
    except Exception:
        if live_skills.exists():
            shutil.rmtree(live_skills, ignore_errors=True)
        backup.rename(live_skills)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)


def _render_addendum(skill_name: str, data: Mapping[str, Any]) -> str:
    lines = [
        "### LLM project specialization",
        (
            f"> Generated by ResearchOS LLM from root `skills/external_executor_skills/{skill_name}/SKILL.md` "
            "and the current project handoff/context artifacts."
        ),
        f"- **Focus:** {_string(data.get('focus'))}",
    ]
    for label, key in (
        ("Priorities", "priorities"),
        ("Constraints", "constraints"),
        ("Completion criteria", "completion_criteria"),
        ("Uncertainty handling", "uncertainty_handling"),
    ):
        values = _string_list(data.get(key))
        if not values:
            continue
        lines.append(f"- **{label}:**")
        lines.extend(f"  - {value}" for value in values)
    return "\n".join(lines).rstrip() + "\n"


def _string(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip()
    return " ".join(str(value or "").split()) or "Use the confirmed project context and stop if it is insufficient."


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_string(item) for item in value if _string(item)]
    if value in (None, ""):
        return []
    return [_string(value)]
