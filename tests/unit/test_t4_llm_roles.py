from __future__ import annotations

import json

import pytest

from researchos.ideation.llm_roles import LLMIdeaGenerator, LLMJsonRoleInvoker, _parse_json_object
from researchos.ideation.models import T4RunConfig


@pytest.mark.asyncio
async def test_generator_opportunity_role_uses_json_only_contract():
    calls = []

    async def fake_call(system: str, user: str) -> str:
        calls.append((system, user))
        return json.dumps(
            {
                "opportunities": [
                    {
                        "opportunity_id": "O1",
                        "type": "mechanism_gap",
                        "one_line_summary": "A bounded fixture opportunity.",
                        "question": "Which mechanism is testable?",
                        "why_it_matters": "The hypothesis needs a discriminating test.",
                        "compatible_routes": ["evidence_routed_literature"],
                    },
                    {
                        "opportunity_id": "O2",
                        "type": "failure_boundary",
                        "one_line_summary": "A fixture boundary opportunity.",
                        "question": "Where does the mechanism fail?",
                        "why_it_matters": "A boundary prevents overclaiming.",
                        "compatible_routes": ["informed_brainstorm"],
                    },
                    {
                        "opportunity_id": "O3",
                        "type": "evaluation_blind_spot",
                        "one_line_summary": "A fixture evaluation opportunity.",
                        "question": "Which outcome distinguishes the mechanism?",
                        "why_it_matters": "The result requires falsification.",
                        "compatible_routes": ["evidence_routed_literature"],
                    },
                ]
            }
        )

    generator = LLMIdeaGenerator(LLMJsonRoleInvoker(call=fake_call))
    opportunities = await generator.plan_opportunities(
        evidence_summary={"atom_count": 1},
        run_config=T4RunConfig(),
    )
    assert [item.opportunity_id for item in opportunities] == ["O1", "O2", "O3"]
    assert "do not score" in calls[0][0].casefold()
    assert "atom_count" in calls[0][1]


def test_role_json_parser_rejects_markdown_prose_and_accepts_fenced_json():
    assert _parse_json_object("```json\n{\"ok\": true}\n```") == {"ok": True}
    with pytest.raises(ValueError, match="JSON object"):
        _parse_json_object("Here is the result: {\"ok\": true}")
