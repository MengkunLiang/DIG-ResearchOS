"""T4 Ideation Agent — 假设生成与实验计划

基于文献综述生成研究假设和实验计划，通过两轮Gate确认。
输入: synthesis.md, missing_areas.md, seed_ideas.md
输出: hypotheses.md, exp_plan.yaml, risks.md, idea_rationales.json,
      idea_scorecard.yaml, rejected_ideas.md, gate_decisions.json,
      _pass1_forward_candidates.json, _pass2_grounding_review.json,
      _gate1_selection_brief.md, _gate1_candidate_cards.md,
      selected_idea_brief.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime, timezone

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..schemas.validator import validate_record
from ..tools.ideation_analysis import analyze_ideation_coverage
from ..literature_identity import is_placeholder_text
from .survey_writer import _validate_survey_insights_fingerprints
from ._common import (
    cdr_schema_prompt_summary,
    load_cdr_schema,
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)
from .guidance import load_agent_guidance


CROSS_DOMAIN_RELATIONS = {
    "mechanism_bridge",
    "method_transfer",
    "evaluation_or_metric_bridge",
    "baseline_or_dataset_relevance",
    "adjacent_application",
}

T4_CONTEXT_PACK_JSON = Path("ideation/t4_context_pack.json")
T4_CONTEXT_PACK_MD = Path("ideation/t4_context_pack.md")
T4_PROGRESS_MD = Path("ideation/t4_progress.md")

CROSS_DOMAIN_IDEA_ORIGINS = {
    "cross_domain_analogy",
    "bridge_synthesis",
}


def _weak_evidence_prompt_summary(synthesis_workbench_text: str) -> str:
    """Extract weak-evidence guardrails from synthesis_workbench for prompt visibility."""

    try:
        data = json.loads(synthesis_workbench_text) if synthesis_workbench_text.strip() else {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    weak = data.get("weak_evidence_and_resource_upgrade")
    if not isinstance(weak, dict) or not weak:
        return ""
    examples = weak.get("abstract_only_examples") if isinstance(weak.get("abstract_only_examples"), list) else []
    lines = [
        "semantics: weak_evidence_and_resource_upgrade_not_claim_evidence",
        f"abstract_only_count: {weak.get('abstract_only_count', 0)}",
        f"metadata_triage_available: {weak.get('metadata_triage_available', False)}",
        "allowed_use: coverage_hint_or_upgrade_candidate_not_mechanism_evidence",
        "rule: weak-only ideas must be not_supported_by_current_evidence, deferred, or resource-upgrade tasks; never selected claims.",
    ]
    for item in examples[:6]:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        title = str(item.get("title") or "").strip()
        allowed_use = str(item.get("allowed_use") or "").strip()
        lines.append(f"- {paper_id}: {title} | {allowed_use}")
    return "\n".join(lines)


def _shorten(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) > limit:
        return clean[: max(0, limit - 3)] + "..."
    return clean


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _note_card_usable_for_t4(card: dict[str, object]) -> bool:
    use = str(card.get("citation_use") or "").strip().lower()
    if use in {"do_not_cite", "do-not-cite", "excluded", "unrelated"}:
        return False
    if card.get("citation_allowed") is False:
        return False
    score = _float_or_none(card.get("citation_quality_score"))
    if score is not None and score < 0.55:
        return False
    combined = " ".join(
        str(card.get(key) or "")
        for key in ("gaps", "mechanism_claim", "design_rationale", "core_approach_view", "bridge_point")
    )
    if "与项目无关" in combined or "unrelated" in combined.lower():
        return False
    return True


def _note_card_t4_priority(card: dict[str, object]) -> tuple[int, float, int]:
    use = str(card.get("citation_use") or "").strip().lower()
    evidence = str(card.get("evidence_level") or "").strip().upper()
    use_priority = {
        "core_evidence": 4,
        "supporting_context": 3,
        "background_context": 2,
        "background": 1,
    }.get(use, 1)
    evidence_priority = 1 if "FULL" in evidence else 0
    score = _float_or_none(card.get("citation_quality_score"))
    return use_priority + evidence_priority, score if score is not None else 0.0, len(str(card.get("gaps") or ""))


def _note_card_prompt_summary(synthesis_workbench_text: str, *, limit: int = 10) -> str:
    """Expose compact paper-note section cues for idea generation."""

    try:
        data = json.loads(synthesis_workbench_text) if synthesis_workbench_text.strip() else {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    cards = data.get("all_note_cards")
    if not isinstance(cards, list):
        cards = []
        for key in ("notes", "abstract_notes"):
            items = data.get(key)
            if isinstance(items, list):
                cards.extend(items)
    cards = [card for card in cards if isinstance(card, dict) and _note_card_usable_for_t4(card)]
    cards.sort(key=_note_card_t4_priority, reverse=True)
    rows: list[str] = []
    for card in cards:
        title = _shorten(str(card.get("title") or card.get("paper_id") or "unknown"), 120)
        evidence = str(card.get("evidence_level") or "unknown")
        use = str(card.get("citation_use") or "unknown")
        score = card.get("citation_quality_score")
        parts = [f"- {title} | evidence={evidence} | use={use} | score={score}"]
        for label, key in (
            ("A", "core_approach_view"),
            ("B", "bridge_point"),
            ("§9", "gaps"),
            ("§13", "mechanism_claim"),
            ("§14", "design_rationale"),
            ("§18", "boundary_conditions"),
            ("Raw abstract", "raw_abstract"),
        ):
            value = _shorten(str(card.get(key) or ""), 180)
            if value:
                parts.append(f"  {label}: {value}")
        rows.append("\n".join(parts))
        if len(rows) >= limit:
            break
    return "\n".join(rows)[:4500]


def _json_loads_or_empty(text: str) -> dict[str, object]:
    try:
        data = json.loads(text) if text.strip() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        return _json_loads_or_empty(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def _workspace_rel(path: Path, workspace_dir: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_dir.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _source_file_info(workspace_dir: Path, rel: str) -> dict[str, object]:
    path = workspace_dir / rel
    info: dict[str, object] = {"path": rel, "exists": path.exists()}
    if path.exists() and path.is_file():
        try:
            info["size"] = path.stat().st_size
        except OSError:
            pass
    return info


def _collect_workbench_note_cards(workbench: dict[str, object]) -> list[dict[str, object]]:
    cards = workbench.get("all_note_cards")
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)]
    collected: list[dict[str, object]] = []
    for key in ("notes", "abstract_notes"):
        items = workbench.get(key)
        if isinstance(items, list):
            collected.extend(card for card in items if isinstance(card, dict))
    return collected


def _section_excerpt_from_markdown(text: str, names: tuple[str, ...], *, limit: int = 450) -> str:
    lines = text.splitlines()
    normalized_names = tuple(name.lower() for name in names)
    capture: list[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        heading_match = re.match(r"^(#{2,6})\s*(.+?)\s*$", stripped)
        if heading_match:
            title = re.sub(r"^[A-Z]\.\s*", "", heading_match.group(2).strip(), flags=re.IGNORECASE)
            title = re.sub(r"^§\d+\s*", "", title).strip().lower()
            if any(name in title for name in normalized_names):
                active = True
                continue
            if active:
                break
        if active:
            capture.append(stripped)
    return _shorten(" ".join(line for line in capture if line), limit)


def _markdown_note_card(path: Path, workspace_dir: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    title_match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    abstract_only = "abstract-only" in text.lower() or "paper_notes_abstract" in path.as_posix()
    if "do_not_cite" in text.lower() or "do-not-cite" in text.lower():
        citation_use = "do_not_cite"
    elif abstract_only:
        citation_use = "background_context"
    else:
        citation_use = "supporting_context"
    card: dict[str, object] = {
        "note_id": path.stem,
        "paper_id": path.stem,
        "title": title,
        "source_file": _workspace_rel(path, workspace_dir),
        "evidence_level": "ABSTRACT_ONLY" if abstract_only else "FULL_TEXT",
        "citation_use": citation_use,
        "citation_quality_score": 0.55 if abstract_only else 0.7,
        "core_approach_view": _section_excerpt_from_markdown(
            text,
            ("core approach", "core approach/view", "core method", "核心做法", "核心视角"),
        ),
        "bridge_point": _section_excerpt_from_markdown(
            text,
            ("bridge point", "bridge", "桥接点", "迁移"),
        ),
        "gaps": _section_excerpt_from_markdown(
            text,
            ("gap", "gaps", "missing", "limitation", "缺口", "局限"),
        ),
        "mechanism_claim": _section_excerpt_from_markdown(
            text,
            ("mechanism claim", "mechanism", "机制"),
        ),
        "design_rationale": _section_excerpt_from_markdown(
            text,
            ("design rationale", "rationale", "设计论证", "设计理由"),
        ),
        "boundary_conditions": _section_excerpt_from_markdown(
            text,
            ("boundary condition", "boundary", "scope", "边界"),
        ),
        "raw_abstract": _section_excerpt_from_markdown(text, ("raw abstract", "abstract"), limit=320),
    }
    return card


def _collect_markdown_note_cards(workspace_dir: Path, *, limit: int = 40) -> list[dict[str, object]]:
    roots = [
        workspace_dir / "literature" / "paper_notes",
        workspace_dir / "literature" / "paper_notes_bridge",
        workspace_dir / "literature" / "paper_notes_abstract",
    ]
    cards: list[dict[str, object]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("**/*.md")):
            if path.name.startswith("_") or path.name.startswith("."):
                continue
            card = _markdown_note_card(path, workspace_dir)
            if card is not None:
                cards.append(card)
            if len(cards) >= limit:
                return cards
    return cards


def _compact_note_card(card: dict[str, object], workspace_dir: Path) -> dict[str, object]:
    source_file = card.get("source_file") or card.get("path") or card.get("note_path")
    if isinstance(source_file, str) and source_file:
        source_file = source_file.strip()
        if "/" not in source_file:
            for rel_root in (
                Path("literature/paper_notes"),
                Path("literature/paper_notes_bridge"),
                Path("literature/paper_notes_abstract"),
            ):
                root = workspace_dir / rel_root
                if not root.exists():
                    continue
                matches = list(root.glob(f"**/{source_file}"))
                if matches:
                    source_file = _workspace_rel(matches[0], workspace_dir)
                    break
    compact: dict[str, object] = {
        "note_id": str(card.get("note_id") or card.get("paper_id") or card.get("id") or "").strip(),
        "paper_id": str(card.get("paper_id") or card.get("raw_paper_id") or card.get("note_id") or "").strip(),
        "title": _shorten(str(card.get("title") or card.get("display_label") or "unknown"), 180),
        "year": card.get("year"),
        "venue": _shorten(str(card.get("venue") or ""), 100),
        "evidence_level": str(card.get("evidence_level") or "unknown"),
        "citation_use": str(card.get("citation_use") or "unknown"),
        "citation_quality_score": card.get("citation_quality_score"),
        "citation_ref": str(card.get("citation_ref") or "").strip(),
        "source_file": str(source_file or "").strip(),
    }
    for key in (
        "core_approach_view",
        "bridge_point",
        "gaps",
        "mechanism_claim",
        "design_rationale",
        "boundary_conditions",
        "raw_abstract",
    ):
        value = _shorten(_stringify_note_card_field(card.get(key)), 300)
        if value:
            compact[key] = value
    if not compact["source_file"] and compact["note_id"]:
        candidate = workspace_dir / "literature" / "paper_notes" / f"{compact['note_id']}.md"
        if candidate.exists():
            compact["source_file"] = _workspace_rel(candidate, workspace_dir)
    return compact


def _stringify_note_card_field(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        preferred = (
            "stated_mechanism",
            "rationale",
            "works_when",
            "may_fail_when",
            "untested_boundary",
            "rationale_evidence",
            "rationale_weakness",
            "supporting_artifact",
            "evidence_type",
        )
        parts: list[str] = []
        for key in preferred:
            item = value.get(key)
            if item in (None, "", [], {}):
                continue
            parts.append(f"{key}: {_stringify_note_card_field(item)}")
            if len(parts) >= 3:
                break
        if not parts:
            for key, item in value.items():
                if item in (None, "", [], {}):
                    continue
                parts.append(f"{key}: {_stringify_note_card_field(item)}")
                if len(parts) >= 3:
                    break
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(_stringify_note_card_field(item) for item in value[:5] if item not in (None, "", [], {}))
    return " ".join(str(value).split())


def _compact_items(items: object, *, limit: int, fields: tuple[str, ...]) -> list[dict[str, object]]:
    if not isinstance(items, list):
        return []
    compact: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row: dict[str, object] = {}
        for field in fields:
            if field in item and item.get(field) not in (None, "", []):
                value = item.get(field)
                if isinstance(value, (dict, list)):
                    row[field] = _shorten(_stringify_note_card_field(value), 360)
                elif isinstance(value, str):
                    row[field] = _shorten(value, 360)
                else:
                    row[field] = value
        if row:
            compact.append(row)
        if len(compact) >= limit:
            break
    return compact


def _comparison_table_summary(path: Path) -> dict[str, object]:
    if not path.exists() or not path.is_file():
        return {"path": "literature/comparison_table.csv", "exists": False}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"path": "literature/comparison_table.csv", "exists": True, "readable": False}
    header = lines[0].split(",")[:12] if lines else []
    return {
        "path": "literature/comparison_table.csv",
        "exists": True,
        "row_count_estimate": max(0, len(lines) - 1),
        "columns_preview": [_shorten(col, 80) for col in header],
        "sample_rows": [_shorten(line, 260) for line in lines[1:4]],
    }


def prepare_t4_context_pack(workspace_dir: Path, *, card_limit: int = 18) -> dict[str, object]:
    """Build compact T4 context artifacts before the LLM starts."""

    workspace_dir = Path(workspace_dir)
    ideation_dir = workspace_dir / "ideation"
    ideation_dir.mkdir(parents=True, exist_ok=True)
    workbench_path = workspace_dir / "literature" / "synthesis_workbench.json"
    workbench = _read_json_file(workbench_path)
    raw_cards = _collect_workbench_note_cards(workbench)
    if not raw_cards:
        raw_cards = _collect_markdown_note_cards(workspace_dir)
    usable_cards = [
        _compact_note_card(card, workspace_dir)
        for card in raw_cards
        if isinstance(card, dict) and _note_card_usable_for_t4(card)
    ]
    usable_cards.sort(key=_note_card_t4_priority, reverse=True)
    selected_cards = usable_cards[:card_limit]

    bridge_plan = _read_json_file(workspace_dir / "literature" / "bridge_domain_plan.json")
    domain_map = _read_json_file(workspace_dir / "literature" / "domain_map.json")
    pack: dict[str, object] = {
        "version": "1.0",
        "semantics": "t4_compact_ideation_context_pack",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recommended_use": [
            "Use ideation/t4_context_pack.md or this JSON before broad-scanning paper_notes.",
            "Open individual note files only when a specific claim or citation needs verification.",
            "Keep abstract-only material as weak idea fuel, not as strong claim evidence.",
        ],
        "source_files": [
            _source_file_info(workspace_dir, rel)
            for rel in (
                "literature/synthesis.md",
                "literature/missing_areas.md",
                "literature/synthesis_workbench.json",
                "literature/comparison_table.csv",
                "literature/domain_map.json",
                "literature/bridge_domain_plan.json",
                "ideation/survey_insights.json",
            )
        ],
        "note_card_summary": {
            "source": "synthesis_workbench.json" if workbench else "paper_note_markdown_scan",
            "raw_card_count": len(raw_cards),
            "usable_card_count": len(usable_cards),
            "selected_card_count": len(selected_cards),
            "deep_note_count": workbench.get("note_count", 0),
            "abstract_note_count": workbench.get("abstract_note_count", 0),
            "total_note_count": workbench.get("total_note_count", 0),
        },
        "note_cards": selected_cards,
        "mechanism_claim_clusters": _compact_items(
            workbench.get("mechanism_claim_clusters") or workbench.get("domain_consensus"),
            limit=6,
            fields=(
                "mechanism",
                "paper_count",
                "citation_refs",
                "evidence_strength_hint",
                "challengeable_hint",
                "allowed_use",
            ),
        ),
        "bridge_transfer_drafts": _compact_items(
            workbench.get("bridge_transfer_drafts"),
            limit=6,
            fields=(
                "bridge_id",
                "bridge_name",
                "relation_to_project",
                "transferable_mechanism",
                "how_it_maps_to_project",
                "why_potentially_novel",
                "risk",
                "allowed_use",
            ),
        ),
        "adjacent_transfers": _compact_items(
            workbench.get("adjacent_transfers"),
            limit=5,
            fields=(
                "source_field",
                "mechanism",
                "transfer_hypothesis_hint",
                "why_unused_in_target",
                "evidence_level",
                "allowed_use",
            ),
        ),
        "cross_paper_tensions": _compact_items(
            workbench.get("cross_paper_tensions"),
            limit=6,
            fields=("tension", "papers", "evidence", "why_it_matters"),
        ),
        "research_question_candidates": _compact_items(
            workbench.get("research_question_candidates"),
            limit=6,
            fields=("question", "rationale", "related_papers", "evidence_level"),
        ),
        "comparison_table": _comparison_table_summary(workspace_dir / "literature" / "comparison_table.csv"),
        "bridge_domain_plan": {
            "source": bridge_plan.get("source"),
            "bridge_domains": bridge_plan.get("bridge_domains", []),
        }
        if bridge_plan
        else {},
        "domain_map_preview": {
            "bucket_summary": domain_map.get("bucket_summary") or domain_map.get("domain_map_bucket_summary"),
            "theory_bridge": domain_map.get("theory_bridge"),
        }
        if domain_map
        else {},
    }

    pack["outputs"] = [T4_CONTEXT_PACK_JSON.as_posix(), T4_CONTEXT_PACK_MD.as_posix(), T4_PROGRESS_MD.as_posix()]
    json_path = workspace_dir / T4_CONTEXT_PACK_JSON
    md_path = workspace_dir / T4_CONTEXT_PACK_MD
    progress_path = workspace_dir / T4_PROGRESS_MD
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_render_t4_context_pack_markdown(pack), encoding="utf-8")
    progress_path.write_text(_render_t4_progress_markdown(pack), encoding="utf-8")
    return pack


def _render_t4_context_pack_markdown(pack: dict[str, object]) -> str:
    summary = pack.get("note_card_summary") if isinstance(pack.get("note_card_summary"), dict) else {}
    cards = pack.get("note_cards") if isinstance(pack.get("note_cards"), list) else []
    lines = [
        "# T4 Compact Context Pack",
        "",
        "This pack is the first-stop context for T4 ideation. It summarizes the usable note-card sections and compact grounding signals so the agent does not need to broad-scan every paper note.",
        "",
        "## Coverage",
        f"- Usable note cards: {summary.get('usable_card_count', 0)}",
        f"- Selected for prompt use: {summary.get('selected_card_count', 0)}",
        f"- Deep notes recorded in workbench: {summary.get('deep_note_count', 0)}",
        f"- Abstract/light notes recorded in workbench: {summary.get('abstract_note_count', 0)}",
        "",
        "## Reading Rule",
        "- Read this pack before opening individual note files.",
        "- Open a note file only to verify a specific claim, citation, or boundary condition.",
        "- Treat abstract-only material as weak idea fuel unless upgraded by a full note.",
        "",
        "## Selected Note Cards",
    ]
    for idx, card in enumerate(cards[:18], start=1):
        if not isinstance(card, dict):
            continue
        title = card.get("title") or card.get("note_id") or "unknown"
        lines.extend(
            [
                f"{idx}. {title}",
                f"   - evidence={card.get('evidence_level', 'unknown')} | use={card.get('citation_use', 'unknown')} | score={card.get('citation_quality_score', 'unknown')} | ref={card.get('citation_ref', '')}",
            ]
        )
        if card.get("source_file"):
            lines.append(f"   - source={card.get('source_file')}")
        for label, key in (
            ("Approach", "core_approach_view"),
            ("Bridge", "bridge_point"),
            ("Gap", "gaps"),
            ("Mechanism", "mechanism_claim"),
            ("Design rationale", "design_rationale"),
            ("Boundary", "boundary_conditions"),
            ("Raw abstract", "raw_abstract"),
        ):
            value = str(card.get(key) or "").strip()
            if value:
                lines.append(f"   - {label}: {value}")
    bridge_drafts = pack.get("bridge_transfer_drafts") if isinstance(pack.get("bridge_transfer_drafts"), list) else []
    if bridge_drafts:
        lines.extend(["", "## Bridge Transfer Seeds"])
        for item in bridge_drafts[:6]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('bridge_id', '')}: {_shorten(str(item.get('bridge_name') or item.get('transferable_mechanism') or ''), 220)}")
    mechanisms = pack.get("mechanism_claim_clusters") if isinstance(pack.get("mechanism_claim_clusters"), list) else []
    if mechanisms:
        lines.extend(["", "## Mechanism Challenge Seeds"])
        for item in mechanisms[:6]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {_shorten(str(item.get('mechanism') or ''), 240)}")
    lines.append("")
    return "\n".join(lines)


def _render_t4_progress_markdown(pack: dict[str, object]) -> str:
    summary = pack.get("note_card_summary") if isinstance(pack.get("note_card_summary"), dict) else {}
    outputs = pack.get("outputs") if isinstance(pack.get("outputs"), list) else [
        T4_CONTEXT_PACK_JSON.as_posix(),
        T4_CONTEXT_PACK_MD.as_posix(),
    ]
    return "\n".join(
        [
            "# T4 Progress",
            "",
            "- [ready] 已生成 Gate1 候选构思用 compact context pack。",
            (
                "- [evidence] 已从 "
                f"{summary.get('raw_card_count', 0)} 张候选笔记卡中筛出 "
                f"{summary.get('selected_card_count', 0)} 张可用卡片。"
            ),
            "- [rule] T4 会先读 compact pack，只在核验具体 claim 时打开单篇 note。",
            "- [outputs] " + "; ".join(str(item) for item in outputs),
            "- [pending] 待写入 Pass1 候选、Pass2 接地复核、候选方向和 Gate1 brief。",
            "",
        ]
    )


class IdeationAgent(Agent):
    """假设生成Agent。深度推理+两轮Gate确认。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "ideation",
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "list_files",
                        "grep_search",
                        "analyze_idea_concentration",
                        "compute_idea_novelty_signal",
                        "ask_human",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 200_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.75,
                    "allowed_read_prefixes": [
                        "",
                        "literature/",
                        "user_seeds/",
                        "ideation/",
                        "_runtime/resume/",
                    ],
                    "allowed_write_prefixes": ["ideation/"],
                    "prompt_template": "ideation.j2",
                    "structured_outputs": {
                        "ideation/exp_plan.yaml": "exp_plan",
                        "ideation/idea_rationales.json": "idea_rationales",
                        "ideation/idea_scorecard.yaml": "idea_scorecard",
                        "ideation/gate_decisions.json": "gate_decisions",
                        "ideation/bridge_coverage_review.json": "bridge_coverage_review",
                    },
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息和文献综述。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")
        missing_areas = read_text_file(ws / "literature" / "missing_areas.md", default="")
        seed_ideas = read_text_file(ws / "user_seeds" / "seed_ideas.md", default="")
        if is_placeholder_text(seed_ideas):
            seed_ideas = ""
        comparison_table = read_text_file(ws / "literature" / "comparison_table.csv", default="")
        domain_map = read_text_file(ws / "literature" / "domain_map.json", default="")
        bridge_domain_plan = read_text_file(ws / "literature" / "bridge_domain_plan.json", default="")
        synthesis_workbench = read_text_file(ws / "literature" / "synthesis_workbench.json", default="")
        survey_insights = read_text_file(ws / "ideation" / "survey_insights.json", default="")
        t4_context_pack = read_text_file(ws / T4_CONTEXT_PACK_MD, default="")
        weak_evidence_summary = _weak_evidence_prompt_summary(synthesis_workbench)
        note_card_summary = "" if t4_context_pack.strip() else _note_card_prompt_summary(synthesis_workbench)

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            synthesis_preview=synthesis[:8000],
            missing_areas=missing_areas[:2000],
            seed_ideas=seed_ideas[:2000],
            comparison_table_preview=comparison_table[:1000],
            domain_map_preview=domain_map[:2500],
            bridge_domain_plan_preview=bridge_domain_plan[:2500],
            synthesis_workbench_preview=synthesis_workbench[:3000],
            t4_context_pack_preview=t4_context_pack[:6500],
            weak_evidence_summary=weak_evidence_summary,
            note_card_summary=note_card_summary,
            survey_insights_preview=survey_insights[:3000],
            has_domain_map=bool(domain_map.strip()),
            has_bridge_domain_plan=bool(bridge_domain_plan.strip()),
            has_synthesis_workbench=bool(synthesis_workbench.strip()),
            has_t4_context_pack=bool(t4_context_pack.strip()),
            has_survey_insights=bool(survey_insights.strip()),
            has_seed_ideas=bool(seed_ideas.strip()),
            temperature=self.spec.temperature,
            agent_guidance=load_agent_guidance("ideation"),
            cdr_schema_summary=cdr_schema_prompt_summary(),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        from ..orchestration.state_machine import validate_t4_gate1_selection_file

        gate1_selection_ok, gate1_selection_error = validate_t4_gate1_selection_file(ctx.workspace_dir)
        if not gate1_selection_ok:
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T4 Gate1 前半段。当前尚无合法 ideation/_gate1_user_selection.json"
                f"（{gate1_selection_error}），所以本轮只生成并写入 Gate1 候选池中间产物："
                "先读取 ideation/t4_context_pack.md 或 ideation/t4_context_pack.json，"
                "并立刻更新 ideation/t4_progress.md 说明正在从 compact pack 生成候选；"
                "ideation/_pass1_forward_candidates.json、ideation/_pass2_grounding_review.json、"
                "ideation/_candidate_directions.json、ideation/_family_distribution.md、"
                "ideation/_gate1_candidate_cards.md、ideation/_gate1_selection_brief.md，"
                "以及必要时的 bridge_coverage_review.json。"
                "候选池必须在四类补充通道之外包含至少一个领域交叉候选："
                "idea_origin=cross_domain_analogy 或 bridge_synthesis。"
                "写完这些文件后立即调用 finish_task；不要在本轮调用 ask_human，也不要写"
                "hypotheses.md、exp_plan.yaml、risks.md 或 gate_decisions.json。runtime 会自动进入 T4-GATE1。"
                ),
            )
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T4 Gate1 后半段。必须先读取 ideation/_gate1_user_selection.json，"
            "再参考 ideation/t4_context_pack.md 或 ideation/t4_context_pack.json 做定向证据核对；"
            "并根据用户已确认/合并/重构的候选方向产出 hypotheses.md + exp_plan.yaml + "
            "risks.md + idea_rationales.json + idea_scorecard.yaml + "
            "rejected_ideas.md + selected_idea_brief.md + gate_decisions.json。"
            "最终输出必须绑定 Gate1 selection_fingerprint，并在 hypotheses.md 中为每个 H 写出"
            "技术机制、现实/管理/商业含义、评分依据和核心论文依赖。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构 + schema + 引用一致性。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir
        hyp_text = read_text_file(ws / "ideation" / "hypotheses.md")
        if len(hyp_text) < 500:
            return False, f"hypotheses.md 过短({len(hyp_text)} 字符)"

        # 提取假设anchors（支持 ## H1, ## H2 等格式）
        anchors = re.findall(r"^#+\s*(H\d+)", hyp_text, re.MULTILINE)
        if not anchors:
            return False, "hypotheses.md 必须包含假设anchor（## H1, ## H2等）"

        # 规范化anchors为大写
        anchor_set = set(a.upper() for a in anchors)

        try:
            plan_data = yaml.safe_load(read_text_file(ws / "ideation" / "exp_plan.yaml"))
        except Exception as e:
            return False, f"exp_plan.yaml 解析失败: {e}"
        ok, err = validate_record(plan_data, "exp_plan")
        if not ok:
            return False, f"exp_plan.yaml 不符合schema: {err}"

        experiments = plan_data.get("experiments", [])
        if not experiments:
            return False, "exp_plan.yaml 必须包含至少一个实验"

        # 检查hypothesis_ref引用
        for i, exp in enumerate(experiments):
            if "hypothesis_ref" in exp:
                raw_ref = exp["hypothesis_ref"]
                if isinstance(raw_ref, (list, tuple)):
                    refs = [str(ref).strip() for ref in raw_ref if str(ref).strip()]
                else:
                    refs = [
                        ref.strip()
                        for ref in re.split(r"[,;，、\s]+", str(raw_ref))
                        if ref.strip()
                    ]
                if not refs:
                    return False, f"实验{i+1}的hypothesis_ref 为空"
                for ref in refs:
                    # 移除可能的 # 前缀，并转为大写
                    ref_normalized = ref.lstrip("#").strip().upper()
                    if ref_normalized not in anchor_set:
                        return False, f"实验{i+1}的hypothesis_ref '{ref}' 不存在于hypotheses.md中（可用: {anchor_set}）"

        risks_text = read_text_file(ws / "ideation" / "risks.md")
        risk_markers = risks_text.count("## 风险") + risks_text.count("## Risk")
        if risk_markers < 3:
            return False, f"risks.md 至少需要3条风险，当前{risk_markers}条"

        rationales_path = ws / "ideation" / "idea_rationales.json"
        if not rationales_path.exists():
            return False, "缺少 ideation/idea_rationales.json，无法追踪每个idea的生成依据"
        try:
            rationale_data = json.loads(rationales_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"idea_rationales.json 解析失败: {e}"
        if not isinstance(rationale_data, dict):
            return False, "idea_rationales.json 必须是JSON对象"
        ok, err = validate_record(rationale_data, "idea_rationales")
        if not ok:
            return False, f"idea_rationales.json 不符合schema: {err}"

        ideas = rationale_data.get("ideas", [])
        if not isinstance(ideas, list) or not ideas:
            return False, "idea_rationales.json 必须包含至少一条idea依据记录"
        covered_refs: set[str] = set()
        for i, idea in enumerate(ideas, start=1):
            if not isinstance(idea, dict):
                return False, f"idea_rationales.json 第{i}条idea必须是对象"
            refs = idea.get("hypothesis_refs") or []
            for ref in refs:
                covered_refs.add(str(ref).lstrip("#").strip().upper())

            basis = idea.get("basis") or {}
            observations = basis.get("literature_observations") or []
            forward_reasoning = (
                basis.get("forward_reasoning")
                or basis.get("problem_reframing")
                or basis.get("analogy_basis")
                or basis.get("grounding_checks")
            )
            if not observations and not forward_reasoning:
                return False, (
                    f"idea_rationales.json 第{i}条idea缺少生成依据："
                    "可用 literature_observations，也可用 forward_reasoning/problem_reframing/"
                    "analogy_basis/grounding_checks，不能为通过 gate 伪造文献来源"
                )
            reasoning = str(idea.get("reasoning") or "").strip()
            if len(reasoning) < 10:
                return False, f"idea_rationales.json 第{i}条idea的reasoning过短"

        missing_rationales = sorted(anchor_set - covered_refs)
        if missing_rationales:
            return False, (
                "idea_rationales.json 必须覆盖 hypotheses.md 中的所有假设anchor，"
                f"缺少: {missing_rationales}"
            )

        scorecard_path = ws / "ideation" / "idea_scorecard.yaml"
        if not scorecard_path.exists():
            return False, "缺少 ideation/idea_scorecard.yaml，无法追踪候选idea证据链"
        try:
            scorecard_data = yaml.safe_load(scorecard_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"idea_scorecard.yaml 解析失败: {e}"
        if not isinstance(scorecard_data, dict):
            return False, "idea_scorecard.yaml 必须是YAML对象"
        ok, err = validate_record(scorecard_data, "idea_scorecard")
        if not ok:
            return False, f"idea_scorecard.yaml 不符合schema: {err}"

        scorecard_ideas = scorecard_data.get("ideas", [])
        if not isinstance(scorecard_ideas, list) or len(scorecard_ideas) < 2:
            return False, "idea_scorecard.yaml 至少需要记录2个候选idea，包含选中和淘汰/暂缓项"

        ok, err = _validate_pass_stage_artifacts(ws)
        if not ok:
            return False, err
        ok, err = _validate_candidate_directions(ws)
        if not ok:
            return False, err
        ok, err = _validate_bridge_coverage_review(ws)
        if not ok:
            return False, err

        # R1: mechanism / prediction / counterfactual / mechanism_family 必须存在
        _mechanism_fields = ("mechanism", "prediction", "counterfactual", "mechanism_family")
        placeholder_values = {
            "mechanism": {"see core_claim", "same as core_claim", "tbd", "todo", "n/a"},
            "prediction": {"qualitative: outperforms baseline", "outperforms baseline", "tbd", "todo", "n/a"},
            "counterfactual": {"no clear counterfactual", "tbd", "todo", "n/a"},
        }
        for i, item in enumerate(scorecard_ideas, start=1):
            if not isinstance(item, dict):
                continue
            idea = item.get("idea") or {}
            idea_id = str(idea.get("id") or f"#{i}")
            ok, err = _validate_cross_domain_provenance(item.get("source") or {}, idea_id, "idea_scorecard.yaml source")
            if not ok:
                return False, err
            ok, err = _validate_soft_novelty_fields(item, idea_id)
            if not ok:
                return False, err
            for field in _mechanism_fields:
                val = str(idea.get(field) or "").strip()
                if not val:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少必要字段 mechanism/{field}，"
                        "每个 idea 必须包含 mechanism, prediction, counterfactual, mechanism_family"
                    )
            decision = item.get("decision") or {}
            status = str(decision.get("status") or "").strip().lower()
            has_hypothesis_refs = bool(item.get("hypothesis_refs"))
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            constraint_status = str(
                source.get("constraint_status")
                or idea.get("constraint_status")
                or ""
            ).strip().lower()
            if constraint_status == "not_supported_by_current_evidence" and (status == "selected" or has_hypothesis_refs):
                return False, (
                    f"idea_scorecard.yaml idea {idea_id} 仅有弱证据或补资源语义，"
                    "不能被 selected，也不能绑定最终 hypothesis_refs"
                )
            if status == "selected" or has_hypothesis_refs:
                cdr_tuple = idea.get("cdr_tuple") if isinstance(idea, dict) else {}
                if not isinstance(cdr_tuple, dict):
                    cdr_tuple = {}
                design_rationale = str(
                    cdr_tuple.get("design_rationale")
                    or idea.get("design_rationale")
                    or ""
                ).strip()
                contribution_type = str(
                    cdr_tuple.get("contribution_type")
                    or idea.get("contribution_type")
                    or ""
                ).strip().lower()
                contribution_character = str(
                    item.get("selection_rationale", {}).get("contribution_character")
                    or idea.get("contribution_character")
                    or ""
                ).strip()
                contribution_strength = (
                    idea.get("contribution_strength")
                    or item.get("scores", {}).get("contribution_strength")
                )
                if not design_rationale:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少 CDR design_rationale；"
                        "选中或进入最终假设的 idea 必须说明为什么 artifact 应该这样设计"
                    )
                if contribution_type not in {"invention", "improvement", "exaptation"}:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 的 contribution_type 不能为 "
                        f"{contribution_type or '空'}；selected idea 不能是 routine"
                    )
                if len(contribution_character) < 20:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少 contribution_character："
                        "必须回答如果成立领域会怎样不同"
                    )
                try:
                    strength_value = float(contribution_strength)
                except (TypeError, ValueError):
                    return False, f"idea_scorecard.yaml idea {idea_id} 缺少 contribution_strength"
                if strength_value < 2:
                    return False, f"idea_scorecard.yaml idea {idea_id} contribution_strength 过低"
                for field, placeholders in placeholder_values.items():
                    val = str(idea.get(field) or "").strip().lower()
                    if val in placeholders:
                        return False, (
                            f"idea_scorecard.yaml idea {idea_id} 的 {field} 仍是占位语；"
                            "选中或进入最终假设的 idea 必须给出具体机制、预测和反事实"
                        )
            elif status in {"rejected", "deferred", "merged"}:
                has_placeholder = any(
                    str(idea.get(field) or "").strip().lower() in placeholders
                    for field, placeholders in placeholder_values.items()
                )
                if has_placeholder:
                    reasons = " ".join(str(v) for v in (decision.get("rejection_reason") or []))
                    if not re.search(r"机制未成形|反事实|mechanism|counterfactual", reasons, re.IGNORECASE):
                        return False, (
                            f"idea_scorecard.yaml {status} idea {idea_id} 使用机制占位语，"
                            "必须在 rejection_reason 中说明机制未成形或无法形成可检验反事实"
                        )

        # R2: _family_distribution.md 必须存在且长度 > 100
        family_dist_path = ws / "ideation" / "_family_distribution.md"
        if not family_dist_path.exists():
            return False, "缺少 ideation/_family_distribution.md，必须在生成 scorecard 前写入 family distribution"
        family_dist_text = read_text_file(family_dist_path)
        if len(family_dist_text.strip()) < 100:
            return False, (
                f"ideation/_family_distribution.md 过短({len(family_dist_text.strip())} 字符)，"
                "至少需要 100 字符的 family 分布描述"
            )

        known_idea_ids: set[str] = set()
        selected_idea_ids: set[str] = set()
        rejected_or_deferred_ids: set[str] = set()
        selected_scorecard_refs: set[str] = set()
        for i, item in enumerate(scorecard_ideas, start=1):
            if not isinstance(item, dict):
                return False, f"idea_scorecard.yaml 第{i}条idea必须是对象"
            idea = item.get("idea") or {}
            idea_id = str(idea.get("id") or "").strip()
            if not idea_id:
                return False, f"idea_scorecard.yaml 第{i}条idea缺少idea.id"
            known_idea_ids.add(idea_id)
            decision = item.get("decision") or {}
            status = str(decision.get("status") or "").strip().lower()
            if status == "selected":
                selected_idea_ids.add(idea_id)
                selected_reasons = decision.get("selected_reason") or []
                if not selected_reasons:
                    return False, f"idea_scorecard.yaml 选中idea {idea_id} 缺少selected_reason"
                for ref in item.get("hypothesis_refs") or []:
                    selected_scorecard_refs.add(str(ref).lstrip("#").strip().upper())
            elif status in {"rejected", "deferred", "merged"}:
                rejected_or_deferred_ids.add(idea_id)
                rejection_reasons = decision.get("rejection_reason") or []
                if not rejection_reasons:
                    return False, f"idea_scorecard.yaml {status} idea {idea_id} 缺少rejection_reason"
            else:
                return False, f"idea_scorecard.yaml idea {idea_id} 的decision.status无效: {status}"

        if not selected_idea_ids:
            return False, "idea_scorecard.yaml 必须至少有一个 decision.status=selected 的idea"
        if not rejected_or_deferred_ids:
            return False, "idea_scorecard.yaml 必须记录至少一个被淘汰/暂缓/合并的idea及原因"
        try:
            pass1_data_for_scorecard = json.loads(
                (ws / "ideation" / "_pass1_forward_candidates.json").read_text(encoding="utf-8")
            )
        except Exception:
            pass1_data_for_scorecard = {}
        pass1_ids_for_scorecard = {
            str(candidate.get("id") or candidate.get("idea_id") or "").strip()
            for candidate in pass1_data_for_scorecard.get("candidates", [])
            if isinstance(candidate, dict)
        }
        missing_scorecard_candidates = sorted(pass1_ids_for_scorecard - known_idea_ids)
        if missing_scorecard_candidates:
            return False, (
                "idea_scorecard.yaml 必须记录 Pass1 全部候选，不能删除被 Pass2 筛掉的候选: "
                f"{missing_scorecard_candidates}"
            )
        missing_selected_refs = sorted(anchor_set - selected_scorecard_refs)
        if missing_selected_refs:
            return False, (
                "idea_scorecard.yaml 中选中idea的hypothesis_refs必须覆盖所有最终假设anchor，"
                f"缺少: {missing_selected_refs}"
            )

        coverage_result = analyze_ideation_coverage(ws)
        coverage = coverage_result.get("coverage", {}) if isinstance(coverage_result, dict) else {}
        origin_mix = coverage.get("origin_mix", {}) if isinstance(coverage, dict) else {}
        mainline_total = int(origin_mix.get("mainline_total") or 0)
        if mainline_total < 1:
            schema = load_cdr_schema()
            mainline = ", ".join((schema.get("idea_origins") or {}).get("mainline") or [])
            return False, f"idea_scorecard.yaml 至少需要一个 CDR 主线idea（{mainline}）"
        if origin_mix.get("supplement_only_risk") is True:
            return False, "idea_scorecard.yaml 不能全部由四类补充候选构成，必须保留主线LLM推理idea"

        rejected_path = ws / "ideation" / "rejected_ideas.md"
        rejected_text = read_text_file(rejected_path)
        if not rejected_path.exists():
            return False, "缺少 ideation/rejected_ideas.md，无法记录淘汰idea原因"
        if len(rejected_text.strip()) < 100:
            return False, "rejected_ideas.md 过短，必须解释被淘汰/暂缓idea的原因"
        missing_rejected_mentions = [
            idea_id for idea_id in sorted(rejected_or_deferred_ids) if idea_id not in rejected_text
        ]
        if missing_rejected_mentions:
            return False, f"rejected_ideas.md 必须提到这些被淘汰/暂缓idea: {missing_rejected_mentions}"

        gate_path = ws / "ideation" / "gate_decisions.json"
        if not gate_path.exists():
            return False, "缺少 ideation/gate_decisions.json，无法追踪Gate决策链"
        try:
            gate_data = json.loads(gate_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"gate_decisions.json 解析失败: {e}"
        if not isinstance(gate_data, dict):
            return False, "gate_decisions.json 必须是JSON对象"
        ok, err = validate_record(gate_data, "gate_decisions")
        if not ok:
            return False, f"gate_decisions.json 不符合schema: {err}"
        ok, err = _validate_current_survey_insights(ws)
        if not ok:
            return False, err
        ok, err = _validate_gate1_selection_fingerprint(ws, gate_data)
        if not ok:
            return False, err
        decisions = gate_data.get("decisions", [])
        gate_ids = {str(item.get("gate_id") or "") for item in decisions if isinstance(item, dict)}
        required_gates = {"T4-DECIDE-1", "T4-DECIDE-2"}
        missing_gates = sorted(required_gates - gate_ids)
        if missing_gates:
            return False, f"gate_decisions.json 必须记录两轮Gate决策，缺少: {missing_gates}"
        gate_selected_ids: set[str] = set()
        gate_rejected_ids: set[str] = set()
        merged_sources_in_gate: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            gate_selected_ids.update(str(v).strip() for v in item.get("selected_idea_ids") or [] if str(v).strip())
            gate_rejected_ids.update(str(v).strip() for v in item.get("rejected_idea_ids") or [] if str(v).strip())
            gate_rejected_ids.update(str(v).strip() for v in item.get("deferred_idea_ids") or [] if str(v).strip())
            for merge in item.get("merged_idea_ids") or []:
                if isinstance(merge, (list, tuple)):
                    merged_sources_in_gate.update(str(v).strip() for v in merge if str(v).strip())
                elif isinstance(merge, dict):
                    merged_sources_in_gate.update(
                        str(v).strip()
                        for v in merge.get("from") or merge.get("source_idea_ids") or []
                        if str(v).strip()
                    )
        unknown_gate_ids = sorted((gate_selected_ids | gate_rejected_ids) - known_idea_ids)
        if unknown_gate_ids:
            return False, f"gate_decisions.json 引用了scorecard中不存在的idea_id: {unknown_gate_ids}"
        if not selected_idea_ids.issubset(gate_selected_ids):
            return False, "gate_decisions.json 必须记录scorecard中所有selected idea"
        if not rejected_or_deferred_ids.intersection(gate_rejected_ids):
            return False, "gate_decisions.json 必须记录至少一个被淘汰/暂缓idea"

        merged_scorecard_ids = {
            str((item.get("idea") or {}).get("id") or "").strip()
            for item in scorecard_ideas
            if isinstance(item, dict)
            and str((item.get("decision") or {}).get("status") or "").strip().lower() == "merged"
        }
        if merged_scorecard_ids and not merged_scorecard_ids.issubset(gate_rejected_ids | merged_sources_in_gate):
            return False, (
                "gate_decisions.json 必须记录被合并的原始idea，缺少: "
                f"{sorted(merged_scorecard_ids - (gate_rejected_ids | merged_sources_in_gate))}"
            )

        project = load_project(ctx)
        max_budget = project.get("constraints", {}).get("max_budget_usd", 100.0)
        total_estimated_cost = 0.0
        for exp in experiments:
            estimate = exp.get("compute_estimate", {}) or {}
            gpu_hours = float(estimate.get("gpu_hours", 0) or 0)
            estimated_cost = estimate.get("estimated_cost_usd")
            exp_cost = float(estimated_cost) if estimated_cost is not None else gpu_hours * 3.0
            total_estimated_cost += exp_cost
            if exp_cost > max_budget * 0.85:
                return False, f"实验'{exp.get('name', '?')}'成本超预算85%"

        declared_total = plan_data.get("total_estimated_cost_usd")
        if declared_total is not None and float(declared_total) > max_budget:
            return False, (
                f"exp_plan.yaml 声明总成本 ${float(declared_total):.2f} "
                f"超过项目预算 ${float(max_budget):.2f}"
            )

        if total_estimated_cost > max_budget:
            return False, (
                f"实验总成本 ${total_estimated_cost:.2f} "
                f"超过项目预算 ${float(max_budget):.2f}"
            )

        budget_check = plan_data.get("budget_check") or {}
        if isinstance(budget_check, dict) and budget_check.get("over_budget") is True:
            return False, "exp_plan.yaml budget_check.over_budget=true，不能判定为完成"

        ok, err = _validate_hypotheses_user_readable_sections(hyp_text)
        if not ok:
            return False, err
        ok, err = _validate_selected_idea_brief(ws)
        if not ok:
            return False, err

        return True, None


def _validate_candidate_directions(ws: Path) -> tuple[bool, str | None]:
    candidate_path = ws / "ideation" / "_candidate_directions.json"
    if not candidate_path.exists():
        return False, "缺少 ideation/_candidate_directions.json，必须记录主线与补充候选方向池"
    try:
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_candidate_directions.json 解析失败: {exc}"
    candidates = candidate_data.get("candidates") if isinstance(candidate_data, dict) else None
    if not isinstance(candidates, list) or len(candidates) < 4:
        return False, "_candidate_directions.json 必须包含至少4个候选方向"

    cdr_schema = load_cdr_schema()
    origins = cdr_schema.get("idea_origins") or {}
    mainline_origins = set(origins.get("mainline") or [
        "free_reasoning",
        "seed_refinement",
        "seed_derived",
        "evidence_driven",
    ])
    supplement_origins = set(origins.get("supplement") or [
        "mechanism_challenge",
        "reverse_operation",
        "subgroup_failure",
        "missing_area_exploration",
        "gap_exploration",
    ])
    bridge_origins = set(origins.get("bridge") or ["bridge_synthesis"])
    bridge_plan = _load_bridge_plan(ws)
    confirmed_bridge_ids = set(_confirmed_bridge_ids(bridge_plan))
    must_bridge_ids = set(_must_explore_bridge_ids(bridge_plan))
    mainline_count = 0
    supplement_count = 0
    bridge_candidate_count = 0
    bridge_covered_ids: set[str] = set()
    cross_domain_candidate_ids: set[str] = set()
    ids: set[str] = set()
    for idx, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            return False, f"_candidate_directions.json 第{idx}条候选必须是对象"
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not idea_id:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 id/idea_id"
        if idea_id in ids:
            return False, f"_candidate_directions.json 候选ID重复: {idea_id}"
        ids.add(idea_id)
        origin = str(candidate.get("idea_origin") or candidate.get("origin") or "").strip()
        status = str(candidate.get("constraint_status") or "").strip()
        basis = str(candidate.get("basis_summary") or candidate.get("basis") or "").strip()
        if not origin:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 idea_origin"
        if not status:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 constraint_status"
        if len(basis) < 20:
            return False, f"_candidate_directions.json 第{idx}条候选 basis_summary 过短"
        ok, err = _validate_cross_domain_provenance(candidate, idea_id, "_candidate_directions.json")
        if not ok:
            return False, err
        if _is_cross_domain_candidate(candidate):
            cross_domain_candidate_ids.add(idea_id)
        if origin in mainline_origins or status == "mainline":
            mainline_count += 1
        if origin in supplement_origins or status == "supplement":
            supplement_count += 1
        if origin in bridge_origins or status == "bridge":
            bridge_candidate_count += 1
            bridge_covered_ids.update(_cross_domain_sources(candidate))
            if not _cross_domain_sources(candidate):
                return False, (
                    f"_candidate_directions.json bridge_synthesis 候选 {idea_id} "
                    "必须填写 cross_domain_sources，不能只写笼统跨域灵感"
                )
        pass2 = candidate.get("pass2_screening") or {}
        if pass2:
            visible = pass2.get("visible_to_gate")
            gate_visibility = str(candidate.get("gate_visibility") or "").strip().lower()
            if visible is False or gate_visibility == "hidden":
                return False, (
                    f"_candidate_directions.json 候选 {idea_id} 被 Pass2 隐藏；"
                    "Pass2 只能标风险，不能从 Gate1 删除候选"
                )
        if status == "not_supported_by_current_evidence":
            pass2 = candidate.get("pass2_screening") or {}
            if pass2 and str(pass2.get("screening_recommendation") or "").strip() == "proceed":
                return False, (
                    f"_candidate_directions.json 第{idx}条 unsupported 候选不能在 Pass2 标为 proceed；"
                    "只能可见上桌、暂缓、淘汰或作为资源升级计划"
                )

    if confirmed_bridge_ids and bridge_candidate_count == 0:
        return False, (
            "_candidate_directions.json 零 bridge_synthesis 候选；"
            "T1 已确认 bridge_domain_plan 时，T4 必须至少把一个桥接综合候选放到 Gate1 桌面。"
        )
    missing_must = sorted(must_bridge_ids - bridge_covered_ids)
    if missing_must:
        coverage_path = ws / "ideation" / "bridge_coverage_review.json"
        if not coverage_path.exists():
            return False, (
                "_candidate_directions.json 未覆盖全部 must_explore bridge，且缺少 "
                "ideation/bridge_coverage_review.json 记录 WARN/逃生舱: "
                f"{missing_must}"
            )
        try:
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"bridge_coverage_review.json 解析失败: {exc}"
        warnings = " ".join(str(item) for item in coverage.get("warnings") or [])
        if not all(bridge_id in warnings for bridge_id in missing_must):
            return False, (
                "bridge_coverage_review.json 必须显式记录未覆盖的 must_explore bridge WARN: "
                f"{missing_must}"
            )

    if mainline_count < 2:
        return False, (
            "_candidate_directions.json 至少需要2个 CDR 主线候选，不能只靠四类补充；"
            f"合法主线 origins: {sorted(mainline_origins)}"
        )
    if not cross_domain_candidate_ids:
        return False, (
            "_candidate_directions.json 必须包含至少1个 Gate1 可见的领域交叉候选；"
            "请在四类补充候选之外生成 idea_origin=cross_domain_analogy 的主线候选，"
            "或在已确认 bridge 有足够素材时生成 idea_origin=bridge_synthesis 的候选。"
        )
    if supplement_count > mainline_count + 4:
        return False, "_candidate_directions.json 四类补充候选过多，主线推理被覆盖"
    return True, None


def validate_t4_gate1_ready(ws: Path) -> tuple[bool, str | None]:
    """Validate the T4 pre-human-decision artifact set.

    This is intentionally narrower than ``IdeationAgent.validate_outputs``:
    it checks that the candidate pool is ready for a human Gate1 decision,
    without requiring final hypotheses, exp_plan, scorecard decisions, or risks.
    """

    ok, err = _validate_pass_stage_artifacts(ws)
    if not ok:
        return False, err
    ok, err = _validate_candidate_directions(ws)
    if not ok:
        return False, err
    ok, err = _validate_bridge_coverage_review(ws)
    if not ok:
        return False, err
    return True, None


def ensure_t4_gate1_candidate_cards(ws: Path) -> bool:
    """Backfill the human-readable Gate1 card deck for upgraded workspaces.

    Returns True when a file was written. This is intentionally conservative:
    it never overwrites an existing card deck, and it only uses the structured
    candidate pool that T4 already produced.
    """

    cards_path = ws / "ideation" / "_gate1_candidate_cards.md"
    if cards_path.exists() and cards_path.stat().st_size > 0:
        return False
    candidate_path = ws / "ideation" / "_candidate_directions.json"
    if not candidate_path.exists():
        return False
    try:
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    candidates = candidate_data.get("candidates") if isinstance(candidate_data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return False
    pass2_by_id: dict[str, dict] = {}
    pass2_path = ws / "ideation" / "_pass2_grounding_review.json"
    if pass2_path.exists():
        try:
            pass2_data = json.loads(pass2_path.read_text(encoding="utf-8"))
            for item in pass2_data.get("reviews") or []:
                if isinstance(item, dict):
                    idea_id = str(item.get("idea_id") or item.get("id") or "").strip()
                    if idea_id:
                        pass2_by_id[idea_id] = item
        except Exception:
            pass
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    cards_path.write_text(_render_gate1_candidate_cards(candidates, pass2_by_id), encoding="utf-8")
    return True


def _render_gate1_candidate_cards(candidates: list[dict], pass2_by_id: dict[str, dict]) -> str:
    score_order = [
        "novelty",
        "feasibility",
        "impact",
        "evaluability",
        "differentiation",
        "cost",
        "contribution_strength",
    ]
    lines = [
        "# T4 Gate1 Candidate Cards",
        "",
        "## How to Use",
        "- **排序 / 推荐动作**: 优先比较 recommendation、技术机制、现实含义、评分依据、核心论文依赖、风险和 kill criteria。",
        "- JSON 只作为机器可读附录；用户主要阅读本文件和 `_gate1_selection_brief.md`。",
        "- 可选动作：选择 Dn、选择 Dn 并重构、合并 D1+D3、新想法、重新分析。",
        "",
    ]
    for rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or f"D{rank}").strip()
        title = str(candidate.get("title") or "Untitled candidate").strip()
        pass2 = candidate.get("pass2_screening") if isinstance(candidate.get("pass2_screening"), dict) else {}
        review = pass2_by_id.get(idea_id, {})
        recommendation = str(
            pass2.get("screening_recommendation")
            or review.get("screening_recommendation")
            or "review_needed"
        ).strip()
        warning = str(
            pass2.get("selection_warning")
            or review.get("selection_warning")
            or "none"
        ).strip()
        scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
        score_text = ", ".join(
            f"{key}={scores.get(key)}" for key in score_order if scores.get(key) is not None
        ) or "not scored"
        basis_sources = candidate.get("basis_sources") if isinstance(candidate.get("basis_sources"), list) else []
        papers = candidate.get("supporting_papers") if isinstance(candidate.get("supporting_papers"), list) else []
        if not papers and basis_sources:
            papers = [
                {
                    "title": str(item.get("ref") or item.get("type") or "source"),
                    "claim_used": str(item.get("claim") or ""),
                }
                for item in basis_sources
                if isinstance(item, dict)
            ]
        paper_text = _format_card_papers(papers)
        if paper_text == "none":
            paper_text = "none; forward-generated from synthesis/problem reframing/cross-domain analogy"
        minimum = candidate.get("minimum_experiment") if isinstance(candidate.get("minimum_experiment"), dict) else {}
        metrics = minimum.get("metric")
        if isinstance(metrics, list):
            metrics_text = ", ".join(str(item) for item in metrics)
        else:
            metrics_text = str(metrics or "metric TBD")
        risks = candidate.get("key_risks") or candidate.get("risks") or []
        risk_text = _format_card_risks(risks)
        practical = _candidate_practical_implication(candidate)
        nearest = candidate.get("nearest_prior_work") if isinstance(candidate.get("nearest_prior_work"), dict) else {}
        lines.extend(
            [
                f"## Rank {rank} / {idea_id}: {title}",
                f"- **Recommended action**: {recommendation}; warning: {warning}",
                f"- **One-line hypothesis**: {candidate.get('pitch') or candidate.get('core_claim') or 'TBD'}",
                f"- **Technical mechanism**: {candidate.get('mechanism') or 'TBD'} Prediction: {candidate.get('prediction') or 'TBD'} Counterfactual: {candidate.get('counterfactual') or 'TBD'}",
                f"- **Practical / managerial / business implication**: {practical}",
                f"- **Scores + score rationale**: {score_text}; rationale: {candidate.get('basis_summary') or candidate.get('contribution_character') or 'see candidate pool'}",
                f"- **Core paper dependencies**: {paper_text}",
                f"- **Nearest prior work / novelty delta**: {nearest.get('work', 'not_computed')} (distance={nearest.get('distance', 'not_computed')}); novelty_signal={candidate.get('novelty_signal') or 'not_computed'}",
                f"- **Minimum convincing evidence**: dataset={minimum.get('dataset', 'TBD')}; baseline={minimum.get('baseline', 'TBD')}; metric={metrics_text}; expected_signal={minimum.get('expected_signal', 'TBD')}",
                f"- **Top risks / kill criteria**: {risk_text}",
                "- **User-edit hint**: edit mechanism, practical implication, core paper dependencies, or merge plan before selecting if the card feels under-specified.",
                "",
            ]
        )
    lines.extend(
        [
            "## Machine-Readable Artifact Paths",
            "- `ideation/_candidate_directions.json`: complete structured candidate pool",
            "- `ideation/_pass2_grounding_review.json`: grounding review and risk flags",
            "- `ideation/_pass1_forward_candidates.json`: raw forward candidates",
            "- `ideation/bridge_coverage_review.json`: bridge visibility and escape-hatch review if present",
            "",
        ]
    )
    return "\n".join(lines)


def _format_card_papers(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "none"
    parts: list[str] = []
    for item in items[:5]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("paper_id") or item.get("ref") or "paper").strip()
            claim = str(item.get("claim_used") or item.get("claim") or "").strip()
            parts.append(f"{title}" + (f" ({claim})" if claim else ""))
        else:
            parts.append(str(item))
    return "; ".join(parts) if parts else "none"


def _candidate_practical_implication(candidate: dict) -> str:
    explicit = (
        str(candidate.get("practical_implication") or "").strip()
        or str(candidate.get("managerial_implication") or "").strip()
        or str(candidate.get("business_implication") or "").strip()
    )
    if explicit:
        return explicit
    title = str(candidate.get("title") or "this idea").strip()
    origin = str(candidate.get("idea_origin") or candidate.get("origin") or "").strip()
    target = str(candidate.get("target_problem") or "the target decision setting").strip()
    lower = f"{title} {origin} {target}".casefold()
    if "coverage" in lower or "resource" in lower or "missing" in lower:
        return (
            "Practically, this is a research-governance checkpoint: it prevents the team "
            "from turning retrieval or dataset uncertainty into an overstated field-gap claim."
        )
    if "subgroup" in lower or "failure" in lower:
        return (
            "Practically, it helps managers identify which customer, treatment, or deployment "
            "subgroups are unsafe to transfer across before making targeting decisions."
        )
    if "reverse" in lower or "remove" in lower or "ablation" in lower:
        return (
            "Practically, it tests whether a costly component is actually needed, helping teams "
            "avoid maintaining bridges or modules that do not change decisions."
        )
    if "bridge" in lower or "cross_domain" in lower or "analogy" in lower:
        return (
            "Practically, it clarifies which ideas can travel across domains and which require "
            "local evidence, reducing blind transfer risk in new business contexts."
        )
    if "treatment" in lower or "uplift" in lower or "causal" in lower:
        return (
            "Practically, it shifts uplift deployment from generic feature adaptation toward "
            "treatment-aware targeting decisions with clearer budget and intervention logic."
        )
    contribution = str(candidate.get("contribution_character") or "").strip()
    if contribution:
        return contribution
    return (
        "Practically, this idea should change how users prioritize design, evaluation, or "
        "deployment decisions in the target setting if the mechanism is validated."
    )


def _format_card_risks(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "risk not specified; require Gate1 clarification"
    parts: list[str] = []
    for item in items[:3]:
        if isinstance(item, dict):
            risk = str(item.get("risk") or "risk").strip()
            kill = str(item.get("kill_criteria") or "kill criteria TBD").strip()
            parts.append(f"{risk}; kill criteria: {kill}")
        else:
            parts.append(str(item))
    return " | ".join(parts)


def _validate_current_survey_insights(ws: Path) -> tuple[bool, str | None]:
    path = ws / "ideation" / "survey_insights.json"
    if not path.exists() or path.stat().st_size <= 0:
        return True, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"survey_insights.json 解析失败: {exc}"
    if not isinstance(data, dict):
        return False, "survey_insights.json 顶层必须是对象"
    if data.get("semantics") != "survey_insights_optional_ideation_fuel_not_gate":
        return False, "survey_insights.json semantics 不正确"
    if ((data.get("audit_summary") or {}).get("passed")) is not True:
        return False, "survey_insights.json 只能来自已通过 audit 的 survey"
    return _validate_survey_insights_fingerprints(ws, data)


def _validate_gate1_selection_fingerprint(ws: Path, gate_data: dict) -> tuple[bool, str | None]:
    """Bind final T4 outputs to the current formal Gate1 decision.

    Old workspaces may not have a selection fingerprint, so this check is
    backward-compatible. New runtime-written Gate1 decisions include
    `selection_fingerprint`; final T4 artifacts must echo it in
    `gate_decisions.json` to prove they consumed the current human choice rather
    than reusing touched stale outputs.
    """

    selection_path = ws / "ideation" / "_gate1_user_selection.json"
    if not selection_path.exists() or selection_path.stat().st_size <= 0:
        return True, None
    try:
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_gate1_user_selection.json 解析失败: {exc}"
    if not isinstance(selection, dict):
        return False, "_gate1_user_selection.json 顶层必须是对象"
    expected = str(selection.get("selection_fingerprint") or "").strip()
    if not expected:
        return True, None
    ok, err = _validate_gate1_candidate_pool_fingerprints(ws, selection)
    if not ok:
        return False, err

    candidates = {
        str(gate_data.get("gate1_selection_fingerprint") or "").strip(),
        str(gate_data.get("selection_fingerprint") or "").strip(),
    }
    for item in gate_data.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        candidates.add(str(item.get("gate1_selection_fingerprint") or "").strip())
        candidates.add(str(item.get("selection_fingerprint") or "").strip())
        if str(item.get("gate_id") or "").strip() == "T4-DECIDE-1":
            candidates.add(str(item.get("source_selection_fingerprint") or "").strip())
    candidates.discard("")
    if expected not in candidates:
        return False, (
            "gate_decisions.json 未绑定当前 Gate1 选择；必须回写 "
            f"_gate1_user_selection.json 的 selection_fingerprint={expected[:12]}..."
        )
    return True, None


def _validate_gate1_candidate_pool_fingerprints(ws: Path, selection: dict) -> tuple[bool, str | None]:
    fingerprints = selection.get("candidate_pool_fingerprints")
    if not isinstance(fingerprints, dict):
        return True, None
    stale: list[str] = []
    for label, item in fingerprints.items():
        if not isinstance(item, dict):
            stale.append(str(label))
            continue
        rel = str(item.get("path") or "").strip()
        if not rel:
            stale.append(str(label))
            continue
        path = ws / rel
        expected_exists = bool(item.get("exists"))
        if expected_exists != path.exists():
            stale.append(str(label))
            continue
        if not expected_exists:
            continue
        expected_sha = str(item.get("sha256") or "").strip()
        if not expected_sha:
            stale.append(str(label))
            continue
        if not path.is_file() or _sha256_file(path) != expected_sha:
            stale.append(str(label))
    if stale:
        return False, "Gate1 用户选择绑定的候选池已变化，必须重新进入 T4-GATE1: " + ", ".join(stale)
    return True, None


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pass_stage_artifacts(ws: Path) -> tuple[bool, str | None]:
    """Validate that T4 exposes both generation and grounding stages to Gate1."""

    ideation_dir = ws / "ideation"
    pass1_path = ideation_dir / "_pass1_forward_candidates.json"
    pass2_path = ideation_dir / "_pass2_grounding_review.json"
    candidate_path = ideation_dir / "_candidate_directions.json"
    candidate_cards_path = ideation_dir / "_gate1_candidate_cards.md"
    gate_brief_path = ideation_dir / "_gate1_selection_brief.md"

    for path, label in [
        (pass1_path, "_pass1_forward_candidates.json"),
        (pass2_path, "_pass2_grounding_review.json"),
        (candidate_path, "_candidate_directions.json"),
        (candidate_cards_path, "_gate1_candidate_cards.md"),
        (gate_brief_path, "_gate1_selection_brief.md"),
    ]:
        if not path.exists():
            return False, (
                f"缺少 ideation/{label}。T4 Gate1 前半段必须先按顺序写入 "
                "_pass1_forward_candidates.json、_pass2_grounding_review.json、"
                "_candidate_directions.json、_family_distribution.md、"
                "_gate1_candidate_cards.md、_gate1_selection_brief.md，"
                "然后 finish_task 交给 T4-GATE1；不要只读取材料后等待最终阶段。"
            )

    try:
        pass1_data = json.loads(pass1_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_pass1_forward_candidates.json 解析失败: {exc}"
    try:
        pass2_data = json.loads(pass2_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_pass2_grounding_review.json 解析失败: {exc}"
    try:
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_candidate_directions.json 解析失败: {exc}"

    pass1_candidates = pass1_data.get("candidates") if isinstance(pass1_data, dict) else None
    if not isinstance(pass1_candidates, list) or len(pass1_candidates) < 4:
        return False, "_pass1_forward_candidates.json 必须包含至少4个 Pass1 原始候选"

    pass1_ids: set[str] = set()
    for idx, candidate in enumerate(pass1_candidates, start=1):
        if not isinstance(candidate, dict):
            return False, f"_pass1_forward_candidates.json 第{idx}条候选必须是对象"
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not idea_id:
            return False, f"_pass1_forward_candidates.json 第{idx}条候选缺少 id"
        if idea_id in pass1_ids:
            return False, f"_pass1_forward_candidates.json 候选ID重复: {idea_id}"
        pass1_ids.add(idea_id)
        if not str(candidate.get("idea_origin") or candidate.get("origin") or "").strip():
            return False, f"_pass1_forward_candidates.json 候选 {idea_id} 缺少 idea_origin"
        ok, err = _validate_cross_domain_provenance(candidate, idea_id, "_pass1_forward_candidates.json")
        if not ok:
            return False, err

    reviews = pass2_data.get("reviews") if isinstance(pass2_data, dict) else None
    if not isinstance(reviews, list):
        return False, "_pass2_grounding_review.json 必须包含 reviews 数组"
    pass2_ids: set[str] = set()
    for idx, review in enumerate(reviews, start=1):
        if not isinstance(review, dict):
            return False, f"_pass2_grounding_review.json 第{idx}条review必须是对象"
        idea_id = str(review.get("idea_id") or review.get("id") or "").strip()
        if not idea_id:
            return False, f"_pass2_grounding_review.json 第{idx}条review缺少 idea_id"
        pass2_ids.add(idea_id)
        ok, err = _validate_pass2_soft_diagnostics(review, idea_id)
        if not ok:
            return False, err
        if review.get("visible_to_gate") is False:
            return False, (
                f"_pass2_grounding_review.json review {idea_id} visible_to_gate=false；"
                "Pass2 不能隐藏候选"
            )
        recommendation = str(review.get("screening_recommendation") or "").strip()
        if recommendation not in {
            "proceed",
            "revise_before_selection",
            "defer_recommended",
            "reject_recommended",
        }:
            return False, (
                f"_pass2_grounding_review.json review {idea_id} screening_recommendation 无效: "
                f"{recommendation or '空'}"
            )

    missing_reviews = sorted(pass1_ids - pass2_ids)
    if missing_reviews:
        return False, f"_pass2_grounding_review.json 未覆盖这些 Pass1 候选: {missing_reviews}"

    candidates = candidate_data.get("candidates") if isinstance(candidate_data, dict) else None
    candidate_ids = {
        str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        for candidate in candidates or []
        if isinstance(candidate, dict)
    }
    missing_gate_candidates = sorted(pass1_ids - candidate_ids)
    if missing_gate_candidates:
        return False, (
            "_candidate_directions.json 必须保留 Pass1 全部候选，不能因 Pass2 筛选删除: "
            f"{missing_gate_candidates}"
        )

    ok, err = _validate_gate1_candidate_cards(candidate_cards_path, pass1_ids)
    if not ok:
        return False, err

    brief_text = read_text_file(gate_brief_path)
    if len(brief_text.strip()) < 300:
        return False, "_gate1_selection_brief.md 过短，必须展示全量候选、Pass2风险和合并建议"
    missing_from_brief = [idea_id for idea_id in sorted(pass1_ids) if idea_id not in brief_text]
    if missing_from_brief:
        return False, f"_gate1_selection_brief.md 必须提到所有候选ID，缺少: {missing_from_brief}"
    if not re.search(r"合并|merge|D\d+\+D\d+", brief_text, re.IGNORECASE):
        return False, "_gate1_selection_brief.md 必须说明可合并多个候选，例如 合并 D1+D3"
    required_soft_sections = [
        ("集中度提示", r"集中度|concentration"),
        ("Origin 分布", r"Origin\s*分布|origin\s+distribution|origin mix"),
        ("Novelty-Utility 谱系排布", r"Novelty[-– ]Utility|新颖度.*可行|新颖.*效用"),
    ]
    missing_soft = [
        label for label, pattern in required_soft_sections
        if not re.search(pattern, brief_text, re.IGNORECASE)
    ]
    if missing_soft:
        return False, "_gate1_selection_brief.md 缺少软提示章节: " + ", ".join(missing_soft)

    return True, None


def _validate_gate1_candidate_cards(path: Path, pass1_ids: set[str]) -> tuple[bool, str | None]:
    """Validate the reader-facing Gate1 card deck.

    `_candidate_directions.json` remains the machine-readable source of truth,
    but the runtime gate should show a compact Markdown card deck first. This
    check keeps the human-facing artifact from degrading into a short pointer or
    a raw JSON dump.
    """

    text = read_text_file(path)
    stripped = text.strip()
    if len(stripped) < 800:
        return False, (
            "ideation/_gate1_candidate_cards.md 过短，必须用 Markdown 卡片展示每个候选的"
            "技术机制、现实含义、评分依据、核心论文依赖和推荐动作"
        )
    if re.search(r"^\s*[\{\[]", stripped):
        return False, "ideation/_gate1_candidate_cards.md 不能是 raw JSON，必须是给用户阅读的 Markdown"
    missing = [idea_id for idea_id in sorted(pass1_ids) if idea_id not in text]
    if missing:
        return False, f"_gate1_candidate_cards.md 必须提到所有候选ID，缺少: {missing}"
    required_sections = [
        ("排序/推荐动作", r"排序|rank|推荐动作|recommended action|建议动作"),
        ("技术机制", r"技术机制|technical mechanism|mechanism"),
        ("现实含义", r"现实含义|管理含义|商业含义|业务含义|practical|managerial|business"),
        ("评分依据", r"评分依据|score rationale|评分|scores"),
        ("核心论文依赖", r"核心论文依赖|paper dependencies|core paper|supporting papers|核心文献"),
        ("风险/kill criteria", r"kill criteria|停止条件|关键风险|risk"),
        ("文件路径提示", r"_candidate_directions\.json|_pass2_grounding_review\.json|机器可读|完整 JSON"),
    ]
    missing_sections = [
        label for label, pattern in required_sections
        if not re.search(pattern, text, flags=re.IGNORECASE)
    ]
    if missing_sections:
        return False, "_gate1_candidate_cards.md 缺少用户选择所需字段: " + ", ".join(missing_sections)
    return True, None


def _validate_hypotheses_user_readable_sections(text: str) -> tuple[bool, str | None]:
    anchors = list(re.finditer(r"^#+\s*(H\d+)\b.*$", text, flags=re.MULTILINE | re.IGNORECASE))
    if not anchors:
        return True, None
    section_requirements = [
        ("技术机制", r"技术机制|机制假设|technical formulation|mechanism hypothesis|mechanism"),
        ("现实/管理/商业含义", r"现实含义|管理含义|商业含义|业务含义|practical implication|managerial implication|business implication"),
        ("评分依据", r"评分依据|score rationale|选择依据|selection rationale|why selected"),
        ("核心论文依赖", r"核心论文依赖|核心文献依赖|paper dependencies|core paper dependencies|supporting papers"),
        ("证伪/停止条件", r"证伪|kill criteria|停止条件|counterfactual|falsification"),
    ]
    for index, match in enumerate(anchors):
        anchor = match.group(1).upper()
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        block = text[match.start():end]
        missing = [
            label for label, pattern in section_requirements
            if not re.search(pattern, block, flags=re.IGNORECASE)
        ]
        if missing:
            return False, f"hypotheses.md {anchor} 缺少用户可读质量小节: " + ", ".join(missing)
    return True, None


def _validate_selected_idea_brief(ws: Path) -> tuple[bool, str | None]:
    path = ws / "ideation" / "selected_idea_brief.md"
    if not path.exists():
        return False, (
            "缺少 ideation/selected_idea_brief.md；Gate1 后必须写用户可读的最终 idea/假设确认摘要"
        )
    text = read_text_file(path)
    if len(text.strip()) < 400:
        return False, "selected_idea_brief.md 过短，必须说明用户选择、最终 idea、现实含义和下一步假设范围"
    if re.search(r"待\s*T4|待.*后半段|待.*补全|待.*写入|TBD|TODO", text, flags=re.IGNORECASE):
        return False, "selected_idea_brief.md 仍是 Gate1 stub 或占位内容，T4 后半段必须补全最终选择摘要"
    required = [
        ("用户选择", r"用户选择|Gate1|selected_option|human selection"),
        ("最终 idea", r"最终 idea|selected idea|最终方向|final idea"),
        ("技术机制", r"技术机制|mechanism|机制假设"),
        ("现实含义", r"现实含义|管理含义|商业含义|业务含义|practical|managerial|business"),
        ("核心论文依赖", r"核心论文依赖|核心文献依赖|paper dependencies|supporting papers"),
        ("后续假设", r"H1|hypotheses\.md|后续假设|final hypotheses"),
    ]
    missing = [label for label, pattern in required if not re.search(pattern, text, flags=re.IGNORECASE)]
    if missing:
        return False, "selected_idea_brief.md 缺少字段: " + ", ".join(missing)
    return True, None


def _validate_cross_domain_provenance(record: dict, idea_id: str, label: str) -> tuple[bool, str | None]:
    """Check optional bridge provenance without forcing every idea to be cross-domain."""

    if not isinstance(record, dict):
        return True, None
    sources = _cross_domain_sources(record)
    raw_source = sources[0] if sources else record.get("cross_domain_source")
    raw_relation = record.get("cross_domain_relation")
    source = str(raw_source or "").strip()
    relation = str(raw_relation or "").strip()
    source_is_empty = source.casefold() in {"", "none", "null", "n/a"}
    relation_is_empty = relation.casefold() in {"", "none", "null", "n/a"}
    origin = str(record.get("idea_origin") or record.get("origin") or "").strip()

    if origin == "bridge_synthesis" and not sources:
        return False, (
            f"{label} idea {idea_id} 是 bridge_synthesis，必须填写非空 cross_domain_sources 数组"
        )
    if source_is_empty and relation_is_empty:
        return True, None
    if source_is_empty and not relation_is_empty:
        return False, (
            f"{label} idea {idea_id} 填写了 cross_domain_relation={relation}，"
            "但缺少 cross_domain_sources/bridge_id，无法追踪跨域素材来源"
        )
    if relation_is_empty:
        return False, (
            f"{label} idea {idea_id} 填写了 cross_domain_sources={sources or [source]}，"
            "但缺少 cross_domain_relation"
        )
    if relation not in CROSS_DOMAIN_RELATIONS:
        return False, (
            f"{label} idea {idea_id} 的 cross_domain_relation 非法: {relation}；"
            f"合法值: {sorted(CROSS_DOMAIN_RELATIONS)}"
        )
    return True, None


def _is_cross_domain_candidate(record: dict) -> bool:
    """Return whether a candidate satisfies the mandatory cross-domain slot."""

    if not isinstance(record, dict):
        return False
    origin = str(record.get("idea_origin") or record.get("origin") or "").strip()
    if origin in CROSS_DOMAIN_IDEA_ORIGINS:
        return True
    return bool(_cross_domain_sources(record))


def _validate_bridge_coverage_review(ws: Path) -> tuple[bool, str | None]:
    bridge_plan = _load_bridge_plan(ws)
    confirmed_bridge_ids = set(_confirmed_bridge_ids(bridge_plan))
    review_path = ws / "ideation" / "bridge_coverage_review.json"
    if not confirmed_bridge_ids:
        return True, None
    if not review_path.exists():
        return False, (
            "缺少 ideation/bridge_coverage_review.json；T1 已确认 bridge domain 时，"
            "T4 必须记录 bridge_synthesis 候选是否上桌、是否进入 hypotheses 以及逃生舱理由"
        )
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"bridge_coverage_review.json 解析失败: {exc}"
    normalized = _normalize_bridge_coverage_review_for_schema(review, bridge_plan)
    if normalized is not review:
        review = normalized
        # Resume compatibility: old partial T4 artifacts used a legacy bridge
        # coverage schema. Normalize once so later validators and agents read
        # the same schema-bound file.
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok, err = validate_record(review, "bridge_coverage_review")
    if not ok:
        return False, f"bridge_coverage_review.json 不符合schema: {err}"
    reviews = review.get("bridge_reviews")
    if not isinstance(reviews, list):
        return False, "bridge_coverage_review.json bridge_reviews 必须是数组"
    by_bridge = {
        str(item.get("bridge_id") or "").strip(): item
        for item in reviews
        if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()
    }
    missing_reviews = sorted(confirmed_bridge_ids - set(by_bridge))
    if missing_reviews:
        return False, f"bridge_coverage_review.json 缺少 bridge review: {missing_reviews}"
    must_bridge_ids = set(_must_explore_bridge_ids(bridge_plan))
    for bridge_id in sorted(must_bridge_ids):
        item = by_bridge.get(bridge_id) or {}
        escape = item.get("escape_hatch") if isinstance(item.get("escape_hatch"), dict) else {}
        escape_status = str(escape.get("status") or "").strip()
        if not item.get("visible_to_gate") and not item.get("candidate_ids"):
            if escape_status != "no_candidate_available":
                return False, (
                    f"must_explore bridge {bridge_id} 没有可见 Gate1 候选；"
                    "如果确实缺少可用素材，必须在 escape_hatch.status 写 no_candidate_available，"
                    "并记录 reason / kill criteria / can_revisit_if，交给用户在 Gate1 裁决"
                )
            warnings = " ".join(str(item) for item in review.get("warnings") or [])
            if bridge_id not in warnings:
                return False, (
                    f"must_explore bridge {bridge_id} 未上桌但 warnings 未显式记录。"
                    "must_explore 不足是 WARN/逃生舱语义，不能静默跳过"
                )
        if not str(escape.get("reason") or "").strip():
            return False, f"bridge {bridge_id} 缺少 escape_hatch.reason"
        if not str(escape.get("falsification_or_kill_criteria") or "").strip():
            return False, f"bridge {bridge_id} 缺少证伪/kill criteria"
    return True, None


def _normalize_bridge_coverage_review_for_schema(review: dict, bridge_plan: dict) -> dict:
    """Migrate older T4 bridge review drafts into the current schema.

    Earlier prompts asked for ``bridge_domains`` and semantics
    ``bridge_coverage_review_for_gate1_visibility``. The schema now requires
    ``bridge_reviews`` and an explicit escape-hatch contract. Normalizing here
    lets existing workspaces resume without hand-editing partial T4 artifacts.
    """

    if not isinstance(review, dict):
        return review
    if review.get("semantics") == "bridge_candidate_visibility_and_escape_hatch_review" and isinstance(
        review.get("bridge_reviews"), list
    ):
        return review

    legacy_items = review.get("bridge_reviews")
    if not isinstance(legacy_items, list):
        legacy_items = review.get("bridge_domains")
    if not isinstance(legacy_items, list):
        return review

    priority_by_bridge = {
        str(item.get("bridge_id") or "").strip(): str(item.get("priority") or "should_explore").strip()
        for item in _bridge_domains(bridge_plan)
        if isinstance(item, dict)
    }
    normalized_reviews: list[dict] = []
    for item in legacy_items:
        if not isinstance(item, dict):
            continue
        bridge_id = str(item.get("bridge_id") or "").strip()
        if not bridge_id:
            continue
        candidate_ids = item.get("candidate_ids")
        if not isinstance(candidate_ids, list):
            candidate_ids = item.get("candidates_generated")
        if not isinstance(candidate_ids, list):
            candidate_ids = []
        candidate_ids = [str(candidate).strip() for candidate in candidate_ids if str(candidate).strip()]
        escape = item.get("escape_hatch") if isinstance(item.get("escape_hatch"), dict) else {}
        legacy_status = str(escape.get("status") or "").strip()
        status = _normalize_bridge_escape_status(legacy_status, bool(candidate_ids))
        reason = (
            str(escape.get("reason") or "").strip()
            or str(escape.get("note") or "").strip()
            or str(item.get("summary") or "").strip()
            or str(item.get("decision_summary") or "").strip()
            or "Legacy bridge review normalized during resume."
        )
        normalized_reviews.append(
            {
                "bridge_id": bridge_id,
                "priority": priority_by_bridge.get(bridge_id) or str(item.get("priority") or "should_explore"),
                "candidate_ids": candidate_ids,
                "visible_to_gate": bool(item.get("visible_to_gate", bool(candidate_ids))),
                "forced_surfaced": bool(item.get("forced_surfaced", False)),
                "selected_into_hypotheses": bool(item.get("selected_into_hypotheses", False)),
                "decision_summary": str(item.get("decision_summary") or item.get("summary") or reason),
                "escape_hatch": {
                    "status": status,
                    "reason": reason,
                    "falsification_or_kill_criteria": str(
                        escape.get("falsification_or_kill_criteria")
                        or escape.get("kill_criteria")
                        or "Drop this bridge if Gate1 or T4.5 cannot identify a testable transferable mechanism."
                    ),
                    "can_revisit_if": str(
                        escape.get("can_revisit_if")
                        or "Revisit if later T2/T3 evidence adds stronger bridge-specific notes or the user selects this framing."
                    ),
                },
            }
        )

    normalized = dict(review)
    normalized["version"] = str(normalized.get("version") or "1.0")
    normalized["semantics"] = "bridge_candidate_visibility_and_escape_hatch_review"
    normalized.setdefault("source_bridge_plan", "literature/bridge_domain_plan.json")
    normalized["bridge_reviews"] = normalized_reviews
    normalized.pop("bridge_domains", None)
    return normalized


def _normalize_bridge_escape_status(raw_status: str, has_candidate: bool) -> str:
    status = raw_status.strip().casefold()
    aliases = {
        "well_covered": "deferred",
        "partial_coverage": "deferred",
        "not_enough_evidence": "no_candidate_available",
        "no_candidate": "no_candidate_available",
        "no_candidate_available": "no_candidate_available",
        "not_needed_selected": "not_needed_selected",
        "deferred": "deferred",
        "rejected": "rejected",
        "merged": "merged",
    }
    if status in aliases:
        return aliases[status]
    if status == "low_evidence":
        return "deferred" if has_candidate else "no_candidate_available"
    return "deferred" if has_candidate else "no_candidate_available"


def _load_bridge_plan(ws: Path) -> dict:
    path = ws / "literature" / "bridge_domain_plan.json"
    if not path.exists():
        return {"bridge_domains": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"bridge_domains": []}
    return data if isinstance(data, dict) else {"bridge_domains": []}


def _bridge_domains(plan: dict) -> list[dict]:
    if str(plan.get("source") or "").strip().casefold() == "none":
        return []
    domains = plan.get("bridge_domains") if isinstance(plan, dict) else []
    return [
        item for item in domains or []
        if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()
    ]


def _confirmed_bridge_ids(plan: dict) -> list[str]:
    return [str(item.get("bridge_id") or "").strip() for item in _bridge_domains(plan)]


def _must_explore_bridge_ids(plan: dict) -> list[str]:
    return [
        str(item.get("bridge_id") or "").strip()
        for item in _bridge_domains(plan)
        if str(item.get("priority") or "").strip() == "must_explore"
    ]


def _cross_domain_sources(record: dict) -> list[str]:
    if not isinstance(record, dict):
        return []
    raw = record.get("cross_domain_sources")
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(item) for item in raw]
    else:
        values = []
    legacy = str(record.get("cross_domain_source") or "").strip()
    if legacy and legacy.casefold() not in {"none", "null", "n/a"}:
        values.append(legacy)
    sources: list[str] = []
    for value in values:
        source = str(value or "").strip()
        if source and source.casefold() not in {"none", "null", "n/a"} and source not in sources:
            sources.append(source)
    return sources


def _validate_pass2_soft_diagnostics(review: dict, idea_id: str) -> tuple[bool, str | None]:
    """Pass2 must expose soft diagnostics before Gate1, without using them as gates."""

    counterfactual_values = {"collapses", "survives_weakened", "independent", "insufficient_evidence"}
    distance_values = {"very_close", "moderate", "distant", "none_found", "not_computed"}
    novelty_values = {"marginal_zone", "adjacent_zone", "no_nearby_cluster", "not_computed", "domain_map_unavailable"}

    counterfactual_check = review.get("counterfactual_check")
    if counterfactual_check not in counterfactual_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} 缺少合法 counterfactual_check；"
            "Pass2 必须在 Gate1 前标注该软信号或说明 insufficient_evidence"
        )
    counterfactual_note = str(review.get("counterfactual_note") or "").strip()
    if len(counterfactual_note) < 8:
        return False, f"_pass2_grounding_review.json review {idea_id} counterfactual_note 过短"
    nearest = review.get("nearest_prior_work")
    if not isinstance(nearest, dict):
        return False, f"_pass2_grounding_review.json review {idea_id} 缺少 nearest_prior_work"
    distance = str(nearest.get("distance") or "").strip()
    if distance not in distance_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} nearest_prior_work.distance 无效: "
            f"{distance or '空'}"
        )
    if "work" not in nearest:
        return False, f"_pass2_grounding_review.json review {idea_id} nearest_prior_work 缺少 work"
    novelty_signal = review.get("novelty_signal")
    if novelty_signal not in novelty_values:
        return False, (
            f"_pass2_grounding_review.json review {idea_id} 缺少合法 novelty_signal；"
            "该字段只是引用图近邻参考信号，不是 gate；图谱不可用时写 domain_map_unavailable/not_computed"
        )
    return True, None


