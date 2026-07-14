"""Typed, artifact-first primitives for T4 Research Idea Formation & Evolution.

This package is deliberately independent from the external state machine.  The
T4 facade and runtime integrations use these models to persist, validate, and
resume internal evolutionary phases while preserving the existing T4 contract.
"""

from .config import T4EvolutionSettings, load_t4_evolution_settings
from .models import (
    CandidateDossier,
    EvidenceAtom,
    EvidencePermission,
    EvidenceRole,
    EvidenceStatus,
    EvolutionPlan,
    IdeaDirective,
    IdeaFamily,
    IdeaGene,
    IdeaGenome,
    IdeaSeed,
    PopulationSnapshot,
    ReadingLevel,
    RoundArtifact,
    ScoreReport,
    T4InternalState,
    T4RunConfig,
)

__all__ = [
    "CandidateDossier",
    "EvidenceAtom",
    "EvidencePermission",
    "EvidenceRole",
    "EvidenceStatus",
    "EvolutionPlan",
    "IdeaDirective",
    "IdeaFamily",
    "IdeaGene",
    "IdeaGenome",
    "IdeaSeed",
    "PopulationSnapshot",
    "ReadingLevel",
    "RoundArtifact",
    "ScoreReport",
    "T4EvolutionSettings",
    "T4InternalState",
    "T4RunConfig",
    "load_t4_evolution_settings",
]
