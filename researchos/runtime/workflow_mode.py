"""Durable project-level autonomy settings and safe Gate defaults."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from ..latex_templates import available_ccf_template_ids, normalize_ccf_template_id


WORKFLOW_MODE_PATH = "_runtime/workflow_mode.json"


def _available_ccf_template_ids() -> set[str]:
    """Resolve the local bundle once at use time, not at module import time."""

    return available_ccf_template_ids(Path(__file__).resolve().parents[2])

AUTO_PRESETS: dict[str, dict[str, str]] = {
    "research_ccf": {
        "literature_preset": "standard_research",
        "t4_mode": "auto",
        "publication_orientation": "ccf_cs",
        "survey_policy": "skip",
        "writing_style": "ccf_a",
        "proposal_tracks": "one",
        "template_family": "ccf",
        "template_id": "",
        "writing_language": "en",
        "template_selection_source": "pending_t1",
    },
    "research_utd": {
        "literature_preset": "standard_research",
        "t4_mode": "auto",
        "publication_orientation": "utd_is",
        "survey_policy": "skip",
        "writing_style": "is",
        "proposal_tracks": "one",
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
        "template_selection_source": "preset_default",
    },
    "survey_ccf": {
        "literature_preset": "survey_balanced",
        "t4_mode": "auto",
        "publication_orientation": "ccf_cs",
        "survey_policy": "write_with_supplement",
        "writing_style": "ccf_a",
        "proposal_tracks": "one",
        "template_family": "ccf",
        "template_id": "",
        "writing_language": "en",
        "template_selection_source": "pending_t1",
    },
    "survey_utd": {
        "literature_preset": "survey_balanced",
        "t4_mode": "auto",
        "publication_orientation": "utd_is",
        "survey_policy": "write_with_supplement",
        "writing_style": "is",
        "proposal_tracks": "one",
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
        "template_selection_source": "preset_default",
    },
    "survey_exhaustive_utd": {
        "literature_preset": "survey_exhaustive",
        "t4_mode": "deep",
        "publication_orientation": "utd_is",
        "survey_policy": "write_with_supplement",
        "writing_style": "is",
        "proposal_tracks": "one",
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
        "template_selection_source": "preset_default",
    },
}

_LITERATURE_PRESET_SUMMARIES = {
    "standard_research": "研究论文覆盖：40 篇候选，25 篇精读，15 篇摘要轻读",
    "survey_balanced": "综述均衡覆盖：80 篇候选，40 篇精读，40 篇摘要轻读",
    "survey_exhaustive": "综述强覆盖：90 篇候选，40 篇精读，50 篇摘要轻读",
}

# Copilot is a control mode, not a publication profile.  It may retain the
# selected literature coverage family for resume compatibility, but it must
# never carry a hidden CCF/CS or UTD/IS answer into the T4 pre-run Gate.
_COPILOT_PENDING_ORIENTATION = "pending_user"
_COPILOT_PRESET = "copilot"
_EXECUTION_SETUP_CONFIRMATIONS = {"1", "[1]", "confirm", "确认", "确认默认", "确认保存", "默认", "保存", "yes", "是"}


def _copilot_settings() -> dict[str, str]:
    """Return the non-authorizing defaults for researcher-controlled mode."""

    return {
        "literature_preset": "standard_research",
        "t4_mode": "auto",
        "publication_orientation": _COPILOT_PENDING_ORIENTATION,
        "survey_policy": "ask",
        "writing_style": _COPILOT_PENDING_ORIENTATION,
        "proposal_tracks": "one",
        "template_family": _COPILOT_PENDING_ORIENTATION,
        "template_id": "",
        "writing_language": "",
        "template_selection_source": "pending_user",
    }


def _apply_template_settings(
    settings: dict[str, Any],
    *,
    mode: str,
    existing_settings: Mapping[str, Any],
    preserve_existing: bool,
    template_id: str | None,
) -> None:
    """Normalize template identity without letting a profile change leak it.

    The workflow profile is deliberately the one durable owner of the early
    template decision.  A CCF/CS orientation alone has no concrete conference
    identity, while the UTD/IS branch has exactly one bundled default.  An
    explicit preset change starts from that preset's template policy; a
    mode-only update can preserve a compatible, already confirmed conference.
    """

    if mode != "auto":
        settings.update(
            {
                "template_family": _COPILOT_PENDING_ORIENTATION,
                "template_id": "",
                "writing_language": "",
                "template_selection_source": "pending_user",
            }
        )
        if template_id is not None:
            raise ValueError("Copilot 模式没有可预先授权的 CCF 模板；请先切换到 Auto + CCF/CS。")
        return

    orientation = str(settings.get("publication_orientation") or "")
    if orientation == "utd_is":
        requested = normalize_ccf_template_id(template_id) if template_id is not None else ""
        if requested and requested != "informs":
            raise ValueError("UTD/IS 当前只支持 INFORMS ISRE 模板（template_id=informs）。")
        settings.update(
            {
                "template_family": "utd",
                "template_id": "informs",
                "writing_language": "en",
                "template_selection_source": "preset_default",
            }
        )
        return

    if orientation == "ccf_cs":
        requested = normalize_ccf_template_id(template_id) if template_id is not None else ""
        available = _available_ccf_template_ids()
        if template_id is not None and requested not in available:
            raise ValueError("CCF 模板必须是当前本机已安装的会议 template_id。")
        existing_orientation = str(existing_settings.get("publication_orientation") or "")
        existing_template = normalize_ccf_template_id(str(existing_settings.get("template_id") or ""))
        if not requested and preserve_existing and existing_orientation == "ccf_cs" and existing_template in available:
            requested = existing_template
        settings.update(
            {
                "template_family": "ccf",
                "template_id": requested,
                "writing_language": "en",
                "template_selection_source": "explicit_configuration" if template_id is not None else "preserved" if requested else "pending_t1",
            }
        )
        return

    settings.update(
        {
            "template_family": _COPILOT_PENDING_ORIENTATION,
            "template_id": "",
            "writing_language": "",
            "template_selection_source": "pending_user",
        }
    )


def configure_workflow_mode(
    workspace: Path,
    *,
    mode: str,
    preset: str | None = None,
    t4_mode: str | None = None,
    literature_preset: str | None = None,
    proposal_tracks: str | None = None,
    template_id: str | None = None,
    startup_setup_confirmed: bool | None = None,
    selection_source: str = "api",
) -> dict[str, Any]:
    normalized_mode = str(mode or "copilot").strip().casefold()
    if normalized_mode not in {"auto", "copilot"}:
        raise ValueError("workflow mode must be auto or copilot")
    existing_path = workspace / WORKFLOW_MODE_PATH
    existing = load_workflow_mode(workspace) if existing_path.is_file() else _fallback_profile()
    if normalized_mode == "copilot":
        # ``preset`` remains a compatibility field for the prior literature
        # coverage family.  It is *not* a publication authorization in this
        # mode: the settings below explicitly retain ``pending_user``.
        selected_preset = str(preset or existing.get("preset") or "research_ccf")
        if selected_preset not in AUTO_PRESETS:
            selected_preset = "research_ccf"
        settings = _copilot_settings()
        # Explicit preset flags still control coverage/effort.  They do not
        # authorize the preset's publication orientation or writing style.
        preset_defaults = AUTO_PRESETS[selected_preset]
        settings["literature_preset"] = preset_defaults["literature_preset"]
        settings["t4_mode"] = preset_defaults["t4_mode"]
    else:
        selected_preset = str(preset or existing.get("preset") or "research_ccf")
        if selected_preset not in AUTO_PRESETS:
            selected_preset = "research_ccf"
        settings = dict(AUTO_PRESETS[selected_preset])
    # A mode-only run/resume switch preserves execution scale, but never a
    # publication orientation when switching to Copilot.
    if preset is None:
        existing_settings = existing.get("settings") if isinstance(existing.get("settings"), dict) else {}
        existing_t4_mode = str(existing_settings.get("t4_mode") or "")
        if existing_t4_mode in {"standard", "quick", "deep", "auto"}:
            settings["t4_mode"] = existing_t4_mode
        existing_literature_preset = str(existing_settings.get("literature_preset") or "")
        if existing_literature_preset in _LITERATURE_PRESET_SUMMARIES:
            settings["literature_preset"] = existing_literature_preset
        existing_proposal_tracks = str(existing_settings.get("proposal_tracks") or "")
        if existing_proposal_tracks in {"one", "top2"}:
            settings["proposal_tracks"] = existing_proposal_tracks
        if "startup_setup_confirmed" in existing_settings:
            settings["startup_setup_confirmed"] = bool(existing_settings.get("startup_setup_confirmed"))
    if t4_mode:
        if t4_mode not in {"standard", "quick", "deep", "auto"}:
            raise ValueError("T4 mode must be standard, quick, deep, or auto")
        settings["t4_mode"] = t4_mode
    if literature_preset:
        if literature_preset not in _LITERATURE_PRESET_SUMMARIES:
            raise ValueError("literature preset must be standard_research, survey_balanced, or survey_exhaustive")
        settings["literature_preset"] = literature_preset
    if proposal_tracks:
        if proposal_tracks not in {"one", "top2"}:
            raise ValueError("proposal tracks must be one or top2")
        settings["proposal_tracks"] = proposal_tracks
    existing_settings = existing.get("settings") if isinstance(existing.get("settings"), dict) else {}
    _apply_template_settings(
        settings,
        mode=normalized_mode,
        existing_settings=existing_settings,
        # A CCF-to-CCF preset change keeps an already confirmed conference;
        # switching from UTD/Copilot cannot leak a template because the helper
        # checks the prior orientation before preservation.
        preserve_existing=True,
        template_id=template_id,
    )
    if startup_setup_confirmed is not None:
        settings["startup_setup_confirmed"] = bool(startup_setup_confirmed)
    elif selection_source == "command_line":
        # ``run --workflow-mode ...`` and ``init-workspace --auto-preset ...``
        # have chosen an authorization style, but have not yet shown the full
        # coverage/effort/Proposal comparison. Let T1 render that one compact
        # confirmation instead of treating a partial flag set as consent to
        # every default. ``configure-workflow --yes`` passes an explicit True.
        settings["startup_setup_confirmed"] = False
    previous_history = existing.get("configuration_history") if isinstance(existing.get("configuration_history"), list) else []
    previous_record = {
        "mode": str(existing.get("mode") or ""),
        "preset": str(existing.get("preset") or ""),
        "settings": existing.get("settings") if isinstance(existing.get("settings"), dict) else {},
        "selection_source": str(existing.get("selection_source") or ""),
        "configured_at": str(existing.get("configured_at") or ""),
    }
    configured_at = datetime.now(timezone.utc).isoformat()
    history = [item for item in previous_history if isinstance(item, dict)]
    # Do not fabricate a history record from the in-memory legacy fallback or
    # a corrupt JSON file. A history entry is meaningful only when it refers
    # to an earlier durable, successfully parsed project setting.
    if existing_path.is_file() and not existing.get("load_warning") and any(previous_record.values()):
        history.append(previous_record)
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": "researchos_project_workflow_mode",
        "mode": normalized_mode,
        "preset": selected_preset,
        "settings": settings,
        "authorization_boundary": (
            "Auto may resolve preconfigured coverage, survey, T4 run, top-ranked Candidate, and UTD/IS writing-style Gates. "
            "It never selects an unresolved CCF/CS conference template. "
            "It never auto-resolves recovery, failed novelty, external side-effect, or changed-research-scope Gates."
            if normalized_mode == "auto"
            else "Copilot records future Gate recommendations only. Every research Gate remains a human decision; "
            "the profile never supplies a publication orientation, recovery decision, novelty verdict, or external execution authorization."
        ),
        "configured_at": configured_at,
        "selection_source": str(selection_source or "api"),
    }
    if history:
        # This is operational audit history, not research history. Retain a
        # bounded tail so settings can be changed freely without allowing this
        # small runtime receipt to grow without limit.
        payload["configuration_history"] = history[-20:]
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
        "configure_workflow",
    }


def workflow_startup_setup_needs_confirmation(workspace: Path) -> bool:
    """Whether a freshly selected mode still needs default-plan confirmation.

    Mode selection controls authorization style, but it does not fully answer
    how much literature to cover, how much T4 exploration to request, or how
    many independent Proposals to carry. Both Auto and Copilot therefore get
    one explicit default-settings confirmation after an interactive T1 mode
    choice. In Copilot the settings remain recommendations; they never make a
    human Gate automatic.
    """

    profile = load_workflow_mode(workspace)
    settings = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    if "startup_setup_confirmed" in settings:
        return not bool(settings.get("startup_setup_confirmed"))
    return str(profile.get("selection_source") or "").strip() in {"t1_gate", "command_line"}


def workflow_startup_template_needs_confirmation(workspace: Path) -> bool:
    """Whether Auto + CCF/CS still needs its concrete conference at T1.

    This is intentionally separate from the coverage/effort confirmation.  A
    CCF/CS paper family is not a LaTeX template, so treating it as one leaves
    Survey and manuscript assembly with an unresolved venue much later in the
    pipeline.  Legacy profiles with no template remain valid and are prompted
    when they next enter T1 or their existing T3.6/T8 style Gate.
    """

    profile = load_workflow_mode(workspace)
    if str(profile.get("mode") or "").casefold() != "auto":
        return False
    settings = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    if str(settings.get("publication_orientation") or "") != "ccf_cs":
        return False
    if str(settings.get("template_family") or "ccf") != "ccf":
        return True
    template_id = normalize_ccf_template_id(str(settings.get("template_id") or ""))
    return template_id not in _available_ccf_template_ids()


def workflow_auto_setup_needs_confirmation(workspace: Path) -> bool:
    """Backward-compatible Auto-only view of the startup confirmation state."""

    profile = load_workflow_mode(workspace)
    return profile.get("mode") == "auto" and workflow_startup_setup_needs_confirmation(workspace)


def is_execution_setup_confirmation_answer(answer: str) -> bool:
    """Return whether the user explicitly approves the displayed setup.

    Free-form parsing can propose a valid configuration, but it must not be
    confused with permission to write that proposal.  T1 uses this predicate
    to require one final confirmation after every actual default change.
    """

    normalized = " ".join(str(answer or "").strip().casefold().split())
    return normalized in _EXECUTION_SETUP_CONFIRMATIONS


def parse_workflow_mode_answer(answer: str) -> tuple[str, str, str | None] | None:
    """Parse the numbered T1 workflow-mode menu before an LLM fallback.

    Numbers are first-class inputs because this decision is presented as a
    compact terminal menu.  Keep the same mapping visible in the Rich panel
    and in this parser so a researcher never needs to reconstruct a wrapped
    command such as ``Auto survey_ccf`` from a narrow terminal table.
    """

    normalized = " ".join(str(answer or "").strip().casefold().split())
    if not normalized:
        return None
    if normalized in {"1", "[1]"} or "copilot" in normalized or "协作" in normalized:
        return "copilot", "research_ccf", None
    if normalized in {"2", "[2]"}:
        return "auto", "research_ccf", None
    if normalized in {"3", "[3]"}:
        return "auto", "research_utd", None
    if normalized in {"4", "[4]"}:
        return "auto", "survey_ccf", None
    if normalized in {"5", "[5]"}:
        return "auto", "survey_utd", None
    if normalized in {"6", "[6]"}:
        return "auto", "survey_exhaustive_utd", None
    if normalized in AUTO_PRESETS:
        return "auto", normalized, None
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


def parse_workflow_mode_proposal(value: object) -> tuple[str, str, str | None] | None:
    """Validate a bounded LLM proposal for first-run mode selection.

    The LLM may translate prose such as "I want every gate confirmed" into a
    small proposal, but it cannot introduce a mode, preset, or exploration
    value outside the same enum exposed by the CLI. Exact menu input still
    takes precedence in the caller.
    """

    if not isinstance(value, Mapping):
        return None
    mode = str(value.get("mode") or "").strip().casefold()
    preset = str(value.get("preset") or "").strip()
    t4_mode = str(value.get("t4_mode") or "").strip().casefold() or None
    if mode not in {"auto", "copilot"}:
        return None
    if preset not in AUTO_PRESETS:
        preset = "research_ccf" if mode == "copilot" else ""
    if not preset:
        return None
    if t4_mode is not None and t4_mode not in {"quick", "standard", "deep", "auto"}:
        return None
    return mode, preset, t4_mode


def parse_auto_execution_setup_answer(
    answer: str,
    *,
    current_preset: str,
    current_t4_mode: str,
    current_proposal_tracks: str = "one",
) -> tuple[str, str, str] | None:
    """Parse the deliberately small, durable Auto setup menu.

    This accepts a confirmation or the two researcher-facing knobs without
    turning a recovery-critical Gate into free-form configuration parsing.
    """

    normalized = " ".join(str(answer or "").strip().casefold().split())
    if is_execution_setup_confirmation_answer(normalized):
        return current_preset, current_t4_mode, current_proposal_tracks
    if not normalized:
        return None

    recognized = False

    if any(token in normalized for token in ("exhaustive", "强覆盖", "全面综述")):
        literature_preset = "survey_exhaustive"
        recognized = True
    elif any(token in normalized for token in ("balanced", "均衡", "survey", "综述")):
        literature_preset = "survey_balanced"
        recognized = True
    elif any(token in normalized for token in ("standard", "research", "研究", "轻量")):
        literature_preset = "standard_research"
        recognized = True
    else:
        literature_preset = current_preset

    # ``standard_research`` is a literature preset, not an implicit request
    # for standard T4 exploration.  Check the explicit T4 words first so
    # `standard_research deep top2` faithfully means deep exploration.
    effort = next((item for item in ("deep", "quick", "standard", "auto") if item in normalized), None)
    if effort is None:
        localized_effort = next(
            (
                mapped
                for token, mapped in (("快速", "quick"), ("标准", "standard"), ("深入", "deep"), ("深度", "deep"))
                if token in normalized
            ),
            None,
        )
        effort = localized_effort or current_t4_mode
        recognized = recognized or localized_effort is not None
    else:
        recognized = True
    if effort not in {"auto", "quick", "standard", "deep"}:
        return None
    top2_requested = any(
        token in normalized
        for token in (
            "top2",
            "two",
            "two proposals",
            "2 proposals",
            "multiple",
            "multi",
            "2份",
            "两个",
            "两份",
            "两条",
            "多个",
            "多份",
            "多条",
        )
    )
    one_requested = any(token in normalized for token in ("one", "single", "一份", "一个", "一条", "单个"))
    proposal_tracks = "top2" if top2_requested else "one" if one_requested else current_proposal_tracks
    recognized = recognized or top2_requested or one_requested
    if not recognized:
        return None
    if proposal_tracks not in {"one", "top2"}:
        return None
    return literature_preset, effort, proposal_tracks


def parse_execution_setup_proposal(
    value: object,
    *,
    current_preset: str,
    current_t4_mode: str,
    current_proposal_tracks: str,
) -> tuple[str, str, str] | None:
    """Validate a bounded LLM proposal for setup or later workflow changes."""

    if not isinstance(value, Mapping):
        return None
    literature_preset = str(value.get("literature_preset") or current_preset).strip()
    t4_mode = str(value.get("t4_mode") or current_t4_mode).strip().casefold()
    proposal_tracks = str(value.get("proposal_tracks") or current_proposal_tracks).strip().casefold()
    if literature_preset not in _LITERATURE_PRESET_SUMMARIES:
        return None
    if t4_mode not in {"auto", "quick", "standard", "deep"}:
        return None
    if proposal_tracks not in {"one", "top2"}:
        return None
    recognized = any(
        str(value.get(key) or "").strip()
        for key in ("literature_preset", "t4_mode", "proposal_tracks")
    )
    return (literature_preset, t4_mode, proposal_tracks) if recognized else None


def auto_execution_setup_summary(profile: dict[str, Any]) -> str:
    """Render only the settings a researcher must understand at T1."""

    settings = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    literature = str(settings.get("literature_preset") or "standard_research")
    return (
        f"文献覆盖：{_LITERATURE_PRESET_SUMMARIES.get(literature, literature)}；"
        f"T4 探索：{settings.get('t4_mode') or 'auto'}；"
        f"Proposal：{'前两条独立 Proposal' if settings.get('proposal_tracks') == 'top2' else '一条 Proposal'}；"
        f"写作取向：{settings.get('writing_style') or 'pending_user'}。"
    )


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
    persisted_preset = str(payload.get("preset") or "research_ccf").strip()
    if mode not in {"auto", "copilot"}:
        return _fallback_profile("workflow mode is invalid")
    if mode == "auto" and persisted_preset not in AUTO_PRESETS:
        return _fallback_profile("workflow preset is invalid")
    if mode == "copilot" and persisted_preset not in AUTO_PRESETS:
        # Keep the legacy coverage selector valid even when an old or edited
        # profile contains an unknown compatibility value.  Copilot's actual
        # authorization fields remain pending_user below.
        persisted_preset = "research_ccf"
    settings = payload.get("settings")
    if settings is None:
        settings = _copilot_settings() if mode == "copilot" else dict(AUTO_PRESETS[persisted_preset])
    if not isinstance(settings, dict):
        return _fallback_profile("workflow settings must contain an object")
    preset = persisted_preset if mode == "copilot" and persisted_preset in AUTO_PRESETS else persisted_preset
    normalized_settings = _copilot_settings() if mode == "copilot" else dict(AUTO_PRESETS[preset])
    configured_literature_preset = str(settings.get("literature_preset") or normalized_settings["literature_preset"])
    if configured_literature_preset not in _LITERATURE_PRESET_SUMMARIES:
        return _fallback_profile("workflow literature preset is invalid")
    normalized_settings["literature_preset"] = configured_literature_preset
    configured_t4_mode = str(settings.get("t4_mode") or normalized_settings["t4_mode"])
    if configured_t4_mode not in {"standard", "quick", "deep", "auto"}:
        return _fallback_profile("workflow T4 mode is invalid")
    normalized_settings["t4_mode"] = configured_t4_mode
    configured_proposal_tracks = str(settings.get("proposal_tracks") or normalized_settings.get("proposal_tracks") or "one")
    if configured_proposal_tracks not in {"one", "top2"}:
        return _fallback_profile("workflow proposal track setting is invalid")
    normalized_settings["proposal_tracks"] = configured_proposal_tracks
    # Profiles created before T1 template selection existed deliberately stay
    # usable.  Their CCF setting is normalized to an explicit pending value
    # rather than being treated as corrupt or silently mapped to basic_en.
    _apply_template_settings(
        normalized_settings,
        mode=mode,
        existing_settings=settings,
        preserve_existing=True,
        template_id=None,
    )
    if "startup_setup_confirmed" in settings:
        normalized_settings["startup_setup_confirmed"] = bool(settings.get("startup_setup_confirmed"))
    normalized = dict(payload)
    normalized.update({"mode": mode, "preset": preset, "settings": normalized_settings})
    return normalized


def _fallback_profile(reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "copilot",
        "preset": _COPILOT_PRESET,
        "settings": _copilot_settings(),
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
    proposal_tracks = str(settings.get("proposal_tracks") or "one")
    orientation = str(settings.get("publication_orientation") or "ccf_cs")
    survey_policy = str(settings.get("survey_policy") or "skip")
    style = str(settings.get("writing_style") or "ccf_a")
    template_id = normalize_ccf_template_id(str(settings.get("template_id") or ""))
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
        configured_tracks = str(settings.get("proposal_tracks") or "one")
        allowed_count = 2 if configured_tracks == "top2" else 1
        if "T4.5" not in next_stage or len(candidate_ids) < 1 or len(candidate_ids) > allowed_count:
            return None
        return _automatic_result(
            profile,
            {"option_id": "confirm", "captured": {}},
        )

    portfolio_candidate_ids = _current_t4_portfolio_candidate_ids(workspace)
    lead_candidate_id = portfolio_candidate_ids[0] if portfolio_candidate_ids else ""
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
        "t36_corpus_gate": {
            "option_id": "complete",
            "captured": {"supplement_target_papers": "8", "supplement_focus": "taxonomy coverage gaps"},
        },
        "t36_post_survey_gate": {"option_id": "continue_to_t4", "captured": {}},
    }
    if style == "is":
        # UTD/IS has a single supported default: the bundled INFORMS package.
        decisions["t36_template_gate"] = {"option_id": "utd_informs", "captured": {}}
        decisions["t8_style_template_gate"] = {"option_id": "is_informs", "captured": {}}
    elif style == "ccf_a":
        # CCF/CS only becomes automatic after T1 has persisted a concrete
        # conference.  Retain the two-level state-machine route so its normal
        # receipt and template validation still run, but feed both levels the
        # same researcher-selected venue instead of asking again at T3.6/T8.
        if template_id in _available_ccf_template_ids():
            ccf_captured = {
                "template_family": "ccf",
                "template_id": template_id,
                "writing_language": "en",
            }
            decisions["t36_template_gate"] = {"option_id": "ccf", "captured": dict(ccf_captured)}
            decisions["t36_ccf_template_gate"] = {
                "option_id": f"ccf_{template_id}",
                "captured": dict(ccf_captured),
            }
            decisions["t8_style_template_gate"] = {
                "option_id": "ccf",
                "captured": {**ccf_captured, "venue_style": "ccf_a"},
            }
            decisions["t8_ccf_template_gate"] = {
                "option_id": f"ccf_{template_id}",
                "captured": {**ccf_captured, "venue_style": "ccf_a"},
            }
    if lead_candidate_id:
        selected_ids = portfolio_candidate_ids[:2] if proposal_tracks == "top2" and len(portfolio_candidate_ids) >= 2 else [lead_candidate_id]
        action = "select_multiple" if len(selected_ids) > 1 else "select_candidate"
        decisions["t4_gate1_selection_gate"] = {
            "option_id": "proceed_multiple" if len(selected_ids) > 1 else "proceed_candidate",
            "captured": {
                "directive": "[Auto] " + ("分别推进 " if len(selected_ids) > 1 else "推进 ") + "、".join(selected_ids),
                "parsed_directive": {
                    "action": action,
                    "target_candidate_ids": selected_ids,
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


def _current_t4_portfolio_candidate_ids(workspace: Path) -> list[str]:
    """Return the typed Portfolio lead, never a guessed display handle."""

    path = Path(workspace) / "ideation" / "portfolio.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
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
    if not lead or lead not in portfolio_ids:
        return []
    ordered = [lead]
    for candidate_id in [
        *(payload.get("alternative_ids") if isinstance(payload.get("alternative_ids"), list) else []),
        *(payload.get("high_upside_ids") if isinstance(payload.get("high_upside_ids"), list) else []),
    ]:
        candidate_id = str(candidate_id or "").strip()
        if candidate_id and candidate_id in portfolio_ids and candidate_id not in ordered:
            ordered.append(candidate_id)
    return ordered


def _current_t4_lead_candidate_id(workspace: Path) -> str:
    ids = _current_t4_portfolio_candidate_ids(workspace)
    return ids[0] if ids else ""
