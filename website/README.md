# ResearchOS Project Website

This directory contains the public-facing project website for ResearchOS.

## Design goal

The website is organized around the current ResearchOS positioning as a **Research Compilation Runtime**, not as a generic chat-style research agent. The public story is:

```text
Research Intent
  -> Research Space
  -> Evolved Hypothesis
  -> External Execution
  -> Defensible Paper
```

The four substantive capability sections are:

1. Research Evidence
2. Idea Evolution
3. Execution Compilation
4. Evidence-to-Manuscript

The runtime section separately explains the Artifact Store, Validator, State Machine, Human Gate, Authority Boundary, recovery, and traceability foundation.

## Local preview

From the repository root:

```bash
python -m http.server 8000 -d website
```

Then open `http://localhost:8000`.

## Real demo media

The redesigned page uses the real ResearchOS main-flow recording immediately after the hero.

Required files:

```text
website/assets/researchos-main-flow.webm
website/assets/researchos-demo-poster.webp
```

See `website/assets/README.md` for recommended encoding settings.

## GitHub Pages

The workflow at `.github/workflows/pages.yml` publishes this directory after changes reach `main`.

In the repository, keep **Settings -> Pages -> Build and deployment -> Source** set to **GitHub Actions**.

Expected URL:

```text
https://mengkunliang.github.io/DIG-ResearchOS/
```

## Related project

The page includes **DIG Research Hub** as an independent related project maintained by DIG. The Hub is a curated public resource repository and is **not** presented as a runtime dependency or embedded capability source of ResearchOS.
