from __future__ import annotations

"""Mechanical claim and visual registries for manuscript writing.

This module intentionally contains pure helpers only. It does not decide
scientific claims, captions, or figure messages; it turns existing plans into
stable registries that Writer/Reviewer agents can fill and audit.
"""

import re
from typing import Any


CORE_SECTION_ORDER = [
    "abstract",
    "introduction",
    "related_work",
    "methodology",
    "experiments",
    "analysis",
    "limitations",
    "conclusion",
]


def build_claim_ledger_seed(
    evidence_plan: dict[str, Any],
    *,
    paper_state: dict[str, Any] | None = None,
    resource_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a claim ledger seed from mechanical claim slots.

    The returned ledger is deliberately incomplete: ``claim_text`` and final
    support decisions must be supplied by the Writer LLM after reading the
    source artifacts.
    """

    slots = evidence_plan.get("claim_slots", []) if isinstance(evidence_plan, dict) else []
    shared_facts = paper_state.get("shared_facts", {}) if isinstance(paper_state, dict) else {}
    known_bib_keys = _unique_strings(
        shared_facts.get("bib_keys", []) or (resource_index or {}).get("bib_keys", [])
    )
    known_metrics = _normalize_metric_candidates(
        shared_facts.get("result_metrics", []) or (resource_index or {}).get("result_metrics", [])
    )
    known_artifacts = _known_artifacts(resource_index)

    claims: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, slot in enumerate(slots if isinstance(slots, list) else [], start=1):
        if not isinstance(slot, dict):
            continue
        slot_id = str(slot.get("slot_id") or f"claim_slot_{index:03d}").strip()
        claim_id = _dedupe_id(_safe_id(slot_id, fallback=f"claim_{index:03d}"), used_ids)
        used_ids.add(claim_id)
        candidate_evidence = _unique_strings(slot.get("candidate_evidence", []))
        citation_pool = _unique_strings(slot.get("citation_pool", []))
        metric_candidates = _normalize_metric_candidates(slot.get("result_metric_candidates", []))
        if not metric_candidates:
            metric_candidates = known_metrics
        claims.append(
            {
                "claim_id": claim_id,
                "source_slot_id": slot_id,
                "section": _normalize_section(str(slot.get("section") or "")),
                "claim_type": str(slot.get("claim_type") or "claim"),
                "status": "needs_llm_claim",
                "claim_text": "",
                "support_status": "unverified",
                "evidence_refs": candidate_evidence,
                "verified_evidence_refs": [],
                "citation_pool": citation_pool,
                "citation_keys": [],
                "result_metric_candidates": metric_candidates,
                "metric_refs": [],
                "figure_refs": [],
                "table_refs": [],
                "llm_task": str(slot.get("llm_task") or ""),
                "notes": "",
            }
        )

    return {
        "version": "1.0",
        "semantics": "mechanical_claim_ledger_seed_not_final_scientific_judgment",
        "claims": sorted(claims, key=lambda item: (_section_sort_key(item["section"]), item["claim_id"])),
        "global_constraints": {
            "bib_keys": known_bib_keys,
            "result_metrics": known_metrics,
            "known_artifacts": sorted(known_artifacts),
        },
        "rules": [
            "Tools seed slots and validate provenance only; the Writer LLM writes claim_text.",
            "A supported claim must cite source artifacts, metrics, citations, or visuals actually used.",
            "Unsupported claims should remain in the ledger with support_status=unsupported or deferred.",
        ],
    }


def validate_claim_ledger(
    ledger: dict[str, Any],
    *,
    known_bib_keys: list[str] | None = None,
    known_artifacts: list[str] | None = None,
) -> list[str]:
    """Return mechanical claim-ledger issues without judging claim quality."""

    issues: list[str] = []
    claims = ledger.get("claims", []) if isinstance(ledger, dict) else []
    if not isinstance(claims, list) or not claims:
        return ["claim ledger has no claims"]

    constraints = ledger.get("global_constraints", {}) if isinstance(ledger, dict) else {}
    bib_keys = set(known_bib_keys or constraints.get("bib_keys", []) or [])
    artifacts = set(known_artifacts or constraints.get("known_artifacts", []) or [])
    seen: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            issues.append(f"claim #{index} is not an object")
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if not claim_id:
            issues.append(f"claim #{index} missing claim_id")
        elif claim_id in seen:
            issues.append(f"duplicate claim_id: {claim_id}")
        seen.add(claim_id)

        section = _normalize_section(str(claim.get("section") or ""))
        if section not in CORE_SECTION_ORDER and section != "global":
            issues.append(f"{claim_id or index}: unknown section {section!r}")

        status = str(claim.get("status") or "").strip()
        support_status = str(claim.get("support_status") or "").strip()
        claim_text = str(claim.get("claim_text") or "").strip()
        if status in {"ready", "written", "supported"} and not claim_text:
            issues.append(f"{claim_id or index}: status {status} requires claim_text")
        if support_status == "supported":
            evidence = _unique_strings(claim.get("verified_evidence_refs", []))
            metrics = _unique_strings(claim.get("metric_refs", []))
            cites = _unique_strings(claim.get("citation_keys", []))
            valid_cites = [key for key in cites if not bib_keys or key in bib_keys]
            visuals = _unique_strings(claim.get("figure_refs", [])) + _unique_strings(claim.get("table_refs", []))
            if not any([evidence, metrics, valid_cites, visuals]):
                issues.append(f"{claim_id or index}: supported claim has no verified support refs")

        if bib_keys:
            for key in _unique_strings(claim.get("citation_keys", [])):
                if key not in bib_keys:
                    issues.append(f"{claim_id or index}: citation key not in bibliography: {key}")
        if artifacts:
            for ref in _unique_strings(claim.get("evidence_refs", [])) + _unique_strings(
                claim.get("verified_evidence_refs", [])
            ):
                if ref not in artifacts:
                    issues.append(f"{claim_id or index}: evidence artifact not indexed: {ref}")

    return issues


def build_figure_registry_seed(
    figure_plan: dict[str, Any],
    *,
    resource_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a registry seed for planned figures and tables."""

    visuals = figure_plan.get("planned_visuals", []) if isinstance(figure_plan, dict) else []
    figures = _media_list(resource_index, "figures") or _media_list(figure_plan, "existing_figures")
    tables = _media_list(resource_index, "tables") or _media_list(figure_plan, "existing_tables")
    assets_by_slug = {_asset_slug(item.get("path", "")): item for item in figures + tables if isinstance(item, dict)}

    registry_items: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, visual in enumerate(visuals if isinstance(visuals, list) else [], start=1):
        if not isinstance(visual, dict):
            continue
        raw_id = str(visual.get("figure_id") or visual.get("table_id") or f"visual_{index:03d}").strip()
        visual_id = _dedupe_id(_safe_id(raw_id, fallback=f"visual_{index:03d}"), used_ids)
        used_ids.add(visual_id)
        kind = "table" if raw_id.startswith("tab:") or "table_id" in visual else "figure"
        matched_asset = assets_by_slug.get(_asset_slug(raw_id), {})
        registry_items.append(
            {
                "visual_id": visual_id,
                "label": raw_id,
                "kind": kind,
                "status": str(visual.get("status") or "planned"),
                "intended_section": _normalize_section(str(visual.get("intended_section") or "")),
                "message_slot": str(visual.get("message_slot") or ""),
                "source_artifacts": _unique_strings(visual.get("source_artifacts", [])),
                "file_path": str(matched_asset.get("path") or ""),
                "caption": "",
                "alt_text": "",
                "notes": str(visual.get("notes") or ""),
            }
        )

    return {
        "version": "1.0",
        "semantics": "mechanical_figure_registry_seed_not_visual_generation",
        "visuals": sorted(
            registry_items,
            key=lambda item: (_section_sort_key(item["intended_section"]), item["visual_id"]),
        ),
        "existing_assets": {
            "figures": figures,
            "tables": tables,
        },
        "rules": [
            "Tools register planned visuals and assets only; LLM decides message and caption wording.",
            "Generated or included visuals must point to a file_path and source_artifacts.",
            "Captions must state data provenance when the visual reports empirical results.",
        ],
    }


def validate_figure_registry(
    registry: dict[str, Any],
    *,
    known_artifacts: list[str] | None = None,
) -> list[str]:
    """Return mechanical registry issues for figures and tables."""

    issues: list[str] = []
    visuals = registry.get("visuals", []) if isinstance(registry, dict) else []
    if not isinstance(visuals, list) or not visuals:
        return ["figure registry has no visuals"]

    artifacts = set(known_artifacts or [])
    seen: set[str] = set()
    ready_statuses = {"ready", "generated", "included", "available"}
    for index, visual in enumerate(visuals, start=1):
        if not isinstance(visual, dict):
            issues.append(f"visual #{index} is not an object")
            continue
        visual_id = str(visual.get("visual_id") or "").strip()
        if not visual_id:
            issues.append(f"visual #{index} missing visual_id")
        elif visual_id in seen:
            issues.append(f"duplicate visual_id: {visual_id}")
        seen.add(visual_id)

        label = str(visual.get("label") or "").strip()
        kind = str(visual.get("kind") or "").strip()
        if kind == "figure" and label and not label.startswith("fig:"):
            issues.append(f"{visual_id or index}: figure label should start with fig:")
        if kind == "table" and label and not label.startswith("tab:"):
            issues.append(f"{visual_id or index}: table label should start with tab:")

        status = str(visual.get("status") or "").strip()
        if status in ready_statuses and not str(visual.get("file_path") or "").strip():
            issues.append(f"{visual_id or index}: status {status} requires file_path")
        if status in ready_statuses and not str(visual.get("caption") or "").strip():
            issues.append(f"{visual_id or index}: status {status} requires caption")

        if artifacts:
            for ref in _unique_strings(visual.get("source_artifacts", [])):
                if ref not in artifacts:
                    issues.append(f"{visual_id or index}: source artifact not indexed: {ref}")

    return issues


def _known_artifacts(resource_index: dict[str, Any] | None) -> set[str]:
    if not isinstance(resource_index, dict):
        return set()
    paths = {
        str(item.get("path"))
        for item in resource_index.get("artifacts", [])
        if isinstance(item, dict) and item.get("path")
    }
    for key in ("figures", "tables"):
        paths.update(
            str(item.get("path"))
            for item in resource_index.get(key, [])
            if isinstance(item, dict) and item.get("path")
        )
    return paths


def _media_list(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    values = data.get(key, [])
    return [dict(item) for item in values if isinstance(item, dict)]


def _normalize_metric_candidates(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        if isinstance(item, dict):
            metric = str(item.get("metric") or item.get("name") or "").strip()
            value = "" if item.get("value") is None else str(item.get("value"))
            experiment_id = str(item.get("experiment_id") or item.get("source") or "").strip()
            key = (experiment_id, metric, value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"experiment_id": experiment_id, "metric": metric, "value": value})
        elif isinstance(item, (str, int, float)):
            key = ("", str(item), "")
            if key not in seen:
                seen.add(key)
                normalized.append({"experiment_id": "", "metric": str(item), "value": ""})
    return normalized


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_section(section: str) -> str:
    key = section.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "intro": "introduction",
        "related": "related_work",
        "related_work": "related_work",
        "literature_review": "related_work",
        "method": "methodology",
        "methods": "methodology",
        "approach": "methodology",
        "results": "experiments",
        "evaluation": "experiments",
        "discussion": "analysis",
        "limitation": "limitations",
    }
    return aliases.get(key, key or "global")


def _section_sort_key(section: str) -> int:
    if section in CORE_SECTION_ORDER:
        return CORE_SECTION_ORDER.index(section)
    return len(CORE_SECTION_ORDER)


def _safe_id(value: str, *, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_:-]+", "_", value.strip())
    clean = clean.strip("_")
    return clean or fallback


def _dedupe_id(value: str, used: set[str]) -> str:
    if value not in used:
        return value
    suffix = 2
    while f"{value}_{suffix}" in used:
        suffix += 1
    return f"{value}_{suffix}"


def _asset_slug(value: str) -> str:
    stem = value.rsplit("/", maxsplit=1)[-1].rsplit(".", maxsplit=1)[0]
    stem = stem.replace("fig:", "").replace("tab:", "")
    return re.sub(r"[^a-z0-9]+", "", stem.lower())
