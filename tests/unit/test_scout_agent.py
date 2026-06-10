"""T2 Scout Agent 单元测试

测试覆盖：
1. Mock LLM 基本流程测试
2. 去重逻辑测试
3. validate_outputs 测试
4. MCP 降级测试
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from researchos.agents.scout import ScoutAgent, _bridge_screen_counts
from researchos.runtime.agent import ExecutionContext


def _domain_map_fixture(
    *,
    core: list[dict] | None = None,
    theory_bridge: list[dict] | None = None,
    adjacent: list[dict] | None = None,
    boundary: list[dict] | None = None,
    citation_edges: list | None = None,
    bucket_assignments: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "version": "1.0",
        "semantics": "domain_map_for_synthesis_and_ideation_not_final_gaps",
        "core": core or [],
        "theory_bridge": theory_bridge or [],
        "adjacent": adjacent or [],
        "boundary": boundary or [],
        "citation_edges": citation_edges or [],
        "bucket_assignments": bucket_assignments or {},
        "warnings": warnings or ["citation_edges_empty_or_unavailable"],
    }


@pytest.fixture
def test_workspace(tmp_path):
    """创建测试workspace"""
    workspace = tmp_path / "test_scout"
    workspace.mkdir()

    # 创建project.yaml
    project_data = {
        "project_id": "test-scout",
        "research_direction": "discrete diffusion language models",
        "keywords": ["discrete diffusion", "language model", "factorized"],
        "target_venue": "NeurIPS",
        "created_at": "2026-04-19T14:00:00Z",
    }
    (workspace / "project.yaml").write_text(yaml.dump(project_data))

    # 创建user_seeds目录
    (workspace / "user_seeds").mkdir()

    return workspace


@pytest.fixture
def scout_agent():
    """创建Scout Agent实例"""
    return ScoutAgent()


@pytest.fixture
def execution_context(test_workspace):
    """创建执行上下文"""
    return ExecutionContext(
        workspace_dir=test_workspace,
        project_id="test-scout",
        task_id="T2",
        run_id="test-run-001",
        inputs={"project": test_workspace / "project.yaml"},
        outputs_expected={
            "papers_raw": test_workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": test_workspace / "literature" / "papers_dedup.jsonl",
            "papers_verified": test_workspace / "literature" / "papers_verified.jsonl",
            "verification_failures": test_workspace / "literature" / "verification_failures.jsonl",
            "deep_read_queue": test_workspace / "literature" / "deep_read_queue.jsonl",
            "domain_map": test_workspace / "literature" / "domain_map.json",
            "access_audit": test_workspace / "literature" / "access_audit.md",
            "search_log": test_workspace / "literature" / "search_log.md",
            "missing_areas": test_workspace / "literature" / "missing_areas.md",
        },
    )


def test_scout_agent_spec(scout_agent):
    """测试Scout Agent的AgentSpec配置"""
    spec = scout_agent.spec
    assert spec.name == "scout"
    assert spec.model_tier in {"medium", "heavy"}
    assert spec.temperature == 0.5
    assert "inspect_user_seeds" in spec.tool_names
    assert "search_papers" in spec.tool_names
    assert "fetch_paper_metadata" in spec.tool_names
    assert "save_papers_dedup" in spec.tool_names
    assert "filter_by_domain" in spec.tool_names
    assert "build_verified_papers" in spec.tool_names
    assert "build_deep_read_queue" in spec.tool_names
    assert "fetch_outgoing_citations" in spec.tool_names
    assert "build_domain_map" in spec.tool_names
    assert "apply_semantic_screening" in spec.tool_names
    assert "elsevier_scopus_search" in spec.tool_names
    assert "informs_search" in spec.tool_names
    assert "normalize_seed_outline" in spec.tool_names
    # MCP工具已移除，等MCP配置完成后再启用
    # assert "mcp_semantic_scholar_search" in spec.tool_names
    # assert "mcp_arxiv_search" in spec.tool_names
    assert "literature/" in spec.allowed_write_prefixes
    assert "user_seeds/" in spec.allowed_write_prefixes


def test_scout_system_prompt(scout_agent, execution_context):
    """测试system prompt生成"""
    prompt = scout_agent.system_prompt(execution_context)
    assert "Scout Agent" in prompt
    assert "discrete diffusion language models" in prompt
    assert "Step 1" in prompt
    assert "inspect_user_seeds" in prompt
    assert "_DIR_GUIDE.md" in prompt
    assert "Step 5" in prompt
    assert "MCP" in prompt


def test_scout_system_prompt_with_seed_papers(scout_agent, execution_context):
    """测试带seed papers的system prompt"""
    # 创建seed papers
    seed_papers = [
        {
            "title": "Discrete Diffusion Models",
            "year": 2023,
            "role": "anchor",
            "doi": "10.1234/test",
        },
        {
            "title": "Language Model Basics",
            "year": 2022,
            "role": "reference",
        },
    ]
    seed_path = execution_context.workspace_dir / "user_seeds" / "seed_papers.jsonl"
    with seed_path.open("w") as f:
        for paper in seed_papers:
            f.write(json.dumps(paper) + "\n")

    prompt = scout_agent.system_prompt(execution_context)
    assert "2 篇" in prompt
    assert "Discrete Diffusion Models" in prompt


def test_scout_system_prompt_counts_user_seed_pdfs(scout_agent, execution_context):
    """旧路径 user_seeds/pdfs/ 的 PDF 也应进入 T2 seed prompt。"""
    pdf_dir = execution_context.workspace_dir / "user_seeds" / "pdfs"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "Doe - 2024 - Memory Retrieval for Agents.pdf").write_bytes(b"%PDF-1.4\n")

    prompt = scout_agent.system_prompt(execution_context)

    assert "1 篇" in prompt
    assert "Memory Retrieval for Agents" in prompt


def test_scout_system_prompt_includes_seed_ideas(scout_agent, execution_context):
    """测试 seed_ideas 会进入 T2 prompt。"""
    ideas_path = execution_context.workspace_dir / "user_seeds" / "seed_ideas.md"
    ideas_path.write_text(
        "研究如何从因果效应角度改进 AI agent memory retrieval，而不是只看 semantic 相似性。",
        encoding="utf-8",
    )

    prompt = scout_agent.system_prompt(execution_context)
    assert "用户种子想法" in prompt
    assert "因果效应角度改进 AI agent memory retrieval" in prompt


def test_scout_system_prompt_ignores_placeholder_seed_ideas(scout_agent, execution_context):
    ideas_path = execution_context.workspace_dir / "user_seeds" / "seed_ideas.md"
    ideas_path.parent.mkdir(parents=True, exist_ok=True)
    ideas_path.write_text("# 初步研究想法\n\n（暂无）\n", encoding="utf-8")

    prompt = scout_agent.system_prompt(execution_context)

    assert "用户种子想法" not in prompt
    assert "（暂无）" not in prompt


def test_scout_system_prompt_includes_external_resources(scout_agent, execution_context):
    """测试 seed_external_resources 会进入 T2 prompt。"""
    resources_path = execution_context.workspace_dir / "user_seeds" / "seed_external_resources.jsonl"
    resources_path.write_text(
        json.dumps(
            {
                "type": "dataset",
                "name": "AgentMemoryBench",
                "source": "huggingface:org/agent-memory-bench",
                "notes": "memory retrieval benchmark",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    prompt = scout_agent.system_prompt(execution_context)
    assert "用户提供的外部资源" in prompt
    assert "AgentMemoryBench" in prompt
    assert "huggingface:org/agent-memory-bench" in prompt


def test_scout_prompt_consumes_seed_outline_profile_as_query_prior(scout_agent, execution_context):
    profile_path = execution_context.workspace_dir / "user_seeds" / "seed_outline_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "semantics": "user_seed_outline_profile",
                "manuscript_type": "survey",
                "title": "从数据智能到决策风险：面向管理决策的智能算法风险研究综述",
                "framework": {
                    "risk_generation_chain": ["场景", "数据", "模型", "决策", "反馈"],
                    "perspectives": ["理论", "技术", "管理", "治理"],
                    "taxonomy_hint": "理论 / 技术 / 管理 / 治理 × 场景 -> 数据 -> 模型 -> 决策 -> 反馈",
                },
                "representative_literature_directions": [
                    {"direction": "bounded rationality", "use_as": "query_direction_not_verified_citation"},
                    {"direction": "AI governance", "use_as": "query_direction_not_verified_citation"},
                ],
                "query_profile": {
                    "search_languages": ["zh", "en"],
                    "query_variants": ["智能算法风险 管理决策 综述", "algorithmic risk managerial decision-making survey"],
                },
                "literature_seed_policy": {"directions_are_verified_citations": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prompt = scout_agent.system_prompt(execution_context)

    assert "文献流程 profile: survey" in prompt
    assert "用户 seed outline profile" in prompt
    assert "representative_literature_directions" in prompt
    assert "不是已验证 citation" in prompt
    assert "不能写入 `seed_papers.jsonl`" in prompt
    assert "智能算法风险/管理决策/算法治理" in prompt
    assert "management/IS/OR" in prompt


def test_scout_prompt_respects_english_no_chinese_literature_policy(scout_agent, execution_context):
    literature_dir = execution_context.workspace_dir / "literature"
    literature_dir.mkdir(parents=True, exist_ok=True)
    (literature_dir / "literature_params.json").write_text(
        json.dumps(
            {
                "semantics": "workspace_literature_coverage_parameters_for_t2_t3",
                "literature_quality": {
                    "enabled": True,
                    "manuscript_language": "en",
                    "include_chinese_literature": "false",
                    "english_manuscript_policy": "exclude_non_seed_chinese",
                    "chinese_literature_policy": "authoritative_or_seed",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prompt = scout_agent.system_prompt(execution_context)

    assert "manuscript_language=en" in prompt
    assert "include_chinese_literature=False" in prompt
    assert "不要主动设计中文 query" in prompt
    assert "不得把它当作英文稿核心 citation" in prompt


def test_scout_initial_user_message(scout_agent, execution_context):
    """测试initial user message"""
    msg = scout_agent.initial_user_message(execution_context)
    assert "T2" in msg
    assert "文献普查" in msg
    assert "agents.scout.behavior.t2_finalize.active_pool_max" in msg
    assert "metadata verification" in msg
    assert "deep_read_queue.jsonl" in msg


def test_validate_outputs_success(scout_agent, execution_context):
    """测试validate_outputs成功场景"""
    # 创建输出文件
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # papers_raw.jsonl
    raw_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A", "Author B"],
            "year": 2023,
            "venue": "NeurIPS",
            "abstract": "Abstract",
            "doi": f"10.1234/{i}",
            "citation_count": 100,
            "url": f"https://example.com/{i}",
        }
        for i in range(50)
    ]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # papers_dedup.jsonl (去重后30篇)
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A", "Author B"],
            "year": 2023,
            "venue": "NeurIPS",
            "source_type": "top_conference",
            "relevance_score": 0.8,
            "why_relevant": "Relevant",
            "abstract": "Abstract",
            "doi": f"10.1234/{i}",
            "citation_count": 100,
            "url": f"https://example.com/{i}",
        }
        for i in range(30)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    with (lit_dir / "papers_verified.jsonl").open("w") as f:
        for i, paper in enumerate(dedup_papers[:24], start=1):
            verified = dict(paper)
            verified["canonical_id"] = paper["id"]
            verified["preferred_id_source"] = "source_id"
            verified["verification_status"] = "metadata_verified"
            verified["verification_method"] = "crossref"
            verified["verification_source"] = "crossref"
            verified["verification_confidence"] = 0.95
            verified["verification_title_similarity"] = 0.98
            verified["verification_year_match"] = True
            f.write(json.dumps(verified) + "\n")

    (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")

    with (lit_dir / "deep_read_queue.jsonl").open("w") as f:
        for i, paper in enumerate(dedup_papers[:24], start=1):
            queue_record = {
                "paper_id": paper["id"],
                "title": paper["title"],
                "relevance_score": paper["relevance_score"],
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "PARTIAL_TEXT",
                "verification_status": "metadata_verified",
                "verification_confidence": 0.95,
                "seed_priority": i == 1,
                "queue_rank": i,
                "read_priority": 0.8,
                "target_bucket": "seed" if i == 1 else "target",
            }
            f.write(json.dumps(queue_record) + "\n")

    (lit_dir / "domain_map.json").write_text(json.dumps(_domain_map_fixture(
        core=[{"id": "s2_0", "title": "Paper 0", "degree": 1, "key_rationale_hint": "LLM_REVIEW_REQUIRED"}],
        bucket_assignments={"s2_0": "core"},
    ), ensure_ascii=False))

    # search_log.md
    (lit_dir / "access_audit.md").write_text("# Access Audit\n")
    (lit_dir / "search_log.md").write_text("# Search Log\n")

    # missing_areas.md
    (lit_dir / "missing_areas.md").write_text("# Missing Areas\n")

    seed_path = execution_context.workspace_dir / "user_seeds" / "seed_papers.jsonl"
    seed_path.write_text(json.dumps({"title": "Paper 0", "role": "anchor"}) + "\n")

    ok, err = scout_agent.validate_outputs(execution_context)
    assert ok
    assert err is None


def test_bridge_screen_counts_accepts_screened_baseline_bridge_candidate():
    records = [
        {
            "id": "bridge-baseline",
            "bridge_id": "b1",
            "retrieval_intent": "cross_domain_bridge",
            "semantic_screen": {
                "relation_to_project": "baseline_or_dataset_relevance",
                "role": "baseline",
                "can_enter_deep_read": True,
            },
        }
    ]

    assert _bridge_screen_counts(records)["b1"] == 1


def test_validate_outputs_too_few_papers(scout_agent, execution_context):
    """测试validate_outputs失败：论文太少"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # 15篇刚好达到当前配置的 papers_dedup_min。
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "venue": "Test Venue",
            "source_type": "preprint",
            "relevance_score": 0.8,
            "why_relevant": "Relevant",
            "abstract": "Abstract",
            "citation_count": 10,
            "url": f"https://example.com/{i}",
        }
        for i in range(15)
    ]
    # 创建对应的 raw papers
    raw_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "venue": "Test Venue",
            "abstract": "Abstract",
            "citation_count": 10,
            "doi": "",
            "url": f"https://example.com/{i}",
        }
        for i in range(15)
    ]

    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    with (lit_dir / "deep_read_queue.jsonl").open("w") as f:
        for i, paper in enumerate(dedup_papers, start=1):
            queue_record = {
                "paper_id": paper["id"],
                "title": paper["title"],
                "relevance_score": paper["relevance_score"],
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "PARTIAL_TEXT",
                "verification_status": "metadata_verified",
                "verification_confidence": 0.9,
                "seed_priority": False,
                "queue_rank": i,
                "read_priority": 0.8,
                "target_bucket": "target",
            }
            f.write(json.dumps(queue_record) + "\n")

    with (lit_dir / "papers_verified.jsonl").open("w") as f:
        for paper in dedup_papers:
            verified = dict(paper)
            verified["canonical_id"] = paper["id"]
            verified["verification_status"] = "metadata_verified"
            verified["verification_method"] = "semantic_scholar"
            verified["verification_source"] = "semantic_scholar"
            verified["verification_confidence"] = 0.9
            verified["verification_title_similarity"] = 0.99
            verified["verification_year_match"] = True
            f.write(json.dumps(verified) + "\n")

    (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")

    (lit_dir / "domain_map.json").write_text(json.dumps(_domain_map_fixture(
        core=[{"id": "s2_0", "title": "Paper 0", "degree": 1, "key_rationale_hint": "LLM_REVIEW_REQUIRED"}],
        bucket_assignments={"s2_0": "core"},
    ), ensure_ascii=False))

    (lit_dir / "access_audit.md").write_text("# Access Audit\n")
    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    # 刚好达到最低要求，应该通过
    ok, err = scout_agent.validate_outputs(execution_context)
    assert ok


def test_validate_outputs_rejects_verified_records_missing_queue_disposition(scout_agent, execution_context):
    """verified 池里的论文不能在 deep/shallow 阅读链中静默消失。"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    raw_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2024,
            "venue": "Test Venue",
            "abstract": "Abstract",
            "citation_count": 1,
            "doi": f"10.1234/{i}",
            "url": f"https://example.com/{i}",
        }
        for i in range(17)
    ]
    dedup_papers = [
        {
            **paper,
            "source_type": "unknown",
            "relevance_score": 0.8,
            "why_relevant": "metadata priority hint; needs review",
        }
        for paper in raw_papers
    ]
    with (lit_dir / "papers_raw.jsonl").open("w", encoding="utf-8") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")
    with (lit_dir / "papers_dedup.jsonl").open("w", encoding="utf-8") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")
    with (lit_dir / "papers_verified.jsonl").open("w", encoding="utf-8") as f:
        for paper in dedup_papers:
            verified = dict(paper)
            verified["canonical_id"] = paper["id"]
            verified["verification_status"] = "metadata_verified"
            verified["verification_method"] = "semantic_scholar"
            verified["verification_source"] = "semantic_scholar"
            verified["verification_confidence"] = 0.9
            verified["verification_title_similarity"] = 0.99
            verified["verification_year_match"] = True
            f.write(json.dumps(verified) + "\n")
    (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")

    # Old bug shape: queue reaches the configured minimum, but two verified papers have
    # no deep/shallow disposition and would vanish before T3 abstract sweep.
    with (lit_dir / "deep_read_queue.jsonl").open("w", encoding="utf-8") as f:
        for i, paper in enumerate(dedup_papers[:15], start=1):
            queue_record = {
                "paper_id": paper["id"],
                "title": paper["title"],
                "relevance_score": paper["relevance_score"],
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "ABSTRACT_ONLY",
                "verification_status": "metadata_verified",
                "verification_confidence": 0.9,
                "seed_priority": False,
                "queue_rank": i,
                "read_priority": 0.8,
                "target_bucket": "mainline_deep" if i <= 4 else "mainline_screened",
                "triaged_out": i > 4,
                "read_disposition": "deep_read" if i <= 4 else "shallow_read",
            }
            f.write(json.dumps(queue_record) + "\n")

    (lit_dir / "domain_map.json").write_text(
        json.dumps(
            _domain_map_fixture(
                core=[{"id": "s2_0", "title": "Paper 0", "degree": 1, "key_rationale_hint": "LLM_REVIEW_REQUIRED"}],
                bucket_assignments={"s2_0": "core"},
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lit_dir / "access_audit.md").write_text("# Access Audit\n", encoding="utf-8")
    (lit_dir / "search_log.md").write_text("# Search Log\n", encoding="utf-8")
    (lit_dir / "missing_areas.md").write_text("# Missing Areas\n", encoding="utf-8")

    ok, err = scout_agent.validate_outputs(execution_context)

    assert not ok
    assert err is not None
    assert "没有进入 deep_read_queue 的深读/浅读处置" in err


def test_validate_outputs_dedup_anomaly(scout_agent, execution_context):
    """测试validate_outputs失败：去重异常（dedup > raw）"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # raw只有10篇
    raw_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "venue": "Test Venue",
            "abstract": "Abstract",
            "citation_count": 10,
            "doi": "",
            "url": f"https://example.com/{i}",
        }
        for i in range(10)
    ]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # dedup有20篇（异常）
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "source": "semantic_scholar",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "venue": "Test Venue",
            "source_type": "preprint",
            "relevance_score": 0.8,
            "why_relevant": "Relevant",
            "abstract": "Abstract",
            "citation_count": 10,
            "url": f"https://example.com/{i}",
        }
        for i in range(20)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    (lit_dir / "papers_verified.jsonl").write_text("", encoding="utf-8")
    (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")
    (lit_dir / "deep_read_queue.jsonl").write_text("", encoding="utf-8")

    (lit_dir / "access_audit.md").write_text("# Access Audit\n")
    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    (lit_dir / "domain_map.json").write_text(json.dumps(_domain_map_fixture(warnings=["test"]), ensure_ascii=False))

    ok, err = scout_agent.validate_outputs(execution_context)
    assert not ok
    assert "去重异常" in err


def test_validate_outputs_missing_required_field(scout_agent, execution_context):
    """测试validate_outputs失败：缺少必需字段"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # papers_raw.jsonl (30篇)
    raw_papers = [{"id": f"s2:{i}", "title": f"Paper {i}"} for i in range(30)]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # 缺少relevance_score字段
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            # 缺少 relevance_score
        }
        for i in range(20)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    (lit_dir / "papers_verified.jsonl").write_text("", encoding="utf-8")
    (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")
    (lit_dir / "deep_read_queue.jsonl").write_text("", encoding="utf-8")

    (lit_dir / "access_audit.md").write_text("# Access Audit\n")
    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    (lit_dir / "domain_map.json").write_text(json.dumps(_domain_map_fixture(warnings=["test"]), ensure_ascii=False))

    ok, err = scout_agent.validate_outputs(execution_context)
    assert not ok
    assert "缺少字段" in err
    assert "relevance_score" in err
