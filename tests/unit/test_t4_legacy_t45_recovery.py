from __future__ import annotations

import hashlib

from researchos.ideation.selected_compilation import (
    ensure_t45_pre_novelty_brief,
    validate_legacy_t45_brief_source,
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_t45_migration_preserves_formal_source_and_detects_source_drift(tmp_path):
    ideation = tmp_path / "ideation"
    ideation.mkdir()
    hypotheses = ideation / "hypotheses.md"
    hypotheses.write_text(
        "# Formal legacy hypotheses\n\n## H1\n\nA retained hypothesis.\n\n## H2\n\nA second retained hypothesis.\n",
        encoding="utf-8",
    )
    before = _sha256(hypotheses)

    result = ensure_t45_pre_novelty_brief(tmp_path)

    assert result["mode"] == "legacy_migrated"
    assert _sha256(hypotheses) == before
    assert (ideation / "hypothesis_brief.yaml").is_file()
    assert (ideation / "selected" / "selected_candidate.json").is_file()
    assert (ideation / "selected" / "t45_search_targets.json").is_file()
    assert validate_legacy_t45_brief_source(tmp_path) == (True, None)

    hypotheses.write_text(
        "# Formal legacy hypotheses\n\n## H1\n\nThe source changed after migration.\n",
        encoding="utf-8",
    )

    valid, error = validate_legacy_t45_brief_source(tmp_path)

    assert not valid
    assert error is not None
    assert "changed after the Pre-Novelty migration" in error
