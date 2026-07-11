from __future__ import annotations

"""Paths for versioned system configuration files.

These files describe ResearchOS workflow contracts and writing schemas. They
are intentionally separate from user-facing runtime settings.
"""

from pathlib import Path
import os


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG_DIR = REPO_ROOT / "config" / "system_config"
LEGACY_CONFIG_DIR = REPO_ROOT / "config"


def _candidate_config_dirs() -> list[Path]:
    """Return config roots in deployment-friendly priority order."""

    candidates: list[Path] = []
    explicit_system = os.getenv("RESEARCHOS_SYSTEM_CONFIG_DIR", "").strip()
    if explicit_system:
        candidates.append(Path(explicit_system).parent)
    for env_name in ("RESEARCHOS_CONFIG", "RESEARCHOS_RUNTIME_CONFIG"):
        value = os.getenv(env_name, "").strip()
        if value:
            candidates.append(Path(value).parent)
    candidates.extend(
        [
            Path.cwd() / "config",
            Path("/app/config"),
            LEGACY_CONFIG_DIR,
        ]
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def system_config_path(name: str) -> Path:
    """Return the preferred system config path, with legacy fallback."""

    explicit_system = os.getenv("RESEARCHOS_SYSTEM_CONFIG_DIR", "").strip()
    if explicit_system:
        preferred = Path(explicit_system) / name
        if preferred.exists():
            return preferred

    for config_dir in _candidate_config_dirs():
        preferred = config_dir / "system_config" / name
        if preferred.exists():
            return preferred
        legacy = config_dir / name
        if legacy.exists():
            return legacy
    return SYSTEM_CONFIG_DIR / name


def config_file_path(name: str, *, env_var: str | None = None) -> Path:
    """Return a top-level ResearchOS config file path with deployment fallbacks."""

    if env_var:
        explicit = os.getenv(env_var, "").strip()
        if explicit:
            return Path(explicit)

    for config_dir in _candidate_config_dirs():
        candidate = config_dir / name
        if candidate.exists():
            return candidate
    return LEGACY_CONFIG_DIR / name


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
