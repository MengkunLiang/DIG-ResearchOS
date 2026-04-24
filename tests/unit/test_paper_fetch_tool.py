from __future__ import annotations

from pathlib import Path

import pytest

from researchos.tools.paper_fetch import FetchPaperPdfTool
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
        if url in self.responses:
            return self.responses[url]
        raise RuntimeError(f"Unexpected URL: {url}")


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


def test_url_to_pdf_candidates_converts_arxiv_abs_link():
    candidates = FetchPaperPdfTool._url_to_pdf_candidates("https://arxiv.org/abs/2401.12345")
    assert "https://arxiv.org/abs/2401.12345" in candidates
    assert "https://arxiv.org/pdf/2401.12345.pdf" in candidates
