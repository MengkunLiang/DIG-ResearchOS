from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from researchos.runtime.config import RuntimeSettings
from researchos.skills.contracts import expected_outputs_from_metadata
from researchos.skills.loader import load_skill
from researchos.skills.runner import run_skill
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockHumanInterface,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def _require_native_tex() -> None:
    required = ("latexmk", "pdflatex", "bibtex")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        pytest.skip(f"native TeX toolchain unavailable: {', '.join(missing)}")


@pytest.mark.asyncio
async def test_paper_compile_skill_runs_real_bundle_and_latex(tmp_path: Path):
    """Exercise a public Skill through AgentRunner and the real TeX backend."""

    _require_native_tex()
    workspace = tmp_path / "workspace"
    (workspace / "drafts").mkdir(parents=True)
    (workspace / "literature").mkdir(parents=True)
    (workspace / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "A public-skill compilation check cites~\\cite{smith2024}.\n"
        "\\bibliographystyle{plain}\n"
        "\\bibliography{related_work}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{smith2024, author={Smith, Ada}, title={A Test Article}, "
        "journal={Test Journal}, year={2024}}\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[2]
    skill = load_skill(repo_root / "skills" / "paper-compile")
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="prepare_submission_bundle",
                            arguments={
                                "paper_path": "drafts/paper.tex",
                                "bib_path": "literature/related_work.bib",
                                "bundle_dir": "submission/bundle",
                            },
                            id="bundle",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="latex_compile",
                            arguments={
                                "tex_path": "submission/bundle/main.tex",
                                "engine": "pdflatex",
                                "bibtex": True,
                                "backend": "latexmk",
                                "allow_docker_fallback": False,
                            },
                            id="compile",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "submission/compile_summary.md",
                                "content": "# Compile Summary\n\nPDF: `submission/bundle/main.pdf`\n",
                            },
                            id="summary",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "compiled"}, id="finish")]
                )
            ),
        ]
    )

    result = await run_skill(
        skill=skill,
        user_request="Compile the submission bundle.",
        workspace=workspace,
        tool_registry=registry,
        llm_client=llm,
        human_interface=MockHumanInterface(),
        outputs_expected=expected_outputs_from_metadata(skill.metadata, workspace),
        runtime_settings=RuntimeSettings(),
    )

    assert result.ok, result.error or result.message
    assert (workspace / "submission" / "bundle" / "main.pdf").read_bytes().startswith(b"%PDF")
    assert (workspace / "submission" / "compile_report.json").exists()
    assert (workspace / "submission" / "compile_summary.md").exists()
