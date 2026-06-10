# ResearchOS LaTeX Template Registry

This directory contains local template fallbacks and uploaded venue templates.

- `normal/basic_en.tex`: minimal English article fallback.
- `normal/basic_zh.tex`: minimal Chinese `ctexart` fallback.
- `utd/informs_basic.tex`: INFORMS-style double-spaced fallback for UTD/IS/OM journals.
- `ccf-latex-templates/NeurIPS`: default CCF template family target (`template_id=neurips`).
- `ccf-latex-templates/SIGKDD`: uploaded KDD/ACM-style templates for `template_id=kdd`.

T3.6 and T8 gates store the selected family/id in `writing_template.json` or
`writing_style.json`. Draft assembly applies the selected local template when a
single-file template is available:

- `basic_zh` -> `normal/basic_zh.tex`
- `basic_en` -> `normal/basic_en.tex`
- `utd` / `informs` -> `utd/informs_basic.tex`
- `ccf` / `neurips` -> `ccf-latex-templates/NeurIPS/neurips_2026.tex`

If a selected venue template cannot be resolved, assembly falls back to the
basic article/ctex template and records the fallback in the generated TeX
comments. T9 submission bundling can still perform stricter venue migration.
