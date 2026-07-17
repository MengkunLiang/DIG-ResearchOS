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
    "inspect_files",
    "compare_candidates",
    "regenerate_route",
    "change_target_profile",
    "rollback",
    "pause",
    "cancel",
}

_COMPONENT_KINDS = {
    "problem",
    "opportunity",
    "challenged_assumption",
    "core_thesis",
    "mechanism",
    "design",
    "design_or_artifact",
    "contribution",
    "contribution_package",
    "hypothesis",
    "hypothesis_bundle",
    "validation",
    "validation_logic",
    "boundary",
    "boundary_conditions",
    "risk",
    "risks",
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
    if (
        option_id in {"", "t4_directive"}
        and detected_ids
        and not components
        and _bare_candidate_reference_only(raw, detected_ids)
    ):
        joined = "、".join(detected_ids)
        raise ValueError(
            f"你只输入了 {joined}。请说明是“推进 {joined}”、“优化 {joined}”还是“查看 {joined}”。"
        )
    proposed_ids = proposed.get("target_candidate_ids")
    if isinstance(proposed_ids, list):
        detected_ids = list(dict.fromkeys([*detected_ids, *[str(item) for item in proposed_ids if str(item) in candidate_ids]]))
    proposed_components = proposed.get("component_refs")
    if isinstance(proposed_components, list):
        components = list(
            dict.fromkeys(
                [
                    *components,
                    *[
                        str(item).strip()
                        for item in proposed_components
                        if _component_candidate_id(str(item).strip(), candidate_ids)
                    ],
                ]
            )
        )
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
        "change_target_profile",
        "rollback",
    }
    directive = IdeaDirective(
        directive_id=f"DIR-{uuid4().hex[:12]}",
        action=action,
        target_candidate_ids=detected_ids,
        target_family_ids=_string_list(proposed.get("target_family_ids")),
        component_refs=components,
        preserve_genes=_string_list(proposed.get("preserve_genes")),
        donor_genes=_string_map(proposed.get("donor_genes")),
        requested_route=str(proposed.get("requested_route") or proposed.get("route") or _route_from_raw(raw)).strip(),
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
        for candidate_id in [_component_candidate_id(item, candidate_ids)]
        if candidate_id
    ]
    component_missing = [candidate_id for candidate_id in component_candidates if candidate_id not in candidate_ids]
    if component_missing:
        raise ValueError("T4 directive references components from unknown candidates: " + ", ".join(component_missing))
    if directive.action == "select_candidate" and len(directive.target_candidate_ids) != 1:
        raise ValueError("select_candidate requires exactly one complete Candidate")
    if directive.action in {
        "focus_candidate",
        "inspect_score",
        "inspect_evidence",
        "inspect_lineage",
        "inspect_hypotheses",
        "inspect_contributions",
        "inspect_genome",
        "inspect_files",
    } and not directive.target_candidate_ids:
        raise ValueError(f"{directive.action} requires a Candidate ID")
    if directive.action == "compare_candidates" and len(directive.target_candidate_ids) < 2:
        raise ValueError("compare_candidates requires at least two Candidate IDs")
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


