from __future__ import annotations

import pytest

from researchos.tools.publisher_search import ElsevierScopusSearchTool, InformsSearchTool


@pytest.mark.asyncio
async def test_elsevier_scopus_search_requires_api_key(monkeypatch):
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    tool = ElsevierScopusSearchTool(api_key=None)

    result = await tool.execute(query="supply chain optimization", count=5)

    assert not result.ok
    assert result.error == "missing_api_key"
    assert "ELSEVIER_API_KEY" in result.content


def test_elsevier_scopus_normalizes_entry():
    paper = ElsevierScopusSearchTool._normalize_entry(
        {
            "dc:identifier": "SCOPUS_ID:123",
            "eid": "2-s2.0-123",
            "dc:title": "A Scopus Paper",
            "dc:creator": "Doe J.",
            "prism:publicationName": "European Journal of Operational Research",
            "prism:coverDate": "2025-03-01",
            "prism:doi": "10.1016/example",
            "citedby-count": "7",
            "link": [{"@ref": "scopus", "@href": "https://www.scopus.com/record/display.uri"}],
        }
    )

    assert paper["source"] == "elsevier_scopus"
    assert paper["title"] == "A Scopus Paper"
    assert paper["year"] == 2025
    assert paper["citation_count"] == 7
    assert paper["doi"] == "10.1016/example"
    assert paper["externalIds"]["EID"] == "2-s2.0-123"


def test_informs_search_normalizes_crossref_item():
    paper = InformsSearchTool._normalize_item(
        {
            "DOI": "10.1287/mnsc.2025.1234",
            "title": ["An INFORMS Paper"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published-online": {"date-parts": [[2025, 4, 1]]},
            "container-title": ["Management Science"],
            "is-referenced-by-count": "11",
            "URL": "https://doi.org/10.1287/mnsc.2025.1234",
            "abstract": "<jats:p>Queueing and optimization.</jats:p>",
            "type": "journal-article",
            "publisher": "Institute for Operations Research and the Management Sciences (INFORMS)",
        }
    )

    assert paper["source"] == "informs_crossref"
    assert paper["title"] == "An INFORMS Paper"
    assert paper["authors"] == ["Ada Lovelace"]
    assert paper["year"] == 2025
    assert paper["venue"] == "Management Science"
    assert paper["citation_count"] == 11
    assert paper["externalIds"] == {
        "DOI": "10.1287/mnsc.2025.1234",
        "CrossrefPrefix": "10.1287",
    }
    assert paper["abstract"] == "Queueing and optimization."


@pytest.mark.asyncio
async def test_informs_search_filters_to_journal_articles_by_default(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"items": [], "total-results": 0}}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None, headers=None):
            captured["params"] = params or {}
            return _FakeResponse()

    monkeypatch.setattr("researchos.tools.publisher_search.httpx.AsyncClient", _FakeClient)

    tool = InformsSearchTool(email="researcher@example.com")
    result = await tool.execute(query="supply chain optimization", rows=5)

    assert result.ok
    assert "prefix:10.1287" in captured["params"]["filter"]
    assert "type:journal-article" in captured["params"]["filter"]
