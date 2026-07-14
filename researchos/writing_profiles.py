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


def _venue_alias_matches(venue: str, alias: str) -> bool:
    """Match short aliases as complete words to avoid accidental substrings.

    For example, the generic ``ai`` alias must not turn ``AISeL`` into an AI
    conference profile. Longer venue names remain substring-matchable so names
    such as ``NeurIPS 2026`` and ``Information Systems Research`` work without
    an exact-string registry.
    """

    if not alias or not venue:
        return False
    if len(alias) <= 3:
        return alias in venue.split()
    return alias in venue


@lru_cache(maxsize=4)
def _load_profile_catalog(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    profiles = raw.get("profiles") if isinstance(raw, dict) else {}
    style_detection = raw.get("style_detection") if isinstance(raw, dict) else {}
    return {
        "default_profile": str(raw.get("default_profile") or DEFAULT_PROFILE_ID) if isinstance(raw, dict) else DEFAULT_PROFILE_ID,
        "profiles": profiles if isinstance(profiles, dict) else {},
        "style_detection": style_detection if isinstance(style_detection, dict) else {},
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
            matched = [alias for alias in aliases if _venue_alias_matches(venue, alias)]
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


def suggest_venue_style(target_venue: object = "") -> str:
    """Suggest an internal writing style from the unified venue catalog.

    This is deliberately a drafting hint. It does not assert a venue's current
    template, review criteria, page limit, or submission requirements.
    """

    venue = _normalized(target_venue)
    profile = resolve_venue_writing_profile(target_venue)
    style = str(profile.get("venue_style") or "").strip()
    if str(profile.get("resolved_from") or "") != "default" and style:
        return style

    detection = _catalog().get("style_detection", {})
    is_aliases = detection.get("is_aliases", []) if isinstance(detection, Mapping) else []
    if venue and any(
        _venue_alias_matches(venue, _normalized(alias))
        for alias in is_aliases
        if _normalized(alias)
    ):
        return "is"
    if style:
        return style
    default = detection.get("default_style") if isinstance(detection, Mapping) else ""
    return str(default or "ccf_a")


def suggest_template_selection(target_venue: object = "") -> dict[str, str]:
    """Suggest a locally supported template from the same venue catalog.

    Unknown venues stay on the neutral English template rather than silently
    pretending that a NeurIPS template is appropriate. The user may still
    select a specific template at the T8 template gate.
    """

    profile = resolve_venue_writing_profile(target_venue)
    template_ids = profile.get("template_ids")
    source = str(profile.get("resolved_from") or "")
    if source == "target_venue" and isinstance(template_ids, list) and template_ids:
        template_id = str(template_ids[0]).strip()
        template_family = str(profile.get("template_family") or "basic_en").strip()
        language = str(profile.get("writing_language") or "en").strip()
        if template_id:
            return {
                "template_family": template_family or "basic_en",
                "template_id": template_id,
                "writing_language": language or "en",
            }
    return {"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en"}


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
