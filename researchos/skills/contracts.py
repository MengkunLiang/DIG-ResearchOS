from __future__ import annotations

"""Declarative input/output contracts for standalone ResearchOS skills.

Historically a standalone skill was only a prompt plus a tool list.  That made
the CLI capable of *starting* a skill, but unable to tell a researcher what to
prepare, where to put it, or whether it was safe to spend an LLM call.  This
module keeps those details declarative in ``SKILL.md`` frontmatter and performs
the deterministic part of the check before the runtime is prepared.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from ..runtime.errors import ConfigurationError


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{label} must be a YAML object")
    return value


def _as_string(value: Any, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ConfigurationError(f"{label} must not be empty")
    return normalized


def _safe_relative_path(value: Any, *, label: str) -> str:
    text = _as_string(value, label=label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigurationError(f"{label} must be a workspace-relative path: {text!r}")
    if text in {".", "./"}:
        raise ConfigurationError(f"{label} must name a file, not the workspace root")
    return path.as_posix()


def _as_string_list(value: Any, *, label: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"{label} must be a non-empty list of strings")
    return tuple(_as_string(item, label=label) for item in value)


def _permission_prefixes(metadata: Mapping[str, Any], *, field: str, source: Path) -> tuple[str, ...]:
    """Normalize a Skill's declared workspace capability prefixes."""

    raw = metadata.get(field, [""])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigurationError(f"{field} must be a list of workspace-relative prefixes: {source}")
    normalized: list[str] = []
    for item in raw:
        value = item.strip().replace("\\", "/")
        if value in {"", ".", "./"}:
            normalized.append("")
            continue
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ConfigurationError(f"{field} contains an unsafe workspace prefix {item!r}: {source}")
        normalized.append(path.as_posix().rstrip("/") + "/")
    return tuple(dict.fromkeys(normalized))


def _path_matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = Path(path).as_posix()
    for prefix in prefixes:
        trimmed = prefix.rstrip("/")
        if prefix == "":
            if "/" not in normalized:
                return True
        elif normalized == trimmed or normalized.startswith(trimmed + "/"):
            return True
    return False


@dataclass(frozen=True)
class SkillInputRequirement:
    """One user-visible input requirement with alternative workspace paths."""

    key: str
    label: str
    description: str
    paths: tuple[str, ...]
    extensions: tuple[str, ...] = ()
    min_bytes: int = 1
    required: bool = True
    example: str = ""


@dataclass(frozen=True)
class SkillOutputDescriptor:
    """A user-visible output produced by a standalone skill."""

    key: str
    label: str
    path: str
    description: str


@dataclass(frozen=True)
class SkillInteraction:
    """The guided standalone-skill interaction declared in frontmatter."""

    mode: str
    language: str
    summary: str
    request_required: bool
    request_prompt: str
    example_request: str
    required_inputs: tuple[SkillInputRequirement, ...]
    optional_inputs: tuple[SkillInputRequirement, ...]
    outputs: tuple[SkillOutputDescriptor, ...]


@dataclass(frozen=True)
class SkillInputStatus:
    requirement: SkillInputRequirement
    state: str
    selected_path: str | None = None
    detail: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True)
class SkillReadiness:
    """Input-check result that can be persisted and rendered without an LLM."""

    skill_name: str
    workspace: Path
    interaction: SkillInteraction | None
    request: str
    input_statuses: tuple[SkillInputStatus, ...]
    request_ready: bool
    workspace_mode: str = "standalone"

    @property
    def ready(self) -> bool:
        return self.request_ready and all(
            status.is_ready
            for status in self.input_statuses
            if status.requirement.required
        )

    @property
    def selected_inputs(self) -> dict[str, Path]:
        return {
            status.requirement.key: self.workspace / status.selected_path
            for status in self.input_statuses
            if status.selected_path and status.is_ready
        }


