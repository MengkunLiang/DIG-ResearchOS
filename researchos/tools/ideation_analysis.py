"""Ideation coverage analysis tool.

Deterministically summarizes where T4 candidate ideas came from. The output is
coverage telemetry for the LLM/user, not a judgment that a candidate is good,
novel, or scientifically valid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def analyze_ideation_coverage(
    workspace_dir: Path,
    *,
    idea_scorecard_path: str = "ideation/idea_scorecard.yaml",
    synthesis_workbench_path: str = "literature/synthesis_workbench.json",
    missing_areas_path: str = "literature/missing_areas.md",
) -> dict[str, Any]:
    """Analyze how well ideation candidates cover available evidence.

    Returns a structured report with coverage metrics and gaps.
    """
    ws = Path(workspace_dir)

    # Load idea scorecard
    scorecard_file = ws / idea_scorecard_path
    if not scorecard_file.exists():
        return {"error": "idea_scorecard.yaml not found", "coverage": {}}

    with scorecard_file.open(encoding="utf-8") as f:
        scorecard = yaml.safe_load(f)

    ideas = scorecard.get("ideas", [])
    if not ideas:
        return {"error": "no ideas in scorecard", "coverage": {}}

    # Load synthesis workbench
    workbench_file = ws / synthesis_workbench_path
    workbench: dict[str, Any] = {}
    if workbench_file.exists():
        workbench = json.loads(workbench_file.read_text(encoding="utf-8"))

    # Load missing areas
    missing_file = ws / missing_areas_path
    missing_text = missing_file.read_text(encoding="utf-8") if missing_file.exists() else ""

    # Extract evidence sources from workbench
    families = workbench.get("method_families", [])
    mechanism_clusters = workbench.get("mechanism_claim_clusters") or workbench.get("domain_consensus", [])
    questions = workbench.get("research_question_candidates", [])
    assumptions = workbench.get("shared_assumption_candidates", [])

    # Analyze coverage
    family_names = {f["name"] for f in families}
    question_ids = {q["id"] for q in questions}
    consensus_mechanisms = [c["mechanism"][:80] for c in mechanism_clusters]

    # Count idea source types
    source_stats = {
        "total_ideas": len(ideas),
        "from_synthesis": 0,
        "from_missing_area": 0,
        "from_seed_idea": 0,
        "from_mechanism_claim_cluster": 0,
        "origin_free_reasoning": 0,
        "origin_seed_refinement": 0,
        "origin_evidence_driven": 0,
        "origin_synthesis_gestalt": 0,
        "origin_problem_reframing": 0,
        "origin_design_rationale_derivation": 0,
        "origin_cross_domain_analogy": 0,
        "origin_bridge_synthesis": 0,
        "origin_supplement": 0,
        "constraint_unsupported": 0,
        "has_mechanism_challenge": 0,
        "has_reverse_operation": 0,
        "has_subgroup_failure": 0,
        "has_gap_exploration": 0,
        "seed_alignment_direct": 0,
        "seed_alignment_partial": 0,
        "seed_alignment_none": 0,
    }

    for entry in ideas:
        source = entry.get("source", {})
        if source.get("from_synthesis_section"):
            source_stats["from_synthesis"] += 1
        if source.get("from_missing_area"):
            source_stats["from_missing_area"] += 1
        if source.get("from_seed_idea"):
            source_stats["from_seed_idea"] += 1

        # Check seed_alignment
        alignment = source.get("seed_alignment", "none")
        if alignment == "direct":
            source_stats["seed_alignment_direct"] += 1
        elif alignment == "partial":
            source_stats["seed_alignment_partial"] += 1
        else:
            source_stats["seed_alignment_none"] += 1

        # Detect idea origins/categories using structured fields. These fields
        # are descriptive telemetry; missing a supplement is a prompt/coverage
        # signal, not proof of low idea quality.
        idea_obj = entry.get("idea", {})
        category = str(source.get("category", "")).lower().strip()
        idea_origin = str(
            source.get("idea_origin")
            or source.get("origin")
            or idea_obj.get("idea_origin")
            or ""
        ).lower().strip()
        constraint_status = str(
            source.get("constraint_status")
            or idea_obj.get("constraint_status")
            or ""
        ).lower().strip()

        if idea_origin == "free_reasoning":
            source_stats["origin_free_reasoning"] += 1
        elif idea_origin in {"seed_refinement", "seed_derived"}:
            source_stats["origin_seed_refinement"] += 1
        elif idea_origin == "evidence_driven":
            source_stats["origin_evidence_driven"] += 1
        elif idea_origin == "synthesis_gestalt":
            source_stats["origin_synthesis_gestalt"] += 1
        elif idea_origin == "problem_reframing":
            source_stats["origin_problem_reframing"] += 1
        elif idea_origin == "design_rationale_derivation":
            source_stats["origin_design_rationale_derivation"] += 1
        elif idea_origin == "cross_domain_analogy":
            source_stats["origin_cross_domain_analogy"] += 1
        elif idea_origin == "survey_driven":
            source_stats["origin_survey_driven"] += 1
        elif idea_origin == "bridge_synthesis":
            source_stats["origin_bridge_synthesis"] += 1
        if constraint_status == "supplement" or idea_origin in {
            "mechanism_challenge",
            "reverse_operation",
            "subgroup_failure",
            "missing_area_exploration",
            "gap_exploration",
        }:
            source_stats["origin_supplement"] += 1
        if constraint_status == "not_supported_by_current_evidence":
            source_stats["constraint_unsupported"] += 1

        if (
            "mechanism_claim_clusters" in str(source)
            or "domain_consensus" in str(source)
            or category == "mechanism_challenge"
            or idea_origin == "mechanism_challenge"
        ):
            source_stats["from_mechanism_claim_cluster"] += 1

        # Use the structured category field when available
        origin_or_category = category or idea_origin
        if origin_or_category == "mechanism_challenge":
            source_stats["has_mechanism_challenge"] += 1
        elif origin_or_category == "reverse_operation":
            source_stats["has_reverse_operation"] += 1
        elif origin_or_category == "subgroup_failure":
            source_stats["has_subgroup_failure"] += 1
        elif origin_or_category in {"gap_exploration", "missing_area_exploration"}:
            source_stats["has_gap_exploration"] += 1
        else:
            # Fallback: analyze trigger_observation text
            basis_summary = str(source.get("trigger_observation", "")).lower()
            if any(kw in basis_summary for kw in ["质疑", "falsify", "disambiguate", "mechanism"]):
                source_stats["has_mechanism_challenge"] += 1
            elif any(kw in basis_summary for kw in ["反向", "reverse", "remove", "without", "减掉"]):
                source_stats["has_reverse_operation"] += 1
            elif any(kw in basis_summary for kw in ["子群", "subgroup", "underperform", "失败"]):
                source_stats["has_subgroup_failure"] += 1
            elif any(kw in basis_summary for kw in ["缺口", "gap", "missing", "覆盖不足"]):
                source_stats["has_gap_exploration"] += 1

    # Coverage of method families
    # Use the idea's mechanism_family field and the synthesis workbench's family names.
    # Match when the idea's family overlaps with a workbench family name.
    covered_families = set()
    for entry in ideas:
        idea_obj = entry.get("idea", {})
        idea_family = idea_obj.get("mechanism_family", "").lower().strip()
        if not idea_family:
            continue
        for fname in family_names:
            fname_lower = fname.lower()
            # Direct substring match or shared tokens
            if idea_family in fname_lower or fname_lower in idea_family:
                covered_families.add(fname)
            else:
                # Token overlap: split by common delimiters and check overlap
                idea_tokens = set(re.split(r"[、,，\s/]+", idea_family))
                family_tokens = set(re.split(r"[、,，\s/]+", fname_lower))
                idea_tokens.discard("")
                family_tokens.discard("")
                if idea_tokens & family_tokens:
                    covered_families.add(fname)

    # Coverage of research questions
    covered_questions = set()
    for entry in ideas:
        source = entry.get("source", {})
        synthesis_ref = str(source.get("from_synthesis_section", ""))
        for qid in question_ids:
            if qid in synthesis_ref:
                covered_questions.add(qid)

    # Missing areas coverage
    gap_count = len(re.findall(r"### (?:缺口|提示) \d+", missing_text))
    gaps_addressed = source_stats["from_missing_area"]

    coverage = {
        "idea_count": len(ideas),
        "source_stats": source_stats,
        "method_family_coverage": {
            "total_families": len(family_names),
            "covered_families": len(covered_families),
            "family_names": sorted(family_names),
            "covered_names": sorted(covered_families),
        },
        "research_question_coverage": {
            "total_questions": len(question_ids),
            "covered_questions": len(covered_questions),
            "question_ids": sorted(question_ids),
            "covered_ids": sorted(covered_questions),
        },
        "mechanism_claim_cluster_coverage": {
            "total_clusters": len(mechanism_clusters),
            "challengeable_hint_count": sum(1 for c in mechanism_clusters if c.get("challengeable_hint") or c.get("challengeable")),
            "ideas_addressing_clusters": source_stats["from_mechanism_claim_cluster"],
            "semantics": "mechanical_cluster_coverage_hint_only",
        },
        "missing_area_coverage": {
            "total_gaps": gap_count,
            "gaps_addressed": gaps_addressed,
        },
        "category_coverage": {
            "mechanism_challenge": source_stats["has_mechanism_challenge"],
            "reverse_operation": source_stats["has_reverse_operation"],
            "subgroup_failure": source_stats["has_subgroup_failure"],
            "gap_exploration": source_stats["has_gap_exploration"],
        },
        "origin_mix": {
            "free_reasoning": source_stats["origin_free_reasoning"],
            "seed_refinement": source_stats["origin_seed_refinement"],
            "evidence_driven": source_stats["origin_evidence_driven"],
            "synthesis_gestalt": source_stats["origin_synthesis_gestalt"],
            "problem_reframing": source_stats["origin_problem_reframing"],
            "design_rationale_derivation": source_stats["origin_design_rationale_derivation"],
            "cross_domain_analogy": source_stats["origin_cross_domain_analogy"],
            "bridge_synthesis": source_stats["origin_bridge_synthesis"],
            "supplement": source_stats["origin_supplement"],
            "mainline_total": _count_mainline_origins(source_stats),
            "constraint_unsupported": source_stats["constraint_unsupported"],
            "supplement_only_risk": source_stats["origin_supplement"] >= len(ideas),
        },
        "analysis_semantics": "coverage_telemetry_only_not_quality_or_novelty_judgment",
    }

    return {"coverage": coverage, "ok": True}


def _count_mainline_origins(source_stats: dict[str, int]) -> int:
    return (
        source_stats["origin_free_reasoning"]
        + source_stats["origin_seed_refinement"]
        + source_stats["origin_evidence_driven"]
        + source_stats["origin_synthesis_gestalt"]
        + source_stats["origin_problem_reframing"]
        + source_stats["origin_design_rationale_derivation"]
        + source_stats["origin_cross_domain_analogy"]
    )
