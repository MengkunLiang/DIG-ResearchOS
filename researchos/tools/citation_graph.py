from __future__ import annotations

"""Citation graph helpers for T2 literature scouting.

The tools here only collect and organize repeatable metadata signals. They do
not decide scholarly importance, novelty, or final research gaps. Downstream
LLM agents use the resulting domain map as a review scaffold.
"""

from collections import Counter, defaultdict
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


DOMAIN_MAP_SEMANTICS = "domain_map_for_synthesis_and_ideation_not_final_gaps"


class FetchOutgoingCitationsParams(BaseModel):
    openalex_id_or_doi: str = Field(
        ...,
        description="OpenAlex work id (W...), OpenAlex URL, or DOI.",
    )
    max_refs: int = Field(default=60, ge=1, le=200, description="Maximum referenced works to return.")
    max_related: int = Field(default=20, ge=0, le=100, description="Maximum related works to return.")
    max_candidate_papers: int = Field(
        default=20,
        ge=0,
        le=80,
        description=(
            "Maximum one-hop referenced/related OpenAlex works to resolve into lightweight paper "
            "records for papers_raw auto-persistence. Set 0 to return edges only."
        ),
    )


class BuildDomainMapParams(BaseModel):
    papers_verified_path: str = Field(
        default="literature/papers_verified.jsonl",
        description="Workspace-relative JSONL path containing verified paper records.",
    )
    citation_edges_path: str = Field(
        default="literature/citation_edges.json",
        description="Optional JSON/JSONL path containing citation edges or fetch_outgoing_citations results.",
    )
    output_path: str = Field(
        default="literature/domain_map.json",
        description="Workspace-relative JSON path for the domain map.",
    )
    papers_verified: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional verified paper records. If supplied, this overrides papers_verified_path.",
    )
    citation_edges: list[Any] | None = Field(
        default=None,
        description="Optional citation edges or fetch results. If supplied, this overrides citation_edges_path.",
    )
    max_nodes: int = Field(default=120, ge=1, le=300, description="Maximum verified papers to map.")


class FetchOutgoingCitationsTool(Tool):
    name = "fetch_outgoing_citations"
    description = (
        "Fetch one-hop OpenAlex outgoing references and related works for a paper. "
        "This collects citation graph metadata only; it is not a quality or novelty judgment."
    )
    parameters_schema = FetchOutgoingCitationsParams
    timeout_seconds = 30.0

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = FetchOutgoingCitationsParams(**kwargs)
        work_url = _openalex_work_url(params.openalex_id_or_doi)
        if not work_url:
            return ToolResult(
                ok=False,
                content=f"Unsupported OpenAlex/DOI identifier: {params.openalex_id_or_doi}",
                error="unsupported_identifier",
            )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(work_url, params={"mailto": "researchos@example.com"})
                if response.status_code == 404:
                    return ToolResult(
                        ok=True,
                        content=(
                            "OpenAlex work not found; recorded warning and returned empty edges: "
                            f"{params.openalex_id_or_doi}"
                        ),
                        data={
                            "source_id": _normalize_node_id(params.openalex_id_or_doi),
                            "referenced_works": [],
                            "related_works": [],
                            "papers": [],
                            "warnings": ["openalex_404"],
                        },
                    )
                response.raise_for_status()
                work = response.json()
                source_id = _normalize_node_id(work.get("id") or params.openalex_id_or_doi)
                refs = [_normalize_node_id(item) for item in work.get("referenced_works") or []]
                related = [_normalize_node_id(item) for item in work.get("related_works") or []]
                refs = [item for item in refs if item][: params.max_refs]
                related = [item for item in related if item][: params.max_related]
                papers = await _fetch_neighbor_papers(
                    client,
                    source_id=source_id,
                    referenced_works=refs,
                    related_works=related,
                    max_candidate_papers=params.max_candidate_papers,
                )
        except Exception as exc:
            return ToolResult(
                ok=True,
                content=f"Could not fetch citation graph from OpenAlex; returned empty edges with warning: {exc}",
                data={
                    "source_id": _normalize_node_id(params.openalex_id_or_doi),
                    "referenced_works": [],
                    "related_works": [],
                    "papers": [],
                    "warnings": [f"fetch_failed: {exc}"],
                },
            )

        data = {
            "source_id": source_id,
            "referenced_works": refs,
            "related_works": related,
            "papers": papers,
            "query_bucket": "snowball",
            "warnings": [],
        }
        return ToolResult(
            ok=True,
            content=(
                f"Fetched {len(refs)} referenced works, {len(related)} related works, "
                f"and resolved {len(papers)} one-hop candidate papers for {source_id}."
            ),
            data=data,
        )