def _parse_input_requirement(raw: Any, *, required: bool, label: str) -> SkillInputRequirement:
    item = _as_mapping(raw, label=label)
    key = _as_string(item.get("id"), label=f"{label}.id")
    if not _IDENTIFIER_RE.fullmatch(key):
        raise ConfigurationError(f"{label}.id must use lowercase letters, digits, '_' or '-': {key!r}")
    paths_raw = item.get("paths")
    if isinstance(paths_raw, str):
        paths_raw = [paths_raw]
    if not isinstance(paths_raw, list) or not paths_raw:
        raise ConfigurationError(f"{label}.paths must be a non-empty list of workspace-relative paths")
    paths = tuple(
        _safe_relative_path(path, label=f"{label}.paths")
        for path in paths_raw
    )
    extensions_raw = item.get("extensions", [])
    if isinstance(extensions_raw, str):
        extensions_raw = [extensions_raw]
    if not isinstance(extensions_raw, list):
        raise ConfigurationError(f"{label}.extensions must be a list")
    extensions = tuple(
        _as_string(extension, label=f"{label}.extensions").lower()
        for extension in extensions_raw
    )
    if any(not extension.startswith(".") for extension in extensions):
        raise ConfigurationError(f"{label}.extensions entries must start with '.'")
    min_bytes = item.get("min_bytes", 1)
    if isinstance(min_bytes, bool) or not isinstance(min_bytes, int) or min_bytes < 0:
        raise ConfigurationError(f"{label}.min_bytes must be a non-negative integer")
    return SkillInputRequirement(
        key=key,
        label=_as_string(item.get("label"), label=f"{label}.label"),
        description=_as_string(item.get("description"), label=f"{label}.description"),
        paths=paths,
        extensions=extensions,
        min_bytes=min_bytes,
        required=required,
        example=_as_string(item.get("example", ""), label=f"{label}.example", allow_empty=True),
    )


def _parse_output(raw: Any, *, label: str) -> SkillOutputDescriptor:
    item = _as_mapping(raw, label=label)
    key = _as_string(item.get("id"), label=f"{label}.id")
    if not _IDENTIFIER_RE.fullmatch(key):
        raise ConfigurationError(f"{label}.id must use lowercase letters, digits, '_' or '-': {key!r}")
    return SkillOutputDescriptor(
        key=key,
        label=_as_string(item.get("label"), label=f"{label}.label"),
        path=_safe_relative_path(item.get("path"), label=f"{label}.path"),
        description=_as_string(item.get("description"), label=f"{label}.description"),
    )


def parse_skill_interaction(metadata: Mapping[str, Any]) -> SkillInteraction | None:
    """Parse an optional guided interaction contract from skill frontmatter.

    Skills without an ``interaction`` object remain loadable for backward
    compatibility.  New public skills should always provide one; this is
    enforced in repository tests rather than silently breaking user-created
    local skills.
    """

    raw = metadata.get("interaction")
    if raw is None:
        return None
    item = _as_mapping(raw, label="interaction")
    mode = _as_string(item.get("mode", "guided"), label="interaction.mode")
    if mode not in {"guided", "legacy"}:
        raise ConfigurationError("interaction.mode must be 'guided' or 'legacy'")
    language = _as_string(item.get("language", "zh-CN"), label="interaction.language")
    required_items = item.get("required_inputs", [])
    optional_items = item.get("optional_inputs", [])
    outputs_raw = item.get("outputs", [])
    for raw_items, item_label in (
        (required_items, "interaction.required_inputs"),
        (optional_items, "interaction.optional_inputs"),
        (outputs_raw, "interaction.outputs"),
    ):
        if not isinstance(raw_items, list):
            raise ConfigurationError(f"{item_label} must be a list")
    required_inputs = tuple(
        _parse_input_requirement(value, required=True, label=f"interaction.required_inputs[{index}]")
        for index, value in enumerate(required_items)
    )
    optional_inputs = tuple(
        _parse_input_requirement(value, required=False, label=f"interaction.optional_inputs[{index}]")
        for index, value in enumerate(optional_items)
    )
    outputs = tuple(
        _parse_output(value, label=f"interaction.outputs[{index}]")
        for index, value in enumerate(outputs_raw)
    )
    keys = [requirement.key for requirement in required_inputs + optional_inputs]
    if len(set(keys)) != len(keys):
        raise ConfigurationError("interaction input ids must be unique")
    output_keys = [output.key for output in outputs]
    if len(set(output_keys)) != len(output_keys):
        raise ConfigurationError("interaction output ids must be unique")
    request_required = item.get("request_required", True)
    if not isinstance(request_required, bool):
        raise ConfigurationError("interaction.request_required must be a boolean")
    return SkillInteraction(
        mode=mode,
        language=language,
        summary=_as_string(item.get("summary", ""), label="interaction.summary", allow_empty=True),
        request_required=request_required,
        request_prompt=_as_string(
            item.get("request_prompt", "请说明希望本 Skill 完成什么。"),
            label="interaction.request_prompt",
        ),
        example_request=_as_string(item.get("example_request", ""), label="interaction.example_request", allow_empty=True),
        required_inputs=required_inputs,
        optional_inputs=optional_inputs,
        outputs=outputs,
    )


