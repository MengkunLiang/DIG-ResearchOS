"""Durable project-level autonomy settings and safe Gate defaults."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


WORKFLOW_MODE_PATH = "_runtime/workflow_mode.json"

AUTO_PRESETS: dict[str, dict[str, str]] = {
    "research_ccf": {
        "literature_preset": "standard_research",
        "t4_mode": "auto",
        "publication_orientation": "ccf_cs",
        "survey_policy": "skip",
        "writing_style": "ccf_a",
    },
    "research_utd": {
        "literature_preset": "standard_research",
        "t4_mode": "auto",
        "publication_orientation": "utd_is",
        "survey_policy": "skip",
        "writing_style": "is",
    },
    "survey_ccf": {
        "literature_preset": "survey_balanced",
        "t4_mode": "auto",
        "publication_orientation": "ccf_cs",
        "survey_policy": "write_with_supplement",
        "writing_style": "ccf_a",
    },
    "survey_utd": {
        "literature_preset": "survey_balanced",
        "t4_mode": "auto",
        "publication_orientation": "utd_is",
        "survey_policy": "write_with_supplement",
        "writing_style": "is",
    },
    "survey_exhaustive_utd": {
        "literature_preset": "survey_exhaustive",
        "t4_mode": "deep",
        "publication_orientation": "utd_is",
        "survey_policy": "write_with_supplement",
        "writing_style": "is",
    },
}


def configure_workflow_mode(
    workspace: Path,
    *,
    mode: str,
    preset: str | None = None,
    t4_mode: str | None = None,
    selection_source: str = "api",
) -> dict[str, Any]:
    normalized_mode = str(mode or "copilot").strip().casefold()
    if normalized_mode not in {"auto", "copilot"}:
        raise ValueError("workflow mode must be auto or copilot")
    existing = load_workflow_mode(workspace) if (workspace / WORKFLOW_MODE_PATH).is_file() else _fallback_profile()
    selected_preset = str(preset or existing.get("preset") or "research_ccf")
    if selected_preset not in AUTO_PRESETS:
        raise ValueError(f"unknown automation preset: {selected_preset}")
    settings = dict(AUTO_PRESETS[selected_preset])
    # A mode-only run/resume switch must not silently replace an existing
    # publication profile or its explicitly selected T4 effort.
    if preset is None and t4_mode is None:
        existing_settings = existing.get("settings") if isinstance(existing.get("settings"), dict) else {}
        existing_t4_mode = str(existing_settings.get("t4_mode") or "")
        if existing_t4_mode in {"standard", "quick", "deep", "auto"}:
            settings["t4_mode"] = existing_t4_mode
    if t4_mode:
        if t4_mode not in {"standard", "quick", "deep", "auto"}:
            raise ValueError("T4 mode must be standard, quick, deep, or auto")
        settings["t4_mode"] = t4_mode
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": "researchos_project_workflow_mode",
        "mode": normalized_mode,
        "preset": selected_preset,
        "settings": settings,
        "authorization_boundary": (
            "Auto may resolve preconfigured coverage, survey, T4 run, top-ranked Candidate, and writing-style Gates. "
            "It never auto-resolves recovery, failed novelty, external side-effect, or changed-research-scope Gates."
        ),
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "selection_source": str(selection_source or "api"),
    }
    path = workspace / WORKFLOW_MODE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def workflow_mode_needs_confirmation(workspace: Path) -> bool:
    """Return whether T1 must ask the researcher to choose a workflow mode.

    Older workspaces and the previous ``init-workspace`` default carried a
    silent Copilot fallback without recording that a researcher chose it.  It
    is safe for a runtime fallback, but not permission to suppress the first
    Auto/Copilot decision.
    """

    path = Path(workspace) / WORKFLOW_MODE_PATH
    if not path.is_file():
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return str(payload.get("selection_source") or "").strip() not in {
        "command_line",
        "t1_gate",
        "api",
    }


def parse_workflow_mode_answer(answer: str) -> tuple[str, str, str | None] | None:
    """Parse the small, explicit T1 workflow-mode menu without an LLM."""

    normalized = " ".join(str(answer or "").strip().casefold().split())
    if not normalized:
        return None
    if normalized in {"1", "[1]"} or "copilot" in normalized or "协作" in normalized:
        return "copilot", "research_ccf", None
    if normalized in {"2", "[2]"}:
        return "auto", "research_ccf", None
    if normalized in {"3", "[3]"}:
        return "auto", "research_utd", None
    if "auto" not in normalized and "自动" not in normalized:
        return None

    if "exhaustive" in normalized or "全面综述" in normalized:
        preset = "survey_exhaustive_utd"
    elif "survey" in normalized or "综述" in normalized:
        preset = "survey_utd" if any(token in normalized for token in ("utd", "is", "管理")) else "survey_ccf"
    elif any(token in normalized for token in ("utd", "informs", "is", "管理")):
        preset = "research_utd"
    else:
        preset = "research_ccf"

    t4_mode = next(
        (effort for effort in ("quick", "standard", "deep") if effort in normalized),
        None,
    )
    return "auto", preset, t4_mode


def load_workflow_mode(workspace: Path) -> dict[str, Any]:
    path = workspace / WORKFLOW_MODE_PATH
    if not path.is_file():
        return _fallback_profile()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _fallback_profile("workflow_mode.json cannot be parsed")
    if not isinstance(payload, dict):
        return _fallback_profile("workflow_mode.json must contain an object")

    mode = str(payload.get("mode") or "").strip().casefold()
    preset = str(payload.get("preset") or "research_ccf").strip()
    if mode not in {"auto", "copilot"}:
        return _fallback_profile("workflow mode is invalid")
    if preset not in AUTO_PRESETS:
        return _fallback_profile("workflow preset is invalid")
    settings = payload.get("settings")
    if settings is None:
        settings = dict(AUTO_PRESETS[preset])
    if not isinstance(settings, dict):
        return _fallback_profile("workflow settings must contain an object")
    normalized_settings = dict(AUTO_PRESETS[preset])
    configured_t4_mode = str(settings.get("t4_mode") or normalized_settings["t4_mode"])
    if configured_t4_mode not in {"standard", "quick", "deep", "auto"}:
        return _fallback_profile("workflow T4 mode is invalid")
    normalized_settings["t4_mode"] = configured_t4_mode
    normalized = dict(payload)
    normalized.update({"mode": mode, "preset": preset, "settings": normalized_settings})
    return normalized


def _fallback_profile(reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "copilot",
        "preset": "research_ccf",
        "settings": dict(AUTO_PRESETS["research_ccf"]),
    }
    if reason:
        payload["load_warning"] = reason
    return payload


def automatic_gate_result(
    workspace: Path,
    gate_id: str,
    *,
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a pre-authorized Gate decision, or None for a genuine pause."""

    profile = load_workflow_mode(workspace)
    if profile.get("mode") != "auto":
        return None
    settings = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    preset = str(settings.get("literature_preset") or "standard_research")
    t4_mode = str(settings.get("t4_mode") or "auto")
    orientation = str(settings.get("publication_orientation") or "ccf_cs")
    survey_policy = str(settings.get("survey_policy") or "skip")
    style = str(settings.get("writing_style") or "ccf_a")
    confirmation = (
        presentation.get("t4_directive_confirmation")
        if isinstance(presentation, dict)
        and isinstance(presentation.get("t4_directive_confirmation"), dict)
        else {}
    )
    if gate_id == "t4_gate1_selection_gate" and confirmation:
        # Only the pre-authorized transition of the current lead Candidate to
        # T4.5 is automatic.  A refinement, merge, profile change, or other
        # model-backed operation still pauses even in Auto mode.
        next_stage = str(confirmation.get("next_stage") or "")
        candidate_ids = confirmation.get("candidate_ids") if isinstance(confirmation.get("candidate_ids"), list) else []
        if "T4.5" not in next_stage or len(candidate_ids) != 1:
            return None
        return _automatic_result(
            profile,
            {"option_id": "confirm", "captured": {}},
        )

    lead_candidate_id = _current_t4_lead_candidate_id(workspace)
    decisions: dict[str, dict[str, Any]] = {
        "t2_literature_param_gate": {"option_id": preset, "captured": {}},
        "t2_literature_param_confirm_gate": {"option_id": "confirm_start_t2", "captured": {}},
        "t2_coverage_gate": {"option_id": "continue_to_t3", "captured": {}},
        "t4_prerun_gate": {
            "option_id": f"start_{t4_mode}",
            "captured": {"publication_orientation": orientation},
        },
        "t36_survey_gate": {
            "option_id": "yes_targeted_retrieval" if survey_policy == "write_with_supplement" else "no",
            "captured": {},
        },
        "t36_template_gate": {
            "option_id": "utd_informs" if style == "is" else "basic_en",
            "captured": {},
        },
        "t36_corpus_gate": {
            "option_id": "complete",
            "captured": {"supplement_target_papers": "8", "supplement_focus": "taxonomy coverage gaps"},
        },
        "t36_post_survey_gate": {"option_id": "continue_to_t4", "captured": {}},
        "t8_style_template_gate": {
            "option_id": "is_informs" if style == "is" else "basic_en",
            "captured": {},
        },
    }
    if lead_candidate_id:
        decisions["t4_gate1_selection_gate"] = {
            "option_id": "proceed_candidate",
            "captured": {
                "directive": f"推进 {lead_candidate_id}",
                "parsed_directive": {
                    "action": "select_candidate",
                    "target_candidate_ids": [lead_candidate_id],
                },
            },
        }
    result = decisions.get(gate_id)
    if result is None:
        return None
    return _automatic_result(profile, result)


def _automatic_result(profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(result),
        "automation": {
            "mode": "auto",
            "preset": profile.get("preset"),
            "preauthorized_at": profile.get("configured_at"),
        },
    }


def _current_t4_lead_candidate_id(workspace: Path) -> str:
    """Return the typed Portfolio lead, never a guessed display handle."""

    path = Path(workspace) / "ideation" / "portfolio.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    lead = str(payload.get("lead_id") or "").strip()
    portfolio_ids = {
        str(value).strip()
        for value in [
            payload.get("lead_id"),
            *(payload.get("alternative_ids") if isinstance(payload.get("alternative_ids"), list) else []),
            *(payload.get("high_upside_ids") if isinstance(payload.get("high_upside_ids"), list) else []),
        ]
        if str(value or "").strip()
    }
    return lead if lead and lead in portfolio_ids else ""
