"""Rich renderer for T4 Pre-run readiness without internal JSON leakage."""

from __future__ import annotations

from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..ideation.prerun import T4InputInspection
from ..ideation.models import T4RunConfig


def render_t4_prerun(
    inspection: T4InputInspection,
    config: T4RunConfig,
    *,
    console: Console | None = None,
    file: TextIO | None = None,
) -> None:
    """Render the first-use T4 confirmation screen using workspace facts."""

    output = console or Console(file=file, highlight=False)
    status_label = {
        "ready": "READY",
        "ready_with_warnings": "READY WITH WARNINGS",
        "blocked": "BLOCKED",
    }[inspection.status]
    output.print(
        Panel(
            Text(
                "T4 organizes your literature evidence into an Idea Population, then compares and evolves it. "
                "No external novelty claim is made at this stage.",
                overflow="fold",
            ),
            title=f"T4 · Research Idea Formation & Evolution · {status_label}",
            border_style="bright_cyan" if inspection.status != "blocked" else "bright_red",
            expand=True,
        )
    )
    materials = inspection.materials
    table = Table(title="Evidence available", expand=True, show_header=True, header_style="bold cyan")
    table.add_column("Material", ratio=2)
    table.add_column("Status", ratio=1)
    for label, key in (
        ("Core deep-read Paper Cards", "core_deep_cards"),
        ("Core abstract-read Paper Cards", "core_abstract_cards"),
        ("Bridge deep-read Paper Cards", "bridge_deep_cards"),
        ("Bridge abstract-read Paper Cards", "bridge_abstract_cards"),
        ("Synthesis Workbench", "synthesis_workbench"),
        ("Domain Map", "domain_map"),
        ("User Seed Ideas", "user_seed_ideas"),
        ("User Constraints", "user_constraints"),
    ):
        table.add_row(label, str(materials.get(key, "unavailable")))
    output.print(table)
    profile = config.target_profile
    profile_label = {
        "management_is": "UTD / Management & IS",
        "technical_cs": "CCF A / Technical",
        "hybrid": "Hybrid / Cross-disciplinary",
        "custom": "Custom",
    }[profile.profile_type]
    profile_source = ", ".join(profile.inferred_from) if profile.inferred_from else "system default"
    output.print(
        Panel(
            Text(
                f"Suggested orientation: {profile_label}\n"
                f"T4 will emphasize: {', '.join(profile.priority_dimensions[:4]) or 'balanced scientific contribution'}\n"
                f"Source: {profile_source}\n"
                "After choosing a run mode, press Enter to use this suggestion or describe another target in one sentence. "
                "This changes Prompt emphasis, Profile Fit, and final-card ordering only; it never changes evidence facts or citations.",
                overflow="fold",
            ),
            title="Publication Orientation",
            border_style="magenta",
            expand=True,
        )
    )
    if inspection.status == "blocked":
        for issue in inspection.blocking_issues:
            output.print(
                Panel(
                    Text(
                        f"Missing: {issue['artifact']}\nWhy it matters: {issue['why']}\nNext: {issue['how_to_fix']}",
                        overflow="fold",
                    ),
                    title="T4 cannot start yet",
                    border_style="bright_red",
                    expand=True,
                )
            )
        output.print("No T4 model call has been made. You can pause safely and resume after the missing artifact is available.")
        return
    round_description = {
        "quick": "Formation only: build P0, families, and initial independent scoring.",
        "standard": "One complete P0 -> P1 Evolution Round: formation, scoring, Mutation/Crossover, rescoring, and Survival Selection.",
        "deep": "Two complete Evolution Rounds with an additional repair and exploration pass.",
        "auto": "The controller may run up to the configured limit and stops when another round has low expected value.",
    }[config.mode]
    population_line = (
        f"P0: multi-route Seeds -> Idea Families -> independent scoring -> "
        f"{config.max_offspring_per_round} maximum Child slots -> P1: active candidates -> {config.final_top_k} Portfolio candidates"
    )
    output.print(
        Panel(
            Text(
                f"Mode: {config.mode.title()} · {config.rounds} Evolution Round(s)\n"
                f"{round_description}\n"
                f"Population change: {population_line}\n"
                "Estimated time: an estimate based on model availability and evidence volume; all Seeds, Children, scores, and generations are saved for resume and rollback.",
                overflow="fold",
            ),
            title="What this run will do",
            border_style="green",
            expand=True,
        )
    )
    for warning in inspection.warnings:
        output.print(Panel(Text(warning, overflow="fold"), title="Warning · non-blocking", border_style="yellow", expand=True))
    options = Table(title="Choose how to run T4", expand=True, show_header=True, header_style="bold magenta")
    options.add_column("Option", width=5)
    options.add_column("Action", ratio=2)
    options.add_column("Effect", ratio=4)
    options.add_row("1", "Start Standard (recommended)", "One complete P0 -> P1 round. Existing generations are retained and can be rolled back.")
    options.add_row("2", "Run Quick", "Inspect initial Population and Idea Families without creating Children.")
    options.add_row("3", "Run Deep", "Run two Evolution Rounds; takes longer and retains every intermediate generation.")
    options.add_row("4", "Use Auto", "Let the controller stop when an additional round is unlikely to improve the Population.")
    options.add_row("5", "Adjust settings", "Change round, crossover, portfolio, or UI settings. Material search-space changes may require confirmation.")
    options.add_row("6", "Inspect materials", "Read evidence coverage only; no model call and no Population change.")
    options.add_row("7", "Pause", "Save this readiness state and return here on resume.")
    output.print(options)
