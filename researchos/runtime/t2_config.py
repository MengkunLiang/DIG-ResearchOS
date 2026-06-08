from __future__ import annotations

"""Centralized T2/T3 literature-flow runtime parameters.

These helpers keep mechanical thresholds in `config/agent_params.yaml` instead
of scattering them across validators, recovery paths, and prompts.
"""

from dataclasses import asdict, dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .agent_params import get_agent_mode_params


@dataclass(frozen=True)
class T2FinalizeConfig:
    active_pool_max: int = 120
    bridge_active_pool_cap_per_bridge: int = 15
    must_bridge_active_pool_cap_per_bridge: int = 15
    should_bridge_active_pool_cap_per_bridge: int = 5
    screened_active_pool_cap: int = 60
    snowball_active_pool_cap: int = 12
    finish_finalize_min_raw: int = 30
    dedup_title_threshold: float = 0.95
    access_audit_top_n: int = 50
    pre_active_light_backfill_max: int = 220
    metadata_backfill_max_concurrency: int = 6
    abstract_backfill_title_match_threshold: float = 0.88
    abstract_backfill_max_concurrency: int = 6
    snowball_max_sources: int = 12
    snowball_refs_per_source: int = 8
    snowball_max_candidates: int = 40
    snowball_max_concurrency: int = 6
    snowball_title_match_threshold: float = 0.90
    progress_enabled: bool = True
    progress_update_on_tool_results: bool = True
    progress_update_on_finalize: bool = True
    progress_file: str = "literature/temp/scout_progress.md"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepReadQueueConfig:
    deep_read_min: int = 35
    deep_read_target: int = 35
    deep_read_max: int = 45
    probe_pool: int = 45
    mainline_screened_cap: int = 90
    bridge_deep_floor: int = 3
    bridge_screened_cap: int = 7
    bridge_pool_cap: int = 15
    citation_hub_slots: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        if value in (None, "", [], {}):
            result = int(default)
        else:
            result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    return result


