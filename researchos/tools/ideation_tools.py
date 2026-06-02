from __future__ import annotations

"""Soft-signal tools for T4 ideation.

These tools produce diagnostic hints for the LLM/user. They intentionally do
not return pass/fail decisions, because novelty and contribution quality remain
scientific judgments.
"""

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


class AnalyzeIdeaConcentrationParams(BaseModel):
    scorecard_path: str = Field(default="ideation/idea_scorecard.yaml")
    output_path: str = Field(default="ideation/_idea_concentration_report.json")
    scorecard: dict[str, Any] | None = Field(
        default=None,
        description="Optional parsed scorecard; if supplied it overrides scorecard_path.",
    )


class ComputeIdeaNoveltySignalParams(BaseModel):
    idea: dict[str, Any] = Field(..., description="Idea object or scorecard item.")
    domain_map_path: str = Field(default="literature/domain_map.json")
    output_path: str = Field(default="", description="Optional JSON output path.")


class AnalyzeIdeaConcentrationTool(Tool):
    name = "analyze_idea_concentration"
    description = (
        "Analyze whether candidate ideas concentrate around the same prior work, rationale, or origin. "
        "Returns telemetry only; no pass/fail gate."
    )
    parameters_schema = AnalyzeIdeaConcentrationParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AnalyzeIdeaConcentrationParams(**kwargs)
        try:
            scorecard = params.scorecard
            if scorecard is None:
                path = self.policy.resolve_read(params.scorecard_path)
                scorecard = _read_yaml(path) if path.exists() else {}
            report = analyze_idea_concentration(scorecard or {})
            output_path = self.policy.resolve_write(params.output_path)
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"idea concentration analysis failed: {exc}", error="analysis_failed")
        return ToolResult(
            ok=True,
            content=report.get("human_hint", "Idea concentration telemetry generated."),
            data={"path": params.output_path, "report": report},
        )


class ComputeIdeaNoveltySignalTool(Tool):
    name = "compute_idea_novelty_signal"
    description = (
        "Compute a rough citation-graph proximity signal for one idea against domain_map.json. "
        "This is a reference signal only, not a novelty verdict."
    )
    parameters_schema = ComputeIdeaNoveltySignalParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ComputeIdeaNoveltySignalParams(**kwargs)
        try:
            domain_path = self.policy.resolve_read(params.domain_map_path)
            domain_map = json.loads(domain_path.read_text(encoding="utf-8")) if domain_path.exists() else {}
            signal = compute_idea_novelty_signal(params.idea, domain_map)
            if params.output_path:
                output_path = self.policy.resolve_write(params.output_path)
                output_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"idea novelty signal failed: {exc}", error="novelty_signal_failed")
        return ToolResult(ok=True, content=signal["note"], data=signal)


