from __future__ import annotations

"""Workspace-independent catalogue for bundled LaTeX templates.

The catalogue records what is actually present under ``latex_templete``.  A
template with an official ``.tex`` entry is rendered from that entry.  Some
uploaded CCF packages contain only a class/style file; those remain selectable
and use a clearly labelled anonymous shell instead of silently falling back to
the generic article template.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LatexTemplateEntry:
    template_id: str
    label: str
    directory: str
    entry_file: str
    shell_kind: str = ""

    @property
    def has_official_entry(self) -> bool:
        return bool(self.entry_file and not self.shell_kind)

    @property
    def availability_label(self) -> str:
        return "官方 LaTeX 入口" if self.has_official_entry else "本地模板包"


_CCF_TEMPLATES: tuple[LatexTemplateEntry, ...] = (
    LatexTemplateEntry("aaai", "AAAI", "AAAI", "aaai2026_template.tex"),
    LatexTemplateEntry("acl", "ACL", "ACL", "acl_latex.tex"),
    LatexTemplateEntry("acm", "ACM", "ACM", "acmart.cls", "acmart"),
    LatexTemplateEntry("cikm", "CIKM", "CIKM", "acmart.cls", "acmart"),
    LatexTemplateEntry("cvpr", "CVPR", "CVPR", "main.tex"),
    LatexTemplateEntry("eccv", "ECCV", "ECCV", "main.tex"),
    LatexTemplateEntry("emnlp", "EMNLP", "EMNLP", "acl_latex.tex"),
    LatexTemplateEntry("icassp", "ICASSP", "ICASSP", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("iccv", "ICCV", "ICCV", "iccv.sty", "iccv"),
    LatexTemplateEntry("icde", "ICDE", "ICDE", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("icdm", "ICDM", "ICDM", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("iclr", "ICLR", "ICLR", "iclr2026_basic.tex"),
    LatexTemplateEntry("icme", "ICME", "ICME", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("icml", "ICML", "ICML", "example_paper.tex"),
    LatexTemplateEntry("icra", "ICRA", "ICRA", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("ieee", "IEEE conference", "IEEE", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("ijcai", "IJCAI", "IJCAI", "ijcai26.tex"),
    LatexTemplateEntry("iros", "IROS", "IROS", "IEEEtran.cls", "ieee"),
    LatexTemplateEntry("naacl", "NAACL", "NAACL", "acl_latex.tex"),
    LatexTemplateEntry("neurips", "NeurIPS", "NeurIPS", "neurips_2026.tex"),
    LatexTemplateEntry("sigir", "SIGIR", "SIGIR", "acmart.cls", "acmart"),
    LatexTemplateEntry("kdd", "SIGKDD", "SIGKDD", "kdd_basic.tex"),
    LatexTemplateEntry("sigmod", "SIGMOD", "SIGMOD", "acmart.cls", "acmart"),
    LatexTemplateEntry("vldb", "VLDB", "VLDB", "main.tex"),
    LatexTemplateEntry("wsdm", "WSDM", "WSDM", "acmart.cls", "acmart"),
    LatexTemplateEntry("www", "WWW", "WWW", "acmart.cls", "acmart"),
)

_ALIASES = {
    "nips": "neurips",
    "neurips2026": "neurips",
    "neurips_2026": "neurips",
    "iclr2026": "iclr",
    "iclr_2026": "iclr",
    "iclr_conference": "iclr",
    "iclr2026_conference": "iclr",
    "icml2026": "icml",
    "icml_2026": "icml",
    "sigkdd": "kdd",
    "kdd2026": "kdd",
    "kdd_2026": "kdd",
}


def normalize_ccf_template_id(value: str) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_")
    return _ALIASES.get(normalized, normalized)


def ccf_template_entries(*, repo_root: Path | None = None, available_only: bool = False) -> tuple[LatexTemplateEntry, ...]:
    """Return bundled CCF entries, optionally filtering missing local packages."""

    if not available_only or repo_root is None:
        return _CCF_TEMPLATES
    base = repo_root / "latex_templete" / "ccf-latex-templates"
    return tuple(entry for entry in _CCF_TEMPLATES if (base / entry.directory / entry.entry_file).exists())


def ccf_template_entry(template_id: str) -> LatexTemplateEntry | None:
    canonical = normalize_ccf_template_id(template_id)
    return next((entry for entry in _CCF_TEMPLATES if entry.template_id == canonical), None)


def ccf_template_ids() -> set[str]:
    return {entry.template_id for entry in _CCF_TEMPLATES}


def ccf_template_option_id(template_id: str) -> str:
    return f"ccf_{normalize_ccf_template_id(template_id)}"


def resolve_latex_template(repo_root: Path, family: str, template_id: str, writing_language: str) -> Path | None:
    """Resolve the bundled source file or class package selected by a Gate."""

    base = repo_root / "latex_templete"
    family = str(family or "").strip().lower()
    template_id = str(template_id or "").strip().lower()
    writing_language = str(writing_language or "").strip().lower()
    candidates: list[Path] = []
    if family == "basic_zh":
        candidates.append(base / "normal" / "basic_zh.tex")
    elif family == "basic_en":
        candidates.append(base / "normal" / "basic_en.tex")
    elif family == "utd":
        tid = template_id or "informs"
        if tid in {"informs", "mnsc", "isre", "isr", "ijds"}:
            candidates.append(base / "utd" / "informs" / "INFORMS-ISRE-Template-6-10-2024" / "INFORMS-ISRE-Template.tex")
            candidates.append(base / "utd" / "informs" / "informs_fallback.tex")
        candidates.append(base / "utd" / "informs_basic.tex")
    elif family == "ccf":
        entry = ccf_template_entry(template_id or "neurips")
        if entry is not None:
            candidates.append(base / "ccf-latex-templates" / entry.directory / entry.entry_file)
    if not candidates:
        candidates.append(base / "normal" / ("basic_zh.tex" if writing_language == "zh" else "basic_en.tex"))
    return next((candidate for candidate in candidates if candidate.exists()), None)


def is_ccf_template_path(template_path: Path | None, template_id: str) -> bool:
    entry = ccf_template_entry(template_id)
    if template_path is None or entry is None:
        return False
    marker = f"/ccf-latex-templates/{entry.directory.casefold()}/"
    return marker in template_path.as_posix().casefold()


def is_ccf_package_shell(template_path: Path | None) -> bool:
    if template_path is None:
        return False
    return any(
        is_ccf_template_path(template_path, entry.template_id) and bool(entry.shell_kind)
        for entry in _CCF_TEMPLATES
    )


def render_ccf_package_shell(template_path: Path, body: str) -> str:
    """Build an anonymous document around a class-only bundled package."""

    entry = next((item for item in _CCF_TEMPLATES if is_ccf_template_path(template_path, item.template_id)), None)
    if entry is None:
        return body
    if entry.shell_kind == "acmart":
        preamble = "\\documentclass[sigconf,anonymous,review]{acmart}\n\\settopmatter{printacmref=false}\n"
    elif entry.shell_kind == "ieee":
        preamble = "\\documentclass[conference]{IEEEtran}\n"
    elif entry.shell_kind == "iccv":
        preamble = (
            "\\documentclass[10pt,twocolumn,letterpaper]{article}\n"
            "\\usepackage[review]{iccv}\n"
            "\\def\\paperID{0000}\n\\def\\confName{ICCV}\n\\def\\confYear{2026}\n"
        )
    else:
        preamble = "\\documentclass[11pt]{article}\n"
    return (
        preamble
        + "\\usepackage{graphicx}\n\\usepackage{booktabs}\n\\usepackage{hyperref}\n\n"
        + "\\begin{document}\n\n"
        + body.strip()
        + "\n\\end{document}\n"
    )
