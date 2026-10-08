# ResearchOS Project Website

The public project page presents ResearchOS as a **Research Compilation Runtime** for persistent, auditable AI-assisted research.

## Local preview

```bash
python -m http.server 8000 -d website
```

## Demo media

Place these files under `website/assets/`:

```text
researchos-main-flow.webm
researchos-main-flow.mp4
researchos-demo-poster.webp
```

The page prefers WebM and keeps MP4 as a browser fallback.

## GitHub Pages

`.github/workflows/pages.yml` publishes `website/` after changes reach `main`.

Expected URL:

```text
https://mengkunliang.github.io/DIG-ResearchOS/
```

## Related project

The page links to **DIG Research Hub**, a separate task-oriented repository for public research resources, including Skills, MCPs, research agents, prompts, benchmarks, tools, templates, and workflow references.
