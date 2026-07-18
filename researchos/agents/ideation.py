"""T4 recovery-facing Ideation Agent.

Native T4 is an artifact-first evolutionary workflow coordinated by the
runtime controller. This agent owns the compatibility/recovery prompt only;
formal hypotheses and an experiment plan are compiled by T4.5 after a passing
novelty/collision audit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime, timezone

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.bridge_catalog import load_bridge_catalog_summaries
from ..literature_resources import refresh_resource_catalog
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
from ..ideation.final_card_readiness import validate_t4_portfolio_final_cards


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
T4_EXECUTION_EVENTS = Path("ideation/t4_execution_events.jsonl")
T4_EVIDENCE_POOL = Path("ideation/t4_evidence_pool.json")
T4_GATE1_CARD_SCHEMA_MARKER = "<!-- ResearchOS Gate1 candidate-card schema: v3 -->"

T4_GATE1_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("ideation/_pass1_forward_candidates.json", "Pass1 候选发散"),
    ("ideation/_pass2_grounding_review.json", "Pass2 接地复核"),
    ("ideation/_candidate_directions.json", "结构化候选方向池"),
    ("ideation/_family_distribution.md", "候选谱系与集中度检查"),
    ("ideation/_gate1_candidate_cards.md", "Gate1 完整候选卡片"),
    ("ideation/_gate1_selection_brief.md", "Gate1 选择简报"),
    ("ideation/final_cards/portfolio_cards.json", "Portfolio 完整 LLM Idea Card"),
)
T4_BRIDGE_COVERAGE_PATH = "ideation/bridge_coverage_review.json"

CROSS_DOMAIN_IDEA_ORIGINS = {
    "cross_domain_analogy",
    "bridge_synthesis",
}


def _shallow_reading_prompt_summary(synthesis_workbench_text: str) -> str:
    """Expose productive shallow-reading uses and their claim boundary to T4."""

    try:
        data = json.loads(synthesis_workbench_text) if synthesis_workbench_text.strip() else {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    shallow = data.get("shallow_reading_context")
    if not isinstance(shallow, dict):
        shallow = data.get("weak_evidence_and_resource_upgrade")
    if not isinstance(shallow, dict) or not shallow:
        return ""
    examples = shallow.get("abstract_only_examples") if isinstance(shallow.get("abstract_only_examples"), list) else []
    lines = [
        "semantics: shallow-reading supplementary context with an explicit claim boundary",
        f"shallow_note_count: {shallow.get('abstract_only_count', 0)}",
        f"metadata_triage_available: {shallow.get('metadata_triage_available', False)}",
        "allowed_use: coverage, taxonomy, trends, comparison, bridge discovery, and candidate-idea discovery",
        "rule: a selected direction may use shallow notes as supplementary breadth, but needs deep-reading support for its central mechanism or design rationale.",
    ]
    for item in examples[:6]:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "").strip()
        title = str(item.get("title") or "").strip()
        allowed_use = str(item.get("allowed_use") or "abstract-level supplementary context").strip()
        lines.append(f"- {paper_id}: {title} | {allowed_use}")
    return "\n".join(lines)


def _shorten(text: str, limit: int) -> str:
    """Normalize persisted T4 evidence without silently cutting its ending."""

    del limit
    return " ".join(str(text or "").split())


def _t4_prompt_material(text: str, *, path: str, budget_chars: int) -> str:
    """Embed complete source text or require an explicit, chunked file read.

    A partial excerpt in an ideation prompt looks like a completed source to
    the model and is the direct cause of malformed claims and literal
    ellipses.  When a source does not fit the model-aware allocation, retain a
    precise file instruction instead; the agent already has scoped read tools
    and must inspect the source in chunks before relying on it.
    """

    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= budget_chars:
        return normalized
    return (
        f"`{path}` 共 {len(normalized):,} 个字符，未直接放入初始提示，避免提供不完整内容。"
        f"开始生成候选前，请先用 `read_file` 按段读取该文件；需要定位具体材料时可先用 `grep_search`。"
    )


def _t4_prompt_budgets(context_window: object) -> dict[str, int]:
    """Allocate source text from the discovered model context window.

    The total remains deliberately below half of the available token window so
    tool schemas, the system instruction, and the first tool exchange still
    fit.  Values are character budgets because the sources are plain text; the
    conservative allocation works for both Chinese-heavy and English-heavy
    notes without pretending that one byte equals one token.
    """

    try:
        tokens = int(context_window)
    except (TypeError, ValueError):
        tokens = 100_000
    total = max(24_000, min(360_000, tokens // 2))
    return {
        "context_pack": max(12_000, int(total * 0.48)),
        "synthesis": max(6_000, int(total * 0.22)),
        "workbench": max(4_000, int(total * 0.10)),
        "missing_areas": max(2_000, int(total * 0.05)),
        "seed_ideas": max(2_000, int(total * 0.05)),
        "comparison_table": max(2_000, int(total * 0.03)),
        "domain_map": max(2_000, int(total * 0.03)),
        "bridge_domain_plan": max(2_000, int(total * 0.02)),
        "survey_insights": max(2_000, int(total * 0.02)),
    }


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


def _note_card_t4_lane(card: dict[str, object]) -> str:
    """Return one evidence lane used to keep compact T4 inputs diverse."""

    for key in ("method_family", "domain", "source_bucket", "venue"):
        value = str(card.get(key) or "").strip().casefold()
        if value:
            return f"{key}:{value}"
    if str(card.get("bridge_point") or "").strip():
        return "bridge"
    if str(card.get("gaps") or "").strip() or str(card.get("boundary_conditions") or "").strip():
        return "gap_or_boundary"
    if str(card.get("mechanism_claim") or "").strip():
        return "mechanism"
    return f"source:{str(card.get('source_file') or card.get('note_id') or '').casefold()}"


def _select_t4_compact_note_cards(
    cards: list[dict[str, object]],
    *,
    initial_limit: int = 12,
    max_limit: int = 18,
) -> list[dict[str, object]]:
    """Select a bounded, high-quality and lane-diverse T4 evidence set."""

    if not cards:
        return []
    target = min(max(1, initial_limit), max_limit, len(cards))
    ranked = sorted(cards, key=_note_card_t4_priority, reverse=True)
    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    seen_lanes: set[str] = set()
    for card in ranked:
        lane = _note_card_t4_lane(card)
        card_id = str(card.get("note_id") or card.get("source_file") or card.get("title") or "")
        if lane in seen_lanes or card_id in selected_ids:
            continue
        selected.append(card)
        selected_ids.add(card_id)
        seen_lanes.add(lane)
        if len(selected) >= target:
            return selected
    for card in ranked:
        card_id = str(card.get("note_id") or card.get("source_file") or card.get("title") or "")
        if card_id in selected_ids:
            continue
        selected.append(card)
        selected_ids.add(card_id)
        if len(selected) >= target:
            break
    return selected


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
        for key in ("notes", "shallow_read_notes"):
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
    return "\n".join(rows)


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
    for key in ("notes", "shallow_read_notes"):
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
    abstract_only = "abstract-only" in text.lower() or "shallow_read_notes" in path.as_posix()
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
        workspace_dir / "literature" / "deep_read_notes",
        workspace_dir / "literature" / "bridge_notes",
        workspace_dir / "literature" / "shallow_read_notes",
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
                Path("literature/deep_read_notes"),
                Path("literature/bridge_notes"),
                Path("literature/shallow_read_notes"),
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
        for directory in (
            workspace_dir / "literature" / "deep_read_notes",
            workspace_dir / "literature" / "bridge_notes",
            workspace_dir / "literature" / "shallow_read_notes",
        ):
            candidate = directory / f"{compact['note_id']}.md"
            if candidate.exists():
                compact["source_file"] = _workspace_rel(candidate, workspace_dir)
                break
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


def prepare_t4_context_pack(workspace_dir: Path) -> dict[str, object]:
    """Build compact T4 context artifacts before the LLM starts."""

    workspace_dir = Path(workspace_dir)
    ideation_dir = workspace_dir / "ideation"
    ideation_dir.mkdir(parents=True, exist_ok=True)
    # Backfill older workspaces before compacting T4 context. This only scans
    # existing notes/metadata and writes discovery records; it never fetches
    # or executes third-party resources.
    refresh_resource_catalog(workspace_dir)
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
    selected_cards = _select_t4_compact_note_cards(usable_cards)
    selected_ids = {
        str(card.get("note_id") or card.get("source_file") or "")
        for card in selected_cards
    }
    deferred_cards = [
        {
            "note_id": card.get("note_id"),
            "title": card.get("title"),
            "source_file": card.get("source_file"),
            "evidence_level": card.get("evidence_level"),
            "citation_use": card.get("citation_use"),
            "citation_quality_score": card.get("citation_quality_score"),
            "revisit_reason": "available_outside_compact_context_pack",
        }
        for card in usable_cards
        if str(card.get("note_id") or card.get("source_file") or "") not in selected_ids
    ]

    bridge_plan = _read_json_file(workspace_dir / "literature" / "bridge_domain_plan.json")
    domain_map = _read_json_file(workspace_dir / "literature" / "domain_map.json")
    resource_catalog_summary = _read_json_file(workspace_dir / "literature" / "resource_catalog_summary.json")
    bridge_catalogs = load_bridge_catalog_summaries(
        workspace_dir,
        records_per_bridge=2,
        abstract_excerpt_chars=480,
    )
    pack: dict[str, object] = {
        "version": "1.0",
        "semantics": "t4_compact_ideation_context_pack",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recommended_use": [
            "Use ideation/t4_context_pack.md or this JSON before broad-scanning deep_read_notes.",
            "Open individual note files only when a specific claim or citation needs verification.",
            "Keep abstract-only material as weak idea fuel, not as strong claim evidence.",
            "Use Cross-domain catalogs for structural analogy, history, boundary discovery, and reading priority; do not cite a catalog as direct proof.",
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
                "literature/cross_domain_catalogs/index.json",
                "literature/resource_catalog.jsonl",
                "literature/resource_catalog_summary.json",
                "ideation/survey_insights.json",
            )
        ],
        "note_card_summary": {
            "source": "synthesis_workbench.json" if workbench else "paper_note_markdown_scan",
            "raw_card_count": len(raw_cards),
            "usable_card_count": len(usable_cards),
            "selected_card_count": len(selected_cards),
            "selection_policy": {
                "initial_limit": 12,
                "maximum_limit": 18,
                "strategy": "quality-ranked with method/bridge/gap/source lane diversity",
            },
            "deep_note_count": workbench.get("note_count", 0),
            "abstract_note_count": workbench.get("abstract_note_count", 0),
            "total_note_count": workbench.get("total_note_count", 0),
        },
        "note_cards": selected_cards,
        "deferred_note_card_pool": {
            "path": T4_EVIDENCE_POOL.as_posix(),
            "count": len(deferred_cards),
            "rule": "These cards were omitted only to bound the initial prompt. They remain available for targeted T4 verification.",
        },
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
        "resource_discovery_context": {
            "catalog_path": "literature/resource_catalog.jsonl",
            "summary_path": "literature/resource_catalog_summary.json",
            "available": bool(resource_catalog_summary),
            "record_count": int(resource_catalog_summary.get("record_count") or 0),
            "paper_count": int(resource_catalog_summary.get("paper_count") or 0),
            "by_resource_type": resource_catalog_summary.get("by_resource_type")
            if isinstance(resource_catalog_summary.get("by_resource_type"), dict)
            else {},
            "usage_boundary": (
                "Use resource discoveries to assess feasibility, identify official baselines, and name verification needs. "
                "A link is not evidence that a mechanism works, a baseline is equivalent, or a resource can be executed."
            ),
        },
        "bridge_domain_plan": {
            "source": bridge_plan.get("source"),
            "bridge_domains": bridge_plan.get("bridge_domains", []),
        }
        if bridge_plan
        else {},
        "cross_domain_catalogs": bridge_catalogs,
        "cross_domain_catalog_usage_boundary": (
            "Catalog records remain metadata/abstract-level transfer context. They can inspire a distinct mechanism, boundary, "
            "comparison, or validation question, but they never prove a mechanism, result, baseline equivalence, or external novelty claim."
        ),
        "domain_map_preview": {
            "bucket_summary": domain_map.get("bucket_summary") or domain_map.get("domain_map_bucket_summary"),
            "theory_bridge": domain_map.get("theory_bridge"),
        }
        if domain_map
        else {},
    }

    pack["outputs"] = [T4_CONTEXT_PACK_JSON.as_posix(), T4_CONTEXT_PACK_MD.as_posix(), T4_PROGRESS_MD.as_posix(), T4_EVIDENCE_POOL.as_posix()]
    json_path = workspace_dir / T4_CONTEXT_PACK_JSON
    md_path = workspace_dir / T4_CONTEXT_PACK_MD
    progress_path = workspace_dir / T4_PROGRESS_MD
    evidence_pool_path = workspace_dir / T4_EVIDENCE_POOL
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence_pool_path.write_text(
        json.dumps(
            {
                "semantics": "t4_revisitable_note_card_pool_not_initial_prompt_selection",
                "selected_count": len(selected_cards),
                "deferred_count": len(deferred_cards),
                "cards": deferred_cards,
                "usage": "Use grep_search/read_file for a named note. Evidence level and citation-use restrictions still apply.",
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render_t4_context_pack_markdown(pack), encoding="utf-8")
    progress_path.write_text(_render_t4_progress_markdown(pack), encoding="utf-8")
    refresh_t4_gate1_progress(workspace_dir)
    return pack


def ensure_t4_evidence_pool(workspace_dir: Path) -> dict[str, object]:
    """Backfill the revisitable T4 note-card pool without changing T2/T3 evidence.

    Older workspaces may already contain a compact context pack from before the
    deferred-card index existed. This derives that index from the same T3.5
    workbench and updates only T4 context artifacts; it never changes literature
    selection, paper notes, or reading budgets.
    """

    workspace_dir = Path(workspace_dir)
    evidence_pool_path = workspace_dir / T4_EVIDENCE_POOL
    if evidence_pool_path.is_file() and evidence_pool_path.stat().st_size > 0:
        return {"changed": False, "reason": "already_present", "path": T4_EVIDENCE_POOL.as_posix()}

    pack_path = workspace_dir / T4_CONTEXT_PACK_JSON
    pack = _read_json_file(pack_path)
    workbench = _read_json_file(workspace_dir / "literature" / "synthesis_workbench.json")
    raw_cards = _collect_workbench_note_cards(workbench)
    if not raw_cards:
        raw_cards = _collect_markdown_note_cards(workspace_dir)
    usable_cards = [
        _compact_note_card(card, workspace_dir)
        for card in raw_cards
        if isinstance(card, dict) and _note_card_usable_for_t4(card)
    ]
    if not usable_cards:
        return {"changed": False, "reason": "no_usable_note_cards", "path": T4_EVIDENCE_POOL.as_posix()}

    selected_cards = pack.get("note_cards") if isinstance(pack.get("note_cards"), list) else []
    selected_cards = [card for card in selected_cards if isinstance(card, dict)]
    if not selected_cards:
        selected_cards = _select_t4_compact_note_cards(usable_cards)
    selected_ids = {
        str(card.get("note_id") or card.get("source_file") or "")
        for card in selected_cards
    }
    deferred_cards = [
        {
            "note_id": card.get("note_id"),
            "title": card.get("title"),
            "source_file": card.get("source_file"),
            "evidence_level": card.get("evidence_level"),
            "citation_use": card.get("citation_use"),
            "citation_quality_score": card.get("citation_quality_score"),
            "revisit_reason": "available_outside_compact_context_pack",
        }
        for card in usable_cards
        if str(card.get("note_id") or card.get("source_file") or "") not in selected_ids
    ]
    evidence_pool_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_pool_path.write_text(
        json.dumps(
            {
                "semantics": "t4_revisitable_note_card_pool_not_initial_prompt_selection",
                "selected_count": len(selected_cards),
                "deferred_count": len(deferred_cards),
                "cards": deferred_cards,
                "usage": "Use grep_search/read_file for a named note. Evidence level and citation-use restrictions still apply.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if pack:
        pack["deferred_note_card_pool"] = {
            "path": T4_EVIDENCE_POOL.as_posix(),
            "count": len(deferred_cards),
            "rule": "These cards were omitted only to bound the initial prompt. They remain available for targeted T4 verification.",
        }
        outputs = pack.get("outputs") if isinstance(pack.get("outputs"), list) else []
        if T4_EVIDENCE_POOL.as_posix() not in outputs:
            outputs.append(T4_EVIDENCE_POOL.as_posix())
        pack["outputs"] = outputs
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (workspace_dir / T4_CONTEXT_PACK_MD).write_text(_render_t4_context_pack_markdown(pack), encoding="utf-8")
    return {
        "changed": True,
        "path": T4_EVIDENCE_POOL.as_posix(),
        "selected_count": len(selected_cards),
        "deferred_count": len(deferred_cards),
    }


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
        f"- Included in pack: {summary.get('selected_card_count', 0)}",
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
    bridge_catalogs = pack.get("cross_domain_catalogs") if isinstance(pack.get("cross_domain_catalogs"), list) else []
    if bridge_catalogs:
        lines.extend(["", "## Cross-domain Catalog Context"])
        lines.append("- Catalog items are inspiration, comparison, and reading-priority context only; a linked canonical note is required for direct claim use.")
        for item in bridge_catalogs[:8]:
            if not isinstance(item, dict):
                continue
            label = item.get("name") or item.get("bridge_id") or "Cross-domain bridge"
            lines.append(
                f"- {item.get('bridge_id', '')} · {label}: records={item.get('record_count', 0)}, "
                f"abstract_leads={item.get('abstract_record_count', 0)}, status={item.get('status', '')}"
            )
            rationale = str(item.get("rationale") or "").strip()
            if rationale:
                lines.append(f"  - intent: {rationale}")
            for record in item.get("sample_records", [])[:2] if isinstance(item.get("sample_records"), list) else []:
                if not isinstance(record, dict):
                    continue
                lines.append(f"  - lead: {record.get('title') or record.get('paper_id') or 'untitled'} ({record.get('usage_boundary', '')})")
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
                f"{summary.get('selected_card_count', 0)} 张可用卡片并全部写入 context pack。"
            ),
            "- [rule] T4 会先读 compact pack，只在核验具体 claim 时打开单篇 note。",
            "- [outputs] " + "; ".join(str(item) for item in outputs),
            "- [running] Gate1 前半段已开始；后续状态以本文件中的 artifact checkpoint 为准。",
            "",
        ]
    )


def _t4_bridge_coverage_required(workspace_dir: Path) -> bool:
    """Return whether T1/T3 declared a bridge lane that needs Gate1 audit."""

    plan = _read_json_file(workspace_dir / "literature" / "bridge_domain_plan.json")
    domains = plan.get("bridge_domains") if isinstance(plan.get("bridge_domains"), list) else []
    return bool(domains)


def _relative_t4_path(workspace_dir: Path, value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        return path.resolve().relative_to(workspace_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix().lstrip("./")


def _read_t4_execution_events(workspace_dir: Path, *, limit: int = 120) -> list[dict[str, object]]:
    """Read only structured public T4 telemetry; malformed lines are ignored."""

    path = workspace_dir / T4_EXECUTION_EVENTS
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("phase") and item.get("status"):
                events.append(item)
    except OSError:
        return []
    return events


def _render_t4_execution_event(event: dict[str, object]) -> str:
    phase_labels = {
        "context_pack": "上下文包",
        "pass1_mainline": "Pass1 主线",
        "pass1_supplement": "Pass1 补充通道",
        "pass2_grounding": "Pass2 接地复核",
        "scoring": "评分整理",
        "gate_cards": "Gate1 卡片",
    }
    status_labels = {
        "started": "已开始",
        "candidate_started": "候选开始",
        "candidate_completed": "候选完成",
        "channel_started": "通道开始",
        "channel_completed": "通道完成",
        "completed": "已完成",
    }
    phase = phase_labels.get(str(event.get("phase") or ""), str(event.get("phase") or "T4"))
    status = status_labels.get(str(event.get("status") or ""), str(event.get("status") or "更新"))
    completed = event.get("completed")
    total = event.get("total")
    count = f" {completed}/{total}" if completed is not None and total is not None else ""
    subject = str(event.get("candidate_id") or event.get("channel") or "").strip()
    title = _shorten(str(event.get("candidate_title") or ""), 76)
    suffix = ""
    if subject:
        suffix += f" · {subject}"
    if title:
        suffix += f" · {title}"
    if event.get("recommendation"):
        suffix += f" · 建议={event.get('recommendation')}"
    scores = event.get("score_snapshot") if isinstance(event.get("score_snapshot"), dict) else {}
    if scores:
        suffix += " · 评分=" + ", ".join(f"{key}={value}/5" for key, value in scores.items())
    return f"- [event] {phase}{count} · {status}{suffix}"


_T4_SUPPLEMENT_CHANNELS: tuple[tuple[str, str, str], ...] = (
    (
        "mechanism_challenge",
        "S1 机制挑战",
        "检验替代机制与失效边界；通常作为主线的反证模块。",
    ),
    (
        "reverse_operation",
        "S2 反向操作",
        "移除、关闭或反转机制成分，形成消融或反事实检验。",
    ),
    (
        "subgroup_failure",
        "S3 子群失败",
        "定位子群、状态或数据条件下的失败模式与边界。",
    ),
    (
        "missing_area_exploration",
        "S4 缺口探索",
        "探索已确认空白；证据不足时先补检，不能直接升级为主张。",
    ),
)


def _t4_supplement_channel_states(events: list[dict[str, object]]) -> dict[str, str]:
    """Reduce durable public events to one visible state per supplement lane."""

    states = {channel: "queued" for channel, _label, _note in _T4_SUPPLEMENT_CHANNELS}
    for event in events:
        if str(event.get("phase") or "") != "pass1_supplement":
            continue
        channel = str(event.get("channel") or "").strip()
        if channel not in states:
            continue
        status = str(event.get("status") or "")
        if status == "channel_completed":
            states[channel] = "done"
        elif status in {"channel_started", "candidate_started", "candidate_completed"} and states[channel] != "done":
            states[channel] = "running"
    return states


def refresh_t4_gate1_progress(
    workspace_dir: Path,
    *,
    active_path: str | None = None,
    paused_reason: str | None = None,
) -> dict[str, object]:
    """Render T4 Gate1 progress from persisted files, never from assumptions.

    This function is intentionally deterministic so that a provider timeout,
    restart, or resume shows the same checkpoint state.  It distinguishes
    Gate1's required pre-selection artifacts from the post-selection work,
    avoiding a misleading wall of ``pending`` files.
    """

    workspace_dir = Path(workspace_dir)
    ideation_dir = workspace_dir / "ideation"
    ideation_dir.mkdir(parents=True, exist_ok=True)
    active_rel = _relative_t4_path(workspace_dir, active_path)
    compact_pack_paths = (
        "ideation/t4_context_pack.json",
        "ideation/t4_context_pack.md",
    )
    compact_pack_ready = all((workspace_dir / path).exists() for path in compact_pack_paths)
    final_cards_ready, final_cards_error = validate_t4_portfolio_final_cards(workspace_dir)
    completed_count = sum(
        1
        for rel_path, _label in T4_GATE1_ARTIFACTS
        if (
            final_cards_ready
            if rel_path == "ideation/final_cards/portfolio_cards.json"
            else (workspace_dir / rel_path).exists()
        )
    )
    total_count = len(T4_GATE1_ARTIFACTS)
    bridge_required = _t4_bridge_coverage_required(workspace_dir)
    bridge_payload = _read_json_file(workspace_dir / T4_BRIDGE_COVERAGE_PATH)
    bridge_exists = (workspace_dir / T4_BRIDGE_COVERAGE_PATH).exists()
    bridge_reviews = bridge_payload.get("bridge_reviews") if isinstance(bridge_payload.get("bridge_reviews"), list) else []
    unreviewed_bridges = [
        str(item.get("bridge_id") or "bridge")
        for item in bridge_reviews
        if isinstance(item, dict) and str(item.get("review_status") or "").strip() == "unreviewed"
    ]
    events = _read_t4_execution_events(workspace_dir)
    supplement_states = _t4_supplement_channel_states(events)

    lines = ["# T4 Gate1 Progress", ""]
    lines.extend(
        [
            "## 通道说明（公开执行角色，不含模型内部推理）",
            "- **D 主线**：面向论文主贡献的候选路线；可被选择、合并或重构。",
            "- **Cross-domain 候选**：来自已确认跨领域方向的机制迁移或结构类比；选择后必须回查相应文献笔记 section。",
            "- **证据不足**：保留可见性，但补足明确证据前不应作为最终主张。",
            "- **S 补充**：反证、失败分析或消融路线；默认服务于 D 主线，不单独承担论文主贡献。",
            "",
            "### Pass1 补充通道状态",
        ]
    )
    for channel, label, note in _T4_SUPPLEMENT_CHANNELS:
        lines.append(f"- [{supplement_states[channel]}] `{channel}` / {label}：{note}")
    lines.append("")
    lines.append("## Gate1 前半段（候选池，自动执行）")
    if compact_pack_ready:
        compact_state = "done"
        compact_note = "已生成 Gate1 候选构思用 compact context pack；具体 claim 仅在需要时回查对应笔记 section。"
    elif paused_reason:
        compact_state = "paused"
        compact_note = "前置 compact context pack 尚未完整落盘；恢复后会先继续构建它。"
    else:
        compact_state = "running"
        compact_note = "正在构建 Gate1 候选构思用 compact context pack。"
    lines.append(
        f"- [{compact_state}] compact context pack：`ideation/t4_context_pack.json`、`ideation/t4_context_pack.md`"
    )
    lines.append(f"- [{compact_state}] {compact_note}")
    current_label = "正在准备 Pass 1 候选发散" if compact_pack_ready else "正在构建 Gate1 compact context pack"
    next_artifact_label = "compact context pack"
    first_incomplete_seen = False
    for index, (rel_path, label) in enumerate(T4_GATE1_ARTIFACTS, start=1):
        exists = (
            final_cards_ready
            if rel_path == "ideation/final_cards/portfolio_cards.json"
            else (workspace_dir / rel_path).exists()
        )
        if exists:
            state = "done"
        elif not compact_pack_ready:
            state = "queued"
        elif paused_reason:
            state = "paused" if not first_incomplete_seen else "queued"
            first_incomplete_seen = True
        elif active_rel == rel_path:
            state = "running"
            first_incomplete_seen = True
            current_label = f"正在写入 {label}：`{rel_path}`"
        elif not first_incomplete_seen:
            state = "running"
            first_incomplete_seen = True
            current_label = f"正在生成 {label}：`{rel_path}`"
            next_artifact_label = f"{index}/{total_count} {label}"
        else:
            state = "queued"
        if not exists and next_artifact_label == "compact context pack" and compact_pack_ready:
            next_artifact_label = f"{index}/{total_count} {label}"
        lines.append(f"- [{state}] {index}/{total_count} {label}：`{rel_path}`")
        if rel_path == "ideation/final_cards/portfolio_cards.json" and not exists and final_cards_error:
            lines.append(f"  - 卡片状态：{str(final_cards_error)[:500]}")

    if unreviewed_bridges:
        bridge_state = "degraded"
        bridge_note = (
            "已记录 Cross-domain 路线未返回 LLM 复核："
            + ", ".join(unreviewed_bridges)
            + "。现有候选仍可进入 Gate1；可请求该路线重新探索。"
        )
    else:
        bridge_state = "done" if bridge_exists else ("queued" if bridge_required else "not_required")
        bridge_note = "上游已声明 Cross-domain 方向，Gate1 会保留覆盖说明与后续复核入口。" if bridge_required else "上游没有 Cross-domain 方向；仅在候选实际采用跨域类比时生成。"
    lines.extend(
        [
            f"- [{bridge_state}] Cross-domain 候选覆盖说明：`{T4_BRIDGE_COVERAGE_PATH}`（{bridge_note}）",
            "",
            "## Gate1 后半段（等待人工选择后才开始）",
            "- [waiting_human] 用户可选择、合并、重构候选，或请求重新分析。",
            "- [waiting_human] 后续才会生成 `ideation/idea_scorecard.yaml`、`ideation/hypotheses.md`、`ideation/exp_plan.yaml` 和风险/决策记录。",
        ]
    )
    if events:
        lines.extend(["", "## 可观察执行轨迹（不含模型内部推理）"])
        for event in events:
            lines.append(_render_t4_execution_event(event))
        latest = events[-1]
        if not paused_reason and str(latest.get("status") or "") not in {"completed", "channel_completed", "candidate_completed"}:
            current_label = _render_t4_execution_event(latest).removeprefix("- [event] ")
    else:
        lines.extend(
            [
                "",
                "## 可观察执行轨迹（不含模型内部推理）",
                "- [waiting] 尚未写入候选级事件；runtime 会在候选文件落盘时补充可验证摘要。",
            ]
        )
    if paused_reason:
        current_label = f"已暂停：{paused_reason}"
        lines.append("")
        lines.append(f"- [paused] {paused_reason}")
    elif completed_count == total_count:
        current_label = "Gate1 候选池必需产物已落盘，正在等待人工选择"
        next_artifact_label = "等待人工选择"
    lines.append("")
    (workspace_dir / T4_PROGRESS_MD).write_text("\n".join(lines), encoding="utf-8")
    return {
        "completed_count": completed_count,
        "total_count": total_count,
        "bridge_required": bridge_required,
        "bridge_exists": bridge_exists,
        "current_label": current_label,
        "next_artifact_label": next_artifact_label,
        "path": T4_PROGRESS_MD.as_posix(),
    }


class IdeationAgent(Agent):
    """Recovery-facing T4 agent; native evolution is controller-owned."""

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
                        "lookup_paper_record",
                        "analyze_idea_concentration",
                        "compute_idea_novelty_signal",
                        "log_t4_ideation_progress",
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
                    "structured_outputs": {},
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息和文献综述。"""
        if ctx.task_id == "T4" and ctx.extra.get("t4_execution_mode") != "legacy_fallback":
            return (
                "T4 uses the controller-owned Evidence-Routed Evolutionary Pipeline. "
                "This compatibility agent must not render or execute the legacy ideation prompt. "
                "The runtime will either complete the native controller flow or pause for its required pre-run gate."
            )
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
        shallow_reading_summary = _shallow_reading_prompt_summary(synthesis_workbench)
        note_card_summary = "" if t4_context_pack.strip() else _note_card_prompt_summary(synthesis_workbench)
        budgets = _t4_prompt_budgets(ctx.extra.get("runtime_context_window"))

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            synthesis_preview=_t4_prompt_material(
                synthesis, path="literature/synthesis.md", budget_chars=budgets["synthesis"]
            ),
            missing_areas=_t4_prompt_material(
                missing_areas, path="literature/missing_areas.md", budget_chars=budgets["missing_areas"]
            ),
            seed_ideas=_t4_prompt_material(
                seed_ideas, path="user_seeds/seed_ideas.md", budget_chars=budgets["seed_ideas"]
            ),
            comparison_table_preview=_t4_prompt_material(
                comparison_table, path="literature/comparison_table.csv", budget_chars=budgets["comparison_table"]
            ),
            domain_map_preview=_t4_prompt_material(
                domain_map, path="literature/domain_map.json", budget_chars=budgets["domain_map"]
            ),
            bridge_domain_plan_preview=_t4_prompt_material(
                bridge_domain_plan,
                path="literature/bridge_domain_plan.json",
                budget_chars=budgets["bridge_domain_plan"],
            ),
            synthesis_workbench_preview=_t4_prompt_material(
                synthesis_workbench,
                path="literature/synthesis_workbench.json",
                budget_chars=budgets["workbench"],
            ),
            t4_context_pack_preview=_t4_prompt_material(
                t4_context_pack, path=T4_CONTEXT_PACK_MD.as_posix(), budget_chars=budgets["context_pack"]
            ),
            shallow_reading_summary=shallow_reading_summary,
            note_card_summary=note_card_summary,
            survey_insights_preview=_t4_prompt_material(
                survey_insights, path="ideation/survey_insights.json", budget_chars=budgets["survey_insights"]
            ),
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
        """Keep the rare legacy fallback inside the native T4 lifecycle."""

        from ..orchestration.state_machine import validate_t4_gate1_selection_file

        selection_ok, selection_error = validate_t4_gate1_selection_file(ctx.workspace_dir)
        if not selection_ok:
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行 T4 Gate1 前半段的恢复检查。当前尚无合法 "
                    f"`ideation/_gate1_user_selection.json`（{selection_error}）。先检查 "
                    "`ideation/t4_context_pack.md`、`ideation/t4_progress.md`、native Evolution Artifact 与 "
                    "`_pass1_forward_candidates.json`、`_pass2_grounding_review.json`、"
                    "`_candidate_directions.json`、`_family_distribution.md`、"
                    "`_gate1_candidate_cards.md`、`_gate1_selection_brief.md`。"
                    "不要在本轮调用 ask_human，不要写hypotheses.md 或 exp_plan.yaml；Rich Gate 会处理研究者选择。"
                ),
            )
        return prepend_resume_prefix(
            ctx,
            (
                "请执行 T4 Gate1 后半段的恢复交接：读取 `_gate1_user_selection.json` 与 selection_fingerprint，"
                "检查 native Evolution Artifact、已选 Candidate、Pre-Novelty brief 和 T4.5 search targets。"
                "不要生成或覆盖 Candidate，不要调用 ask_human，不要写正式 hypotheses.md 或 exp_plan.yaml；"
                "它们只会在 T4.5 audit 通过后 formalize。若状态可恢复，调用 finish_task，说明保留了什么、"
                "下一步进入哪个 Rich Gate 或 T4.5，以及是否可 rollback。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构 + schema + 引用一致性。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir
        hyp_text = read_text_file(ws / "ideation" / "hypotheses.md")
        if not _t4_substantive_text(hyp_text):
            return False, "hypotheses.md 为空或仍是占位内容"
        # This legacy validation is only reached for the formal hypothesis
        # handoff.  Keep its explicit content floor ahead of structural
        # checks so a truncated write is diagnosed as such rather than as an
        # unrelated missing-anchor error.
        if len(hyp_text.strip()) < 500:
            return False, f"hypotheses.md 过短({len(hyp_text.strip())} 字符)，至少需要500字符的正式假设说明"

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
            if not _t4_substantive_text(reasoning):
                return False, f"idea_rationales.json 第{i}条idea的reasoning为空或仍是占位内容"

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
                if not _t4_substantive_text(design_rationale):
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 缺少 CDR design_rationale；"
                        "选中或进入最终假设的 idea 必须说明为什么 artifact 应该这样设计"
                    )
                if contribution_type not in {"invention", "improvement", "exaptation"}:
                    return False, (
                        f"idea_scorecard.yaml idea {idea_id} 的 contribution_type 不能为 "
                        f"{contribution_type or '空'}；selected idea 不能是 routine"
                    )
                if not _t4_substantive_text(contribution_character):
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

        # R2: family distribution must remain readable and non-placeholder.
        family_dist_path = ws / "ideation" / "_family_distribution.md"
        if not family_dist_path.exists():
            return False, "缺少 ideation/_family_distribution.md，必须在生成 scorecard 前写入 family distribution"
        family_dist_text = read_text_file(family_dist_path)
        if not _t4_substantive_text(family_dist_text):
            return False, "ideation/_family_distribution.md 为空或仍是占位内容"

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
        if not _t4_substantive_text(rejected_text):
            return False, "rejected_ideas.md 为空或仍是占位内容"
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
            return False, f"legacy gate_decisions.json 缺少历史 Gate 记录: {missing_gates}"
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
        raw_budget = (project.get("constraints") or {}).get("max_budget_usd") if isinstance(project, dict) else None
        try:
            max_budget = float(raw_budget) if raw_budget is not None else None
        except (TypeError, ValueError):
            max_budget = None
        total_estimated_cost = 0.0
        has_complete_cost_estimates = True
        for exp in experiments:
            estimate = exp.get("compute_estimate", {}) or {}
            estimated_cost = estimate.get("estimated_cost_usd")
            if estimated_cost is None:
                has_complete_cost_estimates = False
                continue
            try:
                exp_cost = float(estimated_cost)
            except (TypeError, ValueError):
                has_complete_cost_estimates = False
                continue
            total_estimated_cost += exp_cost

        declared_total = plan_data.get("total_estimated_cost_usd")
        if max_budget is not None and declared_total is not None and float(declared_total) > max_budget:
            return False, (
                f"exp_plan.yaml 声明总成本 ${float(declared_total):.2f} "
                f"超过项目预算 ${float(max_budget):.2f}"
            )

        if max_budget is not None and has_complete_cost_estimates and total_estimated_cost > max_budget:
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
    if not isinstance(candidates, list) or not candidates:
        return False, "_candidate_directions.json 至少需要一个可供人工查看的候选方向"

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
        if not origin:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 idea_origin"
        if not status:
            return False, f"_candidate_directions.json 第{idx}条候选缺少 constraint_status"
        # ``basis_summary`` is valuable LLM-authored explanation, but it is
        # not a prerequisite for keeping an exploratory, traceable Candidate
        # visible.  Informed-brainstorm and cross-domain routes are allowed
        # to arrive as conjectural idea fuel; the UI labels a missing basis as
        # an enrichment/evidence task instead of falsely treating the idea as
        # evidence-supported or pausing the whole Population.
        # Projection can retain a traceable Candidate whose optional Gate1
        # presentation, legacy score view, or card translation was incomplete.
        # The projection marks that fact explicitly and the Gate UI prevents
        # direct T4.5 selection.  Treating this local display gap as a global
        # artifact failure used to hide every other valid Candidate and pause
        # the whole evolution run. Identity and route metadata remain
        # mandatory; research prose is never synthesized here.
        projection_status = str(candidate.get("projection_status") or "").strip().lower()
        if projection_status == "degraded":
            diagnostics = candidate.get("projection_diagnostics")
            if not isinstance(diagnostics, list) or not any(str(item).strip() for item in diagnostics):
                return False, (
                    f"_candidate_directions.json 候选 {idea_id} 标记为 degraded，"
                    "但未记录可追溯的展示诊断"
                )
            continue
        ok, err = _validate_t4_candidate_authored_content(candidate, idea_id, status)
        if not ok:
            return False, err
        minimum = candidate.get("minimum_experiment")
        if isinstance(minimum, dict) and minimum.get("evidence_status") is not None:
            protocol_status = str(minimum.get("evidence_status") or "").strip().lower()
            allowed_protocol_statuses = {
                "supported",
                "user_provided",
                "proposed_not_verified",
                "unknown",
            }
            if protocol_status not in allowed_protocol_statuses:
                return False, (
                    f"_candidate_directions.json 候选 {idea_id} 的 minimum_experiment.evidence_status "
                    f"无效：{protocol_status or 'empty'}"
                )
            refs = minimum.get("source_refs")
            valid_refs = [str(item).strip() for item in refs if str(item).strip()] if isinstance(refs, list) else []
            if protocol_status in {"supported", "user_provided"} and not valid_refs:
                return False, (
                    f"_candidate_directions.json 候选 {idea_id} 将最小验证标为 {protocol_status}，"
                    "但缺少 source_refs；不得把无来源的实验提议写成既定协议"
                )
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

    # Bridge coverage is visible through the durable escape-hatch review. A
    # missing bridge Candidate is an evidence-upgrade signal, not a reason to
    # throw away an otherwise usable P0/P1 population.
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

    # Route mix is a portfolio-quality signal. It is intentionally not a
    # correctness gate: a constrained project can have a small viable
    # mainline or a deferred Bridge while still providing useful Candidates
    # for a human to compare, refine, or regenerate.
    return True, None


_T4_PLACEHOLDER_RE = re.compile(
    r"^(?:\.{2,}|…+|待(?:补充|确定|核验)|unknown|n/?a|none|tbd|"
    r"candidate[_ -]?draft|see\s+(?:core_claim|above)|未提供|未标注)$",
    flags=re.IGNORECASE,
)
_T4_CORE_SCORE_KEYS = (
    "research_value",
    "mechanism_integrity",
    "contribution_distinctiveness",
)


def _t4_substantive_text(value: Any, *, min_chars: int = 16) -> bool:
    """Reject empty/template fields; prose length is a quality signal, not a gate."""

    del min_chars
    text = " ".join(str(value or "").split())
    return bool(text) and not bool(_T4_PLACEHOLDER_RE.fullmatch(text))


def _validate_t4_candidate_authored_content(
    candidate: dict[str, Any],
    idea_id: str,
    constraint_status: str,
) -> tuple[bool, str | None]:
    """Validate only the Gate1 safety boundary, never a paper-completeness quota.

    Gate1 is intentionally an exploration and human-comparison surface.  A
    Candidate may arrive there as a useful Seed, with one provisional
    hypothesis, an incomplete final-card explanation, a missing legacy score
    grid, or a not-yet-enriched evidence interpretation.  Those are visible
    *diagnostics* for focused evolution; they must not discard a traceable
    Candidate Population or force the model to invent prose merely to satisfy
    a serializer.

    The canonical ``CandidateDossier`` / ``ScoreReport`` models enforce the
    richer native contracts when those artifacts are created.  This legacy
    projection validator therefore retains only what cannot be safely
    recovered later: a Candidate must still contain a real conceptual anchor.
    Source/claim boundaries, identity, lineage, and any explicitly-supported
    experimental claim are checked by the surrounding validator.
    """

    del constraint_status
    conceptual_anchor_fields = (
        "title",
        "display_title",
        "pitch",
        "core_claim",
        "target_problem",
        "mechanism",
    )
    if not any(_t4_substantive_text(candidate.get(field)) for field in conceptual_anchor_fields):
        return False, (
            f"_candidate_directions.json 候选 {idea_id} 没有可追溯的研究命题；"
            "至少保留 title、pitch、core_claim、target_problem 或 mechanism 中的一项真实 Candidate 内容"
        )

    # The three native scores are authoritative when available.  Do not
    # demand them here: scorer outages or an optional display projection may
    # leave a Candidate visibly unscored.  But a value that *is* supplied must
    # not masquerade as a valid score outside the documented 1--5 range.
    evolution_score = candidate.get("evolution_score")
    if isinstance(evolution_score, dict):
        dimensions = evolution_score.get("dimensions")
        if isinstance(dimensions, dict):
            for key in _T4_CORE_SCORE_KEYS:
                if key not in dimensions:
                    continue
                try:
                    value = float(dimensions[key])
                except (TypeError, ValueError):
                    return False, (
                        f"_candidate_directions.json 候选 {idea_id} 的正式评分 {key} 不是数值；"
                        "请修复该分数或将 Candidate 标为未评分，不能把损坏数值展示为科学判断"
                    )
                if not 1.0 <= value <= 5.0:
                    return False, (
                        f"_candidate_directions.json 候选 {idea_id} 的正式评分 {key} 超出 1-5 范围；"
                        "请修复该分数或将 Candidate 标为未评分"
                    )

    # ``scores`` / ``score_rationale`` deliberately are not inspected here.
    # They are a deprecated seven-dimension compatibility view.  Likewise,
    # title-card prose, innovation explanation, basis-source interpretation,
    # hypothesis count, Profile Fit, and qualitative diagnostics are all
    # enrichment work.  The renderer must disclose their absence rather than
    # fabricate a generic explanation, while evolution/Human Gate can request
    # a focused LLM enrichment.
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
    """Compatibility hook that intentionally never writes research prose.

    Gate1 Markdown cards and selection briefs are authored by the LLM.  Older
    versions reconstructed them from candidate JSON during resume, which
    produced generic language and incomplete hypothesis chains.  Missing or
    outdated artifacts now fail normal T4 validation and return to the model
    for a focused repair.
    """

    del ws
    return False


def _candidate_display_text(candidate: dict) -> dict[str, str]:
    """Project candidate-owned display fields without runtime research rewriting."""

    title = str(candidate.get("title") or "Untitled candidate").strip()
    localized_title = str(candidate.get("title_zh") or "").strip()
    return {
        "title": localized_title or title,
        "original_title": title if localized_title else "",
        "pitch": str(candidate.get("pitch_zh") or candidate.get("pitch") or candidate.get("core_claim") or "模型未提供"),
        "mechanism": str(candidate.get("mechanism_zh") or candidate.get("mechanism") or "模型未提供"),
        "prediction": str(candidate.get("prediction_zh") or candidate.get("prediction") or "模型未提供"),
        "counterfactual": str(candidate.get("counterfactual_zh") or candidate.get("counterfactual") or "模型未提供"),
    }


def refresh_t4_gate1_candidate_presentation(ws: Path) -> bool:
    """Retired compatibility hook for callers of the old presentation refresh.

    A renderer may lay out factual fields but must never rewrite LLM-authored
    candidate cards or decision advice.  Returning ``False`` tells callers
    that no files were changed.
    """

    del ws
    return False


def _candidate_card_short_title(candidate: dict, display: dict[str, str]) -> str:
    """Return a bounded label suitable for a terminal/Markdown heading."""

    raw = str(
        candidate.get("display_title")
        or candidate.get("title_short_zh")
        or candidate.get("short_title")
        or display.get("title")
        or "未命名方向"
    ).strip()
    limit = 32 if re.search(r"[\u4e00-\u9fff]", raw) else 80
    return _shorten(" ".join(raw.split()), limit)


def _candidate_card_hypotheses(candidate: dict, idea_id: str, display: dict[str, str]) -> list[dict[str, str]]:
    raw = candidate.get("candidate_hypotheses")
    hypotheses: list[dict[str, str]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw[:3], start=1):
            if not isinstance(item, dict):
                continue
            hypotheses.append(
                {
                    "id": str(item.get("id") or f"{idea_id}-H{index}").strip(),
                    "statement": str(item.get("statement") or item.get("hypothesis") or "待补充").strip(),
                    "mechanism": str(item.get("mechanism") or "待补充").strip(),
                    "prediction": str(item.get("observable_prediction") or item.get("prediction") or "待补充").strip(),
                    "test": str(item.get("discriminating_test") or item.get("test") or "待补充").strip(),
                }
            )
    if hypotheses:
        return hypotheses
    # Compatibility/recovery paths expose only the one testable proposition
    # already present in the durable candidate. They must not invent H2/H3.
    return [
        {
            "id": f"{idea_id}-H1",
            "statement": display["pitch"],
            "mechanism": display["mechanism"],
            "prediction": display["prediction"],
            "test": display["counterfactual"],
        }
    ]


def _candidate_card_innovation(candidate: dict) -> dict[str, str]:
    raw = candidate.get("innovation") if isinstance(candidate.get("innovation"), dict) else {}
    return {
        "summary": str(raw.get("summary") or "未提供明确创新说明；选择前需要补写，不能把假设本身当成创新。"),
        "type": str(raw.get("type") or "待界定"),
        "delta": str(raw.get("novelty_delta") or "未提供相对最近工作的明确差异。"),
        "non_incremental": str(raw.get("non_incremental_reason") or "未提供非增量理由；Pass2 需继续核验。"),
    }


def _candidate_card_merges(candidate: dict) -> list[dict[str, str]]:
    raw = candidate.get("merge_opportunities")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "with": str(item.get("with_candidate") or item.get("candidate_id") or "未指定"),
                "combine": str(item.get("combine") or "未提供假设组合"),
                "rationale": str(item.get("rationale") or "未提供组合理由"),
            }
        )
    return result


