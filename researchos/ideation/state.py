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
from typing import Any, TypeVar

from pydantic import BaseModel

from ..pydantic_compat import model_dump, model_validate
from ..runtime.artifact_fingerprints import build_input_fingerprints, validate_input_fingerprints
from .models import CandidateDossier, EvolutionPhase, PopulationSnapshot, RoundArtifact, T4InternalState, T4RunConfig


T4_STATE_REL_PATH = "ideation/evolution/state.json"
T4_INPUT_FINGERPRINT_PATHS: dict[str, str] = {
    "project": "project.yaml",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "core_deep_notes": "literature/paper_notes",
    "core_abstract_notes": "literature/paper_notes_abstract",
    "bridge_notes": "literature/paper_notes_bridge",
    "legacy_deep_notes": "literature/deep_read_notes",
    "legacy_shallow_notes": "literature/shallow_read_notes",
    "legacy_bridge_notes": "literature/bridge_notes",
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

    return build_input_fingerprints(Path(workspace_dir), T4_INPUT_FINGERPRINT_PATHS)


def t4_input_fingerprint(workspace_dir: Path) -> str:
    return stable_fingerprint(build_t4_input_fingerprints(workspace_dir))


def run_config_fingerprint(run_config: T4RunConfig) -> str:
    return stable_fingerprint(model_dump(run_config, mode="json"))


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        ok, error = validate_input_fingerprints(
            self.workspace_dir,
            state.input_fingerprints,
            T4_INPUT_FINGERPRINT_PATHS,
            label_for_error="T4 population",
        )
        if not ok:
            return False, error
        current = t4_input_fingerprint(self.workspace_dir)
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
                "current_population_id": population_id,
                "generation_history": history,
                "archived_population_ids": archived,
                "last_completed_artifact": f"ideation/populations/{population_id}.json",
            }
        )
        self.write_state(updated)
        return updated
