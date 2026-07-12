from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator


ALLOWED_RENDER_TYPES = {"scalar", "list", "object_list", "mapping", "auto"}


def make_error(
    code: str,
    message: str,
    *,
    path: str | None = None,
    skill_name: str | None = None,
    field_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if path:
        payload["path"] = path
    if skill_name:
        payload["skill_name"] = skill_name
    if field_path:
        payload["field_path"] = field_path
    return payload


def resolve_ref(schema: Mapping[str, Any], node: Mapping[str, Any]) -> Mapping[str, Any]:
    current: Mapping[str, Any] = node
    seen: set[str] = set()
    while "$ref" in current:
        ref = str(current["$ref"])
        if ref in seen:
            raise ValueError(f"cyclic schema $ref: {ref}")
        seen.add(ref)
        if not ref.startswith("#/"):
            raise ValueError(f"unsupported schema $ref: {ref}")
        parts = ref.removeprefix("#/").split("/")
        target: Any = schema
        for part in parts:
            if not isinstance(target, Mapping) or part not in target:
                raise KeyError(ref)
            target = target[part]
        if not isinstance(target, Mapping):
            raise TypeError(f"schema $ref target is not an object: {ref}")
        current = target
    return current


def schema_type(node: Mapping[str, Any]) -> set[str]:
    raw = node.get("type")
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, str):
        return {raw}
    if "const" in node:
        value = node["const"]
        if isinstance(value, str):
            return {"string"}
        if isinstance(value, bool):
            return {"boolean"}
        if isinstance(value, int):
            return {"integer"}
        if isinstance(value, float):
            return {"number"}
    return set()


def resolve_schema_node(schema: Mapping[str, Any], dotted_path: str) -> Mapping[str, Any]:
    node: Mapping[str, Any] = schema
    if not dotted_path:
        return node
    for part in dotted_path.split("."):
        node = resolve_ref(schema, node)
        types = schema_type(node)
        if "array" in types:
            items = node.get("items")
            if not isinstance(items, Mapping):
                raise KeyError(dotted_path)
            node = resolve_ref(schema, items)
            types = schema_type(node)
        properties = node.get("properties")
        if not isinstance(properties, Mapping) or part not in properties:
            raise KeyError(dotted_path)
        child = properties[part]
        if not isinstance(child, Mapping):
            raise KeyError(dotted_path)
        node = child
    return resolve_ref(schema, node)


def get_by_dotted_path(data: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def set_by_dotted_path(data: dict[str, Any], dotted_path: str, value: Any) -> None:
    current: dict[str, Any] = data
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def iter_injection_items(mapping: Mapping[str, Any]) -> Iterable[tuple[str, str, Mapping[str, Any]]]:
    skills = mapping.get("skills")
    if not isinstance(skills, Mapping):
        return
    for skill_name, skill_cfg in skills.items():
        if not isinstance(skill_cfg, Mapping):
            continue
        inject = skill_cfg.get("inject")
        if not isinstance(inject, Mapping):
            continue
        for section_id, items in inject.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, Mapping):
                    yield str(skill_name), str(section_id), item


def injected_paths(mapping: Mapping[str, Any]) -> list[str]:
    seen: list[str] = []
    for _skill, _section, item in iter_injection_items(mapping):
        path = str(item.get("path") or "")
        if path and path not in seen:
            seen.append(path)
    return seen


