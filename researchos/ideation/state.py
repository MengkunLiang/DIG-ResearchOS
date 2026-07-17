"""Artifact store, fingerprints, resume checks, and rollback-safe T4 state.

The store is deliberately deterministic.  It owns atomic writes, content
reuse, phase markers, and active-population pointers; semantic research work
stays with the role-separated agents and controller built in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

from ..pydantic_compat import model_dump, model_validate
from ..runtime.artifact_fingerprints import build_input_fingerprints
from ..runtime.bridge_catalog import cross_domain_catalogs_are_plan_only
from .models import (
    CandidateDossier,
    CrossoverCompatibilityDecision,
    EvolutionPhase,
    PopulationSnapshot,
    PortfolioSelection,
    RoundArtifact,
    ScoreReport,
    T4InternalState,
    T4RunConfig,
)


T4_STATE_REL_PATH = "ideation/evolution/state.json"
T4_FINAL_CARD_REPAIR_STATE_REL_PATH = "ideation/evolution/final_card_repair_state.json"
T4_LITERATURE_MANIFEST_FINGERPRINT_SCHEMA = "t4_literature_evidence_contract_v1"
T4_INPUT_FINGERPRINT_PATHS: dict[str, str] = {
    "project": "project.yaml",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "literature_manifest": "literature/literature_manifest.json",
    "core_deep_notes": "literature/deep_read_notes",
    "core_abstract_notes": "literature/shallow_read_notes",
    "bridge_notes": "literature/bridge_notes",
    "cross_domain_catalogs": "literature/cross_domain_catalogs",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md",
    "survey_insights": "ideation/survey_insights.json",
}

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True)
class ArtifactWriteResult:
    path: str
    changed: bool
    sha256: str


def build_t4_input_fingerprints(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint all scientific inputs that can invalidate a T4 population."""

    workspace = Path(workspace_dir)
    fingerprints = build_input_fingerprints(workspace, T4_INPUT_FINGERPRINT_PATHS)
    fingerprints["literature_manifest"] = _t4_literature_manifest_fingerprint(
        workspace,
        fallback=fingerprints["literature_manifest"],
    )
    # Workspace entry points create the standard reading directories lazily.
    # An absent route-specific directory and an empty one carry the same
    # scientific meaning: no evidence is available from that route.  Normalize
    # that operational distinction here, rather than making a later
    # ``run-task`` invocation invalidate a human-confirmed T4 configuration.
    # Any actual note remains represented by its directory digest and still
    # invalidates the Population when it changes.
    for label in ("core_deep_notes", "core_abstract_notes", "bridge_notes", "cross_domain_catalogs"):
        item = fingerprints[label]
        if item.get("exists") and item.get("kind") == "dir" and int(item.get("file_count") or 0) == 0:
            fingerprints[label] = {"path": str(item["path"]), "exists": False}
    # A catalog with no records and no linked reading note is only a
    # deterministic projection of bridge_domain_plan.json, which is already
    # fingerprinted above.  It must not invalidate an otherwise identical T4
    # Population merely because a resume materialized the B1/B2 directory.
    # Once a catalog contains a retrieved paper or a linked note, it becomes
    # genuine new scientific context and its byte-level fingerprint remains
    # mandatory.
    if _cross_domain_catalogs_are_plan_only(workspace):
        item = fingerprints["cross_domain_catalogs"]
        fingerprints["cross_domain_catalogs"] = {"path": str(item["path"]), "exists": False}
    return fingerprints


