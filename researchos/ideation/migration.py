"""Backward-compatible conversion of legacy T4 candidate artifacts into P0.

Migration is intentionally conservative: it builds traceable `legacy_partial`
genomes and never invents a completed evolution round, score, hypothesis, or
evidence permission that the legacy workspace did not persist.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CandidateLineage,
    CandidateMaturity,
    CandidateStatus,
    GeneProvenance,
    IdeaGene,
    IdeaGenome,
    PopulationSnapshot,
    ReadingLevel,
    SourceRef,
)


LEGACY_CANDIDATE_PATH = Path("ideation/_candidate_directions.json")


def legacy_candidate_pool_exists(workspace_dir: Path) -> bool:
    path = Path(workspace_dir) / LEGACY_CANDIDATE_PATH
    return path.is_file() and path.stat().st_size > 0


def migrate_legacy_candidate_pool(
    workspace_dir: Path,
    *,
    input_fingerprint: str,
    run_config_fingerprint: str,
) -> tuple[PopulationSnapshot, list[IdeaGenome], list[CandidateLineage]]:
    """Return a P0 projection for a legacy candidate pool without writing it.

    The controller/state store owns persistence in the next phase. Returning
    typed objects makes migration testable and prevents a migration helper from
    silently modifying old workspace files.
    """

    path = Path(workspace_dir) / LEGACY_CANDIDATE_PATH
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read legacy candidate pool: {exc}") from exc
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("legacy candidate pool has no candidates")

    genomes: list[IdeaGenome] = []
    lineages: list[CandidateLineage] = []
    for index, raw in enumerate(candidates, start=1):
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("id") or raw.get("idea_id") or f"L{index}").strip()
        route = str(raw.get("idea_origin") or raw.get("origin") or "legacy_migration").strip() or "legacy_migration"
        source_refs = _legacy_sources(raw)
        provenance = GeneProvenance(
            source_routes=[route],
            source_refs=source_refs,
            reading_levels=_legacy_reading_levels(raw),
            confidence="low",
            upgrade_required=True,
        )
        gene = lambda value: IdeaGene(value=_legacy_text(value, candidate_id), provenance=provenance)
        genome = IdeaGenome(
            candidate_id=candidate_id,
            version=1,
            generation_created=0,
            maturity=CandidateMaturity.LEGACY_PARTIAL,
            route=route,
            parents=[],
            problem=gene(raw.get("target_problem") or raw.get("problem")),
            opportunity=gene(raw.get("pitch") or raw.get("core_claim")),
            challenged_assumption=gene(raw.get("challenged_assumption") or "legacy assumption not yet structured"),
            core_thesis=gene(raw.get("core_claim") or raw.get("pitch")),
            mechanism=gene(raw.get("mechanism")),
            design_or_artifact=gene(_legacy_design(raw)),
            contribution_package=gene(raw.get("contribution_character") or raw.get("innovation")),
            hypothesis_bundle=gene(_legacy_hypotheses(raw)),
            validation_logic=gene(raw.get("minimum_experiment") or raw.get("prediction")),
            boundary_conditions=gene(raw.get("counterfactual") or raw.get("selection_warning")),
            risks=gene(raw.get("selection_warning") or raw.get("risks")),
            migration_quality="legacy_partial",
        )
        lineage = CandidateLineage(
            candidate_id=candidate_id,
            parent_ids=[],
            route=route,
            created_by="legacy_migration",
        )
        genomes.append(genome)
        lineages.append(lineage)
    if not genomes:
        raise ValueError("legacy candidate pool contains no usable candidate objects")
    population = PopulationSnapshot(
        population_id="P0",
        generation=0,
        input_fingerprint=input_fingerprint,
        run_config_fingerprint=run_config_fingerprint,
        active_candidate_ids=[item.candidate_id for item in genomes],
        family_ids=[],
        elite_candidate_ids=[],
        archived_candidate_ids=[],
        created_from_round=None,
    )
    return population, genomes, lineages


def _legacy_sources(raw: dict[str, Any]) -> list[SourceRef]:
    sources = raw.get("supporting_papers") if isinstance(raw.get("supporting_papers"), list) else []
    result: list[SourceRef] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("source_file") or item.get("note_path") or "").strip()
        if not source_path or source_path.startswith("/") or ".." in source_path.split("/"):
            continue
        result.append(
            SourceRef(
                source_path=source_path,
                citation_key=str(item.get("ref") or item.get("citation") or ""),
                paper_id=str(item.get("paper_id") or ""),
                note=str(item.get("claim_used") or item.get("claim") or ""),
            )
        )
    return result


def _legacy_reading_levels(raw: dict[str, Any]) -> list[ReadingLevel]:
    supporting = raw.get("supporting_papers") if isinstance(raw.get("supporting_papers"), list) else []
    levels: set[ReadingLevel] = set()
    mapping = {
        "FULL_TEXT": ReadingLevel.FULL_TEXT,
        "PARTIAL_TEXT": ReadingLevel.PARTIAL_TEXT,
        "ABSTRACT_ONLY": ReadingLevel.ABSTRACT_ONLY,
        "METADATA_ONLY": ReadingLevel.METADATA_ONLY,
    }
    for item in supporting:
        if not isinstance(item, dict):
            continue
        level = mapping.get(str(item.get("evidence_level") or "").upper())
        if level:
            levels.add(level)
    return sorted(levels, key=lambda item: item.value)


def _legacy_text(value: Any, candidate_id: str) -> str:
    if isinstance(value, dict):
        parts = [str(item).strip() for item in value.values() if str(item).strip()]
        value = "; ".join(parts)
    elif isinstance(value, list):
        value = "; ".join(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return text or f"{candidate_id} legacy field requires structured completion"


def _legacy_design(raw: dict[str, Any]) -> Any:
    cdr = raw.get("cdr_tuple") if isinstance(raw.get("cdr_tuple"), dict) else {}
    return cdr.get("design_rationale") or raw.get("design_rationale") or raw.get("artifact")


def _legacy_hypotheses(raw: dict[str, Any]) -> Any:
    hypotheses = raw.get("candidate_hypotheses") if isinstance(raw.get("candidate_hypotheses"), list) else []
    if hypotheses:
        return hypotheses
    return raw.get("prediction")