def analyze_idea_concentration(scorecard: dict[str, Any]) -> dict[str, Any]:
    ideas = scorecard.get("ideas", []) if isinstance(scorecard, dict) else []
    prior_counter: Counter[str] = Counter()
    rationale_counter: Counter[str] = Counter()
    origin_counter: Counter[str] = Counter()
    for item in ideas:
        if not isinstance(item, dict):
            continue
        idea = item.get("idea") if isinstance(item.get("idea"), dict) else {}
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        origin = str(source.get("idea_origin") or idea.get("idea_origin") or "unknown").strip() or "unknown"
        origin_counter[origin] += 1

        nearest = item.get("nearest_prior_work") or idea.get("nearest_prior_work") or {}
        if isinstance(nearest, dict):
            work = str(nearest.get("work") or nearest.get("title") or "").strip()
            if work:
                prior_counter[work] += 1
        for baseline in item.get("closest_baselines") or []:
            if isinstance(baseline, dict) and baseline.get("name"):
                prior_counter[str(baseline["name"]).strip()] += 1

        rationale = ""
        cdr_tuple = idea.get("cdr_tuple") if isinstance(idea.get("cdr_tuple"), dict) else {}
        rationale = str(cdr_tuple.get("design_rationale") or idea.get("design_rationale") or "").strip()
        if rationale:
            rationale_counter[_signature(rationale)] += 1

    total = len([item for item in ideas if isinstance(item, dict)])
    top_prior = prior_counter.most_common(5)
    top_rationale = rationale_counter.most_common(5)
    concentration_flags = [
        {"type": "prior_work", "key": key, "count": count}
        for key, count in top_prior
        if total and count >= max(2, int(total * 0.4))
    ]
    concentration_flags.extend(
        {"type": "design_rationale_signature", "key": key, "count": count}
        for key, count in top_rationale
        if total and count >= max(2, int(total * 0.4))
    )
    hint = "候选来源分散度可接受；这只是软提示，不是质量结论。"
    if concentration_flags:
        items = ", ".join(f"{flag['type']}={flag['key']} ({flag['count']})" for flag in concentration_flags[:4])
        hint = f"集中度提示：多个候选依赖相同来源/设计论证 {items}；Gate1 选择时请显式考虑多样性。"
    return {
        "semantics": "idea_concentration_soft_telemetry_not_gate",
        "idea_count": total,
        "origin_distribution": dict(origin_counter),
        "top_prior_work": [{"work": key, "count": count} for key, count in top_prior],
        "top_design_rationale_signatures": [{"signature": key, "count": count} for key, count in top_rationale],
        "concentration_flags": concentration_flags,
        "human_hint": hint,
    }


def compute_idea_novelty_signal(idea: dict[str, Any], domain_map: dict[str, Any]) -> dict[str, Any]:
    idea_text = _idea_text(idea)
    idea_tokens = _tokens(idea_text)
    best_bucket = ""
    best_score = 0
    best_node: dict[str, Any] | None = None
    for bucket in ("core", "adjacent", "boundary"):
        for node in domain_map.get(bucket, []) if isinstance(domain_map, dict) else []:
            if not isinstance(node, dict):
                continue
            node_text = " ".join(str(node.get(key) or "") for key in ("title", "key_rationale_hint", "why_adjacent", "note"))
            score = len(idea_tokens & _tokens(node_text))
            if score > best_score:
                best_score = score
                best_bucket = bucket
                best_node = node
    if best_score >= 3 and best_bucket == "core":
        signal = "marginal_zone"
    elif best_score >= 2 and best_bucket == "adjacent":
        signal = "adjacent_zone"
    elif best_score >= 2 and best_bucket == "boundary":
        signal = "adjacent_zone"
    else:
        signal = "no_nearby_cluster"
    return {
        "signal": signal,
        "nearest_domain_node": {
            "id": best_node.get("id") if best_node else "",
            "title": best_node.get("title") if best_node else "",
            "bucket": best_bucket,
            "token_overlap": best_score,
        },
        "note": "参考信号，非结论；无近邻可能表示高新颖、高风险、表述差异或图谱覆盖不足。",
        "semantics": "citation_graph_novelty_hint_not_gate",
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _signature(text: str) -> str:
    tokens = sorted(_tokens(text))
    return " ".join(tokens[:8]) or "unknown"


def _idea_text(idea_or_item: dict[str, Any]) -> str:
    if not isinstance(idea_or_item, dict):
        return ""
    idea = idea_or_item.get("idea") if isinstance(idea_or_item.get("idea"), dict) else idea_or_item
    source = idea_or_item.get("source") if isinstance(idea_or_item.get("source"), dict) else {}
    cdr_tuple = idea.get("cdr_tuple") if isinstance(idea.get("cdr_tuple"), dict) else {}
    values = []
    for mapping in (idea, source, cdr_tuple):
        for key in (
            "title",
            "pitch",
            "core_claim",
            "target_problem",
            "mechanism",
            "mechanism_family",
            "design_rationale",
            "artifact",
            "trigger_observation",
        ):
            value = mapping.get(key)
            if value:
                values.append(str(value))
    return " ".join(values)


def _tokens(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "based",
        "method",
        "model",
        "paper",
        "using",
        "通过",
        "一个",
        "方法",
        "机制",
    }
    return {
        token
        for token in re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", text.casefold())
        if token not in stop
    }
