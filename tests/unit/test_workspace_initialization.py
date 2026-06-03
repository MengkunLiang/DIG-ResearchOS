from __future__ import annotations

from pathlib import Path

from researchos.runtime.workspace import initialize_workspace


def test_initialize_workspace_writes_major_directory_guides(tmp_path: Path):
    initialize_workspace(tmp_path, create_project_file=False)

    for rel in [
        "_DIR_GUIDE.md",
        "_runtime/_DIR_GUIDE.md",
        "_runtime/logs/_DIR_GUIDE.md",
        "_runtime/resume/_DIR_GUIDE.md",
        "_runtime/traces/_DIR_GUIDE.md",
        "user_seeds/_DIR_GUIDE.md",
        "user_seeds/pdfs/_DIR_GUIDE.md",
        "literature/_DIR_GUIDE.md",
        "literature/pdfs/_DIR_GUIDE.md",
        "literature/paper_notes/_DIR_GUIDE.md",
        "literature/paper_notes_abstract/_DIR_GUIDE.md",
        "resources/_DIR_GUIDE.md",
        "resources/repos/_DIR_GUIDE.md",
        "resources/datasets/_DIR_GUIDE.md",
        "resources/benchmarks/_DIR_GUIDE.md",
        "resources/baselines/_DIR_GUIDE.md",
        "ideation/_DIR_GUIDE.md",
        "ideation/_mechanism_tuples/_DIR_GUIDE.md",
        "novelty/_DIR_GUIDE.md",
        "external_executor/_DIR_GUIDE.md",
        "external_executor/workdir/_DIR_GUIDE.md",
        "external_executor/raw_results/_DIR_GUIDE.md",
        "external_executor/configs/_DIR_GUIDE.md",
        "external_executor/logs/_DIR_GUIDE.md",
        "external_executor/patches/_DIR_GUIDE.md",
        "experiments/_DIR_GUIDE.md",
        "experiments/runs/_DIR_GUIDE.md",
        "experiments/configs/_DIR_GUIDE.md",
        "experiments/logs/_DIR_GUIDE.md",
        "evaluation/_DIR_GUIDE.md",
        "drafts/_DIR_GUIDE.md",
        "drafts/survey/_DIR_GUIDE.md",
        "drafts/survey/sections/_DIR_GUIDE.md",
        "drafts/survey/section_outlines/_DIR_GUIDE.md",
        "drafts/sections/_DIR_GUIDE.md",
        "drafts/section_outlines/_DIR_GUIDE.md",
        "drafts/review_rounds/_DIR_GUIDE.md",
        "drafts/patches/_DIR_GUIDE.md",
        "drafts/figures/_DIR_GUIDE.md",
        "drafts/is/_DIR_GUIDE.md",
        "drafts/ccf_a/_DIR_GUIDE.md",
        "submission/_DIR_GUIDE.md",
        "submission/bundle/_DIR_GUIDE.md",
        "submission/bundle/figures/_DIR_GUIDE.md",
    ]:
        path = tmp_path / rel
        assert path.exists(), rel
        text = path.read_text(encoding="utf-8")
        assert "# Workspace Directory Guide" in text
        assert "| 项目 | 说明 |" in text
        assert "## Key Files" in text

    resources = (tmp_path / "resources" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "baseline_candidates.jsonl" in resources
    assert "| `baseline_candidates.jsonl` |" in resources
    root = (tmp_path / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "workspace 的根目录" in root
    resume = (tmp_path / "_runtime" / "resume" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "Resume snapshots" in resume
    external = (tmp_path / "external_executor" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "result_pack.json" in external
    sections = (tmp_path / "drafts" / "sections" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "Section-by-section" in sections
    survey = (tmp_path / "drafts" / "survey" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "T3.6" in survey
    assert not (tmp_path / "pilot").exists()
    assert not (tmp_path / "reviews").exists()
    assert not (tmp_path / "skills").exists()


def test_initialize_workspace_refreshes_generated_guides_but_preserves_custom_guides(tmp_path: Path):
    generated = tmp_path / "drafts" / "sections" / "_DIR_GUIDE.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("# Directory Purpose\nold\n# Produced By\nold\n# Consumed By\nold\n# Validation Rules\nold\n")
    custom = tmp_path / "resources" / "repos" / "_DIR_GUIDE.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom notes\n", encoding="utf-8")

    initialize_workspace(tmp_path, create_project_file=False)

    assert "Section-by-section" in generated.read_text(encoding="utf-8")
    assert "# Workspace Directory Guide" in generated.read_text(encoding="utf-8")
    assert custom.read_text(encoding="utf-8") == "custom notes\n"


def test_initialize_workspace_guides_known_dynamic_dirs_without_polluting_executor_workdir(tmp_path: Path):
    (tmp_path / "experiments" / "runs" / "exp1").mkdir(parents=True)
    (tmp_path / "drafts" / "review_rounds" / "round_1_sections").mkdir(parents=True)
    (tmp_path / "external_executor" / "workdir" / "repo_a" / "src").mkdir(parents=True)
    (tmp_path / "app_exp" / "legacy_run").mkdir(parents=True)
    (tmp_path / "pilot" / "pilot_code").mkdir(parents=True)
    (tmp_path / "reviews" / "review_rounds").mkdir(parents=True)
    (tmp_path / "skills").mkdir(parents=True)

    initialize_workspace(tmp_path, create_project_file=False)

    run_guide = (tmp_path / "experiments" / "runs" / "exp1" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "One normalized experiment run" in run_guide
    review_guide = (
        tmp_path / "drafts" / "review_rounds" / "round_1_sections" / "_DIR_GUIDE.md"
    ).read_text(encoding="utf-8")
    assert "Per-section review shards" in review_guide
    app_exp_guide = (tmp_path / "app_exp" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "Legacy application-experiment" in app_exp_guide
    pilot_guide = (tmp_path / "pilot" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "Legacy internal-pilot" in pilot_guide
    reviews_guide = (tmp_path / "reviews" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "Legacy top-level review" in reviews_guide
    skills_guide = (tmp_path / "skills" / "_DIR_GUIDE.md").read_text(encoding="utf-8")
    assert "workspace-local skill" in skills_guide
    assert not (tmp_path / "external_executor" / "workdir" / "repo_a" / "_DIR_GUIDE.md").exists()
    assert not (tmp_path / "external_executor" / "workdir" / "repo_a" / "src" / "_DIR_GUIDE.md").exists()
