from __future__ import annotations

from pathlib import Path

from researchos.runtime.literature_quality import (
    LiteratureQualityPolicy,
    apply_literature_quality_policy,
    detect_record_language,
    infer_manuscript_language,
)


def test_english_manuscript_filters_non_seed_chinese_records(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "project.yaml").write_text("language: en\n", encoding="utf-8")

    kept, filtered, meta = apply_literature_quality_policy(
        [
            {"id": "cn1", "title": "普通期刊论文", "venue": "工程管理与技术探讨"},
            {"id": "en1", "title": "AI governance in organizations", "venue": "MIS Quarterly"},
        ],
        LiteratureQualityPolicy(),
        workspace_dir=workspace,
    )

    assert [item["id"] for item in kept] == ["en1"]
    assert filtered[0]["triaged_reason"] == "english_manuscript_excludes_chinese_literature"
    assert meta["filtered_count"] == 1


def test_chinese_literature_requires_authoritative_source_or_seed(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "project.yaml").write_text("language: zh\n", encoding="utf-8")

    kept, filtered, _meta = apply_literature_quality_policy(
        [
            {"id": "cn-good", "title": "算法治理研究", "venue": "管理世界 CSSCI"},
            {"id": "cn-bad", "title": "算法治理随笔", "venue": "地方普刊"},
            {"id": "cn-seed", "title": "用户提供论文", "venue": "未知", "source": "user_seed"},
        ],
        LiteratureQualityPolicy(),
        workspace_dir=workspace,
    )

    assert [item["id"] for item in kept] == ["cn-good", "cn-seed"]
    assert kept[0]["chinese_authority_status"] == "authoritative"
    assert kept[1]["literature_quality_policy"]["reason"] == "user_seed_chinese_literature_needs_authority_review"
    assert filtered[0]["triaged_reason"] == "chinese_literature_without_authoritative_source_label"


def test_detect_language_and_infer_mixed_from_seed_outline(tmp_path: Path):
    workspace = tmp_path / "ws"
    profile_dir = workspace / "user_seeds"
    profile_dir.mkdir(parents=True)
    (profile_dir / "seed_outline_profile.json").write_text(
        '{"language":"zh-en","query_profile":{"search_languages":["zh","en"]}}',
        encoding="utf-8",
    )

    assert detect_record_language({"title": "数智赋能：信息系统研究的新跃迁"}) == "zh"
    assert infer_manuscript_language(workspace) == "mixed"