def validate_json_schema(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        return [make_error("schema_invalid", str(exc))]
    return []


def validate_context_schema(schema: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(context), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.path)
        errors.append(make_error("schema_invalid", error.message, field_path=path or None))
    return errors


def validate_mapping(
    *,
    schema: Mapping[str, Any],
    mapping: Mapping[str, Any],
    template_root: Path | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if mapping.get("schema_version") != "skill_specialization.v1":
        errors.append(make_error("mapping_invalid", "schema_version must be skill_specialization.v1"))
    guidance = mapping.get("guidance")
    if not isinstance(guidance, Mapping):
        errors.append(make_error("mapping_invalid", "guidance must be an object"))
        return errors
    begin = str(guidance.get("begin_marker") or "")
    end = str(guidance.get("end_marker") or "")
    if not begin or not end or begin == end:
        errors.append(make_error("mapping_invalid", "guidance markers must be non-empty and distinct"))
    sections = guidance.get("sections")
    section_ids: set[str] = set()
    if not isinstance(sections, list) or not sections:
        errors.append(make_error("mapping_invalid", "guidance.sections must be a non-empty list"))
    else:
        for section in sections:
            if not isinstance(section, Mapping) or not section.get("id") or not section.get("title"):
                errors.append(make_error("mapping_invalid", "each guidance section needs id and title"))
                continue
            section_id = str(section["id"])
            if section_id in section_ids:
                errors.append(make_error("mapping_invalid", f"duplicate guidance section: {section_id}"))
            section_ids.add(section_id)

    skills = mapping.get("skills")
    if not isinstance(skills, Mapping):
        errors.append(make_error("mapping_invalid", "skills must be an object"))
        return errors
    if len(skills) != 13:
        errors.append(make_error("mapping_invalid", "skills must contain exactly 13 skill entries"))
    for skill_name, skill_cfg in skills.items():
        skill_name = str(skill_name)
        if template_root is not None:
            skill_file = template_root / skill_name / "SKILL.md"
            if not skill_file.is_file():
                errors.append(
                    make_error(
                        "template_skill_missing",
                        f"missing template skill {skill_name}",
                        path=str(skill_file),
                        skill_name=skill_name,
                    )
                )
        if not isinstance(skill_cfg, Mapping):
            errors.append(make_error("mapping_invalid", "skill config must be an object", skill_name=skill_name))
            continue
        inject = skill_cfg.get("inject")
        if not isinstance(inject, Mapping):
            errors.append(make_error("mapping_invalid", "skill inject must be an object", skill_name=skill_name))
            continue
        for section_id, items in inject.items():
            if str(section_id) not in section_ids:
                errors.append(
                    make_error(
                        "mapping_invalid",
                        f"inject section is not declared in guidance.sections: {section_id}",
                        skill_name=skill_name,
                    )
                )
            if not isinstance(items, list):
                errors.append(
                    make_error("mapping_invalid", "inject section must be a list", skill_name=skill_name)
                )
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    errors.append(make_error("mapping_invalid", "inject item must be an object", skill_name=skill_name))
                    continue
                path = str(item.get("path") or "")
                render = str(item.get("render") or "")
                if not path or not item.get("label") or render not in ALLOWED_RENDER_TYPES or "required" not in item:
                    errors.append(
                        make_error(
                            "mapping_invalid",
                            "inject item must contain path, label, render, and required",
                            skill_name=skill_name,
                            field_path=path or None,
                        )
                    )
                if path:
                    try:
                        resolve_schema_node(schema, path)
                    except Exception:
                        errors.append(
                            make_error(
                                "schema_path_missing",
                                "inject path is not present in context schema",
                                skill_name=skill_name,
                                field_path=path,
                            )
                        )
                if "max_items" in item and (not isinstance(item["max_items"], int) or item["max_items"] <= 0):
                    errors.append(
                        make_error("mapping_invalid", "max_items must be a positive integer", skill_name=skill_name, field_path=path)
                    )
                if "display_fields" in item and render != "object_list":
                    errors.append(
                        make_error(
                            "display_field_invalid",
                            "display_fields is only allowed for object_list render",
                            skill_name=skill_name,
                            field_path=path,
                        )
                    )
        detail_refs = skill_cfg.get("detail_refs") or []
        if not isinstance(detail_refs, list):
            errors.append(make_error("mapping_invalid", "detail_refs must be a list", skill_name=skill_name))
            continue
        for ref in detail_refs:
            try:
                resolve_schema_node(schema, str(ref))
            except Exception:
                errors.append(
                    make_error(
                        "schema_path_missing",
                        "detail_ref is not present in context schema",
                        skill_name=skill_name,
                        field_path=str(ref),
                    )
                )
    return errors


def validate_template_markers(text: str, begin_marker: str, end_marker: str, *, skill_name: str) -> list[dict[str, Any]]:
    begin_count = text.count(begin_marker)
    end_count = text.count(end_marker)
    if begin_count == 0 or end_count == 0:
        return [make_error("template_marker_missing", "template guidance marker missing", skill_name=skill_name)]
    if begin_count != 1 or end_count != 1:
        return [
            make_error(
                "template_marker_duplicate",
                "template guidance markers must each appear exactly once",
                skill_name=skill_name,
            )
        ]
    if text.index(begin_marker) > text.index(end_marker):
        return [make_error("template_marker_missing", "begin marker must appear before end marker", skill_name=skill_name)]
    return []


def strip_guidance_region(text: str, begin_marker: str, end_marker: str) -> str:
    start = text.index(begin_marker) + len(begin_marker)
    end = text.index(end_marker)
    return text[:start] + "\n__PROJECT_GUIDANCE_PLACEHOLDER__\n" + text[end:]


def validate_template_integrity(
    *,
    template_text: str,
    rendered_text: str,
    begin_marker: str,
    end_marker: str,
    skill_name: str,
) -> list[dict[str, Any]]:
    try:
        template_canonical = strip_guidance_region(template_text.replace("\r\n", "\n"), begin_marker, end_marker)
        rendered_canonical = strip_guidance_region(rendered_text.replace("\r\n", "\n"), begin_marker, end_marker)
    except Exception as exc:
        return [
            make_error(
                "template_integrity_error",
                f"could not compare template integrity: {exc}",
                skill_name=skill_name,
            )
        ]
    if template_canonical != rendered_canonical:
        return [make_error("template_integrity_error", "marker-external template text changed", skill_name=skill_name)]
    return []


def validate_context_metadata(context: Mapping[str, Any], mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    metadata = context.get("field_metadata")
    if not isinstance(metadata, Mapping):
        return [make_error("metadata_missing", "field_metadata must be an object")]
    for path in injected_paths(mapping):
        try:
            value = get_by_dotted_path(context, path)
        except KeyError:
            errors.append(make_error("context_path_missing", "inject path missing in context", field_path=path))
            continue
        item = metadata.get(path)
        if not isinstance(item, Mapping):
            errors.append(make_error("metadata_missing", "inject path has no field_metadata entry", field_path=path))
            continue
        status = item.get("status")
        if status not in {"confirmed", "confirmed_from_source", "uncertain"}:
            errors.append(make_error("metadata_invalid", "metadata status is invalid", field_path=path))
        if status == "uncertain" and not str(item.get("note") or "").strip():
            errors.append(make_error("metadata_invalid", "uncertain metadata requires a note", field_path=path))
        if status == "confirmed" and is_empty_value(value):
            errors.append(make_error("metadata_invalid", "confirmed metadata cannot point to an empty value", field_path=path))
        if status == "confirmed_from_source" and not item.get("sources"):
            errors.append(
                make_error("metadata_invalid", "confirmed_from_source metadata requires sources", field_path=path)
            )
    return errors


def copy_jsonable(value: Any) -> Any:
    return copy.deepcopy(value)
