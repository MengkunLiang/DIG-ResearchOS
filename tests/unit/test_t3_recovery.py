from __future__ import annotations

import json
from pathlib import Path

from researchos.runtime.agent import Agent, AgentResult, AgentSpec, ExecutionContext
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.t3_recovery import prepare_t3_resume_artifacts
from researchos.testing.mocks import MockHumanInterface, MockLLMClient
from researchos.tools.registry import ToolRegistry


def _screen() -> dict:
    return {
        "relation_to_project": "baseline_or_dataset_relevance",
        "role": "baseline",
        "confidence": "medium",
        "bridge_id": None,
        "can_enter_core": False,
        "can_enter_deep_read": True,
        "rationale": "LLM screening keeps this legacy dedup record eligible for T3 recovery.",
        "evidence_fields_used": ["title", "abstract"],
    }


class _T3Agent(Agent):
    def __init__(self) -> None:
        super().__init__(AgentSpec(name="reader", model_tier="medium", tool_names=[]))

    def system_prompt(self, ctx):
        return "reader"

    def initial_user_message(self, ctx):
        return "read papers"


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
- **Rationale**: The paper argues that targeted feature extraction is the right design because generic representations miss task-specific signals.
- **Rationale evidence**: The rationale is supported by the ablation in Table 2 and the qualitative error analysis.
- **Rationale weakness**: The rationale may depend on datasets where task-specific signals are stable.

## 15. Artifact & Design Principles
- **Artifact type**: method
- **Artifact description**: A representation learning method with targeted feature extraction.
- **Design principles**: Match perturbations and feature selection to the target task structure.

## 16. Data View & Evaluation Mode
- **Data view**: Benchmark datasets with labeled outcomes and held-out evaluation.
- **Evaluation mode**: summative benchmark evaluation with ablation-supported mechanism checks.
- **Validity concern**: External validity is limited outside the benchmark distribution.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: The paper improves an existing representation learning pipeline rather than introducing a new artifact class.
- **Why not routine**: The design rationale changes where task-specific structure enters the method.

## 18. Boundary Conditions
- **Works when**: Task-specific features are stable and observable in training data.
- **May fail when**: The target task has rapidly changing or unobserved features.
- **Untested boundary**: Cross-domain transfer remains untested.

