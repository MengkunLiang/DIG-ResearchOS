from __future__ import annotations

"""Deterministic literature-language and venue-quality policy helpers."""

from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any

import yaml


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

DEFAULT_CHINESE_AUTHORITY_KEYWORDS = (
    "WJCI",
    "SCI",
    "SSCI",
    "EI",
    "北大核心",
    "CSSCI",
    "CSCD",
    "AMI",
    "AMI顶级",
    "AMI权威",
    "AMI核心",
    "管理世界",
    "管理科学学报",
    "系统工程理论与实践",
    "科研管理",
    "中国管理科学",
    "南开管理评论",
    "管理评论",
    "管理工程学报",
    "情报学报",
    "情报理论与实践",
)


@dataclass(frozen=True)
class LiteratureQualityPolicy:
    enabled: bool = True
    manuscript_language: str = "auto"
    include_chinese_literature: str = "auto"
    english_manuscript_policy: str = "exclude_non_seed_chinese"
    chinese_literature_policy: str = "authoritative_or_seed"
    authoritative_chinese_keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CHINESE_AUTHORITY_KEYWORDS)
    allow_user_seed_override: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "manuscript_language": self.manuscript_language,
            "include_chinese_literature": self.include_chinese_literature,
            "english_manuscript_policy": self.english_manuscript_policy,
            "chinese_literature_policy": self.chinese_literature_policy,
            "authoritative_chinese_keywords": list(self.authoritative_chinese_keywords),
            "allow_user_seed_override": self.allow_user_seed_override,
        }


def detect_record_language(record: dict[str, Any]) -> str:
    """Return ``zh`` when title/abstract/venue visibly contains Chinese."""

    text = " ".join(
        str(record.get(key) or "")
        for key in ("title", "abstract", "venue", "source", "journal", "container_title")
    )
    return "zh" if _CJK_RE.search(text) else "en_or_unknown"


def infer_manuscript_language(workspace_dir: Path | str | None, configured: str = "auto") -> str:
    configured = str(configured or "auto").strip().lower().replace("-", "_")
    if configured in {"en", "english", "英文"}:
        return "en"
    if configured in {"zh", "chinese", "中文"}:
        return "zh"
    if configured in {"mixed", "bilingual", "zh_en", "中英", "双语"}:
        return "mixed"
    if workspace_dir is None:
        return "en"
    workspace = Path(workspace_dir)
    texts: list[str] = []
    project_path = workspace / "project.yaml"
    if project_path.exists():
        try:
            project = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        except Exception:
            project = {}
        if isinstance(project, dict):
            for key in ("language", "manuscript_language", "writing_language", "target_language"):
                value = str(project.get(key) or "").strip().lower()
                if value in {"en", "english", "英文"}:
                    return "en"
                if value in {"zh", "chinese", "中文"}:
                    return "zh"
                if value in {"mixed", "bilingual", "zh-en", "zh_en", "双语"}:
                    return "mixed"
            texts.extend(str(project.get(key) or "") for key in ("title", "research_direction", "target_venue"))
            texts.extend(str(item) for item in project.get("keywords") or [] if item is not None)
    profile_path = workspace / "user_seeds" / "seed_outline_profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            profile = {}
        if isinstance(profile, dict):
            lang = str(profile.get("language") or "").strip().lower()
            if lang in {"zh", "zh-en", "zh_en", "mixed", "bilingual"}:
                return "mixed" if lang != "zh" else "zh"
            if lang in {"en", "english"}:
                return "en"
            texts.append(str(profile.get("title") or ""))
            query_profile = profile.get("query_profile")
            if isinstance(query_profile, dict):
                languages = {str(item).lower() for item in query_profile.get("search_languages") or []}
                if "zh" in languages and "en" in languages:
                    return "mixed"
                if "zh" in languages:
                    return "zh"
    joined = " ".join(texts)
    return "mixed" if _CJK_RE.search(joined) else "en"


def include_chinese_literature(
    workspace_dir: Path | str | None,
    policy: LiteratureQualityPolicy,
    *,
    manuscript_language: str | None = None,
) -> bool:
    raw = str(policy.include_chinese_literature or "auto").strip().lower().replace("-", "_")
    if raw in {"true", "yes", "1", "on", "include", "enabled"}:
        return True
    if raw in {"false", "no", "0", "off", "exclude", "disabled"}:
        return False
    language = manuscript_language or infer_manuscript_language(workspace_dir, policy.manuscript_language)
    return language in {"zh", "mixed"}


