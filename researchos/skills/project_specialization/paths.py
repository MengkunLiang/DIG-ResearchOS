from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .types import SpecializationPaths


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (
            candidate / "skills" / "external_executor_skills"
        ).is_dir():
            return candidate
    raise FileNotFoundError("repo root not found from " + str(current))


def build_specialization_paths(*, workspace: Path, repo_root: Path | None = None) -> SpecializationPaths:
    root = (repo_root or find_repo_root()).resolve()
    ws = workspace.resolve()
    template_root = root / "skills" / "external_executor_skills"
    external_root = ws / "external_executor"
    return SpecializationPaths(
        repo_root=root,
        workspace=ws,
        template_root=template_root,
        mapping_path=template_root / "skill_specialization.yaml",
        schema_path=template_root / "schemas" / "project_skill_context.schema.json",
        external_executor_root=external_root,
        output_context_path=external_root / "project_skill_context.yaml",
        output_schema_path=external_root / "schemas" / "project_skill_context.schema.json",
        output_skills_path=external_root / "skills",
        output_report_path=external_root / "skill_specialization_report.json",
    )


def workspace_relative(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def repo_relative(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes allowed root: {path}") from exc
    return resolved


def new_staging_dir(external_root: Path) -> Path:
    return external_root / f".skill_specialization_staging-{uuid4().hex}"
