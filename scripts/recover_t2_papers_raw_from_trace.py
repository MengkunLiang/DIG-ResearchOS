#!/usr/bin/env python3
from __future__ import annotations

"""从 T2 trace 恢复 literature/papers_raw.jsonl。"""

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from researchos.tools.paper_save_tools import _transform_to_papers_raw


SEARCH_TOOL_NAMES = {
    "multi_source_search",
    "search_papers",
    "semantic_scholar_search",
    "arxiv_search",
    "openalex_search",
    "crossref_search",
}


def _default_output_path(trace_path: Path) -> Path:
    # <workspace>/_runtime/traces/<run>.jsonl -> <workspace>/literature/papers_raw.jsonl
    workspace_dir = trace_path.parent.parent.parent
    return workspace_dir / "literature" / "papers_raw.jsonl"


def recover_papers(trace_path: Path) -> list[dict]:
    recovered: list[dict] = []
    seen_ids: set[str] = set()

    for raw_line in trace_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        if event.get("type") != "message":
            continue

        payload = event.get("payload", {})
        if payload.get("role") != "tool":
            continue
        if payload.get("name") not in SEARCH_TOOL_NAMES:
            continue

        metadata = payload.get("metadata") or {}
        data = metadata.get("data") or {}
        papers = data.get("papers")
        if not isinstance(papers, list):
            continue

        for paper in papers:
            transformed = _transform_to_papers_raw(paper)
            paper_id = transformed.get("id", "")
            if paper_id and paper_id in seen_ids:
                continue
            if paper_id:
                seen_ids.add(paper_id)
            recovered.append(transformed)

    return recovered


def write_jsonl(path: Path, papers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(paper, ensure_ascii=False) for paper in papers]
    content = "\n".join(lines)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover T2 papers_raw.jsonl from a trace file")
    parser.add_argument("trace", type=Path, help="Path to T2 trace JSONL")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path; defaults to <workspace>/literature/papers_raw.jsonl",
    )
    args = parser.parse_args()

    trace_path = args.trace.resolve()
    output_path = args.output.resolve() if args.output else _default_output_path(trace_path)

    papers = recover_papers(trace_path)
    write_jsonl(output_path, papers)

    print(f"Recovered {len(papers)} papers")
    print(f"Wrote: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
