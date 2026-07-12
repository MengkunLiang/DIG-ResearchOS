# ResearchOS LaTeX Template Registry

This directory contains local template fallbacks and uploaded venue templates.

## Compiler Environment

Template source is versioned here; compilers and fonts are not Python
dependencies. `basic_zh` and Chinese survey/paper output require XeLaTeX plus
Chinese TeX packages. ResearchOS supports either a host TeX Live installation
or the built-in `researchos/system:latest` Compose image, both with `latexmk`,
pdfLaTeX, XeLaTeX, BibTeX, and Chinese TeX support. See
[../docs/docker.md](../docs/docker.md) for backend selection and recovery.

- `normal/basic_en.tex`: minimal English article fallback.
- `normal/basic_zh.tex`: minimal Chinese article fallback for XeLaTeX/CJK-capable environments.
- `utd/informs/INFORMS-ISRE-Template-6-10-2024/`: uploaded official INFORMS ISRE 2024 package, used by default for `template_id=informs`.
- `utd/informs/informs_fallback.tex`: emergency legacy INFORMS/UTD draft fallback using `informs2014.bst`.
- `utd/informs_basic.tex`: backward-compatible INFORMS-style entry point.
- `ccf-latex-templates/NeurIPS`: default CCF template family target (`template_id=neurips`).
- `ccf-latex-templates/ICLR`: ICLR 2026 style and ResearchOS shell (`template_id=iclr`).
- `ccf-latex-templates/ICML`: ICML 2026 style package (`template_id=icml`).
- `ccf-latex-templates/SIGKDD`: uploaded KDD/ACM-style templates for `template_id=kdd`.

T3.6 and T8 gates store the selected family/id in `writing_template.json` or
`writing_style.json`. Draft assembly applies the selected local template when a
single-file template is available:

- `basic_zh` -> `normal/basic_zh.tex`
- `basic_en` -> `normal/basic_en.tex`
- `utd` / `informs` -> `utd/informs/INFORMS-ISRE-Template-6-10-2024/INFORMS-ISRE-Template.tex`
- `ccf` / `neurips` -> `ccf-latex-templates/NeurIPS/neurips_2026.tex`
- `ccf` / `iclr` -> `ccf-latex-templates/ICLR/iclr2026_basic.tex`
- `ccf` / `icml` -> `ccf-latex-templates/ICML/example_paper.tex`
- `ccf` / `kdd` -> `ccf-latex-templates/SIGKDD/kdd_basic.tex`

If a selected venue template cannot be resolved, assembly falls back to the
basic article template and records the fallback in the generated TeX comments.
T9 submission bundling copies support files required by the already selected
template and compiles the bundle; target-venue migration should happen through
the T3.6/T8 template gate, not as an ad hoc T9 rewrite.

For INFORMS/UTD targets, ResearchOS now uses the uploaded official ISRE 2024
package by default and strips sample-only packages that are absent from the
Docker image. For Management Science or another specific INFORMS journal, replace
the local package with that journal's current official ZIP before final
submission if exact journal branding is required.
