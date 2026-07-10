# INFORMS / UTD LaTeX Templates

This directory contains the local INFORMS template family used by ResearchOS
when a T3.6 survey or T8 paper selects:

```json
{"template_family": "utd", "template_id": "informs", "writing_language": "en"}
```

## Default Template

ResearchOS now uses the uploaded official INFORMS Information Systems Research
template package by default:

```text
INFORMS-ISRE-Template-6-10-2024/
  INFORMS-ISRE-Template.tex
  informs4.cls
  informs2014.bst
  eqndefns-left.sty
  eqndefns-center.sty
  informs_Logo.pdf
  informs_Logo.eps
```

The assembly code does not paste an `article` document into this class. It
renders an INFORMS-native front matter block using `\TITLE`, `\ARTICLEAUTHORS`,
`\ABSTRACT`, `\KEYWORDS`, and `\maketitle`, then appends the generated sections
and `\bibliographystyle{informs2014}` / `\bibliography{...}`.

The same default is used for aliases such as `informs`, `isre`, `isr`, `mnsc`,
and `ijds` until separate official journal packages are added locally.

## Legacy Fallback

`informs_fallback.tex` remains only as an emergency legacy fallback. It is not
the default for `template_id=informs`.

## Required Support Files

When ResearchOS assembles an INFORMS document, it copies the required local
support files next to the generated `.tex` file:

- `informs4.cls`
- `informs2014.bst`
- `eqndefns-left.sty`
- `eqndefns-center.sty`
- `informs_Logo.pdf`
- `informs_Logo.eps`

The logo files are required because `informs4.cls` includes `informs_Logo`.

## Compile Expectations

The template should be compiled with a full LaTeX environment such as the
ResearchOS Docker image. The repository root environment may not have
`pdflatex` or `latexmk` installed.

Typical ResearchOS compile path:

```bash
python -m researchos.cli run-task T3.6-COMPILE --workspace <workspace>
```

or, inside the generated draft directory:

```bash
latexmk -pdflatex -interaction=nonstopmode -bibtex survey.tex
```

For final submission to a specific INFORMS journal, replace or extend this
directory with the current official package for that journal if its class
options or author instructions differ from the ISRE package.