def _candidate_minimum_experiment_display(minimum: dict) -> tuple[str, str]:
    """Render a candidate protocol without presenting unsourced details as facts."""

    status = str(minimum.get("evidence_status") or "legacy_unverified").strip().lower()
    sources = minimum.get("source_refs")
    refs = [str(item).strip() for item in sources if str(item).strip()] if isinstance(sources, list) else []
    labels = {
        "supported": "已由可追溯材料支持",
        "user_provided": "由人工明确提供",
        "proposed_not_verified": "待验证的实验提议，不是既有协议",
        "unknown": "当前未知，必须补充材料后确定",
        "legacy_unverified": "遗留候选未声明协议来源；不得视为既定实验配置",
    }
    return labels.get(status, f"协议状态未识别：{status}"), ", ".join(refs) if refs else "无；需要补充证据或人工决策"


def _candidate_card_lane_description(candidate: dict, idea_id: str) -> str:
    """Explain the candidate's public Gate1 role from durable fields only."""

    lane = str(candidate.get("constraint_status") or candidate.get("lane") or "").strip().lower()
    origin = str(candidate.get("idea_origin") or candidate.get("origin") or "").strip().lower()
    if "not_supported" in lane or ("evidence" in lane and "not" in lane):
        return "证据不足候选：保留可见性以供讨论；补足对应笔记 section 的机制证据前，不应升级为最终主张。"
    if "bridge" in lane or "bridge" in origin:
        return "Cross-domain 候选：来自已确认的跨领域方向；选择后必须重新核验相应文献笔记 section 的可迁移边界。"
    if idea_id.upper().startswith("S") or "supplement" in lane:
        return "补充候选：用于反证、失败分析或消融；默认服务于 D 主线，而非单独承担论文主贡献。"
    return "主线候选：可作为论文主贡献路线被选择、合并或重构；仍需完成后半段的定向证据回查。"


