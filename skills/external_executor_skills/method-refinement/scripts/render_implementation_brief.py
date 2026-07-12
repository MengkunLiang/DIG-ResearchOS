#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import listify, load_json, resolve_in_workspace, resolve_workspace, write_text_atomic


def bullets(values) -> str:
    items = [str(x) for x in listify(values) if str(x).strip()]
    return "\n".join(f"- {x}" for x in items) if items else "- None declared"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a concise implementation brief from the method spec.")
    parser.add_argument("--workspace")
    parser.add_argument("--spec", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--review", default="external_executor/method_refinement_review.json")
    parser.add_argument("--output", default="external_executor/method_implementation_brief.md")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    spec = load_json(resolve_in_workspace(ws, args.spec))
    review = load_json(resolve_in_workspace(ws, args.review))

    lines = [
        "# Method Implementation Brief",
        "",
        "> Navigation artifact only. `external_executor/method_implementation_spec.json` is the source of truth.",
        "",
        "## Approval",
        "",
        f"- Review status: `{review.get('review_status')}`",
        f"- Approved for: `{review.get('approved_for')}`",
        f"- Spec ID: `{spec.get('spec_id')}`",
        f"- Spec version: `{spec.get('spec_version')}`",
        f"- Spec fingerprint: `{spec.get('spec_fingerprint')}`",
        f"- Protocol fingerprint: `{spec.get('protocol_fingerprint')}`",
        "",
        "## Scientific contract",
        "",
        f"- Central hypothesis: {spec.get('research_contract', {}).get('central_hypothesis', '')}",
        f"- Contribution type: {spec.get('research_contract', {}).get('contribution_type', '')}",
        f"- Core mechanism: {spec.get('research_contract', {}).get('core_mechanism', '')}",
        "- Claim boundary:",
        bullets(spec.get("research_contract", {}).get("claim_boundary")),
        "",
        "## Modules",
        "",
    ]
    for module in listify(spec.get("modules")):
        if not isinstance(module, dict):
            continue
        lines.extend([
            f"### {module.get('module_id')}: {module.get('name')}",
            "",
            f"- Role: `{module.get('contribution_role')}`",
            f"- Purpose: {module.get('purpose')}",
            f"- Mechanism: {module.get('mechanism_ref')}",
            f"- Code targets: {', '.join(str(x) for x in listify(module.get('code_targets')))}",
            f"- Config keys: {', '.join(str(x.get('key')) for x in listify(module.get('config_keys')) if isinstance(x, dict))}",
            f"- Ablation control: `{module.get('ablation_switch', {}).get('config_key', '')}`",
            "",
        ])
    lines.extend([
        "## Required flows",
        "",
    ])
    for name in ("training_flow", "inference_flow"):
        lines.append(f"### {name.replace('_', ' ').title()}")
        lines.append("")
        for step in listify(spec.get(name)):
            if isinstance(step, dict):
                lines.append(f"{step.get('step')}. {step.get('description')}")
        lines.append("")
    lines.extend([
        "## Acceptance checks",
        "",
        bullets([x.get("assertion") for x in listify(spec.get("acceptance_checks")) if isinstance(x, dict)]),
        "",
        "## Non-contribution engineering",
        "",
        bullets([x.get("description") if isinstance(x, dict) else x for x in listify(spec.get("non_contribution_engineering"))]),
        "",
        "## Open issues",
        "",
        bullets(review.get("required_fixes")),
        "",
        "## Blocking issues",
        "",
        bullets(review.get("blocking_issues")),
        "",
        "Actual code must remain inside the approved iteration change surface and must later pass `code-and-protocol-review`.",
    ])
    write_text_atomic(resolve_in_workspace(ws, args.output), "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
