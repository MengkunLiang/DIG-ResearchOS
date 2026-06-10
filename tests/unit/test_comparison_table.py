from __future__ import annotations

import json
from pathlib import Path

from researchos.runtime.comparison_table import repair_comparison_table_evidence_levels


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )


def test_repair_comparison_table_upgrades_seed_pdf_abstract_only_row(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "comparison_table.csv").write_text(
        "id,title,year,venue,method_family,dataset,key_metric,metric_value,baseline_of_ours,relevance_score,evidence_level\n"
        "noopenalex__seed,Seed Paper,2024,user_seed,,,,,,1.0,ABSTRACT_ONLY\n",
        encoding="utf-8",
    )
    _write_jsonl(
        literature / "access_audit.jsonl",
        [
            {
                "paper_id": "noopenalex::seed",
                "normalized_id": "noopenalex__seed",
                "title": "Seed Paper",
                "has_seed_pdf": True,
                "access_level_hint": "FULL_TEXT_LOCAL",
                "evidence_level": "FULL_TEXT",
            }
        ],
    )

    result = repair_comparison_table_evidence_levels(workspace)

    assert result["changed"] == 1
    assert "FULL_TEXT" in (literature / "comparison_table.csv").read_text(encoding="utf-8")


def test_repair_comparison_table_uses_note_status_when_stronger(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes = literature / "paper_notes"
    notes.mkdir(parents=True)
    (literature / "comparison_table.csv").write_text(
        "id,title,year,venue,method_family,dataset,key_metric,metric_value,baseline_of_ours,relevance_score,evidence_level\n"
        "paper1,Paper One,2024,Venue,,,,,,0.8,ABSTRACT_ONLY\n",
        encoding="utf-8",
    )
    (notes / "paper1.md").write_text(
        "# Paper One\n\n- **ID**: paper1\n- **Status**: [PARTIAL-TEXT]\n",
        encoding="utf-8",
    )

    result = repair_comparison_table_evidence_levels(workspace)

    assert result["changed"] == 1
    assert "PARTIAL_TEXT" in (literature / "comparison_table.csv").read_text(encoding="utf-8")
