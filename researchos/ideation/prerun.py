"""Read-only T4 input inspection and validated Pre-run directives."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import T4EvolutionSettings
from .models import T4RunConfig
from .state import t4_input_fingerprint


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class T4InputInspection(_Model):
    status: Literal["ready", "ready_with_warnings", "blocked"]
    input_fingerprint: str
    materials: dict[str, int | str]
    artifact_paths: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[dict[str, str]] = Field(default_factory=list)


class T4PreRunDirective(_Model):
    action: Literal["start", "inspect", "pause"]
    mode: Literal["quick", "standard", "deep", "auto"] = "standard"
    allow_crossover: bool = True
    final_top_k: int = Field(default=3, ge=1, le=3)
    requested_rounds: int | None = Field(default=None, ge=0, le=3)
    raw_user_input: str = ""
    needs_confirmation: bool = False


_NOTE_ROOTS = (
    ("core_deep_cards", "literature/paper_notes", "core"),
    ("core_abstract_cards", "literature/paper_notes_abstract", "core"),
    ("bridge_cards", "literature/paper_notes_bridge", "bridge"),
    ("legacy_deep_cards", "literature/deep_read_notes", "core"),
    ("legacy_abstract_cards", "literature/shallow_read_notes", "core"),
    ("legacy_bridge_cards", "literature/bridge_notes", "bridge"),
)


def inspect_t4_inputs(workspace_dir: Path) -> T4InputInspection:
    """Inspect actual files for the pre-run Gate without calling an LLM."""

    workspace = Path(workspace_dir)
    materials: dict[str, int | str] = {
        "core_deep_cards": 0,
        "core_abstract_cards": 0,
        "bridge_deep_cards": 0,
        "bridge_abstract_cards": 0,
        "synthesis": "available" if (workspace / "literature/synthesis.md").is_file() else "missing",
        "synthesis_workbench": "available" if (workspace / "literature/synthesis_workbench.json").is_file() else "missing",
        "domain_map": "available" if (workspace / "literature/domain_map.json").is_file() else "missing",
        "comparison_table": "available" if (workspace / "literature/comparison_table.csv").is_file() else "missing",
        "survey_insights": "available" if (workspace / "ideation/survey_insights.json").is_file() else "unavailable",
        "user_seed_ideas": _material_status(workspace / "user_seeds/seed_ideas.md"),
        "user_constraints": _material_status(workspace / "user_seeds/seed_constraints.md"),
    }
    seen: set[Path] = set()
    for _label, relative, domain in _NOTE_ROOTS:
        root = workspace / relative
        for path in _paper_note_paths(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            reading_level = _reading_level(path)
            if domain == "bridge":
                materials["bridge_abstract_cards" if reading_level == "abstract" else "bridge_deep_cards"] += 1
            else:
                materials["core_abstract_cards" if reading_level == "abstract" else "core_deep_cards"] += 1

    required = {
        "project": "project.yaml",
        "synthesis": "literature/synthesis.md",
        "synthesis_workbench": "literature/synthesis_workbench.json",
        "domain_map": "literature/domain_map.json",
    }
    blocking = [
        {
            "artifact": path,
            "why": "T4 needs this upstream research artifact to build an Evidence Index and Opportunity Map.",
            "how_to_fix": "Resume the upstream stage that produces this artifact, then return to T4.",
        }
        for _label, path in required.items()
        if not (workspace / path).is_file()
    ]
    warnings: list[str] = []
    if not blocking and int(materials["core_deep_cards"]) == 0 and int(materials["core_abstract_cards"]) == 0:
        warnings.append("No readable Paper Cards were found. T4 can inspect synthesis artifacts, but evidence-linked candidates will be limited.")
    if int(materials["bridge_abstract_cards"]) and not int(materials["bridge_deep_cards"]):
        warnings.append("Bridge evidence is currently abstract-only. It may inspire a candidate, but it cannot establish a mechanism without a reading upgrade.")
    if materials["comparison_table"] == "missing":
        warnings.append("The comparison table is unavailable. T4 can continue, but baseline differentiation will be less specific.")
    status: Literal["ready", "ready_with_warnings", "blocked"]
    status = "blocked" if blocking else ("ready_with_warnings" if warnings else "ready")
    return T4InputInspection(
        status=status,
        input_fingerprint=t4_input_fingerprint(workspace),
        materials=materials,
        artifact_paths={key: value for key, value in required.items()},
        warnings=warnings,
        blocking_issues=blocking,
    )


def default_run_config(settings: T4EvolutionSettings, directive: T4PreRunDirective | None = None) -> T4RunConfig:
    """Build a validated run config from settings and a parsed user directive."""

    directive = directive or T4PreRunDirective(action="start")
    mode_rounds = {"quick": 0, "standard": 1, "deep": 2, "auto": 1}
    rounds = directive.requested_rounds if directive.mode == "auto" and directive.requested_rounds is not None else mode_rounds[directive.mode]
    return T4RunConfig(
        mode=directive.mode,
        rounds=rounds,
        allow_crossover=directive.allow_crossover,
        final_top_k=directive.final_top_k,
        max_initial_population=settings.population.max_initial_population,
        active_population_size=settings.population.active_population_target,
        max_offspring_per_round=settings.offspring.max_total,
        max_crossover_children=settings.offspring.crossover_maximum if directive.allow_crossover else 0,
        bridge_policy=settings.bridge_policy_default,
        route_quotas={item.route: item.maximum for item in settings.route_quotas},
        raw_user_input=directive.raw_user_input,
    )


def parse_t4_prerun_intent(text: str) -> T4PreRunDirective:
    """Deterministic validation fallback for a future LLM semantic parser.

    The runtime will first ask an LLM directive parser when configured. This
    conservative parser recognizes unambiguous menu language and makes all
    budget-changing requests explicit rather than guessing researcher intent.
    """

    raw = " ".join(str(text or "").strip().split())
    lowered = raw.casefold()
    if not raw:
        return T4PreRunDirective(action="start", raw_user_input=raw)
    if any(token in lowered for token in ("pause", "暂停")):
        return T4PreRunDirective(action="pause", raw_user_input=raw)
    if any(token in lowered for token in ("inspect", "查看材料", "查看输入", "证据覆盖")):
        return T4PreRunDirective(action="inspect", raw_user_input=raw)
    mode = "standard"
    if any(token in lowered for token in ("quick", "快速", "formation only")):
        mode = "quick"
    elif any(token in lowered for token in ("deep", "两轮", "2轮")):
        mode = "deep"
    elif "auto" in lowered or "自动" in lowered:
        mode = "auto"
    allow_crossover = not any(
        token in lowered
        for token in ("no crossover", "without crossover", "不要 crossover", "不使用 crossover", "禁用 crossover")
    )
    top_match = re.search(r"(?:top|show|展示|候选)\s*([1-3])\s*(?:个|candidates?)", lowered)
    top_k = int(top_match.group(1)) if top_match else 3
    round_match = re.search(r"(?:rounds?|轮)\s*([0-3])", lowered)
    requested_rounds = int(round_match.group(1)) if round_match and mode == "auto" else None
    needs_confirmation = not allow_crossover or (requested_rounds is not None and requested_rounds >= 3)
    return T4PreRunDirective(
        action="start",
        mode=mode,
        allow_crossover=allow_crossover,
        final_top_k=top_k,
        requested_rounds=requested_rounds,
        raw_user_input=raw,
        needs_confirmation=needs_confirmation,
    )


def _paper_note_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        path for path in root.rglob("*.md")
        if path.is_file() and not path.name.startswith("_") and path.name.lower() not in {"readme.md"}
    ]


def _reading_level(path: Path) -> Literal["deep", "abstract"]:
    path_text = path.as_posix().casefold()
    if "abstract" in path_text or "shallow" in path_text:
        return "abstract"
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:8000].casefold()
    except OSError:
        return "deep"
    return "abstract" if "[abstract" in head or "abstract-only" in head else "deep"


def _material_status(path: Path) -> str:
    if not path.is_file() or path.stat().st_size == 0:
        return "unavailable"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "unavailable"
    return "loaded" if text and text not in {"# Seed ideas", "# Seed constraints"} else "unavailable"
