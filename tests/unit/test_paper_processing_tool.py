from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos.tools.paper_processing import ExtractSectionsTool, extract_paper_sections
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class _FakePage:
    """测试用假 PDF page。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePDF:
    """测试用假 PDF 对象，模拟 pdfplumber.open() 的上下文管理行为。"""

    def __init__(self, pages: list[str]) -> None:
        self.pages = [_FakePage(text) for text in pages]

    def __enter__(self) -> "_FakePDF":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _install_fake_pdfplumber(monkeypatch: pytest.MonkeyPatch, pages: list[str]) -> None:
    """把一个极简的假 pdfplumber 模块注入到 sys.modules。"""

    def fake_open(path: Path) -> _FakePDF:
        # 这里顺手断言工具实际上传入的是 workspace 内的真实路径对象。
        assert path.suffix.lower() == ".pdf"
        return _FakePDF(pages)

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=fake_open))


@pytest.mark.asyncio
async def test_extract_sections_happy_path_with_fake_pdfplumber(
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace: Path,
):
    pdf_path = tmp_workspace / "papers" / "demo.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    _install_fake_pdfplumber(
        monkeypatch,
        pages=[
            "\n".join(
                [
                    "A Great Paper",
                    "ABSTRACT",
                    "We study a useful problem.",
                    "1 Introduction",
                    "The introduction explains motivation.",
                    "2 Methodology",
                    "Our method has two stages.",
                ]
            ),
            "\n".join(
                [
                    "3 Results",
                    "Results show consistent gains.",
                    "4 Conclusion",
                    "The paper concludes here.",
                ]
            ),
        ],
    )

    policy = WorkspaceAccessPolicy(tmp_workspace, ["papers/"], [""])
    result = await ExtractSectionsTool(policy).execute(pdf_path="papers/demo.pdf")

    assert result.ok
    assert result.data["pdf"] == "papers/demo.pdf"
    assert result.data["sections"]["abstract"] == "We study a useful problem."
    assert result.data["sections"]["introduction"] == "The introduction explains motivation."
    assert result.data["sections"]["methodology"] == "Our method has two stages."
    assert result.data["sections"]["results"] == "Results show consistent gains."
    assert result.data["sections"]["conclusion"] == "The paper concludes here."
    assert "## introduction" in result.content
    assert "## results" in result.content


@pytest.mark.asyncio
async def test_extract_sections_filters_requested_sections_via_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace: Path,
):
    pdf_path = tmp_workspace / "papers" / "filtered.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    policy = WorkspaceAccessPolicy(tmp_workspace, ["papers/"], [""])

    # 这里直接 patch 类方法，让 helper 函数内部新建的工具实例也会复用这份假数据。
    monkeypatch.setattr(
        ExtractSectionsTool,
        "_iter_pdf_lines",
        lambda self, _path: [
            "1 Introduction",
            "Intro text.",
            "2 Materials and Methods",
            "Method text.",
            "3 Experimental Results",
            "Result text.",
        ],
    )

    result = await extract_paper_sections(
        policy,
        pdf_path="papers/filtered.pdf",
        sections=["method", "results"],
    )

    assert result.ok
    assert set(result.data["sections"]) == {"materials and methods", "experimental results"}
    assert "introduction" not in result.data["sections"]
    assert "## materials and methods" in result.content
    assert "## experimental results" in result.content


@pytest.mark.asyncio
async def test_extract_sections_returns_dependency_missing_when_pdfplumber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace: Path,
):
    pdf_path = tmp_workspace / "papers" / "missing_dep.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    policy = WorkspaceAccessPolicy(tmp_workspace, ["papers/"], [""])
    tool = ExtractSectionsTool(policy)

    monkeypatch.delitem(sys.modules, "pdfplumber", raising=False)
    monkeypatch.setattr(
        tool,
        "_load_pdfplumber",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("pdfplumber")),
    )

    result = await tool.execute(pdf_path="papers/missing_dep.pdf")

    assert not result.ok
    assert result.error == "dependency_missing"


@pytest.mark.asyncio
async def test_extract_sections_rejects_non_pdf_input(tmp_workspace: Path):
    txt_path = tmp_workspace / "papers" / "note.txt"
    txt_path.parent.mkdir(parents=True)
    txt_path.write_text("not a pdf", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, ["papers/"], [""])
    result = await ExtractSectionsTool(policy).execute(pdf_path="papers/note.txt")

    assert not result.ok
    assert result.error == "not_pdf"