def _render_gate1_candidate_cards(candidates: list[dict], pass2_by_id: dict[str, dict]) -> str:
    """Retired deterministic card renderer.

    This symbol remains only to fail closed for any third-party extension that
    still calls it. Gate1 research prose must be produced in the T4 LLM turn,
    never reconstructed from structured fields during resume or display.
    """

    del candidates, pass2_by_id
    raise RuntimeError(
        "Deterministic Gate1 card rendering is retired; run T4 LLM reanalysis to write the candidate deck."
    )

    score_order = ["novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength"]
    score_labels = {
        "novelty": "新颖性",
        "feasibility": "可行性",
        "impact": "影响力",
        "evaluability": "可评估性",
        "differentiation": "差异化",
        "cost": "资源成本",
        "contribution_strength": "贡献强度",
    }
    lines = [
        "# T4 Gate1 候选方向卡片",
        T4_GATE1_CARD_SCHEMA_MARKER,
        "",
        "## 阅读与选择",
        "- 每个 `D#` 是可选研究方向；其下 `D#-H#` 是候选假设/机制，并非最终 `hypotheses.md`。",
        "- 先比较创新变化、可证伪命题、最小验证、证据边界和 kill criteria；分数只支持判断，不替代人工选择。",
        "- 可直接输入：`选 D1，强调……`、`合并 D1-H2 + D3-H1`、`新想法：……` 或 `重新分析：……`。",
        "",
        "## 通道说明",
        "- **D 主线**：面向论文主贡献的候选路线，可选择、合并或重构。",
        "- **Bridge**：从已确认跨领域机制导出的路线；后半段必须回查 bridge 文献笔记 section，不能把类比直接写成结论。",
        "- **证据不足**：保持可见以供人工判断，但补足明确机制证据前不应成为最终主张。",
        "- **S 补充**：服务于反证、失败分析或消融，默认不单独构成论文主贡献。",
        "",
        "### 四个补充通道",
        "- **S1 / mechanism_challenge**：挑战声称机制，检验替代解释和失效边界。",
        "- **S2 / reverse_operation**：移除、反转或关闭机制成分，形成消融或反事实检验。",
        "- **S3 / subgroup_failure**：定位子群、状态或数据条件下的失败模式。",
        "- **S4 / missing_area_exploration**：探索已确认研究空白；先补证据，再决定是否升级为主线。",
        "",
    ]
    for rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        idea_id = str(candidate.get("id") or candidate.get("idea_id") or f"D{rank}").strip()
        display = _candidate_display_text(candidate)
        short_title = _candidate_card_short_title(candidate, display)
        full_title = str(candidate.get("title") or "").strip()
        pass2 = candidate.get("pass2_screening") if isinstance(candidate.get("pass2_screening"), dict) else {}
        review = pass2_by_id.get(idea_id, {})
        recommendation = str(pass2.get("screening_recommendation") or review.get("screening_recommendation") or "review_needed").strip()
        warning = str(pass2.get("selection_warning") or review.get("selection_warning") or "none").strip()
        innovation = _candidate_card_innovation(candidate)
        hypotheses = _candidate_card_hypotheses(candidate, idea_id, display)
        merges = _candidate_card_merges(candidate)
        scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
        rationale = candidate.get("score_rationale") if isinstance(candidate.get("score_rationale"), dict) else {}
        basis_sources = candidate.get("basis_sources") if isinstance(candidate.get("basis_sources"), list) else []
        papers = candidate.get("supporting_papers") if isinstance(candidate.get("supporting_papers"), list) else []
        if not papers and basis_sources:
            papers = [{"title": str(item.get("ref") or item.get("type") or "source"), "claim_used": str(item.get("claim") or "")} for item in basis_sources if isinstance(item, dict)]
        minimum = candidate.get("minimum_experiment") if isinstance(candidate.get("minimum_experiment"), dict) else {}
        metrics = minimum.get("metric")
        metrics_text = ", ".join(str(item) for item in metrics) if isinstance(metrics, list) else str(metrics or "待定")
        risks = candidate.get("key_risks") or candidate.get("risks") or []
        nearest = candidate.get("nearest_prior_work") if isinstance(candidate.get("nearest_prior_work"), dict) else {}
        protocol_status, protocol_sources = _candidate_minimum_experiment_display(minimum)
        lines.extend(
            [
                "=" * 88,
                f"## {idea_id} · {short_title}",
                f"**方向类型**：{candidate.get('constraint_status') or '未标注'} | **来源**：{candidate.get('idea_origin') or '未标注'} | **Pass2 建议**：{recommendation}",
                f"**候选角色**：{_candidate_card_lane_description(candidate, idea_id)}",
                f"**核心命题**：{display['pitch']}",
                f"**选择风险**：{warning}",
                "",
                "### 方向概要",
                f"- **研究问题**：{candidate.get('target_problem') or '待补充'}",
                f"- **技术机制主线**：{display['mechanism']}",
                f"- **完整方向描述**：{_shorten(full_title or display['pitch'], 760)}",
                f"- **实践/管理含义**：{_candidate_practical_implication(candidate)}",
                "",
                "### 核心创新",
                f"- **创新是什么**：{innovation['summary']}",
                f"- **创新类型**：{innovation['type']}",
                f"- **相对最近工作的变化**：{innovation['delta']}",
                f"- **为何不是普通增量**：{innovation['non_incremental']}",
                "",
                "### 候选假设链（仅供 Gate1 选择，尚未写入最终假设）",
            ]
        )
        for hypothesis in hypotheses:
            lines.extend(
                [
                    f"- **{hypothesis['id']}**",
                    f"  - **可证伪命题**：{hypothesis['statement']}",
                    f"  - **机制**：{hypothesis['mechanism']}",
                    f"  - **可观测预测**：{hypothesis['prediction']}",
                    f"  - **判别测试**：{hypothesis['test']}",
                ]
            )
        if len(hypotheses) < 2:
            lines.append("- 当前候选假设不足两条；必须由 LLM 基于证据重新分析并补全不同、可证伪的假设链，展示层不会补写。")
        lines.extend(["", "### 可组合关系"])
        if merges:
            for merge in merges:
                lines.append(f"- `{merge['combine']}`（与 `{merge['with']}`）：{merge['rationale']}")
        else:
            lines.append("- 当前候选池未提供可组合关系；可在 Gate 输入 `合并 D1-H1 + D3-H1` 提出具体组合。")
        lines.extend(
            [
                "",
                "### 最小验证与证据边界",
                f"- **最小验证（候选提议）**：数据/任务={minimum.get('dataset', '待定')}；基线={minimum.get('baseline', '待定')}；指标={metrics_text}；预期信号={minimum.get('expected_signal', '待定')}",
                f"  - **协议证据状态**：{protocol_status}；来源={protocol_sources}",
                f"- **核心文献依赖**：{_format_card_papers(papers)}",
                f"- **最近工作与差异**：{nearest.get('work', 'not_computed')}（distance={nearest.get('distance', 'not_computed')}）；novelty_signal={candidate.get('novelty_signal') or review.get('novelty_signal') or 'not_computed'}",
                f"- **接地摘要**：{candidate.get('basis_summary') or candidate.get('contribution_character') or '见结构化候选池'}",
                f"- **主要风险 / kill criteria**：{_format_card_risks(risks)}",
                "",
                "### 评分与依据",
            ]
        )
        for key in score_order:
            if scores.get(key) is None:
                continue
            reason = str(rationale.get(key) or candidate.get("basis_summary") or "未提供单维依据；需以接地材料复核。")
            lines.append(f"- **{score_labels[key]}：{scores[key]}/5**\n  - 依据：{reason}")
        if not scores:
            lines.append("- **未评分**：当前候选未附评分；不能据此自动排序。")
        lines.extend(["", "### 文献笔记锚点"])
        if not papers:
            lines.append("- 当前没有直接支撑论文；这是证据缺口，不是已证实结论。")
        for paper in papers[:5]:
            if not isinstance(paper, dict):
                continue
            lines.extend(
                [
                    f"- **{_shorten(str(paper.get('title') or paper.get('paper_id') or 'paper'), 150)}**",
                    f"  - 笔记：`{paper.get('source_file') or paper.get('path') or '未提供笔记路径'}`",
                    f"  - 使用内容：{_shorten(str(paper.get('claim_used') or paper.get('claim') or '未提供'), 360)}",
                ]
            )
        lines.append("")
    lines.extend(
        [
            "## 审计材料",
            "- `ideation/_candidate_directions.json`：机器可读候选、创新、候选假设、评分和实验信息。",
            "- `ideation/_pass2_grounding_review.json`：Pass2 接地检查、风险与上桌建议。",
            "- `ideation/_pass1_forward_candidates.json`：Pass1 原始发散候选，检查是否遗漏通道。",
            "- `ideation/t4_execution_events.jsonl`：公开执行轨迹，不含模型内部推理。",
            "- `ideation/bridge_coverage_review.json`：桥接候选可见性与暂缓原因（如存在）。",
            "",
        ]
    )
    return "\n".join(lines)


