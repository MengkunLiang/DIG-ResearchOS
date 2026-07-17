"""Auditable capability profiles for public ResearchOS Skills.

Public Skills used to carry a hand-maintained, often minimal tool list.  That
made otherwise capable workflows fail on ordinary work such as locating a
paper, resolving a DOI, inspecting a directory, or validating a structured
artifact.  Profiles provide a small vocabulary for granting the right group of
registered tools without turning a Skill into an unrestricted shell session.

Profiles add tools only.  ``WorkspaceAccessPolicy`` remains authoritative for
every file read and write, remote acquisition still requires an explicit
identifier/query and a writable destination, and no profile grants ``bash_run``
or ``docker_exec``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..runtime.errors import ConfigurationError


CAPABILITY_PROFILE_TOOLS: dict[str, tuple[str, ...]] = {
    # Available to every public Skill.  These tools are still constrained by
    # that Skill's allowed_read_prefixes, so they cannot inspect other projects.
    "workspace_navigation": ("list_files", "glob_files", "grep_search"),
    "structured_artifacts": ("write_structured_file",),
    "literature_discovery": (
        "multi_source_search",
        "search_papers",
        "fetch_paper_metadata",
        "semantic_scholar_search",
        "semantic_scholar_get_paper",
        "arxiv_search",
        "openalex_search",
        "openalex_get_work",
        "crossref_search",
        "crossref_get_work",
        "elsevier_scopus_search",
        "informs_search",
    ),
    "paper_acquisition": (
        "fetch_paper_pdf",
        "extract_pdf_text",
        "extract_paper_sections",
        "lookup_paper_record",
    ),
    "paper_curation": ("process_seed_paper", "save_paper_note"),
    "literature_processing": (
        "deduplicate_papers",
        "score_papers",
        "expand_queries",
        "filter_by_domain",
        "generate_search_log",
        "enrich_papers",
        "backfill_paper_abstracts",
        "apply_semantic_screening",
        "detect_duplicate_queries",
        "analyze_dedup_rate",
        "build_verified_papers",
        "build_access_audit",
        "build_deep_read_queue",
        "fetch_outgoing_citations",
        "build_domain_map",
        "build_synthesis_workbench",
    ),
    "idea_analysis": (
        "analyze_idea_concentration",
        "compute_idea_novelty_signal",
        "extract_mechanism_tuple",
        "compare_mechanism_tuples",
        "extract_design_rationale_tuple",
        "compare_design_rationale_tuples",
    ),
    "claim_review": ("audit_manuscript_claims", "audit_paper_claims", "audit_writing_craft"),
    "manuscript_planning": (
        "build_manuscript_resource_index",
        "plan_manuscript_sections",
        "plan_manuscript_evidence",
        "build_manuscript_registries",
        "build_alignment_matrix",
        "initialize_manuscript_state",
        "build_section_evidence_supplement",
        "update_manuscript_section_state",
        "assemble_manuscript",
        "build_manuscript_revision_patches",
    ),
    "survey_workflow": (
        "build_survey_state",
        "expand_corpus_for_survey",
        "build_survey_figures",
        "update_survey_section_state",
        "assemble_survey",
        "audit_survey_coverage",
        "bind_survey_review",
        "export_survey_for_ideation",
    ),
    "tex_delivery": ("latex_compile", "prepare_submission_bundle"),
    "external_handoff": (
        "build_experiment_handoff_pack",
        "compile_research_reboost_handoff",
        "build_experiment_evidence_pack",
        "audit_experiment_integrity",
        "map_results_to_claims",
    ),
}


# The public catalog is intentionally explicit.  A profile change here is
# reviewable in one place and shows up in ``list-skills`` / ``describe-skill``.
# Protected external-executor skills are deliberately excluded; they have their
# own executor-side contract and are not part of the public CLI catalog.
DEFAULT_PUBLIC_SKILL_PROFILES: dict[str, tuple[str, ...]] = {
    "citation-graph-explorer": ("literature_discovery", "paper_acquisition", "literature_processing"),
    "citation-library-curator": ("literature_discovery", "paper_acquisition"),
    "citation-provenance-audit": ("claim_review",),
    "claim-evidence-map": ("structured_artifacts", "claim_review"),
    "cross-domain-idea-studio": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
        "literature_processing", "idea_analysis",
    ),
    "domain-synthesis-studio": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
        "literature_processing",
    ),
    "draft-evidence-repair": ("structured_artifacts", "claim_review", "manuscript_planning"),
    "experiment-design-review": ("structured_artifacts", "idea_analysis", "claim_review"),
    "hypothesis-compiler": ("structured_artifacts", "idea_analysis", "claim_review"),
    "idea-fanout-jury": ("structured_artifacts", "literature_discovery", "idea_analysis"),
    "t4-evolution": ("structured_artifacts", "idea_analysis"),
    "literature-comparison-studio": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
        "literature_processing",
    ),
    "literature-evidence-matrix": ("structured_artifacts", "literature_discovery", "paper_acquisition"),
    "literature-evidence-scout": ("literature_discovery", "paper_acquisition", "paper_curation", "literature_processing"),
    "literature-gap-map": ("structured_artifacts", "literature_discovery", "literature_processing"),
    "literature-query-plan": ("structured_artifacts", "literature_discovery", "literature_processing"),
    "literature-resource-scout": ("literature_discovery", "paper_acquisition", "literature_processing"),
    "literature-review-studio": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
        "literature_processing", "survey_workflow",
    ),
    "method-builder": ("structured_artifacts", "idea_analysis", "claim_review"),
    "paper-claim-audit": ("claim_review",),
    "paper-comparison": ("structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation"),
    "paper-compile": ("tex_delivery", "claim_review"),
    "paper-identifier-resolver": ("literature_discovery", "paper_acquisition", "paper_curation"),
    "paper-note-review": ("paper_acquisition", "paper_curation", "claim_review"),
    "paper-outline": ("structured_artifacts", "manuscript_planning", "claim_review"),
    "paper-peer-review": ("claim_review", "manuscript_planning"),
    "paper-polish": ("claim_review", "manuscript_planning"),
    "paper-reading-workbench": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
    ),
    "paper-revision": ("claim_review", "manuscript_planning"),
    "paper-section-evidence": ("paper_acquisition", "paper_curation", "claim_review"),
    "paper-write": ("structured_artifacts", "manuscript_planning", "claim_review", "tex_delivery"),
    "pdf-note-card": ("literature_discovery", "paper_acquisition", "paper_curation"),
    "reference-project-miner": ("literature_discovery",),
    "related-work-builder": ("structured_artifacts", "literature_discovery", "paper_acquisition", "claim_review", "manuscript_planning"),
    "research-landscape-report": ("structured_artifacts", "literature_discovery", "literature_processing"),
    "research-material-ingest": ("paper_acquisition", "paper_curation"),
    "research-reboost": ("structured_artifacts", "claim_review", "external_handoff"),
    "research-scope": ("structured_artifacts", "literature_discovery", "literature_processing"),
    "submission-readiness": ("claim_review", "tex_delivery"),
    "survey-evidence-package": (
        "structured_artifacts", "literature_discovery", "paper_acquisition", "paper_curation",
        "literature_processing", "survey_workflow",
    ),
    "survey-visuals": ("survey_workflow",),
    "venue-fit-review": ("literature_discovery", "claim_review"),
}


def resolve_capability_profiles(skill_name: str, metadata: Mapping[str, object]) -> tuple[str, ...]:
    """Return validated capability profiles for one Skill.

    A Skill may override or extend its catalog defaults with frontmatter:
    ``capability_profiles: [literature_discovery, paper_acquisition]``.  The
    universal navigation profile is always included for public and legacy
    Skills, preserving the same read-policy boundary in every session.
    """

    raw = metadata.get("capability_profiles")
    if raw is None:
        declared: Sequence[object] = DEFAULT_PUBLIC_SKILL_PROFILES.get(skill_name, ())
    else:
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ConfigurationError("capability_profiles must be a list of known profile names")
        declared = raw
    profiles = ["workspace_navigation", *(str(item).strip() for item in declared)]
    normalized: list[str] = []
    for profile in profiles:
        if not profile:
            raise ConfigurationError("capability_profiles must not contain an empty profile name")
        if profile not in CAPABILITY_PROFILE_TOOLS:
            known = ", ".join(sorted(CAPABILITY_PROFILE_TOOLS))
            raise ConfigurationError(f"Unknown capability profile {profile!r}; known profiles: {known}")
        if profile not in normalized:
            normalized.append(profile)
    return tuple(normalized)


def expand_skill_tools(declared_tools: Sequence[str], profiles: Sequence[str]) -> list[str]:
    """Merge explicit and profile tools while retaining deterministic order."""

    merged: list[str] = []
    for name in [*declared_tools, *(tool for profile in profiles for tool in CAPABILITY_PROFILE_TOOLS[profile])]:
        normalized = str(name).strip()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged
