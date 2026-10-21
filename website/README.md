# ResearchOS Project Website

The public project page presents ResearchOS as a **Research Compilation Runtime** for persistent, auditable AI-assisted research.

## Local preview

```bash
python -m http.server 8000 -d website
```

## Demo media

The default homepage demo is `website/assets/researchos-main-flow-cn.mp4`, with Mandarin narration and burned-in Chinese subtitles. The original `researchos-main-flow.webm` and `researchos-main-flow.mp4` are preserved for reference. The video poster is `researchos-demo-poster.webp`.

## GitHub Pages

The GitHub Actions workflow at `.github/workflows/pages.yml` deploys the contents of `website/` after site changes are merged into `main`. Keep the repository **Settings → Pages → Build and deployment → Source** set to **GitHub Actions**.

```text
https://mengkunliang.github.io/DIG-ResearchOS/
```

After renaming a GitHub account, check the Pages URL shown under **Settings → Pages** and run the deployment workflow again to refresh publication. The `website/.nojekyll` marker preserves static-file handling.

## Related project

The page also links to **DIG Research Hub**, a separately maintained, task-oriented repository of public research resources, including Skills, MCPs, agents, prompts, benchmarks, tools, templates, and workflow references.
