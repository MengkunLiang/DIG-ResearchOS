from __future__ import annotations

from pathlib import Path

from researchos import cli


def test_detect_environment_warnings_when_conda_shell_and_interpreter_mismatch(monkeypatch, tmp_path: Path):
    conda_prefix = tmp_path / "envs" / "researchos"
    base_prefix = tmp_path / "base"
    other_prefix = tmp_path / "other"
    conda_prefix.mkdir(parents=True)
    base_prefix.mkdir(parents=True)
    other_prefix.mkdir(parents=True)
    shell_python = base_prefix / "bin" / "python"
    shell_researchos = other_prefix / "bin" / "researchos"
    shell_python.parent.mkdir(parents=True)
    shell_researchos.parent.mkdir(parents=True)
    shell_python.write_text("", encoding="utf-8")
    shell_researchos.write_text("", encoding="utf-8")

    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "researchos")
    monkeypatch.setattr(cli.sys, "prefix", str(base_prefix))
    monkeypatch.setattr(cli.sys, "executable", str(base_prefix / "bin" / "python3.11"))

    def fake_which(name: str) -> str | None:
        if name == "python":
            return str(shell_python)
        if name == "researchos":
            return str(shell_researchos)
        return None

    monkeypatch.setattr(cli.shutil, "which", fake_which)

    warnings = cli._detect_environment_warnings()

    assert any("激活的 conda 环境目录" in item for item in warnings)
    assert any("`researchos` 命令来自" in item for item in warnings)
    assert any("conda run -n researchos" in item for item in warnings)


def test_detect_environment_warnings_returns_empty_when_paths_are_consistent(monkeypatch, tmp_path: Path):
    conda_prefix = tmp_path / "envs" / "researchos"
    conda_prefix.mkdir(parents=True)
    env_python = conda_prefix / "bin" / "python"
    env_researchos = conda_prefix / "bin" / "researchos"
    env_python.parent.mkdir(parents=True)
    env_python.write_text("", encoding="utf-8")
    env_researchos.write_text("", encoding="utf-8")

    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "researchos")
    monkeypatch.setattr(cli.sys, "prefix", str(conda_prefix))
    monkeypatch.setattr(cli.sys, "executable", str(env_python))

    def fake_which(name: str) -> str | None:
        if name == "python":
            return str(env_python)
        if name == "researchos":
            return str(env_researchos)
        return None

    monkeypatch.setattr(cli.shutil, "which", fake_which)

    assert cli._detect_environment_warnings() == []
