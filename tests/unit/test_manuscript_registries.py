from __future__ import annotations

from researchos.tools.manuscript_registries import (
    build_claim_ledger_seed,
    build_figure_registry_seed,
    validate_claim_ledger,
    validate_figure_registry,
)


def test_build_claim_ledger_seed_preserves_llm_claim_space():
    evidence_plan = {
        "claim_slots": [
            {
                "slot_id": "experiments_main_result",
                "section": "experiments",
                "claim_type": "empirical_result",
                "candidate_evidence": ["experiments/results_summary.json"],
                "citation_pool": ["smith2026"],
                "result_metric_candidates": [
                    {"experiment_id": "main", "metric": "Recall@20", "value": 0.213}
                ],
                "llm_task": "Report only metrics present in result artifacts.",
            }
        ]
    }
    resource_index = {
        "artifacts": [{"path": "experiments/results_summary.json"}],
        "bib_keys": ["smith2026"],
        "result_metrics": [{"experiment_id": "main", "metric": "Recall@20", "value": 0.213}],
    }

    ledger = build_claim_ledger_seed(evidence_plan, resource_index=resource_index)

    claim = ledger["claims"][0]
    assert claim["claim_id"] == "experiments_main_result"
    assert claim["section"] == "experiments"
    assert claim["claim_text"] == ""
    assert claim["status"] == "needs_llm_claim"
    assert claim["support_status"] == "unverified"
    assert claim["evidence_refs"] == ["experiments/results_summary.json"]
    assert claim["citation_pool"] == ["smith2026"]
    assert ledger["semantics"] == "mechanical_claim_ledger_seed_not_final_scientific_judgment"


def test_validate_claim_ledger_catches_fake_support_and_unknown_refs():
    ledger = {
        "global_constraints": {
            "bib_keys": ["known2026"],
            "known_artifacts": ["experiments/results_summary.json"],
        },
        "claims": [
            {
                "claim_id": "c1",
                "section": "experiments",
                "status": "ready",
                "claim_text": "Method improves Recall@20.",
                "support_status": "supported",
                "evidence_refs": ["experiments/missing.json"],
                "verified_evidence_refs": [],
                "metric_refs": [],
                "citation_keys": ["missing2026"],
                "figure_refs": [],
                "table_refs": [],
            }
        ],
    }

    issues = validate_claim_ledger(ledger)

    assert "c1: supported claim has no verified support refs" in issues
    assert "c1: citation key not in bibliography: missing2026" in issues
    assert "c1: evidence artifact not indexed: experiments/missing.json" in issues


def test_build_figure_registry_seed_keeps_caption_for_llm():
    figure_plan = {
        "planned_visuals": [
            {
                "figure_id": "fig:main_results",
                "status": "available_or_generate_from_results",
                "intended_section": "experiments",
                "message_slot": "experiments_main_result",
                "source_artifacts": ["experiments/results_summary.json"],
            },
            {
                "table_id": "tab:related_work",
                "status": "derive_from_literature_table",
                "intended_section": "related_work",
                "source_artifacts": ["literature/comparison_table.csv"],
            },
        ]
    }
    resource_index = {
        "figures": [{"path": "drafts/figures/main_results.png"}],
        "tables": [{"path": "literature/comparison_table.csv"}],
    }

    registry = build_figure_registry_seed(figure_plan, resource_index=resource_index)

    assert [item["visual_id"] for item in registry["visuals"]] == [
        "tab:related_work",
        "fig:main_results",
    ]
    assert registry["visuals"][1]["caption"] == ""
    assert registry["visuals"][1]["kind"] == "figure"
    assert registry["semantics"] == "mechanical_figure_registry_seed_not_visual_generation"


def test_validate_figure_registry_requires_ready_asset_and_caption():
    registry = {
        "visuals": [
            {
                "visual_id": "v1",
                "label": "fig:main_results",
                "kind": "figure",
                "status": "ready",
                "file_path": "",
                "caption": "",
                "source_artifacts": ["experiments/results_summary.json"],
            },
            {
                "visual_id": "v2",
                "label": "bad_table_label",
                "kind": "table",
                "status": "planned",
                "source_artifacts": ["missing.csv"],
            },
        ]
    }

    issues = validate_figure_registry(
        registry,
        known_artifacts=["experiments/results_summary.json"],
    )

    assert "v1: status ready requires file_path" in issues
    assert "v1: status ready requires caption" in issues
    assert "v2: table label should start with tab:" in issues
    assert "v2: source artifact not indexed: missing.csv" in issues
