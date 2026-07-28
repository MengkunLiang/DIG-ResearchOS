# Bundled Template Support

These skeletons are self-contained for local `latexmk` compilation when copied with every file in this directory. They intentionally keep the actual paper prose in `sections/` and the bibliography in `references.bib`; the files here provide only the template and its required class, style, bibliography, and algorithm support assets.

The vendored annual styles come from the corresponding official distribution:

- `icml2025.sty`, `icml2025.bst`, `algorithm.sty`, `algorithmic.sty`, and `fancyhdr.sty`: ICML 2025 style archive, `https://media.icml.cc/Conferences/ICML2025/Styles/icml2025.zip`.
- `neurips_2025.sty`: NeurIPS 2025 style archive, `https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip`.
- `iclr2026_conference.sty`: the repository's bundled ICLR 2026 official template package under `latex_templete/ccf-latex-templates/ICLR/`.
- `IEEEtran.cls` and `IEEEtran.bst`: the bundled IEEE distribution.

The integration regression test copies this complete directory into an empty temporary location, writes minimal `sections/` and `references.bib` inputs, and compiles every public skeleton. Do not update a yearly template name without also replacing its matching official support assets and extending that test.
