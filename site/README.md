---
title: BrightBuild
emoji: 🛠️
colorFrom: indigo
colorTo: red
sdk: static
pinned: false
---

# BrightBuild: services website

A dependency-free static site. Edit, rebuild, deploy.

## Edit

- Copy and layout: `template.html`
- The 60 extra services: `services.json` (the build fails if there are fewer than 50)
- Look and feel: `styles.css` · behaviour: `app.js`
- Replace "BrightBuild" with your business name (search all files).
- Set `CONTACT_EMAIL` at the top of `app.js` so the form opens an email to you.

## Rebuild

```bash
node site/build.mjs   # regenerates site/index.html
```

## Deploy for free

- **GitHub Pages:** `.github/workflows/site.yml` publishes `site/` on pushes to `main`.
  Enable it once under Settings → Pages → Source: **GitHub Actions**.
- **Hugging Face Spaces:** create a new Space with SDK **Static** and upload the contents
  of this folder (the front matter above is already set up).
