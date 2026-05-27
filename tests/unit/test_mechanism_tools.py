from __future__ import annotations

import json

import pytest

from researchos.tools.mechanism_tools import (
    ExtractMechanismTupleTool,
    CompareMechanismTuplesTool,
    compare_mechanism_tuples,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def test_compare_mechanism_tuples_returns_hint_not_final_verdict():
    result = compare_mechanism_tuples(
        {
            "source_id": "H1",
            "input_signal": "activity conditioned records",
            "mechanism": "noise regularization improves sparse user embeddings",
        },
        {
            "source_id": "P1",
            "input_signal": "activity conditioned records",
            "mechanism": "noise regularization improves sparse user embeddings",
        },
    )

    assert result["heuristic_verdict"] == "possible_true_collision"
    assert result["requires_llm_judgment"] is True
    assert result["input_similarity_hint"] == 1.0


@pytest.mark.asyncio
async def test_extract_mechanism_tuple_accepts_llm_normalized_signal(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = ExtractMechanismTupleTool(policy)

    result = await tool.execute(
        mechanism="activity-conditioned perturbation regularizes sparse users",
        claimed_effect="better sparse-user recommendation",
        input_signal="user activity distribution",
        normalized_input_signal="user activity / sparsity",
        evidence_type="conceptual argument",
        normalized_evidence_type="theory",
        source_type="hypothesis",
        source_id="H1",
    )

    assert result.ok
    tuple_path = tmp_workspace / "ideation" / "_mechanism_tuples" / "H1.json"
    data = json.loads(tuple_path.read_text(encoding="utf-8"))
    assert data["input_signal"] == "user activity / sparsity"
    assert data["input_signal_normalization_source"] == "llm"
    assert data["evidence_type"] == "theory"


@pytest.mark.asyncio
async def test_extract_mechanism_tuple_preserves_raw_signal_without_builtin_taxonomy(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = ExtractMechanismTupleTool(policy)

    result = await tool.execute(
        mechanism="policy timing changes downstream stability",
        claimed_effect="more stable allocations",
        input_signal="domain-specific policy timing signal",
        evidence_type="claimed_untested",
        source_type="hypothesis",
        source_id="H2",
    )

    assert result.ok
    tuple_path = tmp_workspace / "ideation" / "_mechanism_tuples" / "H2.json"
    data = json.loads(tuple_path.read_text(encoding="utf-8"))
    assert data["input_signal"] == "domain-specific policy timing signal"
    assert data["input_signal_normalization_source"] == "heuristic_hint"
    assert data["evidence_type"] == "claimed_untested"


@pytest.mark.asyncio
async def test_compare_mechanism_tool_attaches_llm_assessment(tmp_workspace):
    tuple_dir = tmp_workspace / "ideation" / "_mechanism_tuples"
    tuple_dir.mkdir(parents=True)
    (tuple_dir / "H1.json").write_text(
        json.dumps(
            {
                "source_id": "H1",
                "input_signal": "user activity / sparsity",
                "mechanism": "activity-conditioned perturbation regularizes sparse users",
            }
        ),
        encoding="utf-8",
    )
    (tuple_dir / "P1.json").write_text(
        json.dumps(
            {
                "source_id": "P1",
                "input_signal": "embedding representations",
                "mechanism": "uniform embedding noise regularizes representations",
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_workspace, ["ideation/"], ["ideation/"])
    tool = CompareMechanismTuplesTool(policy)

    result = await tool.execute(
        tuple_a_path="ideation/_mechanism_tuples/H1.json",
        tuple_b_path="ideation/_mechanism_tuples/P1.json",
        llm_assessment={
            "final_verdict": "mechanism_collision",
            "rationale": "same regularization idea but different conditioning signal",
        },
    )

    assert result.ok
    assert result.data["heuristic_verdict"].startswith("possible_") or result.data["heuristic_verdict"] == "likely_distinct"
    assert result.data["llm_assessment"]["final_verdict"] == "mechanism_collision"
