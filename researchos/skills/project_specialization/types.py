from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


SpecializationStatus = Literal["ready", "incomplete", "failed"]
FieldStatus = Literal["confirmed", "confirmed_from_source", "uncertain"]


@dataclass(frozen=True)
class SpecializationPaths:
    repo_root: Path
    workspace: Path
    template_root: Path
    mapping_path: Path
    schema_path: Path
    external_executor_root: Path
    output_context_path: Path
    output_schema_path: Path
    output_skills_path: Path
    output_report_path: Path


@dataclass
class FieldMetadata:
    status: FieldStatus
    sources: list[str]
    note: str | None = None
    handoff_value_ignored: Any | None = None


@dataclass
class SpecializationResult:
    status: SpecializationStatus
    report_path: Path
    context_path: Path | None = None
    skills_path: Path | None = None
    required_uncertain_fields: list[dict[str, Any]] = field(default_factory=list)
    optional_uncertain_fields: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderedSkill:
    skill_name: str
    text: str
    confirmed_injections: int
    uncertain_injections: int
    required_uncertain_paths: list[str]
    optional_uncertain_paths: list[str]
    render_errors: list[dict[str, Any]]
    template_integrity_errors: list[dict[str, Any]]