def persist_idea_directive_confirmation(
    workspace_dir: Path,
    *,
    directive: IdeaDirective,
    directive_path: str,
    accepted: bool,
    outcome: str,
) -> str:
    """Append the human confirmation outcome without mutating the request."""

    store = T4ArtifactStore(workspace_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"ideation/human_directives/{timestamp}_{directive.directive_id}_confirmation.json"
    store.write_json(
        path,
        {
            "schema_version": "1.0.0",
            "semantics": "t4_human_directive_confirmation",
            "directive_id": directive.directive_id,
            "directive_path": directive_path,
            "action": directive.action,
            "accepted": accepted,
            "outcome": outcome,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
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
    component_pattern = "|".join(
        [r"(?:H|C)\d+", *[re.escape(item) for item in sorted(_COMPONENT_KINDS, key=len, reverse=True)]]
    )
    for candidate_id in sorted(candidate_ids, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9._:-]){re.escape(candidate_id)}(?:[-.]({component_pattern}))?(?![A-Za-z0-9._:-])",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(raw):
            matches.append((match.start(), candidate_id, str(match.group(1) or "")))
    for _position, candidate_id, suffix in sorted(matches):
        if candidate_id not in candidate_refs:
            candidate_refs.append(candidate_id)
        if suffix:
            components.append(f"{candidate_id}-{suffix}")
    return candidate_refs, list(dict.fromkeys(components))


def _component_candidate_id(value: str, candidate_ids: set[str]) -> str | None:
    """Resolve a public component reference without accepting arbitrary text."""

    text = str(value or "").strip()
    for candidate_id in sorted(candidate_ids, key=len, reverse=True):
        if not text.startswith(candidate_id):
            continue
        suffix = text[len(candidate_id) :]
        if not suffix.startswith(("-", ".")):
            continue
        component = suffix[1:]
        if re.fullmatch(r"[HC]\d+", component, flags=re.IGNORECASE) or component.casefold() in _COMPONENT_KINDS:
            return candidate_id
    return None


def _normalized_action(*, option_id: str, raw: str, proposed_action: str, target_count: int, component_count: int) -> str:
    # A plain inspection request is an explicit safety boundary.  The LLM is
    # allowed to help with complex research wording, but it may never turn
    # ``查看 D1`` into a selecting/confirming operation.  Resolve this exact,
    # non-mutating intent before considering an LLM proposal.
    read_only_action = _explicit_read_only_action(raw, target_count=target_count)
    if read_only_action:
        return read_only_action
    # “推进 D1” is a public, user-facing promise: it means choose that
    # completed Candidate for the T4.5 novelty gate, not “focus D1 and run
    # another T4 evolution”.  Resolve this narrow wording before an LLM can
    # reinterpret it as focus_candidate/refine_candidate.
    if _explicit_selection_action(raw, target_count=target_count):
        return "select_candidate"
    proposed = proposed_action.strip()
    if proposed in _DIRECTIVE_ACTIONS:
        return proposed
    option_map = {
        "select_or_reframe": "select_candidate" if target_count == 1 else "refine_candidate",
        "select_candidate": "select_candidate",
        "proceed": "select_candidate",
        "proceed_candidate": "select_candidate",
        "merge": "compose_from_components" if component_count else "merge_candidates",
        "create_crossover": "merge_candidates",
        "crossover": "merge_candidates",
        "compose": "compose_from_components",
        "new_idea": "refine_candidate",
        "reanalyze": "regenerate_route",
        "continue_evolution": "continue_evolution",
        "another_generation": "continue_evolution",
        "focus_evolution": "focus_candidate",
        "keep_parallel": "keep_parallel",
        "show_population": "show_more",
        "show_archive": "show_archive",
        "inspect": "inspect_score",
        "regenerate_route": "regenerate_route",
        "change_orientation": "change_target_profile",
        "change_target_profile": "change_target_profile",
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
    if any(token in lowered for token in ("研究取向", "publication orientation", "target profile", "改成更偏 utd", "改成 ccf", "改成 hybrid")):
        return "change_target_profile"
    # Gate1 is Chinese-first.  These are intent aliases, not a substitute for
    # the later candidate-ID and confirmation checks: all model-changing
    # requests still become a fingerprint-bound directive before execution.
    if any(
        token in lowered
        for token in (
            "再进化",
            "再演化",
            "重新演化",
            "重新进化",
            "继续演化",
            "继续进化",
            "下一代",
            "继续一轮",
            "continue evolution",
            "another generation",
            "evolve again",
        )
    ):
        return "continue_evolution"
    if any(token in lowered for token in ("剩余候选", "剩余 population", "show population", "remaining population", "查看 population")):
        return "show_more"
    if any(token in lowered for token in ("archive", "归档候选", "查看归档")):
        return "show_archive"
    if any(token in lowered for token in ("focus", "优化", "改进", "精修", "简化", "降低", "只优化", "聚焦", "定向优化", "定向演化", "refine")):
        return "focus_candidate"
    if any(token in lowered for token in ("并行", "parallel", "分别保留", "保留多个方向", "并行保留")):
        return "keep_parallel"
    if any(token in lowered for token in ("crossover", "交叉", "组合候选", "合并", "merge", "组合")) and target_count >= 2:
        return "merge_candidates"
    if any(token in lowered for token in ("重新生成", "regenerate", "重跑 route", "route")):
        return "regenerate_route"
    inspection_requested = any(token in lowered for token in ("查看", "view", "inspect", "详情"))
    if any(token in lowered for token in ("比较", "对比", "compare")):
        return "compare_candidates"
    if any(token in lowered for token in ("文件", "产物", "artifact", "files", "路径")) and inspection_requested:
        return "inspect_files" if target_count else "show_more"
    if ("证据" in lowered and inspection_requested) or "evidence" in lowered:
        return "inspect_evidence" if target_count else "show_more"
    if ("谱系" in lowered and inspection_requested) or "lineage" in lowered:
        return "inspect_lineage" if target_count else "show_more"
    if ("假设" in lowered and inspection_requested) or "hypotheses" in lowered:
        return "inspect_hypotheses" if target_count else "show_more"
    if ("贡献" in lowered and inspection_requested) or "contributions" in lowered:
        return "inspect_contributions" if target_count else "show_more"
    if ("基因" in lowered and inspection_requested) or "genome" in lowered:
        return "inspect_genome" if target_count else "show_more"
    if any(token in lowered for token in ("查看", "inspect", "评分", "score")):
        return "inspect_score" if target_count else "show_more"
    if component_count >= 2:
        return "compose_from_components"
    if target_count >= 2:
        return "select_multiple"
    if option_id in {"", "t4_directive"} and target_count == 1:
        return "focus_candidate"
    return "select_candidate" if target_count == 1 else "refine_candidate"


def _bare_candidate_reference_only(raw: str, detected_ids: list[str]) -> bool:
    """Return True when the turn contains only candidate handles and separators."""

    remainder = str(raw or "").strip()
    if not remainder:
        return False
    for candidate_id in sorted(detected_ids, key=len, reverse=True):
        remainder = re.sub(
            rf"(?<![A-Za-z0-9._:-]){re.escape(candidate_id)}(?![A-Za-z0-9._:-])",
            "",
            remainder,
            flags=re.IGNORECASE,
        )
    remainder = re.sub(r"(?i)\b(and|or)\b", "", remainder)
    remainder = re.sub(r"[\s,，、;；/|+&和或与]+", "", remainder)
    return not remainder


def _explicit_read_only_action(raw: str, *, target_count: int) -> str:
    """Return a read-only action for an unambiguously inspection-only turn.

    This deliberately does not classify mixed requests such as “查看 D1 后
    推进它”; those need the normal semantic parser and confirmation.  A user
    who starts with 查看/view/inspect and supplies no mutation verb, however,
    must never reach a confirmation screen.
    """

    lowered = " ".join(str(raw or "").casefold().split())
    if not lowered:
        return ""
    inspection_signal = any(
        token in lowered for token in ("查看", "看一下", "想看", "看看", "详情", "view", "inspect", "show")
    )
    comparison_signal = any(token in lowered for token in ("对比", "比较", "compare"))
    if not (inspection_signal or comparison_signal):
        return ""
    mutation_tokens = (
        "推进", "选择", "选定", "确认", "优化", "修改", "重构", "组合", "合并", "交叉", "演化", "进化",
        "重新生成", "重跑", "保留", "提交", "proceed", "select", "confirm", "refine", "merge", "compose",
        "crossover", "evolve", "regenerate", "rollback",
    )
    if any(token in lowered for token in mutation_tokens):
        return ""
    if comparison_signal:
        return "compare_candidates" if target_count >= 2 else "show_more"
    if any(token in lowered for token in ("文件", "产物", "artifact", "files", "路径")):
        return "inspect_files" if target_count else "show_more"
    if any(token in lowered for token in ("证据", "evidence")):
        return "inspect_evidence" if target_count else "show_more"
    if any(token in lowered for token in ("谱系", "lineage")):
        return "inspect_lineage" if target_count else "show_more"
    if any(token in lowered for token in ("假设", "hypotheses")):
        return "inspect_hypotheses" if target_count else "show_more"
    if any(token in lowered for token in ("贡献", "contributions")):
        return "inspect_contributions" if target_count else "show_more"
    if any(token in lowered for token in ("基因", "genome")):
        return "inspect_genome" if target_count else "show_more"
    # A request to view several complete Candidates is still read-only.  The
    # most useful deterministic presentation is their comparison, never an
    # implicit choice of the first handle.
    return "compare_candidates" if target_count >= 2 else "inspect_score" if target_count else "show_more"


def _explicit_selection_action(raw: str, *, target_count: int) -> bool:
    """Recognize an unambiguous one-Candidate advance request.

    Mixed scientific instructions remain available to the LLM parser, but
    simple public commands such as ``推进 D1`` must preserve the documented
    T4.5 meaning even if a model proposes a T4 focus/evolution action.
    """

    if target_count != 1:
        return False
    lowered = " ".join(str(raw or "").casefold().split())
    if not lowered:
        return False
    starts_selection = lowered.startswith(("推进", "选择", "选定", "进入 t4.5", "进入t4.5", "proceed", "select", "advance"))
    next_stage_selection = any(
        phrase in lowered
        for phrase in (
            "进入下一阶段",
            "进入下一个阶段",
            "用于下一阶段",
            "for the next stage",
            "to the next stage",
            "for t4.5",
            "into t4.5",
        )
    )
    if not (starts_selection or next_stage_selection):
        return False
    alternate_operation_tokens = (
        "优化", "修改", "重构", "演化", "进化", "再探索", "重新生成", "重跑", "合并", "组合", "交叉",
        "refine", "focus", "evolve", "regenerate", "merge", "compose", "crossover",
    )
    return not any(token in lowered for token in alternate_operation_tokens)


def _requested_rounds(value: object, raw: str) -> int | None:
    if isinstance(value, int) and 0 <= value <= 3:
        return value
    match = re.search(r"(?:rounds?|轮)\s*([0-3])", raw.casefold())
    return int(match.group(1)) if match else None


def _route_from_raw(raw: str) -> str:
    """Extract one declared Route alias without introducing project content."""

    aliases = {
        "literature": "evidence_routed_literature",
        "evidence": "evidence_routed_literature",
        "brainstorm": "informed_brainstorm",
        "mechanism challenge": "mechanism_challenge",
        "reverse operation": "reverse_operation",
        "subgroup failure": "subgroup_failure",
        "gap exploration": "gap_exploration",
        "cross-domain": "cross_domain_bridge",
        "cross domain": "cross_domain_bridge",
        "bridge": "cross_domain_bridge",
        "文献": "evidence_routed_literature",
        "头脑风暴": "informed_brainstorm",
        "机制挑战": "mechanism_challenge",
        "逆向": "reverse_operation",
        "子群": "subgroup_failure",
        "缺口": "gap_exploration",
        "跨域": "cross_domain_bridge",
        "跨领域": "cross_domain_bridge",
        "交叉领域": "cross_domain_bridge",
        "跨学科": "cross_domain_bridge",
        "桥接": "cross_domain_bridge",
    }
    lowered = " ".join(str(raw or "").casefold().replace("_", " ").replace("-", " ").split())
    for label, route in aliases.items():
        if label in lowered:
            return route
    return ""


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _string_map(value: object) -> dict[str, str]:
    return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip() and str(item).strip()} if isinstance(value, dict) else {}
