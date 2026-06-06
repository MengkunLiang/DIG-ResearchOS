from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.runtime.t3_notes_manifest import (
    build_t3_notes_manifest,
    format_completion_diagnostics,
    target_entries,
)
from researchos.tools.save_paper_note import SavePaperNoteTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + ("\n" if records else ""),
        encoding="utf-8",
    )


def _valid_note(paper_id: str) -> str:
    return f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: A, B
- **Venue**: TestConf (2025)
- **DOI/arXiv**: arxiv:2501.00001
- **Citations**: 10
- **Verification**: metadata_verified (confidence: 0.95)
- **Status**: [FULL-TEXT]

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
- Accuracy: 88.1 [Evidence: Results section]

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| test | test | Strong |

## 5. Limitations
- limit

## 6. Relevance to Our Research
- relevant

## 7. Technical Details Worth Noting
- detail

## 8. Strengths
- strong

## 9. Weaknesses / Gaps
- weak

## 10. Key Quotes
> "quote"

## 11. My Questions
- question

## 12. Reading Coverage
- **PDF source**: literature/pdfs/{paper_id}.pdf
- **Pages read**: 1-10 / 10
- **Extraction calls**: extract_pdf_text pages 1-10
- **Truncation**: none
- **Status rationale**: All PDF pages were read without truncation.

## 13. Mechanism Claim
- **Stated mechanism**: The method improves performance through better feature extraction
- **Evidence type**: ablation_supported
- **Supporting artifact**: Table 2

## 14. Design Rationale
- **Rationale**: The paper argues that targeted feature extraction is the right design.
- **Rationale evidence**: The rationale is supported by the ablation in Table 2.
- **Rationale weakness**: The rationale may depend on benchmark assumptions.

## 15. Artifact & Design Principles
- **Artifact type**: method
- **Artifact description**: A representation learning method.
- **Design principles**: Match feature selection to task structure.

## 16. Data View & Evaluation Mode
- **Data view**: Benchmark datasets.
- **Evaluation mode**: summative benchmark evaluation.
- **Validity concern**: External validity is limited.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: It improves an existing pipeline.
- **Why not routine**: The design rationale changes where task-specific structure enters.

## 18. Boundary Conditions
- **Works when**: Task-specific features are stable.
- **May fail when**: The target task changes.
- **Untested boundary**: Cross-domain transfer remains untested.