## 19. Cross-Paper Tension
- **Tension**: none
- **Competing rationale**: No prior completed note is available in this fixture.
- **Idea fuel**: Revisit after more papers are read to compare task-specific and generic representation rationales.
"""


def test_prepare_t3_resume_artifacts_builds_pending_queue_from_dedup(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper1.md").write_text(_valid_note("paper1"), encoding="utf-8")

    _write_jsonl(
        literature / "papers_dedup.jsonl",
        [
            {
                "id": "paper1",
                "title": "Paper 1",
                "year": 2025,
                "source": "arxiv",
                "relevance_score": 0.91,
                "access_score_estimate": 0.8,
                "access_score": 0.8,
                "evidence_level": "PARTIAL_TEXT",
                "semantic_screen": _screen(),
            },
            {
                "id": "paper2",
                "title": "Paper 2",
                "year": 2025,
                "source": "openalex",
                "relevance_score": 0.89,
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "ABSTRACT_ONLY",
                "semantic_screen": _screen(),
            },
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_queue = literature / "deep_read_queue_pending.jsonl"
    full_queue = literature / "deep_read_queue.jsonl"
    assert full_queue.exists()
    assert pending_queue.exists()
    assert info["existing_note_count"] == 1
    assert info["resume_queue_count"] == 1
    assert info["resume_queue_source"] == "papers_dedup"
    pending_records = [json.loads(line) for line in pending_queue.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(pending_records) == 1
    assert pending_records[0]["paper_id"] == "paper2"


def test_prepare_t3_resume_artifacts_filters_existing_queue(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "seed_paper.md").write_text(_valid_note("seed_paper"), encoding="utf-8")

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {"paper_id": "seed_paper", "normalized_id": "seed_paper", "queue_rank": 1, "title": "Seed"},
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert info["resume_queue_source"] == "deep_read_queue"
    assert info["resume_queue_count"] == 1
    assert pending_records[0]["paper_id"] == "paper2"
    assert pending_records[0]["queue_rank"] == 1


def test_prepare_t3_resume_artifacts_matches_note_internal_ids_and_titles(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "Causal Intervention-Based Memory Selection for Long-Horizon LLM Agents.md").write_text(
        _valid_note("arxiv:2605.17641"),
        encoding="utf-8",
    )

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {
                "paper_id": "arxiv:2605.17641",
                "normalized_id": "arxiv_2605.17641",
                "queue_rank": 1,
                "title": "Causal Intervention-Based Memory Selection for Long-Horizon LLM Agents",
            },
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta = json.loads((literature / "deep_read_queue_pending_meta.json").read_text(encoding="utf-8"))
    assert info["existing_note_count"] >= 1
    assert info["resume_queue_count"] == 1
    assert pending_records[0]["paper_id"] == "paper2"
    assert meta["valid_note_file_count"] == 1
    assert meta["invalid_note_file_count"] == 0


def test_prepare_t3_resume_artifacts_matches_seed_title_punctuation_variants(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "From Feature Interaction to Feature Generation_ A Generative Paradigm of CTR Prediction Models.md").write_text(
        _valid_note("From Feature Interaction to Feature Generation: A Generative Paradigm of CTR Prediction Models"),
        encoding="utf-8",
    )

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {
                "paper_id": "From Feature Interaction to Feature Generation: A Generative Paradigm of CTR Prediction Models",
                "normalized_id": "From Feature Interaction to Feature Generation_ A Generative Paradigm of CTR Prediction Models",
                "queue_rank": 1,
                "title": "From Feature Interaction to Feature Generation: A Generative Paradigm of CTR Prediction Models",
                "seed_priority": True,
            },
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert info["existing_note_count"] == 1
    assert info["resume_queue_count"] == 1
    assert pending_records[0]["paper_id"] == "paper2"


def test_prepare_t3_resume_artifacts_keeps_incomplete_notes_pending(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper1.md").write_text("# old incomplete note", encoding="utf-8")

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {"paper_id": "paper1", "normalized_id": "paper1", "queue_rank": 1, "title": "Paper 1"},
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert info["resume_queue_count"] == 2
    assert [item["paper_id"] for item in pending_records] == ["paper1", "paper2"]


def test_prepare_t3_resume_artifacts_treats_chunked_full_text_note_complete(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    chunked_note = (
        _valid_note("paper1")
        .replace("- **Pages read**: 1-10 / 10", "- **Pages read**: 1-4, 5-8, 9-10 / 10")
        .replace(
            "- **Extraction calls**: extract_pdf_text pages 1-10",
            "- **Extraction calls**: extract_pdf_text pages 1-10 truncated, then pages 1-4, 5-8, 9-10",
        )
        .replace(
            "- **Truncation**: none",
            "- **Truncation**: 第一次调用被截断；分块重读覆盖全部页面，最终未截断",
        )
    )
    (notes_dir / "paper1.md").write_text(chunked_note, encoding="utf-8")

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {"paper_id": "paper1", "normalized_id": "paper1", "queue_rank": 1, "title": "Paper 1"},
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert info["existing_note_count"] == 1
    assert info["resume_queue_count"] == 1
    assert [item["paper_id"] for item in pending_records] == ["paper2"]


def test_runner_refreshes_t3_pending_meta_after_any_exit(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper1.md").write_text(_valid_note("paper1"), encoding="utf-8")
    (notes_dir / "paper2.md").write_text(_valid_note("paper2"), encoding="utf-8")

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {"paper_id": "paper1", "normalized_id": "paper1", "queue_rank": 1, "title": "Paper 1"},
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
            {"paper_id": "paper3", "normalized_id": "paper3", "queue_rank": 3, "title": "Paper 3"},
        ],
    )
    (literature / "deep_read_queue_pending_meta.json").write_text(
        json.dumps(
            {
                "source_queue": "deep_read_queue",
                "original_queue_count": 3,
                "completed_note_count": 0,
                "pending_queue_count": 3,
            }
        ),
        encoding="utf-8",
    )

    runner = AgentRunner(_T3Agent(), ToolRegistry(), MockLLMClient([]), MockHumanInterface())
    ctx = ExecutionContext(workspace_dir=workspace, project_id="p1", task_id="T3", run_id="r1")
    runner._maybe_refresh_t3_resume_artifacts(ctx, AgentResult.STOP_MAX_STEPS)

    meta = json.loads((literature / "deep_read_queue_pending_meta.json").read_text(encoding="utf-8"))
    assert meta["completed_note_count"] == 2
    assert meta["pending_queue_count"] == 1
    assert meta["refresh_reason"] == "runner_exit:max_steps"
    assert ctx.extra["resume_queue_count"] == 1
