from __future__ import annotations

"""Seed-outline normalization tools.

Users often start with a substantial Markdown outline rather than a list of
papers.  This module turns such outlines into structured, downstream-readable
seed artifacts without pretending that literature directions are verified
citations.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..literature_identity import is_placeholder_text
from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy

SEED_OUTLINE_MANAGED_START = "<!-- seed_outline_profile: derived; do not remove unless regenerating seed outline -->"
SEED_OUTLINE_MANAGED_END = "<!-- /seed_outline_profile -->"


class NormalizeSeedOutlineParams(BaseModel):
    path: str = Field(
        default="user_seeds/seed_outline.md",
        description="相对 workspace 的 Markdown seed outline 路径；可传中文文件名。",
    )
    output_profile_path: str = Field(
        default="user_seeds/seed_outline_profile.json",
        description="结构化 seed outline profile 输出路径。",
    )
    write_derived_seed_files: bool = Field(
        default=True,
        description="是否同步写入/追加 seed_ideas.md、seed_constraints.md、seed_external_resources.jsonl。",
    )
    seed_ideas_path: str = Field(default="user_seeds/seed_ideas.md")
    seed_constraints_path: str = Field(default="user_seeds/seed_constraints.md")
    seed_external_resources_path: str = Field(default="user_seeds/seed_external_resources.jsonl")


class NormalizeSeedOutlineTool(Tool):
    name = "normalize_seed_outline"
    description = (
        "把用户提供的 Markdown 种子提纲归一化为 user_seeds/seed_outline_profile.json，"
        "并可派生 seed_ideas/constraints/external_resources。代表性文献方向只作为 query seed，"
        "不会伪造成 seed_papers。"
    )
    parameters_schema = NormalizeSeedOutlineParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = NormalizeSeedOutlineParams(**kwargs)
        try:
            outline_path = self.policy.resolve_read(params.path)
            if not outline_path.exists():
                return ToolResult(ok=False, content=f"Seed outline not found: {params.path}", error="not_found")
            text = outline_path.read_text(encoding="utf-8", errors="replace")
            profile = build_seed_outline_profile(text, source_path=params.path)
            output_path = self.policy.resolve_write(params.output_profile_path)
            output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            derived: dict[str, Any] = {}
            if params.write_derived_seed_files:
                derived = _write_derived_seed_files(
                    self.policy,
                    profile,
                    seed_ideas_path=params.seed_ideas_path,
                    seed_constraints_path=params.seed_constraints_path,
                    seed_external_resources_path=params.seed_external_resources_path,
                )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"File is not UTF-8 text: {params.path}", error="not_text")
        except OSError as exc:
            return ToolResult(ok=False, content=f"Failed to normalize seed outline: {exc}", error="io_error")

        directions = profile.get("representative_literature_directions") or []
        content = (
            f"Normalized seed outline -> {params.output_profile_path}. "
            f"manuscript_type={profile.get('manuscript_type')}; "
            f"directions={len(directions)}; keywords={len(profile.get('keywords', []))}; "
            "no seed_papers were created from non-citation directions."
        )
        return ToolResult(
            ok=True,
            content=content,
            data={
                "profile_path": params.output_profile_path,
                "profile": profile,
                "derived": derived,
                "created_seed_papers": 0,
            },
        )


@dataclass(frozen=True)
class _Section:
    title: str
    body: str


def looks_like_seed_outline(text: str) -> bool:
    """Heuristic used by inspect_user_seeds to flag outline-like Markdown."""

    lowered = text.casefold()
    signals = [
        "种子提纲",
        "seed outline",
        "代表性文献方向",
        "框架总览",
        "taxonomy",
        "综述",
        "survey",
    ]
    return sum(1 for signal in signals if signal.casefold() in lowered) >= 2


def build_seed_outline_profile(text: str, *, source_path: str) -> dict[str, Any]:
    """Build a deterministic profile from a Markdown seed outline."""

    title = _first_heading(text) or _fallback_title(source_path)
    subtitle = _first_subtitle(text)
    sections = _parse_sections(text)
    directions = _representative_directions(sections)
    keywords = _keywords(text)
    framework = _framework(text)
    external_resources = _external_resources(text)
    query_profile = _query_profile(title, subtitle, keywords, directions, framework)

    manuscript_type = _detect_manuscript_type(text)
    profile = {
        "semantics": "user_seed_outline_profile",
        "version": "1.0",
        "source_path": source_path,
        "source_sha256": _text_sha256(text),
        "title": title,
        "subtitle": subtitle,
        "language": _detect_language(text),
        "manuscript_type": manuscript_type,
        "project_type": manuscript_type,
        "writing_intent": _writing_intent(title, subtitle, manuscript_type),
        "framework": framework,
        "sections": [
            {
                "section_title": section.title,
                "core_claim": _core_claim(section.body),
                "representative_literature_directions": [
                    item["direction"]
                    for item in directions
                    if item.get("section_title") == section.title
                ],
            }
            for section in sections
            if _is_numbered_outline_section(section.title)
        ],
        "representative_literature_directions": directions,
        "keywords": keywords,
        "query_profile": query_profile,
        "external_resources": external_resources,
        "literature_seed_policy": {
            "directions_are_verified_citations": False,
            "use_directions_as": "query_terms_and_taxonomy_priors_only",
            "do_not_write_seed_papers_from_directions": True,
            "note": (
                "Representative literature directions in a seed outline are not bibliographic records. "
                "T2 must retrieve and verify concrete papers before they can be cited."
            ),
        },
    }
    return profile


def _text_sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _parse_sections(text: str) -> list[_Section]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    sections: list[_Section] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append(_Section(title=match.group(1).strip(), body=text[start:end].strip()))
    return sections


def _first_heading(text: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _first_subtitle(text: str) -> str:
    match = re.search(r"(?m)^#{2,6}\s*[—-]+\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _fallback_title(source_path: str) -> str:
    return Path(source_path).stem.replace("_", " ").strip() or "Seed Outline"


def _detect_language(text: str) -> str:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    if chinese_chars and ascii_letters:
        return "zh-en"
    if chinese_chars:
        return "zh"
    return "en"


def _detect_manuscript_type(text: str) -> str:
    lowered = text.casefold()
    if any(token in lowered for token in ("综述", "survey", "literature review", "taxonomy-driven")):
        return "survey"
    return "research_article"


def _writing_intent(title: str, subtitle: str, manuscript_type: str) -> dict[str, Any]:
    return {
        "manuscript_type": manuscript_type,
        "preferred_title": title,
        "subtitle": subtitle,
        "primary_output": "taxonomy-driven professional survey" if manuscript_type == "survey" else "research article",
        "must_not": [
            "treat representative literature directions as verified citations",
            "convert synthesis.md directly into TeX without section planning",
        ],
    }


def _framework(text: str) -> dict[str, Any]:
    risk_axis_raw = _capture_after_label(text, "横轴")
    perspective_axis_raw = _capture_after_label(text, "纵轴")
    risk_chain = _axis_terms(risk_axis_raw or text, ["场景", "数据", "模型", "决策", "反馈"])
    perspectives = _axis_terms(perspective_axis_raw or text, ["理论", "技术", "管理", "治理"])
    taxonomy_hint = " × ".join(part for part in [" / ".join(perspectives), " -> ".join(risk_chain)] if part)
    return {
        "risk_generation_chain": risk_chain,
        "perspectives": perspectives,
        "axis_descriptions": {
            "risk_generation_chain": risk_axis_raw,
            "perspectives": perspective_axis_raw,
        },
        "taxonomy_hint": taxonomy_hint,
        "main_argument": _capture_after_label(text, "总论点"),
    }


def _capture_after_label(text: str, label: str) -> str:
    pattern = rf"\*\*[^*\n]*{re.escape(label)}[^*\n]*\*\*[：:]\s*(.+)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _ordered_present_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def _axis_terms(raw: str, canonical_terms: list[str]) -> list[str]:
    """Extract only canonical framework-axis terms from explanatory text."""

    ordered = _ordered_present_terms(raw, canonical_terms)
    if len(ordered) >= 2:
        return ordered
    return [term for term in _split_terms(raw) if term in canonical_terms]


def _representative_directions(sections: list[_Section]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in sections:
        for raw in _representative_blocks(section.body):
            for direction in _split_terms(raw):
                key = direction.casefold()
                if not direction or key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "direction": direction,
                        "section_title": section.title,
                        "use_as": "query_direction_not_verified_citation",
                    }
                )
    return records


def _representative_blocks(body: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"\*\*代表性文献方向\*\*[：:]\s*(.+)", body):
        blocks.append(match.group(1).strip())
    return blocks


def _core_claim(body: str) -> str:
    match = re.search(r"\*\*核心论点\*\*[：:]\s*(.+)", body)
    return match.group(1).strip() if match else ""


def _is_numbered_outline_section(title: str) -> bool:
    return bool(re.match(r"^\d+[\.、]\s*", title.strip()))


def _keywords(text: str) -> list[str]:
    values: list[str] = []
    for label in ("English", "中文"):
        match = re.search(rf"\*\*{label}\*\*[：:]\s*(.+)", text, flags=re.IGNORECASE)
        if match:
            values.extend(_split_terms(match.group(1)))
    if not values:
        values.extend(_ordered_present_terms(text, ["algorithmic risk", "AI governance", "管理决策", "算法治理"]))
    return _dedupe(values)


def _query_profile(
    title: str,
    subtitle: str,
    keywords: list[str],
    directions: list[dict[str, Any]],
    framework: dict[str, Any],
) -> dict[str, Any]:
    direction_terms = [str(item.get("direction") or "") for item in directions]
    base_terms = _dedupe([*keywords, *direction_terms])
    risk_chain = framework.get("risk_generation_chain") if isinstance(framework, dict) else []
    perspectives = framework.get("perspectives") if isinstance(framework, dict) else []
    include_keywords = _dedupe(
        [
            *base_terms[:40],
            "algorithmic risk",
            "managerial decision-making",
            "AI governance",
            "algorithmic accountability",
            "human-AI decision-making",
            "model risk management",
            "智能算法风险",
            "管理决策",
            "算法治理",
            "人机协同决策",
        ]
    )
    query_variants = _dedupe(
        [
            f'"{title}"',
            "algorithmic risk managerial decision-making survey",
            "AI governance algorithmic accountability management decisions",
            "human-AI decision-making algorithm aversion automation bias",
            "model risk management AI governance lifecycle audit",
            "uncertainty quantification explainable AI fairness decision making",
            "智能算法风险 管理决策 综述",
            "算法治理 管理决策 风险",
            "人机协同决策 算法厌恶 自动化偏差",
            "模型风险管理 人工智能治理 生命周期",
            *base_terms[:24],
        ]
    )
    return {
        "domain": title,
        "search_languages": ["zh", "en"],
        "include_keywords": include_keywords,
        "exclude_keywords": ["6G", "wireless network", "blockchain cryptocurrency"],
        "query_variants": query_variants,
        "related_concepts": base_terms[:60],
        "risk_chain_terms": risk_chain or [],
        "perspective_terms": perspectives or [],
        "venue_terms": [
            "Management Science",
            "MIS Quarterly",
            "Information Systems Research",
            "Organization Science",
            "Journal of Management Information Systems",
            "人工智能",
            "管理科学学报",
            "系统工程理论与实践",
        ],
        "ambiguity_risks": [
            "algorithm risk may retrieve financial trading or network-security-only papers",
            "governance may retrieve policy commentary without management-decision mechanisms",
            "Chinese management literature may be under-covered by OpenAlex/Crossref/arXiv APIs",
        ],
    }


def _external_resources(text: str) -> list[dict[str, str]]:
    known = [
        ("EU AI Act", "regulation", "European Union AI risk classification and obligations"),
        ("NIST AI RMF", "governance_framework", "NIST AI Risk Management Framework"),
        ("ISO/IEC 42001", "standard", "AI management system standard"),
        ("ISO/IEC 23894", "standard", "AI risk management standard"),
        ("生成式人工智能服务管理暂行办法", "regulation", "China generative AI service governance"),
        ("算法推荐管理规定", "regulation", "China algorithm recommendation governance"),
        ("SR 11-7", "model_risk_management", "supervisory guidance on model risk management"),
    ]
    resources: list[dict[str, str]] = []
    lowered = text.casefold()
    for name, resource_type, notes in known:
        if name.casefold() in lowered:
            resources.append(
                {
                    "type": resource_type,
                    "name": name,
                    "source": "official_source_lookup_required",
                    "notes": notes + "; from seed outline; verify official text before citing",
                }
            )
    return resources


def _split_terms(raw: str) -> list[str]:
    if not raw:
        return []
    cleaned = raw
    cleaned = cleaned.replace("；", ";").replace("，", ",").replace("、", ",")
    cleaned = cleaned.replace(" / ", ",").replace("/", ",")
    cleaned = cleaned.replace(" → ", ",").replace("->", ",").replace("×", ",")
    parts = re.split(r"[;,\n]+|·", cleaned)
    out: list[str] = []
    for part in parts:
        item = re.sub(r"^[\s\-*]+", "", part).strip()
        item = re.sub(r"[。；;,.，、]+$", "", item).strip()
        if item:
            out.append(item)
    return _dedupe(out)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = " ".join(str(item or "").split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _write_derived_seed_files(
    policy: WorkspaceAccessPolicy,
    profile: dict[str, Any],
    *,
    seed_ideas_path: str,
    seed_constraints_path: str,
    seed_external_resources_path: str,
) -> dict[str, Any]:
    ideas_path = policy.resolve_write(seed_ideas_path)
    constraints_path = policy.resolve_write(seed_constraints_path)
    resources_path = policy.resolve_write(seed_external_resources_path)

    ideas_written = _merge_markdown_seed_file(ideas_path, _seed_ideas_markdown(profile), marker="seed_outline_profile")
    constraints_written = _merge_markdown_seed_file(
        constraints_path,
        _seed_constraints_markdown(profile),
        marker="seed_outline_profile",
    )
    resources_written = _merge_external_resources(resources_path, profile.get("external_resources") or [])
    return {
        "seed_ideas_path": seed_ideas_path,
        "seed_ideas_written": ideas_written,
        "seed_constraints_path": seed_constraints_path,
        "seed_constraints_written": constraints_written,
        "seed_external_resources_path": seed_external_resources_path,
        "external_resources_added": resources_written,
    }


def _merge_markdown_seed_file(path: Path, addition: str, *, marker: str) -> bool:
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    block = _managed_markdown_block(addition)
    if marker in existing:
        path.write_text(_replace_managed_markdown_block(existing, block, marker=marker), encoding="utf-8")
        return True
    if not existing.strip() or is_placeholder_text(existing):
        path.write_text(block + "\n", encoding="utf-8")
        return True
    path.write_text(existing.rstrip() + "\n\n---\n\n" + block + "\n", encoding="utf-8")
    return True


def _managed_markdown_block(addition: str) -> str:
    block = str(addition or "").strip()
    if SEED_OUTLINE_MANAGED_START not in block:
        block = SEED_OUTLINE_MANAGED_START + "\n" + block
    if SEED_OUTLINE_MANAGED_END not in block:
        block = block.rstrip() + "\n" + SEED_OUTLINE_MANAGED_END
    return block


def _replace_managed_markdown_block(existing: str, block: str, *, marker: str) -> str:
    start = existing.find(SEED_OUTLINE_MANAGED_START)
    if start < 0:
        start = existing.find(marker)
    if start < 0:
        return existing.rstrip() + "\n\n---\n\n" + block + "\n"

    end_marker_start = existing.find(SEED_OUTLINE_MANAGED_END, start)
    if end_marker_start >= 0:
        end = end_marker_start + len(SEED_OUTLINE_MANAGED_END)
    else:
        # Legacy generated blocks had no end marker. Treat everything from the
        # marker to EOF as the managed block, preserving any user content before it.
        end = len(existing)

    prefix = existing[:start].rstrip()
    suffix = existing[end:].lstrip()
    parts = [part for part in (prefix, block, suffix.rstrip()) if part]
    return "\n\n---\n\n".join(parts).rstrip() + "\n"


def _seed_ideas_markdown(profile: dict[str, Any]) -> str:
    directions = profile.get("representative_literature_directions") or []
    query_profile = profile.get("query_profile") or {}
    lines = [
        SEED_OUTLINE_MANAGED_START,
        "# Seed Ideas Derived From Seed Outline",
        "",
        f"- Preferred title: {profile.get('title')}",
        f"- Manuscript type: {profile.get('manuscript_type')}",
        f"- Writing intent: {(profile.get('writing_intent') or {}).get('primary_output', '')}",
        f"- Framework: {(profile.get('framework') or {}).get('taxonomy_hint', '')}",
        "",
        "## Representative Literature Directions",
        "",
    ]
    for item in directions[:40]:
        lines.append(
            f"- {item.get('direction')} "
            f"(source section: {item.get('section_title')}; use: query_direction_not_verified_citation)"
        )
    lines.extend(["", "## Query Variants", ""])
    for query in (query_profile.get("query_variants") or [])[:30]:
        lines.append(f"- {query}")
    return "\n".join(lines)


def _seed_constraints_markdown(profile: dict[str, Any]) -> str:
    policy = profile.get("literature_seed_policy") or {}
    query_profile = profile.get("query_profile") or {}
    lines = [
        SEED_OUTLINE_MANAGED_START,
        "# Seed Constraints Derived From Seed Outline",
        "",
        f"- manuscript_type: {profile.get('manuscript_type')}",
        f"- language: {profile.get('language')}",
        f"- search_languages: {', '.join(query_profile.get('search_languages') or [])}",
        "- representative_literature_directions_are_not_citations: "
        + str(not policy.get("directions_are_verified_citations", True)).lower(),
        "- T2 must retrieve concrete papers via OpenAlex/Crossref/arXiv/Semantic Scholar/etc. before citation use.",
        "- Chinese management/governance literature may need manual resource or web/official-source supplementation when APIs under-cover it.",
        "- Governance frameworks and regulations are external resources; verify official text before citing in writing.",
    ]
    return "\n".join(lines)


def _merge_external_resources(path: Path, resources: list[dict[str, Any]]) -> int:
    existing: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                existing.append(item)
    seen = {
        (
            str(item.get("name") or "").strip().casefold(),
            str(item.get("source") or "").strip().casefold(),
        )
        for item in existing
    }
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            key = (
                str(resource.get("name") or "").strip().casefold(),
                str(resource.get("source") or "").strip().casefold(),
            )
            if key in seen or not key[0]:
                continue
            handle.write(json.dumps(resource, ensure_ascii=False) + "\n")
            seen.add(key)
            added += 1
    return added
