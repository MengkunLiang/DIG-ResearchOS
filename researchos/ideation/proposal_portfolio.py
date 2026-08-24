"""Durable, isolated T4.5 proposal tracks selected from a T4 portfolio.

The user can advance several complete Ideas from T4, but each one remains a
separate research proposal.  T5 deliberately consumes only one materialized
track so an experiment protocol can never silently mix claims or methods from
different Ideas.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any
from difflib import SequenceMatcher

import yaml

from ..pydantic_compat import model_dump
from .state import stable_fingerprint


MANIFEST_REL_PATH = "ideation/proposal_portfolio/manifest.json"
SELECTION_REL_PATH = "ideation/proposal_portfolio/selection.json"
TRACKS_REL_DIR = "ideation/proposal_portfolio/tracks"
ACTIVE_TRACK_CONTEXT_REL_PATH = "ideation/proposal_portfolio/active_track.json"

# Research-facing sources must be different enough to justify two independent
# Proposal tracks.  Candidate IDs and provenance receipts are checked
# separately; these paths protect the scientific package from a stale/generic
# copy that happens to carry the new ID.
TRACK_DISTINCTNESS_ARTIFACTS = (
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/proposal/research_proposal.md",
)

# These are the candidate-bound artifacts that T4.5 and T5 consume.  Shared
# T4 evidence, the Population, and all source literature intentionally remain
# outside a track and are never copied or deleted here.
TRACK_ARTIFACTS = (
    "ideation/_gate1_user_selection.json",
    "ideation/hypothesis_brief.yaml",
    # T5/T8 consume the selected orientation as part of the active package,
    # and post_novelty_formalization records its digest. Keep one verified
    # copy in every track so an archived Proposal is self-contained.
    "ideation/orientation_config.yaml",
    "ideation/selected",
    "ideation/selected_idea_brief.md",
    "ideation/novelty_audit.md",
    "ideation/collision_cases.md",
    "ideation/novelty_audit_fingerprints.json",
    "ideation/_mechanism_tuples",
    "ideation/_design_rationale_tuples",
    # These compiled planning artifacts are required by T5 and are
    # candidate-bound even though they are derived deterministically from the
    # blueprint.  They must be copied before the active projection is cleared
    # for the next Proposal track.
    "ideation/research_dossier.json",
    "ideation/contribution_hypothesis_map.yaml",
    "ideation/validation_map.yaml",
    "ideation/kill_criteria.yaml",
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/proposal",
    "ideation/orientation_review.json",
    "ideation/t45_selection_isolation.json",
    "ideation/post_novelty_formalization.json",
)

# A passed track must contain these files before it can become the single T5
# input. Optional collision material is copied when present, but a missing
# formalization source is never silently filled from another active track.
TRACK_REQUIRED_ARTIFACTS = (
    "ideation/selected/selected_candidate.json",
    "ideation/hypothesis_brief.yaml",
    "ideation/orientation_config.yaml",
    "ideation/selected_idea_brief.md",
    "ideation/novelty_audit.md",
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/hypotheses.md",
    "ideation/research_dossier.json",
    "ideation/exp_plan.yaml",
    "ideation/contribution_hypothesis_map.yaml",
    "ideation/validation_map.yaml",
    "ideation/kill_criteria.yaml",
    "ideation/proposal/research_proposal.md",
    "ideation/proposal/proposal_manifest.json",
    "ideation/orientation_review.json",
    "ideation/post_novelty_formalization.json",
    "ideation/t45_selection_isolation.json",
)

_IDENTITY_ARTIFACTS = (
    "ideation/research_dossier.json",
    "ideation/proposal/proposal_manifest.json",
    "ideation/post_novelty_formalization.json",
)

_ORDINAL_NUMBER_WORDS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def proposal_selection_alias(raw_selection: str | None) -> str | None:
    """Normalize human-facing ordinal wording to the stable ``D<n>`` alias.

    This helper intentionally handles only selection expressions, not the
    numeric Gate menu.  A bare ``1`` therefore remains available to choose
    the menu action, while ``第一个``/``第一条`` can identify the first
    completed Proposal in either the menu follow-up prompt or a scripted
    input.  Candidate IDs and existing D/S aliases are returned unchanged in
    canonical uppercase form.
    """

    value = re.sub(r"\s+", "", str(raw_selection or "").strip())
    if not value:
        return None
    upper = value.upper()
    if re.fullmatch(r"[DS]\d+", upper):
        return upper

    aliases = {
        "首个": 1,
        "首条": 1,
        "首份": 1,
        "第一": 1,
        "第一个": 1,
        "第一条": 1,
        "第一项": 1,
        "第一份": 1,
        "第二": 2,
        "第二个": 2,
        "第二条": 2,
        "第二项": 2,
        "第二份": 2,
        "第三": 3,
        "第三个": 3,
        "第三条": 3,
        "第三项": 3,
        "第三份": 3,
    }
    if value in aliases:
        return f"D{aliases[value]}"

    ordinal = re.fullmatch(r"(?:选择|选|推进)?第(\d+|[一二两三四五六七八九十])(?:个|条|项|份|号)?(?:proposal|方案|提案)?", value, re.IGNORECASE)
    if ordinal:
        token = ordinal.group(1)
        number = int(token) if token.isdigit() else _ORDINAL_NUMBER_WORDS.get(token)
        if number:
            return f"D{number}"

    named = re.fullmatch(r"(?:proposal|方案|提案)(?:第)?(\d+|[一二两三四五六七八九十])", value, re.IGNORECASE)
    if named:
        token = named.group(1)
        number = int(token) if token.isdigit() else _ORDINAL_NUMBER_WORDS.get(token)
        if number:
            return f"D{number}"
    return None


def _validate_candidate_id(candidate_id: str) -> str:
    """Return a safe candidate component for an on-disk track directory.

    Candidate identifiers are generated upstream, but they are also persisted
    in a manifest and later used as path components.  Failing closed here
    prevents a malformed or hand-edited manifest from escaping the portfolio
    directory or silently colliding with another track.
    """

    value = str(candidate_id or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid Proposal Candidate identifier: {value!r}")
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    """Convert a CandidateDossier or mapping into a JSON-like mapping."""

    if isinstance(candidate, dict):
        return candidate
    try:
        value = model_dump(candidate, mode="json")
    except (TypeError, ValueError, AttributeError):
        value = {}
    return value if isinstance(value, dict) else {}


def candidate_anchor(candidate: Any) -> dict[str, Any]:
    """Return the immutable research intent that a Proposal track must keep.

    The anchor is deliberately compact and derived only from the T4 Candidate
    dossier.  It is not a second score and never changes the Candidate.  Its
    purpose is to make a parallel track's input explicit to the runtime and to
    the Formalizer, so a parent Candidate and an evolved child cannot silently
    share one active research package.
    """

    payload = _candidate_payload(candidate)
    genome = payload.get("genome") if isinstance(payload.get("genome"), dict) else payload
    lineage = payload.get("lineage") if isinstance(payload.get("lineage"), dict) else {}

    def gene_value(name: str, *, limit: int, aliases: tuple[str, ...] = ()) -> str:
        value = genome.get(name) if isinstance(genome, dict) else ""
        if not value and isinstance(payload, dict):
            for alias in aliases:
                value = payload.get(alias)
                if value:
                    break
        if isinstance(value, dict):
            value = value.get("value") or value.get("statement") or value.get("text")
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

    candidate_id = str(
        payload.get("candidate_id")
        or payload.get("id")
        or payload.get("internal_id")
        or genome.get("candidate_id")
        or genome.get("id")
        or ""
    ).strip()
    parents = lineage.get("parent_ids") or genome.get("parents") or []
    if not isinstance(parents, list):
        parents = [str(parents)] if str(parents).strip() else []
    hypothesis_bundle = gene_value("hypothesis_bundle", limit=1200, aliases=("candidate_hypotheses", "hypotheses"))
    return {
        "candidate_id": candidate_id,
        "candidate_fingerprint": stable_fingerprint(payload),
        "route": str(lineage.get("route") or genome.get("route") or "").strip(),
        "maturity": str(payload.get("maturity") or genome.get("maturity") or "").strip(),
        "parent_ids": list(dict.fromkeys(str(item).strip() for item in parents if str(item).strip())),
        "core_problem": gene_value("problem", limit=700, aliases=("target_problem", "problem_statement")),
        "core_thesis": gene_value("core_thesis", limit=1000, aliases=("core_claim", "pitch")),
        "mechanism": gene_value("mechanism", limit=1400),
        "design_or_artifact": gene_value("design_or_artifact", limit=1200, aliases=("innovation", "contribution_character")),
        "hypothesis_bundle": hypothesis_bundle,
        "validation_logic": gene_value("validation_logic", limit=1200, aliases=("minimum_experiment", "prediction")),
        "hypothesis_ids": [
            str(item.get("hypothesis_id") or item.get("id") or "").strip()
            for item in (payload.get("hypotheses") if isinstance(payload.get("hypotheses"), list) else [])
            if isinstance(item, dict) and str(item.get("hypothesis_id") or item.get("id") or "").strip()
        ],
    }


def candidate_relationships(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe parent/child and shared-parent relations in a multi-selection.

    This is a transparent warning for the researcher, not an automatic
    rejection.  A child may still deserve its own Proposal, but Formalizer and
    Portfolio Gate must know that it extends another selected direction.
    """

    by_id = {
        str(item.get("candidate_id") or "").strip(): item
        for item in anchors
        if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
    }
    def selected_ancestors(candidate_id: str) -> set[str]:
        """Follow only selected ancestors; unselected lineage stays context."""

        ancestors: set[str] = set()
        pending = list(by_id.get(candidate_id, {}).get("parent_ids") or [])
        while pending:
            parent_id = str(pending.pop()).strip()
            if not parent_id or parent_id in ancestors:
                continue
            ancestors.add(parent_id)
            parent = by_id.get(parent_id)
            if parent:
                pending.extend(parent.get("parent_ids") or [])
        return ancestors

    relationships: list[dict[str, Any]] = []
    for left_id, left in by_id.items():
        left_parents = selected_ancestors(left_id)
        for right_id, right in by_id.items():
            if left_id >= right_id:
                continue
            right_parents = selected_ancestors(right_id)
            if left_id in right_parents or right_id in left_parents:
                child, parent = (right_id, left_id) if left_id in right_parents else (left_id, right_id)
                relationships.append({"left": left_id, "right": right_id, "kind": "parent_child", "parent": parent, "child": child})
            elif left_parents & right_parents:
                relationships.append({"left": left_id, "right": right_id, "kind": "shared_parent", "shared_parents": sorted(left_parents & right_parents)})
    return relationships