def recover_t4_gate1_candidate_pool(
    workspace_dir: Path,
    *,
    reason: str = "runtime_fallback_after_provider_failure",
    overwrite: bool = False,
) -> dict[str, object]:
    """Report an LLM reanalysis requirement without fabricating a candidate pool.

    Candidate hypotheses, mechanisms, evidence summaries, score rationales,
    and recommendation text are research content. A deterministic fallback
    cannot safely infer them from note-card fields. Older versions generated a
    plausible-looking pool after provider failures; that mixed languages,
    created incomplete H1-only candidates, and could present invented claims as
    user-selectable research directions. Recovery now preserves existing files
    and explicitly routes the workspace back to a normal T4 LLM pass.
    """

    ws = Path(workspace_dir)
    ok, err = validate_t4_gate1_ready(ws)
    if ok:
        return {"ok": True, "changed": False, "reason": "already_gate1_ready", "validation_error": None}
    return {
        "ok": False,
        "changed": False,
        "reason": "llm_reanalysis_required",
        "recovery_trigger": reason,
        "validation_error": err,
        "remediation": (
            "T4 Gate1 candidate artifacts require an LLM reanalysis pass. "
            "No deterministic candidate, hypothesis, score, or evidence text was written."
        ),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fallback_project_topic(ws: Path) -> str:
    try:
        project = yaml.safe_load((ws / "project.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        project = {}
    if not isinstance(project, dict):
        return "the current research problem"
    for key in ("research_direction", "title", "name", "project_id"):
        value = str(project.get(key) or "").strip()
        if value:
            return _shorten(value, 110)
    metadata = project.get("metadata") if isinstance(project.get("metadata"), dict) else {}
    for key in ("research_direction", "title", "topic", "manuscript_title"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return _shorten(value, 110)
    return "the current research problem"


def _build_fallback_gate1_candidates(
    cards: list[dict[str, object]],
    *,
    bridge_id: str,
    bridge_name: str,
    topic: str,
) -> list[dict[str, object]]:
    source_bridge = bridge_id or "cross_domain_context"
    source_bridge_name = bridge_name or "adjacent domain evidence"
    topic_phrase = topic or "the current research problem"
    blueprints = [
        {
            "id": "D1",
            "display_title": "证据边界与污染控制",
            "title": f"Evidence-boundary controls for {topic_phrase}",
            "idea_origin": "problem_reframing",
            "constraint_status": "mainline",
            "mechanism_family": "evidence-boundary diagnostics",
            "pitch": f"Separate the proposed effect in {topic_phrase} from measurement, context, retrieval, or protocol artifacts before making a final claim.",
            "mechanism": "Negative controls, artifact-only controls, and boundary-condition checks isolate whether an observed shift is caused by the proposed mechanism or by a confounding evidence artifact.",
            "prediction": "Claims whose apparent support is mostly artifact-driven will shrink, reverse, or become boundary-limited after the controls are applied.",
            "counterfactual": "If the proposed mechanism is real, removing artifact-only explanations should not fully eliminate the observed signal.",
            "fields": ("mechanism_claim", "boundary_conditions", "gaps"),
            "scores": {"novelty": 4, "feasibility": 4, "impact": 4, "evaluability": 5, "differentiation": 4, "cost": 4, "contribution_strength": 4},
            "innovation": {
                "summary": "把本应被默认接受的测量、上下文或协议前提转为可直接证伪的证据边界设计。",
                "type": "measurement",
                "novelty_delta": "从仅估计表观效应，改为同时判别真实机制与证据/协议伪影。",
                "non_incremental_reason": "贡献目标是改变何种证据可支持后续主张，而非为既有模型附加一个常规模块。",
            },
        },
        {
            "id": "D2",
            "display_title": "条件依赖的响应机制",
            "title": f"Condition-dependent response model for {topic_phrase}",
            "idea_origin": "design_rationale_derivation",
            "constraint_status": "mainline",
            "mechanism_family": "condition-dependent effects",
            "pitch": f"Model when and where the central mechanism in {topic_phrase} should hold, fail, or require a different design choice.",
            "mechanism": "The candidate conditions the effect on state, setting, subgroup, data regime, or intervention timing rather than assuming one stable average effect.",
            "prediction": "Condition-aware modeling will explain heterogeneity that a static baseline or one-size-fits-all design misses.",
            "counterfactual": "If the effect is truly stable across conditions, adding condition variables should not improve calibration, ranking, or explanatory fit.",
            "fields": ("design_rationale", "mechanism_claim", "boundary_conditions"),
            "scores": {"novelty": 4, "feasibility": 3, "impact": 4, "evaluability": 4, "differentiation": 4, "cost": 3, "contribution_strength": 4},
            "innovation": {
                "summary": "将平均效应假设转为状态、场景或子群条件下可被检验的响应机制。",
                "type": "mechanism",
                "novelty_delta": "不再只报告整体改进，而是要求解释效应何时成立、何时失效。",
                "non_incremental_reason": "设计的核心是可区分的机制边界，而非给静态模型增加任意特征。",
            },
        },
        {
            "id": "D3",
            "display_title": "跨域机制迁移验证",
            "title": f"Cross-domain mechanism transfer for {topic_phrase}",
            "idea_origin": "cross_domain_analogy",
            "constraint_status": "mainline",
            "mechanism_family": "cross-domain mechanism transfer",
            "pitch": f"Transfer a mechanism from an adjacent domain into {topic_phrase} and test whether it changes the design rationale rather than only the application setting.",
            "mechanism": "A mechanism observed in the adjacent domain becomes a moderator, diagnostic, or design principle for the target problem.",
            "prediction": "The transferred mechanism will expose a response pattern, failure mode, or design tradeoff not captured by target-domain baselines alone.",
            "counterfactual": "If the transfer is only superficial, adding the adjacent-domain mechanism should not change predictions, evaluation outcomes, or the nearest-prior-work distinction.",
            "fields": ("bridge_point", "mechanism_claim", "cross_paper_tension"),
            "cross_domain_sources": [source_bridge],
            "cross_domain_relation": "mechanism_bridge",
            "scores": {"novelty": 4, "feasibility": 4, "impact": 3, "evaluability": 5, "differentiation": 4, "cost": 4, "contribution_strength": 3},
            "innovation": {
                "summary": "把跨域知识作为可被反驳的机制迁移，而不是将相似术语或应用场景直接移植。",
                "type": "theory_transfer",
                "novelty_delta": "要求迁移后改变设计理由、可观测预测或失败模式，而不只是更换数据场景。",
                "non_incremental_reason": "若无法与目标域基线区分，该方向会被降级或合并，而不会作为装饰性类比。",
            },
        },
        {
            "id": "D4",
            "display_title": "桥接机制的可证伪迁移",
            "title": f"Bridge synthesis from {source_bridge_name} to {topic_phrase}",
            "idea_origin": "bridge_synthesis",
            "constraint_status": "bridge",
            "mechanism_family": "adjacent bridge synthesis",
            "pitch": f"Use the confirmed bridge domain as a Gate1-visible candidate for reframing {topic_phrase}.",
            "mechanism": "A bridge-domain mechanism becomes a candidate design principle, treatment moderator, evaluation lens, or boundary condition for the target contribution.",
            "prediction": "The bridge-specific lens will identify a testable distinction from both the nearest target-domain baseline and a generic adjacent-domain import.",
            "counterfactual": "If the bridge mechanism is irrelevant, adding it should not change the problem framing, design rationale, evaluation setup, or failure-mode interpretation.",
            "fields": ("bridge_point", "mechanism_claim", "design_rationale"),
            "cross_domain_sources": [source_bridge],
            "cross_domain_relation": "method_transfer",
            "scores": {"novelty": 5, "feasibility": 3, "impact": 4, "evaluability": 3, "differentiation": 5, "cost": 3, "contribution_strength": 3},
            "innovation": {
                "summary": "把已确认 bridge domain 的机制转化为目标问题中的可验证设计原则、调节变量或评估视角。",
                "type": "theory_transfer",
                "novelty_delta": "迁移必须经由具体的机制、边界和判别实验，而非把 bridge 名称作为结论。",
                "non_incremental_reason": "若 exact note section 不能支持可迁移机制，候选会被删除或并入主线，而不会以泛化类比保留。",
            },
        },
        {
            "id": "S1",
            "display_title": "反向操作与机制证伪",
            "title": f"Reverse-operation ablation for {topic_phrase}",
            "idea_origin": "reverse_operation",
            "constraint_status": "supplement",
            "mechanism_family": "ablation and falsification",
            "pitch": "For each selected mechanism, deliberately remove or invert the claimed active component to test whether the effect survives.",
            "mechanism": "Reverse-operation tests convert speculative mechanism claims into falsifiable ablations by disabling or inverting the proposed active component one at a time.",
            "prediction": "A valid mechanism should lose explanatory power or outcome separation when its corresponding component is removed or inverted.",
            "counterfactual": "If removal does not change estimates, the mechanism should be weakened, rejected, or reframed as a nonessential design choice.",
            "fields": ("gaps", "boundary_conditions", "design_rationale"),
            "scores": {"novelty": 3, "feasibility": 5, "impact": 3, "evaluability": 5, "differentiation": 3, "cost": 5, "contribution_strength": 2},
            "innovation": {
                "summary": "将已选主线的活性机制逐一移除或反转，形成比普通删模块更强的反事实检验。",
                "type": "evaluation",
                "novelty_delta": "检验机制是否必要，而不是只观察性能是否随模块数量变化。",
                "non_incremental_reason": "它是主线贡献的证伪支撑；没有主线机制时不应被包装为独立论文贡献。",
            },
        },
    ]
    out: list[dict[str, object]] = []
    for blueprint in blueprints:
        fields = tuple(str(item) for item in blueprint.pop("fields"))  # type: ignore[arg-type]
        papers = _fallback_supporting_papers(cards, fields=fields, limit=3)
        nearest = papers[0]["title"] if papers else "current synthesis / no single nearest work"
        candidate = {
            **blueprint,
            "generation_stage": "deterministic_recovery_pass1",
            "core_claim": blueprint["pitch"],
            "target_problem": f"Current evidence suggests {topic_phrase} needs a better-grounded contribution candidate before final hypotheses are written.",
            "basis_summary": _fallback_basis_summary(papers, fields),
            "supporting_papers": papers,
            "basis_sources": [
                {
                    "type": "paper_note_section",
                    "ref": paper.get("ref") or paper.get("title"),
                    "source_file": paper.get("source_file"),
                    "claim": paper.get("claim_used"),
                }
                for paper in papers
            ],
            # A recovery path cannot infer a project's dataset, baselines, or
            # metrics safely. T4's second half must derive them from selected
            # evidence and user constraints rather than emitting a plausible
            # but fabricated experimental protocol.
            "minimum_experiment": {
                "dataset": "待 T4 后半段从 project.yaml、可用材料与笔记卡中明确；当前恢复路径不推断。",
                "baseline": "待基于最近工作和项目约束确定；当前恢复路径不推断。",
                "metric": [],
                "expected_signal": "待选定方向并回查证据后明确为可证伪预测。",
                "evidence_status": "unknown",
                "source_refs": [],
            },
            "nearest_prior_work": {"work": nearest, "distance": "moderate" if papers else "not_computed"},
            "novelty_signal": "adjacent_zone" if papers else "not_computed",
            "generated_by": "deterministic_t4_gate1_recovery",
            "selection_warning": "Runtime-generated Gate1 candidate after provider failure; select only after T4后半段 re-checks exact note sections.",
            "candidate_hypotheses": [
                {
                    "id": f"{blueprint['id']}-H1",
                    "statement": blueprint["pitch"],
                    "mechanism": blueprint["mechanism"],
                    "observable_prediction": blueprint["prediction"],
                    "discriminating_test": blueprint["counterfactual"],
                }
            ],
        }
        out.append(candidate)
    return out


def _fallback_supporting_papers(
    cards: list[dict[str, object]],
    *,
    fields: tuple[str, ...],
    limit: int,
) -> list[dict[str, object]]:
    def score(card: dict[str, object]) -> tuple[int, float, int]:
        field_hits = sum(1 for field in fields if str(card.get(field) or "").strip())
        try:
            quality = float(card.get("citation_quality_score") or 0.0)
        except (TypeError, ValueError):
            quality = 0.0
        full_text = 1 if "FULL" in str(card.get("evidence_level") or "").upper() else 0
        return (field_hits + full_text, quality, len(str(card.get("title") or "")))

    ranked = sorted(cards, key=score, reverse=True)
    papers: list[dict[str, object]] = []
    for card in ranked:
        claim = ""
        for field in fields:
            claim = _shorten(str(card.get(field) or ""), 240)
            if claim:
                break
        if not claim:
            continue
        papers.append(
            {
                "title": str(card.get("title") or card.get("paper_id") or "paper").strip(),
                "paper_id": str(card.get("paper_id") or card.get("note_id") or "").strip(),
                "ref": str(card.get("citation_ref") or "").strip(),
                "source_file": str(card.get("source_file") or card.get("path") or "").strip(),
                "evidence_level": str(card.get("evidence_level") or "unknown"),
                "claim_used": claim,
            }
        )
        if len(papers) >= limit:
            break
    return papers


def _fallback_basis_summary(papers: list[dict[str, object]], fields: tuple[str, ...]) -> str:
    if not papers:
        return (
            "Recovered from the T4 compact context pack and synthesis because the LLM provider failed before Pass1 artifacts were written; "
            "this candidate must be re-grounded against individual note sections before final selection."
        )
    titles = "; ".join(str(paper.get("title") or "paper") for paper in papers[:3])
    return (
        f"Recovered from section-aware note-card cues ({', '.join(fields)}). "
        f"Candidate is grounded for Gate1 discussion in: {titles}. "
        "Final T4 must re-open exact note sections before turning this into hypotheses."
    )


def _fallback_pass2_review(candidate: dict[str, object]) -> dict[str, object]:
    idea_id = str(candidate.get("id") or "")
    status = str(candidate.get("constraint_status") or "")
    if status == "supplement":
        recommendation = "defer_recommended"
        warning = "Use as an ablation or falsification supplement, not as the standalone paper contribution."
        counterfactual = "independent"
        novelty = "marginal_zone"
    elif status == "bridge":
        recommendation = "revise_before_selection"
        warning = "Bridge candidate is visible because T1 confirmed bridge domains; select only if mechanism evidence is strong enough after note-section verification."
        counterfactual = "survives_weakened"
        novelty = "no_nearby_cluster"
    else:
        recommendation = "revise_before_selection"
        warning = "Runtime recovery candidate: suitable for Gate1 discussion but requires T4后半段 grounding before final hypotheses."
        counterfactual = "survives_weakened"
        novelty = "adjacent_zone"
    nearest = candidate.get("nearest_prior_work") if isinstance(candidate.get("nearest_prior_work"), dict) else {}
    return {
        "idea_id": idea_id,
        "screening_recommendation": recommendation,
        "visible_to_gate": True,
        "selection_warning": warning,
        "counterfactual_check": counterfactual,
        "counterfactual_note": str(candidate.get("counterfactual") or "Recovered candidate has an explicit falsification condition."),
        "nearest_prior_work": {
            "work": str(nearest.get("work") or "not computed during runtime recovery"),
            "distance": str(nearest.get("distance") or "not_computed"),
        },
        "novelty_signal": novelty,
    }


def _render_fallback_family_distribution(candidates: list[dict[str, object]]) -> str:
    origin_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for candidate in candidates:
        origin = str(candidate.get("idea_origin") or "unknown")
        family = str(candidate.get("mechanism_family") or "unknown")
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
        family_counts[family] = family_counts.get(family, 0) + 1
    lines = [
        "# T4 Gate1 候选谱系分布",
        "",
        "该文件来自 T4 Gate1 的确定性恢复，用于帮助人工比较候选，不是最终学术判断。",
        "",
        "## 来源分布",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(origin_counts.items()))
    lines.extend(["", "## 机制谱系分布"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(family_counts.items()))
    lines.extend(
        [
            "",
            "## 恢复边界",
            "- 候选池保留主线、跨域/桥接和补充通道，供 Gate1 选择、合并、重构或要求重新分析。",
            "- 恢复的 Candidate 在进入 T4.5 前必须定向复核相应 paper note section；正式 hypotheses 与 experiment plan 只在 audit 通过后生成。",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_fallback_gate1_selection_brief(
    candidates: list[dict[str, object]],
    reviews: list[dict[str, object]],
    *,
    reason: str,
) -> str:
    review_by_id = {str(item.get("idea_id") or ""): item for item in reviews}
    origin_counts: dict[str, int] = {}
    for candidate in candidates:
        origin = str(candidate.get("idea_origin") or "unknown")
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    ids = [str(candidate.get("id") or "") for candidate in candidates]
    lines = [
        "# T4 Gate1 选择简报",
        "",
        "- 当前候选池已就绪，可直接选择、合并、重构或要求重新分析。",
        "- 本简报不替代证据核验：最终假设前，T4 后半段必须回查对应文献笔记的具体 section。",
        f"- 运行记录：{reason}",
        "",
        "## 候选池",
    ]
    for candidate in candidates:
        idea_id = str(candidate.get("id") or "")
        review = review_by_id.get(idea_id, {})
        display = _candidate_display_text(candidate)
        lines.append(
            f"- {idea_id}：{display['title']}｜来源={candidate.get('idea_origin')}｜"
            f"建议={review.get('screening_recommendation') or '需复核'}｜风险={review.get('selection_warning') or candidate.get('selection_warning') or '选择后回查证据'}"
        )
    lines.extend(
        [
            "",
            "## 接地复核提醒",
        ]
    )
    for review in reviews:
        lines.append(f"- {review.get('idea_id')}：{review.get('selection_warning')}")
    merge_a = ids[0] if ids else "D1"
    merge_b = ids[1] if len(ids) > 1 else "D2"
    merge_c = ids[-1] if ids else "S1"
    lines.extend(
        [
            "",
            "## 合并建议",
            f"- 合并 {merge_a}+{merge_b}：保留更强的主机制，把另一个候选作为状态扩展或验证通道。",
            f"- 合并 {merge_a}+{merge_c}：把补充候选作为所选主方向的证伪/消融模块。",
            "",
            "## 集中度提示",
            "候选池覆盖主线、跨域/桥接和补充通道。在 T4 后半段回查具体笔记前，不应把它视为已经收敛到某一篇论文或某一类机制。",
            "",
            "## Origin 分布",
            "; ".join(f"{key}: {value}" for key, value in sorted(origin_counts.items())),
            "",
            "## Novelty-Utility 谱系排布",
            "D1/D2：实用性高、创新风险适中；D3/D4：创新空间较高但接地风险更高；S1：可行性高，但更适合作为消融而非独立主贡献。",
            "",
            "## 审计材料",
            "- `ideation/_candidate_directions.json`",
            "- `ideation/_pass2_grounding_review.json`",
            "- `ideation/_pass1_forward_candidates.json`",
            "- `ideation/bridge_coverage_review.json`（如配置了 bridge domain）",
            "",
        ]
    )
    return "\n".join(lines)


def _render_fallback_bridge_coverage_review(
    bridge_domains: list[dict],
    candidates: list[dict[str, object]],
) -> dict[str, object]:
    candidate_by_bridge: dict[str, list[str]] = {}
    for candidate in candidates:
        idea_id = str(candidate.get("id") or "").strip()
        for source in _cross_domain_sources(candidate):
            candidate_by_bridge.setdefault(source, []).append(idea_id)
    reviews: list[dict[str, object]] = []
    warnings: list[str] = []
    for bridge in bridge_domains:
        bridge_id = str(bridge.get("bridge_id") or "").strip()
        priority = str(bridge.get("priority") or "should_explore").strip()
        if priority not in {"must_explore", "should_explore"}:
            priority = "should_explore"
        candidate_ids = candidate_by_bridge.get(bridge_id, [])
        visible = bool(candidate_ids)
        if priority == "must_explore" and not visible:
            warnings.append(f"{bridge_id}: must_explore bridge not covered by deterministic recovery candidate")
        status = "deferred" if visible else ("no_candidate_available" if priority == "must_explore" else "deferred")
        reviews.append(
            {
                "bridge_id": bridge_id,
                "priority": priority,
                "candidate_ids": candidate_ids,
                "visible_to_gate": visible,
                "forced_surfaced": False,
                "selected_into_hypotheses": False,
                "decision_summary": (
                    f"Recovered Gate1 candidate(s) {candidate_ids} cover this bridge for user review."
                    if visible
                    else "No deterministic candidate was generated for this bridge; it remains a deferred context lane."
                ),
                "escape_hatch": {
                    "status": status,
                    "reason": (
                        "Bridge was surfaced through deterministic recovery; final selection requires exact note-section verification."
                        if visible
                        else "Context pack did not provide enough section-specific mechanism evidence for a separate bridge candidate."
                    ),
                    "falsification_or_kill_criteria": "Drop or merge this bridge if exact note sections cannot support a testable moderator, mechanism, or evaluation transfer.",
                    "can_revisit_if": "Revisit after T2/T3 adds stronger bridge-specific paper notes or the user explicitly selects this bridge framing at Gate1.",
                },
            }
        )
    return {
        "version": "1.0",
        "semantics": "bridge_candidate_visibility_and_escape_hatch_review",
        "source_bridge_plan": "literature/bridge_domain_plan.json",
        "bridge_reviews": reviews,
        "warnings": warnings,
    }


def _append_t4_progress_recovery_note(ws: Path, *, reason: str, candidate_count: int) -> None:
    path = ws / T4_PROGRESS_MD
    existing = read_text_file(path, default="")
    lines = existing.rstrip().splitlines() if existing.strip() else ["# T4 Progress", ""]
    lines.extend(
        [
            f"- [recovered] Runtime generated {candidate_count} Gate1 candidates from section-aware note-card context after interruption.",
            f"- [reason] {reason}",
            "- [next] Human Gate1 should select, merge, reframe, or request reanalysis; final hypotheses still require T4后半段 grounding.",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
    """Project authored implications without inferring a project domain.

    New Gate1 candidates must provide this field themselves. The remaining
    generic fallback exists only to render legacy artifacts without injecting a
    domain-specific claim into an unrelated project.
    """

    explicit = (
        str(candidate.get("practical_implication") or "").strip()
        or str(candidate.get("managerial_implication") or "").strip()
        or str(candidate.get("business_implication") or "").strip()
    )
    if explicit:
        return explicit
    contribution = str(candidate.get("contribution_character") or "").strip()
    if contribution:
        return contribution
    return (
        "The candidate did not provide a practical implication; it requires LLM reanalysis "
        "before a human decision should rely on this field."
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
    if not isinstance(pass1_candidates, list) or not pass1_candidates:
        return False, "_pass1_forward_candidates.json 至少需要一个 Pass1 原始候选"

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

    ok, err = _validate_gate1_candidate_cards(candidate_cards_path)
    if not ok:
        return False, err

    brief_text = read_text_file(gate_brief_path)
    if not _t4_substantive_text(brief_text):
        return False, "_gate1_selection_brief.md 为空或仍是占位内容"
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


def _validate_gate1_candidate_cards(path: Path) -> tuple[bool, str | None]:
    """Validate the reader-facing Gate1 card deck.

    `_candidate_directions.json` remains the complete active-Population record.
    This Markdown deck covers only the visible Portfolio. Exact LLM Card
    coverage and semantic completeness are verified separately at the
    human-facing Gate boundary, so exploratory non-Portfolio Candidates do not
    need a fake final-card narrative merely to satisfy this compatibility check.
    """

    text = read_text_file(path)
    stripped = text.strip()
    if not _t4_substantive_text(stripped):
        return False, "ideation/_gate1_candidate_cards.md 为空或仍是占位内容"
    if re.search(r"^\s*[\{\[]", stripped):
        return False, "ideation/_gate1_candidate_cards.md 不能是 raw JSON，必须是给用户阅读的 Markdown"
    if T4_GATE1_CARD_SCHEMA_MARKER not in text:
        return False, (
            "ideation/_gate1_candidate_cards.md 不是当前 native Gate1 card schema v3；"
            "请由 T4 重新生成当前 Population 的卡片投影，不会在 resume 时静默套用旧版内容。"
        )
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
    if not _t4_substantive_text(text):
        return False, "selected_idea_brief.md 为空或仍是占位内容"
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
        review_status = str(item.get("review_status") or "llm_reviewed").strip()
        if review_status == "unreviewed":
            # This is a recoverable coverage gap, not a scientific verdict.
            # The projection must identify it explicitly, but Gate1 may still
            # compare the valid non-bridge Population and request a new Route.
            diagnostic = item.get("review_diagnostic") if isinstance(item.get("review_diagnostic"), dict) else {}
            if not str(diagnostic.get("code") or "").strip() or not str(diagnostic.get("message") or "").strip():
                return False, f"bridge {bridge_id} 的 unreviewed 状态缺少可追溯诊断码"
            warnings = " ".join(str(entry) for entry in review.get("warnings") or [])
            if bridge_id not in warnings:
                return False, f"bridge {bridge_id} 未审阅但 warnings 未显式记录。"
            continue
        if review_status != "llm_reviewed":
            return False, f"bridge {bridge_id} 的 review_status 无效: {review_status or '空'}"
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
        # Status was added after the first retained Bridge artifact.  An old
        # complete entry is still an LLM review; preserve its prose and only
        # attach the structural discriminator needed by the current schema.
        if all(isinstance(item, dict) and str(item.get("review_status") or "").strip() for item in review["bridge_reviews"]):
            return review
        normalized = dict(review)
        normalized["bridge_reviews"] = [
            ({**item, "review_status": str(item.get("review_status") or "llm_reviewed")} if isinstance(item, dict) else item)
            for item in review["bridge_reviews"]
        ]
        return normalized

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
                "review_status": "llm_reviewed",
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
    if not _t4_substantive_text(counterfactual_note):
        return False, f"_pass2_grounding_review.json review {idea_id} counterfactual_note 为空或仍是占位内容"
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
    if not _t4_substantive_text(counterfactual_note):
        return False, f"idea_scorecard.yaml idea {idea_id} counterfactual_note 为空或仍是占位内容"
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
