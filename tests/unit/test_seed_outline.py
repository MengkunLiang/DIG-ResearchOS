from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents._common import ensure_seed_outline_profile
from researchos.tools.filesystem import InspectUserSeedsTool
from researchos.tools.seed_outline import NormalizeSeedOutlineTool, build_seed_outline_profile, looks_like_seed_outline
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


REFERENCE_OUTLINE = Path("/mnt/data/reference/算法风险综述_种子提纲.md")


def _outline_text() -> str:
    if REFERENCE_OUTLINE.exists():
        return REFERENCE_OUTLINE.read_text(encoding="utf-8")
    return """
# 从数据智能到决策风险：面向管理决策的智能算法风险研究综述
## —— 理论基础、技术方法、组织管理与治理框架

**横轴**：场景 → 数据 → 模型 → 决策 → 反馈。
**纵轴**：理论解释生成逻辑 / 技术刻画与化解 / 管理在组织内控制 / 治理在制度上规范。
**总论点**：智能算法风险沿数据化、模型化、决策化和反馈再学习逐步生成。

## 1. 理论基础
**核心论点**：风险不是纯技术误差。
**代表性文献方向**：bounded rationality；socio-technical systems；algorithmic accountability

## 2. 技术方法
**核心论点**：需要不确定性、可解释性和公平性工具。
**代表性文献方向**：uncertainty quantification & conformal prediction；XAI；AI fairness

## 3. 治理框架
**核心论点**：组织和制度需要共同约束算法风险。
**代表性文献方向**：EU AI Act；NIST AI RMF；ISO/IEC 42001；ISO/IEC 23894；算法推荐管理规定

**English**：algorithmic risk, managerial decision-making, AI governance, human-AI decision-making
**中文**：智能算法风险、管理决策、算法治理、人机协同决策
"""


def _policy(workspace: Path) -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace,
        allowed_read_prefixes=["", "user_seeds/"],
        allowed_write_prefixes=["", "user_seeds/"],
    )


def test_reference_seed_outline_profile_content_is_not_fake_citations() -> None:
    text = _outline_text()
    assert looks_like_seed_outline(text)

    profile = build_seed_outline_profile(text, source_path="user_seeds/算法风险综述_种子提纲.md")

    assert profile["manuscript_type"] == "survey"
    assert profile["project_type"] == "survey"
    assert profile["language"] in {"zh", "zh-en"}
    assert "智能算法风险" in profile["title"]
    assert profile["framework"]["risk_generation_chain"] == ["场景", "数据", "模型", "决策", "反馈"]
    assert profile["framework"]["perspectives"] == ["理论", "技术", "管理", "治理"]
    assert "taxonomy_hint" in profile["framework"]
    assert len(profile["representative_literature_directions"]) >= 10
    directions = " ".join(item["direction"] for item in profile["representative_literature_directions"])
    assert "bounded rationality" in directions
    assert "NIST AI RMF" in directions or any(r["name"] == "NIST AI RMF" for r in profile["external_resources"])
    assert profile["literature_seed_policy"]["directions_are_verified_citations"] is False
    assert profile["literature_seed_policy"]["do_not_write_seed_papers_from_directions"] is True
    assert "智能算法风险" in profile["query_profile"]["include_keywords"]
    assert "algorithmic risk managerial decision-making survey" in profile["query_profile"]["query_variants"]
    resource_names = {item["name"] for item in profile["external_resources"]}
    assert {"EU AI Act", "NIST AI RMF", "ISO/IEC 42001", "算法推荐管理规定"} & resource_names


