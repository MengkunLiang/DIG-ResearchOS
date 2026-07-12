from __future__ import annotations

from researchos.tools.latex_compile import apply_safe_resizebox_to_wide_tables, inspect_latex_table_layout


def test_safe_resizebox_wraps_only_structurally_wide_standard_tabular():
    tex = r"""\documentclass{article}
\begin{document}
\begin{table}
\begin{tabular}{llllll}
A & B & C & D & E & F \\
1 & 2 & 3 & 4 & 5 & 6 \\
\end{tabular}
\end{table}
\begin{table}
\begin{tabular}{lll}
A & B & C \\
1 & 2 & 3 \\
\end{tabular}
\end{table}
\end{document}
"""
    inspected = inspect_latex_table_layout(tex)
    assert inspected["wide_table_count"] == 1
    transformed, report = apply_safe_resizebox_to_wide_tables(tex)
    assert report["resizebox_inserted"] == 1
    assert "\\resizebox{\\textwidth}{!}{%" in transformed
    assert transformed.count("\\resizebox{") == 1
    assert "\\usepackage{graphicx}" in transformed


def test_safe_resizebox_skips_existing_wrapper_and_aaai_policy():
    wrapped = r"""\documentclass{article}\usepackage{graphicx}
\begin{document}\begin{table}\resizebox{\textwidth}{!}{%\begin{tabular}{llllll}a&b&c&d&e&f\\\end{tabular}%}\end{table}\end{document}"""
    transformed, report = apply_safe_resizebox_to_wide_tables(wrapped)
    assert transformed == wrapped
    assert report["resizebox_inserted"] == 0

    aaai = r"""\documentclass{article}\usepackage{aaai26}\begin{document}\begin{table}\begin{tabular}{llllll}a&b&c&d&e&f\\\end{tabular}\end{table}\end{document}"""
    transformed, report = apply_safe_resizebox_to_wide_tables(aaai)
    assert transformed == aaai
    assert report["template_allows_resizebox"] is False
