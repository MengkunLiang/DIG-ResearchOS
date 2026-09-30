# ResearchOS Project Website

This directory contains the public-facing project website for ResearchOS.

## Local preview

From the repository root:

```bash
python -m http.server 8000 -d website
```

Then open `http://localhost:8000`.

## GitHub Pages

The included workflow at `.github/workflows/pages.yml` publishes this directory to GitHub Pages after changes reach `main`.

In the GitHub repository, make sure **Settings → Pages → Build and deployment → Source** is set to **GitHub Actions**.

Expected URL:

```text
https://mengkunliang.github.io/DIG-ResearchOS/
```

## Adding a real demo video later

Put the recording under `website/assets/`, for example:

```text
website/assets/researchos-demo.mp4
website/assets/researchos-demo.webm
website/assets/researchos-demo-poster.webp
```

The current page intentionally ships with a built-in animated terminal simulation so the site is still complete without external media.