def is_user_seed_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").strip().lower()
    return bool(
        source == "user_seed"
        or record.get("seed_priority")
        or record.get("has_seed_pdf")
        or str(record.get("seed_pdf_path") or "").strip()
        or str(record.get("verification_source") or "").strip() == "user_seeds/pdfs"
    )


def is_authoritative_chinese_record(
    record: dict[str, Any],
    policy: LiteratureQualityPolicy,
) -> tuple[bool, list[str]]:
    """Conservative authority check for Chinese literature candidates.

    This only accepts explicit source labels/venue names/seed annotations. It
    does not infer journal authority from fuzzy venue similarity.
    """

    text = " ".join(
        str(record.get(key) or "")
        for key in (
            "venue",
            "source",
            "source_type",
            "journal",
            "container_title",
            "authority_label",
            "venue_authority",
            "why_relevant",
        )
    )
    for key in ("externalIds", "provenance", "domain_profile_used"):
        value = record.get(key)
        if isinstance(value, dict):
            text += " " + " ".join(str(v) for v in value.values() if isinstance(v, str))
    normalized = text.casefold()
    matched: list[str] = []
    for keyword in policy.authoritative_chinese_keywords:
        kw = str(keyword or "").strip()
        if kw and kw.casefold() in normalized:
            matched.append(kw)
    return bool(matched), matched


def annotate_literature_quality(
    record: dict[str, Any],
    policy: LiteratureQualityPolicy,
    *,
    workspace_dir: Path | str | None = None,
    manuscript_language: str | None = None,
) -> dict[str, Any]:
    item = dict(record)
    if not policy.enabled:
        item.setdefault("literature_quality_policy", {"enabled": False})
        return item
    language = manuscript_language or infer_manuscript_language(workspace_dir, policy.manuscript_language)
    record_language = detect_record_language(item)
    include_zh = include_chinese_literature(workspace_dir, policy, manuscript_language=language)
    seed = is_user_seed_record(item)
    authoritative, authority_matches = is_authoritative_chinese_record(item, policy)
    keep = True
    reason = "accepted"
    citation_allowed = True

    if record_language == "zh" and language == "en" and not include_zh:
        citation_allowed = False
        if policy.english_manuscript_policy == "exclude_non_seed_chinese" and not (seed and policy.allow_user_seed_override):
            keep = False
            reason = "english_manuscript_excludes_chinese_literature"
        else:
            reason = "seed_chinese_visible_but_not_for_english_citation" if seed else "chinese_literature_not_for_english_citation"
    elif record_language == "zh" and include_zh and policy.chinese_literature_policy == "authoritative_or_seed":
        if not authoritative and not (seed and policy.allow_user_seed_override):
            keep = False
            citation_allowed = False
            reason = "chinese_literature_without_authoritative_source_label"
        elif seed and not authoritative:
            reason = "user_seed_chinese_literature_needs_authority_review"

    item["paper_language"] = record_language
    item["literature_quality_policy"] = {
        "enabled": True,
        "manuscript_language": language,
        "include_chinese_literature": include_zh,
        "record_language": record_language,
        "is_user_seed": seed,
        "chinese_authority_matches": authority_matches,
        "keep_in_active_pool": keep,
        "citation_allowed": citation_allowed,
        "reason": reason,
    }
    if record_language == "zh":
        item["chinese_authority_status"] = "authoritative" if authoritative else "unverified"
    if not citation_allowed:
        item["citation_allowed"] = False
    return item


def apply_literature_quality_policy(
    records: list[dict[str, Any]],
    policy: LiteratureQualityPolicy,
    *,
    workspace_dir: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    language = infer_manuscript_language(workspace_dir, policy.manuscript_language)
    kept: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for record in records:
        item = annotate_literature_quality(
            record,
            policy,
            workspace_dir=workspace_dir,
            manuscript_language=language,
        )
        quality = item.get("literature_quality_policy") if isinstance(item.get("literature_quality_policy"), dict) else {}
        reason = str(quality.get("reason") or "accepted")
        counts[reason] = counts.get(reason, 0) + 1
        if bool(quality.get("keep_in_active_pool", True)):
            kept.append(item)
        else:
            item["triaged_out"] = True
            item["triaged_reason"] = reason
            item["read_disposition"] = "backlog"
            item["read_disposition_reason"] = "excluded_from_active_pool_by_literature_quality_policy"
            filtered.append(item)
    return kept, filtered, {
        "enabled": policy.enabled,
        "manuscript_language": language,
        "include_chinese_literature": include_chinese_literature(
            workspace_dir,
            policy,
            manuscript_language=language,
        ),
        "input_count": len(records),
        "kept_count": len(kept),
        "filtered_count": len(filtered),
        "reason_counts": counts,
    }
