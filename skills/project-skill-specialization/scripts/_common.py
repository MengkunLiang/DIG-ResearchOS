#!/usr/bin/env python3
"""Shared utilities for the project-skill-specialization wrappers."""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Mapping

BEGIN_MARKER = "<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->"
END_MARKER = "<!-- PROJECT-SPECIFIC-GUIDANCE:END -->"
EXPECTED_SKILL_COUNT = 13
COMPILER_MODULE = "researchos.skills.project_specialization.compiler"
COMPILER_FUNCTION = "specialize_project_skills"
ACTIVE_EXECUTOR_STATUSES = {"running", "experiment_running", "claimed_by_executor"}

REQUIRED_CONTROL_FILES = (
    "project.yaml",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/AGENTS.md",
    "external_executor/allowed_paths.txt",
)

OPTIONAL_SOURCE_FILES = (
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "novelty/novelty_audit.md",
    "literature/synthesis.md",
    "ideation/idea_scorecard.yaml",
    "ideation/risks.md",
)


def emit_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def find_repo_root(start: Path) -> Path | None:
    """Find the nearest ResearchOS root from start or its parents."""
    start = start.expanduser().resolve()
    candidates = (start, *start.parents)
    for candidate in candidates:
        if (
            (candidate / "researchos").is_dir()
            and (
                candidate
                / "skills/external_executor_skills/skill_specialization.yaml"
            ).is_file()
        ):
            return candidate
    return None


def resolve_repo_root(explicit: str | None, workspace: Path) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        root = find_repo_root(Path.cwd()) or find_repo_root(workspace)
    if root is None:
        raise ValueError(
            "Could not locate a ResearchOS repository root containing researchos/ "
            "and skills/external_executor_skills/skill_specialization.yaml"
        )
    return root


def resolve_workspace(raw: str) -> Path:
    workspace = Path(raw).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace directory does not exist: {workspace}")
    return workspace