def validate_skill_metadata(metadata: Mapping[str, Any], *, source: Path) -> None:
    """Reject malformed public contracts at discovery time, before an LLM run."""

    interaction = parse_skill_interaction(metadata)
    read_prefixes = _permission_prefixes(metadata, field="allowed_read_prefixes", source=source)
    write_prefixes = _permission_prefixes(metadata, field="allowed_write_prefixes", source=source)
    outputs_expected = metadata.get("outputs_expected", {})
    if outputs_expected is None:
        outputs_expected = {}
    if not isinstance(outputs_expected, Mapping):
        raise ConfigurationError(f"outputs_expected must be a YAML object: {source}")
    for key, value in outputs_expected.items():
        _as_string(key, label=f"outputs_expected key in {source}")
        _safe_relative_path(value, label=f"outputs_expected.{key} in {source}")
    if interaction is None:
        return
    declared_paths = {
        _safe_relative_path(value, label=f"outputs_expected.{key} in {source}")
        for key, value in outputs_expected.items()
    }
    interaction_paths = {output.path for output in interaction.outputs}
    if declared_paths and interaction_paths and declared_paths != interaction_paths:
        raise ConfigurationError(
            f"interaction.outputs and outputs_expected must describe the same paths: {source}"
        )

    # The deterministic readiness screen must never promise an input/output
    # location which the eventual Skill session cannot access.  This catches a
    # class of late ``access_denied`` failures before an LLM is started.
    inaccessible_inputs = [
        path
        for requirement in interaction.required_inputs + interaction.optional_inputs
        for path in requirement.paths
        if not _path_matches_prefix(path, read_prefixes)
    ]
    if inaccessible_inputs:
        raise ConfigurationError(
            "guided Skill input path is outside allowed_read_prefixes: "
            + ", ".join(sorted(set(inaccessible_inputs)))
            + f" ({source})"
        )
    inaccessible_outputs = [
        output.path for output in interaction.outputs if not _path_matches_prefix(output.path, write_prefixes)
    ]
    if inaccessible_outputs:
        raise ConfigurationError(
            "guided Skill output path is outside allowed_write_prefixes: "
            + ", ".join(sorted(set(inaccessible_outputs)))
            + f" ({source})"
        )


def expected_outputs_from_metadata(metadata: Mapping[str, Any], workspace: Path) -> dict[str, Path]:
    """Return validated expected outputs, using the interaction contract when present."""

    interaction = parse_skill_interaction(metadata)
    raw = metadata.get("outputs_expected", {}) or {}
    if not isinstance(raw, Mapping):
        raise ConfigurationError("outputs_expected must be a YAML object")
    expected = {
        _as_string(key, label="outputs_expected key"): workspace / _safe_relative_path(value, label=f"outputs_expected.{key}")
        for key, value in raw.items()
    }
    if interaction and not expected:
        expected = {output.key: workspace / output.path for output in interaction.outputs}
    return expected


def _check_requirement(workspace: Path, requirement: SkillInputRequirement) -> SkillInputStatus:
    rejected: list[str] = []
    for rel_path in requirement.paths:
        candidate = workspace / rel_path
        if not candidate.exists():
            continue
        if not candidate.is_file():
            rejected.append(f"{rel_path} 不是普通文件")
            continue
        if requirement.extensions and candidate.suffix.lower() not in requirement.extensions:
            expected = ", ".join(requirement.extensions)
            rejected.append(f"{rel_path} 的扩展名不是 {expected}")
            continue
        size = candidate.stat().st_size
        if size < requirement.min_bytes:
            rejected.append(f"{rel_path} 只有 {size} bytes（至少需要 {requirement.min_bytes} bytes）")
            continue
        return SkillInputStatus(
            requirement=requirement,
            state="ready",
            selected_path=rel_path,
            detail=f"已验证：{rel_path}（{size} bytes）",
        )
    if rejected:
        detail = "；".join(rejected)
    else:
        detail = "未找到可用文件"
    return SkillInputStatus(requirement=requirement, state="missing", detail=detail)


