"""Population interaction graph primitives for T4 evolution.

The graph is deliberately a *decision aid*, not a second scorer or a hidden
survival mechanism.  Deterministic code derives bounded structural similarity
from already-authored Candidate genomes, chooses a compact shortlist, and
persists it.  An optional LLM Interaction Reviewer may then explain the
scientific relationship for that shortlist.  If that reviewer is unavailable,
the structural graph remains usable for parent selection, targeted mutation,
and crossover prioritisation without manufacturing a scientific judgement.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from .models import CandidateDossier, IdeaFamily
from .state import stable_fingerprint


_FEATURE_FIELDS = (
    "problem",
    "mechanism",
    "contribution_package",
    "hypothesis_bundle",
    "validation_logic",
)
_FEATURE_WEIGHTS = {
    "problem": 0.30,
    "mechanism": 0.25,
    "contribution_package": 0.20,
    "hypothesis_bundle": 0.15,
    "validation_logic": 0.10,
}
_RELATION_TYPES = frozenset({"competitor", "complement", "distant_transfer", "parallel"})


def candidate_feature_view(candidate: CandidateDossier) -> dict[str, Any]:
    """Return a compact, deterministic feature view without interpreting it.

    This is intentionally shallow.  It gives the LLM reviewer concise source
    material and makes the graph cacheable, but does not assign a scientific
    meaning to lexical overlap.
    """

    values = {
        name: _normalise_text(getattr(candidate.genome, name).value)
        for name in _FEATURE_FIELDS
    }
    return {
        "candidate_id": candidate.candidate_id,
        "route": candidate.genome.route,
        "parents": list(candidate.genome.parents),
        "maturity": candidate.maturity.value,
        "features": values,
        "tokens": {name: sorted(_tokens(value)) for name, value in values.items()},
    }


def pair_similarity(left: CandidateDossier, right: CandidateDossier) -> dict[str, float]:
    """Calculate transparent lexical structural similarity for one pair."""

    left_view = candidate_feature_view(left)
    right_view = candidate_feature_view(right)
    dimensions = {
        name: round(
            _jaccard(
                set(left_view["tokens"][name]),
                set(right_view["tokens"][name]),
            ),
            6,
        )
        for name in _FEATURE_FIELDS
    }
    overall = sum(dimensions[name] * _FEATURE_WEIGHTS[name] for name in _FEATURE_FIELDS)
    return {**dimensions, "overall": round(overall, 6), "distance": round(1.0 - overall, 6)}


def build_interaction_shortlist(
    dossiers: Iterable[CandidateDossier],
    families: Iterable[IdeaFamily],
) -> list[dict[str, Any]]:
    """Choose at most competitor/complement/wildcard peers per Candidate.

    The shortlist is purposely deterministic and cheap.  It does not claim
    that two Candidates are equivalent or composable; it only selects pairs
    worth independent LLM interpretation.  A population of fewer than two
    Candidates has a valid empty graph.
    """

    candidates = list(dossiers)
    family_by_id = {
        candidate_id: family.family_id
        for family in families
        for candidate_id in family.member_ids
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source in candidates:
        comparisons: list[tuple[CandidateDossier, dict[str, float]]] = [
            (target, pair_similarity(source, target))
            for target in candidates
            if target.candidate_id != source.candidate_id
        ]
        if not comparisons:
            continue

        closest, closest_similarity = max(comparisons, key=lambda item: item[1]["overall"])
        _append_hint(
            result,
            seen,
            source=source,
            target=closest,
            relation_type="competitor",
            similarity=closest_similarity,
            family_by_id=family_by_id,
        )

        # Complementarity prefers a shared problem with a different mechanism
        # or validation logic.  It is a retrieval heuristic only; the later
        # reviewer decides whether a joint thesis exists.
        def complement_priority(item: tuple[CandidateDossier, dict[str, float]]) -> tuple[float, float, str]:
            target, values = item
            cross_family = float(family_by_id.get(source.candidate_id) != family_by_id.get(target.candidate_id))
            route_difference = float(source.genome.route != target.genome.route)
            score = (
                values["problem"] * 0.42
                + (1.0 - values["mechanism"]) * 0.26
                + (1.0 - values["validation_logic"]) * 0.17
                + cross_family * 0.10
                + route_difference * 0.05
            )
            return (round(score, 6), values["problem"], target.candidate_id)

        complement, complement_similarity = max(comparisons, key=complement_priority)
        if complement_similarity["problem"] > 0 or source.genome.route != complement.genome.route:
            _append_hint(
                result,
                seen,
                source=source,
                target=complement,
                relation_type="complement",
                similarity=complement_similarity,
                family_by_id=family_by_id,
            )

        # A distant transfer is intentionally not merely the least similar
        # string.  Favor a different Route/Family so a Wildcard remains visible
        # to the reviewer without forcing a crossover.
        def distant_priority(item: tuple[CandidateDossier, dict[str, float]]) -> tuple[float, float, str]:
            target, values = item
            cross_family = float(family_by_id.get(source.candidate_id) != family_by_id.get(target.candidate_id))
            route_difference = float(source.genome.route != target.genome.route)
            score = values["distance"] + cross_family * 0.12 + route_difference * 0.08
            return (round(score, 6), values["distance"], target.candidate_id)

        distant, distant_similarity = max(comparisons, key=distant_priority)
        _append_hint(
            result,
            seen,
            source=source,
            target=distant,
            relation_type="distant_transfer",
            similarity=distant_similarity,
            family_by_id=family_by_id,
        )
    return result


def build_interaction_graph(
    *,
    population_id: str,
    generation: int,
    dossiers: Iterable[CandidateDossier],
    families: Iterable[IdeaFamily],
) -> dict[str, Any]:
    """Build a cacheable graph with deterministic, explicitly non-semantic edges."""

    candidates = list(dossiers)
    family_list = list(families)
    features = [candidate_feature_view(candidate) for candidate in candidates]
    hints = build_interaction_shortlist(candidates, family_list)
    edges = [_deterministic_edge(hint, candidates) for hint in hints]
    fingerprint = stable_fingerprint(
        {
            "population_id": population_id,
            "generation": generation,
            "features": features,
            "family_members": [
                {"family_id": family.family_id, "member_ids": family.member_ids}
                for family in family_list
            ],
        }
    )
    return {
        "schema_version": "1.0.0",
        "semantics": "t4_population_interaction_graph",
        "population_id": population_id,
        "generation": generation,
        "input_fingerprint": fingerprint,
        "nodes": [
            {
                "candidate_id": candidate.candidate_id,
                "route": candidate.genome.route,
                "family_id": _family_id_for(candidate.candidate_id, family_list),
            }
            for candidate in candidates
        ],
        "feature_views": features,
        "shortlist": hints,
        "edges": edges,
        "review_status": "deterministic_pending_review",
        "warnings": [],
    }


def merge_interaction_reviews(
    graph: Mapping[str, Any],
    reviews: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Overlay optional LLM explanations onto deterministic pair identities.

    The reviewer may change the relation interpretation and supply prose, but
    it cannot add a Candidate, score, plan, or survivor.  Invalid review items
    are ignored and recorded as warnings rather than invalidating the graph.
    """

    result = dict(graph)
    edges = [dict(item) for item in graph.get("edges", []) if isinstance(item, Mapping)]
    edge_by_key = {
        (str(edge.get("source_id")), str(edge.get("target_id")), str(edge.get("relation_hint"))): edge
        for edge in edges
    }
    warnings = list(graph.get("warnings", [])) if isinstance(graph.get("warnings"), list) else []
    reviewed = 0
    for raw in reviews:
        source_id = str(raw.get("source_id") or "").strip()
        target_id = str(raw.get("target_id") or "").strip()
        relation_hint = str(raw.get("relation_hint") or raw.get("requested_relation") or "").strip()
        relation_type = _normalise_relation(raw.get("relation_type"))
        key = (source_id, target_id, relation_hint)
        edge = edge_by_key.get(key)
        if edge is None:
            warnings.append(f"ignored reviewer edge outside deterministic shortlist: {source_id}->{target_id}/{relation_hint}")
            continue
        if relation_type is None:
            warnings.append(f"ignored reviewer relation for {source_id}->{target_id}: {raw.get('relation_type')!r}")
            continue
        edge["relation_type"] = relation_type
        for field in (
            "shared_core",
            "key_difference",
            "peer_challenge",
            "transferable_element",
            "differentiation_need",
            "crossover_risk",
            "rationale",
        ):
            value = _normalise_text(raw.get(field))
            if value:
                edge[field] = value
        potential = str(raw.get("crossover_potential") or "").strip().casefold()
        if potential in {"high", "medium", "low", "none"}:
            edge["crossover_potential"] = potential
        edge["reviewed_by"] = "llm_interaction_reviewer"
        reviewed += 1
    result["edges"] = edges
    result["warnings"] = warnings
    result["review_status"] = "reviewed" if reviewed else "deterministic_degraded"
    return result