class BuildDomainMapTool(Tool):
    name = "build_domain_map"
    description = (
        "Build literature/domain_map.json from verified papers and one-hop citation edges. "
        "The output is a mechanical core/adjacent/boundary map for Reader/Ideation/Writer review, not final gaps."
    )
    parameters_schema = BuildDomainMapParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildDomainMapParams(**kwargs)
        try:
            papers = params.papers_verified
            if papers is None:
                papers_path = self.policy.resolve_read(params.papers_verified_path)
                papers = _load_jsonl(papers_path) if papers_path.exists() else []
            edge_payload = params.citation_edges
            if edge_payload is None:
                edge_path = self.policy.resolve_read(params.citation_edges_path)
                edge_payload = _load_edge_payload(edge_path) if edge_path.exists() else []
            output_path = self.policy.resolve_write(params.output_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        domain_map = build_domain_map(
            papers_verified=papers or [],
            citation_edges=edge_payload or [],
            max_nodes=params.max_nodes,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(domain_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return ToolResult(
            ok=True,
            content=(
                f"Wrote {params.output_path}: "
                f"{len(domain_map['core'])} core, {len(domain_map['adjacent'])} adjacent, "
                f"{len(domain_map['boundary'])} boundary nodes."
            ),
            data={"path": params.output_path, "domain_map": domain_map},
        )


def build_domain_map(
    *,
    papers_verified: list[dict[str, Any]],
    citation_edges: list[Any] | None = None,
    max_nodes: int = 120,
) -> dict[str, Any]:
    """Build a lightweight domain map from verified records and citation edges."""

    nodes = [_paper_node(record) for record in papers_verified[:max_nodes] if isinstance(record, dict)]
    nodes = [node for node in nodes if node["id"]]
    if not nodes:
        return {
            "version": "1.0",
            "semantics": DOMAIN_MAP_SEMANTICS,
            "core": [],
            "adjacent": [],
            "boundary": [],
            "citation_edges": [],
            "bucket_assignments": {},
            "warnings": ["no_verified_papers"],
        }

    node_by_id = {node["id"]: node for node in nodes}
    title_to_id = {_normalize_title_key(node["title"]): node["id"] for node in nodes if node.get("title")}
    edges = _extract_edges(citation_edges or [], node_by_id=node_by_id, title_to_id=title_to_id)
    edges.extend(_extract_record_edges(nodes, node_by_id=node_by_id, title_to_id=title_to_id))
    edges = _dedupe_edges(edges)

    degree: Counter[str] = Counter()
    inbound: Counter[str] = Counter()
    adjacency: dict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        degree[left] += 1
        degree[right] += 1
        inbound[right] += 1
        adjacency[left].add(right)
        adjacency[right].add(left)

    bucket_assignments: dict[str, str] = {}
    core: list[dict[str, Any]] = []
    adjacent: list[dict[str, Any]] = []
    boundary: list[dict[str, Any]] = []

    degree_values = [degree[node["id"]] for node in nodes]
    high_degree_threshold = max(2, sorted(degree_values, reverse=True)[min(2, len(degree_values) - 1)] if degree_values else 2)

    for node in nodes:
        node_id = node["id"]
        bucket = _normalize_source_bucket(node.get("source_bucket") or node.get("search_bucket") or node.get("query_bucket"))
        node_degree = int(degree[node_id])
        if bucket in {"adjacent", "adjacent_field", "theory_bridge"}:
            assignment = "adjacent"
        elif bucket == "snowball":
            assignment = "snowball"
        elif bucket == "core" or node_degree >= high_degree_threshold:
            assignment = "core"
        elif bucket == "seed":
            assignment = "seed"
        elif node_degree > 0:
            assignment = "adjacent"
        else:
            assignment = "boundary"
        bucket_assignments[node_id] = assignment

    for node in nodes:
        node_id = node["id"]
        assignment = bucket_assignments[node_id]
        if assignment == "core":
            core.append(
                {
                    "id": node_id,
                    "title": node["title"],
                    "degree": int(degree[node_id]),
                    "inbound_degree": int(inbound[node_id]),
                    "key_rationale_hint": node.get("key_rationale_hint") or "LLM_REVIEW_REQUIRED",
                }
            )
        elif assignment in {"adjacent", "snowball", "seed"}:
            bridges = sorted(
                neighbor
                for neighbor in adjacency.get(node_id, set())
                if bucket_assignments.get(neighbor) in {"core", "seed"}
            )
            adjacent.append(
                {
                    "id": node_id,
                    "title": node["title"],
                    "degree": int(degree[node_id]),
                    "bridges_to_core": bridges,
                    "why_adjacent": node.get("why_relevant") or node.get("source_bucket") or "LLM_REVIEW_REQUIRED",
                }
            )
        else:
            boundary.append(
                {
                    "id": node_id,
                    "title": node["title"],
                    "degree": int(degree[node_id]),
                    "note": "Sparse or isolated in the current retrieved graph; LLM should review whether this is a boundary direction.",
                }
            )

    core.sort(key=lambda item: (-int(item.get("degree") or 0), str(item.get("title", "")).casefold()))
    adjacent.sort(key=lambda item: (-int(item.get("degree") or 0), str(item.get("title", "")).casefold()))
    boundary.sort(key=lambda item: (-int(item.get("degree") or 0), str(item.get("title", "")).casefold()))

    warnings: list[str] = []
    if not edges:
        warnings.append("citation_edges_empty_or_unavailable")
    if not adjacent:
        warnings.append("no_adjacent_nodes_detected")

    return {
        "version": "1.0",
        "semantics": DOMAIN_MAP_SEMANTICS,
        "core": core,
        "adjacent": adjacent,
        "boundary": boundary,
        "citation_edges": [[left, right] for left, right in edges],
        "bucket_assignments": bucket_assignments,
        "warnings": warnings,
        "notes": [
            "Citation edges use outgoing references and related_works only when available.",
            "Core/adjacent/boundary are mechanical review buckets, not final scholarly judgments.",
        ],
    }


async def _fetch_neighbor_papers(
    client: httpx.AsyncClient,
    *,
    source_id: str,
    referenced_works: list[str],
    related_works: list[str],
    max_candidate_papers: int,
) -> list[dict[str, Any]]:
    if max_candidate_papers <= 0:
        return []

    neighbor_ids: list[tuple[str, str]] = []
    seen: set[str] = set()
    for bucket, ids in (("snowball", referenced_works), ("adjacent", related_works)):
        for work_id in ids:
            normalized = _normalize_node_id(work_id)
            if not normalized or normalized in seen or normalized == source_id:
                continue
            seen.add(normalized)
            neighbor_ids.append((normalized, bucket))
            if len(neighbor_ids) >= max_candidate_papers:
                break
        if len(neighbor_ids) >= max_candidate_papers:
            break

    papers: list[dict[str, Any]] = []
    for work_id, bucket in neighbor_ids:
        work_url = _openalex_work_url(work_id)
        if not work_url:
            continue
        try:
            response = await client.get(work_url, params={"mailto": "researchos@example.com"})
            if response.status_code == 404:
                continue
            response.raise_for_status()
            paper = _openalex_work_to_paper(
                response.json(),
                source_bucket=bucket,
                source_id=source_id,
            )
            if paper:
                papers.append(paper)
        except Exception:
            continue
    return papers


def _openalex_work_to_paper(
    work: dict[str, Any],
    *,
    source_bucket: str,
    source_id: str,
) -> dict[str, Any]:
    openalex_id = str(work.get("id") or "").strip()
    paper_id = _normalize_node_id(openalex_id)
    title = str(work.get("title") or "").strip()
    if not paper_id or not title:
        return {}
    authors = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") if isinstance(authorship.get("author"), dict) else {}
        name = str(author.get("display_name") or "").strip()
        if name:
            authors.append(name)
        if len(authors) >= 10:
            break

    doi = str(work.get("doi") or "").strip()
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    venue = "Unknown"
    primary_location = work.get("primary_location")
    if isinstance(primary_location, dict):
        source = primary_location.get("source")
        if isinstance(source, dict):
            venue = str(source.get("display_name") or "Unknown").strip() or "Unknown"

    return {
        "id": paper_id,
        "source": "openalex_snowball",
        "title": title,
        "authors": authors or ["Unknown"],
        "year": work.get("publication_year"),
        "abstract": _abstract_from_openalex(work.get("abstract_inverted_index")),
        "venue": venue,
        "citation_count": int(work.get("cited_by_count") or 0),
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else openalex_id,
        "referenced_works": [_normalize_node_id(item) for item in work.get("referenced_works") or [] if item],
        "related_works": [_normalize_node_id(item) for item in work.get("related_works") or [] if item],
        "search_bucket": "snowball",
        "source_bucket": "adjacent" if source_bucket == "adjacent" else "snowball",
        "adjacent_field": source_bucket == "adjacent",
        "source_query": f"one-hop citation graph from {source_id}",
        "provenance": {
            "source_tool": "fetch_outgoing_citations",
            "source_id": paper_id,
            "source_url": openalex_id,
            "canonical_id": paper_id,
            "id_source": "openalex",
            "snowball_source_id": source_id,
            "snowball_edge_type": "related_work" if source_bucket == "adjacent" else "referenced_work",
        },
    }


def _abstract_from_openalex(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, raw_positions in inverted_index.items():
        if not isinstance(raw_positions, list):
            continue
        for raw_position in raw_positions:
            try:
                position = int(raw_position)
            except (TypeError, ValueError):
                continue
            positions[position] = str(word)
    return " ".join(positions[index] for index in sorted(positions)).strip()


def _openalex_work_url(raw_id: str) -> str:
    value = str(raw_id or "").strip()
    if not value:
        return ""
    base = "https://api.openalex.org/works"
    if value.startswith("https://api.openalex.org/works/"):
        return value
    if value.startswith("https://openalex.org/"):
        return value.replace("https://openalex.org/", f"{base}/")
    if value.startswith("W"):
        return f"{base}/{value}"
    if value.startswith("10."):
        return f"{base}/https://doi.org/{value}"
    if value.startswith("https://doi.org/"):
        return f"{base}/{value}"
    return ""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _load_edge_payload(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payload
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def _paper_node(record: dict[str, Any]) -> dict[str, Any]:
    paper_id = _normalize_node_id(
        record.get("canonical_id")
        or record.get("paper_id")
        or record.get("id")
        or record.get("doi")
        or record.get("title")
    )
    node = dict(record)
    node["id"] = paper_id
    node["title"] = str(record.get("title") or paper_id).strip()
    return node


def _extract_edges(
    payload: list[Any],
    *,
    node_by_id: dict[str, dict[str, Any]],
    title_to_id: dict[str, str],
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for item in payload:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            left = _resolve_node_id(item[0], node_by_id=node_by_id, title_to_id=title_to_id)
            right = _resolve_node_id(item[1], node_by_id=node_by_id, title_to_id=title_to_id)
            if left and right and left != right:
                edges.append((left, right))
            continue
        if not isinstance(item, dict):
            continue
        source = _resolve_node_id(
            item.get("source_id") or item.get("source") or item.get("paper_id") or item.get("id"),
            node_by_id=node_by_id,
            title_to_id=title_to_id,
        )
        if not source:
            continue
        for key in ("referenced_works", "related_works", "references", "related"):
            for target_raw in item.get(key) or []:
                target = _resolve_node_id(target_raw, node_by_id=node_by_id, title_to_id=title_to_id)
                if target and target != source:
                    edges.append((source, target))
    return edges


def _extract_record_edges(
    nodes: list[dict[str, Any]],
    *,
    node_by_id: dict[str, dict[str, Any]],
    title_to_id: dict[str, str],
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for node in nodes:
        source = node["id"]
        for key in ("referenced_works", "related_works", "references", "related"):
            for target_raw in node.get(key) or []:
                target = _resolve_node_id(target_raw, node_by_id=node_by_id, title_to_id=title_to_id)
                if target and target != source:
                    edges.append((source, target))
    return edges


def _dedupe_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for left, right in edges:
        if not left or not right or left == right:
            continue
        key = tuple(sorted((left, right)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((left, right))
    return deduped


def _resolve_node_id(
    value: Any,
    *,
    node_by_id: dict[str, dict[str, Any]],
    title_to_id: dict[str, str],
) -> str:
    if isinstance(value, dict):
        for key in ("canonical_id", "paper_id", "id", "doi", "title"):
            resolved = _resolve_node_id(value.get(key), node_by_id=node_by_id, title_to_id=title_to_id)
            if resolved:
                return resolved
        return ""
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = _normalize_node_id(raw)
    if normalized in node_by_id:
        return normalized
    title_key = _normalize_title_key(raw)
    return title_to_id.get(title_key, "")


def _normalize_node_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://openalex.org/") or text.startswith("https://api.openalex.org/works/"):
        text = text.rstrip("/").split("/")[-1]
    if text.startswith("https://doi.org/"):
        text = text.replace("https://doi.org/", "")
    text = text.replace("doi:", "")
    text = text.replace("arXiv:", "arxiv:")
    text = text.replace("/", "_").replace(":", "_")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("_")


def _normalize_title_key(value: Any) -> str:
    return re.sub(r"\W+", " ", str(value or "").casefold()).strip()


def _normalize_source_bucket(raw: Any) -> str:
    value = str(raw or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "adjacent_field": "adjacent",
        "nearby_field": "adjacent",
        "cross_domain": "adjacent",
        "theory": "adjacent",
        "theory_bridge": "adjacent",
        "seed_paper": "seed",
    }
    return aliases.get(value, value)
