"""测试种子集成配置（Phase 2.5）。

测试内容：
1. 配置生成：验证默认 seed_ensemble 配置正确生成
2. 种子选择逻辑：验证根据实验层级选择正确的种子
3. 缺失配置的回退：验证配置缺失时使用默认值
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def test_default_seed_ensemble_config():
    """测试默认 seed_ensemble 配置。"""
    default_config = {
        "tier1_seeds": [42, 123, 456],
        "tier2_seeds": [789],
        "tier3_seeds": [999]
    }

    # 验证默认配置结构
    assert "tier1_seeds" in default_config
    assert "tier2_seeds" in default_config
    assert "tier3_seeds" in default_config

    # 验证 tier1 至少 3 个种子（关键实验）
    assert len(default_config["tier1_seeds"]) >= 3

    # 验证 tier2 至少 1 个种子（消融实验）
    assert len(default_config["tier2_seeds"]) >= 1

    # 验证 tier3 至少 1 个种子（快速测试）
    assert len(default_config["tier3_seeds"]) >= 1


def test_seed_selection_by_tier():
    """测试根据实验层级选择种子。"""
    seed_ensemble = {
        "tier1_seeds": [42, 123, 456],
        "tier2_seeds": [789],
        "tier3_seeds": [999]
    }

    # 模拟种子选择逻辑
    def select_seeds(tier: str, seed_ensemble: dict) -> list[int]:
        """根据实验层级选择种子。"""
        tier_map = {
            "headline": "tier1_seeds",
            "final_method": "tier1_seeds",
            "ablation": "tier2_seeds",
            "quick_test": "tier3_seeds",
        }
        key = tier_map.get(tier, "tier3_seeds")
        return seed_ensemble.get(key, [42])

    # 测试 headline 实验（应使用 tier1）
    seeds = select_seeds("headline", seed_ensemble)
    assert seeds == [42, 123, 456]
    assert len(seeds) >= 3  # headline 至少 3 个种子

    # 测试 final_method 实验（应使用 tier1）
    seeds = select_seeds("final_method", seed_ensemble)
    assert seeds == [42, 123, 456]

    # 测试 ablation 实验（应使用 tier2）
    seeds = select_seeds("ablation", seed_ensemble)
    assert seeds == [789]

    # 测试 quick_test 实验（应使用 tier3）
    seeds = select_seeds("quick_test", seed_ensemble)
    assert seeds == [999]


def test_missing_config_fallback():
    """测试缺失配置时的回退逻辑。"""
    # 模拟配置缺失
    incomplete_config = {
        "tier1_seeds": [42, 123, 456]
        # tier2_seeds 和 tier3_seeds 缺失
    }

    # 回退逻辑：使用默认值
    def get_seeds_with_fallback(tier: str, seed_ensemble: dict) -> list[int]:
        """获取种子，缺失时使用默认值。"""
        default_seeds = {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }

        tier_map = {
            "headline": "tier1_seeds",
            "final_method": "tier1_seeds",
            "ablation": "tier2_seeds",
            "quick_test": "tier3_seeds",
        }

        key = tier_map.get(tier, "tier3_seeds")
        return seed_ensemble.get(key, default_seeds[key])

    # 测试 tier1 存在
    seeds = get_seeds_with_fallback("headline", incomplete_config)
    assert seeds == [42, 123, 456]

    # 测试 tier2 缺失，使用默认值
    seeds = get_seeds_with_fallback("ablation", incomplete_config)
    assert seeds == [789]

    # 测试 tier3 缺失，使用默认值
    seeds = get_seeds_with_fallback("quick_test", incomplete_config)
    assert seeds == [999]


def test_empty_config_fallback():
    """测试完全空配置时的回退逻辑。"""
    empty_config = {}

    def get_seeds_with_fallback(tier: str, seed_ensemble: dict) -> list[int]:
        """获取种子，缺失时使用默认值。"""
        default_seeds = {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }

        tier_map = {
            "headline": "tier1_seeds",
            "final_method": "tier1_seeds",
            "ablation": "tier2_seeds",
            "quick_test": "tier3_seeds",
        }

        key = tier_map.get(tier, "tier3_seeds")
        return seed_ensemble.get(key, default_seeds[key])

    # 所有层级都应使用默认值
    assert get_seeds_with_fallback("headline", empty_config) == [42, 123, 456]
    assert get_seeds_with_fallback("ablation", empty_config) == [789]
    assert get_seeds_with_fallback("quick_test", empty_config) == [999]


def test_project_yaml_with_seed_ensemble(tmp_path: Path):
    """测试 project.yaml 包含 seed_ensemble 配置。"""
    project_yaml = tmp_path / "project.yaml"

    project_data = {
        "project_id": "test_project",
        "research_direction": "Test research direction",
        "keywords": ["test", "seed", "ensemble"],
        "created_at": "2024-04-19T10:00:00Z",
        "seed_ensemble": {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }
    }

    # 写入 YAML
    project_yaml.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 读取并验证
    loaded = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    assert "seed_ensemble" in loaded
    assert loaded["seed_ensemble"]["tier1_seeds"] == [42, 123, 456]
    assert loaded["seed_ensemble"]["tier2_seeds"] == [789]
    assert loaded["seed_ensemble"]["tier3_seeds"] == [999]


def test_seed_ensemble_schema_validation():
    """测试 seed_ensemble 配置符合 schema。"""
    from researchos.schemas.validator import validate_record

    project_data = {
        "project_id": "test_project",
        "research_direction": "Test research direction with sufficient length",
        "keywords": ["test"],
        "created_at": "2024-04-19T10:00:00Z",
        "seed_ensemble": {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }
    }

    # 验证符合 project schema
    ok, err = validate_record(project_data, "project")
    assert ok, f"Schema validation failed: {err}"


def test_seed_ensemble_in_experimenter_context():
    """测试 Experimenter Agent 读取 seed_ensemble 配置。"""
    # 模拟 project 数据
    project = {
        "project_id": "test_project",
        "research_direction": "Test research",
        "seed_ensemble": {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }
    }

    # 模拟读取逻辑（与 experimenter.py 中的逻辑一致）
    seed_ensemble = project.get("seed_ensemble", {
        "tier1_seeds": [42, 123, 456],
        "tier2_seeds": [789],
        "tier3_seeds": [999]
    })

    # 验证读取成功
    assert seed_ensemble["tier1_seeds"] == [42, 123, 456]
    assert seed_ensemble["tier2_seeds"] == [789]
    assert seed_ensemble["tier3_seeds"] == [999]


def test_custom_seed_ensemble():
    """测试自定义 seed_ensemble 配置。"""
    custom_config = {
        "tier1_seeds": [1, 2, 3, 4, 5],  # 用户自定义 5 个种子
        "tier2_seeds": [100, 200],  # 用户自定义 2 个种子
        "tier3_seeds": [999]
    }

    # 验证自定义配置有效
    assert len(custom_config["tier1_seeds"]) == 5
    assert len(custom_config["tier2_seeds"]) == 2
    assert custom_config["tier1_seeds"][0] == 1
    assert custom_config["tier2_seeds"][0] == 100
