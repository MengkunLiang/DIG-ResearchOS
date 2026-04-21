"""测试 skill 查询命令。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from researchos.cli import list_skills_command


@pytest.fixture
def mock_skills_root(tmp_path: Path):
    """创建 mock skills 目录结构。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    # Skill 1: 完整配置
    skill1_dir = skills_root / "example-skill"
    skill1_dir.mkdir()
    skill1_config = {
        "name": "example-skill",
        "description": "An example skill for testing",
        "version": "1.0.0",
        "tools": ["tool1", "tool2"],
        "agents": ["agent1"],
    }
    (skill1_dir / "skill.yaml").write_text(
        yaml.safe_dump(skill1_config), encoding="utf-8"
    )

    # Skill 2: 最小配置
    skill2_dir = skills_root / "minimal-skill"
    skill2_dir.mkdir()
    skill2_config = {
        "name": "minimal-skill",
        "description": "A minimal skill",
    }
    (skill2_dir / "skill.yaml").write_text(
        yaml.safe_dump(skill2_config), encoding="utf-8"
    )

    # Skill 3: 无效配置（缺少 skill.yaml）
    skill3_dir = skills_root / "invalid-skill"
    skill3_dir.mkdir()

    # Skill 4: 损坏的 YAML
    skill4_dir = skills_root / "broken-skill"
    skill4_dir.mkdir()
    (skill4_dir / "skill.yaml").write_text("invalid: yaml: content:", encoding="utf-8")

    return skills_root


def test_list_skills_simple_mode(mock_skills_root: Path, capsys):
    """测试简洁模式列出 skills。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(mock_skills_root)]
    args.verbose = False

    with patch("researchos.cli.load_runtime_settings"):
        result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "Found 2 skill(s)" in captured.out
    assert "example-skill" in captured.out
    assert "minimal-skill" in captured.out
    assert "An example skill for testing" in captured.out


def test_list_skills_verbose_mode(mock_skills_root: Path, capsys):
    """测试详细模式列出 skills。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(mock_skills_root)]
    args.verbose = True

    with patch("researchos.cli.load_runtime_settings"):
        result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "skills:" in captured.out
    assert "example-skill" in captured.out
    assert "version: 1.0.0" in captured.out
    assert "tools:" in captured.out
    assert "tool1" in captured.out


def test_list_skills_no_skills_found(tmp_path: Path, capsys):
    """测试没有找到 skills 的情况。"""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(empty_dir)]
    args.verbose = False

    with patch("researchos.cli.load_runtime_settings"):
        result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "No skills found" in captured.out


def test_list_skills_multiple_roots(tmp_path: Path, capsys):
    """测试从多个根目录加载 skills。"""
    # 创建两个 skills 目录
    root1 = tmp_path / "skills1"
    root1.mkdir()
    skill1_dir = root1 / "skill1"
    skill1_dir.mkdir()
    (skill1_dir / "skill.yaml").write_text(
        yaml.safe_dump({"name": "skill1", "description": "Skill 1"}),
        encoding="utf-8",
    )

    root2 = tmp_path / "skills2"
    root2.mkdir()
    skill2_dir = root2 / "skill2"
    skill2_dir.mkdir()
    (skill2_dir / "skill.yaml").write_text(
        yaml.safe_dump({"name": "skill2", "description": "Skill 2"}),
        encoding="utf-8",
    )

    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(root1), str(root2)]
    args.verbose = False

    with patch("researchos.cli.load_runtime_settings"):
        result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "Found 2 skill(s)" in captured.out
    assert "skill1" in captured.out
    assert "skill2" in captured.out


def test_list_skills_broken_yaml_warning(mock_skills_root: Path, capsys):
    """测试损坏的 YAML 文件会产生警告（verbose 模式）。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(mock_skills_root)]
    args.verbose = True

    with patch("researchos.cli.load_runtime_settings"):
        result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    # 应该只列出有效的 skills
    assert "example-skill" in captured.out
    assert "minimal-skill" in captured.out
    # broken-skill 不应该出现
    assert "broken-skill" not in captured.out


def test_list_skills_default_skills_directory(tmp_path: Path, capsys):
    """测试使用默认 skills 目录。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = None  # 使用默认目录
    args.verbose = False

    with patch("researchos.cli.load_runtime_settings"):
        with patch("researchos.cli.Path") as mock_path:
            # Mock 默认 skills 目录不存在
            mock_default_skills = MagicMock()
            mock_default_skills.exists.return_value = False
            mock_path.return_value.parent.parent.__truediv__.return_value = mock_default_skills

            result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "No skills found" in captured.out