def workspace_relative(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "PyYAML is required to inspect skill_specialization.yaml"
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_executor_status(workspace: Path) -> str | None:
    path = workspace / "external_executor/executor_status.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    for key in ("executor_status", "status", "current_state"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def compiler_available(repo_root: Path) -> tuple[bool, str | None]:
    """Check the public compiler module without importing it permanently."""
    root_text = str(repo_root)
    added = root_text not in os.sys.path
    if added:
        os.sys.path.insert(0, root_text)
    try:
        try:
            spec = importlib.util.find_spec(COMPILER_MODULE)
        except (ImportError, ModuleNotFoundError, AttributeError) as exc:
            return False, str(exc)
        if spec is None:
            return False, f"Module not found: {COMPILER_MODULE}"
        return True, None
    finally:
        if added:
            try:
                os.sys.path.remove(root_text)
            except ValueError:
                pass


def load_compiler(repo_root: Path):
    root_text = str(repo_root)
    if root_text not in os.sys.path:
        os.sys.path.insert(0, root_text)
    module = importlib.import_module(COMPILER_MODULE)
    function = getattr(module, COMPILER_FUNCTION, None)
    if function is None or not callable(function):
        raise RuntimeError(
            f"{COMPILER_MODULE} does not expose callable {COMPILER_FUNCTION}"
        )
    return function


def run_preflight(
    workspace: Path, repo_root: Path, *, mode: str = "build"
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    template_root = repo_root / "skills/external_executor_skills"
    mapping_path = template_root / "skill_specialization.yaml"
    schema_path = template_root / "schemas/project_skill_context.schema.json"

    if not (repo_root / "researchos").is_dir():
        errors.append(
            {
                "code": "repo_root_invalid",
                "path": str(repo_root),
                "message": "researchos/ is missing",
            }
        )
    if not mapping_path.is_file():
        errors.append(
            {
                "code": "mapping_missing",
                "path": str(mapping_path),
                "message": "skill_specialization.yaml is missing",
            }
        )
    if not schema_path.is_file():
        errors.append(
            {
                "code": "schema_missing",
                "path": str(schema_path),
                "message": "project_skill_context.schema.json is missing",
            }
        )

    for relative in REQUIRED_CONTROL_FILES:
        path = workspace / relative
        if not path.is_file():
            errors.append(
                {
                    "code": "required_input_missing",
                    "path": relative,
                    "message": "Required project/control artifact is missing",
                }
            )

    for relative in OPTIONAL_SOURCE_FILES:
        path = workspace / relative
        if not path.is_file():
            warnings.append(
                {
                    "code": "optional_source_missing",
                    "path": relative,
                    "message": "Missing source may produce uncertain Context fields",
                }
            )

    skill_names: list[str] = []
    if mapping_path.is_file():
        try:
            mapping = _load_yaml(mapping_path)
            raw_skills = mapping.get("skills") if isinstance(mapping, dict) else None
            if not isinstance(raw_skills, dict):
                raise ValueError("Top-level skills mapping is missing or not an object")
            skill_names = list(raw_skills.keys())
            if len(skill_names) != EXPECTED_SKILL_COUNT:
                errors.append(
                    {
                        "code": "skill_count_mismatch",
                        "path": str(mapping_path),
                        "message": (
                            f"Expected {EXPECTED_SKILL_COUNT} mapped Skills, "
                            f"found {len(skill_names)}"
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - preserve parser error
            errors.append(
                {
                    "code": "mapping_parse_error",
                    "path": str(mapping_path),
                    "message": str(exc),
                }
            )

    for skill_name in skill_names:
        skill_dir = template_root / skill_name
        skill_md = skill_dir / "SKILL.md"
        if not skill_dir.is_dir():
            errors.append(
                {
                    "code": "template_skill_missing",
                    "skill_name": skill_name,
                    "path": str(skill_dir),
                    "message": "Template Skill directory is missing",
                }
            )
            continue
        if not skill_md.is_file():
            errors.append(
                {
                    "code": "template_skill_md_missing",
                    "skill_name": skill_name,
                    "path": str(skill_md),
                    "message": "Template SKILL.md is missing",
                }
            )
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(
                {
                    "code": "template_skill_unreadable",
                    "skill_name": skill_name,
                    "path": str(skill_md),
                    "message": str(exc),
                }
            )
            continue
        if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
            errors.append(
                {
                    "code": "template_marker_invalid",
                    "skill_name": skill_name,
                    "path": str(skill_md),
                    "message": "Expected exactly one begin marker and one end marker",
                }
            )
        elif text.index(BEGIN_MARKER) > text.index(END_MARKER):
            errors.append(
                {
                    "code": "template_marker_order_invalid",
                    "skill_name": skill_name,
                    "path": str(skill_md),
                    "message": "Guidance begin marker appears after end marker",
                }
            )

    executor_status = _read_executor_status(workspace)
    if executor_status and executor_status.strip().lower() in ACTIVE_EXECUTOR_STATUSES and mode == "build":
        errors.append(
            {
                "code": "active_executor_suite_cannot_be_replaced",
                "path": "external_executor/executor_status.json",
                "message": "Executor status is active; a build cannot replace the live Skill Suite",
            }
        )
    elif executor_status == "unreadable":
        errors.append(
            {
                "code": "executor_status_unreadable",
                "path": "external_executor/executor_status.json",
                "message": "Existing executor status cannot be parsed safely",
            }
        )

    available, compiler_error = compiler_available(repo_root)
    if not available:
        errors.append(
            {
                "code": "specializer_service_missing",
                "path": COMPILER_MODULE,
                "message": compiler_error or "Compiler service is unavailable",
            }
        )

    status = "fail" if errors else ("warning" if warnings else "pass")
    return {
        "schema_version": "project_skill_specialization_preflight.v1",
        "mode": mode,
        "status": status,
        "workspace": str(workspace),
        "repo_root": str(repo_root),
        "template_root": str(template_root),
        "mapping": str(mapping_path),
        "schema": str(schema_path),
        "skills_expected": EXPECTED_SKILL_COUNT,
        "skills_found": len(skill_names),
        "compiler_module": COMPILER_MODULE,
        "errors": errors,
        "warnings": warnings,
    }


def normalize_result(result: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        data = dataclasses.asdict(result)
    elif isinstance(result, Mapping):
        data = dict(result)
    elif hasattr(result, "__dict__"):
        data = {
            key: value
            for key, value in vars(result).items()
            if not key.startswith("_")
        }
    else:
        data = {"result": str(result)}

    def convert(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return convert(dataclasses.asdict(value))
        if isinstance(value, Mapping):
            return {str(key): convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [convert(item) for item in value]
        return value

    return convert(data)


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Specialization report root must be an object")
    return payload
