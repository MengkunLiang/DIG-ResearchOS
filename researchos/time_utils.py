"""Shared time-window helpers for retrieval and novelty checks."""

from __future__ import annotations

from datetime import datetime, timezone


def current_utc_year() -> int:
    """Return the current year in UTC.

    Runtime retrieval policies should not bake in a calendar year; tests can
    pass explicit years to callers that expose a `current_year` parameter.
    """

    return datetime.now(timezone.utc).year


def recent_year_from(years_back: int = 1, *, current_year: int | None = None) -> int:
    """Return the inclusive start year for a recent-publication search."""

    year = current_year if current_year is not None else current_utc_year()
    return max(year - max(0, years_back), 1900)


def year_window(
    years_back: int,
    *,
    current_year: int | None = None,
    lag_years: int = 0,
) -> tuple[int, int]:
    """Return an inclusive year window ending at `current_year - lag_years`."""

    year = current_year if current_year is not None else current_utc_year()
    end_year = max(year - max(0, lag_years), 1900)
    start_year = recent_year_from(years_back, current_year=end_year)
    return start_year, end_year


def format_year_window(
    years_back: int,
    *,
    current_year: int | None = None,
    lag_years: int = 0,
) -> str:
    """Return a compact inclusive year range, e.g. `2024-2026`."""

    start_year, end_year = year_window(
        years_back,
        current_year=current_year,
        lag_years=lag_years,
    )
    return f"{start_year}-{end_year}"
