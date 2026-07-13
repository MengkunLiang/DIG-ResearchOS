from __future__ import annotations

"""Declarative lifecycle metadata for multi-phase standalone Skills.

An integrated Skill is still executed by the regular Skill runtime, but its
research phases are declared separately from an LLM prompt.  This gives the
CLI, session store, and progress tool a shared vocabulary without introducing
nested Skill sessions or hidden child agents.
"""

from dataclasses import dataclass
from typing import Any, Mapping

from ..runtime.errors import ConfigurationError


@dataclass(frozen=True)
class SkillWorkflowPhase:
    """One observable phase of an integrated Skill."""

    phase_id: str
    label: str
    objective: str
    operations: tuple[str, ...]
    human_gate: bool = False


@dataclass(frozen=True)
class SkillWorkflow:
    """Human-facing execution contract for a composed research workflow."""

    kind: str
    summary: str
    phases: tuple[SkillWorkflowPhase, ...]


def parse_skill_workflow(metadata: Mapping[str, Any]) -> SkillWorkflow | None:
    """Parse the optional ``workflow`` block from public Skill frontmatter."""

    raw = metadata.get("workflow")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigurationError("workflow must be a YAML object")
    kind = str(raw.get("kind") or "integrated").strip()
    if kind != "integrated":
        raise ConfigurationError("workflow.kind must be 'integrated'")
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        raise ConfigurationError("workflow.summary must not be empty")
    raw_phases = raw.get("phases")
    if not isinstance(raw_phases, list) or len(raw_phases) < 2:
        raise ConfigurationError("workflow.phases must contain at least two phase objects")

    phases: list[SkillWorkflowPhase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_phases):
        if not isinstance(item, Mapping):
            raise ConfigurationError(f"workflow.phases[{index}] must be a YAML object")
        phase_id = str(item.get("id") or "").strip()
        if not phase_id or not phase_id.replace("_", "").replace("-", "").isalnum():
            raise ConfigurationError(f"workflow.phases[{index}].id must be a simple identifier")
        if phase_id in seen:
            raise ConfigurationError(f"workflow.phases contains duplicate id: {phase_id}")
        seen.add(phase_id)
        label = str(item.get("label") or "").strip()
        objective = str(item.get("objective") or "").strip()
        if not label or not objective:
            raise ConfigurationError(f"workflow.phases[{index}] requires label and objective")
        raw_operations = item.get("operations") or []
        if isinstance(raw_operations, str):
            raw_operations = [raw_operations]
        if not isinstance(raw_operations, list) or not all(isinstance(value, str) and value.strip() for value in raw_operations):
            raise ConfigurationError(f"workflow.phases[{index}].operations must be a non-empty list of strings")
        human_gate = item.get("human_gate", False)
        if not isinstance(human_gate, bool):
            raise ConfigurationError(f"workflow.phases[{index}].human_gate must be a boolean")
        phases.append(
            SkillWorkflowPhase(
                phase_id=phase_id,
                label=label,
                objective=objective,
                operations=tuple(value.strip() for value in raw_operations),
                human_gate=human_gate,
            )
        )
    return SkillWorkflow(kind=kind, summary=summary, phases=tuple(phases))


def workflow_as_session_payload(workflow: SkillWorkflow) -> dict[str, Any]:
    """Return the durable, human-readable session representation."""

    return {
        "kind": workflow.kind,
        "summary": workflow.summary,
        "current_phase": workflow.phases[0].phase_id,
        "phases": [
            {
                "id": phase.phase_id,
                "label": phase.label,
                "objective": phase.objective,
                "operations": list(phase.operations),
                "human_gate": phase.human_gate,
                "status": "pending",
            }
            for phase in workflow.phases
        ],
    }


def workflow_prompt_block(workflow: SkillWorkflow) -> str:
    """Render an explicit lifecycle contract for the executing Skill agent."""

    lines = [
        "# Integrated Workflow Protocol",
        f"- Workflow purpose: {workflow.summary}",
        "- Work through the phases in order. At the start and end of every phase call `update_skill_workflow` so the human can resume without guessing what happened.",
        "- A phase may use deterministic tools, source-returning search tools, and LLM synthesis. Never treat tool hints, abstract-only records, or retrieval coverage as settled scholarly findings.",
        "- If a phase needs decision-critical material, write the focused follow-up request, call `ask_human`, and record the phase as waiting_input or waiting_evidence. Do not silently skip the phase.",
        "- At each human_gate phase, present completed artifacts, evidence boundaries, and concrete options; do not continue into a costly or irreversible next phase without the user's explicit decision.",
        "- Before finish_task, write the declared workflow_manifest JSON. It must contain `phases`: one object for every declared id with `id`, `status` (`completed` or `skipped`), non-empty `summary`, `evidence_boundary` (empty string is allowed), artifact paths, and `next_action`.",
        "",
        "## Declared Workflow Phases",
    ]
    for position, phase in enumerate(workflow.phases, start=1):
        gate = " · Human Gate" if phase.human_gate else ""
        lines.append(f"{position}. {phase.phase_id} · {phase.label}{gate}")
        lines.append(f"   Objective: {phase.objective}")
        lines.append("   Operations: " + " -> ".join(phase.operations))
    return "\n".join(lines) + "\n\n"
