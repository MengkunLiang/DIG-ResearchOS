from pathlib import Path

from researchos.cli import main


def test_cli_validate_hello_outputs(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("Hello, Runtime!", encoding="utf-8")
    state_machine = tmp_path / "state_machine.yaml"
    state_machine.write_text(
        """
initial_state: HELLO
states:
  HELLO:
    outputs:
      hello_file: hello.txt
""".strip(),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--state-machine",
            str(state_machine),
            "validate",
            "--task",
            "HELLO",
        ]
    )

    assert exit_code == 0
