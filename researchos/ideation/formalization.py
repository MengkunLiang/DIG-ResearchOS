"""Unified, orientation-aware T4.5 research formalization contract.

The module deliberately separates researcher-facing formalization from the
novelty audit.  The audit can constrain claims and required baselines, but its
internal labels are not a substitute for a research problem, a method, or an
evaluation design.  ``research_blueprint.yaml`` and ``claim_registry.yaml``
are the durable sources of truth used by the Markdown proposal and the T5
handoff.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

import yaml

from ..schemas.validator import validate_record
from .novelty_verdict import (
    PASSING_FINAL_GATE_VERDICTS,
    extract_final_gate_verdict,
    is_passing_final_gate_verdict,
    normalize_final_gate_verdict,
)
from .t45_semantic_adjudication import accepted_t45_semantic_errors


BLUEPRINT_REL_PATH = "ideation/research_blueprint.yaml"
CLAIM_REGISTRY_REL_PATH = "ideation/claim_registry.yaml"
ORIENTATION_CONFIG_REL_PATH = "ideation/orientation_config.yaml"
ORIENTATION_REVIEW_REL_PATH = "ideation/orientation_review.json"
FORMALIZATION_MANIFEST_REL_PATH = "ideation/post_novelty_formalization.json"
T45_SELECTION_ISOLATION_REL_PATH = "ideation/t45_selection_isolation.json"
T45_SELECTION_HISTORY_DIR = "ideation/t45_selection_history"
T45_REPAIRABLE_WARNING_PREFIX = "T45_REPAIRABLE_WARNING:"
T45_STRUCTURED_SOURCE_PATHS = (
    BLUEPRINT_REL_PATH,
    CLAIM_REGISTRY_REL_PATH,
    "ideation/exp_plan.yaml",
)

# A Gate1 Candidate selection starts a new research-plan lineage. These are
# active, selection-bound T4.5 artifacts, never shared workspace background.
# Archive rather than delete them so the prior plan remains auditable without
# being mistaken for the newly selected Candidate's hypotheses or Proposal.
_T45_FULL_SELECTION_RESET_PATHS = (
    "ideation/novelty_audit.md",
    "ideation/collision_cases.md",
    "ideation/_mechanism_tuples",
    "ideation/_design_rationale_tuples",
    BLUEPRINT_REL_PATH,
    CLAIM_REGISTRY_REL_PATH,
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/research_dossier.json",
    "ideation/contribution_hypothesis_map.yaml",
    "ideation/validation_map.yaml",
    "ideation/kill_criteria.yaml",
    "ideation/proposal",
    ORIENTATION_REVIEW_REL_PATH,
    FORMALIZATION_MANIFEST_REL_PATH,
    T45_SELECTION_ISOLATION_REL_PATH,
)

_T45_FORMALIZATION_RESET_PATHS = tuple(
    path
    for path in _T45_FULL_SELECTION_RESET_PATHS
    if path
    not in {
        "ideation/novelty_audit.md",
        "ideation/collision_cases.md",
        "ideation/_mechanism_tuples",
        "ideation/_design_rationale_tuples",
    }
)


# The three user-facing orientations share one template.  Legacy profile names
# remain readable only as aliases, so a resumed old T4 run never asks the user
# to choose again.
_PROFILE_ALIASES = {
    "utd": "utd",
    "utd_is": "utd",
    "management_is": "utd",
    "is": "utd",
    "ccf_a": "ccf_a",
    "ccf": "ccf_a",
    "ccf_cs": "ccf_a",
    "technical_cs": "ccf_a",
    "hybrid": "hybrid",
    "both": "hybrid",
    "custom": "hybrid",
}

_ORIENTATION_SPECS: dict[str, dict[str, Any]] = {
    "ccf_a": {
        "proposal_weights": {
            "problem_and_motivation": 0.10,
            "technical_gap_and_challenges": 0.20,
            "methodology": 0.25,
            "design_rationale": 0.15,
            "evaluation": 0.20,
            "theory_and_implications": 0.10,
        },
        "guidance": (
            "Prioritize a clearly abstracted computational problem, substantive technical novelty, "
            "rigorous methodology and comprehensive evaluation. Real-world motivation and design rationale "
            "remain mandatory. Do not reduce practical significance to a generic application paragraph. "
            "Management or IS theory is optional and should only be used when it directly informs the technical "
            "design, mechanism or boundary conditions."
        ),
        "minimum_words": {"proposal": 1500, "claims": 800},
        "review_focus": ("technical_novelty", "technical_completeness", "evaluation_rigor", "mechanism_validation"),
    },
    "utd": {
        "proposal_weights": {
            "problem_and_motivation": 0.15,
            "technical_gap_and_challenges": 0.10,
            "methodology": 0.20,
            "design_rationale": 0.20,
            "evaluation": 0.15,
            "theory_and_implications": 0.20,
        },
        "guidance": (
            "Prioritize a significant digital phenomenon, a substantive technical artifact, theory-informed design "
            "rationale and evidence connecting technical properties with user, organizational, platform or market "
            "outcomes. Technical contribution is mandatory. Do not reduce the artifact to an application of an "
            "existing LLM, model or API."
        ),
        "minimum_words": {"proposal": 1700, "claims": 900},
        "review_focus": ("technical_novelty", "design_rationale", "theoretical_or_design_value", "practical_significance"),
    },
    "hybrid": {
        "proposal_weights": {
            "problem_and_motivation": 0.15,
            "technical_gap_and_challenges": 0.15,
            "methodology": 0.20,
            "design_rationale": 0.20,
            "evaluation": 0.20,
            "theory_and_implications": 0.10,
        },
        "guidance": (
            "Balance substantive technical novelty with theory, design knowledge and real-world significance. The "
            "technical and IS contributions must address one unified problem and must be connected by explicit "
            "cross-level mechanisms. Do not write two parallel and disconnected research stories."
        ),
        "minimum_words": {"proposal": 1900, "claims": 1000},
        "review_focus": ("technical_novelty", "design_rationale", "evaluation_rigor", "theoretical_or_design_value", "practical_significance", "cross_level_integration"),
    },
}


# These are seven argument functions, not a compulsory seven-heading layout.
# The canonical headings make a first draft easy to navigate, but a strong
# proposal may merge adjacent functions when that produces a more continuous
# argument.  Surface-form departures are reviewed semantically below.
PROPOSAL_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("motivation", ("research motivation and core problem", "研究动机与核心问题", "研究背景与核心问题")),
    ("gap_and_challenges", ("prior research, gap and key challenges", "现有研究、缺口与关键挑战", "文献缺口与关键挑战")),
    ("approach", ("proposed approach and design rationale", "技术方案与设计理由", "研究方法与设计理由")),
    ("claims", ("research questions, claims and hypotheses", "研究问题、研究主张与假设", "research claims and hypotheses")),
    ("evaluation", ("research design and evaluation", "研究设计与评测", "研究设计与评估")),
    ("contributions", ("expected contributions and implications", "预期贡献与现实含义", "预期贡献与影响")),
    ("risks", ("risks, limitations and execution plan", "风险、局限与执行计划", "风险、局限与实施计划")),
)

_MINIMUM_PROPOSAL_SUBSTANCE = 600

_AUDIT_LANGUAGE = re.compile(
    r"(?i)\b(?:t4\.5|level\s*[0-3]|true_collision|mechanism_collision|pass_to_experiment|"
    r"pass_with_required_baselines|candidate[_ ]?id|selection[_ ]?fingerprint|lineage[_ ]?hash|"
    r"proposed_not_verified)\b"
)

# These are runtime/provenance labels, not academic prose.  A Proposal should
# state a falsifiable prediction or a concrete pre-execution check instead of
# exposing a workflow flag such as ``verification_required`` or ``待验证``.
_INTERNAL_VERIFICATION_LABEL = re.compile(
    r"(?i)\b(?:verification_required|requires_verification|proposed_not_verified|"
    r"to_be_verified|pending_verification|evidence[_ ]?(?:level|status))\b|"
    r"\b(?:to be verified|pending verification)\b|待(?:验证|核验|补证)|(?:需要|需)核验|证据(?:等级|状态)"
)


def canonical_orientation(profile_type: str | None) -> str:
    """Map T4's durable profile spelling to one of the three T4.5 lenses."""

    return _PROFILE_ALIASES.get(str(profile_type or "").strip().casefold(), "hybrid")


def orientation_spec(profile_type: str | None) -> dict[str, Any]:
    """Return a copyable orientation configuration for a formalization run."""

    key = canonical_orientation(profile_type)
    spec = _ORIENTATION_SPECS[key]
    return {
        "profile_type": key,
        "proposal_weights": dict(spec["proposal_weights"]),
        "guidance": str(spec["guidance"]),
        "minimum_words": dict(spec["minimum_words"]),
        "review_focus": list(spec["review_focus"]),
    }