def _validate_soft_novelty_fields(item: dict, idea_id: str) -> tuple[bool, str | None]:
    """Ensure soft diagnostic fields are present without turning them into gates."""

    counterfactual_values = {"collapses", "survives_weakened", "independent", "insufficient_evidence"}
    distance_values = {"very_close", "moderate", "distant", "none_found", "not_computed"}
    novelty_values = {"marginal_zone", "adjacent_zone", "no_nearby_cluster", "not_computed", "domain_map_unavailable"}

    idea = item.get("idea") if isinstance(item.get("idea"), dict) else {}
    counterfactual_check = item.get("counterfactual_check") or idea.get("counterfactual_check")
    if counterfactual_check not in counterfactual_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} 缺少合法 counterfactual_check；"
            "该字段是软提示，可取 collapses/survives_weakened/independent/insufficient_evidence"
        )
    counterfactual_note = str(item.get("counterfactual_note") or idea.get("counterfactual_note") or "").strip()
    if len(counterfactual_note) < 8:
        return False, f"idea_scorecard.yaml idea {idea_id} counterfactual_note 过短"
    nearest = item.get("nearest_prior_work") or idea.get("nearest_prior_work")
    if not isinstance(nearest, dict):
        return False, f"idea_scorecard.yaml idea {idea_id} 缺少 nearest_prior_work"
    distance = str(nearest.get("distance") or "").strip()
    if distance not in distance_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} nearest_prior_work.distance 无效: "
            f"{distance or '空'}"
        )
    if "work" not in nearest:
        return False, f"idea_scorecard.yaml idea {idea_id} nearest_prior_work 缺少 work"
    novelty_signal = item.get("novelty_signal") or idea.get("novelty_signal")
    if novelty_signal not in novelty_values:
        return False, (
            f"idea_scorecard.yaml idea {idea_id} 缺少合法 novelty_signal；"
            "该字段只是引用图近邻参考信号，不是 gate；图谱不可用时写 domain_map_unavailable/not_computed"
        )
    return True, None