def interaction_peer_context(graph: Mapping[str, Any], candidate_id: str) -> list[dict[str, Any]]:
    """Return compact peer inputs for one Mutation Plan without hidden state."""

    return [
        {
            key: edge.get(key)
            for key in (
                "target_id",
                "relation_hint",
                "relation_type",
                "deterministic_similarity",
                "shared_core",
                "key_difference",
                "peer_challenge",
                "transferable_element",
                "differentiation_need",
                "crossover_potential",
                "crossover_risk",
                "rationale",
                "reviewed_by",
            )
            if edge.get(key) not in (None, "", [], {})
        }
        for edge in graph.get("edges", [])
        if isinstance(edge, Mapping) and str(edge.get("source_id") or "") == candidate_id
    ]


def rank_crossover_pairs(
    graph: Mapping[str, Any],
    *,
    allowed_parent_ids: Iterable[str],
    maximum: int,
) -> list[tuple[str, str]]:
    """Rank graph-supported pairs for advisory Crossover review.

    This function does not approve a merge.  It only saves the reviewer from
    spending calls on pairs with no visible structural relation.
    """

    allowed = {str(candidate_id) for candidate_id in allowed_parent_ids}
    ranked: dict[tuple[str, str], float] = {}
    for edge in graph.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        source = str(edge.get("source_id") or "")
        target = str(edge.get("target_id") or "")
        if source not in allowed or target not in allowed or source == target:
            continue
        pair = tuple(sorted((source, target)))
        similarity = edge.get("deterministic_similarity")
        values = similarity if isinstance(similarity, Mapping) else {}
        distance = _as_float(values.get("distance"))
        problem = _as_float(values.get("problem"))
        mechanism = _as_float(values.get("mechanism"))
        relation = str(edge.get("relation_type") or edge.get("relation_hint") or "")
        relation_bonus = {"complement": 0.42, "distant_transfer": 0.20, "parallel": -0.18, "competitor": -0.06}.get(relation, 0.0)
        potential_bonus = {"high": 0.30, "medium": 0.16, "low": 0.04, "none": -0.12}.get(
            str(edge.get("crossover_potential") or ""),
            0.0,
        )
        # Related problem + different mechanism receives the highest base
        # priority.  Fully distant pairs stay visible but cannot dominate a
        # coherent complement merely by having a large lexical distance.
        priority = problem * 0.38 + (1.0 - mechanism) * 0.30 + distance * 0.12 + relation_bonus + potential_bonus
        ranked[pair] = max(ranked.get(pair, float("-inf")), round(priority, 6))
    return [
        pair
        for pair, _priority in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[: max(0, maximum)]
    ]


