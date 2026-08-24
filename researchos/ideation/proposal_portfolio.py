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

import yaml


MANIFEST_REL_PATH = "ideation/proposal_portfolio/manifest.json"
SELECTION_REL_PATH = "ideation/proposal_portfolio/selection.json"
TRACKS_REL_DIR = "ideation/proposal_portfolio/tracks"

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
            errors = track_identity_errors(
                Path(workspace_dir) / str(track.get("artifact_root") or ""),
                candidate_id,
                require_anchors=True,
                verify_digests=True,
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
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(_validate_candidate_id(str(item)) for item in candidate_ids if str(item).strip()))
    if not unique_ids:
        raise ValueError("proposal portfolio needs at least one Candidate")
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
            break
    manifest["active_candidate_id"] = None
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
        integrity_errors: list[str] = []
        status_ready = str(track.get("status") or "") == "ready_for_t5_selection"
        if workspace_dir is not None and status_ready:
            integrity_errors = track_identity_errors(
                Path(workspace_dir) / str(track.get("artifact_root") or ""),
                candidate_id,
                require_anchors=True,
                verify_digests=True,
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
            }
        )
    return {"tracks": tracks, "status": str(manifest.get("status") or "unknown")}


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
    ready_ids = ready_track_ids(manifest, workspace_dir=workspace_dir)
    normalized = proposal_selection_alias(value) or value.upper()
    aliases: dict[str, str] = {}
    for index, candidate_id in enumerate(ready_ids, start=1):
        aliases[f"D{index}"] = candidate_id
        aliases[f"S{index}"] = candidate_id
        aliases[str(index)] = candidate_id
    return aliases.get(normalized, value)
