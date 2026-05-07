from __future__ import annotations

from researchos.tools.paper_save_tools import _transform_to_papers_raw


def test_transform_infers_semantic_scholar_source_from_paper_id():
    paper = _transform_to_papers_raw(
        {
            "paperId": "abc123",
            "title": "Semantic Scholar Result",
            "authors": [{"name": "Ada Lovelace"}],
            "year": 2025,
            "url": "https://www.semanticscholar.org/paper/abc123",
            "externalIds": {"ArXiv": "2501.12345"},
        }
    )

    assert paper["source"] == "semantic_scholar"
    assert paper["provenance"]["source_tool"] == "semantic_scholar"


def test_transform_infers_informs_source_from_doi_and_unescapes_text():
    paper = _transform_to_papers_raw(
        {
            "title": "Supply &amp; Demand",
            "authors": ["Ada Lovelace"],
            "year": 2024,
            "venue": "Manufacturing &amp; Service Operations Management",
            "doi": "10.1287/msom.2022.0606",
        }
    )

    assert paper["source"] == "informs_crossref"
    assert paper["title"] == "Supply & Demand"
    assert paper["venue"] == "Manufacturing & Service Operations Management"


def test_transform_replaces_blank_provenance_source_tool():
    paper = _transform_to_papers_raw(
        {
            "id": "abc123",
            "source": "unknown",
            "title": "Semantic Scholar Result",
            "authors": ["Ada Lovelace"],
            "year": 2025,
            "url": "https://www.semanticscholar.org/paper/abc123",
            "provenance": {
                "source_tool": "",
                "source_url": "https://www.semanticscholar.org/paper/abc123",
            },
        }
    )

    assert paper["source"] == "semantic_scholar"
    assert paper["provenance"]["source_tool"] == "semantic_scholar"
