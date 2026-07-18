#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path
from typing import Any

from _common import canonical_json_hash, dump_json_atomic, file_ref, load_json, resolve_in_workspace, resolve_workspace, utc_now


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "result"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clear_owned_figures(figure_dir: Path) -> list[str]:
    """Remove result plots owned by this renderer while preserving framework/custom assets."""
    removed: list[str] = []
    for prefix in ("main_", "ablation_", "other_"):
        for path in sorted(figure_dir.glob(f"{prefix}*.svg")):
            if path.is_file():
                path.unlink()
                removed.append(path.name)
    return removed


def display_name(row: dict[str, str], kind: str) -> str:
    if kind == "ablation":
        return row.get("variant") or row.get("method_id") or "variant"
    baseline = row.get("baseline_id")
    return baseline or row.get("method_id") or row.get("variant") or "method"


def render_group(rows: list[dict[str, str]], path: Path, *, kind: str, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    matplotlib.rcParams["svg.hashsalt"] = "researchos-evidence-packaging"
    names = [display_name(row, kind) for row in rows]
    means = [float(row["mean"]) for row in rows]
    errors = [float(row.get("std") or 0.0) for row in rows]
    palette = ["#2C6E8F", "#C44E52", "#4C956C", "#8172B2", "#CCB974", "#64B5CD"]
    width = max(6.4, min(13.0, 1.0 + len(rows) * 1.15))
    figure, axis = plt.subplots(figsize=(width, 4.8))
    bars = axis.bar(range(len(rows)), means, yerr=errors, capsize=4, color=[palette[index % len(palette)] for index in range(len(rows))], edgecolor="#263238", linewidth=0.7)
    axis.set_xticks(range(len(rows)), names, rotation=25, ha="right")
    axis.set_ylabel(rows[0].get("metric") or "value")
    axis.set_title(title)
    axis.grid(axis="y", color="#D7DEE2", linewidth=0.8)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, means):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.4g}", ha="center", va="bottom", fontsize=8)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="svg", metadata={"Date": None, "Creator": "ResearchOS evidence-packaging"})
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render deterministic SVG result figures from generated aggregate tables.")
    parser.add_argument("--workspace")
    parser.add_argument("--tables-report", default="external_executor/report/phase_F/result_table_build_report.json")
    parser.add_argument("--figure-dir", default="external_executor/figure")
    parser.add_argument("--output", default="external_executor/report/phase_F/result_figure_build_report.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    table_report = load_json(resolve_in_workspace(workspace, args.tables_report))
    figure_dir = resolve_in_workspace(workspace, args.figure_dir)
    figure_dir.mkdir(parents=True, exist_ok=True)
    removed_stale_outputs = clear_owned_figures(figure_dir)
    figures: list[dict[str, Any]] = []
    warnings: list[str] = []
    for table in table_report.get("tables", []):
        kind = table.get("kind")
        if kind not in {"main", "ablation", "other"}:
            continue
        table_path = resolve_in_workspace(workspace, str(table.get("path")))
        rows = read_rows(table_path)
        groups: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = {}
        for row in rows:
            groups.setdefault((
                row.get("dataset") or "unknown",
                row.get("split") or "unknown",
                row.get("metric") or "unknown",
                row.get("metric_direction") or "unknown",
                row.get("protocol_fingerprint") or "unknown",
            ), []).append(row)
        for (dataset, split, metric, direction, protocol_fingerprint), records in sorted(groups.items()):
            if kind == "main":
                roles = {record.get("method_role") for record in records}
                if not {"ours", "baseline"}.issubset(roles):
                    warnings.append(
                        f"main_group_not_comparable:{dataset}:{split}:{metric}:{protocol_fingerprint}"
                    )
                    continue
            if kind == "ablation" and len({display_name(record, kind) for record in records}) < 2:
                warnings.append(
                    f"ablation_group_has_fewer_than_two_variants:{dataset}:{split}:{metric}:{protocol_fingerprint}"
                )
                continue
            name = (
                f"{kind}_{slug(dataset)}_{slug(split)}_{slug(metric)}_"
                f"{slug(protocol_fingerprint)}.svg"
            )
            output_path = figure_dir / name
            title = (
                f"{kind.replace('_', ' ').title()}: {dataset} / {split} / {metric} "
                f"({direction}; protocol {protocol_fingerprint})"
            )
            render_group(records, output_path, kind=kind, title=title)
            figures.append({
                "figure_id": f"FIG-{slug(name)}",
                "kind": kind,
                "dataset": dataset,
                "split": split,
                "metric": metric,
                "metric_direction": direction,
                "protocol_fingerprint": protocol_fingerprint,
                "source_table_ref": table.get("path"),
                "plot_script_ref": "evidence-packaging/scripts/render_result_figures.py",
                "caption_draft": f"{metric} comparison on {dataset} ({split}); bars show means and error bars show sample standard deviations when repeats are available.",
                **file_ref(workspace, output_path, evidence_level="derived_figure"),
            })
    report = {
        "schema_version": "result_figure_build_report.v1",
        "generated_at": utc_now(),
        "snapshot_id": table_report.get("snapshot_id"),
        "snapshot_fingerprint": table_report.get("snapshot_fingerprint"),
        "status": "complete" if figures and not warnings else ("partial" if figures else "unavailable"),
        "source_table_report": args.tables_report,
        "figures": figures,
        "warnings": warnings,
        "removed_stale_outputs": removed_stale_outputs,
        "rendering": {
            "format": "svg",
            "network_access": False,
            "reads_generated_tables_only": True,
            "error_bars": "sample_std",
            "mixed_metric_axes_forbidden": True,
        },
        "report_fingerprint": None,
    }
    report["report_fingerprint"] = canonical_json_hash({key: value for key, value in report.items() if key != "report_fingerprint"})
    dump_json_atomic(resolve_in_workspace(workspace, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
