from __future__ import annotations

from researchos.skills.project_specialization.renderer import render_skill_guidance


def test_renderer_moves_uncertain_fields_and_preserves_template_body():
    template = """---
name: demo-skill
description: demo
---

# Demo

Before.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
old
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

After.
"""
    mapping = {
        "guidance": {
            "begin_marker": "<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->",
            "end_marker": "<!-- PROJECT-SPECIFIC-GUIDANCE:END -->",
            "heading": "## Project-Specific Guidance",
            "sections": [
                {"id": "project_focus", "title": "### Project focus"},
                {"id": "project_priorities", "title": "### Project priorities"},
                {"id": "hard_constraints", "title": "### Hard project constraints"},
                {"id": "decision_criteria", "title": "### Project-specific decision and completion criteria"},
                {"id": "detailed_context", "title": "### Detailed project context"},
                {"id": "uncertain_fields", "title": "### Uncertain project fields"},
            ],
        },
        "rendering": {
            "confirmed_statuses": ["confirmed", "confirmed_from_source"],
            "uncertain_status": "uncertain",
            "detail_reference_format": "`<workspace>/external_executor/project_skill_context.yaml#{path}`",
            "runtime_note": "Runtime note.",
        },
        "skills": {
            "demo-skill": {
                "inject": {
                    "project_focus": [
                        {"path": "project.goal", "label": "Goal", "render": "scalar", "required": True}
                    ],
                    "hard_constraints": [
                        {"path": "execution.budget", "label": "Budget", "render": "mapping", "required": True}
                    ],
                },
                "detail_refs": ["project", "execution"],
            }
        },
    }
    context = {
        "project": {"goal": "Build a deterministic compiler"},
        "execution": {"budget": {}},
        "field_metadata": {
            "project.goal": {"status": "confirmed", "sources": ["project.yaml"]},
            "execution.budget": {"status": "uncertain", "sources": [], "note": "Budget missing."},
        },
    }

    rendered = render_skill_guidance(
        skill_name="demo-skill",
        template_text=template,
        context=context,
        specialization=mapping,
    )

    assert rendered.render_errors == []
    assert rendered.template_integrity_errors == []
    assert rendered.required_uncertain_paths == ["execution.budget"]
    assert "- **Goal:** Build a deterministic compiler" in rendered.text
    assert "### Uncertain project fields" in rendered.text
    assert "**Budget** (`execution.budget`): Budget missing." in rendered.text
    assert "Before." in rendered.text
    assert "After." in rendered.text