def _append_hint(
    result: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    source: CandidateDossier,
    target: CandidateDossier,
    relation_type: str,
    similarity: Mapping[str, float],
    family_by_id: Mapping[str, str],
) -> None:
    key = (source.candidate_id, target.candidate_id, relation_type)
    if key in seen:
        return
    seen.add(key)
    result.append(
        {
            "source_id": source.candidate_id,
            "target_id": target.candidate_id,
            "relation_hint": relation_type,
            "deterministic_similarity": dict(similarity),
            "source_family_id": family_by_id.get(source.candidate_id, source.candidate_id),
            "target_family_id": family_by_id.get(target.candidate_id, target.candidate_id),
            "source_route": source.genome.route,
            "target_route": target.genome.route,
        }
    )


def _deterministic_edge(hint: Mapping[str, Any], candidates: Iterable[CandidateDossier]) -> dict[str, Any]:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    source = by_id.get(str(hint.get("source_id") or ""))
    target = by_id.get(str(hint.get("target_id") or ""))
    relation = str(hint.get("relation_hint") or "parallel")
    similarity = hint.get("deterministic_similarity") if isinstance(hint.get("deterministic_similarity"), Mapping) else {}
    shared = _shared_tokens(source, target, field="problem")
    difference = _different_fields(similarity)
    return {
        **dict(hint),
        "relation_type": relation if relation in _RELATION_TYPES else "parallel",
        "shared_core": ("、".join(shared) if shared else "结构关系待独立审阅"),
        "key_difference": ("、".join(difference) if difference else "机制与贡献差异待独立审阅"),
        "peer_challenge": "需要由 Interaction Reviewer 说明可区分的机制、贡献或验证差异。",
        "transferable_element": "仅为结构性候选关系，未经审阅不得视为可迁移结论。",
        "differentiation_need": "在后续演化中保持与该候选的核心差异可见。",
        "crossover_potential": "none",
        "crossover_risk": "尚未进行 LLM Interaction Review。",
        "rationale": "基于 Candidate Genome 的可复现结构相似度短名单；不是科学等价或合并结论。",
        "reviewed_by": "deterministic_shortlist",
    }