def check_skill_readiness(*, skill_name: str, metadata: Mapping[str, Any], workspace: Path, request: str) -> SkillReadiness:
    """Check a Skill's request and declared input files without calling an LLM."""

    interaction = parse_skill_interaction(metadata)
    if interaction is None or interaction.mode == "legacy":
        return SkillReadiness(
            skill_name=skill_name,
            workspace=workspace,
            interaction=interaction,
            request=request.strip(),
            input_statuses=(),
            request_ready=True,
            workspace_mode=detect_skill_workspace_mode(workspace),
        )
    statuses = tuple(
        _check_requirement(workspace, requirement)
        for requirement in interaction.required_inputs + interaction.optional_inputs
    )
    return SkillReadiness(
        skill_name=skill_name,
        workspace=workspace,
        interaction=interaction,
        request=request.strip(),
        input_statuses=statuses,
        request_ready=not interaction.request_required or bool(request.strip()),
        workspace_mode=detect_skill_workspace_mode(workspace),
    )


def detect_skill_workspace_mode(workspace: Path) -> str:
    """Classify a Skill workspace without inferring that its evidence is sufficient."""

    project_markers = (
        "project.yaml",
        "literature/synthesis.md",
        "literature/synthesis_workbench.json",
        "ideation/hypotheses.md",
        "drafts/outline.md",
        "drafts/paper.tex",
        "experiments/results_summary.json",
    )
    return "project" if any((workspace / marker).exists() for marker in project_markers) else "standalone"


def prepare_skill_intake_packet(readiness: SkillReadiness) -> Path | None:
    """Write a user-editable, deterministic material checklist for a guided Skill.

    The packet never makes an input appear ready.  It records discovered project
    files and exact missing uploads so a user can resume the same session from
    a separate terminal or later conversation.
    """

    interaction = readiness.interaction
    if interaction is None or interaction.mode != "guided":
        return None
    packet_path = readiness.workspace / "user_inputs" / readiness.skill_name / "_intake.md"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Skill Intake · {readiness.skill_name}",
        "",
        f"- Workspace mode: `{readiness.workspace_mode}`",
        f"- Request: {readiness.request or '未提供；请在命令行或下一轮说明任务。'}",
        "- This file is a material checklist, not evidence and not a final output.",
        "",
        "## How This Run Will Use Materials",
    ]
    if readiness.workspace_mode == "project":
        lines.extend(
            [
                "The workspace contains project artifacts. Existing files are candidate inputs, not automatic proof that a claim is supported.",
                "The Skill must inspect selected artifacts before substantive output. If they are semantically insufficient, it writes `_followup_request.md` in this directory and asks for a focused human response.",
            ]
        )
    else:
        lines.extend(
            [
                "This is a standalone workspace. Put requested materials under the listed `user_inputs/` paths, then resume the same session. In an interactive terminal, the constrained intake agent can also organize material pasted by the human into those paths.",
                "The Skill must not invent missing evidence, bibliographic entries, results, or venue rules.",
            ]
        )
    lines.extend(["", "## Input Checklist"])
    for status in readiness.input_statuses:
        requirement = status.requirement
        state = "ready" if status.is_ready else "missing"
        lines.append("")
        lines.append(f"### [{state}] {requirement.label} ({'required' if requirement.required else 'optional'})")
        lines.append(requirement.description)
        if status.selected_path:
            lines.append(f"- Selected file: `{status.selected_path}`")
        else:
            lines.append("- Put one usable file at:")
            lines.extend(f"  - `{path}`" for path in requirement.paths)
            if requirement.example:
                lines.append(f"- Example: `{requirement.example}`")
        if status.detail:
            lines.append(f"- Deterministic check: {status.detail}")
    lines.extend(
        [
            "",
            "## Follow-up Protocol",
            "If the selected files exist but lack a necessary fact, source, result, citation, venue choice, or constraint, the running Skill writes `user_inputs/<skill>/_followup_request.md` with the exact gap, why it matters, and the preferred answer/file path. Do not replace missing information with assumptions.",
        ]
    )
    packet_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return packet_path


def readiness_as_dict(readiness: SkillReadiness) -> dict[str, Any]:
    """Return a JSON-safe representation for the persistent skill session."""

    return {
        "ready": readiness.ready,
        "request_ready": readiness.request_ready,
        "request": readiness.request,
        "workspace_mode": readiness.workspace_mode,
        "inputs": [
            {
                "id": status.requirement.key,
                "label": status.requirement.label,
                "required": status.requirement.required,
                "paths": list(status.requirement.paths),
                "state": status.state,
                "selected_path": status.selected_path,
                "detail": status.detail,
            }
            for status in readiness.input_statuses
        ],
    }
