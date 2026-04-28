"""测试 skill 查询命令。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
    (skill1_dir / "SKILL.md").write_text(
        """---
name: example-skill
description: An example skill for testing
tools:
  - tool1
  - tool2
tier: heavy
max_steps: 42
---
Example skill body
""",
        encoding="utf-8",
    )

    # Skill 2: 最小配置
    skill2_dir = skills_root / "minimal-skill"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text(
        """---
name: minimal-skill
description: A minimal skill
---
Minimal body
""",
        encoding="utf-8",
    )

    # Skill 3: 无效配置（缺少 SKILL.md）
    skill3_dir = skills_root / "invalid-skill"
    skill3_dir.mkdir()

    return skills_root


def test_list_skills_simple_mode(mock_skills_root: Path, capsys):
    """测试简洁模式列出 skills。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(mock_skills_root)]
    args.verbose = False

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

    result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "skills:" in captured.out
    assert "example-skill" in captured.out
    assert "model_tier: heavy" in captured.out
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
    (skill1_dir / "SKILL.md").write_text(
        "---\nname: skill1\ndescription: Skill 1\n---\nbody\n",
        encoding="utf-8",
    )

    root2 = tmp_path / "skills2"
    root2.mkdir()
    skill2_dir = root2 / "skill2"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text(
        "---\nname: skill2\ndescription: Skill 2\n---\nbody\n",
        encoding="utf-8",
    )

    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(root1), str(root2)]
    args.verbose = False

    result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "Found 2 skill(s)" in captured.out
    assert "skill1" in captured.out
    assert "skill2" in captured.out


def test_list_skills_ignores_dirs_without_skill_md(mock_skills_root: Path, capsys):
    """测试没有 SKILL.md 的目录会被忽略。"""
    args = MagicMock()
    args.workspace = "/tmp/test"
    args.skills_root = [str(mock_skills_root)]
    args.verbose = True

    result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "example-skill" in captured.out
    assert "minimal-skill" in captured.out
    assert "invalid-skill" not in captured.out


def test_list_skills_default_skills_directory(tmp_path: Path, capsys, monkeypatch):
    """测试默认会扫描 cwd/skills。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    skill_dir = skills_root / "cwd-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cwd-skill\ndescription: From cwd\n---\nbody\n",
        encoding="utf-8",
    )

    args = MagicMock()
    args.workspace = str(tmp_path / "workspace")
    Path(args.workspace).mkdir()
    args.skills_root = None
    args.verbose = False

    monkeypatch.chdir(tmp_path)
    result = list_skills_command(args)

    assert result == 0

    captured = capsys.readouterr()
    assert "cwd-skill" in captured.out