def _family_id_for(candidate_id: str, families: Iterable[IdeaFamily]) -> str:
    for family in families:
        if candidate_id in family.member_ids:
            return family.family_id
    return candidate_id


def _shared_tokens(left: CandidateDossier | None, right: CandidateDossier | None, *, field: str) -> list[str]:
    if left is None or right is None:
        return []
    return sorted(
        _tokens(getattr(left.genome, field).value) & _tokens(getattr(right.genome, field).value)
    )[:8]


def _different_fields(similarity: Mapping[str, Any]) -> list[str]:
    values = [
        (field, _as_float(similarity.get(field)))
        for field in ("mechanism", "contribution_package", "hypothesis_bundle", "validation_logic")
    ]
    return [field for field, _value in sorted(values, key=lambda item: item[1])[:2]]


def _normalise_relation(value: object) -> str | None:
    raw = " ".join(str(value or "").strip().casefold().replace("_", " ").replace("-", " ").split())
    aliases = {
        "competitor": "competitor",
        "competition": "competitor",
        "overlap": "competitor",
        "complement": "complement",
        "complementary": "complement",
        "distant transfer": "distant_transfer",
        "wildcard": "distant_transfer",
        "parallel": "parallel",
        "keep parallel": "parallel",
        "并行": "parallel",
        "互补": "complement",
        "竞争": "competitor",
    }
    return aliases.get(raw)


def _normalise_text(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(_normalise_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_normalise_text(item) for item in value)
    return " ".join(str(value or "").split())


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", _normalise_text(value).casefold())
        if token not in {"the", "and", "for", "with", "that", "this", "method", "model", "paper"}
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _as_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
