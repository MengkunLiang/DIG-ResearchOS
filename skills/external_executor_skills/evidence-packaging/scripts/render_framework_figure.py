#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

from _common import (
    dump_json_atomic,
    dump_text_atomic,
    file_ref,
    load_json,
    resolve_in_workspace,
    resolve_workspace,
    xml_escape,
)


def wrap(text: str, width: int = 22) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4] or [text[:width]]


def svg_for(spec: dict) -> str:
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    count = max(1, len(nodes))
    cols = min(4, count)
    rows = math.ceil(count / cols)
    box_w, box_h = 220, 115
    gap_x, gap_y = 80, 90
    margin_x, margin_y = 70, 95
    width = margin_x * 2 + cols * box_w + max(0, cols - 1) * gap_x
    height = margin_y * 2 + rows * box_h + max(0, rows - 1) * gap_y + 100
    pos = {}
    for index, node in enumerate(nodes):
        col = index % cols
        row = index // cols
        x = margin_x + col * (box_w + gap_x)
        y = margin_y + row * (box_h + gap_y)
        pos[node["node_id"]] = (x, y)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#333"/></marker></defs>',
        f'<text x="{width/2}" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="24" font-weight="bold">{xml_escape(spec.get("main_message") or "Realized Method")}</text>',
    ]
    for edge in edges:
        if edge.get("source") not in pos or edge.get("target") not in pos:
            continue
        sx, sy = pos[edge["source"]]
        tx, ty = pos[edge["target"]]
        x1, y1 = sx + box_w / 2, sy + box_h / 2
        x2, y2 = tx + box_w / 2, ty + box_h / 2
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#333" stroke-width="2" marker-end="url(#arrow)"/>')
        if edge.get("label"):
            parts.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2-8}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12">{xml_escape(edge["label"])}</text>')
    for node in nodes:
        x, y = pos[node["node_id"]]
        supported = node.get("empirical_support_status") == "supported"
        fill = "#e8f4ea" if supported else "#f3f3f3"
        border = "#2f6b3f" if supported else "#555"
        parts.append(f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="12" fill="{fill}" stroke="{border}" stroke-width="2"/>')
        lines = wrap(str(node.get("label") or node["node_id"]))
        start_y = y + 35
        for idx, line in enumerate(lines):
            parts.append(f'<text x="{x+box_w/2}" y="{start_y+idx*20}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" font-weight="bold">{xml_escape(line)}</text>')
        support_text = "controlled support" if supported else "implementation fact"
        parts.append(f'<text x="{x+box_w/2}" y="{y+box_h-16}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12">{xml_escape(support_text)}</text>')
    caption = spec.get("caption_draft") or ""
    caption_lines = wrap(caption, width=110)
    base_y = height - 62
    for idx, line in enumerate(caption_lines):
        parts.append(f'<text x="{width/2}" y="{base_y+idx*16}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12">{xml_escape(line)}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def mermaid_for(spec: dict) -> str:
    lines = ["flowchart LR"]
    for node in spec.get("nodes", []):
        label = str(node.get("label") or node["node_id"]).replace('"', "'")
        lines.append(f'  {node["node_id"].replace("-", "_")}["{label}"]')
    for edge in spec.get("edges", []):
        source = str(edge.get("source")).replace("-", "_")
        target = str(edge.get("target")).replace("-", "_")
        label = edge.get("label")
        if label:
            lines.append(f'  {source} -->|{str(label).replace("|", "/")}| {target}')
        else:
            lines.append(f"  {source} --> {target}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a conservative editable Mermaid source and standalone SVG from a validated framework spec.")
    parser.add_argument("--workspace")
    parser.add_argument("--spec", default="external_executor/report/framework_figure_spec.json")
    parser.add_argument("--svg", default="external_executor/figure/framework_figure.svg")
    parser.add_argument("--mermaid", default="external_executor/report/framework_figure.mmd")
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    spec_path = resolve_in_workspace(ws, args.spec)
    spec = load_json(spec_path)
    if spec.get("status") != "ready_for_T7_audit":
        raise SystemExit("Framework spec is not ready_for_T7_audit; refusing to fabricate rendered files")
    if not spec.get("nodes"):
        raise SystemExit("No framework nodes")
    node_ids = {node.get("node_id") for node in spec.get("nodes", [])}
    # `must_not_show` may either exclude an item entirely (dropped/unimplemented)
    # or constrain how an implemented item is visually represented. Only the
    # former blocks rendering; hint-only modules remain visible but neutral.
    forbidden = set()
    for item in spec.get("must_not_show", []):
        reason = str(item.get("reason") or "").lower()
        action = str(item.get("action") or "").lower()
        if action in {"hide", "exclude", "omit"} or any(token in reason for token in ("dropped", "not_in_final", "unimplemented", "removed")):
            if item.get("item"):
                forbidden.add(str(item.get("item")))
    for node in spec.get("nodes", []):
        if node.get("node_id") in forbidden or str(node.get("label")) in forbidden:
            raise SystemExit(f"Forbidden node would be rendered: {node.get('node_id')}")
        if not node.get("code_refs") or not node.get("config_keys"):
            raise SystemExit(f"Node lacks code/config traceability: {node.get('node_id')}")
    for edge in spec.get("edges", []):
        if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
            raise SystemExit(f"Edge references unknown node: {edge}")

    svg_path = resolve_in_workspace(ws, args.svg)
    mmd_path = resolve_in_workspace(ws, args.mermaid)
    dump_text_atomic(svg_path, svg_for(spec))
    dump_text_atomic(mmd_path, mermaid_for(spec))
    if args.write_back:
        spec["editable_source"] = file_ref(ws, mmd_path, evidence_level="method_definition")
        spec["rendered_files"] = [file_ref(ws, svg_path, evidence_level="method_definition")]
        dump_json_atomic(spec_path, spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
