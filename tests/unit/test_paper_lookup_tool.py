from __future__ import annotations

from pathlib import Path

import pytest

from researchos.tools.paper_lookup import LookupPaperRecordTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.mark.asyncio
async def test_lookup_paper_record_matches_normalized_doi(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "papers_verified.jsonl").write_text(
        (
            '{"id":"10.18653/v1/2026.eacl-long.15",'
            '"canonical_id":"10.18653/v1/2026.eacl-long.15",'
            '"title":"H-MEM",'
            '"year":2026,'
            '"venue":"EACL",'
            '"abstract":"A long abstract about hierarchical memory.",'
            '"verification_status":"metadata_verified"}\n'
        ),
        encoding="utf-8",
    )
    (literature / "deep_read_queue.jsonl").write_text(
        '{"paper_id":"10.18653/v1/2026.eacl-long.15","normalized_id":"10.18653_v1_2026.eacl-long.15","queue_rank":1}\n',
        encoding="utf-8",
    )

    policy = WorkspaceAccessPolicy(workspace, ["literature/"], [])
    result = await LookupPaperRecordTool(policy).execute(
        paper_id="10.18653_v1_2026.eacl-long.15",
    )

    assert result.ok
    assert result.data["found"] is True
    assert result.data["record"]["title"] == "H-MEM"
    assert result.data["record"]["normalized_id"] == "10.18653_v1_2026.eacl-long.15"
    assert "papers_verified.jsonl" in result.content
    assert len(result.content) < 3000


@pytest.mark.asyncio
async def test_lookup_paper_record_reports_missing_without_dumping_pool(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "papers_verified.jsonl").write_text(
        '{"id":"paper1","title":"Known Paper","abstract":"x"}\n',
        encoding="utf-8",
    )

    policy = WorkspaceAccessPolicy(workspace, ["literature/"], [])
    result = await LookupPaperRecordTool(policy).execute(paper_id="missing")

    assert result.ok
    assert result.data["found"] is False
    assert "Known Paper" not in result.content


@pytest.mark.asyncio
async def test_lookup_paper_record_accepts_t3_queue_rank(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "deep_read_queue.jsonl").write_text(
        (
            '{"paper_id":"noopenalex::496b8b9485c829bf",'
            '"normalized_id":"noopenalex__496b8b9485c829bf",'
            '"queue_rank":1,'
            '"title":"Causal-Invariant Cross-Domain Out-of-Distribution Recommendation",'
            '"abstract":"Queue abstract."}\n'
        ),
        encoding="utf-8",
    )

    policy = WorkspaceAccessPolicy(workspace, ["literature/"], [])
    result = await LookupPaperRecordTool(policy).execute(queue_rank=1)

    assert result.ok
    assert result.data["found"] is True
    assert result.data["record"]["paper_id"] == "noopenalex::496b8b9485c829bf"
    assert result.data["matched_sources"] == ["literature/deep_read_queue.jsonl"]
    assert "Causal-Invariant" in result.content


@pytest.mark.asyncio
async def test_lookup_paper_record_queue_rank_merges_verified_and_raw_metadata(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "deep_read_queue.jsonl").write_text(
        (
            '{"paper_id":"doi:10.1234/test",'
            '"normalized_id":"doi_10.1234_test",'
            '"queue_rank":1,'
            '"title":"Thin Queue Paper"}\n'
        ),
        encoding="utf-8",
    )
    (literature / "papers_verified.jsonl").write_text(
        (
            '{"id":"doi:10.1234/test",'
            '"canonical_id":"doi:10.1234/test",'
            '"title":"Thin Queue Paper",'
            '"abstract":"Verified abstract with enough detail for Reader.",'
            '"doi":"10.1234/test",'
            '"externalIds":{"DOI":"10.1234/test"},'
            '"references":[{"doi":"10.9999/ref"}],'
            '"verification_status":"metadata_verified"}\n'
        ),
        encoding="utf-8",
    )
    (literature / "papers_raw.jsonl").write_text(
        (
            '{"id":"doi:10.1234/test",'
            '"title":"Thin Queue Paper",'
            '"pdf_url":"https://example.org/paper.pdf",'
            '"openAccessPdf":{"url_for_pdf":"https://example.org/oa.pdf"}}\n'
        ),
        encoding="utf-8",
    )

    policy = WorkspaceAccessPolicy(workspace, ["literature/"], [])
    result = await LookupPaperRecordTool(policy).execute(queue_rank=1)

    assert result.ok
    assert result.data["found"] is True
    assert result.data["match_count"] == 3
    record = result.data["record"]
    assert record["abstract"] == "Verified abstract with enough detail for Reader."
    assert record["pdf_url"] == "https://example.org/paper.pdf"
    assert record["references"] == [{"doi": "10.9999/ref"}]
    assert "pdf/oa candidates" in result.content
    assert "reference hints: 1" in result.content
