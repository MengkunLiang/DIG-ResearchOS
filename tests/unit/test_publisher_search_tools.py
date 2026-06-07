from __future__ import annotations

import pytest

from researchos.tools.crossref_api import CrossRefSearchTool
from researchos.tools.multi_source_search import MultiSourceSearchParams, MultiSourceSearchTool
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


def test_elsevier_scopus_keeps_missing_year_unknown():
    paper = ElsevierScopusSearchTool._normalize_entry(
        {
            "dc:identifier": "SCOPUS_ID:123",
            "dc:title": "A Scopus Paper",
        }
    )

    assert paper["year"] is None


def test_informs_search_normalizes_crossref_item():
    paper = InformsSearchTool._normalize_item(
        {
            "DOI": "10.1287/mnsc.2025.1234",
            "title": ["An INFORMS Paper"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published-online": {"date-parts": [[2025, 4, 1]]},
            "container-title": ["Manufacturing &amp; Service Operations Management"],
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
    assert paper["venue"] == "Manufacturing & Service Operations Management"
    assert paper["citation_count"] == 11
    assert paper["externalIds"] == {
        "DOI": "10.1287/mnsc.2025.1234",
        "CrossrefPrefix": "10.1287",
    }
    assert paper["abstract"] == "Queueing and optimization."


def test_informs_search_keeps_missing_year_unknown():
    paper = InformsSearchTool._normalize_item(
        {
            "DOI": "10.1287/mnsc.example",
            "title": ["An INFORMS Paper"],
            "publisher": "Institute for Operations Research and the Management Sciences (INFORMS)",
        }
    )

    assert paper["year"] is None


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


def test_multi_source_search_defaults_include_informs():
    params = MultiSourceSearchParams(query="supply chain optimization")

    assert "informs" in params.sources


def test_multi_source_search_dedup_merges_pdf_and_external_ids():
    tool = MultiSourceSearchTool(email="researcher@example.com")

    papers = tool._deduplicate_papers(
        [
            {
                "id": "doi:10.1234/example",
                "source": "crossref",
                "title": "Shared Metadata Paper",
                "authors": [{"name": "Ada"}],
                "doi": "10.1234/example",
                "abstract": "",
                "externalIds": {"DOI": "10.1234/example"},
            },
            {
                "id": "arxiv:2401.12345",
                "source": "arxiv",
                "title": "Shared Metadata Paper",
                "authors": [{"name": "Ada"}],
                "abstract": "Richer abstract from arXiv.",
                "pdf_url": "https://arxiv.org/pdf/2401.12345.pdf",
                "externalIds": {"ArXiv": "2401.12345"},
            },
        ]
    )

    assert len(papers) == 1
    assert papers[0]["doi"] == "10.1234/example"
    assert papers[0]["pdf_url"] == "https://arxiv.org/pdf/2401.12345.pdf"
    assert papers[0]["externalIds"]["DOI"] == "10.1234/example"
    assert papers[0]["externalIds"]["ArXiv"] == "2401.12345"
    assert papers[0]["abstract"] == "Richer abstract from arXiv."


def test_multi_source_search_format_accepts_string_and_dict_authors():
    content = MultiSourceSearchTool._format_papers(
        [
            {
                "title": "OpenAlex Style Paper",
                "authors": ["Ada Lovelace", "Grace Hopper"],
                "year": 2025,
                "source": "openalex",
                "citation_count": 3,
            },
            {
                "title": "Crossref Style Paper",
                "authors": [{"name": "Alan Turing"}, {"display_name": "Katherine Johnson"}],
                "year": 2024,
                "source": "crossref",
                "citation_count": 1,
            },
        ],
        {"openalex": 1, "crossref": 1},
    )

    assert "OpenAlex Style Paper" in content
    assert "Ada Lovelace, Grace Hopper" in content
    assert "Crossref Style Paper" in content
    assert "Alan Turing, Katherine Johnson" in content


@pytest.mark.asyncio
async def test_multi_source_search_crossref_tolerates_heterogeneous_metadata(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {
                            "DOI": "10.5555/heterogeneous",
                            "title": "String Title Paper",
                            "author": ["Ada Lovelace", {"given": "Grace", "family": "Hopper"}],
                            "published": {"date-parts": [["not-a-year"]]},
                            "container-title": "String Venue",
                            "is-referenced-by-count": "7",
                        }
                    ]
                }
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr("researchos.tools.multi_source_search.httpx.AsyncClient", _FakeClient)

    tool = MultiSourceSearchTool(email="researcher@example.com")
    papers = await tool._search_crossref("heterogeneous metadata", 5)

    assert papers[0]["title"] == "String Title Paper"
    assert papers[0]["authors"] == [{"name": "Ada Lovelace"}, {"name": "Grace Hopper"}]
    assert papers[0]["year"] is None
    assert papers[0]["venue"] == "String Venue"
    assert papers[0]["citation_count"] == 7


@pytest.mark.asyncio
async def test_crossref_search_uses_issued_year_when_published_missing(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "total-results": 1,
                    "items": [
                        {
                            "DOI": "10.5555/issued",
                            "title": ["Issued Only Paper"],
                            "author": [{"given": "Ada", "family": "Lovelace"}],
                            "issued": {"date-parts": [[2026, 2, 1]]},
                            "container-title": ["Issued Venue"],
                        }
                    ],
                }
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr("researchos.tools.crossref_api.httpx.AsyncClient", _FakeClient)

    result = await CrossRefSearchTool().execute(query="issued only", rows=1)

    assert result.ok
    assert result.data["papers"][0]["year"] == 2026


@pytest.mark.asyncio
async def test_multi_source_search_rejects_blank_query_with_tool_result():
    tool = MultiSourceSearchTool(email="researcher@example.com")

    result = await tool.execute(query="   ", max_results=5)

    assert not result.ok
    assert result.error == "empty_query"
    assert "query 不能为空" in result.content


@pytest.mark.asyncio
async def test_multi_source_search_informs_branch_uses_crossref_prefix(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1287/mnsc.2025.1234",
                            "title": ["An INFORMS Paper"],
                            "author": [{"given": "Ada", "family": "Lovelace"}],
                            "published-online": {"date-parts": [[2025, 4, 1]]},
                            "container-title": ["Management Science"],
                            "is-referenced-by-count": 11,
                        }
                    ]
                }
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            captured["url"] = url
            return _FakeResponse()

    monkeypatch.setattr("researchos.tools.multi_source_search.httpx.AsyncClient", _FakeClient)

    tool = MultiSourceSearchTool(email="researcher@example.com")
    papers = await tool._search_informs("supply chain optimization", 5)

    assert "filter=prefix:10.1287,type:journal-article" in captured["url"]
    assert papers[0]["source"] == "informs_crossref"
    assert papers[0]["externalIds"]["CrossrefPrefix"] == "10.1287"