@pytest.mark.asyncio
async def test_normalize_seed_outline_tool_writes_profile_and_no_seed_papers(tmp_path: Path) -> None:
    ws = tmp_path
    user_seeds = ws / "user_seeds"
    user_seeds.mkdir()
    outline_path = user_seeds / "算法风险综述_种子提纲.md"
    outline_path.write_text(_outline_text(), encoding="utf-8")

    result = await NormalizeSeedOutlineTool(_policy(ws)).execute(path="user_seeds/算法风险综述_种子提纲.md")

    assert result.ok, result.content
    assert result.data["created_seed_papers"] == 0
    assert (user_seeds / "seed_outline_profile.json").exists()
    assert (user_seeds / "seed_ideas.md").exists()
    assert (user_seeds / "seed_constraints.md").exists()
    assert (user_seeds / "seed_external_resources.jsonl").exists()
    assert not (user_seeds / "seed_papers.jsonl").exists()
    profile = json.loads((user_seeds / "seed_outline_profile.json").read_text(encoding="utf-8"))
    assert profile["literature_seed_policy"]["directions_are_verified_citations"] is False
    assert "query_direction_not_verified_citation" in (user_seeds / "seed_ideas.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_inspect_user_seeds_flags_seed_outline_markdown(tmp_path: Path) -> None:
    ws = tmp_path
    user_seeds = ws / "user_seeds"
    user_seeds.mkdir()
    (user_seeds / "README.md").write_text("# README\n", encoding="utf-8")
    (user_seeds / "算法风险综述_种子提纲.md").write_text(_outline_text(), encoding="utf-8")

    result = await InspectUserSeedsTool(_policy(ws)).execute(path="user_seeds")

    assert result.ok
    assert result.data["actual_material_count"] == 1
    detail = next(item for item in result.data["items_detailed"] if item["path"].endswith("种子提纲.md"))
    assert detail["kind"] == "user_material"
    assert "normalize_seed_outline" in detail["reason"]


def test_runtime_seed_outline_helper_generates_profile_and_derived_files(tmp_path: Path) -> None:
    ws = tmp_path
    user_seeds = ws / "user_seeds"
    user_seeds.mkdir()
    (user_seeds / "seed_ideas.md").write_text("# 初步研究想法\n\n（暂无）\n", encoding="utf-8")
    (user_seeds / "算法风险综述_种子提纲.md").write_text(_outline_text(), encoding="utf-8")

    profile = ensure_seed_outline_profile(ws)

    assert profile is not None
    assert profile["manuscript_type"] == "survey"
    assert (user_seeds / "seed_outline_profile.json").exists()
    assert "seed_outline_profile: derived" in (user_seeds / "seed_ideas.md").read_text(encoding="utf-8")
    assert (user_seeds / "seed_external_resources.jsonl").exists()
    assert not (user_seeds / "seed_papers.jsonl").exists()


def test_runtime_seed_outline_helper_refreshes_stale_profile_and_derived_block(tmp_path: Path) -> None:
    ws = tmp_path
    user_seeds = ws / "user_seeds"
    user_seeds.mkdir()
    outline_path = user_seeds / "seed_outline.md"
    outline_path.write_text(
        """
# 算法风险综述
> 种子提纲 · survey
## 框架总览
**横轴**：场景 → 数据 → 模型 → 决策 → 反馈。
**纵轴**：理论 / 技术 / 管理 / 治理。
## 1. 理论基础
**核心论点**：先理解风险生成。
**代表性文献方向**：bounded rationality；socio-technical systems
""".strip(),
        encoding="utf-8",
    )

    first = ensure_seed_outline_profile(ws)
    assert first is not None
    assert first["source_sha256"]
    assert "bounded rationality" in (user_seeds / "seed_ideas.md").read_text(encoding="utf-8")

    outline_path.write_text(
        """
# 算法风险综述
> 种子提纲 · survey
## 框架总览
**横轴**：场景 → 数据 → 模型 → 决策 → 反馈。
**纵轴**：理论 / 技术 / 管理 / 治理。
## 1. 治理基础
**核心论点**：需要责任与审计框架。
**代表性文献方向**：responsibility gap；algorithm auditing
""".strip(),
        encoding="utf-8",
    )

    second = ensure_seed_outline_profile(ws)

    assert second is not None
    assert second["source_sha256"] != first["source_sha256"]
    directions = " ".join(item["direction"] for item in second["representative_literature_directions"])
    assert "responsibility gap" in directions
    ideas = (user_seeds / "seed_ideas.md").read_text(encoding="utf-8")
    assert "responsibility gap" in ideas
    assert "bounded rationality" not in ideas
