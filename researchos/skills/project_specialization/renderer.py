from __future__ import annotations

import json
from typing import Any, Mapping

from .types import RenderedSkill
from .validation import (
    get_by_dotted_path,
    is_empty_value,
    make_error,
    validate_template_integrity,
    validate_template_markers,
)


def render_skill_guidance(
    *,
    skill_name: str,
    template_text: str,
    context: Mapping[str, Any],
    specialization: Mapping[str, Any],
) -> RenderedSkill:
    guidance = specialization.get("guidance") or {}
    begin_marker = str(guidance.get("begin_marker") or "")
    end_marker = str(guidance.get("end_marker") or "")
    marker_errors = validate_template_markers(template_text, begin_marker, end_marker, skill_name=skill_name)
    if marker_errors:
        return RenderedSkill(skill_name, template_text, 0, 0, [], [], marker_errors, marker_errors)

    skill_cfg = (specialization.get("skills") or {}).get(skill_name) or {}
    metadata = context.get("field_metadata") if isinstance(context.get("field_metadata"), Mapping) else {}
    rendering = specialization.get("rendering") if isinstance(specialization.get("rendering"), Mapping) else {}
    confirmed_statuses = set(rendering.get("confirmed_statuses") or ["confirmed", "confirmed_from_source"])
    uncertain_status = str(rendering.get("uncertain_status") or "uncertain")
    max_default = int(rendering.get("max_items_default") or 8)

    lines: list[str] = [
        str(guidance.get("heading") or "## Project-Specific Guidance"),
        "",
        "> Generated from `external_executor/project_skill_context.yaml`.",
        "> This section adds project facts without changing this Skill's generic responsibilities or ownership.",
        "",
    ]
    render_errors: list[dict[str, Any]] = []
    confirmed_count = 0
    uncertain_entries: list[dict[str, Any]] = []
    required_uncertain_paths: list[str] = []
    optional_uncertain_paths: list[str] = []

    section_titles = {
        str(section.get("id")): str(section.get("title"))
        for section in guidance.get("sections", [])
        if isinstance(section, Mapping)
    }
    inject = skill_cfg.get("inject") if isinstance(skill_cfg, Mapping) else {}
    if not isinstance(inject, Mapping):
        inject = {}

    for section_id in ("project_focus", "project_priorities", "hard_constraints", "decision_criteria"):
        section_lines: list[str] = []
        for item in inject.get(section_id, []) or []:
            if not isinstance(item, Mapping):
                continue
            path = str(item.get("path") or "")
            required = bool(item.get("required"))
            label = str(item.get("label") or path)
            try:
                value = get_by_dotted_path(context, path)
            except KeyError:
                render_errors.append(
                    make_error(
                        "context_path_missing",
                        "inject path missing in context",
                        skill_name=skill_name,
                        field_path=path,
                    )
                )
                continue
            meta = metadata.get(path) if isinstance(metadata, Mapping) else None
            if not isinstance(meta, Mapping):
                render_errors.append(
                    make_error(
                        "metadata_missing",
                        "inject path has no field_metadata entry",
                        skill_name=skill_name,
                        field_path=path,
                    )
                )
                continue
            status = str(meta.get("status") or "")
            if status == uncertain_status or is_empty_value(value):
                entry = {
                    "path": path,
                    "label": label,
                    "required": required,
                    "note": str(meta.get("note") or "The field is not resolved from current project sources."),
                    "sources": list(meta.get("sources") or []),
                }
                uncertain_entries.append(entry)
                if required:
                    required_uncertain_paths.append(path)
                else:
                    optional_uncertain_paths.append(path)
                continue
            if status not in confirmed_statuses:
                render_errors.append(
                    make_error(
                        "metadata_invalid",
                        f"unsupported metadata status: {status}",
                        skill_name=skill_name,
                        field_path=path,
                    )
                )
                continue
            try:
                section_lines.extend(_render_confirmed_item(item, value, max_default=max_default))
            except Exception as exc:
                render_errors.append(
                    make_error(
                        "render_type_mismatch",
                        str(exc),
                        skill_name=skill_name,
                        field_path=path,
                    )
                )
                continue
            confirmed_count += 1
        if section_lines:
            lines.append(section_titles.get(section_id, f"### {section_id.replace('_', ' ').title()}"))
            lines.extend(section_lines)
            lines.append("")

    detail_refs = list(skill_cfg.get("detail_refs") or []) if isinstance(skill_cfg, Mapping) else []
    lines.append(section_titles.get("detailed_context", "### Detailed project context"))
    reference_format = str(
        rendering.get("detail_reference_format")
        or "`<workspace>/external_executor/project_skill_context.yaml#{path}`"
    )
    for ref in detail_refs:
        lines.append(f"- {reference_format.format(path=ref)}")
    lines.append("")

    if uncertain_entries:
        lines.append(section_titles.get("uncertain_fields", "### Uncertain project fields"))
        for entry in uncertain_entries:
            lines.append(f"- **{entry['label']}** (`{entry['path']}`): {entry['note']}")
            sources = entry["sources"]
            if sources:
                formatted = ", ".join(f"`{source}`" for source in sources)
                lines.append(f"  Sources: {formatted}.")
            else:
                lines.append("  Sources: none available.")
        lines.append("")

    runtime_note = str(
        rendering.get("runtime_note")
        or "Current iteration state and execution evidence remain authoritative in runtime artifacts."
    )
    lines.append(runtime_note)
    guidance_text = "\n".join(lines).rstrip() + "\n"
    rendered_text = _replace_marked_region(template_text, begin_marker, end_marker, guidance_text)
    integrity_errors = validate_template_integrity(
        template_text=template_text,
        rendered_text=rendered_text,
        begin_marker=begin_marker,
        end_marker=end_marker,
        skill_name=skill_name,
    )
    return RenderedSkill(
        skill_name=skill_name,
        text=rendered_text,
        confirmed_injections=confirmed_count,
        uncertain_injections=len(uncertain_entries),
        required_uncertain_paths=_dedupe(required_uncertain_paths),
        optional_uncertain_paths=_dedupe(optional_uncertain_paths),
        render_errors=render_errors,
        template_integrity_errors=integrity_errors,
    )


