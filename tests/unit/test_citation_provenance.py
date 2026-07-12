from __future__ import annotations

import json
from pathlib import Path

from researchos.tools.manuscript import build_citation_provenance_audit


def _write_provenance_fixture(workspace: Path, *, evidence_level: str, score: float, citation_use: str) -> None:
    literature = workspace / "literature"
    notes = literature / "paper_notes"
    notes.mkdir(parents=True)
    (literature / "related_work.bib").write_text(
        "@article{verified2025, title={Verified Treatment Effects}, author={Doe, Jane}, year={2025}}\n",
        encoding="utf-8",
    )
    (notes / "P1.md").write_text(
        "# Verified Treatment Effects\n\n"
        f"- **Status**: [{evidence_level}]\n"
        f"- **Citation Quality Score**: {score}\n"
        f"- **Citation Use**: {citation_use}\n",
        encoding="utf-8",
    )
    (literature / "citation_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "note_id": "P1",
                        "title": "Verified Treatment Effects",
                        "source_file": "paper_notes/P1.md",
                        "bib_key": "verified2025",
                        "evidence_level": evidence_level,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_citation_provenance_audit_accepts_mapped_full_text_source(tmp_path: Path):
    _write_provenance_fixture(
        tmp_path,
        evidence_level="FULL-TEXT",
        score=0.8,
        citation_use="core_evidence",
    )

    audit = build_citation_provenance_audit(
        tmp_path,
        paper="Prior work shows a treatment effect under the stated setting \\citep{verified2025}.",
    )

    assert audit["summary"]["hard_fail_count"] == 0
    assert audit["records"][0]["status"] == "PASS"


def test_citation_provenance_audit_rejects_abstract_only_strong_claim(tmp_path: Path):
    _write_provenance_fixture(
        tmp_path,
        evidence_level="ABSTRACT_ONLY",
        score=0.4,
        citation_use="background_only",
    )

    audit = build_citation_provenance_audit(
        tmp_path,
        paper="Prior work proves that the intervention causes a significant improvement \\citep{verified2025}.",
    )

    assert audit["summary"]["hard_fail_count"] >= 1
    assert any("ABSTRACT_ONLY" in issue for issue in audit["hard_failures"])
    assert audit["records"][0]["status"] == "FAIL"
