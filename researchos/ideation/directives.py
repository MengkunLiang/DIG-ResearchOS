"""Parse, validate, and persist user-directed T4 operations.

Semantic parsing is intentionally separated from execution. An optional LLM
parser can propose a structured directive, while deterministic validation owns
candidate IDs, component references, fingerprints, confirmation requirements,
and durable history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Awaitable, Callable
from uuid import uuid4

from ..pydantic_compat import model_dump
from .models import CandidateDossier, IdeaDirective, PopulationSnapshot
from .state import T4ArtifactStore, stable_fingerprint


DirectiveParser = Callable[[str], Awaitable[dict[str, object]]]
_DIRECTIVE_ACTIONS = {
    "select_candidate",
    "select_multiple",
    "keep_parallel",
    "compose_from_components",
    "continue_evolution",
    "focus_candidate",
    "merge_candidates",
    "refine_candidate",
    "show_more",
    "show_archive",
    "inspect_score",
    "inspect_evidence",
    "inspect_lineage",
    "inspect_hypotheses",
    "inspect_contributions",
    "inspect_genome",
    "regenerate_route",
    "rollback",
    "pause",
    "cancel",
}


def parse_idea_directive(
    raw_user_input: str,
    *,
    candidate_ids: set[str],
    option_id: str = "",
    llm_payload: dict[str, object] | None = None,
) -> IdeaDirective:
    """Build a bounded directive from a user instruction and optional LLM proposal."""

    raw = " ".join(str(raw_user_input or "").split())
    if not raw:
        raise ValueError("T4 directive needs a non-empty user instruction")
    proposed = llm_payload if isinstance(llm_payload, dict) else {}
    detected_ids, components = _extract_references(raw, candidate_ids)
    proposed_ids = proposed.get("target_candidate_ids")
    if isinstance(proposed_ids, list):
        detected_ids = list(dict.fromkeys([*detected_ids, *[str(item) for item in proposed_ids if str(item) in candidate_ids]]))
    action = _normalized_action(
        option_id=option_id,
        raw=raw,
        proposed_action=str(proposed.get("action") or ""),
        target_count=len(detected_ids),
        component_count=len(components),
    )
    confirmation_required = action in {
        "select_candidate",
        "select_multiple",
        "keep_parallel",
        "compose_from_components",
        "continue_evolution",
        "focus_candidate",
        "merge_candidates",
        "refine_candidate",
        "regenerate_route",
        "rollback",
    }
    directive = IdeaDirective(
        directive_id=f"DIR-{uuid4().hex[:12]}",
        action=action,
        target_candidate_ids=detected_ids,
        component_refs=components,
        preserve_genes=_string_list(proposed.get("preserve_genes")),
        donor_genes=_string_map(proposed.get("donor_genes")),
        requested_rounds=_requested_rounds(proposed.get("requested_rounds"), raw),
        constraints=_string_list(proposed.get("constraints")),
        raw_user_input=raw,
        confirmation_required=confirmation_required,
    )
    validate_idea_directive(directive, candidate_ids=candidate_ids)
    return directive


async def parse_idea_directive_llm_first(
    raw_user_input: str,
    *,
    candidate_ids: set[str],
    option_id: str = "",
    parser: DirectiveParser | None = None,
) -> IdeaDirective:
    """Ask an optional semantic parser, then enforce the local directive contract."""

    proposed: dict[str, object] | None = None
    if parser is not None:
        try:
            result = await parser(raw_user_input)
            proposed = result if isinstance(result, dict) else None
        except Exception:
            proposed = None
    return parse_idea_directive(
        raw_user_input,
        candidate_ids=candidate_ids,
        option_id=option_id,
        llm_payload=proposed,
    )


def validate_idea_directive(directive: IdeaDirective, *, candidate_ids: set[str]) -> None:
    """Validate identifiers and prohibit implicit cross-candidate composition."""

    missing = [candidate_id for candidate_id in directive.target_candidate_ids if candidate_id not in candidate_ids]
    if missing:
        raise ValueError("T4 directive references unknown candidate IDs: " + ", ".join(missing))
    component_candidates = [
        candidate_id
        for item in directive.component_refs
        for candidate_id in candidate_ids
        if item == candidate_id or item.startswith(candidate_id + "-")
    ]
    component_missing = [candidate_id for candidate_id in component_candidates if candidate_id not in candidate_ids]
    if component_missing:
        raise ValueError("T4 directive references components from unknown candidates: " + ", ".join(component_missing))
    if directive.action == "select_candidate" and len(directive.target_candidate_ids) != 1:
        raise ValueError("select_candidate requires exactly one complete Candidate")
    if directive.action in {"focus_candidate", "inspect_score", "inspect_evidence", "inspect_lineage", "inspect_hypotheses", "inspect_contributions", "inspect_genome"} and not directive.target_candidate_ids:
        raise ValueError(f"{directive.action} requires a Candidate ID")
    if directive.action == "compose_from_components" and len(set(component_candidates)) < 2:
        raise ValueError("compose_from_components requires components from at least two Candidates")
    if directive.action == "merge_candidates" and len(directive.target_candidate_ids) < 2:
        raise ValueError("merge_candidates requires at least two Candidates")
    if len(directive.target_candidate_ids) > 1 and directive.action == "select_multiple":
        raise ValueError("multiple selected Candidates are ambiguous; choose keep_parallel or compose_from_components")


def persist_idea_directive(
    workspace_dir: Path,
    *,
    directive: IdeaDirective,
    population: PopulationSnapshot,
) -> str:
    """Write an immutable, fingerprint-bound directive before any state change."""

    store = T4ArtifactStore(workspace_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"ideation/human_directives/{timestamp}_{directive.directive_id}_{directive.action}.json"
    payload = {
        "schema_version": "1.0.0",
        "semantics": "t4_human_directive",
        "directive": model_dump(directive, mode="json"),
        "population_id": population.population_id,
        "population_generation": population.generation,
        "input_fingerprint": population.input_fingerprint,
        "run_config_fingerprint": population.run_config_fingerprint,
        "directive_fingerprint": stable_fingerprint(
            {
                "directive": model_dump(directive, mode="json"),
                "population_id": population.population_id,
                "input_fingerprint": population.input_fingerprint,
                "run_config_fingerprint": population.run_config_fingerprint,
            }
        ),
    }
    store.write_json(path, payload)
    return path


def current_population_context(workspace_dir: Path) -> tuple[PopulationSnapshot, dict[str, CandidateDossier]]:
    """Load the active native population and its Candidate Dossiers."""

    store = T4ArtifactStore(workspace_dir)
    state = store.read_state()
    population = store.read_population(state.current_population_id)
    dossiers: dict[str, CandidateDossier] = {}
    for candidate_id in population.active_candidate_ids:
        matches = sorted(store.path("ideation/candidates").glob(f"{candidate_id}.v*.json"))
        if not matches:
            raise ValueError(f"current T4 population is missing Candidate Dossier {candidate_id}")
        dossiers[candidate_id] = store.read_model(matches[-1].relative_to(store.workspace_dir), CandidateDossier)
    return population, dossiers


def _extract_references(raw: str, candidate_ids: set[str]) -> tuple[list[str], list[str]]:
    candidate_refs: list[str] = []
    components: list[str] = []
    matches: list[tuple[int, str, str]] = []
    for candidate_id in sorted(candidate_ids, key=len, reverse=True):
        pattern = re.compile(rf"(?<![A-Za-z0-9._:-]){re.escape(candidate_id)}(?:-(H\d+|C\d+))?(?![A-Za-z0-9._:-])")
        for match in pattern.finditer(raw):
            matches.append((match.start(), candidate_id, str(match.group(1) or "")))
    for _position, candidate_id, suffix in sorted(matches):
        if candidate_id not in candidate_refs:
            candidate_refs.append(candidate_id)
        if suffix:
            components.append(f"{candidate_id}-{suffix}")
    return candidate_refs, list(dict.fromkeys(components))


def _normalized_action(*, option_id: str, raw: str, proposed_action: str, target_count: int, component_count: int) -> str:
    proposed = proposed_action.strip()
    if proposed in _DIRECTIVE_ACTIONS:
        return proposed
    option_map = {
        "select_or_reframe": "select_candidate" if target_count == 1 else "refine_candidate",
        "merge": "compose_from_components" if component_count else "merge_candidates",
        "new_idea": "refine_candidate",
        "reanalyze": "regenerate_route",
        "continue_evolution": "continue_evolution",
        "keep_parallel": "keep_parallel",
        "pause": "pause",
        "rollback": "rollback",
    }
    if option_id in option_map:
        return option_map[option_id]
    lowered = raw.casefold()
    if any(token in lowered for token in ("暂停", "pause")):
        return "pause"
    if any(token in lowered for token in ("回滚", "rollback", "回到 p")):
        return "rollback"
    if any(token in lowered for token in ("再进化", "下一代", "continue evolution", "another generation")):
        return "continue_evolution"
    if any(token in lowered for token in ("查看", "inspect", "评分", "evidence", "lineage")):
        return "inspect_score" if target_count else "show_more"
    if component_count >= 2:
        return "compose_from_components"
    if target_count >= 2:
        return "select_multiple"
    return "select_candidate" if target_count == 1 else "refine_candidate"


def _requested_rounds(value: object, raw: str) -> int | None:
    if isinstance(value, int) and 0 <= value <= 3:
        return value
    match = re.search(r"(?:rounds?|轮)\s*([0-3])", raw.casefold())
    return int(match.group(1)) if match else None


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _string_map(value: object) -> dict[str, str]:
    return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip() and str(item).strip()} if isinstance(value, dict) else {}