def _normalized_content(text: str) -> str:
    """Normalize only formatting noise for cross-track duplicate detection."""

    value = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
    return value


def track_content_fingerprints(artifact_root: Path) -> dict[str, str]:
    """Hash candidate-bound research sources under one track."""

    root = Path(artifact_root)
    fingerprints: dict[str, str] = {}
    for relative in TRACK_DISTINCTNESS_ARTIFACTS:
        path = root / relative
        if path.is_file():
            fingerprints[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


def cross_track_content_errors(
    workspace_dir: Path,
    manifest: dict[str, Any],
    candidate_id: str,
    *,
    active_root: Path | None = None,
) -> list[str]:
    """Reject exact or near-identical scientific packages across tracks.

    Identity checks prevent a Candidate-ID swap; this check prevents a valid
    new ID from being attached to the prior track's generic Proposal.  It is
    intentionally conservative: exact equality is always an error, while a
    very high normalized similarity is reported only when both Proposal bodies
    and at least one structured source agree.  Parent/child tracks normally
    share concepts but should still differ in their research argument.
    """

    workspace = Path(workspace_dir)
    current_root = Path(active_root) if active_root is not None else workspace
    current_paths = {
        relative: current_root / relative
        for relative in TRACK_DISTINCTNESS_ARTIFACTS
        if (current_root / relative).is_file()
    }
    if not current_paths:
        return []
    errors: list[str] = []
    for track in manifest.get("tracks", []):
        if not isinstance(track, dict) or str(track.get("candidate_id") or "").strip() in {"", candidate_id}:
            continue
        if str(track.get("status") or "") not in {"ready_for_t5_selection", "active"}:
            continue
        other_root = workspace / str(track.get("artifact_root") or "")
        if not other_root.is_dir():
            continue
        exact_matches: list[str] = []
        for relative, current_path in current_paths.items():
            other_path = other_root / relative
            if other_path.is_file() and hashlib.sha256(current_path.read_bytes()).hexdigest() == hashlib.sha256(other_path.read_bytes()).hexdigest():
                exact_matches.append(relative)
        if exact_matches:
            errors.append(
                f"track {candidate_id} duplicates candidate-bound source content from "
                f"{track.get('candidate_id')}: {', '.join(exact_matches[:4])}"
            )
            continue
        proposal = current_paths.get("ideation/proposal/research_proposal.md")
        other_proposal = other_root / "ideation/proposal/research_proposal.md"
        if proposal and other_proposal.is_file():
            similarity = SequenceMatcher(
                None,
                _normalized_content(proposal.read_text(encoding="utf-8", errors="replace")),
                _normalized_content(other_proposal.read_text(encoding="utf-8", errors="replace")),
            ).ratio()
            shared_structured = sum(
                1
                for relative in TRACK_DISTINCTNESS_ARTIFACTS[:2]
                if (current_paths.get(relative) and (other_root / relative).is_file()
                    and SequenceMatcher(
                        None,
                        _normalized_content(current_paths[relative].read_text(encoding="utf-8", errors="replace")),
                        _normalized_content((other_root / relative).read_text(encoding="utf-8", errors="replace")),
                    ).ratio() >= 0.94)
            )
            if similarity >= 0.94 and shared_structured:
                errors.append(
                    f"track {candidate_id} is near-duplicate of {track.get('candidate_id')} "
                    f"(Proposal similarity={similarity:.3f}; structured sources={shared_structured})"
                )
    return errors


def track_identity_errors(
    artifact_root: Path,
    candidate_id: str,
    *,
    expected_fingerprint: str | None = None,
    require_anchors: bool = False,
    require_post_novelty_manifest: bool = True,
    verify_digests: bool = False,
) -> list[str]:
    """Check candidate and selection lineage on candidate-bound JSON anchors.

    Schema validation alone cannot detect a valid dossier copied from another
    parallel Proposal.  The selected Candidate and isolation receipt establish
    the expected identity; every identity-bearing downstream artifact must
    echo both values.  A historical archive may not contain the selected file,
    so ``require_anchors`` is only used for an active or ready track.
    """

    root = Path(artifact_root)
    candidate = _validate_candidate_id(candidate_id)
    selected = _read_json_object(root / "ideation/selected/selected_candidate.json")
    isolation = _read_json_object(root / "ideation/t45_selection_isolation.json")
    selected_candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    selected_id = str(selected.get("candidate_id") or selected_candidate.get("id") or "").strip()
    selected_fp = str(selected.get("selection_fingerprint") or "").strip()
    isolation_id = str(isolation.get("candidate_id") or "").strip()
    isolation_fp = str(isolation.get("selection_fingerprint") or "").strip()
    expected = str(expected_fingerprint or selected_fp or isolation_fp).strip()
    errors: list[str] = []
    if require_anchors and not selected_id:
        errors.append("selected_candidate.json has no candidate_id")
    if require_anchors and not selected_fp:
        errors.append("selected_candidate.json has no selection_fingerprint")
    if selected_id and selected_id != candidate:
        errors.append(f"selected_candidate.json candidate_id={selected_id}, expected {candidate}")
    if isolation_id and isolation_id != candidate:
        errors.append(f"t45_selection_isolation.json candidate_id={isolation_id}, expected {candidate}")
    if expected and selected_fp and selected_fp != expected:
        errors.append("selected_candidate.json selection_fingerprint does not match the track lineage")
    if expected and isolation_fp and isolation_fp != expected:
        errors.append("t45_selection_isolation.json selection_fingerprint does not match the track lineage")
    for relative in _IDENTITY_ARTIFACTS:
        path = root / relative
        if not path.exists():
            # During T4.5 review the Proposal and orientation record are
            # validated before the runtime publishes the final receipt.  The
            # receipt is the last derived artifact and cannot be required by
            # the pre-publication validator without creating a circular
            # failure: validate -> require receipt -> never publish receipt.
            # T5 handoff and track snapshot callers keep the default strict
            # behavior and therefore still fail closed when the receipt is
            # genuinely missing.
            if relative == "ideation/post_novelty_formalization.json" and not require_post_novelty_manifest:
                continue
            if require_anchors:
                errors.append(f"missing identity artifact: {relative}")
            continue
        payload = _read_json_object(path)
        if not payload:
            errors.append(f"identity artifact is not a JSON object: {relative}")
            continue
        actual_id = str(payload.get("candidate_id") or "").strip()
        actual_fp = str(payload.get("selection_fingerprint") or "").strip()
        if require_anchors and not actual_id:
            errors.append(f"{relative} has no candidate_id")
        if require_anchors and not actual_fp:
            errors.append(f"{relative} has no selection_fingerprint")
        if actual_id and actual_id != candidate:
            errors.append(f"{relative} candidate_id={actual_id}, expected {candidate}")
        if expected and actual_fp and actual_fp != expected:
            errors.append(f"{relative} selection_fingerprint does not match the track lineage")
        if relative == "ideation/post_novelty_formalization.json" and verify_digests:
            digests = payload.get("artifact_digests")
            if isinstance(digests, dict):
                for digest_path, expected_digest in digests.items():
                    relative_digest_path = str(digest_path or "").strip()
                    if not relative_digest_path or Path(relative_digest_path).is_absolute() or ".." in Path(relative_digest_path).parts:
                        errors.append("post_novelty_formalization.json contains an unsafe artifact digest path")
                        continue
                    digest_file = root / relative_digest_path
                    if not digest_file.is_file():
                        errors.append(f"artifact digest target is missing: {relative_digest_path}")
                        continue
                    actual_digest = hashlib.sha256(digest_file.read_bytes()).hexdigest()
                    if actual_digest != str(expected_digest or "").strip():
                        errors.append(f"artifact digest mismatch: {relative_digest_path}")
    return errors


def ready_track_ids(manifest: dict[str, Any], workspace_dir: Path | None = None) -> list[str]:
    """Return only completed tracks whose candidate lineage is coherent."""

    ready: list[str] = []
    for track in manifest.get("tracks", []):
        if not isinstance(track, dict) or track.get("status") != "ready_for_t5_selection":
            continue
        candidate_id = str(track.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        errors: list[str] = []
        if workspace_dir is not None:
            track_root = Path(workspace_dir) / str(track.get("artifact_root") or "")
            errors = track_identity_errors(
                track_root,
                candidate_id,
                require_anchors=True,
                verify_digests=True,
            )
            if not errors:
                errors = cross_track_content_errors(
                    Path(workspace_dir),
                    manifest,
                    candidate_id,
                    active_root=track_root,
                )
        if not errors:
            ready.append(candidate_id)
    return ready


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(workspace_dir: Path) -> dict[str, Any] | None:
    path = Path(workspace_dir) / MANIFEST_REL_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("semantics") != "t45_proposal_portfolio":
        return None
    tracks = payload.get("tracks")
    return payload if isinstance(tracks, list) else None


def _refresh_formalization_receipt_digests(
    artifact_root: Path,
    receipt: dict[str, Any],
) -> None:
    """Refresh a legacy track receipt after deterministic derived repair."""

    artifacts = dict(receipt.get("artifacts") or {}) if isinstance(receipt.get("artifacts"), dict) else {}
    artifacts.setdefault("orientation_config", "ideation/orientation_config.yaml")
    receipt["artifacts"] = artifacts
    digest_paths = set(str(value) for value in artifacts.values() if str(value).strip())
    digest_paths.update(
        {
            "ideation/selected/selected_candidate.json",
            "ideation/t45_selection_isolation.json",
            "ideation/novelty_audit.md",
            "ideation/collision_cases.md",
            "ideation/novelty_audit_fingerprints.json",
        }
    )
    digests = dict(receipt.get("artifact_digests") or {}) if isinstance(receipt.get("artifact_digests"), dict) else {}
    for relative in digest_paths:
        path = artifact_root / relative
        if path.is_file():
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt["artifact_digest_algorithm"] = "sha256"
    receipt["artifact_digests"] = digests
    (artifact_root / "ideation/post_novelty_formalization.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _repair_track_derived_identity(
    artifact_root: Path,
    candidate_id: str,
    receipt: dict[str, Any],
) -> tuple[bool, str | None]:
    """Recompile only deterministic T5 projections when a legacy track leaked one.

    Parallel tracks created before the isolation boundary could carry a
    dossier from the previous Candidate even though their blueprint,
    registry, Proposal and selection receipt were already track-local.  The
    dossier and three maps are deterministic projections of those sources,
    so regenerating them inside the track is safe; candidate-bound Proposal
    prose is never copied or rewritten here.
    """

    errors = track_identity_errors(
        artifact_root,
        candidate_id,
        require_anchors=True,
        verify_digests=False,
    )
    if not any("research_dossier.json" in item for item in errors):
        return False, None
    audit_path = artifact_root / "ideation/novelty_audit.md"
    if not audit_path.is_file():
        return False, "legacy derived artifact is inconsistent and novelty_audit.md is missing"
    try:
        from .formalization import compile_t45_derived_artifacts

        ok, error = compile_t45_derived_artifacts(artifact_root, audit_path)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        return False, f"deterministic legacy derived-artifact repair failed: {exc}"
    if not ok:
        return False, error or "deterministic legacy derived-artifact repair did not pass source validation"
    _refresh_formalization_receipt_digests(artifact_root, receipt)
    return True, None


def backfill_historical_track_artifacts(workspace_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Recover files omitted by the pre-isolation portfolio implementation.

    Older parallel runs copied the main Proposal files into a track but left
    a few derived formalization files only in the selection-supersession
    archive. The track's own isolation receipt identifies that archive, so it
    is safe to restore only those exact files. If the receipt or source is
    absent, materialization fails closed instead of borrowing another track.
    """

    workspace = Path(workspace_dir).resolve()
    changed = False
    for track in manifest.get("tracks", []):
        if not isinstance(track, dict):
            continue
        artifact_root = str(track.get("artifact_root") or "").strip()
        if not artifact_root:
            continue
        destination_root = (workspace / artifact_root).resolve()
        if workspace not in destination_root.parents:
            continue
        receipt_path = destination_root / "ideation/t45_selection_isolation.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            receipt = {}
        if str(receipt.get("candidate_id") or "").strip() != str(track.get("candidate_id") or "").strip():
            # Never use an archive whose receipt belongs to another Candidate.
            continue

        # A pre-fix portfolio snapshot could contain a valid post-novelty
        # receipt whose digest list included the shared orientation config,
        # while omitting that file from the copied track.  It is safe to
        # recover this one shared artifact only when the active workspace
        # bytes match the archived receipt exactly.  Never copy a
        # candidate-bound artifact from the active projection, because that
        # could silently import another Proposal's research package.
        copied = list(track.get("copied_artifacts") or []) if isinstance(track.get("copied_artifacts"), list) else []
        formalization_receipt = _read_json_object(destination_root / "ideation/post_novelty_formalization.json")
        digests = {}
        if isinstance(receipt.get("artifact_digests"), dict):
            digests.update(receipt["artifact_digests"])
        if isinstance(formalization_receipt.get("artifact_digests"), dict):
            digests.update(formalization_receipt["artifact_digests"])
        for relative in ("ideation/orientation_config.yaml",):
            target = destination_root / relative
            source = workspace / relative
            expected_digest = str(digests.get(relative) or "").strip()
            if target.exists() or not source.is_file():
                continue

            # Newer receipts carry the exact digest.  Older receipts did not
            # list this shared orientation file, so use the independent
            # orientation fields in the archived blueprint/review as a
            # compatibility proof before adding the current shared config.
            if expected_digest:
                actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if actual_digest != expected_digest:
                    continue
            else:
                try:
                    active_orientation = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
                    blueprint = yaml.safe_load(
                        (destination_root / "ideation/research_blueprint.yaml").read_text(encoding="utf-8")
                    ) or {}
                    review = _read_json_object(destination_root / "ideation/orientation_review.json")
                except (OSError, ValueError, yaml.YAMLError):
                    continue
                active_profile = str(active_orientation.get("profile_type") or "").strip()
                archived_orientation = blueprint.get("orientation") if isinstance(blueprint, dict) else {}
                archived_profile = str(archived_orientation.get("profile_type") or "").strip()
                review_profile = str(review.get("orientation") or "").strip()
                active_weights = active_orientation.get("proposal_weights")
                archived_weights = archived_orientation.get("proposal_weights") if isinstance(archived_orientation, dict) else None
                if not (
                    active_profile
                    and active_profile == archived_profile == review_profile
                    and isinstance(active_weights, dict)
                    and active_weights == archived_weights
                ):
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if relative not in copied:
                copied.append(relative)
            changed = True

            # Upgrade a legacy formalization receipt so the normal digest
            # verifier can remain strict on the next render/resume.
            if not expected_digest and formalization_receipt:
                formalization_receipt.setdefault("artifacts", {})["orientation_config"] = relative
                formalization_receipt.setdefault("artifact_digests", {})[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
                formalization_receipt["artifact_digest_algorithm"] = "sha256"
                receipt_target = destination_root / "ideation/post_novelty_formalization.json"
                receipt_target.write_text(json.dumps(formalization_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if copied != track.get("copied_artifacts"):
            track["copied_artifacts"] = list(dict.fromkeys(copied))

        repaired, repair_error = _repair_track_derived_identity(
            destination_root,
            str(track.get("candidate_id") or ""),
            formalization_receipt,
        )
        if repaired:
            track["derived_artifact_repair"] = "deterministic_recompile"
            changed = True
        elif repair_error:
            track["derived_artifact_repair_error"] = repair_error
            changed = True

        archive = receipt.get("archive") if isinstance(receipt, dict) else None
        archive_rel = str(archive.get("root") or "").strip() if isinstance(archive, dict) else ""
        archive_root = (workspace / archive_rel).resolve() if archive_rel else None
        if archive_root is None or workspace not in archive_root.parents:
            continue
        archive_errors = track_identity_errors(
            archive_root,
            str(track.get("candidate_id") or ""),
            expected_fingerprint=str(receipt.get("selection_fingerprint") or "").strip() or None,
            require_anchors=False,
            verify_digests=True,
        )
        if archive_errors:
            track["archive_backfill_error"] = "; ".join(archive_errors[:8])
            changed = True
            continue
        copied = list(track.get("copied_artifacts") or []) if isinstance(track.get("copied_artifacts"), list) else []
        for relative in TRACK_ARTIFACTS:
            target = destination_root / relative
            if target.exists():
                if relative not in copied:
                    copied.append(relative)
                continue
            source = archive_root / relative
            if not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            copied.append(relative)
            changed = True
        if copied != track.get("copied_artifacts"):
            track["copied_artifacts"] = list(dict.fromkeys(copied))
            track["archive_backfill"] = "selection_history_receipt"
            changed = True
    if changed:
        write_manifest(workspace, manifest)
    return manifest


def create_manifest(
    workspace_dir: Path,
    *,
    candidate_ids: list[str],
    population_id: str,
    directive_path: str,
    source: str,
    candidate_anchors: list[dict[str, Any]] | None = None,
    relationships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(_validate_candidate_id(str(item)) for item in candidate_ids if str(item).strip()))
    if not unique_ids:
        raise ValueError("proposal portfolio needs at least one Candidate")
    anchors_by_id = {
        str(item.get("candidate_id") or "").strip(): item
        for item in (candidate_anchors or [])
        if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
    }
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "semantics": "t45_proposal_portfolio",
        "population_id": population_id,
        "directive_path": directive_path,
        "source": source,
        "status": "running",
        "active_candidate_id": unique_ids[0],
        "tracks": [
            {
                "candidate_id": candidate_id,
                "status": "active" if index == 0 else "queued",
                "started_at": now_iso() if index == 0 else None,
                "completed_at": None,
                "artifact_root": f"{TRACKS_REL_DIR}/{candidate_id}/artifacts",
                "candidate_anchor": anchors_by_id.get(candidate_id, {}),
                "candidate_anchor_fingerprint": str(
                    anchors_by_id.get(candidate_id, {}).get("candidate_fingerprint") or ""
                ).strip(),
                "selection_fingerprint": None,
                "selected_candidate_fingerprint": None,
            }
            for index, candidate_id in enumerate(unique_ids)
        ],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "policy": {
            "independent_proposals": True,
            "t5_requires_single_selection": True,
            "note": "Tracks are formalized separately; no claims, mechanisms, or experiments are merged.",
        },
        "selection_relationships": list(relationships or []),
    }
    write_manifest(workspace_dir, payload)
    return payload


def write_manifest(workspace_dir: Path, payload: dict[str, Any]) -> None:
    path = Path(workspace_dir) / MANIFEST_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized["updated_at"] = now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def active_candidate_id(manifest: dict[str, Any]) -> str | None:
    candidate_id = str(manifest.get("active_candidate_id") or "").strip()
    return candidate_id or None


def bind_active_track_selection(
    workspace_dir: Path,
    manifest: dict[str, Any],
    *,
    candidate_id: str,
    selection_fingerprint: str,
    candidate_fingerprint: str,
) -> dict[str, Any]:
    """Bind the freshly compiled T4 selection to its manifest track.

    This is called immediately after Gate1 compilation, before any model task
    starts.  Keeping the binding in the manifest and a small active-context
    file gives every later T4.5 process a durable expected identity, including
    the first track (which otherwise had no activation transition to write it).
    """

    candidate_id = _validate_candidate_id(candidate_id)
    fingerprint = str(selection_fingerprint or "").strip()
    dossier_fingerprint = str(candidate_fingerprint or "").strip()
    if not fingerprint or not dossier_fingerprint:
        raise ValueError("cannot bind a Proposal track without selection and Candidate fingerprints")
    target = next(
        (
            item
            for item in manifest.get("tracks", [])
            if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if target is None:
        raise ValueError(f"proposal portfolio has no track for Candidate {candidate_id}")
    target["selection_fingerprint"] = fingerprint
    target["selected_candidate_fingerprint"] = dossier_fingerprint
    context = {
        "schema_version": "1.0.0",
        "semantics": "t45_active_proposal_track",
        "candidate_id": candidate_id,
        "candidate_fingerprint": dossier_fingerprint,
        "candidate_anchor_fingerprint": str(target.get("candidate_anchor_fingerprint") or "").strip(),
        "selection_fingerprint": fingerprint,
        "candidate_anchor": target.get("candidate_anchor") if isinstance(target.get("candidate_anchor"), dict) else {},
        "activated_at": now_iso(),
    }
    path = Path(workspace_dir) / ACTIVE_TRACK_CONTEXT_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_manifest(workspace_dir, manifest)
    return context


def active_track_formalization_context(workspace_dir: Path) -> dict[str, Any]:
    """Return the active track scope, or a precise pre-write integrity error.

    T4.5 still uses the canonical active ``ideation/`` projection so existing
    agents and T5 have one stable path.  This helper makes that projection
    safe for parallel operation: when a portfolio is active, its manifest,
    selected Candidate and track anchor must all agree before an Agent can
    reason over or write the shared paths.
    """

    workspace = Path(workspace_dir)
    manifest = load_manifest(workspace)
    if manifest is None or str(manifest.get("status") or "") not in {"running", "awaiting_t5_selection"}:
        return {"portfolio_active": False, "binding_error": "", "candidate_anchor": {}, "completed_tracks": []}
    active_id = active_candidate_id(manifest)
    if not active_id:
        # After all tracks are archived there is no T4.5 agent to constrain.
        return {"portfolio_active": False, "binding_error": "", "candidate_anchor": {}, "completed_tracks": []}
    track = next(
        (
            item
            for item in manifest.get("tracks", [])
            if isinstance(item, dict) and str(item.get("candidate_id") or "") == active_id
        ),
        None,
    )
    if track is None:
        return {"portfolio_active": True, "binding_error": f"manifest has no active track record for {active_id}", "candidate_anchor": {}, "completed_tracks": []}
    selected = _read_json_object(workspace / "ideation/selected/selected_candidate.json")
    candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    selected_id = str(selected.get("candidate_id") or candidate.get("id") or "").strip()
    selected_fp = str(selected.get("selection_fingerprint") or "").strip()
    candidate_fp = str(selected.get("candidate_fingerprint") or "").strip()
    errors: list[str] = []
    if selected_id != active_id:
        errors.append(f"selected Candidate is {selected_id or 'missing'}, active Proposal track is {active_id}")
    expected_fp = str(track.get("selection_fingerprint") or "").strip()
    if expected_fp and expected_fp != selected_fp:
        errors.append("selected Candidate fingerprint differs from the active Proposal track")
    expected_candidate_fp = str(
        track.get("selected_candidate_fingerprint") or track.get("candidate_fingerprint") or ""
    ).strip()
    if expected_candidate_fp and candidate_fp and expected_candidate_fp != candidate_fp:
        errors.append("selected Candidate dossier fingerprint differs from the active Proposal track anchor")
    anchor = _track_candidate_anchor(workspace, track)
    if not anchor and candidate:
        # Legacy workspaces did not persist a track anchor.  Derive an
        # in-memory view from the current selected Candidate without mutating
        # historical research artifacts. New tracks always persist it.
        anchor = candidate_anchor(candidate)
    completed_tracks: list[dict[str, str]] = []
    for item in manifest.get("tracks", []):
        if not isinstance(item, dict) or str(item.get("candidate_id") or "") == active_id:
            continue
        if str(item.get("status") or "") != "ready_for_t5_selection":
            continue
        sibling_anchor = _track_candidate_anchor(workspace, item)
        completed_tracks.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "core_thesis": str(sibling_anchor.get("core_thesis") or ""),
                "mechanism": str(sibling_anchor.get("mechanism") or ""),
            }
        )
    return {
        "portfolio_active": True,
        "binding_error": "; ".join(errors),
        "candidate_id": active_id,
        "selection_fingerprint": selected_fp,
        "candidate_fingerprint": candidate_fp,
        "candidate_anchor": anchor,
        "completed_tracks": completed_tracks,
        "relationships": [
            item
            for item in (manifest.get("selection_relationships") or [])
            if isinstance(item, dict) and active_id in {str(item.get("left") or ""), str(item.get("right") or "")}
        ],
    }


def _track_candidate_anchor(workspace_dir: Path, track: dict[str, Any]) -> dict[str, Any]:
    """Read a persisted anchor or derive a non-mutating legacy display view."""

    anchor = track.get("candidate_anchor") if isinstance(track.get("candidate_anchor"), dict) else {}
    if anchor:
        return anchor
    root = Path(workspace_dir) / str(track.get("artifact_root") or "")
    selected = _read_json_object(root / "ideation/selected/selected_candidate.json")
    candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    return candidate_anchor(candidate) if candidate else {}


def snapshot_active_track(workspace_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Copy a passed active T4.5 package into its immutable proposal track."""

    candidate_id = active_candidate_id(manifest)
    if candidate_id is None:
        raise ValueError("proposal portfolio has no active Candidate")
    candidate_id = _validate_candidate_id(candidate_id)
    workspace = Path(workspace_dir)
    missing = [relative for relative in TRACK_REQUIRED_ARTIFACTS if not (workspace / relative).exists()]
    if missing:
        raise ValueError(
            "cannot snapshot a Proposal track before all candidate-bound T4.5 artifacts exist: "
            + ", ".join(missing)
        )
    active_selected = _read_json_object(workspace / "ideation/selected/selected_candidate.json")
    active_candidate = active_selected.get("candidate") if isinstance(active_selected.get("candidate"), dict) else {}
    selected_id = str(active_selected.get("candidate_id") or active_candidate.get("id") or "").strip()
    selected_fingerprint = str(active_selected.get("selection_fingerprint") or "").strip()
    if selected_id != candidate_id or not selected_fingerprint:
        raise ValueError(
            "active T4.5 projection belongs to a different Candidate: "
            f"selected={selected_id or 'missing'}, expected={candidate_id}"
        )
    track_record = next(
        (
            item
            for item in manifest.get("tracks", [])
            if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if track_record is None:
        raise ValueError(f"proposal portfolio has no manifest track for active Candidate {candidate_id}")
    expected_fp = str(track_record.get("selection_fingerprint") or "").strip()
    if expected_fp and expected_fp != selected_fingerprint:
        raise ValueError("active T4.5 selection fingerprint does not match its Proposal track")
    expected_candidate_fp = str(
        track_record.get("selected_candidate_fingerprint") or track_record.get("candidate_fingerprint") or ""
    ).strip()
    actual_candidate_fp = str(active_selected.get("candidate_fingerprint") or "").strip()
    if expected_candidate_fp and actual_candidate_fp and expected_candidate_fp != actual_candidate_fp:
        raise ValueError("active T4.5 Candidate dossier does not match its Proposal track anchor")
    cross_errors = cross_track_content_errors(workspace, manifest, candidate_id)
    if cross_errors:
        raise ValueError("parallel Proposal content is not independent: " + "; ".join(cross_errors[:4]))
    identity_errors = track_identity_errors(workspace, candidate_id, require_anchors=True, verify_digests=True)
    if identity_errors:
        raise ValueError("candidate-bound T4.5 identity is inconsistent: " + "; ".join(identity_errors[:8]))
    destination = Path(workspace_dir) / TRACKS_REL_DIR / candidate_id / "artifacts"
    if destination.exists():
        shutil.rmtree(destination)
    copied: list[str] = []
    try:
        for rel in TRACK_ARTIFACTS:
            source = Path(workspace_dir) / rel
            if not source.exists():
                continue
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            copied.append(rel)
    except (OSError, shutil.Error):
        shutil.rmtree(destination, ignore_errors=True)
        raise
    for track in manifest["tracks"]:
        if isinstance(track, dict) and track.get("candidate_id") == candidate_id:
            track["status"] = "ready_for_t5_selection"
            track["completed_at"] = now_iso()
            track["copied_artifacts"] = copied
            track["selection_fingerprint"] = selected_fingerprint
            track["content_fingerprints"] = track_content_fingerprints(destination)
            track["content_contract"] = "candidate_bound_v1"
            break
    manifest["active_candidate_id"] = None
    (workspace / ACTIVE_TRACK_CONTEXT_REL_PATH).unlink(missing_ok=True)
    write_manifest(workspace_dir, manifest)
    return manifest


def activate_next_track(workspace_dir: Path, manifest: dict[str, Any]) -> str | None:
    for track in manifest.get("tracks", []):
        if isinstance(track, dict) and track.get("status") == "queued":
            candidate_id = str(track.get("candidate_id") or "").strip()
            if not candidate_id:
                continue
            clear_active_track_artifacts(workspace_dir)
            track["status"] = "active"
            track["started_at"] = now_iso()
            manifest["active_candidate_id"] = candidate_id
            manifest["status"] = "running"
            active_context = {
                "schema_version": "1.0.0",
                "semantics": "t45_active_proposal_track",
                "candidate_id": candidate_id,
                "candidate_anchor_fingerprint": str(track.get("candidate_anchor_fingerprint") or "").strip(),
                "selected_candidate_fingerprint": str(track.get("selected_candidate_fingerprint") or "").strip(),
                "selection_fingerprint": str(track.get("selection_fingerprint") or "").strip(),
                "candidate_anchor": track.get("candidate_anchor") if isinstance(track.get("candidate_anchor"), dict) else {},
                "activated_at": now_iso(),
            }
            context_path = Path(workspace_dir) / ACTIVE_TRACK_CONTEXT_REL_PATH
            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(json.dumps(active_context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_manifest(workspace_dir, manifest)
            return candidate_id
    manifest["active_candidate_id"] = None
    manifest["status"] = "awaiting_t5_selection"
    write_manifest(workspace_dir, manifest)
    return None


def clear_active_track_artifacts(workspace_dir: Path) -> None:
    """Clear only the candidate-bound T4.5 projection before a new track.

    This is intentional replacement of an active projection after its complete
    snapshot; it never touches a T4 Population, literature, historical track,
    or user source file.
    """

    for rel in TRACK_ARTIFACTS:
        path = Path(workspace_dir) / rel
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # This runtime-owned pointer is not researcher-facing evidence.  It must
    # never survive a projection clear and describe the prior Candidate.
    (Path(workspace_dir) / ACTIVE_TRACK_CONTEXT_REL_PATH).unlink(missing_ok=True)


def materialize_selected_track(
    workspace_dir: Path,
    manifest: dict[str, Any],
    candidate_id: str,
    *,
    selection_reason: str | None = None,
) -> dict[str, Any]:
    """Make a selected independent Proposal the canonical single T5 input."""

    candidate_id = _validate_candidate_id(candidate_id)
    manifest = backfill_historical_track_artifacts(workspace_dir, manifest)
    ready_ids = set(ready_track_ids(manifest, workspace_dir=Path(workspace_dir)))
    ready = {
        str(track.get("candidate_id")): track
        for track in manifest.get("tracks", [])
        if isinstance(track, dict)
        and str(track.get("candidate_id") or "") in ready_ids
    }
    if candidate_id not in ready:
        raise ValueError("the chosen Proposal is not a completed T4.5 track")
    source_root = Path(workspace_dir) / str(ready[candidate_id].get("artifact_root") or "")
    if not source_root.is_dir():
        raise ValueError("the chosen Proposal archive is missing")
    identity_errors = track_identity_errors(source_root, candidate_id, require_anchors=True, verify_digests=True)
    if identity_errors:
        raise ValueError(
            "the chosen Proposal archive has inconsistent candidate provenance: "
            + "; ".join(identity_errors[:8])
        )
    missing = [relative for relative in TRACK_REQUIRED_ARTIFACTS if not (source_root / relative).exists()]
    if missing:
        raise ValueError(
            "the chosen Proposal archive is incomplete; refusing to mix another track's active files: "
            + ", ".join(missing)
        )
    clear_active_track_artifacts(workspace_dir)
    copied: list[str] = []
    for rel in TRACK_ARTIFACTS:
        source = source_root / rel
        if not source.exists():
            continue
        target = Path(workspace_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        copied.append(rel)
    selection = {
        "schema_version": "1.0.0",
        "semantics": "t45_proposal_portfolio_t5_selection",
        "selected_candidate_id": candidate_id,
        "selected_track": str(ready[candidate_id].get("artifact_root") or ""),
        "copied_artifacts": copied,
        "decided_at": now_iso(),
        "note": "T5 receives exactly one independently formalized Proposal.",
    }
    if selection_reason:
        selection["selection_reason"] = str(selection_reason)
    selection_path = Path(workspace_dir) / SELECTION_REL_PATH
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["status"] = "selected_for_t5"
    manifest["selected_candidate_id"] = candidate_id
    write_manifest(workspace_dir, manifest)
    return selection


def overview(
    manifest: dict[str, Any],
    candidate_labels: dict[str, str] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    labels = candidate_labels or {}
    tracks = []
    ready_index = 0
    for track in manifest.get("tracks", []):
        if not isinstance(track, dict):
            continue
        candidate_id = str(track.get("candidate_id") or "")
        display_anchor = _track_candidate_anchor(Path(workspace_dir), track) if workspace_dir is not None else (
            track.get("candidate_anchor") if isinstance(track.get("candidate_anchor"), dict) else {}
        )
        integrity_errors: list[str] = []
        status_ready = str(track.get("status") or "") == "ready_for_t5_selection"
        if workspace_dir is not None and status_ready:
            integrity_errors = track_identity_errors(
                Path(workspace_dir) / str(track.get("artifact_root") or ""),
                candidate_id,
                require_anchors=True,
                verify_digests=True,
            )
            if not integrity_errors:
                integrity_errors = cross_track_content_errors(
                    Path(workspace_dir),
                    manifest,
                    candidate_id,
                    active_root=Path(workspace_dir) / str(track.get("artifact_root") or ""),
                )
        is_ready = status_ready and not integrity_errors
        if is_ready:
            ready_index += 1
        display_id = f"D{ready_index}" if is_ready else ""
        copied = {str(item).rstrip("/") for item in (track.get("copied_artifacts") or [])}
        required_count = len(TRACK_REQUIRED_ARTIFACTS)
        if workspace_dir is not None:
            root = Path(workspace_dir) / str(track.get("artifact_root") or "")
            required_present = sum(1 for item in TRACK_REQUIRED_ARTIFACTS if (root / item).exists())
        else:
            # Older snapshots record directory roots such as
            # ``ideation/selected`` and ``ideation/proposal`` rather than
            # every child file.  Treat those roots as covering their required
            # descendants when no workspace is available for an exact check.
            required_present = sum(
                1
                for item in TRACK_REQUIRED_ARTIFACTS
                if item in copied or any(item.startswith(root + "/") for root in copied)
            )
        tracks.append(
            {
                "display_id": display_id,
                "candidate_id": candidate_id,
                "title": labels.get(candidate_id, candidate_id),
                "status": str(track.get("status") or "unknown"),
                "track_root": str(track.get("artifact_root") or ""),
                "proposal_path": f"{track.get('artifact_root')}/ideation/proposal/research_proposal.md",
                "artifact_count": len(copied),
                "required_artifacts": f"{required_present}/{required_count}",
                "integrity": "valid" if not integrity_errors else "invalid",
                "integrity_errors": integrity_errors[:8],
                "core_problem": str(
                    display_anchor.get("core_problem") if isinstance(display_anchor, dict) else ""
                ),
                "core_thesis": str(
                    display_anchor.get("core_thesis") if isinstance(display_anchor, dict) else ""
                ),
                "mechanism": str(
                    display_anchor.get("mechanism") if isinstance(display_anchor, dict) else ""
                ),
                "parent_ids": list(
                    display_anchor.get("parent_ids")
                    if isinstance(display_anchor, dict)
                    and isinstance(display_anchor.get("parent_ids"), list)
                    else []
                ),
            }
        )
    return {
        "tracks": tracks,
        "status": str(manifest.get("status") or "unknown"),
        "selection_relationships": [
            item for item in (manifest.get("selection_relationships") or []) if isinstance(item, dict)
        ],
    }


def resolve_ready_track_selection(
    manifest: dict[str, Any],
    raw_selection: str | None,
    *,
    workspace_dir: Path | None = None,
) -> str | None:
    """Resolve the human-facing D1/D2 alias to one completed track.

    The alias is derived from the same ordered ready-track list used by
    :func:`overview`, so the table and the resolver cannot drift apart.  Full
    Candidate IDs remain accepted for scripted or older runs; ``S1`` aliases
    are retained as a backwards-compatible spelling from early builds.
    """

    value = str(raw_selection or "").strip()
    if not value:
        return None
    # A Gate can survive a process restart with a presentation rendered from
    # an older portfolio contract.  Run the same safe, receipt-scoped
    # migration used by the renderer before resolving the answer so a stale
    # table can never make a valid D1/D2 selection look unavailable.
    if workspace_dir is not None:
        backfill_historical_track_artifacts(Path(workspace_dir), manifest)
    ready_ids = ready_track_ids(manifest, workspace_dir=workspace_dir)
    normalized = proposal_selection_alias(value) or value.upper()
    aliases: dict[str, str] = {}
    for index, candidate_id in enumerate(ready_ids, start=1):
        aliases[f"D{index}"] = candidate_id
        aliases[f"S{index}"] = candidate_id
        aliases[str(index)] = candidate_id
    return aliases.get(normalized, value)