def _normalize_formalization_language(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold().replace("_", "-")
    if normalized in {"zh", "zh-cn", "zh-hans", "chinese", "中文", "汉语"}:
        return "zh"
    if normalized in {"en", "en-us", "en-gb", "english", "英文"}:
        return "en"
    return None


def _project_formalization_language(workspace: Path) -> str | None:
    project = _read_yaml_mapping(Path(workspace) / "project.yaml")
    metadata = project.get("metadata") if isinstance(project.get("metadata"), dict) else {}
    configured = (
        _normalize_formalization_language(project.get("formalization_language"))
        or _normalize_formalization_language(metadata.get("formalization_language"))
    )
    if configured is not None:
        return configured
    # A project can explicitly select English for an eventual venue even when
    # its working brief is Chinese.  In the absence of that user-owned choice,
    # use the language already present in the research direction/question only
    # for a *new* formalization.  Applying that inference to an existing
    # accepted English package would retroactively invalidate its hypotheses,
    # Proposal, review, and already-compiled T5 handoff merely because the
    # project brief happens to be bilingual.
    existing_research_prose = (
        Path(workspace) / "ideation" / "hypotheses.md",
        Path(workspace) / "ideation" / "proposal" / "research_proposal.md",
    )
    if any(path.is_file() and path.stat().st_size > 0 for path in existing_research_prose):
        return None
    researcher_text = "\n".join(
        str(project.get(key) or "")
        for key in ("research_direction", "research_question")
    )
    if re.search(r"[\u3400-\u9fff]", researcher_text):
        return "zh"
    return None


def load_orientation_configuration(workspace: Path) -> dict[str, Any]:
    """Load T4's chosen orientation without introducing a second T4.5 choice."""

    workspace = Path(workspace)
    existing = _read_yaml_mapping(workspace / ORIENTATION_CONFIG_REL_PATH)
    if existing and str(existing.get("profile_type") or "").strip():
        profile = canonical_orientation(str(existing.get("profile_type")))
        result = orientation_spec(profile)
        result["target_venues"] = _string_list(existing.get("target_venues"))
        result["user_instruction"] = str(existing.get("user_instruction") or "").strip()
        # project.yaml is the user-owned setting; orientation_config.yaml is
        # a generated derivative and must not keep an earlier default after a
        # researcher changes the intended T4.5 prose language.
        result["formalization_language"] = (
            _project_formalization_language(workspace)
            or _normalize_formalization_language(existing.get("formalization_language"))
            or "en"
        )
        result["source"] = str(existing.get("source") or "ideation/orientation_config.yaml")
        return result

    run_config = _read_json_mapping(workspace / "ideation" / "t4_run_config.json")
    target = run_config.get("target_profile") if isinstance(run_config.get("target_profile"), dict) else {}
    profile = canonical_orientation(str(target.get("profile_type") or ""))
    result = orientation_spec(profile)
    result["target_venues"] = _string_list(target.get("target_venues"))
    result["user_instruction"] = str(target.get("user_instruction") or "").strip()
    result["formalization_language"] = _project_formalization_language(workspace) or "en"
    result["source"] = "ideation/t4_run_config.json" if target else "default_hybrid"
    return result


def persist_orientation_configuration(workspace: Path) -> dict[str, Any]:
    """Materialize the T4 orientation next to the T4.5 source artifacts."""

    config = load_orientation_configuration(workspace)
    payload = {
        "semantics": "t45_orientation_configuration",
        "profile_type": config["profile_type"],
        "target_venues": config["target_venues"],
        "user_instruction": config["user_instruction"],
        "formalization_language": config["formalization_language"],
        "proposal_weights": config["proposal_weights"],
        "guidance": config["guidance"],
        "review_focus": config["review_focus"],
        "source": config["source"],
    }
    _write_yaml(Path(workspace) / ORIENTATION_CONFIG_REL_PATH, payload)
    return payload


def normalize_research_blueprint_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Canonicalize lossless legacy/model aliases before blueprint validation.

    The structured writer used to accept arbitrary objects for rationale and
    alternative entries, while the cross-artifact validator required specific
    fields.  Normalize only unambiguous aliases so historical workspaces stay
    readable and every newly written blueprint has one durable wire format.
    """

    normalized = deepcopy(payload)
    changes: list[str] = []
    approach = normalized.get("proposed_approach")
    if not isinstance(approach, dict):
        return normalized, changes

    raw_rationales = approach.get("design_rationales")
    rationales = raw_rationales if isinstance(raw_rationales, list) else []
    canonical_rationales: list[dict[str, Any]] = []
    covered_components: set[str] = set()
    for item in rationales:
        if not isinstance(item, dict):
            canonical_rationales.append(item)
            continue
        refs: list[str] = []
        for key in ("component_id", "component_ref"):
            value = str(item.get(key) or "").strip()
            if value:
                refs.append(value)
        refs.extend(_string_list(item.get("component_refs")))
        refs = list(dict.fromkeys(refs))
        rationale = item.get("rationale")
        if not _substantive_text(rationale) and _substantive_text(item.get("design_rationale")):
            rationale = item.get("design_rationale")
            changes.append("design_rationales[].design_rationale -> rationale")
        if not refs:
            canonical_rationales.append(item)
            continue
        for component_id in refs:
            canonical = dict(item)
            canonical["component_id"] = component_id
            if rationale is not None:
                canonical["rationale"] = rationale
            canonical.pop("component_ref", None)
            canonical.pop("component_refs", None)
            canonical.pop("design_rationale", None)
            canonical_rationales.append(canonical)
            covered_components.add(component_id)
        if "component_ref" in item or "component_refs" in item:
            changes.append("design_rationales component reference -> component_id")

    # Older Formalizers sometimes placed a complete rationale directly on the
    # component. It is researcher-authored information, not a value we invent,
    # so materialize the canonical cross-reference deterministically.
    for component in _dict_list(approach.get("components")):
        component_id = str(component.get("id") or "").strip()
        rationale = component.get("design_rationale")
        if component_id and component_id not in covered_components and _substantive_text(rationale):
            canonical_rationales.append({"component_id": component_id, "rationale": rationale})
            covered_components.add(component_id)
            changes.append("components[].design_rationale -> design_rationales[]")
    if canonical_rationales != rationales:
        approach["design_rationales"] = canonical_rationales

    alternatives = approach.get("alternatives_considered")
    if isinstance(alternatives, list):
        canonical_alternatives: list[Any] = []
        for item in alternatives:
            if not isinstance(item, dict):
                canonical_alternatives.append(item)
                continue
            canonical = dict(item)
            if not _substantive_text(canonical.get("alternative")):
                for alias in ("simpler_alternative", "approach"):
                    if _substantive_text(canonical.get(alias)):
                        canonical["alternative"] = canonical.get(alias)
                        canonical.pop(alias, None)
                        changes.append(f"alternatives_considered[].{alias} -> alternative")
                        break
            if not _substantive_text(canonical.get("reason_not_sufficient")):
                for alias in ("why_insufficient", "reason"):
                    if _substantive_text(canonical.get(alias)):
                        canonical["reason_not_sufficient"] = canonical.get(alias)
                        canonical.pop(alias, None)
                        changes.append(f"alternatives_considered[].{alias} -> reason_not_sufficient")
                        break
            canonical_alternatives.append(canonical)
        if canonical_alternatives != alternatives:
            approach["alternatives_considered"] = canonical_alternatives

    risks = normalized.get("risks")
    if isinstance(risks, dict):
        for category in ("novelty_risks", "technical_risks", "data_or_experimental_risks"):
            entries = risks.get(category)
            if not isinstance(entries, list):
                continue
            canonical_entries: list[Any] = []
            for item in entries:
                if not isinstance(item, dict):
                    canonical_entries.append(item)
                    continue
                canonical = dict(item)
                if not _substantive_text(canonical.get("risk")) and _substantive_text(canonical.get("description")):
                    canonical["risk"] = canonical.pop("description")
                    changes.append(f"risks.{category}[].description -> risk")
                canonical_entries.append(canonical)
            if canonical_entries != entries:
                risks[category] = canonical_entries
    return normalized, list(dict.fromkeys(changes))


def canonicalize_research_blueprint_file(workspace: Path) -> list[str]:
    """Persist lossless blueprint field-name migrations for the active T4.5 run.

    This is intentionally limited to aliases whose values map one-to-one onto
    the canonical contract. It never creates a rationale, an alternative, or
    any research content that the model did not already supply.
    """

    path = Path(workspace) / BLUEPRINT_REL_PATH
    raw = _read_yaml_mapping(path)
    if not raw:
        return []
    normalized, changes = normalize_research_blueprint_payload(raw)
    if changes and normalized != raw:
        _write_yaml(path, normalized)
    return changes


def reset_t45_artifacts_for_new_selection(
    workspace: Path,
    *,
    candidate_id: str,
    selection_fingerprint: str,
) -> dict[str, Any]:
    """Archive every active T4.5 package before a new Gate1 choice begins.

    A new Candidate is a new research-plan lineage even if it happens to share
    a topic or a compact hypothesis label with a prior Candidate. Keeping the
    previous active audit or Proposal at its canonical path lets later agents
    and T5 confuse old content with the current choice. The old package stays
    inspectable under ``ideation/t45_selection_history/``; only its active
    authority is revoked.
    """

    workspace = Path(workspace)
    previous_candidate_id, previous_fingerprint = _current_selected_identity(workspace)
    archive = _archive_t45_paths(
        workspace,
        paths=_T45_FULL_SELECTION_RESET_PATHS,
        reason="new_gate1_selection_supersedes_active_t45_package",
        current_candidate_id=candidate_id,
        current_selection_fingerprint=selection_fingerprint,
        previous_candidate_id=previous_candidate_id,
        previous_selection_fingerprint=previous_fingerprint,
    )
    context = _write_t45_selection_isolation(
        workspace,
        candidate_id=candidate_id,
        selection_fingerprint=selection_fingerprint,
        status="pending_novelty_audit",
        reason="new_gate1_selection",
        archive=archive,
    )
    return context


def ensure_current_t45_selection_isolation(workspace: Path) -> dict[str, Any] | None:
    """Migrate a resumed old selection to the current isolation contract.

    This only handles workspaces created before Gate1 isolation existed. A
    mismatched legacy manifest is decisive evidence that its own receipt cannot
    authorize the current Candidate. A resumed Formalizer can nevertheless
    have already written new structured sources after the selection timestamp,
    so archive only artifacts that predate the current selection. A newer
    audit remains active: it is the current selection's required source
    boundary and must not be discarded during a formalization resume.
    """

    workspace = Path(workspace)
    candidate_id, selection_fingerprint = _current_selected_identity(workspace)
    if not candidate_id or not selection_fingerprint:
        return None

    current_context = _read_json_mapping(workspace / T45_SELECTION_ISOLATION_REL_PATH)
    if _selection_identity_matches(current_context, candidate_id, selection_fingerprint):
        return current_context

    selected_path = workspace / "ideation" / "selected" / "selected_candidate.json"
    selection_mtime = selected_path.stat().st_mtime if selected_path.is_file() else 0.0
    manifest = _read_json_mapping(workspace / FORMALIZATION_MANIFEST_REL_PATH)
    manifest_mismatch = bool(manifest) and not _selection_identity_matches(
        manifest,
        candidate_id,
        selection_fingerprint,
    )
    stale_paths = [
        path
        for path in _T45_FORMALIZATION_RESET_PATHS
        if (candidate := workspace / path).exists()
        and candidate.stat().st_mtime <= selection_mtime
    ]
    archive: dict[str, Any] | None = None
    if stale_paths:
        archive = _archive_t45_paths(
            workspace,
            paths=tuple(stale_paths),
            reason=(
                "legacy_formalization_manifest_does_not_match_current_selection"
                if manifest_mismatch
                else "formalization_artifacts_predate_current_selection"
            ),
            current_candidate_id=candidate_id,
            current_selection_fingerprint=selection_fingerprint,
            previous_candidate_id=str(manifest.get("candidate_id") or "").strip(),
            previous_selection_fingerprint=str(manifest.get("selection_fingerprint") or "").strip(),
        )
    return _write_t45_selection_isolation(
        workspace,
        candidate_id=candidate_id,
        selection_fingerprint=selection_fingerprint,
        status="pending_formalization",
        reason="legacy_or_resumed_selection_isolation",
        archive=archive,
    )


def validate_t45_selection_isolation(
    workspace: Path,
    *,
    require_accepted: bool,
) -> tuple[bool, str | None]:
    """Ensure an active T4.5 package is bound to the current Gate1 choice."""

    workspace = Path(workspace)
    candidate_id, selection_fingerprint = _current_selected_identity(workspace)
    if not candidate_id or not selection_fingerprint:
        return False, "Current T4.5 selection is missing candidate_id or selection_fingerprint"
    context = _read_json_mapping(workspace / T45_SELECTION_ISOLATION_REL_PATH)
    if not _selection_identity_matches(context, candidate_id, selection_fingerprint):
        return False, "T4.5 selection-isolation receipt does not match the current selected Candidate"
    if require_accepted and str(context.get("status") or "").strip() != "accepted_for_t5":
        return False, "T4.5 selection-isolation receipt is not accepted for the current Candidate"
    return True, None


def _current_selected_identity(workspace: Path) -> tuple[str, str]:
    selected = _read_json_mapping(Path(workspace) / "ideation" / "selected" / "selected_candidate.json")
    candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    candidate_id = str(selected.get("candidate_id") or candidate.get("id") or "").strip()
    selection_fingerprint = str(selected.get("selection_fingerprint") or "").strip()
    return candidate_id, selection_fingerprint


def _selection_identity_matches(
    payload: dict[str, Any],
    candidate_id: str,
    selection_fingerprint: str,
) -> bool:
    return bool(
        payload
        and str(payload.get("candidate_id") or "").strip() == candidate_id
        and str(payload.get("selection_fingerprint") or "").strip() == selection_fingerprint
    )


def _archive_t45_paths(
    workspace: Path,
    *,
    paths: tuple[str, ...],
    reason: str,
    current_candidate_id: str,
    current_selection_fingerprint: str,
    previous_candidate_id: str,
    previous_selection_fingerprint: str,
) -> dict[str, Any]:
    workspace = Path(workspace)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    prior_label = re.sub(r"[^A-Za-z0-9._-]+", "_", previous_candidate_id or "unbound")[:80]
    archive_root = workspace / T45_SELECTION_HISTORY_DIR / f"{timestamp}_{prior_label}"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for rel_path in paths:
        source = workspace / rel_path
        if not source.exists():
            continue
        destination = archive_root / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        archived.append(rel_path)
    receipt = {
        "schema_version": "1.0.0",
        "semantics": "t45_selection_supersession_archive",
        "reason": reason,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "previous_candidate_id": previous_candidate_id,
        "previous_selection_fingerprint": previous_selection_fingerprint,
        "current_candidate_id": current_candidate_id,
        "current_selection_fingerprint": current_selection_fingerprint,
        "archived_paths": archived,
    }
    _write_json(archive_root / "selection_supersession_receipt.json", receipt)
    return {
        "root": str(archive_root.relative_to(workspace)),
        "archived_paths": archived,
        "reason": reason,
    }


def _write_t45_selection_isolation(
    workspace: Path,
    *,
    candidate_id: str,
    selection_fingerprint: str,
    status: str,
    reason: str,
    archive: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "1.0.0",
        "semantics": "t45_selection_isolation",
        "candidate_id": candidate_id,
        "selection_fingerprint": selection_fingerprint,
        "status": status,
        "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if archive is not None:
        payload["archive"] = archive
    _write_json(Path(workspace) / T45_SELECTION_ISOLATION_REL_PATH, payload)
    return payload


def validate_blueprint_and_claim_registry(workspace: Path) -> tuple[bool, str | None]:
    """Validate structured formalization sources and their cross-artifact links."""

    workspace = Path(workspace)
    blueprint, error = _load_structured(workspace / BLUEPRINT_REL_PATH, "research_blueprint")
    if error:
        return False, error
    registry, error = _load_structured(workspace / CLAIM_REGISTRY_REL_PATH, "claim_registry")
    if error:
        return False, error
    assert blueprint is not None and registry is not None

    configured = load_orientation_configuration(workspace)
    blueprint_orientation = _nested_text(blueprint, "orientation", "profile_type")
    registry_orientation = str(registry.get("orientation") or "").strip()
    if blueprint_orientation != configured["profile_type"]:
        return False, (
            "research_blueprint.yaml orientation.profile_type does not match T4 orientation "
            f"({blueprint_orientation or 'missing'} != {configured['profile_type']})"
        )
    if registry_orientation != configured["profile_type"]:
        return False, (
            "claim_registry.yaml orientation does not match T4 orientation "
            f"({registry_orientation or 'missing'} != {configured['profile_type']})"
        )

    challenges = _dict_list(_nested_value(blueprint, "technical_problem", "key_challenges"))
    challenge_ids = [str(item.get("id") or "").strip() for item in challenges]
    if len(set(challenge_ids)) != len(challenge_ids):
        return False, "research_blueprint.yaml key_challenges contains duplicate challenge IDs"
    components = _dict_list(_nested_value(blueprint, "proposed_approach", "components"))
    component_ids = [str(item.get("id") or "").strip() for item in components]
    if len(set(component_ids)) != len(component_ids):
        return False, "research_blueprint.yaml proposed_approach.components contains duplicate component IDs"
    unknown_challenge_refs = sorted(
        {
            str(ref).strip()
            for component in components
            for ref in _string_list(component.get("challenge_refs"))
            if str(ref).strip() not in challenge_ids
        }
    )
    if unknown_challenge_refs:
        return False, "Component references unknown challenge IDs: " + ", ".join(unknown_challenge_refs)
    addressed_challenge_ids = {
        str(ref).strip()
        for component in components
        for ref in _string_list(component.get("challenge_refs"))
        if str(ref).strip()
    }
    unaddressed_challenges = sorted(set(challenge_ids) - addressed_challenge_ids)
    if unaddressed_challenges:
        return False, "Key challenges lack a linked technical component: " + ", ".join(unaddressed_challenges)
    rationales = _dict_list(_nested_value(blueprint, "proposed_approach", "design_rationales"))
    rationale_refs = {
        str(item.get("component_id") or item.get("component_ref") or "").strip()
        for item in rationales
        if str(item.get("component_id") or item.get("component_ref") or "").strip()
    }
    missing_rationales = sorted(set(component_ids) - rationale_refs)
    if missing_rationales:
        return False, "Technical components lack a design rationale: " + ", ".join(missing_rationales)
    alternatives = _dict_list(_nested_value(blueprint, "proposed_approach", "alternatives_considered"))
    if not any(
        _substantive_text(item.get("alternative"))
        and _substantive_text(item.get("reason_not_sufficient") or item.get("reason"))
        for item in alternatives
    ):
        return False, "research_blueprint.yaml must explain one simpler alternative and why it is insufficient"

    claims = _dict_list(registry.get("claims"))
    claim_ids = [str(item.get("id") or "").strip() for item in claims]
    if len(set(claim_ids)) != len(claim_ids):
        return False, "claim_registry.yaml contains duplicate active claim IDs"
    active_ids = _string_list(_nested_value(blueprint, "research_claims", "active_claim_ids"))
    if set(active_ids) != set(claim_ids):
        missing = sorted(set(active_ids) - set(claim_ids))
        extra = sorted(set(claim_ids) - set(active_ids))
        detail = []
        if missing:
            detail.append("missing registry entries: " + ", ".join(missing))
        if extra:
            detail.append("unlisted registry entries: " + ", ".join(extra))
        return False, "Blueprint active_claim_ids and claim_registry claims differ (" + "; ".join(detail) + ")"
    unknown_components = sorted(
        {
            str(ref).strip()
            for claim in claims
            for ref in _string_list(claim.get("related_components"))
            if str(ref).strip() not in component_ids
        }
    )
    if unknown_components:
        return False, "Claim references unknown technical components: " + ", ".join(unknown_components)

    if configured["profile_type"] == "hybrid":
        links = _dict_list(_nested_value(blueprint, "research_claims", "cross_level_links"))
        registry_links = [str(item.get("cross_level_relation") or "").strip() for item in claims]
        if not links and not any(registry_links):
            return False, "Hybrid formalization needs an explicit technical-to-real-world cross-level link"
    if configured["profile_type"] == "utd" and not any(
        str(item.get("claim_type") or "") in {"technical", "algorithmic", "system"} for item in claims
    ):
        return False, "UTD formalization must include a substantive technical, algorithmic, or system claim"
    if configured["profile_type"] == "ccf_a" and not any(
        str(item.get("claim_type") or "") in {"technical", "algorithmic", "system", "robustness", "generalization"}
        for item in claims
    ):
        return False, "CCF-A formalization must include a substantive technical or robustness claim"
    evaluation = blueprint.get("evaluation") if isinstance(blueprint.get("evaluation"), dict) else {}
    # These four fields establish an executable comparison. Component test
    # coverage is checked separately across ablations/mechanism tests.
    # Robustness, efficiency and field validation are conditional on what the
    # actual design claims, so an empty stable array is valid for those slots.
    for key in (
        "datasets_or_setting",
        "baselines",
        "primary_metrics",
        "main_tests",
    ):
        items = _dict_list(evaluation.get(key))
        if not any(_substantive_mapping(item) for item in items):
            return False, f"research_blueprint.yaml evaluation.{key} lacks a substantive planned item"
    risks = blueprint.get("risks") if isinstance(blueprint.get("risks"), dict) else {}
    risk_entries = [
        item
        for key in ("novelty_risks", "technical_risks", "data_or_experimental_risks")
        for item in _dict_list(risks.get(key))
    ]
    if not any(
        _substantive_text(item.get("risk"))
        and _substantive_text(item.get("mitigation") or item.get("fallback") or item.get("response"))
        for item in risk_entries
    ):
        return False, "research_blueprint.yaml must pair at least one material project risk with mitigation or fallback"

    contributions = blueprint.get("contributions") if isinstance(blueprint.get("contributions"), dict) else {}
    if configured["profile_type"] in {"utd", "hybrid"}:
        if not any(_substantive_mapping(item) for item in _dict_list(contributions.get("theoretical_or_design"))):
            return False, f"{configured['profile_type']} formalization requires a substantive theoretical or design contribution"
        if not any(_substantive_mapping(item) for item in _dict_list(contributions.get("practical_or_managerial"))):
            return False, f"{configured['profile_type']} formalization requires a substantive practical or managerial contribution"
    return True, None


def collect_t45_structured_source_errors(workspace: Path) -> list[str]:
    """Return all independently actionable T4.5 source-contract failures.

    The public validator still exposes one stable first error for callers that
    need a boolean contract.  The Formalizer, however, must see independent
    errors together.  The previous fail-fast implementation made a model fix
    a blueprint rationale, discover the missing component tests only on the
    next turn, then discover a claim-to-experiment gap after that.  Those are
    separate properties of the same research contract and should be repaired
    in one structured write whenever their source data is readable.

    This deliberately does not try to diagnose dependent schema failures.  A
    malformed YAML object has to be repaired before cross-artifact reasoning
    is trustworthy, while a readable, schema-valid object can safely expose
    several independent consistency gaps at once.
    """

    workspace = Path(workspace)
    errors: list[str] = []
    blueprint_ok, blueprint_error = validate_blueprint_and_claim_registry(workspace)
    if not blueprint_ok and blueprint_error:
        errors.append(str(blueprint_error))

    blueprint, blueprint_load_error = _load_structured(workspace / BLUEPRINT_REL_PATH, "research_blueprint")
    registry, registry_load_error = _load_structured(workspace / CLAIM_REGISTRY_REL_PATH, "claim_registry")
    exp_plan, exp_plan_error = _load_structured(workspace / "ideation" / "exp_plan.yaml", "exp_plan")

    # ``validate_blueprint_and_claim_registry`` reports one stable first
    # error for compatibility.  The Formalizer needs all missing files on a
    # fresh run, however: otherwise it is told to repair only the blueprint,
    # then discovers a missing registry and plan in separate turns.  Keep the
    # stable first error, and append the other independently unreadable
    # sources once each.
    if blueprint_load_error:
        errors.append(str(blueprint_load_error))
    if registry_load_error:
        errors.append(str(registry_load_error))
    if exp_plan_error:
        errors.append(str(exp_plan_error))
    if blueprint_load_error or registry_load_error or exp_plan_error:
        return list(dict.fromkeys(errors))
    assert blueprint is not None and registry is not None and exp_plan is not None

    claim_ids = [str(item.get("id") or "").strip() for item in _dict_list(registry.get("claims"))]
    mapped_claim_ids = _exp_plan_claim_ids(exp_plan)
    unmapped = [claim_id for claim_id in claim_ids if claim_id and claim_id not in mapped_claim_ids]
    if unmapped:
        errors.append("Experiment plan has no experiment mapped to active claims: " + ", ".join(unmapped))

    components = _dict_list(_nested_value(blueprint, "proposed_approach", "components"))
    component_ids = {str(item.get("id") or "").strip() for item in components if str(item.get("id") or "").strip()}
    evaluation = blueprint.get("evaluation") if isinstance(blueprint.get("evaluation"), dict) else {}
    tested_components = _component_test_ids(evaluation.get("ablations")) | _component_test_ids(evaluation.get("mechanism_tests"))
    missing_component_tests = sorted(component_ids - tested_components)
    if missing_component_tests:
        errors.append(
            "research_blueprint.yaml evaluation.ablations or evaluation.mechanism_tests "
            "lacks a component_id/component_ref for: " + ", ".join(missing_component_tests)
        )

    return list(dict.fromkeys(errors))


def t45_structured_source_initialization_state(workspace: Path) -> tuple[bool, list[str]]:
    """Identify the normal blank-slate opening of T4.5 formalization.

    A selected Candidate intentionally has no blueprint, claim registry, or
    experiment plan yet.  Treating that opening as a failed repair is both
    misleading in the CLI and can induce an LLM to ``read_file`` paths that it
    has just been told do not exist.  This helper is deliberately narrow:
    partial packages remain ordinary recovery work and retain the strict
    shared-contract validator.
    """

    root = Path(workspace)
    missing = [path for path in T45_STRUCTURED_SOURCE_PATHS if not (root / path).is_file()]
    return len(missing) == len(T45_STRUCTURED_SOURCE_PATHS), missing


def validate_t45_structured_sources(workspace: Path) -> tuple[bool, str | None]:
    """Validate the three structured sources required before T4.5 prose.

    Callers that require the historical boolean API receive the first stable
    error.  ``collect_t45_structured_source_errors`` remains available to the
    Formalizer's read-only checkpoint so it can repair independent failures in
    one pass rather than discovering them serially.
    """

    errors = collect_t45_structured_source_errors(workspace)
    return (True, None) if not errors else (False, errors[0])


def validate_t45_formalization_core(
    workspace: Path,
    *,
    accepted_semantic_errors: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Validate structured sources and the researcher-facing claims document."""

    workspace = Path(workspace)
    accepted_semantic_errors = (
        accepted_t45_semantic_errors(workspace)
        if accepted_semantic_errors is None
        else accepted_semantic_errors
    )
    ok, error = validate_t45_structured_sources(workspace)
    if not ok:
        return ok, error
    registry = _read_yaml_mapping(workspace / CLAIM_REGISTRY_REL_PATH)
    orientation = load_orientation_configuration(workspace)

    hypotheses_path = workspace / "ideation" / "hypotheses.md"
    if not hypotheses_path.is_file():
        return False, "Missing ideation/hypotheses.md"
    hypothesis_error = validate_claims_markdown(
        hypotheses_path.read_text(encoding="utf-8", errors="replace"),
        registry=registry,
        orientation=orientation,
        accepted_semantic_errors=accepted_semantic_errors,
    )
    if hypothesis_error:
        return False, hypothesis_error
    return True, None


def collect_t45_semantic_errors(workspace: Path) -> list[str]:
    """Return every current prose-only error eligible for LLM adjudication.

    This is intentionally not a second validator and never returns a hard
    error.  The caller can therefore ask one independent reviewer to assess a
    complete set of ambiguous language failures from the same current source
    package, rather than serialising one keyword false-negative per repair
    loop.  If a prerequisite hard check is not satisfied, the function returns
    no candidates and the deterministic validator remains the only authority.
    """

    workspace = Path(workspace)
    structured_ok, _structured_error = validate_t45_structured_sources(workspace)
    if not structured_ok:
        return []
    blueprint = _read_yaml_mapping(workspace / BLUEPRINT_REL_PATH)
    registry = _read_yaml_mapping(workspace / CLAIM_REGISTRY_REL_PATH)
    orientation = load_orientation_configuration(workspace)
    hypotheses_path = workspace / "ideation" / "hypotheses.md"
    if not hypotheses_path.is_file():
        return []
    hypothesis_hard_error, hypothesis_errors = _claims_markdown_errors(
        hypotheses_path.read_text(encoding="utf-8", errors="replace"),
        registry=registry,
        orientation=orientation,
    )
    if hypothesis_hard_error:
        return []

    proposal_path = workspace / "ideation" / "proposal" / "research_proposal.md"
    if not proposal_path.is_file():
        return list(dict.fromkeys(hypothesis_errors))
    proposal_hard_error, proposal_errors = _proposal_text_errors(
        proposal_path.read_text(encoding="utf-8", errors="replace"),
        blueprint=blueprint,
        registry=registry,
        orientation=orientation,
    )
    if proposal_hard_error:
        return list(dict.fromkeys(hypothesis_errors))
    return list(dict.fromkeys([*hypothesis_errors, *proposal_errors]))


def validate_claims_markdown(
    text: str,
    *,
    registry: dict[str, Any],
    orientation: dict[str, Any],
    accepted_semantic_errors: set[str] | None = None,
) -> str | None:
    """Check that researcher-facing claims are complete rather than audit headings."""

    hard_error, semantic_errors = _claims_markdown_errors(
        text,
        registry=registry,
        orientation=orientation,
    )
    return hard_error or _first_unadjudicated_error(semantic_errors, accepted_semantic_errors)


def _claims_markdown_errors(
    text: str,
    *,
    registry: dict[str, Any],
    orientation: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Separate non-negotiable claim-document checks from prose heuristics."""

    if not re.search(r"(?im)^#\s*(?:Research Claims and Hypotheses|研究主张与假设)\s*$", text):
        return "hypotheses.md must start with '# Research Claims and Hypotheses' or '# 研究主张与假设'", []
    verification_labels = _INTERNAL_VERIFICATION_LABEL.findall(text)
    if verification_labels:
        return (
            "hypotheses.md exposes internal verification labels; express the uncertainty as a falsifiable prediction, "
            "competing explanation, or planned disconfirming test",
            [],
        )
    audit_hits = _AUDIT_LANGUAGE.findall(text)
    if len(audit_hits) > 1:
        return "hypotheses.md leaks internal novelty-audit labels into researcher-facing claims", []
    semantic_errors: list[str] = []
    for claim in _dict_list(registry.get("claims")):
        claim_id = str(claim.get("id") or "").strip()
        block = _markdown_block_for_heading(text, claim_id)
        if not block:
            return f"hypotheses.md omits active claim {claim_id}", []
        if _research_text_length(block) < 80:
            semantic_errors.append(f"{claim_id} in hypotheses.md is only a short assertion, not a testable research claim")
        labels = {
            "rationale": ("rationale", "理由", "依据"),
            "mechanism": ("mechanism", "机制", "设计推理"),
            "expected observation": ("expected observation", "预期观察", "可观察"),
            "evaluation": ("evaluation", "评测", "检验方法"),
            "competing explanation": ("competing explanation", "竞争解释", "替代解释"),
            "falsification": ("falsification", "证伪", "失败条件"),
        }
        missing = [name for name, aliases in labels.items() if not _contains_any(block, aliases)]
        if missing:
            semantic_errors.append(f"{claim_id} in hypotheses.md is missing: " + ", ".join(missing))
    return None, semantic_errors


def validate_research_proposal_text(
    text: str,
    *,
    blueprint: dict[str, Any],
    registry: dict[str, Any],
    orientation: dict[str, Any],
    accepted_semantic_errors: set[str] | None = None,
) -> str | None:
    """Validate a Proposal's research contract without prescribing its layout."""

    hard_error, semantic_errors = _proposal_text_errors(
        text,
        blueprint=blueprint,
        registry=registry,
        orientation=orientation,
    )
    return hard_error or _first_unadjudicated_error(semantic_errors, accepted_semantic_errors)


def _proposal_text_errors(
    text: str,
    *,
    blueprint: dict[str, Any],
    registry: dict[str, Any],
    orientation: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Separate Proposal contract failures from ambiguous prose failures."""

    if _research_text_length(text) < _MINIMUM_PROPOSAL_SUBSTANCE:
        return (
            "research_proposal.md is too short to establish a coherent research problem, technical design, "
            "evaluation logic, and execution boundary",
            [],
        )
    sections = _proposal_sections(text)
    missing = [key for key, _aliases in PROPOSAL_SECTIONS if key not in sections]
    semantic_errors: list[str] = []
    if missing:
        # A Proposal is assessed on whether it makes the seven research
        # functions legible, not whether it mechanically reproduces seven
        # literal Markdown headings.  When functions are merged or named in
        # discipline-specific language, pass the complete prose to the
        # independent, quote-bound semantic reviewer instead of forcing a
        # wholesale rewrite into a template.
        missing_labels = ", ".join(missing)
        semantic_errors.append(
            "Proposal uses noncanonical or merged sectioning; independent review must confirm that its connected "
            "prose still covers these research functions: " + missing_labels
        )
        section_text = {key: text for key, _aliases in PROPOSAL_SECTIONS}
    else:
        section_text = sections
    too_short = [key for key, content in sections.items() if _research_text_length(content) < 120]
    if too_short:
        semantic_errors.append(
            "Proposal has concise labeled sections; independent review must confirm that the complete argument, "
            "rather than every local heading, is substantively developed: " + ", ".join(too_short)
        )
    repeated = _repeated_sentence_count(text)
    if repeated >= 3:
        return "research_proposal.md repeats the same sentence or near-identical sentence blocks instead of developing the argument", []
    verification_labels = _INTERNAL_VERIFICATION_LABEL.findall(text)
    if verification_labels:
        return (
            "research_proposal.md exposes internal verification labels; express a material uncertainty once as a "
            "concrete pre-execution validation, mitigation, or fallback in Risks, Limitations and Execution Plan",
            [],
        )
    audit_hits = _AUDIT_LANGUAGE.findall(text)
    if len(audit_hits) > 1:
        return "research_proposal.md is audit-dominated; move internal T4.5/collision labels to novelty_audit.md", []

    challenges = _dict_list(_nested_value(blueprint, "technical_problem", "key_challenges"))
    for challenge in challenges:
        challenge_id = str(challenge.get("id") or "").strip()
        if challenge_id and challenge_id not in section_text["gap_and_challenges"]:
            semantic_errors.append(f"Proposal does not explain challenge {challenge_id} in Prior Research, Gap and Key Challenges")
    component_ids = [
        str(component.get("id") or "").strip()
        for component in _dict_list(_nested_value(blueprint, "proposed_approach", "components"))
        if str(component.get("id") or "").strip()
    ]
    central_insight_position = _central_insight_position(section_text["approach"])
    if central_insight_position is None:
        semantic_errors.append(
            "Proposal does not state a readable central insight in Proposed Approach and Design Rationale "
            "(use Central Insight, Core Insight, 核心洞见, or 核心洞察)"
        )
    first_component_position = _first_component_position(section_text["approach"], component_ids)
    if (
        central_insight_position is not None
        and first_component_position is not None
        and central_insight_position > first_component_position
    ):
        semantic_errors.append(
            "Proposal states its central insight after the first technical component; explain the insight before component detail"
        )
    for component in _dict_list(_nested_value(blueprint, "proposed_approach", "components")):
        component_id = str(component.get("id") or "").strip()
        if component_id and component_id not in section_text["approach"]:
            return f"Proposal does not explain technical component {component_id}", []
    if not _explains_simpler_alternative(section_text["approach"], blueprint):
        semantic_errors.append(
            "Proposal does not explain the simpler alternative from research_blueprint.yaml "
            "and why it is insufficient"
        )

    for claim in _dict_list(registry.get("claims")):
        claim_id = str(claim.get("id") or "").strip()
        if claim_id and claim_id not in section_text["claims"]:
            return f"Proposal does not carry active claim {claim_id} into Research Questions, Claims and Hypotheses", []
    evaluation_requirements = (
        ("a credible counterfactual or comparison", ("baseline", "control", "counterfactual", "comparison", "基线", "对照组", "对照条件", "反事实", "比较对象")),
        ("component, treatment, or design-isolation evidence", ("ablation", "component comparison", "factorial", "incremental", "treatment contrast", "消融", "移除组件", "组件比较", "因子设计", "增量检验", "处理组")),
        ("robustness or validity analysis", ("robust", "sensitivity", "placebo", "validity", "generaliz", "稳健", "敏感性", "安慰剂", "有效性", "外部效度", "异质性")),
        ("mechanism or process validation", ("mechanism", "mediation", "process tracing", "pathway", "机制", "中介检验", "过程检验", "路径检验")),
    )
    for label, aliases in evaluation_requirements:
        if not _contains_any(section_text["evaluation"], aliases):
            semantic_errors.append(f"Research Design and Evaluation does not include {label}")
    if not _contains_any(section_text["evaluation"], ("real-world", "deployment", "现实", "部署", "组织", "用户", "平台")):
        semantic_errors.append(
            "Research Design and Evaluation does not connect the technical study to a real-world validation or deployment consequence"
        )
    if not _contains_any(section_text["contributions"], ("technical contribution", "技术贡献")):
        semantic_errors.append("Expected Contributions and Implications lacks a concrete technical contribution")
    if not _contains_any(section_text["contributions"], ("practical", "managerial", "现实", "实践", "管理")):
        semantic_errors.append("Expected Contributions and Implications names no practical actor or decision implication")
    actors = _string_list(_nested_value(blueprint, "core_problem", "affected_actors"))
    actor_text = section_text["contributions"] + "\n" + section_text["evaluation"]
    if actors and not any(_actor_is_named_in_text(actor, actor_text) for actor in actors):
        semantic_errors.append("The practical significance section does not name an affected actor from the research blueprint")
    risk_text = section_text["risks"]
    if not _contains_any(risk_text, ("fallback", "mitigation", "kill criteria", "备选", "缓解", "停止条件")):
        semantic_errors.append("Risks, Limitations and Execution Plan lists risks without mitigation, fallback, or kill criteria")

    profile = orientation["profile_type"]
    if profile == "ccf_a" and not _contains_any(section_text["approach"], ("algorithm", "model", "system", "optimization", "算法", "模型", "系统", "优化")):
        semantic_errors.append("CCF-A proposal lacks a complete computational method or system artifact")
    if profile == "utd":
        if not _contains_any(section_text["approach"], ("algorithm", "model", "system", "artifact", "算法", "模型", "系统", "技术构件")):
            semantic_errors.append("UTD proposal lacks the mandatory substantive technical artifact")
        thin_api = re.search(r"(?i)(?:call|invoke|use)\s+(?:an?\s+)?(?:existing\s+)?(?:llm|api)", section_text["approach"])
        technical_design = _contains_any(section_text["approach"], ("objective", "representation", "optimization", "inference", "训练", "表示", "优化", "推断"))
        explicit_api_only = re.search(
            r"(?i)(?:api[- ]only|no\s+(?:new|learned)|omits?\s+.*(?:objective|optimization|inference|representation)|without\s+.*(?:objective|optimization|inference|representation))",
            section_text["approach"],
        )
        if thin_api and (not technical_design or explicit_api_only):
            semantic_errors.append("UTD proposal reduces its technical artifact to calling an existing LLM/API")
    if profile == "hybrid":
        links = _dict_list(_nested_value(blueprint, "research_claims", "cross_level_links"))
        if not links:
            return "Hybrid formalization is missing structured cross_level_links in research_blueprint.yaml", []
        if not _contains_any(text, ("cross-level", "技术属性", "用户", "组织", "平台", "决策结果")):
            semantic_errors.append(
                "Hybrid proposal has no explicit link from a technical design to a user, organizational, platform, or decision outcome"
            )
    return None, semantic_errors


def collect_t45_quality_diagnostics(workspace: Path) -> list[dict[str, str]]:
    """Collect quality guidance without confusing it with integrity failures.

    Integrity and scientific-minimum checks remain in the deterministic
    validators above. These diagnostics identify refinements that an agent can
    make safely: depth relative to the selected orientation, prose-language
    consistency, and avoidable over/under-fragmentation. They are supplied to
    the Formalizer, not rendered as a user-facing warning stream.
    """

    workspace = Path(workspace)
    structured_ok, _structured_error = validate_t45_structured_sources(workspace)
    if not structured_ok:
        return []
    orientation = load_orientation_configuration(workspace)
    language = str(orientation.get("formalization_language") or "en")
    blueprint, _normalizations = normalize_research_blueprint_payload(
        _read_yaml_mapping(workspace / BLUEPRINT_REL_PATH)
    )
    diagnostics: list[dict[str, str]] = []

    challenges = _dict_list(_nested_value(blueprint, "technical_problem", "key_challenges"))
    if len(challenges) == 2:
        diagnostics.append(
            {
                "severity": "advisory",
                "code": "challenge_scope_two",
                "artifact": BLUEPRINT_REL_PATH,
                "message": "The plan uses two independent technical challenges. This is valid when they fully span the problem.",
                "action": "Confirm that a third challenge would not be artificial; do not add one merely to meet a preferred count.",
            }
        )
    elif len(challenges) > 4:
        diagnostics.append(
            {
                "severity": "advisory",
                "code": "challenge_scope_broad",
                "artifact": BLUEPRINT_REL_PATH,
                "message": f"The plan declares {len(challenges)} key challenges, which may diffuse one study's central mechanism.",
                "action": "Consolidate only genuinely overlapping challenges; retain distinct challenges when they change the artifact or evaluation.",
            }
        )

    rationale_counts = Counter(
        str(item.get("component_id") or "").strip()
        for item in _dict_list(_nested_value(blueprint, "proposed_approach", "design_rationales"))
        if str(item.get("component_id") or "").strip()
    )
    repeated_rationales = sorted(component_id for component_id, count in rationale_counts.items() if count > 1)
    if repeated_rationales:
        diagnostics.append(
            {
                "severity": "advisory",
                "code": "duplicate_component_rationale",
                "artifact": BLUEPRINT_REL_PATH,
                "message": "More than one design-rationale entry names " + ", ".join(repeated_rationales) + ".",
                "action": "Merge duplicate rationale entries when they express the same design argument; keep distinct ones only when they test different mechanisms.",
            }
        )

    for relative_path, target_key, label in (
        ("ideation/hypotheses.md", "claims", "Research Claims and Hypotheses"),
        ("ideation/proposal/research_proposal.md", "proposal", "Research Proposal"),
    ):
        path = workspace / relative_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        actual = _research_text_length(text)
        target = int(orientation["minimum_words"][target_key])
        if actual < target:
            diagnostics.append(
                {
                    "severity": "advisory",
                    "code": f"{target_key}_depth",
                    "artifact": relative_path,
                    "message": f"{label} is below the orientation depth target ({actual}/{target} words or CJK-character equivalents).",
                    "action": "Check whether research reasoning, mechanisms, design comparisons, evaluation logic, or boundaries are genuinely missing. Expand only when the argument needs it; never pad to meet a length target.",
                }
            )
        if language == "zh":
            cjk_chars = len(re.findall(r"[\u3400-\u9fff]", text))
            latin_words = len(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\b", text))
            if cjk_chars < max(240, latin_words):
                diagnostics.append(
                    {
                        "severity": "repair",
                        "code": f"{target_key}_language",
                        "artifact": relative_path,
                        "message": f"{label} is configured for Chinese formalization but is predominantly English ({cjk_chars} CJK characters, {latin_words} Latin words).",
                        "action": "Rewrite the researcher-facing argument in Chinese while retaining stable IDs, schema keys, citations, and necessary technical terms in English.",
                    }
                )
    return diagnostics


def repairable_t45_quality_diagnostics(diagnostics: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Return only diagnostics that require an autonomous source revision."""

    return [item for item in diagnostics if str(item.get("severity") or "") == "repair"]


def format_t45_repairable_quality_warnings(diagnostics: Iterable[dict[str, str]]) -> str | None:
    """Encode internal quality guidance for the Formalizer repair loop."""

    repairable = repairable_t45_quality_diagnostics(diagnostics)
    if not repairable:
        return None
    lines = [T45_REPAIRABLE_WARNING_PREFIX, "Internal T4.5 quality refinements required:"]
    for item in repairable:
        lines.append(
            "- [{code}] {artifact}: {message}\n  Required repair: {action}".format(
                code=item.get("code") or "quality",
                artifact=item.get("artifact") or "source artifact",
                message=item.get("message") or "quality target not met",
                action=item.get("action") or "Revise the named source artifact.",
            )
        )
    return "\n".join(lines)


def validate_orientation_review(workspace: Path) -> tuple[bool, str | None]:
    """Ensure the independent reviewer accepted the artifacts under the right lens."""

    workspace = Path(workspace)
    review, error = _load_structured(workspace / ORIENTATION_REVIEW_REL_PATH, "orientation_review", format_name="json")
    if error:
        return False, error
    assert review is not None
    orientation = load_orientation_configuration(workspace)
    profile = orientation["profile_type"]
    if review.get("orientation") != profile:
        return False, "orientation_review.json was written for a different orientation"
    if review.get("status") != "accepted":
        return False, "orientation_review.json requires targeted repair before acceptance"
    # Scores are useful diagnostics but weak release evidence when authored by
    # the same reviewer that can raise them. Acceptance instead requires every
    # material issue to carry an explicit resolution.
    unresolved: list[str] = []
    for index, issue in enumerate(review.get("issues") or [], start=1):
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "").strip().casefold()
        status = str(issue.get("status") or issue.get("resolution_status") or "").strip().casefold()
        if severity in {"high", "medium", "blocking", "major"} and status not in {
            "resolved",
            "repaired",
            "accepted_after_repair",
            "not_applicable",
        }:
            unresolved.append(str(issue.get("id") or issue.get("code") or f"issue_{index}"))
    if unresolved:
        return False, "orientation_review.json has unresolved material issues: " + ", ".join(unresolved)
    return True, None


def legacy_t45_upgrade_reason(workspace: Path) -> str | None:
    """Return a safe migration reason for an old completed T4.5 package.

    This does not alter an artifact.  The CLI uses it only when an historical
    workspace is marked ``COMPLETED`` even though its T4.5 audit passed under
    the old monolithic contract and therefore has no current blueprint/review
    quality gate.  A non-passing audit is intentionally left untouched.
    """

    workspace = Path(workspace)
    audit_path = workspace / "ideation" / "novelty_audit.md"
    if not audit_path.is_file():
        return None
    verdict = extract_final_gate_verdict(audit_path.read_text(encoding="utf-8", errors="replace"))
    if normalize_final_gate_verdict(verdict) not in PASSING_FINAL_GATE_VERDICTS:
        return None
    required_inputs = (
        workspace / "ideation" / "hypothesis_brief.yaml",
        workspace / "ideation" / "selected" / "selected_candidate.json",
        workspace / "literature" / "synthesis.md",
    )
    if not all(path.is_file() for path in required_inputs):
        return None
    formal_ok, formal_error = validate_t45_formalization_core(workspace)
    review_ok, review_error = validate_orientation_review(workspace)
    if formal_ok and review_ok:
        return None
    reason = formal_error or review_error or "missing unified T4.5 formalization artifacts"
    return "旧版 T4.5 已通过 novelty audit，但尚未通过新的统一研究正式化质量 gate：" + reason


def compile_t45_derived_artifacts(workspace: Path, audit_path: Path) -> tuple[bool, str | None]:
    """Compile legacy T5-facing maps from the validated structured sources.

    These files keep the prior T5 contract stable.  They are deterministic
    projections, not a second model-written dossier that can drift away from
    the blueprint or claim registry.
    """

    workspace = Path(workspace)
    ok, error = validate_t45_formalization_core(workspace)
    if not ok:
        return False, error
    if not audit_path.is_file():
        return False, "novelty_audit.md is required before compiling T4.5 derivatives"
    blueprint = _read_yaml_mapping(workspace / BLUEPRINT_REL_PATH)
    registry = _read_yaml_mapping(workspace / CLAIM_REGISTRY_REL_PATH)
    exp_plan = _read_yaml_mapping(workspace / "ideation" / "exp_plan.yaml")
    orientation = persist_orientation_configuration(workspace)
    selected = _read_json_mapping(workspace / "ideation" / "selected" / "selected_candidate.json")
    candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    candidate_id = str(selected.get("candidate_id") or candidate.get("id") or "unknown").strip()
    fingerprint = str(selected.get("selection_fingerprint") or "unknown").strip()
    verdict = extract_final_gate_verdict(audit_path.read_text(encoding="utf-8", errors="replace"))
    if not is_passing_final_gate_verdict(verdict):
        return False, "T4.5 derivatives require a passing Final Gate Verdict in novelty_audit.md"
    claims = _dict_list(registry.get("claims"))
    contributions = blueprint.get("contributions") if isinstance(blueprint.get("contributions"), dict) else {}
    risks = blueprint.get("risks") if isinstance(blueprint.get("risks"), dict) else {}
    core_problem = blueprint.get("core_problem") if isinstance(blueprint.get("core_problem"), dict) else {}
    approach = blueprint.get("proposed_approach") if isinstance(blueprint.get("proposed_approach"), dict) else {}
    source_artifacts = [
        "ideation/hypothesis_brief.yaml",
        "ideation/selected/selected_candidate.json",
        "ideation/novelty_audit.md",
        BLUEPRINT_REL_PATH,
        CLAIM_REGISTRY_REL_PATH,
        "ideation/hypotheses.md",
        "ideation/exp_plan.yaml",
    ]
    dossier = {
        "semantics": "t45_research_dossier",
        "status": "formalized_after_novelty_pass",
        "candidate_id": candidate_id,
        "selection_fingerprint": fingerprint,
        "orientation": orientation["profile_type"],
        "novelty_audit_verdict": verdict,
        "central_thesis": {"statement": approach.get("central_insight", ""), "evidence_status": "proposed_not_verified"},
        "research_problem": {"statement": core_problem.get("scientific_significance", "")},
        "why_it_matters": {
            "scholarly": [{"statement": core_problem.get("scientific_significance", "")}],
            "practical": [{"statement": core_problem.get("observed_failure", "")}],
            "commercial": [{"statement": core_problem.get("decision_or_task", "")}],
            "stakeholders_or_processes": _string_list(core_problem.get("affected_actors")),
        },
        "contributions": _flatten_contributions(contributions),
        "hypotheses": [{"id": item.get("id"), "statement": item.get("statement")} for item in claims],
        "evidence_boundary": {"statement": "The blueprint is a proposed research plan; expected observations are not empirical results."},
        "novelty_boundary": {"statement": "Novelty constraints and required baselines are retained in ideation/novelty_audit.md."},
        "risks_and_kill_criteria": _dict_list(risks.get("kill_criteria")),
        "traceability": {"source_artifacts": [item for item in source_artifacts if (workspace / item).exists()]},
    }
    _write_json(workspace / "ideation" / "research_dossier.json", dossier)
    _write_yaml(
        workspace / "ideation" / "contribution_hypothesis_map.yaml",
        {
            "semantics": "t45_blueprint_contribution_claim_map",
            "orientation": orientation["profile_type"],
            "contributions": contributions,
            "claims": [{"id": item.get("id"), "related_components": item.get("related_components", [])} for item in claims],
            "source": CLAIM_REGISTRY_REL_PATH,
        },
    )
    experiments = _dict_list(exp_plan.get("experiments"))
    _write_yaml(
        workspace / "ideation" / "validation_map.yaml",
        {
            "semantics": "t45_blueprint_validation_map",
            "orientation": orientation["profile_type"],
            "claims": [
                {
                    "claim_id": item.get("id"),
                    "evaluation_methods": item.get("evaluation_methods", []),
                    "experiment_ids": _experiment_ids_for_claim(experiments, str(item.get("id") or "")),
                }
                for item in claims
            ],
            "baselines": _dict_list(_nested_value(blueprint, "evaluation", "baselines")),
            "ablations": _dict_list(_nested_value(blueprint, "evaluation", "ablations")),
            "mechanism_tests": _dict_list(_nested_value(blueprint, "evaluation", "mechanism_tests")),
            "robustness_tests": _dict_list(_nested_value(blueprint, "evaluation", "robustness_tests")),
        },
    )
    _write_yaml(
        workspace / "ideation" / "kill_criteria.yaml",
        {
            "semantics": "t45_blueprint_kill_criteria",
            "orientation": orientation["profile_type"],
            "hypotheses": _dict_list(risks.get("kill_criteria")),
            "fallback_designs": _dict_list(risks.get("fallback_designs")),
            "source": BLUEPRINT_REL_PATH,
        },
    )
    return True, None


def write_post_novelty_formalization_manifest(workspace: Path) -> None:
    """Write receipt only after all sources, Proposal and review passed validation."""

    workspace = Path(workspace)
    candidate_id, selection_fingerprint = _current_selected_identity(workspace)
    if not candidate_id or not selection_fingerprint:
        raise ValueError("Cannot publish T4.5 formalization without the current selected Candidate identity")
    context = _read_json_mapping(workspace / T45_SELECTION_ISOLATION_REL_PATH)
    if context and not _selection_identity_matches(context, candidate_id, selection_fingerprint):
        raise ValueError("Cannot publish T4.5 formalization across different Candidate selection lineages")
    _write_t45_selection_isolation(
        workspace,
        candidate_id=candidate_id,
        selection_fingerprint=selection_fingerprint,
        status="accepted_for_t5",
        reason="formalization_and_orientation_review_accepted",
        archive=context.get("archive") if isinstance(context.get("archive"), dict) else None,
    )
    artifacts = {
        "orientation_config": ORIENTATION_CONFIG_REL_PATH,
        "research_blueprint": BLUEPRINT_REL_PATH,
        "claim_registry": CLAIM_REGISTRY_REL_PATH,
        "hypotheses": "ideation/hypotheses.md",
        "research_dossier": "ideation/research_dossier.json",
        "exp_plan": "ideation/exp_plan.yaml",
        "contribution_hypothesis_map": "ideation/contribution_hypothesis_map.yaml",
        "validation_map": "ideation/validation_map.yaml",
        "kill_criteria": "ideation/kill_criteria.yaml",
        "research_proposal": "ideation/proposal/research_proposal.md",
        "proposal_manifest": "ideation/proposal/proposal_manifest.json",
        "orientation_review": ORIENTATION_REVIEW_REL_PATH,
    }
    _write_json(
        workspace / FORMALIZATION_MANIFEST_REL_PATH,
        {
            "semantics": "t45_post_novelty_formalization",
            "status": "formalized_after_novelty_pass",
            "candidate_id": candidate_id,
            "selection_fingerprint": selection_fingerprint,
            "selection_isolation": T45_SELECTION_ISOLATION_REL_PATH,
            "artifacts": artifacts,
            "quality_gate": "blueprint_claims_proposal_orientation_review",
        },
    )


def _load_structured(path: Path, schema_name: str, *, format_name: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file() or path.stat().st_size <= 0:
        return None, f"Missing required structured artifact: {path.as_posix()}"
    try:
        if format_name == "json" or path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return None, f"Cannot parse {path.name}: {exc}"
    if not isinstance(value, dict):
        return None, f"{path.name} must contain an object"
    if schema_name == "research_blueprint":
        value, _normalizations = normalize_research_blueprint_payload(value)
    valid, error = validate_record(value, schema_name)
    if not valid:
        return None, f"{path.name} fails {schema_name} schema: {error}"
    return value, None


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _nested_value(payload: dict[str, Any], first: str, second: str) -> Any:
    container = payload.get(first)
    return container.get(second) if isinstance(container, dict) else None


def _nested_text(payload: dict[str, Any], first: str, second: str) -> str:
    return str(_nested_value(payload, first, second) or "").strip()


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _substantive_text(value: Any, *, minimum: int = 8) -> bool:
    return len(str(value or "").strip()) >= minimum


def _substantive_mapping(value: dict[str, Any]) -> bool:
    return any(_substantive_text(item) for item in value.values() if not isinstance(item, (dict, list))) or any(
        bool(item) for item in value.values() if isinstance(item, (dict, list))
    )


def _exp_plan_claim_ids(plan: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for experiment in _dict_list(plan.get("experiments")):
        for key in ("hypothesis_ref", "claim_ref"):
            value = str(experiment.get(key) or "").strip()
            if value:
                values.add(value)
        for key in ("hypothesis_refs", "claim_refs"):
            values.update(_string_list(experiment.get(key)))
    return values


def _component_test_ids(value: Any) -> set[str]:
    refs: set[str] = set()
    for item in _dict_list(value):
        for key in ("component_id", "component_ref"):
            ref = str(item.get(key) or "").strip()
            if ref:
                refs.add(ref)
        refs.update(_string_list(item.get("component_refs")))
    return refs


def _research_text_length(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"\b[\w'-]+\b", text))
    return cjk if cjk > words else words


def _markdown_block_for_heading(text: str, heading: str) -> str:
    """Return a claim section without mistaking its field headings for claims.

    Claim blocks use a level-two or level-three heading (for example
    ``## DP1``), followed by field headings such as ``### Rationale`` or
    ``### 理由``.  The previous implementation ended the block at *any*
    level-two/three heading, which reduced every level-two claim with
    level-three fields to its title alone.  A Markdown section ends only at a
    heading of the same or a higher level.
    """

    match = re.search(rf"(?im)^(?P<marks>###?)\s+{re.escape(heading)}\b.*$", text)
    if not match:
        return ""
    tail = text[match.end() :]
    level = len(match.group("marks"))
    end = re.search(rf"(?m)^#{{1,{level}}}\s+", tail)
    return text[match.start() : match.end() + (end.start() if end else len(tail))]


def _contains_any(text: str, values: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(value.casefold() in lowered for value in values)


def _first_unadjudicated_error(
    errors: Iterable[str],
    accepted_semantic_errors: set[str] | None,
) -> str | None:
    """Return the first unsatisfied prose requirement after bounded review.

    ``accepted_semantic_errors`` is populated only by a hash-bound, quoted
    LLM adjudication receipt. All structural checks return before reaching this
    helper, so this cannot relax schema, evidence, identity, or execution
    contracts.
    """

    accepted = accepted_semantic_errors or set()
    return next((error for error in errors if error not in accepted), None)


def _central_insight_position(text: str) -> int | None:
    """Locate a readable central insight in the approach section.

    The contract requires an argument before the component inventory, not one
    particular English heading. Chinese academic prose commonly uses
    ``核心洞察`` rather than ``核心洞见``; treating that natural synonym as
    absent caused a deterministic repair loop in an otherwise valid Proposal.
    """

    marker = re.compile(r"(?i)central\s+insight|core\s+insight|核心洞见|核心洞察|核心思路|核心思想")
    match = marker.search(text)
    return match.start() if match else None


def _first_component_position(text: str, component_ids: Iterable[str]) -> int | None:
    """Return the earliest explicit component reference in the approach."""

    positions = [
        match.start()
        for component_id in component_ids
        if component_id
        for match in re.finditer(rf"(?i)\b{re.escape(component_id)}\b", text)
    ]
    return min(positions) if positions else None


def _explains_simpler_alternative(text: str, blueprint: dict[str, Any]) -> bool:
    """Recognize a substantive design comparison without forcing one label.

    The blueprint supplies the named alternative and the Proposal must explain
    why it falls short. A literal ``alternative`` heading is useful, but not a
    scholarly requirement: Chinese prose often embeds that comparison in the
    central-insight paragraph. Accept an explicit comparison label, or a named
    blueprint alternative paired with an insufficiency explanation.
    """

    comparison_markers = (
        "alternative",
        "simpler design",
        "simpler approach",
        "替代方案",
        "更简单的方案",
        "简化方案",
        "基线方案",
    )
    insufficiency_markers = (
        "insufficient",
        "cannot",
        "does not",
        "fails to",
        "inadequate",
        "not enough",
        "不足",
        "不足以",
        "无法",
        "不能",
        "未能",
        "缺乏",
        "没有",
    )
    if _contains_any(text, comparison_markers) and _contains_any(text, insufficiency_markers):
        return True

    alternatives = _dict_list(_nested_value(blueprint, "proposed_approach", "alternatives_considered"))
    for alternative in alternatives:
        label = str(alternative.get("alternative") or "")
        # Proper names and distinctive English terms are reliable anchors for
        # a comparison embedded in a Chinese paragraph. Generic words such as
        # "static" and "model" do not establish that comparison.
        anchors = [
            token
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", label)
            if token.casefold() not in {"static", "model", "system", "approach", "guardrail"}
        ]
        if anchors and any(anchor.casefold() in text.casefold() for anchor in anchors):
            if _contains_any(text, insufficiency_markers):
                return True
    return False


def _actor_is_named_in_text(actor: str, text: str) -> bool:
    """Recognize a blueprint actor in prose written in the selected language.

    ``affected_actors`` may be inherited from an English Candidate while the
    T4.5 researcher-facing text is intentionally Chinese. A byte-for-byte
    match turns a clearly named actor such as ``平台`` into a false failure.
    This small controlled lexicon covers role nouns rather than attempting open
    ended translation; exact matching remains the default for all other terms.
    """

    actor_normalized = str(actor or "").casefold().strip()
    text_normalized = str(text or "").casefold()
    if actor_normalized and actor_normalized in text_normalized:
        return True
    token_aliases = {
        "anchor": ("主播", "直播主"),
        "streamer": ("主播", "直播主"),
        "platform": ("平台",),
        "agency": ("机构", "mc n", "mcn", "公会"),
        "designer": ("设计者", "设计人员", "系统设计"),
        "developer": ("开发者", "开发人员", "系统开发"),
        "manager": ("管理者", "管理人员", "管理者"),
        "organization": ("组织", "机构"),
        "user": ("用户", "使用者"),
        "worker": ("员工", "工作者"),
        "seller": ("销售", "卖家", "主播"),
        "customer": ("客户", "消费者", "观众"),
    }
    return any(
        token in actor_normalized
        and (token in text_normalized or _contains_any(text_normalized, aliases))
        for token, aliases in token_aliases.items()
    )


def _proposal_sections(text: str) -> dict[str, str]:
    headings = list(re.finditer(r"(?im)^##\s+(.+?)\s*$", text))
    result: dict[str, str] = {}
    for index, heading in enumerate(headings):
        title = heading.group(1).strip().casefold()
        # Numbered academic headings such as ``## 1. Research Motivation``
        # express the same research function. Canonical headings make the
        # deterministic checks more precise; merged or discipline-specific
        # headings remain eligible for quote-bound semantic review.
        title = re.sub(r"^\d+(?:\.\d+)*[.)、．]?\s+", "", title)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        for key, aliases in PROPOSAL_SECTIONS:
            if any(alias.casefold() == title for alias in aliases):
                result[key] = text[heading.end() : end]
                break
    return result


def _repeated_sentence_count(text: str) -> int:
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    normalized = [re.sub(r"\s+", " ", sentence.casefold()).strip() for sentence in sentences]
    repeated = Counter(item for item in normalized if len(item) >= 60)
    return sum(count - 1 for count in repeated.values() if count >= 2)


def _flatten_contributions(contributions: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for kind in ("technical", "theoretical_or_design", "practical_or_managerial"):
        for item in _dict_list(contributions.get(kind)):
            flattened.append({"kind": kind, **item})
    return flattened


def _experiment_ids_for_claim(experiments: list[dict[str, Any]], claim_id: str) -> list[str]:
    output: list[str] = []
    for experiment in experiments:
        refs = _string_list(experiment.get("claim_refs")) + _string_list(experiment.get("hypothesis_refs"))
        refs += [str(experiment.get("claim_ref") or "").strip(), str(experiment.get("hypothesis_ref") or "").strip()]
        if claim_id in refs:
            experiment_id = str(experiment.get("id") or experiment.get("name") or "").strip()
            if experiment_id:
                output.append(experiment_id)
    return output