def _as_float(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        if value in (None, "", [], {}):
            result = float(default)
        else:
            result = float(str(value).strip())
    except (TypeError, ValueError):
        result = float(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled", ""}:
            return False
    return bool(value)


def detect_manuscript_profile(workspace_dir: Path | str | None = None) -> str:
    """Return the literature-flow profile for a workspace.

    `research_article` is the conservative default.  A workspace can opt into
    the broader survey profile either through `project.yaml: metadata` or
    through `user_seeds/seed_outline_profile.json`.
    """

    if workspace_dir is None:
        return "research_article"
    workspace = Path(workspace_dir)
    candidates: list[str] = []

    project_path = workspace / "project.yaml"
    if project_path.exists():
        try:
            project = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        except Exception:
            project = {}
        if isinstance(project, dict):
            metadata = project.get("metadata") if isinstance(project.get("metadata"), dict) else {}
            for key in ("manuscript_type", "project_type", "article_type", "paper_type"):
                candidates.append(str(metadata.get(key) or project.get(key) or ""))
            candidates.append(str(project.get("research_direction") or ""))
            candidates.extend(str(item) for item in project.get("keywords") or [] if item is not None)

    outline_profile_path = workspace / "user_seeds" / "seed_outline_profile.json"
    if outline_profile_path.exists():
        try:
            import json

            profile = json.loads(outline_profile_path.read_text(encoding="utf-8"))
        except Exception:
            profile = {}
        if isinstance(profile, dict):
            for key in ("manuscript_type", "project_type"):
                candidates.append(str(profile.get(key) or ""))
            intent = profile.get("writing_intent")
            if isinstance(intent, dict):
                candidates.append(str(intent.get("primary_output") or ""))
            candidates.append(str(profile.get("title") or ""))

    joined = " ".join(candidates).casefold()
    if any(token in joined for token in ("survey", "综述", "review", "taxonomy-driven")):
        return "survey"
    return "research_article"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _apply_behavior_profile(
    params: dict[str, Any],
    *,
    workspace_dir: Path | str | None,
) -> dict[str, Any]:
    profile_name = detect_manuscript_profile(workspace_dir)
    profiles = params.get("behavior_profiles")
    if not isinstance(profiles, dict):
        return params
    profile_cfg = profiles.get(profile_name)
    if not isinstance(profile_cfg, dict):
        return params
    merged = _deep_merge(params, profile_cfg)
    merged["selected_behavior_profile"] = profile_name
    return merged


def get_effective_reader_read_params(workspace_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        params = get_agent_mode_params("reader", "read")
    except Exception:
        params = {}
    return _apply_behavior_profile(params, workspace_dir=workspace_dir)


def load_t2_finalize_config(workspace_dir: Path | str | None = None) -> T2FinalizeConfig:
    defaults = T2FinalizeConfig()
    try:
        params = get_agent_mode_params("scout", None)
    except Exception:
        params = {}
    params = _apply_behavior_profile(params, workspace_dir=workspace_dir)

    finalize = params.get("t2_finalize")
    if not isinstance(finalize, dict):
        finalize = {}
    progress = params.get("progress")
    if not isinstance(progress, dict):
        progress = {}

    return T2FinalizeConfig(
        active_pool_max=_as_int(finalize.get("active_pool_max"), defaults.active_pool_max, minimum=10),
        bridge_active_pool_cap_per_bridge=_as_int(
            finalize.get("bridge_active_pool_cap_per_bridge"),
            defaults.bridge_active_pool_cap_per_bridge,
            minimum=0,
        ),
        must_bridge_active_pool_cap_per_bridge=_as_int(
            finalize.get("must_bridge_active_pool_cap_per_bridge"),
            finalize.get("bridge_active_pool_cap_per_bridge", defaults.must_bridge_active_pool_cap_per_bridge),
            minimum=0,
        ),
        should_bridge_active_pool_cap_per_bridge=_as_int(
            finalize.get("should_bridge_active_pool_cap_per_bridge"),
            defaults.should_bridge_active_pool_cap_per_bridge,
            minimum=0,
        ),
        screened_active_pool_cap=_as_int(
            finalize.get("screened_active_pool_cap"),
            defaults.screened_active_pool_cap,
            minimum=0,
        ),
        snowball_active_pool_cap=_as_int(
            finalize.get("snowball_active_pool_cap"),
            defaults.snowball_active_pool_cap,
            minimum=0,
        ),
        finish_finalize_min_raw=_as_int(
            finalize.get("finish_finalize_min_raw"),
            defaults.finish_finalize_min_raw,
            minimum=10,
        ),
        dedup_title_threshold=_as_float(
            finalize.get("dedup_title_threshold"),
            defaults.dedup_title_threshold,
            minimum=0.0,
            maximum=1.0,
        ),
        access_audit_top_n=_as_int(finalize.get("access_audit_top_n"), defaults.access_audit_top_n, minimum=1),
        pre_active_light_backfill_max=_as_int(
            finalize.get("pre_active_light_backfill_max"),
            defaults.pre_active_light_backfill_max,
            minimum=0,
        ),
        metadata_backfill_max_concurrency=_as_int(
            finalize.get("metadata_backfill_max_concurrency"),
            defaults.metadata_backfill_max_concurrency,
            minimum=1,
        ),
        abstract_backfill_title_match_threshold=_as_float(
            finalize.get("abstract_backfill_title_match_threshold"),
            defaults.abstract_backfill_title_match_threshold,
            minimum=0.0,
            maximum=1.0,
        ),
        abstract_backfill_max_concurrency=_as_int(
            finalize.get("abstract_backfill_max_concurrency"),
            defaults.abstract_backfill_max_concurrency,
            minimum=1,
        ),
        snowball_max_sources=_as_int(finalize.get("snowball_max_sources"), defaults.snowball_max_sources, minimum=0),
        snowball_refs_per_source=_as_int(
            finalize.get("snowball_refs_per_source"),
            defaults.snowball_refs_per_source,
            minimum=0,
        ),
        snowball_max_candidates=_as_int(
            finalize.get("snowball_max_candidates"),
            defaults.snowball_max_candidates,
            minimum=0,
        ),
        snowball_max_concurrency=_as_int(
            finalize.get("snowball_max_concurrency"),
            defaults.snowball_max_concurrency,
            minimum=1,
        ),
        snowball_title_match_threshold=_as_float(
            finalize.get("snowball_title_match_threshold"),
            defaults.snowball_title_match_threshold,
            minimum=0.0,
            maximum=1.0,
        ),
        progress_enabled=_as_bool(progress.get("enabled"), defaults.progress_enabled),
        progress_update_on_tool_results=_as_bool(
            progress.get("update_on_tool_results"),
            defaults.progress_update_on_tool_results,
        ),
        progress_update_on_finalize=_as_bool(
            progress.get("update_on_finalize"),
            defaults.progress_update_on_finalize,
        ),
        progress_file=str(progress.get("file") or defaults.progress_file),
    )


def load_deep_read_queue_config(workspace_dir: Path | str | None = None) -> DeepReadQueueConfig:
    defaults = DeepReadQueueConfig()
    params = get_effective_reader_read_params(workspace_dir)
    return DeepReadQueueConfig(
        deep_read_min=_as_int(params.get("deep_read_min"), defaults.deep_read_min, minimum=0),
        deep_read_target=_as_int(params.get("deep_read_target"), defaults.deep_read_target, minimum=1),
        deep_read_max=_as_int(params.get("deep_read_max"), defaults.deep_read_max, minimum=1),
        probe_pool=_as_int(params.get("probe_pool"), defaults.probe_pool, minimum=1),
        mainline_screened_cap=_as_int(
            params.get("mainline_screened_cap"),
            defaults.mainline_screened_cap,
            minimum=0,
        ),
        bridge_deep_floor=_as_int(params.get("bridge_deep_floor"), defaults.bridge_deep_floor, minimum=0),
        bridge_screened_cap=_as_int(params.get("bridge_screened_cap"), defaults.bridge_screened_cap, minimum=0),
        bridge_pool_cap=_as_int(params.get("bridge_pool_cap"), defaults.bridge_pool_cap, minimum=0),
        citation_hub_slots=_as_int(params.get("citation_hub_slots"), defaults.citation_hub_slots, minimum=0),
    )