def _t4_literature_manifest_fingerprint(
    workspace_dir: Path,
    *,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Fingerprint literature evidence, not mutable PDF-availability receipts.

    The shared manifest deliberately records PDF acquisition so later readers
    can discover an accessible copy.  That receipt is operational evidence of
    availability only: it does not change a note card, its reading level, or
    the Cross-domain material T4 is allowed to use.  T4 runs PDF acquisition
    after its human pre-run Gate for legacy-resume coverage.  Hashing the
    entire manifest here therefore made a confirmed run invalidate itself
    before its first provider call.

    Keep the manifest in T4's contract, but bind the population only to the
    fields that can alter its scientific evidence surface.  Note-card content,
    evidence levels, aliases and catalog file records remain covered.  The
    manifest timestamp, migration report, derived counts and `pdf_acquisition`
    are excluded because they are runtime bookkeeping or availability data.
    """

    manifest_path = workspace_dir / "literature" / "literature_manifest.json"
    if not manifest_path.is_file():
        return fallback
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(manifest, dict):
        return fallback

    note_cards = manifest.get("note_cards")
    catalog = manifest.get("cross_domain_catalogs")
    if not isinstance(note_cards, list) or not isinstance(catalog, dict):
        return fallback
    semantic_payload = {
        "schema": T4_LITERATURE_MANIFEST_FINGERPRINT_SCHEMA,
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_semantics": manifest.get("semantics"),
        "canonical_roots": manifest.get("canonical_roots"),
        "note_cards": note_cards,
        "cross_domain_catalogs": {
            "root": catalog.get("root"),
            "index_path": catalog.get("index_path"),
            "files": catalog.get("files"),
            "file_records": catalog.get("file_records"),
            "usage_boundary": catalog.get("usage_boundary"),
        },
    }
    return {
        "path": str(fallback.get("path") or "literature/literature_manifest.json"),
        "exists": True,
        "kind": "literature_manifest_evidence_contract",
        "schema": T4_LITERATURE_MANIFEST_FINGERPRINT_SCHEMA,
        "sha256": stable_fingerprint(semantic_payload),
    }


def _cross_domain_catalogs_are_plan_only(workspace_dir: Path) -> bool:
    """Return whether catalog files add no material beyond the bridge plan."""

    return cross_domain_catalogs_are_plan_only(workspace_dir)


def t4_input_fingerprint(workspace_dir: Path) -> str:
    return stable_fingerprint(build_t4_input_fingerprints(workspace_dir))


def run_config_fingerprint(run_config: T4RunConfig) -> str:
    return stable_fingerprint(model_dump(run_config, mode="json"))


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replace_input_fingerprint(value: Any, old: str, new: str) -> bool:
    """Update only exact T4 input-fingerprint fields in durable JSON artifacts."""

    changed = False
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "input_fingerprint" and item == old:
                value[key] = new
                changed = True
            elif _replace_input_fingerprint(item, old, new):
                changed = True
    elif isinstance(value, list):
        for item in value:
            if _replace_input_fingerprint(item, old, new):
                changed = True
    return changed


def _fingerprint_maps_match_except_manifest(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """Return whether only the T4 manifest fingerprint schema differs."""

    previous_keys = set(previous)
    current_keys = set(current)
    if previous_keys != current_keys:
        return False
    for key in current_keys - {"literature_manifest"}:
        if previous.get(key) != current.get(key):
            return False
    return True


def t4_operation_identity(operation: Mapping[str, Any] | None) -> str:
    """Return a durable identity for one human-requested T4 operation.

    A Gate1 operation remains in the workflow state while its T4 execution is
    in progress.  That is useful for an interrupted controller call, but it is
    dangerous after the controller has already persisted a new Population and
    only the LLM Final Card compilation failed: blindly seeing the request
    again would run the same evolution a second time.  The identity deliberately
    excludes transport-only fields such as ``path`` and ``queued_at`` so it is
    stable across process restarts while retaining the directive, source
    Population, and profile/composition inputs that define the operation.
    """

    if not isinstance(operation, Mapping):
        return stable_fingerprint({"kind": "initial_t4_run"})
    directive = operation.get("directive")
    directive_payload = directive if isinstance(directive, Mapping) else {}
    payload = {
        "kind": "native_t4_operation",
        "action": str(operation.get("action") or ""),
        "directive_path": str(operation.get("directive_path") or ""),
        "requested_from_population": str(operation.get("requested_from_population") or ""),
        "directive": dict(directive_payload),
        "composition_plan_path": str(operation.get("composition_plan_path") or ""),
        "target_profile": operation.get("target_profile") if isinstance(operation.get("target_profile"), Mapping) else {},
    }
    return stable_fingerprint(payload)


class T4ArtifactStore:
    """Workspace-scoped store for immutable T4 artifacts and mutable state."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = Path(workspace_dir)

    def path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("T4 artifact path must be workspace-relative")
        return self.workspace_dir / relative

    def write_model(self, relative_path: str | Path, model: BaseModel | dict[str, Any]) -> ArtifactWriteResult:
        payload = model_dump(model, mode="json") if isinstance(model, BaseModel) else model
        if not isinstance(payload, dict):
            raise TypeError("T4 artifact payload must be an object")
        return self.write_json(relative_path, payload)

    def write_json(self, relative_path: str | Path, payload: dict[str, Any]) -> ArtifactWriteResult:
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        destination = self.path(relative_path)
        try:
            if destination.is_file() and destination.read_bytes() == content.encode("utf-8"):
                return ArtifactWriteResult(path=Path(relative_path).as_posix(), changed=False, sha256=digest)
        except OSError:
            pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary_path = Path(temporary)
            if temporary_path.exists():
                temporary_path.unlink()
        return ArtifactWriteResult(path=Path(relative_path).as_posix(), changed=True, sha256=digest)

    def write_jsonl(self, relative_path: str | Path, records: list[dict[str, Any]]) -> ArtifactWriteResult:
        """Atomically write a JSONL artifact and reuse identical content."""

        content = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        destination = self.path(relative_path)
        try:
            if destination.is_file() and destination.read_bytes() == content.encode("utf-8"):
                return ArtifactWriteResult(path=Path(relative_path).as_posix(), changed=False, sha256=digest)
        except OSError:
            pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary_path = Path(temporary)
            if temporary_path.exists():
                temporary_path.unlink()
        return ArtifactWriteResult(path=Path(relative_path).as_posix(), changed=True, sha256=digest)

    def read_model(self, relative_path: str | Path, model_type: type[_ModelT]) -> _ModelT:
        path = self.path(relative_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read T4 artifact {Path(relative_path).as_posix()}: {exc}") from exc
        return model_validate(model_type, data)

    def has_valid_model(self, relative_path: str | Path, model_type: type[_ModelT]) -> bool:
        try:
            self.read_model(relative_path, model_type)
            return True
        except (TypeError, ValueError):
            return False

    def write_run_config(self, config: T4RunConfig) -> ArtifactWriteResult:
        return self.write_model("ideation/t4_run_config.json", config)

    def read_run_config(self) -> T4RunConfig:
        return self.read_model("ideation/t4_run_config.json", T4RunConfig)

    def write_population(self, population: PopulationSnapshot) -> ArtifactWriteResult:
        return self.write_model(f"ideation/populations/{population.population_id}.json", population)

    def read_population(self, population_id: str) -> PopulationSnapshot:
        return self.read_model(f"ideation/populations/{population_id}.json", PopulationSnapshot)

    def write_round(self, artifact: RoundArtifact) -> ArtifactWriteResult:
        return self.write_model(f"ideation/evolution/round_{artifact.round}.json", artifact)

    def write_candidate(self, dossier: CandidateDossier) -> ArtifactWriteResult:
        return self.write_model(f"ideation/candidates/{dossier.candidate_id}.v{dossier.version}.json", dossier)

    def write_state(self, state: T4InternalState) -> ArtifactWriteResult:
        return self.write_model(T4_STATE_REL_PATH, state)

    def read_state(self) -> T4InternalState:
        return self.read_model(T4_STATE_REL_PATH, T4InternalState)

    def migrate_t4_input_fingerprint_schema(self) -> dict[str, Any]:
        """Migrate safe T4 input-fingerprint schema changes without changing science.

        Early native T4 workspaces used ``core_shallow_read_notes`` and had no
        catalog fingerprint.  The current schema separates the Cross-domain
        catalog, but a zero-record catalog is derived entirely from the already
        fingerprinted bridge plan.  This migration updates all T4 receipts that
        bind the old fingerprint to the new schema and writes an explicit
        receipt.  It refuses migration as soon as the catalog contains an
        actual retrieved record or canonical note, because that is new research
        context and must receive a new T4 confirmation.
        """

        try:
            state = self.read_state()
        except ValueError as exc:
            return {"migrated": False, "reason": f"state_unavailable:{type(exc).__name__}"}

        current_fingerprints = build_t4_input_fingerprints(self.workspace_dir)
        current_fingerprint = stable_fingerprint(current_fingerprints)
        old_fingerprint = state.input_fingerprint
        if old_fingerprint == current_fingerprint and state.input_fingerprints == current_fingerprints:
            return {"migrated": False, "reason": "already_current"}

        legacy = {str(key): value for key, value in state.input_fingerprints.items()}
        migrated_fields: list[str] = []
        old_shallow = legacy.pop("core_shallow_read_notes", None)
        if old_shallow is not None and "core_abstract_notes" not in legacy:
            legacy["core_abstract_notes"] = old_shallow
            migrated_fields.append("core_shallow_read_notes_to_core_abstract_notes")

        if "cross_domain_catalogs" not in legacy:
            if not _cross_domain_catalogs_are_plan_only(self.workspace_dir):
                return {
                    "migrated": False,
                    "reason": "catalog_contains_new_retrieved_context_requires_confirmation",
                }
            legacy["cross_domain_catalogs"] = current_fingerprints["cross_domain_catalogs"]
            migrated_fields.append("add_plan_only_cross_domain_catalogs")

        if "literature_manifest" not in legacy:
            legacy["literature_manifest"] = current_fingerprints["literature_manifest"]
            migrated_fields.append("add_literature_manifest")

        # T4 now fingerprints the evidence-bearing projection of the shared
        # Literature Manifest.  The older whole-file hash also covered PDF
        # availability receipts, timestamps and derived counters.  Migrate it
        # only when every other scientific T4 input still matches; a changed
        # note, catalog, synthesis or seed must still require a fresh Gate.
        current_manifest = current_fingerprints["literature_manifest"]
        prior_manifest = legacy.get("literature_manifest")
        if (
            isinstance(prior_manifest, dict)
            and prior_manifest.get("schema") != T4_LITERATURE_MANIFEST_FINGERPRINT_SCHEMA
            and _fingerprint_maps_match_except_manifest(legacy, current_fingerprints)
        ):
            legacy["literature_manifest"] = current_manifest
            migrated_fields.append("literature_manifest_raw_to_evidence_contract_v1")

        if legacy != current_fingerprints:
            return {
                "migrated": False,
                "reason": "legacy_fingerprints_differ_beyond_derived_catalog_schema",
            }

        updated_paths: list[str] = []
        receipt_path = self.path("ideation/evolution/migrations/t4_input_schema_v3.json")
        state_path = self.path(T4_STATE_REL_PATH)
        confirmation_path = self.path("ideation/evolution/pre_run_confirmation.json")
        for path in sorted((self.workspace_dir / "ideation").rglob("*.json")):
            if path in {receipt_path, state_path, confirmation_path}:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not _replace_input_fingerprint(payload, old_fingerprint, current_fingerprint):
                continue
            self.write_json(path.relative_to(self.workspace_dir), payload)
            updated_paths.append(path.relative_to(self.workspace_dir).as_posix())

        try:
            confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            confirmation = None
        if isinstance(confirmation, dict) and confirmation.get("input_fingerprint") == old_fingerprint:
            confirmation["input_fingerprint"] = current_fingerprint
            confirmation.setdefault("compatibility_migrations", []).append("t4_input_schema_v3")
            self.write_json(confirmation_path.relative_to(self.workspace_dir), confirmation)
            updated_paths.append(confirmation_path.relative_to(self.workspace_dir).as_posix())

        updated_state = state.model_copy(
            update={
                "input_fingerprint": current_fingerprint,
                "input_fingerprints": current_fingerprints,
            }
        )
        self.write_state(updated_state)
        updated_paths.append(T4_STATE_REL_PATH)
        receipt = {
            "schema_version": "1.0.0",
            "semantics": "t4_input_schema_migration",
            "migration": "t4_input_schema_v3",
            "old_input_fingerprint": old_fingerprint,
            "new_input_fingerprint": current_fingerprint,
            "migrated_fields": migrated_fields,
            "rationale": (
                "The migration changes only fingerprint representation: an empty Cross-domain catalog is derived from the "
                "already-fingerprinted bridge plan, and the Literature Manifest now excludes PDF-availability bookkeeping "
                "while retaining note-card and catalog evidence. No scientific input was added."
            ),
            "updated_artifacts": updated_paths,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.write_json(receipt_path.relative_to(self.workspace_dir), receipt)
        return {"migrated": True, "receipt_path": receipt_path.relative_to(self.workspace_dir).as_posix(), **receipt}

    def migrate_derived_catalog_input_schema(self) -> dict[str, Any]:
        """Backward-compatible alias for the generalized T4 fingerprint migration."""

        return self.migrate_t4_input_fingerprint_schema()


    def read_final_card_repair_checkpoint(self) -> dict[str, Any] | None:
        """Read the durable Final Card recovery marker without trusting it yet.

        The caller should use :meth:`current_final_card_repair_checkpoint` for
        an execution decision.  Keeping this low-level reader separate makes
        diagnostics and Human Recovery Gate presentation possible even when a
        marker has become stale after a rollback or input change.
        """

        path = self.path(T4_FINAL_CARD_REPAIR_STATE_REL_PATH)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("semantics") != "t4_final_card_repair_checkpoint":
            return None
        return payload

    def write_final_card_repair_checkpoint(
        self,
        *,
        population: PopulationSnapshot,
        operation: Mapping[str, Any] | None,
        status: str = "pending_llm_card_compilation",
        reason: str = "",
        attempts: list[dict[str, Any]] | None = None,
    ) -> ArtifactWriteResult:
        """Persist that scientific work is consumed and only cards remain.

        This marker is created immediately after a controller operation has
        written and activated its Population, before the Final Card Compiler is
        called.  It is therefore the durable boundary that prevents a resumed
        process from running Generator, Scorer, or Evolver a second time when
        LLM-authored explanatory card prose is incomplete.
        """

        state = self.read_state()
        if state.current_population_id != population.population_id:
            raise ValueError(
                "cannot create a Final Card repair checkpoint for an inactive Population"
            )
        existing = self.read_final_card_repair_checkpoint()
        operation_payload = operation if isinstance(operation, Mapping) else {}
        directive = operation_payload.get("directive")
        directive_payload = directive if isinstance(directive, Mapping) else {}
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "schema_version": "1.0.0",
            "semantics": "t4_final_card_repair_checkpoint",
            "population_id": population.population_id,
            "population_generation": population.generation,
            "input_fingerprint": population.input_fingerprint,
            "run_config_fingerprint": population.run_config_fingerprint,
            "operation_consumed": True,
            "operation_identity": t4_operation_identity(operation),
            "operation_action": str(operation_payload.get("action") or "initial_t4_run"),
            "operation_directive_path": str(operation_payload.get("directive_path") or ""),
            "operation_directive_id": str(directive_payload.get("directive_id") or ""),
            "operation_requested_from_population": str(
                operation_payload.get("requested_from_population") or ""
            ),
            "status": str(status),
            "reason": str(reason),
            "attempts": list(attempts or []),
            "created_at": (
                str(existing.get("created_at") or now)
                if isinstance(existing, dict)
                and existing.get("population_id") == population.population_id
                and existing.get("operation_identity") == t4_operation_identity(operation)
                else now
            ),
            "updated_at": now,
        }
        return self.write_json(T4_FINAL_CARD_REPAIR_STATE_REL_PATH, payload)

    def update_final_card_repair_checkpoint(
        self,
        *,
        status: str,
        reason: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
        projection_completed: bool | None = None,
    ) -> ArtifactWriteResult:
        """Advance the current checkpoint without losing its operation link."""

        payload = self.read_final_card_repair_checkpoint()
        if payload is None:
            raise ValueError("cannot update a missing Final Card repair checkpoint")
        state = self.read_state()
        if payload.get("population_id") != state.current_population_id:
            raise ValueError("cannot update a Final Card checkpoint for an inactive Population")
        payload["status"] = str(status)
        if reason is not None:
            payload["reason"] = str(reason)
        if attempts is not None:
            payload["attempts"] = list(attempts)
        if projection_completed is not None:
            payload["projection_completed"] = bool(projection_completed)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self.write_json(T4_FINAL_CARD_REPAIR_STATE_REL_PATH, payload)

    def current_final_card_repair_checkpoint(
        self,
        *,
        operation: Mapping[str, Any] | None = None,
        pending_only: bool = True,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return a checkpoint only when it belongs to the active operation.

        A recovery gate may no longer retain the original operation in its
        transient state after a restart or a local rollback.  In that case the
        durable ``operation_consumed`` receipt is sufficient to resume the
        card-only path.  When a new operation *is* queued, its identity must
        match exactly; a stale card checkpoint must never suppress a newly
        requested evolution round.
        """

        payload = self.read_final_card_repair_checkpoint()
        if payload is None:
            return None, "missing Final Card repair checkpoint"
        state = self.read_state()
        try:
            population = self.read_population(state.current_population_id)
        except ValueError as exc:
            return None, str(exc)
        required = {
            "population_id": population.population_id,
            "population_generation": population.generation,
            "input_fingerprint": population.input_fingerprint,
            "run_config_fingerprint": population.run_config_fingerprint,
        }
        for key, expected in required.items():
            if payload.get(key) != expected:
                return None, f"Final Card repair checkpoint has stale {key}"
        if payload.get("operation_consumed") is not True:
            return None, "Final Card repair checkpoint does not prove the operation was consumed"
        if pending_only and str(payload.get("status") or "") not in {
            "pending_llm_card_compilation",
            "llm_repair_required",
            "cards_compiled_projection_pending",
        }:
            return None, "Final Card repair checkpoint is not pending"
        if isinstance(operation, Mapping):
            expected_operation = t4_operation_identity(operation)
            if payload.get("operation_identity") != expected_operation:
                return None, "Final Card repair checkpoint belongs to a different T4 operation"
        return payload, None

    def migrate_crossover_compatibility_records(self) -> dict[str, Any]:
        """Normalize resumable crossover records and leave an audit receipt.

        Older providers sometimes wrote a full complexity explanation into the
        enum-sized ``complexity_risk`` field.  The model-level normalizer keeps
        that explanation as a conflict note and uses a conservative high-risk
        label, but a completed workspace should not have to rely on every
        future consumer remembering to serialize that normalization.  This
        migration rewrites only successfully parsed compatibility decisions,
        never touches plans, Parents, Children, or scientific prose outside
        the original decision record.
        """

        plans_dir = self.path("ideation/evolution/plans")
        receipt_rel = "ideation/evolution/migrations/crossover_compatibility_v2.json"
        receipt: dict[str, Any] = {
            "schema_version": "1.0.0",
            "semantics": "t4_crossover_compatibility_migration",
            "migration": "crossover_compatibility_v2",
            "scanned_plan_paths": [],
            "migrated_plan_paths": [],
            "migrated_decision_count": 0,
            "unresolved": [],
            "performed_at": datetime.now(timezone.utc).isoformat(),
        }
        if not plans_dir.is_dir():
            receipt["status"] = "not_applicable"
            receipt["reason"] = "no_evolution_plan_directory"
            self.write_json(receipt_rel, receipt)
            return receipt

        for path in sorted(plans_dir.glob("round_*.json")):
            relative = path.relative_to(self.workspace_dir).as_posix()
            receipt["scanned_plan_paths"].append(relative)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                receipt["unresolved"].append({"path": relative, "reason": f"unreadable:{type(exc).__name__}"})
                continue
            if not isinstance(payload, dict) or payload.get("semantics") != "t4_evolution_plan_batch":
                continue
            raw_decisions = payload.get("crossover_decisions")
            if not isinstance(raw_decisions, list):
                receipt["unresolved"].append({"path": relative, "reason": "crossover_decisions_missing_or_not_list"})
                continue
            normalized_decisions: list[dict[str, Any]] = []
            changed = False
            for index, raw in enumerate(raw_decisions, start=1):
                if not isinstance(raw, dict):
                    receipt["unresolved"].append({"path": relative, "decision_index": index, "reason": "decision_not_object"})
                    normalized_decisions.append(raw)
                    continue
                try:
                    normalized = model_dump(model_validate(CrossoverCompatibilityDecision, raw), mode="json")
                except (TypeError, ValueError) as exc:
                    receipt["unresolved"].append(
                        {
                            "path": relative,
                            "decision_index": index,
                            "pair_id": str(raw.get("pair_id") or ""),
                            "reason": "unresolved_schema:" + " ".join(str(exc).split())[:500],
                        }
                    )
                    normalized_decisions.append(raw)
                    continue
                normalized_decisions.append(normalized)
                if normalized != raw:
                    changed = True
                    receipt["migrated_decision_count"] += 1
            if changed:
                payload["crossover_decisions"] = normalized_decisions
                self.write_json(relative, payload)
                receipt["migrated_plan_paths"].append(relative)

        receipt["status"] = "migrated" if receipt["migrated_plan_paths"] else "already_current"
        if receipt["unresolved"]:
            receipt["status"] = "partial" if receipt["migrated_plan_paths"] else "manual_review_required"
        self.write_json(receipt_rel, receipt)
        return receipt

    def ensure_final_card_checkpoint_for_completed_population(
        self,
        *,
        operation: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Recover the pre-card boundary for a legacy completed Population.

        Native T4 writes a Final Card checkpoint immediately after Population
        survival.  Runs created before that ordering existed can contain a
        valid active Population and Portfolio but no checkpoint when a process
        dies between survival selection and card compilation.  Re-running T4
        in that state used to initiate another Evolution round.  This method
        proves the persisted source set is internally coherent, creates only
        the missing receipt, and records an auditable reconciliation.  It
        never regenerates candidates, scores, routes, or scientific text.
        """

        existing, _existing_error = self.current_final_card_repair_checkpoint(
            operation=operation,
            pending_only=False,
        )
        if existing is not None:
            # This reconciliation is invoked only after Final Card readiness
            # has failed. A previously completed checkpoint can therefore be
            # retained as audit history but must become actionable again if a
            # card file was removed or found invalid after the former
            # projection completed.
            if str(existing.get("status") or "") == "completed":
                self.update_final_card_repair_checkpoint(
                    status="llm_repair_required",
                    reason="final_card_readiness_failed_after_prior_completion",
                    projection_completed=False,
                )
                existing, existing_error = self.current_final_card_repair_checkpoint(
                    operation=operation,
                )
                return existing, existing_error
            return existing, None
        try:
            state = self.read_state()
            valid, state_error = self.validate_state_inputs(state)
            if not valid:
                return None, state_error or "active T4 input fingerprint is not current"
            population = self.read_population(state.current_population_id)
            if population.population_id != state.current_population_id:
                return None, "active Population identifier does not match T4 state"
            portfolio = self.read_model("ideation/portfolio.json", PortfolioSelection)
            if portfolio.population_id != population.population_id:
                return None, "T4 Portfolio belongs to a different active Population"
            portfolio_ids = [
                candidate_id
                for candidate_id in [portfolio.lead_id, *portfolio.alternative_ids, *portfolio.high_upside_ids]
                if candidate_id
            ]
            if not portfolio_ids:
                return None, "T4 Portfolio has no visible Candidate"
            if len(portfolio_ids) != len(set(portfolio_ids)):
                return None, "T4 Portfolio contains duplicate Candidate IDs"
            unknown = sorted(set(portfolio_ids) - set(population.active_candidate_ids))
            if unknown:
                return None, "T4 Portfolio references Candidates outside the active Population: " + ", ".join(unknown)
            for candidate_id in portfolio_ids:
                matches = sorted(self.path("ideation/candidates").glob(f"{candidate_id}.v*.json"))
                if not matches:
                    return None, f"T4 Portfolio Candidate Dossier is missing: {candidate_id}"
                self.read_model(matches[-1].relative_to(self.workspace_dir), CandidateDossier)
            score_population_id = "P0" if population.generation == 0 else f"U{population.generation}"
            score_path = self.path(f"ideation/scoring/{score_population_id}.json")
            score_payload = json.loads(score_path.read_text(encoding="utf-8"))
            raw_scores = score_payload.get("scores") if isinstance(score_payload, dict) else None
            if not isinstance(raw_scores, list):
                return None, f"T4 score artifact has no scores list: ideation/scoring/{score_population_id}.json"
            score_ids = {
                model_validate(ScoreReport, raw).candidate_id
                for raw in raw_scores
                if isinstance(raw, dict)
            }
            missing_scores = [candidate_id for candidate_id in population.active_candidate_ids if candidate_id not in score_ids]
            if missing_scores:
                return None, "active Population is missing independent scores: " + ", ".join(missing_scores)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return None, "cannot prove legacy final-card source boundary: " + " ".join(str(exc).split())[:800]

        self.write_final_card_repair_checkpoint(
            population=population,
            operation=operation,
            status="pending_llm_card_compilation",
            reason="legacy_completed_population_missing_final_card_checkpoint",
        )
        checkpoint, checkpoint_error = self.current_final_card_repair_checkpoint(
            operation=operation,
        )
        if checkpoint is None:
            return None, checkpoint_error or "failed to read reconciled Final Card checkpoint"
        receipt = {
            "schema_version": "1.0.0",
            "semantics": "t4_final_card_checkpoint_reconciliation",
            "population_id": population.population_id,
            "population_generation": population.generation,
            "portfolio_candidate_ids": portfolio_ids,
            "checkpoint_path": T4_FINAL_CARD_REPAIR_STATE_REL_PATH,
            "action": "created_missing_checkpoint_only",
            "reason": "legacy_completed_population_missing_final_card_checkpoint",
            "performed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.write_json("ideation/evolution/migrations/final_card_checkpoint_reconciliation.json", receipt)
        return checkpoint, None

    def initialize_state(self, *, config: T4RunConfig, population: PopulationSnapshot) -> T4InternalState:
        current_input_fingerprints = build_t4_input_fingerprints(self.workspace_dir)
        current_input = stable_fingerprint(current_input_fingerprints)
        current_config = run_config_fingerprint(config)
        if population.input_fingerprint != current_input:
            raise ValueError("population input fingerprint does not match current workspace")
        if population.run_config_fingerprint != current_config:
            raise ValueError("population config fingerprint does not match run config")
        self.write_run_config(config)
        self.write_population(population)
        state = T4InternalState(
            phase=EvolutionPhase.FORMATION,
            generation=population.generation,
            configured_rounds=config.rounds,
            completed_rounds=0,
            current_population_id=population.population_id,
            display_candidate_ids=[],
            input_fingerprint=current_input,
            input_fingerprints=current_input_fingerprints,
            run_config_fingerprint=current_config,
            last_completed_artifact=f"ideation/populations/{population.population_id}.json",
            generation_history=[population.population_id],
            archived_population_ids=[],
        )
        self.write_state(state)
        return state

    def write_phase_marker(
        self,
        *,
        phase: EvolutionPhase,
        generation: int,
        input_fingerprint: str,
        run_config_fingerprint: str,
        artifact_paths: list[str],
    ) -> ArtifactWriteResult:
        payload = {
            "schema_version": "1.0.0",
            "semantics": "t4_phase_completion_marker",
            "phase": phase.value,
            "generation": generation,
            "input_fingerprint": input_fingerprint,
            "run_config_fingerprint": run_config_fingerprint,
            "artifact_paths": artifact_paths,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.write_json(f"ideation/evolution/phases/{generation}_{phase.value}.json", payload)

    def phase_is_complete(
        self,
        *,
        phase: EvolutionPhase,
        generation: int,
        input_fingerprint: str,
        run_config_fingerprint: str,
    ) -> bool:
        marker_path = self.path(f"ideation/evolution/phases/{generation}_{phase.value}.json")
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(marker, dict):
            return False
        if marker.get("semantics") != "t4_phase_completion_marker":
            return False
        if marker.get("input_fingerprint") != input_fingerprint or marker.get("run_config_fingerprint") != run_config_fingerprint:
            return False
        paths = marker.get("artifact_paths") if isinstance(marker.get("artifact_paths"), list) else []
        return all(self.path(str(path)).exists() for path in paths)

    def validate_state_inputs(self, state: T4InternalState) -> tuple[bool, str | None]:
        migration = self.migrate_derived_catalog_input_schema()
        if migration.get("migrated"):
            state = self.read_state()
        # T4 deliberately normalizes empty reading directories and plan-only
        # Cross-domain catalogs in ``build_t4_input_fingerprints``.  Calling
        # the generic directory validator here used a different representation
        # and could mark a Population stale solely because runtime-created
        # ``_DIR_GUIDE.md`` files made an empty bridge directory exist.  The
        # recovery path must compare exactly the scientific-input semantics
        # used when the Population and its state were written.
        if not isinstance(state.input_fingerprints, dict):
            return False, "T4 population 缺少 input_fingerprints，必须刷新"
        current_fingerprints = build_t4_input_fingerprints(self.workspace_dir)
        stale: list[str] = []
        for label, current_item in current_fingerprints.items():
            previous = state.input_fingerprints.get(label)
            if not isinstance(previous, dict):
                stale.append(label)
                continue
            if bool(previous.get("exists")) != bool(current_item.get("exists")):
                stale.append(label)
                continue
            if current_item.get("exists") and str(previous.get("sha256") or "") != str(current_item.get("sha256") or ""):
                stale.append(label)
        if stale:
            return False, "T4 population 对应输入已变化，必须刷新: " + ", ".join(stale)
        current = stable_fingerprint(current_fingerprints)
        if state.input_fingerprint != current:
            return False, "T4 population input fingerprint is stale"
        try:
            run_config = self.read_run_config()
        except ValueError as exc:
            return False, str(exc)
        if state.run_config_fingerprint != run_config_fingerprint(run_config):
            return False, "T4 run configuration fingerprint is stale"
        return True, None

    def activate_population(self, population_id: str, *, phase: EvolutionPhase = EvolutionPhase.WAITING_HUMAN) -> T4InternalState:
        state = self.read_state()
        population = self.read_population(population_id)
        if population.input_fingerprint != state.input_fingerprint or population.run_config_fingerprint != state.run_config_fingerprint:
            raise ValueError("cannot activate population with different fingerprints")
        history = list(state.generation_history)
        if population_id not in history:
            history.append(population_id)
        archived = list(state.archived_population_ids)
        previous = state.current_population_id
        if previous and previous != population_id and previous not in archived:
            archived.append(previous)
        updated = state.model_copy(
            update={
                "phase": phase,
                "generation": population.generation,
                "completed_rounds": population.generation,
                "configured_rounds": max(state.configured_rounds, population.generation),
                "current_population_id": population_id,
                "generation_history": history,
                "archived_population_ids": archived,
                "last_completed_artifact": f"ideation/populations/{population_id}.json",
            }
        )
        self.write_state(updated)
        return updated
