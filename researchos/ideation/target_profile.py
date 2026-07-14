"""Versioned T4 publication-orientation profiles.

This module deliberately does not encode a venue catalogue in Python.  It
uses the existing writing-profile resolver as one optional clue, applies a
small set of transparent orientation terms, and preserves any natural-language
instruction for the prompt composer and later human review.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..runtime.system_config import system_config_path
from ..writing_profiles import resolve_venue_writing_profile
from .models import TargetProfile


def load_target_profile_catalog(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load system-maintained, non-project-specific profile defaults."""

    # Use the shared deployment-aware resolver. In a source checkout this is
    # the repository config; in Docker the package lives in site-packages while
    # the mounted/application config remains under `/app/config`.
    source = path or system_config_path("t4_target_profiles.yaml")
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"T4 Target Profile config is unavailable: {source}") from exc
    profiles = payload.get("profiles") if isinstance(payload, dict) else {}
    if not isinstance(profiles, dict):
        raise ValueError(f"T4 Target Profile config has no profiles mapping: {source}")
    return {str(key): deepcopy(value) for key, value in profiles.items() if isinstance(value, dict)}


def suggest_target_profile(workspace_dir: Path) -> TargetProfile:
    """Infer a transparent, editable suggestion from existing workspace data."""

    workspace = Path(workspace_dir)
    project = _read_yaml_mapping(workspace / "project.yaml")
    writing_style = _read_json_mapping(workspace / "drafts" / "writing_style.json")
    raw_orientation = _first_text(
        project,
        "research_orientation",
        "publication_orientation",
        "contribution_preference",
        "paper_type",
        "manuscript_type",
        "project_objective",
    )
    venue = _first_text(project, "target_venue", "target_journal", "target_conference", "publication_target")
    inferred_from: list[str] = []
    instruction = raw_orientation
    profile_type = _profile_type_from_text(raw_orientation)
    if raw_orientation:
        inferred_from.append("project.yaml research orientation")
    if profile_type is None:
        resolved = resolve_venue_writing_profile(venue, writing_style)
        style = str(resolved.get("venue_style") or "").strip().casefold()
        profile_type = {"is": "management_is", "ccf_a": "technical_cs", "both": "hybrid"}.get(style)
        if profile_type is not None:
            inferred_from.append(str(resolved.get("resolved_from") or "workspace publication setting"))
    if profile_type is None:
        profile_type = "hybrid"
        inferred_from.append("system default")
    return _materialize_profile(
        profile_type,
        target_venues=[venue] if venue else [],
        user_instruction=instruction,
        inferred_from=inferred_from,
        confirmed_by_user=False,
        confidence="high" if raw_orientation else "medium" if venue else "low",
    )


def parse_target_profile_instruction(
    text: str,
    *,
    suggested: TargetProfile,
) -> TargetProfile:
    """Parse a concise user override while retaining unfamiliar wording safely.

    The parser never guesses a specific venue's current review policy.  It
    recognizes broad contribution orientations and delegates named-venue hints
    to the existing configurable venue resolver.  Unrecognized requirements
    become a `custom` profile rather than being discarded.
    """

    raw = " ".join(str(text or "").split())
    if not raw:
        return suggested.model_copy(update={"confirmed_by_user": True, "confidence": "high"})
    profile_type = _profile_type_from_text(raw)
    venue_profile = resolve_venue_writing_profile(raw, {})
    if profile_type is None:
        style = str(venue_profile.get("venue_style") or "").strip().casefold()
        profile_type = {"is": "management_is", "ccf_a": "technical_cs", "both": "hybrid"}.get(style)
    if profile_type is None:
        profile_type = "custom"
    venues = list(suggested.target_venues)
    if raw and _looks_like_venue_request(raw):
        venues = [*venues, raw]
    base_type = "hybrid" if profile_type == "custom" else profile_type
    profile = _materialize_profile(
        base_type,
        target_venues=venues,
        user_instruction=raw,
        inferred_from=[*suggested.inferred_from, "user input"],
        confirmed_by_user=True,
        confidence="high" if profile_type != "custom" else "medium",
    )
    if profile_type == "custom":
        profile = profile.model_copy(
            update={
                "profile_type": "custom",
                "scoring_profile": "custom",
                "user_instruction": raw,
                "confidence": "medium",
            }
        )
    return profile


def prompt_profile_summary(profile: TargetProfile, *, mode: str) -> dict[str, Any]:
    """Return only the profile facts relevant to one LLM task mode."""

    base: dict[str, Any] = {
        "profile_type": profile.profile_type,
        "primary_orientation": profile.primary_orientation,
        "priority_dimensions": profile.priority_dimensions,
        "user_instruction": profile.user_instruction,
    }
    if mode in {"planner", "generator"}:
        base["generation_emphasis"] = profile.priority_dimensions
        base["avoid"] = ["Do not turn the profile into an unsupported story or change Evidence Permission."]
    elif mode == "scorer":
        base["secondary_dimensions"] = profile.secondary_dimensions
        base["scoring_rule"] = "Return Profile Fit separately from the five Core Scientific Score dimensions."
    elif mode in {"evolver", "crossover", "human_composition"}:
        base["preservation_rule"] = "Improve profile-relevant strengths only when the approved plan permits it; do not rewrite evidence facts."
    elif mode == "final_card":
        base["storytelling_emphasis"] = profile.storytelling_emphasis
    return base


def _materialize_profile(
    profile_type: str,
    *,
    target_venues: list[str],
    user_instruction: str,
    inferred_from: list[str],
    confirmed_by_user: bool,
    confidence: str,
) -> TargetProfile:
    catalog = load_target_profile_catalog()
    raw = dict(catalog.get(profile_type) or catalog.get("hybrid") or {})
    return TargetProfile(
        profile_type=profile_type if profile_type in {"management_is", "technical_cs", "hybrid", "custom"} else "hybrid",
        target_venues=target_venues,
        primary_orientation=str(raw.get("primary_orientation") or "balanced"),
        priority_dimensions=[str(item) for item in raw.get("priority_dimensions", [])],
        secondary_dimensions=[str(item) for item in raw.get("secondary_dimensions", [])],
        storytelling_emphasis=[str(item) for item in raw.get("storytelling_emphasis", [])],
        scoring_profile=str(raw.get("scoring_profile") or profile_type),
        portfolio_profile_weight=float(raw.get("portfolio_profile_weight", 0.2)),
        user_instruction=user_instruction,
        inferred_from=inferred_from,
        confirmed_by_user=confirmed_by_user,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
    )


def _profile_type_from_text(value: str) -> str | None:
    normalized = " ".join(str(value or "").casefold().replace("/", " ").replace("-", " ").split())
    if not normalized:
        return None
    if any(token in normalized for token in ("hybrid", "cross disciplinary", "cross-disciplinary", "跨学科", "双重贡献", "兼顾")):
        return "hybrid"
    if any(token in normalized for token in ("management", "management is", "information systems", "utd", "管理", "组织", "理论机制")):
        return "management_is"
    if any(token in normalized for token in ("technical", "computer science", "ccf", "algorithm", "算法", "技术", "计算")):
        return "technical_cs"
    return None


def _looks_like_venue_request(value: str) -> bool:
    lowered = value.casefold()
    return any(token in lowered for token in ("target", "venue", "journal", "conference", "目标", "期刊", "会议"))


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json_mapping(path: Path) -> dict[str, Any]:
    import json

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""
