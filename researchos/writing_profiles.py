from __future__ import annotations

"""Venue-aware *internal drafting* profiles for the paper Writer.

The profiles deliberately describe argumentative emphasis and internal section
budgets only.  They are not a source of official page limits, anonymity rules,
or submission-template requirements.  Those must be checked against the
current venue materials immediately before submission.
"""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from .runtime.system_config import system_config_path


DEFAULT_PROFILE_ID = "ccf_generic_concise"


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


@lru_cache(maxsize=4)
def _load_profile_catalog(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    profiles = raw.get("profiles") if isinstance(raw, dict) else {}
    return {
        "default_profile": str(raw.get("default_profile") or DEFAULT_PROFILE_ID) if isinstance(raw, dict) else DEFAULT_PROFILE_ID,
        "profiles": profiles if isinstance(profiles, dict) else {},
    }


def _catalog() -> dict[str, Any]:
    return _load_profile_catalog(str(system_config_path("venue_writing_profiles.yaml")))


def available_venue_writing_profiles() -> dict[str, dict[str, Any]]:
    """Return a copy of the valid profile map for UI, tests, and documentation."""

    profiles = _catalog()["profiles"]
    return {
        str(profile_id): deepcopy(profile)
        for profile_id, profile in profiles.items()
        if isinstance(profile, dict)
    }


def _style_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pick_profile_id(target_venue: object, writing_style: Mapping[str, Any]) -> tuple[str, str]:
    profiles = available_venue_writing_profiles()
    explicit = str(writing_style.get("venue_profile") or writing_style.get("writing_profile") or "").strip()
    if explicit in profiles:
        return explicit, "writing_style.venue_profile"

    template_id = _normalized(writing_style.get("template_id"))
    if template_id:
        for profile_id, profile in profiles.items():
            template_ids = {_normalized(item) for item in profile.get("template_ids", []) if str(item).strip()}
            if template_id in template_ids:
                return profile_id, "writing_style.template_id"

    venue = _normalized(target_venue)
    if venue:
        ranked: list[tuple[int, str]] = []
        for profile_id, profile in profiles.items():
            aliases = [_normalized(item) for item in profile.get("aliases", []) if str(item).strip()]
            matched = [alias for alias in aliases if alias and alias in venue]
            if matched:
                ranked.append((max(len(alias) for alias in matched), profile_id))
        if ranked:
            return max(ranked)[1], "target_venue"

    language = _normalized(writing_style.get("writing_language"))
    if language in {"zh", "chinese", "中文"}:
        return "basic_zh_research", "writing_style.writing_language"
    style = _normalized(writing_style.get("venue_style"))
    if style in {"is", "utd", "informs"}:
        return "informs_story", "writing_style.venue_style"
    if _normalized(writing_style.get("template_family")) == "basic en":
        return "basic_en_research", "writing_style.template_family"
    return str(_catalog()["default_profile"] or DEFAULT_PROFILE_ID), "default"


def resolve_venue_writing_profile(
    target_venue: object = "",
    writing_style: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a venue profile without claiming the venue's official limits.

    ``writing_style`` wins when an explicit profile or a selected template is
    available.  Target-venue aliases provide a useful pre-gate suggestion.
    """

    style = _style_mapping(writing_style)
    profiles = available_venue_writing_profiles()
    profile_id, source = _pick_profile_id(target_venue, style)
    profile = profiles.get(profile_id)
    if profile is None:
        profile_id = DEFAULT_PROFILE_ID
        profile = profiles.get(profile_id, {})
        source = "fallback"
    resolved = deepcopy(profile)
    resolved["id"] = profile_id
    resolved["resolved_from"] = source
    resolved["internal_budget_notice"] = (
        "Internal drafting targets only; verify current official venue page limits, template, and submission rules separately."
    )
    return resolved


def section_word_budget_ranges(profile: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    """Normalize optional internal section target ranges into a safe mapping."""

    raw = profile.get("section_word_budgets") if isinstance(profile, Mapping) else {}
    if not isinstance(raw, Mapping):
        return {}
    ranges: dict[str, tuple[int, int]] = {}
    for section, value in raw.items():
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            continue
        try:
            lower, upper = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            continue
        if lower >= 0 and upper >= lower:
            ranges[str(section)] = (lower, upper)
    return ranges


def storyline_required_headings(profile: Mapping[str, Any]) -> list[str]:
    raw = profile.get("storyline_headings") if isinstance(profile, Mapping) else []
    return [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