## 19. Cross-Paper Tension
- **Tension**: none
- **Competing rationale**: No prior completed note is available in this fixture.
- **Idea fuel**: Revisit after more papers are read.
"""


def test_manifest_distinguishes_complete_incomplete_and_missing_notes(tmp_path: Path):
    workspace = tmp_path / "ws"
    notes_dir = workspace / "literature" / "paper_notes"
    notes_dir.mkdir(parents=True)
    _write_jsonl(
        workspace / "literature" / "deep_read_queue.jsonl",
        [
            {
                "paper_id": "noopenalex::496b8b9485c829bf",
                "normalized_id": "noopenalex__496b8b9485c829bf",
                "title": "Causal-Invariant Cross-Domain Out-of-Distribution Recommendation",
                "queue_rank": 1,
                "target_bucket": "seed",
                "seed_priority": True,
                "queue_reason": "seed_paper",
                "bridge_id": "b1",
                "recalled_by_bridges": ["b1", "b2"],
                "contributed_bridges": ["b2"],
                "core_screen_passed": True,
                "semantic_role": "core",
                "relation_to_project": "method_transfer",
                "is_citation_hub": True,
                "hub_type": "seed_neighbor",
                "hub_score": 101.0,
                "citation_hub_protected_slot": True,
                "has_abstract": True,
                "abstract_chars": 1200,
                "reference_hint_count": 8,
                "has_pdf_url_hint": True,
                "pdf_url_hint_count": 2,
            },
            {
                "paper_id": "noopenalex::d110124f0b8ea8d7",
                "normalized_id": "noopenalex__d110124f0b8ea8d7",
                "title": "Causal Inference with Large Language Model: A Survey",
                "queue_rank": 2,
                "target_bucket": "target",
            },
            {
                "paper_id": "paper3",
                "normalized_id": "paper3",
                "title": "Missing Paper",
                "queue_rank": 3,
                "target_bucket": "target",
            },
        ],
    )
    (notes_dir / "noopenalex__496b8b9485c829bf.md").write_text(
        _valid_note("noopenalex::496b8b9485c829bf"),
        encoding="utf-8",
    )
    (notes_dir / "noopenalex__d110124f0b8ea8d7.md").write_text(
        _valid_note("noopenalex::d110124f0b8ea8d7").replace(
            "\n## 10. Key Quotes\n> \"quote\"\n",
            "\n",
        ),
        encoding="utf-8",
    )

    manifest = build_t3_notes_manifest(workspace)
    entries = target_entries(manifest)

    assert manifest["complete_count"] == 1
    assert manifest["incomplete_count"] == 1
    assert manifest["missing_count"] == 1
    assert [entry["status"] for entry in entries] == ["complete", "incomplete", "missing"]
    assert entries[0]["note_path"] == "literature/paper_notes/noopenalex__496b8b9485c829bf.md"
    assert entries[0]["queue_reason"] == "seed_paper"
    assert entries[0]["bridge_id"] == "b1"
    assert entries[0]["recalled_by_bridges"] == ["b1", "b2"]
    assert entries[0]["contributed_bridges"] == ["b2"]
    assert entries[0]["core_screen_passed"] is True
    assert entries[0]["is_citation_hub"] is True
    assert entries[0]["hub_type"] == "seed_neighbor"
    assert entries[0]["has_abstract"] is True
    assert entries[0]["reference_hint_count"] == 8
    assert entries[0]["has_pdf_url_hint"] is True
    assert "## 10. Key Quotes" in entries[1]["validation_error"]
    diagnostic = format_completion_diagnostics(entries)
    assert "已匹配但结构不合格" in diagnostic
    assert "未找到 note" in diagnostic
    assert (workspace / "literature" / "notes_manifest.json").exists()


@pytest.mark.asyncio
async def test_save_paper_note_uses_queue_rank_and_refreshes_manifest(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature" / "paper_notes").mkdir(parents=True)
    _write_jsonl(
        workspace / "literature" / "deep_read_queue.jsonl",
        [
            {
                "paper_id": "noopenalex::496b8b9485c829bf",
                "normalized_id": "noopenalex__496b8b9485c829bf",
                "title": "Causal-Invariant Cross-Domain Out-of-Distribution Recommendation",
                "queue_rank": 1,
                "target_bucket": "seed",
                "seed_priority": True,
            }
        ],
    )
    policy = WorkspaceAccessPolicy(workspace, ["", "literature/"], ["literature/"])
    tool = SavePaperNoteTool(policy)

    result = await tool.execute(
        queue_rank=1,
        content=_valid_note("noopenalex::496b8b9485c829bf"),
    )

    assert result.ok, result.content
    assert result.data["path"] == "literature/paper_notes/noopenalex__496b8b9485c829bf.md"
    assert (workspace / result.data["path"]).exists()
    manifest = json.loads((workspace / "literature" / "notes_manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete_count"] == 1
    assert manifest["entries"][0]["status"] == "complete"


@pytest.mark.asyncio
async def test_save_paper_note_returns_missing_sections_immediately(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature" / "paper_notes").mkdir(parents=True)
    _write_jsonl(
        workspace / "literature" / "deep_read_queue.jsonl",
        [{"paper_id": "paper1", "normalized_id": "paper1", "queue_rank": 1, "target_bucket": "target"}],
    )
    policy = WorkspaceAccessPolicy(workspace, ["", "literature/"], ["literature/"])
    tool = SavePaperNoteTool(policy)

    result = await tool.execute(queue_rank=1, content="# paper1\n\n- **ID**: paper1\n")

    assert not result.ok
    assert result.error == "note_incomplete"
    assert "缺少必要结构" in result.content
    manifest = json.loads((workspace / "literature" / "notes_manifest.json").read_text(encoding="utf-8"))
    assert manifest["incomplete_count"] == 1
    assert manifest["entries"][0]["status"] == "incomplete"
