"""T1 retrieval-scope contract and legacy Bridge-plan projection.

The research scope is deliberately broader than a Cross-domain Bridge plan.
Core method/theory/evaluation lines belong to normal literature retrieval;
only a researcher-confirmed structural transfer may receive a Bridge ID and
the downstream ``must_explore`` reservation semantics.
"""

from __future__ import annotations

from typing import Any


RETRIEVAL_SCOPE_PLAN_REL_PATH = "literature/retrieval_scope_plan.json"
BRIDGE_DOMAIN_PLAN_REL_PATH = "literature/bridge_domain_plan.json"


def project_bridge_domain_plan(scope: dict[str, Any]) -> dict[str, Any]:
    """Derive the narrow, legacy-compatible Bridge plan from a scope plan."""

    bridges = scope.get("cross_domain_bridges") if isinstance(scope.get("cross_domain_bridges"), list) else []
    domains: list[dict[str, Any]] = []
    for item in bridges:
        if not isinstance(item, dict):
            continue
        priority = str(item.get("priority") or "").strip()
        if priority not in {"must_explore", "should_explore"}:
            continue
        bridge_id = str(item.get("bridge_id") or "").strip()
        name = str(item.get("name") or "").strip()
        why = str(item.get("why") or "").strip()
        queries = [str(query).strip() for query in item.get("queries", []) if str(query).strip()]
        if not (bridge_id and name and why and queries):
            continue
        domains.append(
            {
                "bridge_id": bridge_id,
                "name": name,
                "why": why,
                "priority": priority,
                "queries": queries,
                "source": str(item.get("source") or "auto"),
                "notes": (
                    "source_field=" + str(item.get("source_field") or "")
                    + "; structural_mapping=" + str(item.get("structural_mapping") or "")
                    + "; transfer_question=" + str(item.get("transfer_question") or "")
                ),
            }
        )
    return {
        "semantics": "bridge_domain_plan",
        "source": str(scope.get("source") or "mixed") if domains else "none",
        "bridge_domains": domains,
    }
