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
    "conclusion",
]

CDR_FIELDS = [
    "problem_frame",
    "design_rationale",
    "artifact",
    "design_principles",
    "data_view",
    "evaluation_mode",
    "contribution_type",
    "boundary_conditions",
    "cross_paper_tension",
]

CDR_CONTRIBUTION_TYPES = {"invention", "improvement", "exaptation", "routine"}


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
                if _artifact_path_exists(artifacts, ref):
                    continue
                if ref not in artifacts:
                    issues.append(f"{claim_id or index}: evidence artifact not indexed: {ref}")

    return issues


def build_cdr_claim_ledger_seed(
    *,
    evidence_plan: dict[str, Any],
    resource_index: dict[str, Any] | None = None,
    source_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a CDR ledger seed without making final contribution judgments."""

    source_texts = source_texts or {}
    known_artifacts = sorted(_known_artifacts(resource_index))
    cdr_tuple = _extract_cdr_tuple_hints(source_texts)
    slots = evidence_plan.get("claim_slots", []) if isinstance(evidence_plan, dict) else []
    claims: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, slot in enumerate(slots if isinstance(slots, list) else [], start=1):
        if not isinstance(slot, dict):
            continue
        slot_id = str(slot.get("slot_id") or f"claim_slot_{index:03d}").strip()
        claim_id = _dedupe_id(_safe_id(f"cdr_{slot_id}", fallback=f"cdr_claim_{index:03d}"), used_ids)
        used_ids.add(claim_id)
        section = _normalize_section(str(slot.get("section") or ""))
        cdr_field = str(slot.get("cdr_field") or _default_cdr_field_for_section(section)).strip()
        if cdr_field not in CDR_FIELDS:
            cdr_field = _default_cdr_field_for_section(section)
        evidence_refs = _unique_strings(slot.get("candidate_evidence", []))
        claims.append(
            {
                "claim_id": claim_id,
                "source_slot_id": slot_id,
                "claim": "",
                "status": "needs_llm_claim",
                "cdr_field": cdr_field,
                "required_section": [section] if section in CORE_SECTION_ORDER else [],
                "evidence_artifacts": evidence_refs,
                "citation_plan": _unique_strings(slot.get("citation_pool", [])),
                "risk_if_unsupported": _risk_for_cdr_field(cdr_field),
                "llm_task": str(slot.get("llm_task") or ""),
                "notes": "Seeded mechanically from evidence_plan; Writer LLM must write/verify final claim.",
            }
        )

    if not claims:
        for index, cdr_field in enumerate(CDR_FIELDS[:6], start=1):
            claims.append(
                {
                    "claim_id": f"cdr_{cdr_field}",
                    "source_slot_id": "synthetic_cdr_field",
                    "claim": "",
                    "status": "needs_llm_claim",
                    "cdr_field": cdr_field,
                    "required_section": _sections_for_cdr_field(cdr_field),
                    "evidence_artifacts": known_artifacts[:6],
                    "citation_plan": [],
                    "risk_if_unsupported": _risk_for_cdr_field(cdr_field),
                    "llm_task": "Fill this CDR claim after reading source artifacts.",
                    "notes": "Fallback seed because evidence_plan had no claim slots.",
                }
            )

    contribution_chains = _build_contribution_chain_seeds(
        claims,
        cdr_tuple=cdr_tuple,
        source_texts=source_texts,
    )

    return {
        "version": "1.0",
        "semantics": "cdr_claim_ledger_seed_not_final_scientific_judgment",
        "paper_thesis": _extract_paper_thesis_hint(source_texts),
        "cdr_tuple": cdr_tuple,
        "contribution_chains": contribution_chains,
        "contribution_claims": sorted(
            claims,
            key=lambda item: (
                _section_sort_key((item.get("required_section") or ["global"])[0]),
                str(item.get("claim_id")),
            ),
        ),
        "source_artifacts": {
            "available": known_artifacts,
            "used_for_seeding": sorted(source_texts),
        },
        "rules": [
            "This ledger is a mechanical seed; LLMs decide final claim wording and contribution framing.",
            "contribution_chains are writing lanes for 3-4 final contribution bullets, not final scientific claims.",
            "Do not use provenance count as a quality gate.",
            "A selected paper must explain design_rationale and contribution_type in prose.",
            "Unsupported CDR claims must be marked as limitation or TODO, not invented.",
        ],
    }


def _build_contribution_chain_seeds(
    claims: list[dict[str, Any]],
    *,
    cdr_tuple: dict[str, Any],
    source_texts: dict[str, str],
) -> list[dict[str, Any]]:
    """Seed 3-4 contribution lanes without deciding final contribution wording.

    CDR claim slots are section/evidence slots, so using them one-to-one as
    manuscript contributions creates too many alignment rows. These lanes give
    the Writer LLM a compact contribution skeleton to complete from evidence.
    """

    hypothesis_ids = _extract_hypothesis_ids_from_sources(source_texts)
    target_count = min(4, max(3, len(hypothesis_ids) if hypothesis_ids else 3))
    claims_by_section: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        for section in claim.get("required_section", []) or []:
            normalized = _normalize_section(str(section))
            claims_by_section.setdefault(normalized, []).append(claim)

    chains: list[dict[str, Any]] = []
    contribution_type = str(cdr_tuple.get("contribution_type") or "").strip()
    for idx in range(target_count):
        cid = f"C{idx + 1}"
        chain_claim_ids: list[str] = []
        for section in ["introduction", "related_work", "methodology", "experiments", "analysis", "conclusion"]:
            pool = claims_by_section.get(section, [])
            if pool:
                claim_id = str(pool[idx % len(pool)].get("claim_id") or "").strip()
                if claim_id and claim_id not in chain_claim_ids:
                    chain_claim_ids.append(claim_id)
        chains.append(
            {
                "cid": cid,
                "hypothesis": hypothesis_ids[idx] if idx < len(hypothesis_ids) else "LLM_REVIEW_REQUIRED",
                "source_claim_ids": chain_claim_ids,
                "contribution_type": contribution_type or "LLM_REVIEW_REQUIRED",
                "seed_status": "needs_llm_completion",
                "llm_task": (
                    "Complete this contribution lane by reading the linked claim slots and source artifacts; "
                    "do not treat the mechanical lane as a final claim."
                ),
            }
        )
    return chains


def _extract_hypothesis_ids_from_sources(source_texts: dict[str, str]) -> list[str]:
    text = "\n".join(str(value or "") for value in source_texts.values())
    found = re.findall(r"\bH\d+\b", text, flags=re.IGNORECASE)
    return [item.upper() for item in dict.fromkeys(found)]


def validate_cdr_claim_ledger(ledger: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if ledger.get("semantics") != "cdr_claim_ledger_seed_not_final_scientific_judgment":
        issues.append("cdr ledger semantics is incorrect")
    cdr_tuple = ledger.get("cdr_tuple")
    if not isinstance(cdr_tuple, dict):
        issues.append("cdr ledger missing cdr_tuple")
        cdr_tuple = {}
    contribution_type = str(cdr_tuple.get("contribution_type") or "").strip()
    if contribution_type and contribution_type not in CDR_CONTRIBUTION_TYPES:
        issues.append(f"invalid contribution_type: {contribution_type}")
    for field in ("problem_frame", "design_rationale", "artifact", "data_view", "evaluation_mode"):
        if field not in cdr_tuple:
            issues.append(f"cdr_tuple missing field: {field}")
    claims = ledger.get("contribution_claims")
    if not isinstance(claims, list) or not claims:
        issues.append("cdr ledger has no contribution_claims")
        return issues
    seen: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            issues.append(f"contribution_claim #{index} is not an object")
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if not claim_id:
            issues.append(f"contribution_claim #{index} missing claim_id")
        elif claim_id in seen:
            issues.append(f"duplicate contribution_claim id: {claim_id}")
        seen.add(claim_id)
        cdr_field = str(claim.get("cdr_field") or "").strip()
        if cdr_field not in CDR_FIELDS:
            issues.append(f"{claim_id or index}: invalid cdr_field {cdr_field!r}")
        sections = claim.get("required_section")
        if not isinstance(sections, list):
            issues.append(f"{claim_id or index}: required_section must be a list")
        evidence = claim.get("evidence_artifacts")
        if not isinstance(evidence, list):
            issues.append(f"{claim_id or index}: evidence_artifacts must be a list")
        if not str(claim.get("risk_if_unsupported") or "").strip():
            issues.append(f"{claim_id or index}: missing risk_if_unsupported")
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
                if _artifact_path_exists(artifacts, ref):
                    continue
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


def _artifact_path_exists(artifacts: set[str], ref: str) -> bool:
    if ref in artifacts:
        return True
    prefix = ref.rstrip("/") + "/"
    return any(item == ref or item.startswith(prefix) for item in artifacts)


def _extract_cdr_tuple_hints(source_texts: dict[str, str]) -> dict[str, Any]:
    combined = "\n".join(source_texts.values())
    contribution_type = _extract_contribution_type(combined)
    return {
        "problem_frame": _extract_labeled_hint(combined, "problem_frame") or "",
        "design_rationale": _extract_labeled_hint(combined, "design_rationale") or "",
        "artifact": _extract_labeled_hint(combined, "artifact") or "",
        "design_principles": _extract_list_hint(combined, "design_principles"),
        "data_view": _extract_labeled_hint(combined, "data_view") or "",
        "evaluation_mode": _extract_labeled_hint(combined, "evaluation_mode") or "",
        "contribution_type": contribution_type or "",
        "boundary_conditions": _extract_list_hint(combined, "boundary_conditions"),
        "cross_paper_tension": _extract_list_hint(combined, "cross_paper_tension"),
        "hint_semantics": "mechanical_text_hints_for_llm_review_not_final_claims",
    }


def _extract_paper_thesis_hint(source_texts: dict[str, str]) -> str:
    for text in source_texts.values():
        for pattern in (
            r"(?im)^paper[_ -]?thesis\s*[:：]\s*(.+)$",
            r"(?im)^thesis\s*[:：]\s*(.+)$",
            r"(?im)^core[_ -]?claim\s*[:：]\s*(.+)$",
        ):
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()[:500]
    return ""


def _extract_contribution_type(text: str) -> str:
    match = re.search(
        r"(?i)\b(invention|improvement|exaptation|routine)\b",
        text,
    )
    return match.group(1).lower() if match else ""


def _extract_labeled_hint(text: str, label: str) -> str:
    variants = {label, label.replace("_", " "), label.replace("_", "-")}
    for variant in variants:
        pattern = rf"(?im)^\s*[-*]?\s*['\"]?{re.escape(variant)}['\"]?\s*[:：]\s*(.+)$"
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()[:800]
    return ""


def _extract_list_hint(text: str, label: str) -> list[str]:
    value = _extract_labeled_hint(text, label)
    if not value:
        return []
    return _unique_strings(re.split(r"[;；]|,\s+|，", value))


def _default_cdr_field_for_section(section: str) -> str:
    if section in {"limitation", "limitations"}:
        return "boundary_conditions"
    return {
        "abstract": "contribution_type",
        "introduction": "problem_frame",
        "related_work": "cross_paper_tension",
        "methodology": "design_rationale",
        "experiments": "evaluation_mode",
        "analysis": "design_rationale",
        "conclusion": "design_principles",
    }.get(section, "design_rationale")


def _sections_for_cdr_field(cdr_field: str) -> list[str]:
    return {
        "problem_frame": ["introduction", "abstract"],
        "design_rationale": ["methodology", "analysis", "introduction"],
        "artifact": ["methodology"],
        "design_principles": ["methodology", "conclusion"],
        "data_view": ["experiments"],
        "evaluation_mode": ["experiments", "analysis"],
        "contribution_type": ["abstract", "introduction", "conclusion"],
        "boundary_conditions": ["conclusion", "analysis"],
        "cross_paper_tension": ["related_work", "introduction"],
    }.get(cdr_field, [])


def _risk_for_cdr_field(cdr_field: str) -> str:
    if cdr_field in {"design_rationale", "contribution_type", "problem_frame"}:
        return "high"
    if cdr_field in {"evaluation_mode", "data_view", "boundary_conditions"}:
        return "medium"
    return "low"


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
        "limitation": "conclusion",
        "limitations": "conclusion",
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
