#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from _common import dump_text_atomic, load_json, output_path, resolve_in_workspace, resolve_workspace, utc_now


def text(value: Any, fallback: str = "Not recorded") -> str:
    if value in (None, "", [], {}):
        return fallback
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) if value else fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def cell(value: Any) -> str:
    return text(value).replace("|", "\\|").replace("\n", " ")


def code_paths(values: Any) -> str:
    items = values if isinstance(values, list) else [values] if values else []
    return "<br>".join(f"`{str(item)}`" for item in items) if items else "Not recorded"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    if rows:
        output.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    else:
        output.append("| " + " | ".join(["No verified records"] + [""] * (len(headers) - 1)) + " |")
    return output


def fmt_number(value: Any) -> str:
    if value is None:
        return "Not recorded"
    try:
        return f"{float(value):.8g}"
    except (TypeError, ValueError):
        return str(value)


def phrase(value: Any, fallback: str = "Not recorded") -> str:
    return text(value, fallback).strip().rstrip(".?!")


def sentence(value: Any, fallback: str = "Not recorded") -> str:
    rendered = text(value, fallback).strip()
    return rendered if rendered.endswith((".", "?", "!")) else rendered + "."


def render(facts: dict[str, Any]) -> str:
    project = facts.get("project_summary", {})
    implementation = facts.get("implementation_summary", {})
    lines = [
        "# Executor Research Report",
        "",
        f"This report converts the final external execution record into a ResearchOS research fact package. Its source fingerprint is `{facts.get('input_fingerprint')}`. The recorded executor outcome is `{facts.get('executor_status')}`. All empirical statements remain bounded by their listed files and conditions.",
        "",
        "## 1. Project Summary",
        "",
        sentence(f"The research question recorded for this execution is {text(project.get('research_question'))}"),
        "The external executor treated this question as fixed unless a change is listed below. It did not infer a new question from favorable results.",
        "",
    ]
    hypotheses = project.get("hypotheses", [])
    if hypotheses:
        lines.append("The formal hypotheses carried into execution were " + "; ".join(f"{item.get('hypothesis_id')}, {phrase(item.get('statement'))}" for item in hypotheses) + ".")
    else:
        lines.append("No formal hypothesis statement could be resolved from the final result pack or handoff contract.")
    contributions = project.get("expected_contributions", [])
    if contributions:
        lines.append("The expected contributions were " + "; ".join(f"{item.get('contribution_id')}, {phrase(item.get('statement'))}" for item in contributions) + ".")
    else:
        lines.append("No explicit expected contribution statement was recorded in the final inputs.")
    work = project.get("completed_work", [])
    lines.extend(["", "The completed-work record covers " + ("; ".join(f"{item.get('workstream')} with status {item.get('status')}" for item in work) if work else "no resolvable completed workstreams") + "."])
    lines.append(text(project.get("plan_comparison_note")))
    changes = project.get("plan_changes", [])
    if changes:
        lines.append("Changes relative to the T4.5 or T5 plan were recorded as " + "; ".join(f"{item.get('change')} because {item.get('reason')}" for item in changes) + ".")
    else:
        lines.append("No explicit plan change was recorded. This statement does not override unresolved gaps listed later in the report.")

    lines.extend([
        "",
        "## 2. Implementation Summary",
        "",
        f"The realized method is {text(implementation.get('method_name'))}. {text(implementation.get('method_summary'))} The selected implementation identifier is `{text(implementation.get('implementation_id'))}`, and its recorded root is `{text(implementation.get('implementation_root'))}`.",
        "",
    ])
    lines.extend(markdown_table(
        ["Module ID", "Module", "Implemented role", "Code paths", "Configuration keys", "Evidence status"],
        [[item.get("module_id"), item.get("name"), item.get("role"), code_paths(item.get("paths")), text(item.get("config_keys")), item.get("support_status")] for item in implementation.get("modules", [])],
    ))
    lines.extend([
        "",
        "The main code entry points are " + code_paths(implementation.get("code_entrypoints")) + ". The core configuration files are " + code_paths(implementation.get("configurations")) + ". The recorded environments are " + code_paths(implementation.get("environments")) + ". Dependency information is " + text(implementation.get("dependencies")) + ".",
        "",
        "The recorded data-processing and execution flow is " + text(implementation.get("data_processing_flow")) + ". Differences from the original design are " + text(implementation.get("design_differences")) + ". Work that remained incomplete is " + text(implementation.get("incomplete_items")) + ". These summaries do not replace the listed code and configuration files.",
        "",
        "## 3. Experiment Inventory",
        "",
        "The inventory below includes every planned or executed experiment resolved from the final result pack. A missing field is shown as Not recorded and is not inferred from neighboring runs.",
        "",
    ])
    lines.extend(markdown_table(
        ["Experiment ID", "Objective", "Hypothesis", "Contribution", "Dataset", "Method", "Baseline", "Configuration", "Random Seeds", "Metrics", "Status", "Result Files", "Log Files", "Figures", "Tables"],
        [[
            item.get("experiment_id"), item.get("objective"), text(item.get("hypotheses")), text(item.get("contributions")),
            text(item.get("datasets")), text(item.get("methods")), text(item.get("baselines")), code_paths(item.get("configurations")),
            text(item.get("random_seeds")), text(item.get("metrics")), item.get("status"), code_paths(item.get("result_files")),
            code_paths(item.get("log_files")), code_paths(item.get("figures")), code_paths(item.get("tables")),
        ] for item in facts.get("experiments", [])],
    ))

    lines.extend([
        "",
        "## 4. Comprehensive Results",
        "",
        "This section reports every structured result recovered from the final aggregate tables. It does not select only favorable comparisons. Statistical significance is stated only when a linked test exists; otherwise the field remains Not recorded.",
        "",
    ])
    result_rows = []
    for item in facts.get("comprehensive_results", []):
        observed = f"{item.get('method')}={fmt_number(item.get('method_mean'))}"
        if item.get("comparator"):
            observed += f"; {item.get('comparator')}={fmt_number(item.get('comparator_mean'))}"
        conditions = f"dataset={item.get('dataset')}; split={item.get('split')}; metric={item.get('metric')} ({item.get('metric_direction')}); protocol={item.get('protocol_fingerprint')}"
        sources = code_paths(item.get("raw_result_files"))
        visuals = code_paths(item.get("table_files", []) + item.get("figure_files", []))
        processing = code_paths(item.get("processing_scripts", []))
        result_rows.append([
            item.get("result_id"), text(item.get("experiment_ids")), observed, item.get("comparison_outcome"), conditions,
            item.get("statistical_test"), sources, visuals, processing, item.get("supports"), item.get("does_not_support"),
        ])
    lines.extend(markdown_table(
        ["Result ID", "Experiment", "Observed values", "Comparison", "Conditions", "Statistical test", "Raw result files", "Figures or tables", "Processing scripts", "Supports", "Does not support"],
        result_rows,
    ))

    lines.extend([
        "",
        "## 5. Claim Support Table",
        "",
        "The following mapping is a preliminary organization performed by the external executor. It does not approve manuscript claims. ResearchOS T8 must adjudicate the final wording and evidence strength.",
        "",
    ])
    lines.extend(markdown_table(
        ["Claim ID", "Proposed Claim", "Supporting Experiment", "Supporting File", "Strength", "Limitation"],
        [[item.get("claim_id"), item.get("proposed_claim"), text(item.get("supporting_experiments")), code_paths(item.get("supporting_files")), item.get("strength"), item.get("limitation")] for item in facts.get("claim_support", [])],
    ))

    lines.extend([
        "",
        "## 6. Verified Literature Additions",
        "",
    ])
    literature = facts.get("verified_literature_additions", [])
    if literature:
        lines.append("Only additions with a verifiable identifier are included. The listed supported point remains limited to the material actually accessed.")
        lines.append("")
        lines.extend(markdown_table(
            ["Title", "Authors", "Year", "Venue", "Verified identifiers", "Supported point", "Material used", "Access level", "BibTeX or reference", "Source paths"],
            [[item.get("title"), item.get("authors"), item.get("year"), item.get("venue"), text(item.get("identifiers")), item.get("supported_point"), item.get("used_material"), item.get("access_level"), item.get("bibtex_or_reference"), code_paths(item.get("source_paths"))] for item in literature],
        ))
    else:
        lines.append("No executor-added literature record satisfied the identifier and support-scope requirements. No unverified citation is transferred by this report.")

    lines.extend([
        "",
        "## 7. Limitations and Open Issues",
        "",
        "The limitations below bound interpretation of the method and experiments. They include failed or incomplete work, missing coverage, resource constraints, potential confounds, and explicit prohibitions on over-claiming.",
        "",
    ])
    issues = facts.get("limitations_and_open_issues", [])
    if issues:
        for item in issues:
            refs = code_paths(item.get("source_refs"))
            lines.append(f"* `{item.get('issue_id')}` concerns {item.get('category')}. {phrase(item.get('description'))}. The supporting record is {refs}.")
    else:
        lines.append("No explicit limitation record was found. This absence must not be interpreted as evidence that the study has no limitations.")

    lines.extend([
        "",
        "## 8. Artifact Index",
        "",
        "Every path below is workspace relative. Experimental results without a resolvable source path are not transferred as observed facts.",
        "",
    ])
    lines.extend(markdown_table(
        ["Category", "Path", "SHA-256", "Size bytes", "Indexed from"],
        [[item.get("category"), f"`{item.get('path')}`", item.get("sha256") or "Generated after fact snapshot", item.get("size_bytes"), item.get("source")] for item in facts.get("artifact_index", [])],
    ))
    lines.extend(["", f"Report generated at {utc_now()} from `{facts.get('facts_fingerprint')}`."])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render executor_research_report.md from source-bound facts.")
    parser.add_argument("--workspace")
    parser.add_argument("--facts", default="external_executor/report/writer_handoff_facts.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    facts = load_json(resolve_in_workspace(ws, args.facts))
    destination = output_path(ws, args.output, "external_executor/executor_research_report.md")
    dump_text_atomic(destination, render(facts))
    print(json.dumps({"path": "external_executor/executor_research_report.md", "sections": 8}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
