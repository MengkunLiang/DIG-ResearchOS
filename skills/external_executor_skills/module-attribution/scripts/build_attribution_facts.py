#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import canonical_hash, dump_json_atomic, load_json, stable_id, utc_now


def status_from_effect(effect: dict) -> str:
    mean = effect.get("mean_effect")
    consistency = effect.get("sign_consistency", 0)
    if mean is None:
        return "unsupported"
    if consistency < 0.75:
        return "mixed"
    if mean > 0:
        return "beneficial"
    if mean < 0:
        return "harmful"
    return "neutral"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic module attribution facts.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--module-registry", required=True)
    parser.add_argument("--ablation-effects", required=True)
    parser.add_argument("--interaction-analysis", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_json(Path(args.snapshot).expanduser().resolve())
    registry = load_json(Path(args.module_registry).expanduser().resolve())
    effects = load_json(Path(args.ablation_effects).expanduser().resolve())
    interaction = load_json(Path(args.interaction_analysis).expanduser().resolve())
    effects_by_module = {}
    for item in effects.get("items", []):
        effects_by_module.setdefault(str(item.get("module_id")), []).append(item)
    module_facts = []
    for module in registry.get("items", []):
        mid = str(module.get("module_id"))
        module_effects = effects_by_module.get(mid, [])
        statuses = {status_from_effect(x) for x in module_effects}
        if not module_effects:
            empirical = "implementation_only" if module.get("implementation_status") == "implemented" else "unsupported"
            evidence_type = "implementation_fact" if empirical == "implementation_only" else "unsupported"
            refs = module.get("source_refs", [])
        elif len(statuses) == 1:
            empirical = next(iter(statuses)); evidence_type = "direct_ablation"; refs = [x.get("effect_id") for x in module_effects]
        else:
            empirical = "mixed"; evidence_type = "direct_ablation"; refs = [x.get("effect_id") for x in module_effects]
        module_facts.append({
            "module_fact_id": stable_id("MFACT", mid, snapshot.get("input_fingerprint")), "module_id": mid,
            "owner_method_id": module.get("owner_method_id"), "empirical_status": empirical, "evidence_type": evidence_type,
            "effect_ids": [x.get("effect_id") for x in module_effects], "evidence_refs": refs,
            "tested_settings": [x.get("setting_key") for x in module_effects],
            "implementation_status": module.get("implementation_status"), "mechanism_ids": module.get("mechanism_ids", []),
        })
    mechanism_facts = []
    facts_by_module = {x["module_id"]: x for x in module_facts}
    for mech in registry.get("mechanisms", {}).get("items", []):
        linked = [facts_by_module[x] for x in mech.get("linked_module_ids", []) if x in facts_by_module]
        statuses = {x.get("empirical_status") for x in linked}
        if statuses & {"beneficial", "harmful", "mixed", "neutral"}:
            status = "consistent"; evidence_type = "direct_ablation"
        elif linked:
            status = "unresolved"; evidence_type = "implementation_fact"
        else:
            status = "unresolved"; evidence_type = "unsupported"
        mechanism_facts.append({
            "mechanism_fact_id": stable_id("MECHFACT", mech.get("mechanism_id"), snapshot.get("input_fingerprint")),
            "mechanism_id": mech.get("mechanism_id"), "linked_module_ids": mech.get("linked_module_ids", []),
            "status": status, "evidence_type": evidence_type,
            "evidence_refs": [r for x in linked for r in x.get("evidence_refs", [])],
            "note": "Module effects establish mechanism consistency at most; final mechanism status requires interpretation and alternative-explanation review.",
        })
    payload = {
        "schema_version": "module_attribution_facts.v1", "generated_at": utc_now(),
        "iteration_id": snapshot.get("iteration_id"), "diagnosis_id": snapshot.get("diagnosis_id"),
        "input_fingerprint": canonical_hash({"snapshot": snapshot.get("input_fingerprint"), "registry": registry, "effects": effects, "interaction": interaction}),
        "module_registry": {"status": registry.get("status"), "items": registry.get("items", [])},
        "mechanism_registry": registry.get("mechanisms", {"status": "partial", "items": []}),
        "intervention_effects": {"status": effects.get("status"), "items": effects.get("items", [])},
        "interaction_effects": interaction.get("interaction_effects", {"status": "partial", "items": []}),
        "confounds": interaction.get("confounds", {"status": "complete", "items": []}),
        "module_facts": {"status": "complete", "items": module_facts},
        "mechanism_facts": {"status": "complete", "items": mechanism_facts},
        "unsupported_interactions": interaction.get("unsupported_interactions", []),
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(f"module_facts={len(module_facts)} mechanism_facts={len(mechanism_facts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
