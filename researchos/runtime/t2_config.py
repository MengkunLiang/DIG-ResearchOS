from __future__ import annotations

"""Centralized T2/T3 literature-flow runtime parameters.

These helpers keep mechanical thresholds in `config/agent_params.yaml` instead
of scattering them across validators, recovery paths, and prompts.
"""

from dataclasses import asdict, dataclass
from typing import Any

from .agent_params import get_agent_mode_params


@dataclass(frozen=True)
class T2FinalizeConfig:
    active_pool_max: int = 120
    bridge_active_pool_cap_per_bridge: int = 15
    screened_active_pool_cap: int = 60
    snowball_active_pool_cap: int = 12
    finish_finalize_min_raw: int = 30
    dedup_title_threshold: float = 0.95
    access_audit_top_n: int = 50
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


def load_t2_finalize_config() -> T2FinalizeConfig:
    defaults = T2FinalizeConfig()
    try:
        params = get_agent_mode_params("scout", None)
    except Exception:
        params = {}

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


def load_deep_read_queue_config() -> DeepReadQueueConfig:
    defaults = DeepReadQueueConfig()
    try:
        params = get_agent_mode_params("reader", "read")
    except Exception:
        params = {}
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
