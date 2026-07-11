from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose_service(path: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    return compose["services"]["researchos"]


def test_single_compose_entrypoint_keeps_runtime_contract():
    assert not (REPO_ROOT / "docker-compose.yml").exists()
    assert not (REPO_ROOT / "compose.yaml").exists()
    assert not (REPO_ROOT / "deploy/config").exists()

    service = _compose_service("deploy/compose.yaml")
    env = service["environment"]
    volumes = service["volumes"]

    assert service["working_dir"] == "/app"
    assert service["stdin_open"] is True
    assert service["tty"] is True
    assert service["user"] == "${RESEARCHOS_UID:-0}:${RESEARCHOS_GID:-0}"
    assert env["RESEARCHOS_CONFIG"] == "/app/config/user_settings.yaml"
    assert env["RESEARCHOS_WORKSPACE_ROOT"] == "/app/workspace"
    assert env["RESEARCHOS_HOST_WORKSPACE_ROOT"] == "${RESEARCHOS_HOST_WORKSPACE_ROOT:-./workspace}"

    workspace_mount = volumes[0]
    config_mount = volumes[1]
    assert workspace_mount["type"] == "bind"
    assert workspace_mount["source"] == "../workspace"
    assert workspace_mount["target"] == "/app/workspace"
    assert config_mount["type"] == "bind"
    assert config_mount["source"] == "../config"
    assert config_mount["target"] == "/app/config"
    assert config_mount["read_only"] is True
    assert config_mount["bind"]["create_host_path"] is False

    rendered = (REPO_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in rendered
    assert "privileged:" not in rendered


def test_docker_context_has_single_current_ignore_policy():
    assert (REPO_ROOT / ".dockerignore").exists()
    assert not (REPO_ROOT / "infra/docker/.dockerignore").exists()

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "workspace/" in dockerignore
    assert "workspaces/" in dockerignore
    assert "deploy/workspace/" in dockerignore
    assert "deploy/workspaces/" in dockerignore
    assert "deploy/config/" in dockerignore
    assert ".env" in dockerignore
    assert "tests/manual/" in dockerignore
    assert "!docs/**/*.md" in dockerignore
    assert "!skills/**/*.md" in dockerignore
    assert "!researchos/agent_guidance/**/*.md" in dockerignore


def test_scripts_directory_contains_only_shared_utility_scripts():
    script_names = {path.name for path in (REPO_ROOT / "scripts").glob("*.py")}
    assert not [name for name in script_names if name.startswith("test_")]
    assert not [
        name
        for name in script_names
        if name.startswith(("debug_", "probe_", "check_", "real_debug_"))
    ]

    scripts_readme = (REPO_ROOT / "scripts/README.md").read_text(encoding="utf-8")
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "tests/manual/" in gitignore
    assert "ignored `tests/manual/`" in scripts_readme
    assert "tests/unit/" in scripts_readme
    assert "tests/real/" in scripts_readme


def test_low_level_docker_runner_forwards_documented_provider_envs():
    run_sh = (REPO_ROOT / "infra/docker/run.sh").read_text(encoding="utf-8")
    for env_name in (
        "SILICONFLOW_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "S2_API_KEY",
        "ELSEVIER_API_KEY",
        "ELSEVIER_INSTTOKEN",
        "RESEARCHER_EMAIL",
    ):
        assert env_name in run_sh


def test_docs_index_points_to_scattered_operational_readmes():
    docs_index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for target in ("../deploy/README.md", "../scripts/README.md", "../config/README.md"):
        assert target in docs_index

    for target in ("./docs/project_structure.md", "./deploy/README.md", "./scripts/README.md"):
        assert target in readme
