# INFORMS / UTD Template Notes

Checked on 2026-06-11.

Primary sources:

- INFORMS Author Portal, "LaTeX Style Files": `https://pubsonline.informs.org/authorportal/latex-style-files`
- Management Science official ZIP link exposed by the author portal:
  `https://pubsonline.informs.org/pb-assets/LaTeX/INFORMS-MNSC-Template-6-10-2024-1718048504857.zip`
- Overleaf template, "Template for INFORMS Journal on Data Science":
  `https://www.overleaf.com/latex/templates/template-for-informs-journal-on-data-science/sbthszxgycfn`
- Overleaf template, "Template for Management Science Journal":
  `https://www.overleaf.com/latex/templates/template-for-management-science-journal/bjpqpdqhbshy`

The INFORMS author portal lists journal ZIP packages containing the template,
submission class, BibTeX style, and documentation. Direct command-line download
of the Management Science ZIP returned a Cloudflare challenge/403 in this
environment, so the complete official `informs4.cls` package is not vendored
here.

`informs_fallback.tex` is a compile-ready ResearchOS fallback that uses ordinary
LaTeX `article` layout plus INFORMS-style bibliography via `informs2014.bst`.
It should be used for T3.6/T8 draft assembly. For final T9 submission to a
specific INFORMS journal, download the current official journal ZIP and replace
the fallback with the official class/style package.
