"""Durable, isolated T4.5 proposal tracks selected from a T4 portfolio.

The user can advance several complete Ideas from T4, but each one remains a
separate research proposal.  T5 deliberately consumes only one materialized
track so an experiment protocol can never silently mix claims or methods from
different Ideas.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any


MANIFEST_REL_PATH = "ideation/proposal_portfolio/manifest.json"
SELECTION_REL_PATH = "ideation/proposal_portfolio/selection.json"
TRACKS_REL_DIR = "ideation/proposal_portfolio/tracks"

# These are the candidate-bound artifacts that T4.5 and T5 consume.  Shared
# T4 evidence, the Population, and all source literature intentionally remain
# outside a track and are never copied or deleted here.
TRACK_ARTIFACTS = (
    "ideation/_gate1_user_selection.json",
    "ideation/hypothesis_brief.yaml",
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
        archive = receipt.get("archive") if isinstance(receipt, dict) else None
        archive_rel = str(archive.get("root") or "").strip() if isinstance(archive, dict) else ""
        archive_root = (workspace / archive_rel).resolve() if archive_rel else None
        if archive_root is None or workspace not in archive_root.parents:
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
    unique_ids = list(dict.fromkeys(str(item).strip() for item in candidate_ids if str(item).strip()))
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
    destination = Path(workspace_dir) / TRACKS_REL_DIR / candidate_id / "artifacts"
    if destination.exists():
        shutil.rmtree(destination)
    copied: list[str] = []
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
    if not (destination / "ideation" / "proposal" / "research_proposal.md").is_file():
        raise ValueError("cannot snapshot a proposal track before its formal Proposal exists")
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


def materialize_selected_track(workspace_dir: Path, manifest: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """Make a selected independent Proposal the canonical single T5 input."""

    candidate_id = str(candidate_id or "").strip()
    manifest = backfill_historical_track_artifacts(workspace_dir, manifest)
    ready = {
        str(track.get("candidate_id")): track
        for track in manifest.get("tracks", [])
        if isinstance(track, dict) and track.get("status") == "ready_for_t5_selection"
    }
    if candidate_id not in ready:
        raise ValueError("the chosen Proposal is not a completed T4.5 track")
    source_root = Path(workspace_dir) / str(ready[candidate_id].get("artifact_root") or "")
    if not source_root.is_dir():
        raise ValueError("the chosen Proposal archive is missing")
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
    selection_path = Path(workspace_dir) / SELECTION_REL_PATH
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["status"] = "selected_for_t5"
    manifest["selected_candidate_id"] = candidate_id
    write_manifest(workspace_dir, manifest)
    return selection


def overview(manifest: dict[str, Any], candidate_labels: dict[str, str] | None = None) -> dict[str, Any]:
    labels = candidate_labels or {}
    tracks = []
    for track in manifest.get("tracks", []):
        if not isinstance(track, dict):
            continue
        candidate_id = str(track.get("candidate_id") or "")
        tracks.append(
            {
                "candidate_id": candidate_id,
                "title": labels.get(candidate_id, candidate_id),
                "status": str(track.get("status") or "unknown"),
                "proposal_path": f"{track.get('artifact_root')}/ideation/proposal/research_proposal.md",
            }
        )
    return {"tracks": tracks, "status": str(manifest.get("status") or "unknown")}
