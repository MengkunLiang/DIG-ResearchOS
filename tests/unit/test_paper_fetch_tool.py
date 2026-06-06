from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from researchos.tools.paper_fetch import ExtractPdfTextTool, FetchPaperPdfTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"%PDF-1.4 fake", headers=None, json_data=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "application/pdf"}
        self._json_data = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_data is None:
            raise RuntimeError("No JSON data")
        return self._json_data

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="ignore")


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, _FakeResponse], *args, **kwargs):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        if "api.openalex.org/works/" in url:
            return self.responses["openalex"]
        if "api.unpaywall.org/v2/" in url:
            return self.responses["unpaywall"]
        if url in self.responses:
            return self.responses[url]
        raise RuntimeError(f"Unexpected URL: {url}")


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePDF:
    def __init__(self, pages: list[str]) -> None:
        self.pages = [_FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_fetch_paper_pdf_downloads_from_openalex_oa_location(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = FetchPaperPdfTool(policy)

    openalex_payload = {
        "best_oa_location": {"pdf_url": "https://example.org/paper.pdf", "landing_page_url": "https://example.org/landing"},
        "primary_location": None,
        "locations": [],
        "doi": "https://doi.org/10.1234/test",
    }
    responses = {
        "openalex": _FakeResponse(
            headers={"content-type": "application/json"},
            content=b"{}",
            json_data=openalex_payload,
        ),
        "https://example.org/paper.pdf": _FakeResponse(),
    }

    import researchos.tools.paper_fetch as paper_fetch

    monkeypatch.setattr(
        paper_fetch.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, *args, **kwargs),
    )

    result = await tool.execute(paper_id="10.1234/test", save_path="paper.pdf")

    assert result.ok
    assert (workspace / "paper.pdf").exists()
    assert result.data["url"] == "https://example.org/paper.pdf"


@pytest.mark.asyncio
async def test_fetch_paper_pdf_accepts_arxiv_doi_prefix(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = FetchPaperPdfTool(policy)

    responses = {
        "https://arxiv.org/pdf/2303.12712.pdf": _FakeResponse(),
    }

    import researchos.tools.paper_fetch as paper_fetch

    monkeypatch.setattr(
        paper_fetch.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, *args, **kwargs),
    )

    result = await tool.execute(
        paper_id="doi:10.48550/arXiv.2303.12712",
        save_path="paper.pdf",
    )

    assert result.ok
    assert result.data["url"] == "https://arxiv.org/pdf/2303.12712.pdf"
    assert (workspace / "paper.pdf").exists()


@pytest.mark.asyncio
async def test_fetch_paper_pdf_reports_candidate_failures(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = FetchPaperPdfTool(policy)

    responses = {
        "https://arxiv.org/pdf/2401.12345.pdf": _FakeResponse(
            content=b"<html>not pdf</html>",
            headers={"content-type": "text/html"},
        ),
        "https://export.arxiv.org/pdf/2401.12345.pdf": _FakeResponse(status_code=403),
    }

    import researchos.tools.paper_fetch as paper_fetch

    monkeypatch.setattr(
        paper_fetch.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, *args, **kwargs),
    )

    result = await tool.execute(paper_id="arxiv:2401.12345", save_path="paper.pdf")

    assert not result.ok
    assert result.error == "download_failed"
    assert not (workspace / "paper.pdf").exists()
    errors = [item["error"] for item in result.data["candidate_errors"]]
    assert errors == ["not_pdf", "http_403"]
    assert "Recent errors" in result.content


def test_url_to_pdf_candidates_converts_arxiv_abs_link():
    candidates = FetchPaperPdfTool._url_to_pdf_candidates("https://arxiv.org/abs/2401.12345")
    assert "https://arxiv.org/abs/2401.12345" in candidates
    assert "https://arxiv.org/pdf/2401.12345.pdf" in candidates


def test_fetch_paper_pdf_recovers_candidates_from_workspace_metadata(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    literature = workspace / "literature"
    literature.mkdir()
    (literature / "papers_verified.jsonl").write_text(
        (
            '{"id":"W1","canonical_id":"W1","title":"Paper With PDF",'
            '"pdf_url":"https://example.org/full.pdf","doi":"10.1234/test",'
            '"externalIds":{"ArXiv":"2401.12345"}}\n'
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(workspace, ["", "literature/"], [""])
    tool = FetchPaperPdfTool(policy)

    candidates = tool._workspace_metadata_pdf_candidates("W1")

    assert "https://example.org/full.pdf" in candidates
    assert "https://arxiv.org/pdf/2401.12345.pdf" in candidates
    assert "https://doi.org/10.1234/test" in candidates


def test_fetch_paper_pdf_recovers_open_access_pdf_metadata_variants(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    literature = workspace / "literature"
    literature.mkdir()
    (literature / "papers_verified.jsonl").write_text(
        (
            '{"id":"W2","canonical_id":"W2","title":"Paper With OA Metadata",'
            '"openAccessPdf":{"url":"https://example.org/oa-landing","url_for_pdf":"https://example.org/oa.pdf"},'
            '"oa_locations":[{"pdfUrl":"https://example.org/loc.pdf"}]}\n'
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(workspace, ["", "literature/"], [""])
    tool = FetchPaperPdfTool(policy)

    candidates = tool._workspace_metadata_pdf_candidates("W2")

    assert "https://example.org/oa.pdf" in candidates
    assert "https://example.org/oa-landing" in candidates
    assert "https://example.org/loc.pdf" in candidates


@pytest.mark.asyncio
async def test_fetch_paper_pdf_uses_unpaywall_doi_candidates(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = FetchPaperPdfTool(policy)

    responses = {
        "openalex": _FakeResponse(
            headers={"content-type": "application/json"},
            content=b"{}",
            json_data={"best_oa_location": None, "primary_location": None, "locations": []},
        ),
        "unpaywall": _FakeResponse(
            headers={"content-type": "application/json"},
            content=b"{}",
            json_data={
                "best_oa_location": {
                    "url_for_pdf": "https://example.org/unpaywall.pdf",
                    "url": "https://example.org/landing",
                }
            },
        ),
        "https://example.org/unpaywall.pdf": _FakeResponse(),
    }

    import researchos.tools.paper_fetch as paper_fetch

    monkeypatch.setattr(
        paper_fetch.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, *args, **kwargs),
    )

    result = await tool.execute(paper_id="doi:10.1234/unpaywall", save_path="paper.pdf")

    assert result.ok
    assert result.data["url"] == "https://example.org/unpaywall.pdf"
    assert (workspace / "paper.pdf").exists()
    assert "full_text" not in result.data


@pytest.mark.asyncio
async def test_fetch_paper_pdf_follows_citation_pdf_url_from_landing_html(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = FetchPaperPdfTool(policy)

    landing_html = (
        b"<html><head>"
        b'<meta name="citation_pdf_url" content="/downloads/paper.pdf">'
        b"</head></html>"
    )
    responses = {
        "openalex": _FakeResponse(
            headers={"content-type": "application/json"},
            content=b"{}",
            json_data={"best_oa_location": None, "primary_location": None, "locations": []},
        ),
        "unpaywall": _FakeResponse(
            headers={"content-type": "application/json"},
            content=b"{}",
            json_data={},
        ),
        "https://doi.org/10.1234/html": _FakeResponse(
            content=landing_html,
            headers={"content-type": "text/html; charset=utf-8"},
        ),
        "https://doi.org/downloads/paper.pdf": _FakeResponse(),
    }

    import researchos.tools.paper_fetch as paper_fetch

    monkeypatch.setattr(
        paper_fetch.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses, *args, **kwargs),
    )

    result = await tool.execute(paper_id="doi:10.1234/html", save_path="paper.pdf")

    assert result.ok
    assert result.data["url"] == "https://doi.org/downloads/paper.pdf"
    assert "https://doi.org/downloads/paper.pdf" in result.data["candidates_tried"]
    assert (workspace / "paper.pdf").exists()
    attempts = (workspace / "literature" / "pdf_fetch_attempts.jsonl").read_text(encoding="utf-8")
    assert '"ok": true' in attempts


@pytest.mark.asyncio
async def test_extract_pdf_text_uses_fuller_default_preview(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    pdf_path = workspace / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    def fake_open(path: Path) -> _FakePDF:
        assert path == pdf_path
        return _FakePDF(["a" * 8000, "b" * 8000])

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=fake_open))

    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    result = await ExtractPdfTextTool(policy).execute(pdf_path="paper.pdf")

    assert result.ok
    assert len(result.content) > 16_000
    assert result.data["max_chars"] == 50_000
    assert result.data["truncated"] is False
    assert result.data["total_pages"] == 2
    assert result.data["extracted_page_range"] == "1-2"
    assert result.data["complete_pdf_read"] is True
    assert "total_pages: 2" in result.content
    assert "complete_pdf_read: true" in result.content
    assert "full_text" not in result.data
    assert "text_preview" in result.data
