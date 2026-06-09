from __future__ import annotations

"""Paths for versioned system configuration files.

These files describe ResearchOS workflow contracts and writing schemas. They
are intentionally separate from user-facing runtime settings.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG_DIR = REPO_ROOT / "config" / "system_config"
LEGACY_CONFIG_DIR = REPO_ROOT / "config"


def system_config_path(name: str) -> Path:
    """Return the preferred system config path, with legacy fallback."""

    preferred = SYSTEM_CONFIG_DIR / name
    if preferred.exists():
        return preferred
    legacy = LEGACY_CONFIG_DIR / name
    if legacy.exists():
        return legacy
    return preferred


def system_config_path_for(config_dir: Path, name: str) -> Path:
    """Return a system config path under an arbitrary config directory.

    Tests and downstream deployments sometimes pass a temporary ``config``
    directory rather than the repository default. Prefer the new
    ``system_config`` subdirectory there, but keep the old flat layout as a
    compatibility fallback.
    """

    config_dir = config_dir.resolve()
    preferred = config_dir / "system_config" / name
    if preferred.exists():
        return preferred
    legacy = config_dir / name
    if legacy.exists():
        return legacy
    if config_dir == LEGACY_CONFIG_DIR.resolve():
        return system_config_path(name)
    return preferred
