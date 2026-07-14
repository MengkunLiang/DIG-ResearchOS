from __future__ import annotations

import json
from pathlib import Path

from researchos.cli import main
from researchos.runtime.workspace import initialize_workspace


def test_repeated_workspace_initialization_preserves_note_migration_receipt(tmp_path: Path) -> None:
    legacy_note = tmp_path / "literature" / "paper_notes" / "historic.md"
    legacy_note.parent.mkdir(parents=True)
    legacy_note.write_text("# Historic note\n", encoding="utf-8")
    resume = tmp_path / "_runtime" / "resume" / "t3.json"
    resume.parent.mkdir(parents=True)
    resume.write_text('{"paper_notes_dir":"literature/paper_notes"}\n', encoding="utf-8")

    initialize_workspace(tmp_path, create_project_file=False)
    receipt_path = tmp_path / "_runtime" / "migrations" / "note_directory_migration.json"
    first_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    initialize_workspace(tmp_path, create_project_file=False)

    assert json.loads(receipt_path.read_text(encoding="utf-8")) == first_receipt
    assert first_receipt["moved"] == [
        {"from": "literature/paper_notes", "to": "literature/deep_read_notes"}
    ]
    assert (tmp_path / "literature" / "deep_read_notes" / "historic.md").is_file()
    assert not legacy_note.parent.exists()


def test_status_for_workspace_without_pipeline_state_is_a_normal_rich_message(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "ready-workspace"
    initialize_workspace(workspace, create_project_file=False)

    assert main(["--no-banner", "--no-color", "--workspace", str(workspace), "status"]) == 0

    rendered = capsys.readouterr().out
    assert "Workspace 已准备" in rendered
    assert "尚未启动 pipeline" in rendered
    assert "python -m researchos.cli run --workspace" in rendered
    assert "Traceback" not in rendered
