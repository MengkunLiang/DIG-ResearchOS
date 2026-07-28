"""Regression checks for maintained Python module documentation."""

from __future__ import annotations

import ast
from pathlib import Path


def _maintained_modules(repo_root: Path) -> list[Path]:
    """Return application modules and executor command/support modules."""

    return sorted(
        [*repo_root.joinpath("researchos").rglob("*.py"), *repo_root.joinpath("skills").glob("**/scripts/*.py")]
    )


def test_every_maintained_python_module_has_a_meaningful_docstring() -> None:
    """Keep module responsibility visible before imports and runtime setup."""

    repo_root = Path(__file__).resolve().parents[2]
    missing: list[str] = []
    for path in _maintained_modules(repo_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstring = ast.get_docstring(tree, clean=False)
        if not docstring or len(docstring.strip()) < 24:
            missing.append(path.relative_to(repo_root).as_posix())
    assert not missing, "Modules require a meaningful top-level docstring: " + ", ".join(missing)
