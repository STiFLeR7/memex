# memex-site

The memex v1 product site, https://memex.stifler.in. One page, Next.js 16
(App Router) as a static export, served by a Cloudflare assets-only Worker.
No CSS framework, no client components, no runtime dependencies beyond Next.

## Run

```bash
npm install
npm run dev          # http://localhost:3000
npm run build        # static export to out/
npm run lint
npm run check        # versions and evidence numbers trace to the memex repo (node:test)
npm run site-check   # browser QA against out/ (run after build; uses Google Chrome)
npm run og           # re-render public/og.jpg from scripts/og.html
npm run deploy       # wrangler deploy: Worker `memex` → memex.stifler.in
```

## Structure

| File | What it is |
|---|---|
| `src/app/page.tsx` | All the markup, as one server component. The pipeline stages live here too, as data attributes, so the page reads with JS off |
| `src/app/layout.tsx` | Fonts (Stack Sans Headline, DM Mono, self-hosted by `next/font`), metadata, JSON-LD, and the pre-paint theme script |
| `src/app/globals.css` | The design tokens and every style. Light by default, `[data-theme="dark"]` for the dark theme |
| `public/site.js` | All behaviour, one vanilla script: theme toggle, reveals, hero lattice and demo, the told/now model, the pipeline canvas, copy buttons, the film |
| `public/llms.txt` | Plain-text summary for language models, with sources and limits |
| `src/app/robots.ts`, `sitemap.ts`, `icon.svg` | Generated at build |
| `scripts/content.check.mjs` | `npm run check` |
| `scripts/site-check.mjs` | `npm run site-check`: console errors, failed requests, overflow at 1440 and 390, headings, anchors, reduced motion, JS off, theme toggle, static files |
| `scripts/og.html`, `og.mjs` | Source and renderer of `public/og.jpg` |

## Design

The visual system is the memex brand board,
`../brag-output/brand/index.html` (paper `#F7F9F8` with faint vertical
hairlines, ink `#111417`, teal and lime glass, Stack Sans Headline at 200 to
300 for display, DM Mono for code). Teal marks memex acting (a correction, a
held edit, a link); lime marks the code as it is now; the white overlap of the
mark is what still holds. The August 2026 Swiss prototype in
`../docs/web/archive/` is design history, not a reference.

## Rules

- **No Tailwind.** The design is a hand-authored token system in `globals.css`.
- **Every number traces to a file** in the memex repository. `npm run check`
  pins the version (from `pyproject.toml`) and the evidence figures (from
  `docs/v1/`). Do not add a number that does not trace.
- **Readable with JS off.** Anything a script animates or collapses is hidden
  only under `.rv-ready` (set by `site.js`) or `.js` (set before first paint in
  `layout.tsx`). The `site-check` JS-off pass enforces it.
- **Reduced motion** shows every final state; `site.js` checks it before each animation.
- Code wins over this README where they disagree.

## Known

`npm audit` reports undici findings inside `wrangler` → `miniflare` (dev and
deploy tooling, never shipped to the browser). The only offered fix is a
breaking downgrade of wrangler, so they stay until wrangler ships a patched
undici.