def _replace_marked_region(text: str, begin_marker: str, end_marker: str, replacement: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    start = normalized.index(begin_marker) + len(begin_marker)
    end = normalized.index(end_marker)
    rendered = normalized[:start] + "\n" + replacement + normalized[end:]
    return rendered.rstrip() + "\n"


def _render_confirmed_item(item: Mapping[str, Any], value: Any, *, max_default: int) -> list[str]:
    render_type = str(item.get("render") or "auto")
    label = str(item.get("label") or item.get("path") or "Value")
    max_items = int(item.get("max_items") or max_default)
    if render_type == "auto":
        if isinstance(value, list):
            render_type = "object_list" if any(isinstance(entry, Mapping) for entry in value) else "list"
        elif isinstance(value, Mapping):
            render_type = "mapping"
        else:
            render_type = "scalar"
    if render_type == "scalar":
        if isinstance(value, (list, dict)):
            raise TypeError("scalar render received list or mapping")
        return [f"- **{label}:** {_format_scalar(value)}"]
    if render_type == "list":
        if not isinstance(value, list):
            raise TypeError("list render received non-list value")
        return _render_list(label, value, max_items=max_items)
    if render_type == "object_list":
        if not isinstance(value, list):
            raise TypeError("object_list render received non-list value")
        fields = [str(field) for field in item.get("display_fields") or []]
        return _render_object_list(label, value, fields, max_items=max_items)
    if render_type == "mapping":
        if not isinstance(value, Mapping):
            raise TypeError("mapping render received non-mapping value")
        return _render_mapping(label, value)
    raise TypeError(f"unsupported render type: {render_type}")


def _render_list(label: str, values: list[Any], *, max_items: int) -> list[str]:
    lines = [f"- **{label}:**"]
    shown = values[:max_items]
    for value in shown:
        lines.append(f"  - {_format_compact(value)}")
    if len(values) > max_items:
        lines.append(f"  - ... {len(values) - max_items} additional item(s); see Detailed project context.")
    return lines


def _render_object_list(label: str, values: list[Any], fields: list[str], *, max_items: int) -> list[str]:
    lines = [f"- **{label}:**"]
    for value in values[:max_items]:
        if not isinstance(value, Mapping):
            lines.append(f"  - {_format_compact(value)}")
            continue
        parts: list[str] = []
        for field in fields:
            field_value = value.get(field)
            if is_empty_value(field_value):
                continue
            text = _format_scalar(field_value)
            if not parts and ("id" in field.lower() or field.lower().endswith("_id")):
                text = f"`{text}`"
            parts.append(text)
        lines.append("  - " + (" - ".join(parts) if parts else _format_compact(value)))
    if len(values) > max_items:
        lines.append(f"  - ... {len(values) - max_items} additional item(s); see Detailed project context.")
    return lines


def _render_mapping(label: str, value: Mapping[str, Any]) -> list[str]:
    lines = [f"- **{label}:**"]
    for key in sorted(value):
        lines.append(f"  - `{key}`: {_format_compact(value[key])}")
    return lines


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return " ".join(str(value).split())


def _format_compact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _format_scalar(value)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
