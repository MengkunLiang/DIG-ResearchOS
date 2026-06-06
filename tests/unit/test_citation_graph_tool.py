from __future__ import annotations

import pytest

from researchos.tools.citation_graph import FetchOutgoingCitationsTool


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeCitationClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        if "api.openalex.org/works/https://doi.org/10.1234/source" in url:
            return _FakeResponse(status_code=429)
        if "api.crossref.org/works/10.1234%2Fsource" in url:
            return _FakeResponse(
                payload={
                    "message": {
                        "DOI": "10.1234/source",
                        "title": ["Source Paper"],
                        "reference": [
                            {
                                "DOI": "10.5678/ref",
                                "article-title": "Referenced Paper",
                                "year": "2024",
                            }
                        ],
                    }
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")


@pytest.mark.asyncio
async def test_fetch_outgoing_citations_uses_crossref_fallback_on_openalex_failure(monkeypatch):
    import researchos.tools.citation_graph as citation_graph

    monkeypatch.setattr(
        citation_graph.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeCitationClient(*args, **kwargs),
    )

    result = await FetchOutgoingCitationsTool().execute(
        openalex_id_or_doi="10.1234/source",
        max_refs=10,
        max_candidate_papers=5,
    )

    assert result.ok
    assert result.data["fallback_source"] == "crossref"
    assert result.data["referenced_works"] == ["10.5678/ref"]
    assert result.data["papers"][0]["source_tool"] == "fetch_outgoing_citations_crossref_fallback"
    assert result.data["papers"][0]["doi"] == "10.5678/ref"
    assert "crossref_reference_fallback" in result.data["warnings"]
